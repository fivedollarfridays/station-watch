"""Board E2E conformance (AC6): the real CLI path, not fixtures.

A real ``station-watch run`` subprocess watches a synthetic clip that goes dark
partway and writes a Log; then a real ``station-watch board --once`` subprocess
renders that Log. The board's text must show the ``dark`` blind reason, the open
``unobservable:dark`` alarm episode, and a frame age -- all read back off the Log
the run actually wrote, through the installed console script.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_video import write_synth_clip  # noqa: E402

CONFIG = {
    "station_id": "station-1",
    "camera_id": "cam-0",
    "takt_s": 30.0,
    "grace_s": 5.0,
    "required_slots": [],
    "keepout_zones": [],
    "liveness_window_s": 2.0,
    "dark_luma_threshold": 15.0,
    "dark_window_s": 0.05,
    "frozen_frames": 10000,
    "recover_good_frames": 3,
    "cycle_interval_s": 0.05,
    "recover_healthy_verdicts": 2,
    "fiducial": {
        "dictionary_id": "DICT_4X4_50",
        "marker_id": 0,
        "expected_center_px": [40, 40],
        "tolerance_px": 10,
        "window_s": 10000.0,
    },
    "alarm": {"sinks": ["record"]},
    "watchdog": {"cycle_window_s": 10.0, "alarm_eval_window_s": 10.0, "sinks": ["record"]},
    "detect": {
        "persistence_frames": 3,
        "emit_interval_s": 5.0,
        "rail_positions": {},
        "station_zone": {"id": "bench", "region": [[-4, -3], [4, -3], [4, 3], [-4, 3]]},
        "keepout_rois": {},
        "blur_threshold": 100.0,
        "darkness_threshold": 40.0,
        "occlusion_threshold": 0.5,
    },
}


def _cli(*args, timeout=90):
    return subprocess.run(
        [sys.executable, "-m", "station_watch", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_run_then_board_once_shows_dark_episode_and_frame_age(tmp_path):
    clip = write_synth_clip(tmp_path / "clip", frames=200, fps=50.0, dark_from=100)
    config = tmp_path / "station.yaml"
    config.write_text(yaml.safe_dump(CONFIG))
    logdb = tmp_path / "log.db"

    # A real run over the clip; it ends on its own when the recording is exhausted.
    run = _cli(
        "run",
        "--config",
        str(config),
        "--source",
        str(clip),
        "--log",
        str(logdb),
        "--alarm-record",
        str(tmp_path / "run-alarm.jsonl"),
        "--speed",
        "5",
        "--max-cycles",
        "400",
    )
    assert run.returncode == 0, run.stderr
    assert logdb.exists(), "the run must have written a Log"

    board = _cli("board", "--config", str(config), "--log", str(logdb), "--once")
    assert board.returncode == 0, board.stderr
    out = board.stdout

    assert "dark" in out, f"the board should show the dark blind reason:\n{out}"
    assert "unobservable:dark" in out, f"the board should show the open episode:\n{out}"
    assert re.search(r"\d+\.\d+s old", out), f"the board should show a frame age:\n{out}"
    assert "OK" not in out.replace("NOT OK", ""), f"a dark station is never OK:\n{out}"
