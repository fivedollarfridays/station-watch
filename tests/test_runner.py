"""Runner tests: `station-watch run` wires Capture -> Log -> Judge -> Alarm.

The AC1 proving test (K14) drives the *real* console entry point in a
subprocess over a synthetic clip that goes dark partway, then reads everything
back out of the Log the subprocess wrote: one ``dark`` BlindRecord, one alarm
episode opened and recovered once, and ``CycleCompleted`` rows throughout. AC2
proves ``--stop-stage alarm`` keeps Capture and the cycle loop running while the
Alarm stops evaluating. AC3 proves each startup precondition fails loud (K9),
exiting non-zero and naming the missing piece.
"""

import json
import subprocess
import sys
from pathlib import Path

import yaml

from station_watch.log import Log
from station_watch.records import BlindReason, BlindState

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_video import write_synth_clip  # noqa: E402

EPOCH = "0001-01-01T00:00:00.000000+00:00"

BASE_CONFIG = {
    "station_id": "station-1",
    "camera_id": "cam-0",
    "takt_s": 30.0,
    "grace_s": 5.0,
    "required_slots": [],
    "keepout_zones": [],
    "liveness_window_s": 2.0,
    "dark_luma_threshold": 15.0,
    "dark_window_s": 0.3,
    "frozen_frames": 10000,  # never trip frozen on noise / short dark spans
    "recover_good_frames": 3,
    "cycle_interval_s": 0.1,
    "recover_healthy_verdicts": 2,
    "fiducial": {
        "dictionary_id": "DICT_4X4_50",
        "marker_id": 0,
        "expected_center_px": [40, 40],
        "tolerance_px": 10,
        "window_s": 10000.0,  # never trip fiducial: this task only exercises dark
    },
    "alarm": {"sinks": ["record"]},
    "watchdog": {"cycle_window_s": 10.0, "alarm_eval_window_s": 10.0, "sinks": ["record"]},
    "detect": {
        "persistence_frames": 3,
        "emit_interval_s": 5.0,
        "rail_positions": {},
        "station_zone": {"id": "bench", "region": [[-4, -3], [4, -3], [4, 3], [-4, 3]]},
        # A zone_press ROI is pre-declared so the keepout_zones override below
        # (test_observations_fixture_reaches_the_judge) passes K9 coverage.
        "keepout_rois": {
            "zone_press": {"region": [[2, -3], [5, -3], [5, -1], [2, -1]], "active": True}
        },
        "blur_threshold": 100.0,
        "darkness_threshold": 40.0,
        "occlusion_threshold": 0.5,
    },
}


def _write_config(path, **over):
    cfg = {**BASE_CONFIG, **over}
    Path(path).write_text(yaml.safe_dump(cfg))
    return path


