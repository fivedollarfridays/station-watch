"""`audit rate` E2E: run -> build -> mark -> rate, false count matches the marks.

Real subprocesses: a ``station-watch run`` over a synthetic clip with a missing part
and a dark stretch, ``audit build`` of its Log, a headless ``audit mark`` of the
missing-part flag ``incorrect``, and ``audit rate --min-hours`` (set below the clip's
observable length) -- and the rate file's overall ``false`` count matches the mark,
with the dark blind episode reported in its own block, not the detection rates.
"""

from __future__ import annotations

import json
import subprocess
import sys
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
    "station_id": "station-1", "camera_id": "cam-0", "takt_s": 30.0, "grace_s": 5.0,
    "required_slots": list(RAIL), "keepout_zones": [], "liveness_window_s": 2.0,
    "dark_luma_threshold": 40.0, "dark_window_s": 0.2, "frozen_frames": 10_000,
    "recover_good_frames": 3, "cycle_interval_s": 0.05, "recover_healthy_verdicts": 2,
    "fiducial": {"dictionary_id": "DICT_4X4_50", "marker_id": 0,
                 "expected_center_px": [58, 58], "tolerance_px": 10, "window_s": 10_000.0},
    "alarm": {"sinks": ["record"]},
    "watchdog": {"cycle_window_s": 1.0, "alarm_eval_window_s": 1.0, "sinks": ["record"]},
    "qa": {"max_unknown_fraction": 0.9},
    "detect": {"persistence_frames": 2, "emit_interval_s": 5.0, "rail_positions": RAIL,
               "station_zone": STATION_ZONE, "keepout_rois": {}, "blur_threshold": 100.0,
               "darkness_threshold": 40.0, "occlusion_threshold": 0.5, "unknown_grace_s": 10.0},
}


def _run_cli(*args, cwd=None, timeout=180):
    return subprocess.run(
        [sys.executable, "-m", "station_watch", *args],
        cwd=cwd, capture_output=True, text=True, timeout=timeout,
    )


def _clip(dir_path):
    bright = [{"positions": {"rail_pos_1": "absent", "rail_pos_2": "present"}} for _ in range(40)]
    dark = [{"positions": {"rail_pos_1": "present", "rail_pos_2": "present"}, "dim": True}
            for _ in range(30)]
    clip, _truth = write_synth_station_clip(dir_path, bright + dark, rail_positions=RAIL, fps=20.0)
    return clip


def test_run_build_mark_then_rate_false_count_matches(tmp_path):
    cfg = tmp_path / "station.yaml"
    cfg.write_text(yaml.safe_dump(BASE))
    clip = _clip(tmp_path / "clip")
    logdb = tmp_path / "log.db"
    evidence = tmp_path / "evidence"
    audit = tmp_path / "audit"

    run = _run_cli("run", "--config", str(cfg), "--source", str(clip), "--log", str(logdb),
                   "--alarm-record", str(tmp_path / "alarm.jsonl"), "--evidence-dir", str(evidence))
    assert run.returncode == 0, run.stderr

    build = _run_cli("audit", "build", "--config", str(cfg), "--log", str(logdb),
                     "--evidence-dir", str(evidence), "--out", str(audit))
    assert build.returncode == 0, build.stderr

    flags = json.loads((audit / "flags.json").read_text())
    missing = next(f for f in flags if f["kind"] == "missing_part")

    mark = _run_cli("audit", "mark", "--audit", str(audit), missing["flag_id"], "incorrect")
    assert mark.returncode == 0, mark.stderr

    (tmp_path / "measurements" / "synthetic").mkdir(parents=True)
    rate = _run_cli("audit", "rate", "--audit", str(audit), "--config", str(cfg),
                    "--log", str(logdb), "--dataset-kind", "synthetic",
                    "--out", "measurements/synthetic/rate.json", "--min-hours", "0.0",
                    cwd=tmp_path)
    assert rate.returncode == 0, rate.stderr

    metrics = json.loads((tmp_path / "measurements/synthetic/rate.json").read_text())["metrics"]
    # The one incorrect mark is the session's only false detection flag.
    assert metrics["overall"]["false"] == 1
    assert metrics["per_kind"]["missing_part"]["false"] == 1
    # The dark stretch is a blind episode -- its own block, not a detection false alarm.
    assert "unobservable:dark" not in metrics["per_kind"]
    assert metrics["blind"]["episodes"] >= 1
