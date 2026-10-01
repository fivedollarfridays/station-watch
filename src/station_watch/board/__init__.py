"""Component 7: the Board -- a read-only operator screen built from the Log.

The Board never writes. It opens the Log through a strictly read-only SQLite
connection (:class:`~station_watch.board.reader.LogReader`, ``mode=ro``), reads
the newest records, and renders one view per station: the current state and how
long it has held, the newest frame's age, the open blind reasons, the active
fault flags with their cited frame ids, and the alarm's open episodes. A missing
or unreadable Log, no verdict yet, or a stale verdict all render UNKNOWN with the
reason -- never OK (K1).

Nothing here imports from :mod:`station_watch.alarm` or :mod:`station_watch.runner`;
the Board shares only the record and config formats and has no code path that
writes.
"""

from __future__ import annotations

from station_watch.board.reader import BoardLogError, LogReader
from station_watch.board.render import render_html, render_text, render_view_json
from station_watch.board.server import BoardHandler, make_server
from station_watch.board.view import Flag, StationView, build_view

__all__ = [
    "BoardLogError",
    "LogReader",
    "Flag",
    "StationView",
    "build_view",
    "render_html",
    "render_text",
    "render_view_json",
    "BoardHandler",
    "make_server",
]
