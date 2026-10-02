"""`collect_flags` turns a session's Log into deterministic fault/blind episodes."""

from __future__ import annotations

from station_watch.audit.flags import collect_flags
from station_watch.board.reader import LogReader
from station_watch.clock import offset_iso
from station_watch.log import Log
from station_watch.records import (
    BlindReason,
    BlindRecord,
    BlindState,
    Fault,
    FaultKind,
    Verdict,
    VerdictState,
)

EPOCH = "2026-10-01T00:00:00.000000+00:00"
RUN = "run-flags"


def _t(offset: float) -> str:
    return offset_iso(EPOCH, offset)


def _verdict(seq, offset, faults, state=VerdictState.FAULT, run_id=RUN):
    return Verdict(
        station_id="station-1",
        ts=_t(offset),
        state=state,
        faults=tuple(faults),
        blind_reasons=(),
        seq=seq,
        run_id=run_id,
    )


def _missing(target, frame_ids):
    return Fault(kind=FaultKind.MISSING_PART, target=target, frame_ids=tuple(frame_ids))


def _blind(seq, offset, reason, state, last_good=None):
    return BlindRecord(
        station_id="station-1",
        camera_id="cam-0",
        ts=_t(offset),
        reason=reason,
        evidence={},
        last_good_frame_id=last_good,
        state=state,
        seq=seq,
        run_id=RUN,
    )


def _write(path, records):
    with Log(path) as log:
        for record in records:
            log.append(record)


def _flags(path):
    with LogReader(path) as reader:
        return collect_flags(reader)


def test_contiguous_fault_run_is_one_episode_with_the_frame_union(tmp_path):
    log = tmp_path / "log.db"
    _write(
        log,
        [
            _verdict(1, 0, [_missing("rail_pos_1", [1])]),
            _verdict(2, 1, [_missing("rail_pos_1", [2, 3])]),
            _verdict(3, 2, [], state=VerdictState.HEALTHY),
        ],
    )
    flags = _flags(log)
    assert len(flags) == 1
    flag = flags[0]
    assert flag.flag_id == f"{RUN}:missing_part:rail_pos_1:1"
    assert flag.kind == "missing_part"
    assert flag.target == "rail_pos_1"
    assert flag.opened_ts == _t(0)
    assert flag.closed_ts == _t(1)  # the last verdict that carried the fault
    assert flag.frame_ids == (1, 2, 3)


def test_a_fault_that_recurs_after_a_gap_is_two_episodes(tmp_path):
    log = tmp_path / "log.db"
    _write(
        log,
        [
            _verdict(1, 0, [_missing("rail_pos_1", [1])]),
            _verdict(2, 1, [], state=VerdictState.HEALTHY),
            _verdict(3, 2, [_missing("rail_pos_1", [9])]),
        ],
    )
    flags = _flags(log)
    ids = [f.flag_id for f in flags]
    assert ids == [f"{RUN}:missing_part:rail_pos_1:1", f"{RUN}:missing_part:rail_pos_1:3"]


def test_blind_opened_pairs_with_cleared_and_an_open_one_has_no_close(tmp_path):
    log = tmp_path / "log.db"
    _write(
        log,
        [
            _blind(1, 0, BlindReason.DARK, BlindState.OPENED, last_good=5),
            _blind(2, 2, BlindReason.DARK, BlindState.CLEARED),
            _blind(3, 3, BlindReason.FROZEN, BlindState.OPENED, last_good=None),
        ],
    )
    flags = {f.flag_id: f for f in _flags(log)}
    dark = flags[f"{RUN}:blind:dark:1"]
    assert dark.kind == "unobservable:dark"
    assert dark.target == "dark"
    assert dark.opened_ts == _t(0) and dark.closed_ts == _t(2)
    assert dark.frame_ids == (5,)
    frozen = flags[f"{RUN}:blind:frozen:3"]
    assert frozen.closed_ts is None
    assert frozen.frame_ids == ()


def test_flag_ids_are_identical_across_two_passes(tmp_path):
    log = tmp_path / "log.db"
    _write(
        log,
        [
            _verdict(1, 0, [_missing("rail_pos_1", [1])]),
            _blind(1, 1, BlindReason.DARK, BlindState.OPENED, last_good=2),
            _verdict(2, 2, [_missing("rail_pos_2", [3])]),
        ],
    )
    first = [f.flag_id for f in _flags(log)]
    second = [f.flag_id for f in _flags(log)]
    assert first == second
    assert len(first) == 3


def test_a_fault_still_present_across_two_runs_is_one_episode_per_run(tmp_path):
    # Run A ends faulted and run B starts faulted with no clearing verdict between:
    # two sessions are two episodes, each stamped with its own run.
    log = tmp_path / "log.db"
    _write(
        log,
        [
            _verdict(1, 0, [_missing("rail_pos_1", [3])], run_id="run-a"),
            _verdict(1, 5, [_missing("rail_pos_1", [4])], run_id="run-b"),
        ],
    )
    flags = _flags(log)
    assert [(f.run_id, f.frame_ids) for f in flags] == [("run-a", (3,)), ("run-b", (4,))]
