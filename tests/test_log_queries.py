"""Indexed, run-scoped and incremental Log reads.

The Judge runs every cycle for as long as the station is watched, so its reads
must not grow with the Log's history: ``newest`` answers from an index on
``(kind, run_id, ts)`` instead of scanning every row of a kind, and ``read_new``
hands back only the rows appended since a cursor, filtered to one run. A restart
mints a new ``run_id``, and run-scoped reads are what keep a previous run's state
(an open blind condition, say) from leaking into the new one.
"""

import sqlite3
import sys
from pathlib import Path

from station_watch.clock import offset_iso
from station_watch.log import Log, _newest_sql

sys.path.insert(0, str(Path(__file__).parent))
from test_log import BASE, blind, cycle  # noqa: E402


def test_newest_filters_by_run_id(tmp_path):
    with Log(tmp_path / "log.db") as log:
        log.append(cycle("run-A", 1))
        log.append(cycle("run-B", 2))
        log.append(cycle("run-A", 3))
        log.append(cycle("run-B", 4))
        assert log.newest("cycle", run_id="run-A") == cycle("run-A", 3)
        assert log.newest("cycle", run_id="run-C") is None


def test_newest_until_ignores_rows_after_the_bound(tmp_path):
    with Log(tmp_path / "log.db") as log:
        for n in range(1, 6):
            log.append(cycle("run-A", n))
        assert log.newest("cycle", until=offset_iso(BASE, 3)) == cycle("run-A", 3)


def _plan(path, run_id, until):
    sql, params = _newest_sql("cycle", run_id, until)
    with sqlite3.connect(str(path)) as conn:
        rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    return " | ".join(row[-1] for row in rows)


def test_newest_uses_an_index_not_a_full_scan(tmp_path):
    path = tmp_path / "log.db"
    with Log(path) as log:
        log.append(cycle("run-A", 1))
    plans = [_plan(path, None, None), _plan(path, "run-A", offset_iso(BASE, 9))]
    for plan in plans:
        assert "USING INDEX" in plan or "USING COVERING INDEX" in plan, plan
        assert "TEMP B-TREE" not in plan, plan


def test_read_new_returns_only_rows_after_the_cursor_for_one_run(tmp_path):
    with Log(tmp_path / "log.db") as log:
        log.append(blind("run-A", 1))
        log.append(blind("run-OLD", 2))
        log.append(cycle("run-A", 3))  # a kind the reader did not ask for
        cursor, rows = log.read_new(0, ["blind"], run_id="run-A")
        assert [r.seq for r in rows] == [1]
        log.append(blind("run-A", 4))
        cursor, rows = log.read_new(cursor, ["blind"], run_id="run-A")
        assert [r.seq for r in rows] == [4]
        again, rows = log.read_new(cursor, ["blind"], run_id="run-A")
        assert rows == [] and again == cursor
