"""Detect wired into the real `station-watch run` subprocess, end to end.

The proving test (K14) drives the real console entry point over a synthetic rail
clip with one position empty, then reads back out of the Log the subprocess wrote:
Detect ``Observation`` rows whose method is the detector (not ``fixture``), and a
``missing_part`` Judge verdict citing frames from the clip -- the Judge consuming
Detect's rows through the Log, no side channel (the E2E conformance / counterparty
ACs). A second test proves ``--observations`` and a config with Detect targets are
refused: one source of observations per run.
"""

import json
import subprocess
import sys
from pathlib import Path

import yaml

from station_watch.log import Log
from station_watch.records import FaultKind, ObservationKind, VerdictState

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_station import write_synth_station_clip  # noqa: E402

EPOCH = "0001-01-01T00:00:00.000000+00:00"

RAIL = {
    "rail_pos_1": [[0.7, 1.9], [1.7, 1.9], [1.7, 2.5], [0.7, 2.5]],
    "rail_pos_2": [[2.5, 1.9], [3.5, 1.9], [3.5, 2.5], [2.5, 2.5]],
}
KEEPOUT = {
    "zone_press": {"region": [[4.0, 1.9], [5.3, 1.9], [5.3, 3.0], [4.0, 3.0]], "active": True}
}
STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}

CONFIG = {
    "station_id": "station-1",
    "camera_id": "cam-0",
    "takt_s": 30.0,
    "grace_s": 5.0,
    "required_slots": list(RAIL),
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
        "expected_center_px": [58, 58],  # synth marker (xy 30,30, px 56) centre
        "tolerance_px": 10,
        "window_s": 10_000.0,  # never trip fiducial: this clip keeps the marker in view
    },
    "alarm": {"sinks": ["record"]},
    "watchdog": {"cycle_window_s": 10.0, "alarm_eval_window_s": 10.0, "sinks": ["record"]},
    "detect": {
        "persistence_frames": 2,
        "emit_interval_s": 5.0,
        "rail_positions": RAIL,
        "station_zone": STATION_ZONE,
        "keepout_rois": KEEPOUT,
        "blur_threshold": 100.0,
        "darkness_threshold": 40.0,
        "occlusion_threshold": 0.5,
    },
}


def _write_config(path, **over):
    cfg = {**CONFIG, **over}
    Path(path).write_text(yaml.safe_dump(cfg))
    return path


def _run_cli(*args, timeout=90):
    return subprocess.run(
        [sys.executable, "-m", "station_watch", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _rail_clip(tmp_path, frames=12):
    script = [
        {"positions": {"rail_pos_1": "present", "rail_pos_2": "absent"}} for _ in range(frames)
    ]
    path, _truth = write_synth_station_clip(
        tmp_path / "clip",
        script,
        rail_positions=RAIL,
        keepout_rois=KEEPOUT,
        station_zone=STATION_ZONE,
        fps=20.0,
    )
    return path, frames


# --- AC1 / E2E / counterparty: Detect rows + a missing_part verdict citing them -


def test_run_subprocess_rail_clip_writes_detect_rows_and_missing_part_verdict(tmp_path):
    clip, n_frames = _rail_clip(tmp_path)
    config = _write_config(tmp_path / "station.yaml")
    logdb = tmp_path / "log.db"

    result = _run_cli(
        "run",
        "--config",
        str(config),
        "--source",
        str(clip),
        "--log",
        str(logdb),
        "--alarm-record",
        str(tmp_path / "alarm.jsonl"),
    )
    assert result.returncode == 0, result.stderr

    with Log(logdb) as log:
        obs = log.since(EPOCH, ["obs"])
        verdicts = log.since(EPOCH, ["verdict"])

    assert obs, "Detect must append Observation rows to the Log the run wrote"
    assert all(o.method != "fixture" for o in obs), "the rows are the detector's, not a fixture"
    assert any("rail_positions" in o.method for o in obs)
    present = [
        o for o in obs if o.kind is ObservationKind.PART_PRESENT and o.target == "rail_pos_1"
    ]
    absent = [o for o in obs if o.kind is ObservationKind.PART_ABSENT and o.target == "rail_pos_2"]
    assert present, "the filled position reads part_present"
    assert absent, "the empty position reads part_absent"

    missing = [
        fault
        for verdict in verdicts
        if verdict.state is VerdictState.FAULT
        for fault in verdict.faults
        if fault.kind is FaultKind.MISSING_PART and fault.target == "rail_pos_2"
    ]
    assert missing, "the Judge raises missing_part from Detect's rows (read back from the Log)"
    cited = {frame_id for fault in missing for frame_id in fault.frame_ids}
    assert cited, "the fault cites the frames it came from"
    assert all(0 <= frame_id < n_frames for frame_id in cited), "cited frames exist in the clip"


# --- AC3: --observations together with Detect targets is refused ---------------


def test_observations_with_detect_targets_is_refused(tmp_path):
    clip, _n = _rail_clip(tmp_path, frames=5)
    config = _write_config(tmp_path / "station.yaml")
    obs = tmp_path / "obs.jsonl"
    obs.write_text(
        json.dumps(
            {
                "station_id": "station-1",
                "frame_id": 1,
                "kind": "part_absent",
                "target": "rail_pos_1",
                "t_offset_s": 0.1,
                "confidence_ceiling": 0.9,
            }
        )
        + "\n"
    )

    result = _run_cli(
        "run",
        "--config",
        str(config),
        "--source",
        str(clip),
        "--log",
        str(tmp_path / "log.db"),
        "--alarm-record",
        str(tmp_path / "alarm.jsonl"),
        "--observations",
        str(obs),
    )
    assert result.returncode != 0
    assert "observations" in result.stderr.lower()
    assert "detect" in result.stderr.lower()