def _run_cli(*args, timeout=90):
    return subprocess.run(
        [sys.executable, "-m", "station_watch", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _transitions(seq, cause):
    """(opens, closes) of ``cause`` across a list of open_episodes tuples."""
    opens = closes = 0
    prev = False
    for episodes in seq:
        now = cause in episodes
        if now and not prev:
            opens += 1
        if prev and not now:
            closes += 1
        prev = now
    return opens, closes


# --- AC1: proving test, real `station-watch run` subprocess on a dark clip ----


def test_run_subprocess_dark_clip_alarms_once_and_recovers_once(tmp_path):
    clip = write_synth_clip(tmp_path / "clip", frames=41, fps=20.0, dark_from=8, dark_until=20)
    config = _write_config(tmp_path / "station.yaml")
    logdb = tmp_path / "log.db"
    record = tmp_path / "alarm.jsonl"

    result = _run_cli(
        "run",
        "--config",
        str(config),
        "--source",
        str(clip),
        "--log",
        str(logdb),
        "--alarm-record",
        str(record),
    )
    assert result.returncode == 0, result.stderr

    with Log(logdb) as log:
        blind = log.since(EPOCH, ["blind"])
        cycles = log.since(EPOCH, ["cycle"])
        evals = log.since(EPOCH, ["alarm_eval"])

    dark_opened = [
        b for b in blind if b.reason == BlindReason.DARK and b.state == BlindState.OPENED
    ]
    assert len(dark_opened) == 1, "exactly one dark BlindRecord should have opened"

    assert len(cycles) >= 3, "CycleCompleted rows should be written throughout the run"
    assert [c.cycle for c in cycles] == list(range(1, len(cycles) + 1))

    seq = [e.open_episodes for e in evals]
    opens, closes = _transitions(seq, "unobservable:dark")
    assert opens == 1, "the dark condition should open exactly one alarm episode"
    assert closes == 1, "the dark episode should recover exactly once"
    assert seq[-1] == (), "the run should end with no open episodes (recovered)"

    events = [json.loads(line) for line in record.read_text().splitlines() if line.strip()]
    assert [e["event"] for e in events if e["event"] == "alarm"] == ["alarm"]
    assert [e["event"] for e in events if e["event"] == "recovery"] == ["recovery"]
    assert events[0]["cause"] == "unobservable:dark"


# --- AC2: --stop-stage alarm keeps cycles + capture, stops alarm evaluation ---


def test_stop_stage_alarm_keeps_cycles_and_capture_but_stops_alarm(tmp_path):
    clip = write_synth_clip(tmp_path / "clip", frames=30, fps=20.0)
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
        "--stop-stage",
        "alarm",
    )
    assert result.returncode == 0, result.stderr

    with Log(logdb) as log:
        cycles = log.since(EPOCH, ["cycle"])
        evals = log.since(EPOCH, ["alarm_eval"])
        frames = log.since(EPOCH, ["frame"])

    assert len(cycles) >= 3, "the cycle loop must keep running under --stop-stage alarm"
    assert len(frames) > 0, "Capture must keep running under --stop-stage alarm"
    assert evals == [], "Alarm must stop evaluating: no alarm_evaluated rows"
    assert all("alarm" not in c.stages for c in cycles)
    assert all("capture" in c.stages and "judge" in c.stages for c in cycles)


# --- AC3: each startup precondition fails loud (K9), naming the missing piece -


def test_startup_missing_config_key_exits_nonzero_and_names_it(tmp_path):
    cfg = {k: v for k, v in BASE_CONFIG.items() if k != "takt_s"}
    config = tmp_path / "station.yaml"
    config.write_text(yaml.safe_dump(cfg))
    clip = write_synth_clip(tmp_path / "clip", frames=5, fps=20.0)

    result = _run_cli(
        "run", "--config", str(config), "--source", str(clip), "--log", str(tmp_path / "log.db")
    )
    assert result.returncode != 0
    assert "takt_s" in result.stderr


def test_startup_unopenable_source_exits_nonzero_and_names_it(tmp_path):
    config = _write_config(tmp_path / "station.yaml")
    missing = tmp_path / "does-not-exist.mkv"

    result = _run_cli(
        "run",
        "--config",
        str(config),
        "--source",
        str(missing),
        "--log",
        str(tmp_path / "log.db"),
        "--alarm-record",
        str(tmp_path / "alarm.jsonl"),
    )
    assert result.returncode != 0
    assert str(missing) in result.stderr or "source" in result.stderr.lower()


def test_startup_no_alarm_sink_exits_nonzero_and_names_it(tmp_path):
    config = _write_config(tmp_path / "station.yaml", alarm={"sinks": []})
    clip = write_synth_clip(tmp_path / "clip", frames=5, fps=20.0)

    result = _run_cli(
        "run", "--config", str(config), "--source", str(clip), "--log", str(tmp_path / "log.db")
    )
    assert result.returncode != 0
    assert "sink" in result.stderr.lower()


def test_startup_unwritable_log_exits_nonzero_and_names_it(tmp_path):
    config = _write_config(tmp_path / "station.yaml")
    clip = write_synth_clip(tmp_path / "clip", frames=5, fps=20.0)
    logdb = tmp_path / "missing-dir" / "log.db"

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
    assert result.returncode != 0
    assert "missing-dir" in result.stderr or "log" in result.stderr.lower()


# --- --observations: fixture rows reach the Judge like any other input --------


def test_observations_fixture_reaches_the_judge(tmp_path):
    # A station with no Detect targets may still take fixture observations as a
    # drill input (HF2.6 refuses --observations only alongside a Detect config --
    # see test_runner_detect). A no_motion row with a zero stall window reaches the
    # Judge like any other input and opens a stall episode.
    clip = write_synth_clip(tmp_path / "clip", frames=40, fps=20.0)
    config = _write_config(tmp_path / "station.yaml", takt_s=0.0, grace_s=0.0)
    obs = tmp_path / "obs.jsonl"
    obs.write_text(
        json.dumps(
            {
                "station_id": "station-1",
                "frame_id": 5,
                "kind": "no_motion",
                "target": "zone_press",
                "t_offset_s": 0.3,
                "confidence_ceiling": 0.9,
            }
        )
        + "\n"
    )
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
        "--observations",
        str(obs),
    )
    assert result.returncode == 0, result.stderr

    with Log(logdb) as log:
        evals = log.since(EPOCH, ["alarm_eval"])
    assert any("stalled:zone_press" in e.open_episodes for e in evals)


def test_observations_help_labels_it_fixture_input():
    result = _run_cli("run", "--help", timeout=30)
    assert result.returncode == 0
    assert "fixture" in result.stdout.lower()
    assert "test" in result.stdout.lower() and "drill" in result.stdout.lower()
