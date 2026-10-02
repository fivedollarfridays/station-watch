"""`audit build`, end to end: a real run then a real build on one synthetic clip.

Drives the real ``station-watch run`` over a synthetic clip that has a missing
part (one contiguous bright stretch with rail_pos_1 removed) and then a dark
stretch (dimmed to the end), then the real ``station-watch audit build`` over its
Log and evidence, and checks the proof sheet the operator would open.
"""

from __future__ import annotations

import json
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_station import write_synth_station_clip  # noqa: E402

RAIL = {
    "rail_pos_1": [[0.7, 1.9], [1.7, 1.9], [1.7, 2.5], [0.7, 2.5]],
    "rail_pos_2": [[2.5, 1.9], [3.5, 1.9], [3.5, 2.5], [2.5, 2.5]],
}
STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}

BASE = {
    "station_id": "station-1",
    "camera_id": "cam-0",
    "takt_s": 30.0,
    "grace_s": 5.0,
    "required_slots": list(RAIL),
    "keepout_zones": [],
    "liveness_window_s": 2.0,
    "dark_luma_threshold": 40.0,
    "dark_window_s": 0.2,
    "frozen_frames": 10_000,
    "recover_good_frames": 3,
    "cycle_interval_s": 0.05,
    "recover_healthy_verdicts": 2,
    "fiducial": {
        "dictionary_id": "DICT_4X4_50",
        "marker_id": 0,
        "expected_center_px": [58, 58],
        "tolerance_px": 10,
        "window_s": 10_000.0,
    },
    "alarm": {"sinks": ["record"]},
    "watchdog": {"cycle_window_s": 1.0, "alarm_eval_window_s": 1.0, "sinks": ["record"]},
    "detect": {
        "persistence_frames": 2,
        "emit_interval_s": 5.0,
        "rail_positions": RAIL,
        "station_zone": STATION_ZONE,
        "keepout_rois": {},
        "blur_threshold": 100.0,
        "darkness_threshold": 40.0,
        "occlusion_threshold": 0.5,
        "unknown_grace_s": 10.0,
    },
}


def _run_cli(*args, timeout=180):
    return subprocess.run(
        [sys.executable, "-m", "station_watch", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _clip(dir_path):
    # 40 bright frames with rail_pos_1 missing (one missing_part episode), then 30
    # dimmed frames to the end (one dark blind that never clears).
    bright = [{"positions": {"rail_pos_1": "absent", "rail_pos_2": "present"}} for _ in range(40)]
    dark = [
        {"positions": {"rail_pos_1": "present", "rail_pos_2": "present"}, "dim": True}
        for _ in range(30)
    ]
    clip, _truth = write_synth_station_clip(dir_path, bright + dark, rail_positions=RAIL, fps=20.0)
    return clip


class _Imgs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.imgs = 0

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.imgs += 1
            assert any(n == "src" and v.startswith("data:image/jpeg;base64,") for n, v in attrs)


def test_run_then_audit_build_flags_a_missing_part_and_a_dark_blind(tmp_path):
    cfg = tmp_path / "station.yaml"
    cfg.write_text(yaml.safe_dump(BASE))
    clip = _clip(tmp_path / "clip")
    logdb = tmp_path / "log.db"
    evidence = tmp_path / "evidence"
    out = tmp_path / "audit"

    run = _run_cli(
        "run", "--config", str(cfg), "--source", str(clip), "--log", str(logdb),
        "--alarm-record", str(tmp_path / "alarm.jsonl"), "--evidence-dir", str(evidence),
    )
    assert run.returncode == 0, run.stderr

    build = _run_cli(
        "audit", "build", "--config", str(cfg), "--log", str(logdb),
        "--evidence-dir", str(evidence), "--out", str(out),
    )
    assert build.returncode == 0, build.stderr

    flags = json.loads((out / "flags.json").read_text())
    kinds = sorted(f["kind"] for f in flags)
    assert kinds.count("missing_part") == 1, flags
    assert kinds.count("unobservable:dark") == 1, flags

    cited = sorted({fid for f in flags for fid in f["frame_ids"]})
    assert cited, "both flags must cite frames"

    sheet = (out / "index.html").read_text()
    assert "no evidence:" not in sheet, "every cited frame should resolve to a stored thumbnail"
    for frame_id in cited:
        assert f"frame {frame_id} " in sheet, f"cited frame {frame_id} is not shown on the sheet"
    imgs = _Imgs()
    imgs.feed(sheet)
    assert imgs.imgs >= len(cited), "each cited frame must be shown as an embedded image"
