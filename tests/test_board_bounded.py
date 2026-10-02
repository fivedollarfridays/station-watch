"""The Board's reads are bounded per render, whatever the Log's size (PR #6 review r2).

A demo-length Log holds hundreds of thousands of frame rows, and the Board
re-renders every 2 s, so every query a render issues must fetch a bounded number
of rows and be answered by the ``records_kind_ts`` index without a temp B-tree
sort. These tests count the rows a render actually pulls through the reader's
connection and inspect each query's plan.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from station_watch.board import build_view
from station_watch.board import reader as reader_mod
from station_watch.clock import offset_iso
from station_watch.log import Log
from station_watch.records import BlindReason, BlindState, Verdict, VerdictState

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import blind_at, frame_at, health_config  # noqa: E402

EPOCH = "2026-09-28T00:00:00.000000+00:00"
RUN = "run-bounded"


def _t(offset: float) -> str:
    return offset_iso(EPOCH, offset)


def _verdict(ts: str, seq: int, state=VerdictState.HEALTHY) -> Verdict:
    return Verdict(
        station_id="station-1",
        ts=ts,
        state=state,
        faults=(),
        blind_reasons=(),
        seq=seq,
        run_id=RUN,
    )


def _long_log(path: Path, frames: int, verdicts: int, blinds: int) -> Path:
    """A long healthy run: many frames and verdicts, a flapping blind history."""
    with Log(path) as log:
        for i in range(frames):
            log.append(frame_at(_t(i * 0.05), RUN, i))
        for i in range(verdicts):
            log.append(_verdict(_t(i * 0.1), i + 1))
        for i in range(blinds):
            state = BlindState.OPENED if i % 2 == 0 else BlindState.CLEARED
            log.append(blind_at(_t(i * 0.01), RUN, BlindReason.FROZEN, state, i + 1))
        # The oldest blind row: a ``dark`` open that is still open, far behind
        # thousands of newer blind rows -- a bounded read must still find it.
        log.append(blind_at(_t(-1), RUN, BlindReason.DARK, BlindState.OPENED, 0))
    return path


class _CountingConnection:
    """Wraps the reader's real connection and counts every row it hands back."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.rows = 0
        self.statements: list[str] = []

    def execute(self, sql, params=()):
        self.statements.append(sql)
        for row in self._real.execute(sql, params):
            self.rows += 1
            yield row

    def close(self) -> None:
        self._real.close()


def _count_rows_per_render(monkeypatch, log_path: Path, now: str):
    seen: list[_CountingConnection] = []
    real_connect = sqlite3.connect

    def counting_connect(*args, **kwargs):
        conn = _CountingConnection(real_connect(*args, **kwargs))
        seen.append(conn)
        return conn

    monkeypatch.setattr(reader_mod.sqlite3, "connect", counting_connect)
    view = build_view(health_config(), log_path, now=now)
    monkeypatch.undo()
    return view, sum(c.rows for c in seen), [s for c in seen for s in c.statements]


def test_a_render_fetches_a_bounded_number_of_rows_from_a_long_log(tmp_path, monkeypatch):
    small = _long_log(tmp_path / "small.db", frames=500, verdicts=600, blinds=100)
    big = _long_log(tmp_path / "big.db", frames=8000, verdicts=4000, blinds=2000)

    # Render each just after its own newest verdict, so both are fresh.
    small_view, small_rows, _ = _count_rows_per_render(monkeypatch, small, _t(60))
    big_view, big_rows, _ = _count_rows_per_render(monkeypatch, big, _t(400))

    bound = reader_mod.HELD_WALK_LIMIT + 16
    assert big_rows <= bound, f"render fetched {big_rows} rows (> {bound})"
    assert big_rows <= small_rows + 1  # does not grow with the Log
    # The old, still-open ``dark`` blind reason is found despite the bound.
    assert "dark" in big_view.blind_reasons and "dark" in small_view.blind_reasons
    assert big_view.state == "HEALTHY" and small_view.state == "HEALTHY"


def test_a_streak_longer_than_the_walk_reads_as_at_least_never_a_lie(tmp_path, monkeypatch):
    log_path = _long_log(
        tmp_path / "log.db", frames=1, verdicts=reader_mod.HELD_WALK_LIMIT * 3, blinds=0
    )
    newest = (reader_mod.HELD_WALK_LIMIT * 3 - 1) * 0.1
    view, _, _ = _count_rows_per_render(monkeypatch, log_path, _t(newest + 0.5))
    assert view.state == "HEALTHY"
    assert view.state_detail.startswith("held >=")
    # The lower bound is the walk window's oldest verdict, not the run's start.
    expected = (reader_mod.HELD_WALK_LIMIT - 1) * 0.1 + 0.5
    assert view.state_held_s == pytest.approx(expected, abs=0.01)


def test_a_short_streak_still_reports_the_exact_held_time(tmp_path):
    log_path = tmp_path / "log.db"
    with Log(log_path) as log:
        log.append(_verdict(_t(0), 1, VerdictState.FAULT))
        log.append(_verdict(_t(1), 2))
        log.append(_verdict(_t(2), 3))
    view = build_view(health_config(), log_path, now=_t(2.5))
    assert view.state_detail == "held 1.5s"
    assert view.state_held_s == pytest.approx(1.5, abs=0.01)


def test_every_render_query_uses_the_index_without_a_temp_sort(tmp_path, monkeypatch):
    log_path = _long_log(tmp_path / "log.db", frames=50, verdicts=50, blinds=10)
    _, _, statements = _count_rows_per_render(monkeypatch, log_path, _t(10))
    assert statements, "the render issued no queries"
    # Only queries over the records table must be index-backed and bounded; the
    # one-time ``sqlite_master`` probe for the blind-reason index is metadata.
    data_queries = [s for s in set(statements) if "FROM records" in s]
    assert data_queries, "the render issued no data queries"
    conn = sqlite3.connect(log_path)
    try:
        for sql in data_queries:
            params = (None,) * sql.count("?")
            plan = " | ".join(row[3] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params))
            assert (
                "USING INDEX records_kind_ts" in plan or "USING INDEX records_blind_reason" in plan
            ), f"{sql}: {plan}"
            assert "TEMP B-TREE" not in plan, f"{sql}: {plan}"
            assert "LIMIT" in sql.upper(), f"unbounded render query: {sql}"
    finally:
        conn.close()
