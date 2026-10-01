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

import sqlite3
import urllib.parse
from collections.abc import Iterator
from pathlib import Path

from station_watch.log import _KIND_ALIASES, _KIND_TO_CLASS, _kind_of

# The most verdicts the held-time walk reads; a longer streak reads "held >= ...".
HELD_WALK_LIMIT = 256

_NEWEST_SQL = "SELECT body FROM records WHERE kind = ? ORDER BY ts DESC, rowid DESC LIMIT ?"
_NEWEST_MATCHING_SQL = (
    "SELECT body FROM records WHERE kind = ? AND json_extract(body, ?) = ? "
    "ORDER BY ts DESC, rowid DESC LIMIT 1"
)


def _readonly_uri(path: Path) -> str:
    """A ``mode=ro`` URI whose path is percent-encoded, so ``?``/``#``/``%`` in it
    can neither truncate the path nor displace the ``mode=ro`` query."""
    return f"file:{urllib.parse.quote(str(path.resolve()))}?mode=ro"


class BoardLogError(RuntimeError):
    """The Log is missing or unreadable -- the Board renders UNKNOWN, never OK."""


def _rebuild(body: str):
    import json

    data = json.loads(body)
    return _KIND_TO_CLASS[_kind_of(data["record_id"])].from_dict(data)


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

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying read-only connection (a write through it raises)."""
        return self._conn

    def _bodies(self, sql: str, params: tuple) -> Iterator[str]:
        try:
            for (body,) in self._conn.execute(sql, params):
                yield body
        except sqlite3.Error as exc:
            raise BoardLogError(f"log unreadable: {self._path}: {exc}") from exc

    def newest(self, kind: str):
        """The newest row of ``kind`` by canonical ts, or ``None`` if there is none."""
        for record in self.iter_newest(kind, limit=1):
            return record
        return None

    def newest_matching(self, kind: str, field: str, value: str):
        """The newest row of ``kind`` whose body ``field`` equals ``value`` (one row)."""
        params = (_KIND_ALIASES.get(kind, kind), f"$.{field}", value)
        for body in self._bodies(_NEWEST_MATCHING_SQL, params):
            return _rebuild(body)
        return None

    def iter_newest(self, kind: str, *, limit: int) -> Iterator:
        """At most ``limit`` rows of ``kind``, newest first, rebuilt into records."""
        params = (_KIND_ALIASES.get(kind, kind), limit)
        for body in self._bodies(_NEWEST_SQL, params):
            yield _rebuild(body)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> LogReader:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


__all__ = ["LogReader", "BoardLogError", "HELD_WALK_LIMIT"]
