"""Judge tests: healthy / fault / unobservable over fixtures and a real blind record.

The Judge is the only place "age versus window" and "absence is a fault" live. Its
inputs all come from the Log on one ``ts`` ordering: fixture observations (the
Detect output format, HF2 produces them for real) and the ``BlindRecord``s a real
Capture writes. The frozen-variant proving test therefore drives a real synthetic
clip through the real ``Capture.run`` so the blind record the Judge reads is the
shape production produces (README K14), not a hand-built stand-in.
"""

import sys
from pathlib import Path

from station_watch.capture import BlindThresholds, Capture, FrameSource
from station_watch.clock import offset_iso
from station_watch.config import load_station_config
from station_watch.fixtures import load_fixture_observations
from station_watch.judge import Judge
from station_watch.log import Log
from station_watch.records import (
    BlindReason,
    BlindRecord,
    BlindState,
    FaultKind,
    Observation,
    ObservationKind,
    VerdictState,
)

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import LiveCameraJudge  # noqa: E402
from helpers.synth_video import write_synth_clip  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(__file__).parent / "fixtures" / "station-slots.yaml"
FIXTURES = Path(__file__).parent / "fixtures" / "observations"
RUN = "run-judge-test"
START = "2026-09-28T00:00:00.000000+00:00"


def _config():
    return load_station_config(CONFIG)


def _judge(config):
    # Fixture observations over a live camera: fresh frames at every judged ts.
    return LiveCameraJudge(config, RUN)


def _at(offset_s):
    return offset_iso(START, offset_s)


def _load(log, name):
    for obs in load_fixture_observations(FIXTURES / name, START, RUN):
        log.append(obs)


def _kinds(verdict):
    return {fault.kind for fault in verdict.faults}


def _fault(verdict, kind):
    return next(fault for fault in verdict.faults if fault.kind == kind)


# --- healthy (AC2) ----------------------------------------------------------


def test_normal_cycles_yield_only_healthy(tmp_path):
    config = _config()
    log = Log(tmp_path / "normal.db")
    _load(log, "normal_cycles.jsonl")
    judge = _judge(config)

    for offset in (0.0, 15.0, 30.0):
        verdict = judge.judge(log, _at(offset))
        assert verdict.state == VerdictState.HEALTHY, offset
        assert verdict.faults == ()
        assert verdict.blind_reasons == ()


# --- missing part (AC2) -----------------------------------------------------


def test_missing_part_faults_on_the_slot_and_cites_its_frame(tmp_path):
    config = _config()
    log = Log(tmp_path / "missing.db")
    _load(log, "missing_part.jsonl")

    verdict = _judge(config).judge(log, _at(30.0))

    assert verdict.state == VerdictState.FAULT
    assert FaultKind.MISSING_PART in _kinds(verdict)
    fault = _fault(verdict, FaultKind.MISSING_PART)
    assert fault.target == "slot_a"
    assert 60 in fault.frame_ids  # part_absent observation lives at frame 60


# --- keep-out (AC2) ---------------------------------------------------------


def test_keepout_entry_faults_on_the_zone_and_cites_its_frames(tmp_path):
    config = _config()
    log = Log(tmp_path / "keepout.db")
    _load(log, "keepout_entry.jsonl")

    verdict = _judge(config).judge(log, _at(30.0))

    assert verdict.state == VerdictState.FAULT
    assert FaultKind.KEEPOUT_ENTRY in _kinds(verdict)
    fault = _fault(verdict, FaultKind.KEEPOUT_ENTRY)
    assert fault.target == "zone_press"
    assert set(fault.frame_ids) <= {45, 60}  # the person_in_keepout frames
    assert fault.frame_ids  # and it cites at least one


# --- stall (AC1) ------------------------------------------------------------


