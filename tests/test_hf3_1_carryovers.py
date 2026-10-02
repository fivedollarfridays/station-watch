"""HF3.1 pipeline carry-overs: the P2s dispositioned from PR #5 and #6.

Each section is a regression test written against a specific carry-over:

1. ArUco is detected **once per frame** -- Capture finds the marker corners and
   hands them to Detect, which must not search again.
2. ``RunContext.stall_window_s`` defaults to ``None`` so the Judge's
   ``takt_s + grace_s`` fallback engages for a context built without it.
4. ``station-watch measure`` turns a ``StartupError`` and the no-fps
   ``ValueError`` into a single ``station-watch:`` line and a non-zero exit.
5. The drill and measure "Reproduce:" command strings round-trip through
   ``shlex.split``.
6. A real ``evaluate`` builds one keep-out backend per clip.

(The ArUco-corners contract on ``Detector.process`` lives in ``test_detector``;
the motion-mask carry-over lives in ``test_detect_motion``.)
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import yaml

from station_watch.capture.blind import BlindThresholds
from station_watch.capture.capture import Capture
from station_watch.capture.source import FrameSource
from station_watch.config import StationConfig
from station_watch.detect.detector import Detector
from station_watch.evaluate import run_evaluation
from station_watch.judge import Judge
from station_watch.log import Log
from station_watch.runner.pipeline import Runner
from station_watch.runner.startup import RunContext

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_station import write_synth_station_clip  # noqa: E402

# Regions match the synth-station renderer's defaults, so write_synth_station_clip
# draws the bench exactly where this config samples it.
RAIL = {
    "rail_pos_1": [[0.7, 1.9], [1.7, 1.9], [1.7, 2.5], [0.7, 2.5]],
    "rail_pos_2": [[2.5, 1.9], [3.5, 1.9], [3.5, 2.5], [2.5, 2.5]],
}
STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}


def _config_dict(*, track_motion: bool = False, angle: bool = False) -> dict:
    """A valid Detect config with rail positions and no keep-out zone."""
    station_zone = dict(STATION_ZONE)
    if track_motion:
        station_zone["track_motion"] = True
    return {
        "station_id": "station-1",
        "camera_id": "cam-0",
        "takt_s": 30.0,
        "grace_s": 5.0,
        "required_slots": ["rail_pos_1", "rail_pos_2"],
        "keepout_zones": [],
        "liveness_window_s": 3.0,
        "dark_luma_threshold": 15.0,
        "dark_window_s": 2.0,
        "frozen_frames": 30,
        "recover_good_frames": 5,
        "cycle_interval_s": 1.0,
        "recover_healthy_verdicts": 3,
        "fiducial": {
            "dictionary_id": "DICT_4X4_50",
            "marker_id": 0,
            "expected_center_px": [58, 58],
            "tolerance_px": 60,
        },
        "alarm": {"sinks": ["screen"]},
        "watchdog": {"cycle_window_s": 10.0, "alarm_eval_window_s": 10.0, "sinks": ["screen"]},
        "detect": {
            "persistence_frames": 2,
            "emit_interval_s": 10_000.0,
            "rail_positions": RAIL,
            "station_zone": station_zone,
            "keepout_rois": {},
            "blur_threshold": 100.0,
            "darkness_threshold": 40.0,
            "occlusion_threshold": 0.5,
        },
    }


# --- (1) ArUco detected once per frame through the real Capture with Detect on ----


def test_capture_finds_marker_once_per_frame(tmp_path, monkeypatch):
    import station_watch.capture.capture as capture_mod
    import station_watch.detect.detector as detector_mod
    from station_watch.detect.geometry import find_marker_corners as real_find

    calls = {"n": 0}

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real_find(*args, **kwargs)

    # Count every marker search on the per-frame path: Capture's own call and any
    # the Detector would make. With corners threaded through, the total is one.
    monkeypatch.setattr(capture_mod, "find_marker_corners", counting)
    monkeypatch.setattr(detector_mod, "find_marker_corners", counting)

    config = StationConfig.from_mapping(_config_dict())
    path, _truth = write_synth_station_clip(tmp_path / "clip", [{}])  # one frame
    log = Log(tmp_path / "log.db")
    capture = Capture(
        FrameSource(str(path)),
        station_id=config.station_id,
        camera_id=config.camera_id,
        run_id="r",
        sleep=lambda _s: None,
        detector=Detector(config, run_id="r"),
    )
    capture.run(log, BlindThresholds.from_station_config(config))

    assert calls["n"] == 1, "the marker must be detected exactly once per frame"


# --- (2) RunContext.stall_window_s defaults to None -> Judge falls back -----------


def test_run_context_without_stall_window_uses_takt_plus_grace():
    config = StationConfig.from_mapping(_config_dict())
    context = RunContext(
        config=config, source=object(), log=object(), sinks=[object()], thresholds=object()
    )
    assert context.stall_window_s is None, "an un-set stall window is None, not a real 0.0 s"

    runner = Runner(context, run_id="r")
    assert runner._judge._stall_window == config.takt_s + config.grace_s


def test_judge_built_from_none_stall_window_falls_back():
    config = StationConfig.from_mapping(_config_dict())
    judge = Judge(config, run_id="r", stall_window_s=None)
    assert judge._stall_window == config.takt_s + config.grace_s


# --- (4) measure turns startup / no-fps errors into one station-watch: line -------


def _write_angle_clip(tmp_path, *, fps_tag: bool) -> tuple[Path, Path, Path]:
    """A one-angle manifest + clip + config; ``fps_tag`` adds a native-rate tag."""
    clips_dir = tmp_path / "clips"
    rel_dir = clips_dir / "s1" / "c"
    path, _truth = write_synth_station_clip(rel_dir, [{} for _ in range(3)], fps=20.0)
    rel = str(Path(path).relative_to(clips_dir))
    tags = {"angle_deg": 30}
    if fps_tag:
        tags["fps"] = 20
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"dataset": "d", "sessions": [{"id": "s1", "clips": [{"path": rel, "tags": tags}]}]}
        )
    )
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(_config_dict()))
    return manifest, clips_dir, config


def _assert_clean_cli_error(result: subprocess.CompletedProcess, *, needle: str) -> None:
    assert result.returncode != 0, result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    lines = [ln for ln in result.stderr.splitlines() if ln.strip()]
    station_lines = [ln for ln in lines if ln.startswith("station-watch:")]
    assert len(station_lines) == 1, result.stderr
    assert needle in station_lines[0], station_lines[0]


def test_measure_startup_error_exits_clean(tmp_path):
    manifest, clips_dir, _config = _write_angle_clip(tmp_path, fps_tag=True)
    missing = tmp_path / "nope.yaml"
    result = subprocess.run(
        [
            sys.executable, "-m", "station_watch", "measure", "occlusion",
            "--clips", str(manifest), "--clips-dir", str(clips_dir),
            "--config", str(missing), "--out", str(tmp_path / "out.json"),
        ],
        capture_output=True, text=True,
    )
    _assert_clean_cli_error(result, needle="nope.yaml")


def test_measure_no_fps_clip_exits_clean(tmp_path):
    # No OpenCV build here produces a file that reports fps <= 0, so a tiny driver
    # subprocess forces that exact condition and drives the real CLI: the point is
    # that the no-fps ValueError becomes one station-watch: line, not a traceback.
    manifest, clips_dir, config = _write_angle_clip(tmp_path, fps_tag=False)
    out = tmp_path / "out.json"
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import sys\n"
        "from unittest.mock import patch\n"
        "from station_watch.capture.source import FrameSource\n"
        "from station_watch.cli import main\n"
        "with patch.object(FrameSource, 'fps', property(lambda self: 0.0)):\n"
        f"    sys.exit(main(['measure','occlusion','--clips',{str(manifest)!r},"
        f"'--clips-dir',{str(clips_dir)!r},'--config',{str(config)!r},'--out',{str(out)!r}]))\n"
    )
    result = subprocess.run([sys.executable, str(driver)], capture_output=True, text=True)
    _assert_clean_cli_error(result, needle="s1/c")


# --- (5) reproduce commands round-trip through shlex.split ------------------------


def test_drill_reproduce_command_roundtrips_through_shlex():
    from station_watch.drill.commandline import _command

    argv = ["--config", "a b/c.yaml", "--log", "l$x.db", "--out", "o u t.json",
            "--schedule", "sch ed.yaml"]
    args = Namespace(
        config="a b/c.yaml", log="l$x.db", out="o u t.json",
        live=False, schedule="sch ed.yaml", source=None,
    )
    assert shlex.split(_command(args)) == ["station-watch", "drill", *argv]


def test_measure_reproduce_command_roundtrips_through_shlex():
    from station_watch.physics.commandline import _command

    args = Namespace(name="occlusion", synthetic=False, clips="c l$i/p.yaml")
    assert shlex.split(_command(args)) == [
        "station-watch", "measure", "occlusion", "--clips", "c l$i/p.yaml"
    ]


# --- (6) a real evaluate builds one keep-out backend per clip ---------------------


def test_real_evaluate_builds_one_backend_per_clip(tmp_path, monkeypatch):
    import station_watch.runner.startup as startup_mod

    created: list[object] = []

    def counting_factory(_config):
        backend = object()
        created.append(backend)
        return backend

    monkeypatch.setattr(startup_mod, "build_keepout_backend", counting_factory)

    clips_dir = tmp_path / "clips"
    clips = []
    for i in range(2):
        path, _truth = write_synth_station_clip(
            clips_dir / "s1" / f"clip{i}",
            [{"positions": {"rail_pos_1": "present", "rail_pos_2": "present"}} for _ in range(3)],
            fps=20.0,
        )
        clips.append({"path": str(Path(path).relative_to(clips_dir))})
    manifest = tmp_path / "m.yaml"
    manifest.write_text(yaml.safe_dump({"dataset": "d", "sessions": [{"id": "s1", "clips": clips}]}))
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(_config_dict()))

    rc = run_evaluation(
        config_path=str(config),
        manifest_path=str(manifest),
        out_dir=str(tmp_path / "out"),
        synthetic=False,
        clips_dir=str(clips_dir),
    )

    assert rc == 0
    assert len(created) == 2, "one keep-out backend is built per clip"
    assert len({id(b) for b in created}) == 2, "the per-clip backends are distinct instances"
