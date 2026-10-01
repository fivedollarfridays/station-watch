"""Cycle-time slope alarm (K12): rate, not level.

Over the last ``detect.slope.window_steps`` completed station-zone steps (HF2.4's
``step_durations``), the Judge fits a least-squares slope of step duration against
step index. A slope at least ``detect.slope.min_s_per_step`` -- while the newest
step is still *under* the stall window -- raises ``cycle_time_creep`` on the
station zone, citing the first and last frame ids of every step in the window
(K4). Fewer than ``window_steps`` steps is no verdict either way.

The proving test drives the real Judge and Alarm: a clip whose steps lengthen
steadily (each under the stall window) must raise creep before any ``stalled``
and open exactly one episode (K11/K12). Every step is a ``motion`` run closed by
the ``no_motion`` that ends it, so the clip is the same observation shape Detect
produces for real.
"""

import json
import sys
from pathlib import Path

from station_watch.alarm import Alarm, RecordSink
from station_watch.clock import offset_iso
from station_watch.log import Log
from station_watch.records import FaultKind, Observation, ObservationKind, VerdictState

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import HEALTH_CONFIG, LiveCameraJudge, health_config  # noqa: E402

RUN = "run-creep-test"
START = "2026-09-28T00:00:00.000000+00:00"
ZONE = "bench"

M = ObservationKind.MOTION
N = ObservationKind.NO_MOTION


def _slope_config(window_steps=5, min_s_per_step=2.0, **over):
    detect = {**HEALTH_CONFIG["detect"], "slope": {
        "window_steps": window_steps,
        "min_s_per_step": min_s_per_step,
    }}
    return health_config(detect=detect, **over)


def _obs(kind, offset_s, frame_id):
    return Observation(
        station_id=HEALTH_CONFIG["station_id"],
        frame_id=frame_id,
        ts=offset_iso(START, offset_s),
        kind=kind,
        target=ZONE,
        method="station_zone_motion:frame_diff:v1",
        confidence_ceiling=1.0,
        detector_output={},
        run_id=RUN,
    )


def _lengthening_steps():
    """Five closed steps of 5, 10, 15, 20, 25 s -- each under a 35 s stall window."""
    plan = [(0.0, 5.0), (6.0, 16.0), (17.0, 32.0), (33.0, 53.0), (54.0, 79.0)]
    rows, frame = [], 0
    for start_s, end_s in plan:
        rows.append(_obs(M, start_s, frame))
        rows.append(_obs(N, end_s, frame + 10))
        frame += 20
    return rows


def _steady_steps():
    """Five closed steps near 20 s with ordinary jitter -- a flat slope."""
    plan = [(0.0, 20.0), (21.0, 40.0), (41.0, 62.0), (63.0, 83.0), (84.0, 103.0)]
    rows, frame = [], 0
    for start_s, end_s in plan:
        rows.append(_obs(M, start_s, frame))
        rows.append(_obs(N, end_s, frame + 10))
        frame += 20
    return rows


def _frame_ids(rows):
    return {obs.frame_id for obs in rows}


def _end_offset(rows):
    from station_watch.clock import parse_iso

    return max((parse_iso(obs.ts) - parse_iso(START)).total_seconds() for obs in rows)


def _verdicts(config, rows, tmp_path, judge_offsets):
    """Append the clip, then judge the station at each offset; return the verdicts."""
    log = Log(tmp_path / "creep.db")
    for obs in rows:
        log.append(obs)
    judge = LiveCameraJudge(config, RUN)
    return [judge.judge(log, offset_iso(START, off)) for off in judge_offsets], log


# --- AC1: the proving clip --------------------------------------------------


def test_lengthening_steps_raise_creep_before_stall_and_open_one_episode(tmp_path):
    config = _slope_config()  # takt 30 + grace 5 -> 35 s stall window
    rows = _lengthening_steps()
    offsets = [5.0, 16.0, 32.0, 53.0, 79.0, 85.0]

    log = Log(tmp_path / "creep.db")
    for obs in rows:
        log.append(obs)
    judge = LiveCameraJudge(config, RUN)
    alarm = Alarm(config, run_id=RUN, sinks=[RecordSink(tmp_path / "alarms.jsonl")])

    seen_creep = False
    for off in offsets:
        verdict = judge.judge(log, offset_iso(START, off))
        kinds = {f.kind for f in verdict.faults}
        assert FaultKind.STALLED not in kinds, off  # never stalls: each step is under the window
        seen_creep = seen_creep or FaultKind.CYCLE_TIME_CREEP in kinds
        alarm.evaluate(verdict, log)

    assert seen_creep  # creep did fire

    events = [json.loads(line) for line in (tmp_path / "alarms.jsonl").read_text().splitlines()]
    alarms = [e for e in events if e["event"] == "alarm"]
    assert len(alarms) == 1  # exactly one episode opened
    assert alarms[0]["cause"] == f"{FaultKind.CYCLE_TIME_CREEP.value}:{ZONE}"


# --- AC2: steady times and too-few steps are quiet --------------------------


def test_steady_steps_with_jitter_raise_no_creep(tmp_path):
    config = _slope_config()
    rows = _steady_steps()
    verdicts, _ = _verdicts(config, rows, tmp_path, [_end_offset(rows)])

    assert all(
        FaultKind.CYCLE_TIME_CREEP not in {f.kind for f in v.faults} for v in verdicts
    )
    assert verdicts[-1].state == VerdictState.HEALTHY


def test_fewer_than_window_steps_raise_no_creep(tmp_path):
    config = _slope_config(window_steps=5)
    # Four steeply lengthening steps -- a steep slope, but one short of the window.
    rows = _lengthening_steps()[:8]  # 4 steps (2 rows each)
    verdicts, _ = _verdicts(config, rows, tmp_path, [_end_offset(rows)])

    assert all(
        FaultKind.CYCLE_TIME_CREEP not in {f.kind for f in v.faults} for v in verdicts
    )


# --- AC3: the fault cites frames in the clip --------------------------------


def test_creep_fault_cites_frame_ids_present_in_the_clip(tmp_path):
    config = _slope_config()
    rows = _lengthening_steps()
    verdicts, _ = _verdicts(config, rows, tmp_path, [_end_offset(rows)])

    creep = next(
        f for v in verdicts for f in v.faults if f.kind == FaultKind.CYCLE_TIME_CREEP
    )
    assert creep.target == ZONE
    assert creep.frame_ids  # cites at least one frame
    assert set(creep.frame_ids) <= _frame_ids(rows)  # all cited frames are in the clip
