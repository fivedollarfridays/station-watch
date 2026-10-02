"""A strictly read-only view onto the Log, for the Board (component 7).

The Board must never create schema or write a row, so it opens the SQLite store
through a ``mode=ro`` URI rather than the :class:`~station_watch.log.Log`
constructor (which runs ``CREATE TABLE`` and is the sole writer). A ``mode=ro``
connection cannot create the database file and raises ``sqlite3.OperationalError``
on any write -- so a missing Log is caught at open time and an accidental write is
impossible, not merely unused. (SQLite itself may create or touch the WAL-mode
``-wal``/``-shm`` side files a reader needs for its read snapshot; the Log's rows and
the database file's bytes are never changed.)

Rows are rebuilt into the same frozen record dataclasses the writer stored, using
the Log's own kind map, so the Board reads exactly what Capture, Judge and Alarm
wrote.

Every query is bounded (``LIMIT``) and answered newest-first by the Log's
``records_kind_ts`` index with no temp sort, so a render's cost does not grow with
the Log: the Board re-renders every 2 s against a Log that can hold hundreds of
thousands of frame rows.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from station_watch.log import _KIND_ALIASES, _KIND_TO_CLASS, _kind_of

# The most verdicts the held-time walk reads; a longer streak reads "held >= ...".
HELD_WALK_LIMIT = 256

_NEWEST_SQL = (
    "SELECT record_id, body, ts, run_id FROM records WHERE kind = ? "
    "ORDER BY ts DESC, rowid DESC LIMIT ?"
)
_NEWEST_MATCHING_SQL = (
    "SELECT record_id, body FROM records WHERE kind = ? AND json_extract(body, ?) = ? "
    "ORDER BY ts DESC, rowid DESC LIMIT 1"
)
# The blind-reason lookup's expression is the *literal* ``json_extract(body,
# '$.reason')``, so SQLite can answer it from the ``records_blind_reason``
# expression index (the generic ``_NEWEST_MATCHING_SQL`` binds the path and
# cannot match an expression index).
_NEWEST_WITH_REASON_SQL = (
    "SELECT record_id, body FROM records "
    "WHERE kind = ? AND json_extract(body, '$.reason') = ? "
    "ORDER BY ts DESC, rowid DESC LIMIT 1"
)

# Said once per process: a Log written before the blind-reason index existed
# falls back to the generic scan (the read-only Board cannot add the index).
_warned_no_reason_index = False


def _readonly_uri(path: Path) -> str:
    """A ``mode=ro`` URI whose path is percent-encoded, so ``?``/``#``/``%`` in it
    can neither truncate the path nor displace the ``mode=ro`` query."""
    return f"file:{urllib.parse.quote(str(path.resolve()))}?mode=ro"


class BoardLogError(RuntimeError):
    """The Log is missing or unreadable -- the Board renders UNKNOWN, never OK."""


@dataclass(frozen=True)
class UndecodableRow:
    """A row :meth:`LogReader.iter_newest` could not rebuild, yielded in its place.

    It carries what the row's own columns still say (id, ts, run) and why it failed.
    Its ``state`` is ``"undecodable"`` -- never a healthy or observable state -- and
    it has no faults and no target, so a walk that reads verdict state, faults or
    observation targets treats it as unknown rather than aborting.
    """

    record_id: str
    ts: str
    run_id: str
    reason: str
    state: str = "undecodable"
    faults: tuple = ()
    target: None = None
    kind: None = None


def _rebuild(body: str):
    data = json.loads(body)
    return _KIND_TO_CLASS[_kind_of(data["record_id"])].from_dict(data)


def _warn_no_reason_index(path: Path) -> None:
    """Say once, on stderr, that this Log predates the blind-reason index."""
    global _warned_no_reason_index
    if not _warned_no_reason_index:
        _warned_no_reason_index = True
        sys.stderr.write(
            f"station-watch board: Log {path} predates the records_blind_reason index; "
            "open-blind-reason lookups fall back to a full scan\n"
        )


class LogReader:
    """A read-only reader over a Log database (``mode=ro``); never writes."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        if not self._path.exists():
            raise BoardLogError(f"log missing: {self._path}")
        try:
            self._conn = sqlite3.connect(_readonly_uri(self._path), uri=True)
        except sqlite3.Error as exc:
            raise BoardLogError(f"log unreadable: {self._path}: {exc}") from exc
        self._reason_index: bool | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying read-only connection (a write through it raises)."""
        return self._conn

    def _rows(self, sql: str, params: tuple) -> Iterator[tuple[str, str]]:
        try:
            yield from self._conn.execute(sql, params)
        except sqlite3.Error as exc:
            raise BoardLogError(f"log unreadable: {self._path}: {exc}") from exc

    def _decode(self, record_id: str, body: str):
        """Rebuild one stored row, naming the record id if its body cannot decode.

        A row an older or drifted writer left behind -- an unknown record kind,
        schema drift, or bad JSON -- becomes a ``BoardLogError`` the Board renders
        as UNKNOWN, rather than an exception escaping onto the operator's screen.
        """
        try:
            return _rebuild(body)
        except (KeyError, ValueError, TypeError, IndexError) as exc:
            raise BoardLogError(f"undecodable record {record_id}: {exc}") from exc

    def newest(self, kind: str):
        """The newest row of ``kind`` by canonical ts, or ``None`` if there is none."""
        for record in self.iter_newest(kind, limit=1):
            if isinstance(record, UndecodableRow):
                raise BoardLogError(f"undecodable record {record.record_id}: {record.reason}")
            return record
        return None

    def newest_matching(self, kind: str, field: str, value: str):
        """The newest row of ``kind`` whose body ``field`` equals ``value`` (one row)."""
        params = (_KIND_ALIASES.get(kind, kind), f"$.{field}", value)
        for record_id, body in self._rows(_NEWEST_MATCHING_SQL, params):
            return self._decode(record_id, body)
        return None

    def newest_with_reason(self, kind: str, reason: str):
        """The newest row of ``kind`` whose body ``reason`` equals ``reason``.

        Index-backed by ``records_blind_reason``. A Log written before that index
        existed falls back to the generic scan (said once on stderr); the result
        is the same row either way.
        """
        if not self._has_reason_index():
            _warn_no_reason_index(self._path)
            return self.newest_matching(kind, "reason", reason)
        params = (_KIND_ALIASES.get(kind, kind), reason)
        for record_id, body in self._rows(_NEWEST_WITH_REASON_SQL, params):
            return self._decode(record_id, body)
        return None

    def _has_reason_index(self) -> bool:
        if self._reason_index is None:
            self._reason_index = any(
                True
                for _ in self._rows(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'index' AND name = 'records_blind_reason'",
                    (),
                )
            )
        return self._reason_index

    def iter_newest(self, kind: str, *, limit: int) -> Iterator:
        """At most ``limit`` rows of ``kind``, newest first, rebuilt into records.

        A row that cannot be rebuilt is yielded as an :class:`UndecodableRow` in its
        place and the walk continues: one drifted row degrades to one UNKNOWN row.
        """
        params = (_KIND_ALIASES.get(kind, kind), limit)
        for record_id, body, ts, run_id in self._rows(_NEWEST_SQL, params):
            try:
                yield self._decode(record_id, body)
            except BoardLogError as exc:
                yield UndecodableRow(record_id, ts, run_id, str(exc))

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> LogReader:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


__all__ = ["LogReader", "BoardLogError", "UndecodableRow", "HELD_WALK_LIMIT"]
