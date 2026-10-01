"""The keep-out model's lifecycle: weights are fetched, hash-checked, never run online.

These tests cover the weight file's whole contract without committing it:

* startup with keep-out zones configured but weights missing or hash-mismatched
  refuses to start, naming the file (K9);
* ``run`` opens no socket -- it completes on a synthetic clip with a fake backend
  even when socket creation is blocked (the only network call is ``fetch-model``);
* no model weight file is tracked by git;
* the real YOLOX model runs on a generated frame when the weights are present,
  and is skipped (visibly, never a silent pass) when they are not.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

from station_watch.alarm.sink import build_sinks
from station_watch.capture.blind import BlindThresholds
from station_watch.capture.source import FrameSource
from station_watch.config import load_station_config
from station_watch.detect.yolox import MODEL_SHA256, default_model_path, verify_weights
from station_watch.log import Log
from station_watch.run import new_run_id
from station_watch.runner.pipeline import Runner
from station_watch.runner.startup import RunContext, resolve_stall_window_or_fail

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_station import write_synth_station_clip  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KEEPOUT = {
    "zone_press": {"region": [[4.0, 1.9], [5.3, 1.9], [5.3, 3.0], [4.0, 3.0]], "active": True}
}
STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}
MARKER_CENTER = [58, 58]
EPOCH = "0001-01-01T00:00:00.000000+00:00"

BASE = {
    "station_id": "station-1",
    "camera_id": "cam-0",
    "takt_s": 30.0,
    "grace_s": 5.0,
    "required_slots": [],
    "keepout_zones": ["zone_press"],
    "liveness_window_s": 5.0,
    "dark_luma_threshold": 15.0,
    "dark_window_s": 2.0,
    "frozen_frames": 10000,
    "recover_good_frames": 3,
    "cycle_interval_s": 0.02,
    "recover_healthy_verdicts": 2,
    "fiducial": {
        "dictionary_id": "DICT_4X4_50",
        "marker_id": 0,
        "expected_center_px": MARKER_CENTER,
        "tolerance_px": 10,
        "window_s": 10000.0,
    },
    "alarm": {"sinks": ["screen"]},
    "watchdog": {"cycle_window_s": 10.0, "alarm_eval_window_s": 10.0, "sinks": ["screen"]},
    "detect": {
        "persistence_frames": 2,
        "emit_interval_s": 5.0,
        "rail_positions": {},
        "station_zone": STATION_ZONE,
        "keepout_rois": KEEPOUT,
        "blur_threshold": 100.0,
        "darkness_threshold": 40.0,
        "occlusion_threshold": 0.5,
        "keepout": {"min_overlap": 0.1},
    },
}


class FakeBackend:
    """A keep-out backend that needs no model and no network: it sees no one."""

    def detect_people(self, frame):
        return []


def _write_config(path, *, model_path):
    keepout = {"min_overlap": 0.1, "model_path": str(model_path)}
    cfg = {**BASE, "detect": {**BASE["detect"], "keepout": keepout}}
    Path(path).write_text(yaml.safe_dump(cfg))
    return path


def _run_cli(*args, timeout=60):
    return subprocess.run(
        [sys.executable, "-m", "station_watch", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# --- AC: startup with missing / hash-mismatched weights refuses, naming the file ---


def test_startup_missing_weights_exits_nonzero_naming_the_file(tmp_path):
    missing = tmp_path / "models" / "yolox_nano.onnx"
    config = _write_config(tmp_path / "station.yaml", model_path=missing)
    clip, _ = write_synth_station_clip(
        tmp_path / "clip", [{}], rail_positions={}, keepout_rois=KEEPOUT, station_zone=STATION_ZONE
    )

    result = _run_cli(
        "run", "--config", str(config), "--source", str(clip), "--log", str(tmp_path / "l.db")
    )
    assert result.returncode != 0
    assert str(missing) in result.stderr


def test_startup_hash_mismatch_exits_nonzero_naming_the_file(tmp_path):
    bad = tmp_path / "yolox_nano.onnx"
    bad.write_bytes(b"this is not the real model")
    config = _write_config(tmp_path / "station.yaml", model_path=bad)
    clip, _ = write_synth_station_clip(
        tmp_path / "clip", [{}], rail_positions={}, keepout_rois=KEEPOUT, station_zone=STATION_ZONE
    )

    result = _run_cli(
        "run", "--config", str(config), "--source", str(clip), "--log", str(tmp_path / "l.db")
    )
    assert result.returncode != 0
    assert str(bad) in result.stderr
    assert "hash mismatch" in result.stderr


# --- AC: run makes no network connection (socket creation blocked) ------------


def _context_with_fake_backend(tmp_path, clip):
    config = load_station_config(_write_config(tmp_path / "station.yaml", model_path="unused"))
    stall_window_s, source = resolve_stall_window_or_fail(config)[0], FrameSource(str(clip))
    return RunContext(
        config=config,
        source=source,
        log=Log(tmp_path / "log.db"),
        sinks=build_sinks(config.alarm, record_path=None),
        thresholds=BlindThresholds.from_station_config(config),
        keepout_backend=FakeBackend(),
        stall_window_s=stall_window_s,
        stall_window_source="test",
    )


def test_run_opens_no_socket_with_the_fake_backend(tmp_path, monkeypatch):
    clip, _ = write_synth_station_clip(
        tmp_path / "clip",
        [{} for _ in range(6)],
        rail_positions={},
        keepout_rois=KEEPOUT,
        station_zone=STATION_ZONE,
        fps=30.0,
    )

    def no_sockets(*a, **k):
        raise AssertionError("run attempted to open a socket")

    monkeypatch.setattr(socket, "socket", no_sockets)

    context = _context_with_fake_backend(tmp_path, clip)
    runner = Runner(context, run_id=new_run_id(), max_cycles=3, speed=1000.0)
    try:
        runner.run()  # completes without any socket -- the only network call is fetch-model
    finally:
        context.log.close()
        context.source.release()

    with Log(tmp_path / "log.db") as log:
        obs = log.since(EPOCH, ["obs"])
    assert any(o.target == "zone_press" for o in obs), "the keep-out tracker ran on the clip"


# --- AC: no model weight file is tracked by git -------------------------------


def test_no_model_weights_are_tracked_by_git():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    weight_suffixes = (".onnx", ".pt", ".pth", ".tflite", ".task", ".bin")
    weights = [f for f in tracked if f.lower().endswith(weight_suffixes)]
    assert weights == [], f"model weight files must never be committed: {weights}"


# --- AC: the real model runs when present, is visibly skipped when not --------


def test_real_yolox_runs_on_a_generated_frame_when_weights_present():
    path = default_model_path()
    if not path.exists():
        pytest.skip("model weights not present")
    verify_weights(path)  # the present file must be the real, hash-matching model
    from station_watch.detect.yolox import YoloxBackend

    backend = YoloxBackend(path)
    frame = np.full((320, 480, 3), 70, np.uint8)
    boxes = backend.detect_people(frame)
    assert isinstance(boxes, list)
    for box in boxes:
        assert len(box) == 5  # (x0, y0, x1, y1, score)


def test_expected_sha256_is_a_full_hex_digest():
    assert len(MODEL_SHA256) == 64 and all(c in "0123456789abcdef" for c in MODEL_SHA256)
