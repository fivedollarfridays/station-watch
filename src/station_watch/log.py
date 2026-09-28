"""Component 4: the append-only, durable, deduplicated local Log.

One SQLite store (WAL mode, stdlib ``sqlite3``) holds every record type plus the
pipeline's own ``cycle_completed`` rows. Each row is keyed by the record's
deterministic ``record_id``, so a replayed row is ignored rather than
duplicated. Reads are ordered by each row's canonical ``ts`` (not insert order),
which -- being ISO 8601 UTC with a fixed offset and microseconds -- sorts
chronologically as a plain string.

Exactly one writer per process: all appends go through a single :class:`Log`
instance whose writes are serialized by a lock, so the Capture timer thread and
the main loop never write concurrently; the Watchdog only reads. ``kind`` names
are the ``record_id`` infixes (``frame``, ``blind``, ``obs``, ``verdict``,
``cycle``, ``alarm_eval``); the friendlier ``cycle_completed`` /
``alarm_evaluated`` spellings are accepted as query aliases.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from enum import Enum
from pathlib import Path

from station_watch.records import (
    AlarmEvaluated,
    BlindRecord,
    CycleCompleted,
    FrameRecord,
    Observation,
    Verdict,
)

_KIND_TO_CLASS = {
    "frame": FrameRecord,
    "blind": BlindRecord,
    "obs": Observation,
    "verdict": Verdict,
    "cycle": CycleCompleted,
    "alarm_eval": AlarmEvaluated,
}

# A caller may name a kind by the record's fuller sense (the acceptance
# criteria's ``newest("cycle_completed")``) rather than the record_id infix.
_KIND_ALIASES = {
    "cycle_completed": "cycle",
    "alarm_evaluated": "alarm_eval",
    "observation": "obs",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    record_id TEXT PRIMARY KEY,
    kind      TEXT NOT NULL,
    ts        TEXT NOT NULL,
    run_id    TEXT NOT NULL,
    body      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_kind_ts ON records (kind, ts);
CREATE INDEX IF NOT EXISTS records_kind_run_ts ON records (kind, run_id, ts)
"""


class LogError(RuntimeError):
    """Raised when a Log cannot be opened or used."""


def _kind_of(record_id: str) -> str:
    # record_id is "{run_id}:{infix}:...": run_id (a UUID4) carries no colon, so
    # the second colon-delimited segment is the kind.
    return record_id.split(":", 2)[1]


def _coerce(value):
    return value.value if isinstance(value, Enum) else value


def _rebuild(data: dict):
    return _KIND_TO_CLASS[_kind_of(data["record_id"])].from_dict(data)


def _newest_sql(kind: str, run_id: str | None, until: str | None) -> tuple[str, tuple]:
    """The indexed newest-first query for ``kind`` (shared with the plan test)."""
    clauses, params = ["kind = ?"], [kind]
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(run_id)
    if until is not None:
        clauses.append("ts <= ?")
        params.append(until)
    where = " AND ".join(clauses)
    return f"SELECT body FROM records WHERE {where} ORDER BY ts DESC, rowid DESC", tuple(params)


class Log:
    """The single append-only store; one instance is the sole writer."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        parent = self._path.parent
        if not parent.exists():
            raise LogError(f"log directory does not exist: {parent} (for log path {self._path})")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def append(self, record) -> None:
        """Durably append ``record``; a row with the same record_id is ignored."""
        data = record.to_dict()
        record_id = data["record_id"]
        row = (record_id, _kind_of(record_id), data["ts"], data["run_id"], json.dumps(data))
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO records "
                "(record_id, kind, ts, run_id, body) VALUES (?, ?, ?, ?, ?)",
                row,
            )
            self._conn.commit()

    def newest(self, kind: str, *, run_id: str | None = None, until: str | None = None, **match):
        """Newest row of ``kind`` (optionally of one run, at or before ``until``).

        ``run_id`` and ``until`` are answered by an index; any other ``match``
        fields are compared on rows streamed newest-first, so the common case
        (no extra fields) reads exactly one row whatever the Log's size.
        """
        sql, params = _newest_sql(_KIND_ALIASES.get(kind, kind), run_id, until)
        wanted = {key: _coerce(value) for key, value in match.items()}
        with self._lock:
            for (body,) in self._conn.execute(sql, params):
                data = json.loads(body)
                if all(data.get(key) == value for key, value in wanted.items()):
                    return _rebuild(data)
        return None

    def read_new(self, cursor: int, kinds, *, run_id: str) -> tuple[int, list]:
        """Rows of ``kinds`` for ``run_id`` appended after ``cursor``, in insert order.

        Returns ``(new_cursor, records)``; pass ``new_cursor`` back next time to
        read incrementally. The cursor is the SQLite rowid, so the read is a range
        scan over only the rows appended since the last call.
        """
        canonical = [_KIND_ALIASES.get(kind, kind) for kind in kinds]
        placeholders = ",".join("?" for _ in canonical)
        with self._lock:
            # Bound the read by the newest rowid first, so a row committed by
            # another connection mid-read is picked up next time, never skipped.
            newest = self._conn.execute("SELECT MAX(rowid) FROM records").fetchone()[0] or 0
            rows = self._conn.execute(
                f"SELECT rowid, body FROM records WHERE rowid > ? AND rowid <= ? "
                f"AND run_id = ? AND kind IN ({placeholders}) ORDER BY rowid ASC",
                (cursor, newest, run_id, *canonical),
            ).fetchall()
        records = [_rebuild(json.loads(body)) for _rowid, body in rows]
        return max(cursor, newest), records

    def since(self, ts: str, kinds):
        """All rows of the given ``kinds`` with ``ts >= ts``, oldest first."""
        canonical = [_KIND_ALIASES.get(kind, kind) for kind in kinds]
        if not canonical:
            return []
        placeholders = ",".join("?" for _ in canonical)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT body FROM records WHERE kind IN ({placeholders}) "
                "AND ts >= ? ORDER BY ts ASC, rowid ASC",
                (*canonical, ts),
            ).fetchall()
        return [_rebuild(json.loads(body)) for (body,) in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Log:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


__all__ = ["Log", "LogError"]
