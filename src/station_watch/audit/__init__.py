"""The ``station-watch audit`` command: a proof sheet for every flag of a session.

``audit build`` reads one session's Log read-only, collects every fault and blind
episode (:func:`~station_watch.audit.flags.collect_flags`) and writes ``flags.json``
plus a self-contained ``index.html`` whose thumbnails embed each cited frame with
the configured zones drawn on it. All audit HTML is produced through
:mod:`station_watch.audit.html`, which escapes every Log-sourced value.
"""

from __future__ import annotations

from station_watch.audit.flags import AuditFlag, collect_flags
from station_watch.audit.html import esc

__all__ = ["AuditFlag", "collect_flags", "esc"]
