"""Measured step times: step_durations, step_stats, and the stall-window source.

A *step* is a contiguous run of ``motion`` observations in the station zone,
closed (bounded) by the ``no_motion`` that ends it. :func:`step_durations` pulls
those steps out of a flat observation list; :func:`step_stats` reduces their
durations to count / p50 / p95; :func:`write_step_times` / :func:`read_step_times`
round-trip the committed ``step_times.json``; and :func:`resolve_stall_window`
turns a config into the Judge's stall window and a human source string -- the
measured p95 plus grace when ``detect.step_times_path`` points at a real file,
else ``takt_s + grace_s``, and a loud failure when the configured path is absent.
"""

from __future__ import annotations

import json

import pytest

from station_watch.clock import offset_iso
from station_watch.config import StationConfig
from station_watch.records import Observation, ObservationKind
from station_watch.steps import (
    Step,
    StepTimesError,
    read_step_times,
    resolve_stall_window,
    step_durations,
    step_stats,
    write_step_times,
)

BASE = "2026-01-01T00:00:00.000000+00:00"
ZONE = "bench"


def _obs(kind, offset_s, frame_id, target=ZONE) -> Observation:
    return Observation(
        station_id="station-1",
        frame_id=frame_id,
        ts=offset_iso(BASE, offset_s),
        kind=kind,
        target=target,
        method="station_zone_motion:frame_diff:v1",
        confidence_ceiling=1.0,
        detector_output={},
        run_id="run-test",
    )


M = ObservationKind.MOTION
N = ObservationKind.NO_MOTION


# --- AC3: step_durations over hand-built sequences ---------------------------


def test_step_durations_closes_a_run_on_the_bounding_no_motion():
    observations = [
        _obs(M, 0.0, 0),
        _obs(M, 1.0, 10),
        _obs(N, 2.0, 20),  # closes step 1: frame 0 -> 20, 2.0 s
        _obs(M, 5.0, 50),
        _obs(N, 8.0, 80),  # closes step 2: frame 50 -> 80, 3.0 s
        _obs(M, 10.0, 100),  # trailing, never closed -> not a step
    ]
    steps = step_durations(observations)

    assert [s.duration_s for s in steps] == [2.0, 3.0]
    first = steps[0]
    assert isinstance(first, Step)
    assert first.target == ZONE
    assert (first.start_frame_id, first.end_frame_id) == (0, 20)
    assert first.start_ts == offset_iso(BASE, 0.0)
    assert first.end_ts == offset_iso(BASE, 2.0)


def test_step_durations_ignores_other_kinds_and_separates_targets():
    observations = [
        Observation(
            station_id="station-1",
            frame_id=1,
            ts=offset_iso(BASE, 0.0),
            kind=ObservationKind.PART_PRESENT,
            target="rail_pos_1",
            method="rail",
            confidence_ceiling=1.0,
            detector_output={},
            run_id="run-test",
        ),
        _obs(M, 0.0, 0, target="bench"),
        _obs(N, 1.0, 10, target="bench"),
        _obs(M, 0.0, 0, target="other_zone"),
        _obs(N, 4.0, 40, target="other_zone"),
    ]
    steps = step_durations(observations)

    by_target = {s.target: s for s in steps}
    assert set(by_target) == {"bench", "other_zone"}
    assert by_target["bench"].duration_s == 1.0
    assert by_target["other_zone"].duration_s == 4.0


def test_step_durations_is_empty_without_any_closed_run():
    assert step_durations([]) == []
    assert step_durations([_obs(M, 0.0, 0), _obs(M, 1.0, 10)]) == []  # never bounded


# --- AC3: step_stats count / p50 / p95 --------------------------------------


def test_step_stats_count_p50_p95_on_known_durations():
    steps = [
        Step(ZONE, 0, 1, BASE, BASE, 2.0),
        Step(ZONE, 2, 3, BASE, BASE, 3.0),
    ]
    stats = step_stats(steps)

    assert stats["count"] == 2
    assert stats["p50_s"] == 2.5
    assert stats["p95_s"] == pytest.approx(2.95)


def test_step_stats_on_a_single_step_and_on_none():
    one = step_stats([Step(ZONE, 0, 1, BASE, BASE, 7.0)])
    assert one == {"count": 1, "p50_s": 7.0, "p95_s": 7.0}

    empty = step_stats([])
    assert empty["count"] == 0
    assert empty["p50_s"] == 0.0
    assert empty["p95_s"] == 0.0


# --- AC5 / counterparty: write_step_times round-trips its schema -------------