def test_stall_is_stalled_only_after_takt_plus_grace(tmp_path):
    config = _config()  # takt_s 30 + grace_s 5 -> a 35s window
    log = Log(tmp_path / "stall.db")
    _load(log, "stall.jsonl")
    judge = _judge(config)

    # 30s of no motion since the last motion frame is still inside the window.
    before = judge.judge(log, _at(30.0))
    assert not any(fault.kind == FaultKind.STALLED for fault in before.faults)

    # 45s later the line is past takt + grace: stalled, citing its no_motion frames.
    after = judge.judge(log, _at(45.0))
    assert after.state == VerdictState.FAULT
    stalled = _fault(after, FaultKind.STALLED)
    assert stalled.target == "zone_press"
    assert set(stalled.frame_ids) <= {30, 60, 90}
    assert stalled.frame_ids


def test_a_frozen_blind_record_makes_the_stall_timeline_unobservable(tmp_path):
    # Same stall timeline, but a real frozen BlindRecord from Capture is active.
    config = _config()
    log = Log(tmp_path / "frozen.db")
    _load(log, "stall.jsonl")

    clip = write_synth_clip(tmp_path / "frozen_clip", frames=20, freeze_from=3, fps=30, seed=1)
    thresholds = BlindThresholds(
        liveness_window_s=5.0,
        dark_luma_threshold=15.0,
        dark_window_s=0.06,
        frozen_frames=4,
        recover_good_frames=3,
        fiducial={
            "dictionary_id": "DICT_4X4_50",
            "marker_id": 0,
            "expected_center_px": [40, 40],
            "tolerance_px": 10,
            "window_s": 0.05,
        },
    )
    capture = Capture(
        FrameSource(str(clip)),
        station_id=config.station_id,
        camera_id=config.camera_id,
        run_id=RUN,
        sleep=lambda _s: None,
        clock=lambda: START,
    )
    capture.run(log, thresholds)

    verdict = Judge(config, run_id=RUN).judge(log, _at(100.0))

    assert verdict.state == VerdictState.UNOBSERVABLE
    assert BlindReason.FROZEN in verdict.blind_reasons
    assert verdict.faults == ()  # never a fault verdict from stale observations


def test_a_cleared_blind_record_is_not_active(tmp_path):
    # Opened then cleared (K11: re-observed good frames): no longer unobservable.
    config = _config()
    log = Log(tmp_path / "cleared.db")
    _load(log, "normal_cycles.jsonl")
    for seq, (state, offset) in enumerate(
        ((BlindState.OPENED, 5.0), (BlindState.CLEARED, 10.0)), start=1
    ):
        log.append(
            BlindRecord(
                station_id=config.station_id,
                camera_id=config.camera_id,
                ts=_at(offset),
                reason=BlindReason.FROZEN,
                evidence={},
                last_good_frame_id=1,
                state=state,
                seq=seq,
                run_id=RUN,
            )
        )

    verdict = _judge(config).judge(log, _at(30.0))

    assert verdict.state == VerdictState.HEALTHY
    assert verdict.blind_reasons == ()


# --- part_unknown (AC3) -----------------------------------------------------


def test_part_unknown_slot_is_neither_healthy_nor_missing_part(tmp_path):
    config = _config()
    log = Log(tmp_path / "unknown.db")
    _load(log, "normal_cycles.jsonl")  # a healthy baseline, both slots present
    # A later, most-recent reading of slot_a is unknown (occluded / too dark).
    log.append(
        Observation(
            station_id=config.station_id,
            frame_id=90,
            ts=_at(45.0),
            kind=ObservationKind.PART_UNKNOWN,
            target="slot_a",
            method="fixture",
            confidence_ceiling=0.3,
            detector_output={},
            run_id=RUN,
        )
    )

    verdict = _judge(config).judge(log, _at(45.0))

    assert verdict.state != VerdictState.HEALTHY
    assert not any(fault.kind == FaultKind.MISSING_PART for fault in verdict.faults)


# --- the verdict is written to the Log --------------------------------------


def test_verdict_is_appended_to_the_log(tmp_path):
    config = _config()
    log = Log(tmp_path / "written.db")
    _load(log, "normal_cycles.jsonl")

    verdict = _judge(config).judge(log, _at(30.0))

    stored = log.newest("verdict", station_id=config.station_id)
    assert stored is not None
    assert stored.record_id == verdict.record_id
    assert stored.state == verdict.state
