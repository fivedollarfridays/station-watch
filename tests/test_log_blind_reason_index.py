"""The blind-reason lookup is index-backed and bounded (HF3.2 (5)).

The Log writer adds an expression index ``records_blind_reason`` on
``(kind, json_extract(body, '$.reason'), ts)``. ``LogReader.newest_with_reason``
issues a query whose expression is the literal ``json_extract(body, '$.reason')``,
so SQLite can use that index: a lookup for a reason that is absent among tens of
thousands of blind rows of another reason must not scan them all.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from station_watch.board.reader import _NEWEST_WITH_REASON_SQL, LogReader
from station_watch.clock import offset_iso
from station_watch.log import Log
from station_watch.records import BlindReason, BlindState

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import blind_at  # noqa: E402

EPOCH = "2026-09-28T00:00:00.000000+00:00"
RUN = "run-index"


def _bulk_blind_log(path: Path, n: int, reason: BlindReason) -> Path:
    """A Log the real writer created (so the index exists), then ``n`` blind rows
    of one reason inserted in a single transaction for speed."""
    with Log(path):  # creates schema + indexes
        pass
    rows = []
    for seq in range(1, n + 1):
        record = blind_at(offset_iso(EPOCH, seq), RUN, reason, BlindState.OPENED, seq)
        data = record.to_dict()
        rows.append((data["record_id"], "blind", data["ts"], RUN, json.dumps(data)))
    conn = sqlite3.connect(str(path))
    try:
        conn.executemany(
            "INSERT INTO records (record_id, kind, ts, run_id, body) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return path


def test_explain_query_plan_names_the_blind_reason_index(tmp_path):
    path = _bulk_blind_log(tmp_path / "log.db", 100, BlindReason.FROZEN)
    conn = sqlite3.connect(str(path))
    try:
        plan = " | ".join(
            row[-1]
            for row in conn.execute(
                f"EXPLAIN QUERY PLAN {_NEWEST_WITH_REASON_SQL}", ("blind", "dark")
            )
        )
    finally:
        conn.close()
    assert "records_blind_reason" in plan, plan


def test_absent_reason_lookup_is_bounded_over_many_rows_of_another_reason(tmp_path):
    path = _bulk_blind_log(tmp_path / "log.db", 20_000, BlindReason.FROZEN)

    with LogReader(path) as reader:
        steps: list[int] = []
        reader.connection.set_progress_handler(lambda: steps.append(1), 1)
        found = reader.newest_with_reason("blind", "dark")  # absent among 20k frozen rows
        reader.connection.set_progress_handler(None, 0)

    assert found is None
    # An index seek touches a bounded slice; a full scan of 20k rows (each paying
    # a json_extract) would fire the progress handler orders of magnitude more.
    assert len(steps) < 2000, f"lookup fired {len(steps)} VM steps -- not index-bounded"


def test_newest_with_reason_finds_the_matching_row(tmp_path):
    path = _bulk_blind_log(tmp_path / "log.db", 50, BlindReason.FROZEN)
    with LogReader(path) as reader:
        found = reader.newest_with_reason("blind", "frozen")
    assert found is not None
    assert found.reason == BlindReason.FROZEN
    assert found.seq == 50  # the newest frozen row


def test_the_writer_creates_the_blind_reason_index(tmp_path):
    path = tmp_path / "log.db"
    with Log(path):
        pass
    conn = sqlite3.connect(str(path))
    try:
        names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    finally:
        conn.close()
    assert "records_blind_reason" in names
