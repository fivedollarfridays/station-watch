"""Motion, dwell and the measured stall window, wired through the real pipeline.

The proving test runs a clip with periodic tool motion then a still period through
the *real* Capture and Detect and shows the Judge calls ``stalled`` only once the
no-motion run outlives the window, citing the still frames. A frozen feed, caught
by Capture's ``frozen`` record, reads ``unobservable`` -- never ``stalled`` off the
still frames the frozen sensor keeps repeating. The last two tests exercise the
real ``station-watch run`` CLI: its startup line names the stall threshold's source
(configured takt vs measured step times), a configured ``step_times_path`` that
does not exist refuses to start, and the end-to-end loop -- run, ``write_step_times``
over the Log's steps, run again pointing at that file -- stalls at p95 + grace.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from station_watch.capture import BlindThresholds, Capture, FrameSource
from station_watch.clock import offset_iso, utc_now_iso
from station_watch.config import StationConfig
from station_watch.detect.detector import Detector
from station_watch.judge import Judge
from station_watch.log import Log
from station_watch.records import BlindReason, FaultKind, ObservationKind, VerdictState
from station_watch.steps import read_step_times, step_durations, step_stats, write_step_times

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import LiveCameraJudge  # noqa: E402
from helpers.synth_station import write_synth_station_clip  # noqa: E402
from helpers.synth_video import _write_frames  # noqa: E402

EPOCH = "0001-01-01T00:00:00.000000+00:00"
RUN = "run-motion-test"
STATION_ZONE = {
    "id": "bench",
    "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]],
    "track_motion": True,
}


def _config_dict(**over) -> dict:
    cfg = {
        "station_id": "station-1",
        "camera_id": "cam-0",
        "takt_s": 30.0,
        "grace_s": 5.0,
        "required_slots": [],
        "keepout_zones": [],
        "liveness_window_s": 100.0,
        "dark_luma_threshold": 15.0,
        "dark_window_s": 2.0,
        "frozen_frames": 10_000,
        "recover_good_frames": 3,
        "cycle_interval_s": 0.1,
        "recover_healthy_verdicts": 2,
        "fiducial": {
            "dictionary_id": "DICT_4X4_50",
            "marker_id": 0,
            "expected_center_px": [58, 58],
            "tolerance_px": 10,
            "window_s": 10_000.0,
        },
        "alarm": {"sinks": ["record"]},
        "watchdog": {"cycle_window_s": 10.0, "alarm_eval_window_s": 10.0, "sinks": ["record"]},
        "detect": {
            "persistence_frames": 2,
            "emit_interval_s": 0.05,
            "rail_positions": {},
            "station_zone": STATION_ZONE,
            "keepout_rois": {},
            "blur_threshold": 100.0,
            "darkness_threshold": 40.0,
            "occlusion_threshold": 0.5,
        },
    }
    detect_over = over.pop("detect", {})
    cfg.update(over)
    cfg["detect"].update(detect_over)
    return cfg


def _capture(config: StationConfig, clip, run_id, clock=None) -> Log:
    log = Log(Path(clip).parent / f"{run_id}.db")
    capture = Capture(
        FrameSource(str(clip)),
        station_id=config.station_id,
        camera_id=config.camera_id,
        run_id=run_id,
        sleep=lambda _s: None,
        detector=Detector(config, run_id),
        clock=clock or utc_now_iso,
    )
    capture.run(log, BlindThresholds.from_station_config(config))
    return log


# --- AC1: periodic motion then a still period -> stalled only after the window -


def test_stalled_only_after_the_window_citing_still_frames(tmp_path):
    config = StationConfig.from_mapping(_config_dict(takt_s=1.0, grace_s=0.5))  # 1.5 s window
    script = [{"motion": True} for _ in range(4)] + [{} for _ in range(6)]
    clip, _truth = write_synth_station_clip(
        tmp_path / "clip", script, station_zone=STATION_ZONE, fps=20.0
    )
    # Real Capture + Detect at a fixed clock, so every observation shares one ts and
    # the Judge's `now` alone decides whether the no-motion run has outlived the window.
    log = _capture(config, clip, RUN, clock=lambda: offset_iso(EPOCH, 0.0))

    obs = log.since(EPOCH, ["obs"])
    motion = [o for o in obs if o.kind is ObservationKind.MOTION and o.target == "bench"]
    no_motion = [o for o in obs if o.kind is ObservationKind.NO_MOTION and o.target == "bench"]
    assert motion, "the periodic tool motion must read as motion"
    assert no_motion, "the still period must read as no_motion"
    still_frames = {o.frame_id for o in no_motion}

    judge = LiveCameraJudge(config, RUN)  # default window = takt + grace = 1.5 s
    before = judge.judge(log, offset_iso(EPOCH, 1.4))
    assert not any(f.kind is FaultKind.STALLED for f in before.faults), "inside the window"

    after = judge.judge(log, offset_iso(EPOCH, 1.6))
    assert after.state is VerdictState.FAULT
    stalled = next(f for f in after.faults if f.kind is FaultKind.STALLED)
    assert stalled.target == "bench"
    assert stalled.frame_ids
    assert set(stalled.frame_ids) <= still_frames, "cites the still frames, not the moving ones"


# --- AC2: a frozen feed is unobservable (its frozen record), never stalled -----


def test_frozen_feed_is_unobservable_never_stalled(tmp_path):
    config = StationConfig.from_mapping(_config_dict(takt_s=1.0, grace_s=0.5, frozen_frames=3))
    script = [{"motion": True} for _ in range(3)] + [{} for _ in range(3)]
    base_clip, _truth = write_synth_station_clip(
        tmp_path / "src", script, station_zone=STATION_ZONE, fps=20.0
    )
    frames = _read_frames(base_clip)
    frozen = frames + [frames[-1].copy() for _ in range(5)]  # identical bytes -> frozen sensor
    frozen_dir = tmp_path / "frozen"
    frozen_dir.mkdir()
    clip = _write_frames(frozen_dir, frozen, 20.0)

    log = _capture(config, clip, RUN, clock=lambda: offset_iso(EPOCH, 0.0))

    blind = log.since(EPOCH, ["blind"])
    assert any(b.reason is BlindReason.FROZEN for b in blind), "Capture catches the frozen sensor"

    # 100 s past the one shared ts: the no-motion run is far older than the window,
    # yet the active frozen record makes the station unobservable, not stalled.
    verdict = Judge(config, run_id=RUN, stall_window_s=1.5).judge(log, offset_iso(EPOCH, 100.0))
    assert verdict.state is VerdictState.UNOBSERVABLE
    assert BlindReason.FROZEN in verdict.blind_reasons
    assert not any(f.kind is FaultKind.STALLED for f in verdict.faults)


def _read_frames(path):
    import cv2

    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    return frames


# --- AC4 / AC6: the real `station-watch run` startup line and the measured window


def _write_config(path, **over):
    Path(path).write_text(yaml.safe_dump(_config_dict(**over)))
    return path


def _run_cli(*args, timeout=120):
    return subprocess.run(
        [sys.executable, "-m", "station_watch", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _motion_clip(tmp_path, name, script, fps=20.0):
    clip, _truth = write_synth_station_clip(
        tmp_path / name, script, station_zone=STATION_ZONE, fps=fps
    )
    return clip


def test_startup_line_names_configured_takt_when_no_measured_file(tmp_path):
    config = _write_config(tmp_path / "station.yaml", takt_s=30.0, grace_s=5.0)
    clip = _motion_clip(tmp_path, "clip", [{"motion": True} for _ in range(4)])
    result = _run_cli(
        "run", "--config", str(config), "--source", str(clip),
        "--log", str(tmp_path / "log.db"), "--alarm-record", str(tmp_path / "a.jsonl"),
    )
    assert result.returncode == 0, result.stderr
    assert "stall threshold 35.0 s (configured takt; no measured step times)" in result.stdout


def test_configured_step_times_path_that_does_not_exist_refuses_to_start(tmp_path):
    missing = tmp_path / "step_times.json"
    config = _write_config(tmp_path / "station.yaml", detect={"step_times_path": str(missing)})
    clip = _motion_clip(tmp_path, "clip", [{"motion": True} for _ in range(3)])
    result = _run_cli(
        "run", "--config", str(config), "--source", str(clip),
        "--log", str(tmp_path / "log.db"), "--alarm-record", str(tmp_path / "a.jsonl"),
    )
    assert result.returncode != 0
    assert str(missing) in result.stderr
    assert "step_times" in result.stderr


def test_e2e_measured_window_from_a_real_run_then_stalls_at_p95_plus_grace(tmp_path):
    # Run 1: a normal clip of motion/still cycles, no step-times file yet.
    cfg1 = _write_config(tmp_path / "station1.yaml", takt_s=30.0, grace_s=0.5)
    normal = []
    for _ in range(3):
        normal += [{"motion": True} for _ in range(3)] + [{} for _ in range(3)]
    clip1 = _motion_clip(tmp_path, "normal", normal)
    log1 = tmp_path / "log1.db"
    r1 = _run_cli(
        "run", "--config", str(cfg1), "--source", str(clip1),
        "--log", str(log1), "--alarm-record", str(tmp_path / "a1.jsonl"), "--speed", "5",
    )
    assert r1.returncode == 0, r1.stderr
    assert "configured takt; no measured step times" in r1.stdout

    # write_step_times over the steps read from that run's Log (the producer HF2.8 calls).
    with Log(log1) as log:
        steps = step_durations(log.since(EPOCH, ["obs"]))
    assert steps, "the normal run must yield at least one closed step"
    stats = step_stats(steps)
    step_times = tmp_path / "step_times.json"
    write_step_times(step_times, stats, {"dataset": "synthetic", "detector": "station_zone_motion"})
    p95 = read_step_times(step_times)["p95_s"]
    window = p95 + 0.5

    # Run 2: the same station, now pointing at the measured file, over a long still clip.
    cfg2 = _write_config(
        tmp_path / "station2.yaml", takt_s=30.0, grace_s=0.5,
        detect={"step_times_path": str(step_times)},
    )
    still = _motion_clip(tmp_path, "still", [{} for _ in range(50)], fps=20.0)
    log2 = tmp_path / "log2.db"
    r2 = _run_cli(
        "run", "--config", str(cfg2), "--source", str(still),
        "--log", str(log2), "--alarm-record", str(tmp_path / "a2.jsonl"),
    )
    assert r2.returncode == 0, r2.stderr
    assert "measured step times" in r2.stdout, r2.stdout
    assert f"stall threshold {window:.1f} s" in r2.stdout, (r2.stdout, window)

    with Log(log2) as log:
        verdicts = log.since(EPOCH, ["verdict"])
    stalled = [
        f
        for v in verdicts
        if v.state is VerdictState.FAULT
        for f in v.faults
        if f.kind is FaultKind.STALLED
    ]
    assert stalled, "a long still period past the measured window stalls"