def test_write_and_read_step_times_round_trip(tmp_path):
    path = tmp_path / "step_times.json"
    stats = {"count": 12, "p50_s": 20.0, "p95_s": 28.5}
    provenance = {"dataset": "synthetic", "detector": "station_zone_motion:v1"}

    write_step_times(path, stats, provenance)

    payload = json.loads(path.read_text())
    assert payload["provenance"] == provenance
    assert payload["metrics"] == {"count": 12, "p50_s": 20.0, "p95_s": 28.5}

    metrics = read_step_times(path)
    assert metrics["p95_s"] == 28.5
    assert metrics["count"] == 12


# --- AC4: the stall window's source ------------------------------------------


def _config(**detect_over) -> StationConfig:
    detect = {
        "persistence_frames": 2,
        "emit_interval_s": 5.0,
        "rail_positions": {},
        "station_zone": {"id": ZONE, "region": [[-4, -3], [4, -3], [4, 3], [-4, 3]]},
        "keepout_rois": {},
        "blur_threshold": 100.0,
        "darkness_threshold": 40.0,
        "occlusion_threshold": 0.5,
    }
    detect.update(detect_over)
    return StationConfig.from_mapping(
        {
            "station_id": "station-1",
            "camera_id": "cam-0",
            "takt_s": 30.0,
            "grace_s": 5.0,
            "required_slots": [],
            "keepout_zones": [],
            "liveness_window_s": 2.0,
            "dark_luma_threshold": 15.0,
            "dark_window_s": 2.0,
            "frozen_frames": 10_000,
            "recover_good_frames": 3,
            "cycle_interval_s": 0.1,
            "recover_healthy_verdicts": 2,
            "fiducial": {
                "dictionary_id": "DICT_4X4_50",
                "marker_id": 0,
                "expected_center_px": [40, 40],
                "tolerance_px": 10,
            },
            "alarm": {"sinks": ["screen"]},
            "watchdog": {"cycle_window_s": 10.0, "alarm_eval_window_s": 10.0, "sinks": ["screen"]},
            "detect": detect,
        }
    )


def test_absent_path_key_uses_takt_plus_grace():
    window, source = resolve_stall_window(_config())
    assert window == 35.0  # takt 30 + grace 5
    assert "configured takt" in source
    assert "no measured step times" in source


def test_present_file_uses_p95_plus_grace(tmp_path):
    path = tmp_path / "step_times.json"
    write_step_times(path, {"count": 20, "p50_s": 18.0, "p95_s": 24.0}, {"dataset": "synthetic"})

    window, source = resolve_stall_window(_config(step_times_path=str(path)))
    assert window == 29.0  # p95 24 + grace 5
    assert str(path) in source
    assert "24.0" in source


def test_configured_path_that_does_not_exist_fails_loud_naming_it(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(StepTimesError) as exc:
        resolve_stall_window(_config(step_times_path=str(missing)))
    assert str(missing) in str(exc.value)


def test_configured_path_that_is_malformed_fails_loud_naming_it(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(StepTimesError) as exc:
        resolve_stall_window(_config(step_times_path=str(bad)))
    assert str(bad) in str(exc.value)


def test_zero_steps_writes_a_status_with_no_metrics(tmp_path):
    # K13: nothing measured is a status, never a 0.0 s "measured" step time.
    path = tmp_path / "step_times.json"
    write_step_times(path, {"count": 0, "p50_s": 0.0, "p95_s": 0.0}, {"step_clips": 4})
    data = json.loads(path.read_text())
    assert data["status"] == "no_steps_measured"
    assert "metrics" not in data
    assert data["provenance"]["step_clips"] == 4


def test_a_no_steps_file_fails_loud_at_the_consumer(tmp_path):
    path = tmp_path / "step_times.json"
    write_step_times(path, {"count": 0, "p50_s": 0.0, "p95_s": 0.0}, {})
    with pytest.raises(StepTimesError) as exc:
        resolve_stall_window(_config(step_times_path=str(path)))
    assert "no_steps_measured" in str(exc.value) and str(path) in str(exc.value)


def test_a_zero_count_metrics_file_fails_loud_at_the_consumer(tmp_path):
    # The degenerate shape an older producer committed: count 0, p95 0.0.
    path = tmp_path / "step_times.json"
    payload = {"provenance": {}, "metrics": {"count": 0, "p50_s": 0.0, "p95_s": 0.0}}
    path.write_text(json.dumps(payload))
    with pytest.raises(StepTimesError) as exc:
        resolve_stall_window(_config(step_times_path=str(path)))
    assert "no measured steps" in str(exc.value)
