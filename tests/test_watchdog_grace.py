"""Watchdog startup grace: an empty Log is not silence until one window has passed.

Started alongside (or just before) the run, the Watchdog finds a Log with no
cycle or alarm rows yet. That is a pipeline starting up, not a silent one: each
rail gets a grace equal to its own window, measured on the Watchdog's own clock
from its first check. After that, "no rows yet" is silence and alarms (K1). A
stale row is never graced, and a missing Log still alarms at once.
"""

import sys
from pathlib import Path

from station_watch.clock import offset_iso
from station_watch.log import Log
from station_watch.watchdog import Watchdog

sys.path.insert(0, str(Path(__file__).parent))
from test_watchdog import SpySink, _config, _seed_log  # noqa: E402

T0 = "2026-09-28T12:00:00.000000+00:00"


class _Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def _empty_log(path):
    Log(path).close()
    return path


def test_empty_log_is_graced_for_one_window_then_alarms(tmp_path):
    logdb = _empty_log(tmp_path / "log.db")
    sink, clock = SpySink(), _Clock(T0)
    watchdog = Watchdog(_config(), sinks=[sink], log_path=logdb, clock=clock)  # windows 2s

    watchdog.check()
    clock.now = offset_iso(T0, 1.9)
    watchdog.check()
    assert sink.alarms == [], "no rows yet, inside the startup grace: not an alarm"

    clock.now = offset_iso(T0, 2.1)
    watchdog.check()
    assert sorted(e.cause for e in sink.alarms) == ["watchdog:alarm", "watchdog:cycle"]
    assert all("no rows" in e.label for e in sink.alarms)


def test_a_stale_row_is_not_graced(tmp_path):
    logdb = tmp_path / "log.db"
    _seed_log(logdb, cycle_ts=offset_iso(T0, -10), alarm_ts=offset_iso(T0, -10))
    sink = SpySink()
    Watchdog(_config(), sinks=[sink], log_path=logdb, clock=lambda: T0).check()
    assert sorted(e.cause for e in sink.alarms) == ["watchdog:alarm", "watchdog:cycle"]


def test_missing_log_is_not_graced(tmp_path):
    sink = SpySink()
    Watchdog(_config(), sinks=[sink], log_path=tmp_path / "nope.db", clock=lambda: T0).check()
    assert [e.cause for e in sink.alarms] == ["watchdog:log"]
