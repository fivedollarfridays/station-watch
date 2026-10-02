"""One undecodable row degrades to a per-row UNKNOWN; the walk goes on.

``LogReader.iter_newest`` yields an :class:`UndecodableRow` (record id, ts, run id,
reason) in place of a row it cannot rebuild and keeps walking, so one drifted row
no longer aborts a whole audit build or soak count. Each consumer treats it as
unknown, never as healthy: the audit sheet lists it as its own
``unknown:undecodable`` flag, QA walks every row and then fails closed naming the
unreadable ones, and the single-row ``newest`` still raises (Board shows UNKNOWN).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from station_watch.audit.flags import collect_flags
from station_watch.board.reader import BoardLogError, LogReader, UndecodableRow
from station_watch.clock import offset_iso
from station_watch.log import Log
from station_watch.qa import session_qa
from station_watch.records import Fault, FaultKind, Verdict, VerdictState

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import HEALTH_CONFIG  # noqa: E402

EPOCH = "2026-10-01T00:00:00.000000+00:00"
RUN = "run-undecodable"
QA_CONFIG = {**HEALTH_CONFIG, "qa": {"max_unknown_fraction": 0.9}}


def _t(offset: float) -> str:
    return offset_iso(EPOCH, offset)


def _verdict(seq: int, offset: float, faults=()) -> Verdict:
    state = VerdictState.FAULT if faults else VerdictState.HEALTHY
    return Verdict(
        station_id="station-1",
        ts=_t(offset),
        state=state,
        faults=tuple(faults),
        blind_reasons=(),
        seq=seq,
        run_id=RUN,
    )


def _log(path: Path, *, break_seq: int | None) -> Path:
    """Verdicts at 0..4 s (seq 1..5); ``break_seq``'s body drifts to a schema the
    reader cannot rebuild (valid JSON, so the Log's JSON expression index accepts it)."""
    missing = Fault(kind=FaultKind.MISSING_PART, target="rail_pos_1", frame_ids=(1,))
    with Log(path) as log:
        for seq in range(1, 6):
            log.append(_verdict(seq, seq - 1.0, [missing] if seq == 5 else ()))
    if break_seq is not None:
        with sqlite3.connect(path) as conn:
            record_id = f"{RUN}:verdict:station-1:{break_seq}"
            conn.execute(
                "UPDATE records SET body = ? WHERE record_id = ?",
                (f'{{"record_id": "{record_id}", "drifted": true}}', record_id),
            )
    return path


def test_iter_newest_yields_an_unknown_row_in_place_and_keeps_walking(tmp_path):
    with LogReader(_log(tmp_path / "log.db", break_seq=3)) as reader:
        rows = list(reader.iter_newest("verdict", limit=-1))
    assert len(rows) == 5
    bad = rows[2]
    assert isinstance(bad, UndecodableRow)
    assert bad.record_id == f"{RUN}:verdict:station-1:3"
    assert bad.ts == _t(2.0) and bad.run_id == RUN and bad.reason
    assert bad.state == "undecodable" and bad.state != VerdictState.HEALTHY
    assert all(isinstance(r, Verdict) for i, r in enumerate(rows) if i != 2)


def test_newest_still_raises_when_the_newest_row_is_undecodable(tmp_path):
    with LogReader(_log(tmp_path / "log.db", break_seq=5)) as reader:
        with pytest.raises(BoardLogError):
            reader.newest("verdict")


def test_qa_walks_every_row_then_fails_closed_naming_the_undecodable_ones(tmp_path):
    intact = session_qa(QA_CONFIG, _log(tmp_path / "a.db", break_seq=None))
    broken = session_qa(QA_CONFIG, _log(tmp_path / "b.db", break_seq=3))
    assert intact.status == "pass"
    assert broken.status == "fail"
    assert "1 undecodable verdict row" in broken.reason
    assert f"{RUN}:verdict:station-1:3" in broken.reason


def test_audit_lists_an_undecodable_row_as_its_own_unknown_flag(tmp_path):
    with LogReader(_log(tmp_path / "log.db", break_seq=3)) as reader:
        flags = collect_flags(reader)
    kinds = sorted(f.kind for f in flags)
    assert kinds == ["missing_part", "unknown:undecodable"]
    unknown = next(f for f in flags if f.kind == "unknown:undecodable")
    assert unknown.flag_id == f"{RUN}:verdict:station-1:3" and unknown.run_id == RUN
