"""A strictly read-only view onto the Log, for the Board (component 7).

The Board must never create schema or write a row, so it opens the SQLite store
through a ``mode=ro`` URI rather than the :class:`~station_watch.log.Log`
constructor (which runs ``CREATE TABLE`` and is the sole writer). A ``mode=ro``
connection cannot create the database file, cannot create the ``-wal``/``-shm``
side files, and raises ``sqlite3.OperationalError`` on any write -- so a missing
Log is caught at open time and an accidental write is impossible, not merely
unused.

Rows are rebuilt into the same frozen record dataclasses the writer stored, using
the Log's own kind map, so the Board reads exactly what Capture, Judge and Alarm
wrote.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from station_watch.log import _KIND_ALIASES, _KIND_TO_CLASS, _kind_of


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
            self._conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise BoardLogError(f"log unreadable: {self._path}: {exc}") from exc

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying read-only connection (a write through it raises)."""
        return self._conn

    def _bodies(self, kind: str) -> Iterator[str]:
        canonical = _KIND_ALIASES.get(kind, kind)
        try:
            cursor = self._conn.execute(
                "SELECT body FROM records WHERE kind = ? ORDER BY ts DESC, rowid DESC",
                (canonical,),
            )
            for (body,) in cursor:
                yield body
        except sqlite3.Error as exc:
            raise BoardLogError(f"log unreadable: {self._path}: {exc}") from exc

    def newest(self, kind: str):
        """The newest row of ``kind`` by canonical ts, or ``None`` if there is none."""
        for body in self._bodies(kind):
            return _rebuild(body)
        return None

    def iter_newest(self, kind: str) -> Iterator:
        """Every row of ``kind``, newest first, rebuilt into its record dataclass."""
        for body in self._bodies(kind):
            yield _rebuild(body)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> LogReader:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


__all__ = ["LogReader", "BoardLogError"]
