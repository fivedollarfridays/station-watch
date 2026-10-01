"""Watchdog proving drill: two real subprocesses, the D1 case end to end (K14).

These tests drive the *real* ``station-watch run`` and ``station-watch watchdog``
console entry points as separate OS processes over a synthetic clip, then read
the watchdog's own alarm-record sink back off disk. They prove the finding-D1
case directly: with ``run --stop-stage alarm`` the alarm rail goes silent while
Capture and the cycle loop keep going, and the watchdog -- on its own clock and
its own alarm rail -- fires for the alarm stage. Killing the run process fires
the watchdog for the cycle stage, and a healthy run raises nothing.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

from station_watch.log import Log
from station_watch.watchdog import age_seconds

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
    "frozen_frames": 10000,
    "recover_good_frames": 3,
    "cycle_interval_s": 0.1,
    "recover_healthy_verdicts": 2,
    "fiducial": {
        "dictionary_id": "DICT_4X4_50",
        "marker_id": 0,
        "expected_center_px": [40, 40],
        "tolerance_px": 10,
        "window_s": 10000.0,
    },
    "alarm": {"sinks": ["record"]},
    # Small windows so the drill is fast; the watchdog polls faster still.
    "watchdog": {"cycle_window_s": 1.0, "alarm_eval_window_s": 1.0, "sinks": ["record"]},
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


def _write_config(path, **over):
    Path(path).write_text(yaml.safe_dump({**BASE_CONFIG, **over}))
    return path


def _run_popen(*args):
    return subprocess.Popen(
        [sys.executable, "-m", "station_watch", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _watchdog_cli(*args, timeout=60):
    return subprocess.run(
        [sys.executable, "-m", "station_watch", "watchdog", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _alarm_events(record_path):
    lines = Path(record_path).read_text().splitlines() if Path(record_path).exists() else []
    events = [json.loads(line) for line in lines if line.strip()]
    return [e for e in events if e["event"] == "alarm"]


def _cycle_count(logdb):
    with Log(logdb) as log:
        return len(log.since(EPOCH, ["cycle"]))


def _wait_until_cycles(logdb, at_least, deadline_s=10.0):
    start = time.monotonic()
    while time.monotonic() - start < deadline_s:
        if logdb.exists() and _cycle_count(logdb) >= at_least:
            return True
        time.sleep(0.05)
    return False


# --- AC1: run --stop-stage alarm; watchdog fires for the alarm stage ----------


def _assert_alarm_stage_fired_off_aged_rows(wd_record, logdb, frames_before, window_s, tick_s):
    alarms = _alarm_events(wd_record)
    causes = {a["cause"] for a in alarms}
    assert "watchdog:alarm" in causes, f"watchdog should fire the alarm stage; got {causes}"
    assert "watchdog:cycle" not in causes, "the cycle rail stayed live"
    alarm_stage = next(a for a in alarms if a["cause"] == "watchdog:alarm")
    assert "alarm stage silent" in alarm_stage["label"]
    assert "old" in alarm_stage["label"], alarm_stage["label"]  # aged row, not "no rows"

    with Log(logdb) as log:
        evals = log.since(EPOCH, ["alarm_eval"])
        cycles = log.since(EPOCH, ["cycle"])
        assert len(log.since(EPOCH, ["frame"])) >= frames_before, "Capture kept running"
    assert len(evals) == 5, "the Alarm ran for exactly the first 5 cycles"
    assert all("alarm" not in c.stages for c in cycles[5:])
    lag = age_seconds(evals[-1].ts, alarm_stage["started_ts"])
    assert window_s < lag <= window_s + tick_s + 0.5, f"fired {lag:.2f}s after rows stopped"


def test_watchdog_fires_for_the_alarm_stage_while_capture_keeps_running(tmp_path):
    # The Alarm evaluates for 5 cycles, then stops while Capture and the cycle
    # loop keep going: the D1 failure. The watchdog must name the alarm stage off
    # the *aging* last row -- within its window plus one tick -- not merely
    # because no row ever existed.
    clip = write_synth_clip(tmp_path / "clip", frames=600, fps=50.0)
    config = _write_config(tmp_path / "station.yaml")
    logdb = tmp_path / "log.db"
    wd_record = tmp_path / "wd.jsonl"

    run = _run_popen(
        "run",
        "--config",
        str(config),
        "--source",
        str(clip),
        "--log",
        str(logdb),
        "--alarm-record",
        str(tmp_path / "run-alarm.jsonl"),
        "--stop-stage",
        "alarm",
        "--stop-stage-after",
        "5",
        "--max-cycles",
        "80",
    )
    try:
        assert _wait_until_cycles(logdb, 3), "the run must be writing cycle rows"
        with Log(logdb) as log:
            frames_before = len(log.since(EPOCH, ["frame"]))

        watchdog = _watchdog_cli(
            "--config",
            str(config),
            "--log",
            str(logdb),
            "--alarm-record",
            str(wd_record),
            "--interval",
            "0.2",
            "--max-checks",
            "12",
        )
        assert watchdog.returncode == 0, watchdog.stderr

        _assert_alarm_stage_fired_off_aged_rows(wd_record, logdb, frames_before, 1.0, 0.2)
        assert run.poll() is None, "the run process must still be alive"
    finally:
        run.terminate()
        run.wait(timeout=10)


# --- AC2a: killing the run fires the watchdog for the cycle stage -------------


def test_killing_the_run_fires_the_watchdog_for_the_cycle_stage(tmp_path):
    clip = write_synth_clip(tmp_path / "clip", frames=300, fps=50.0)
    config = _write_config(tmp_path / "station.yaml")
    logdb = tmp_path / "log.db"
    wd_record = tmp_path / "wd.jsonl"

    run = _run_popen(
        "run",
        "--config",
        str(config),
        "--source",
        str(clip),
        "--log",
        str(logdb),
        "--alarm-record",
        str(tmp_path / "run-alarm.jsonl"),
        "--max-cycles",
        "200",
    )
    assert _wait_until_cycles(logdb, 3), "the run must be writing cycle rows before we kill it"
    run.kill()
    run.wait(timeout=10)

    # Let the newest cycle row age past the 1.0s window, then watch.
    time.sleep(1.5)
    watchdog = _watchdog_cli(
        "--config",
        str(config),
        "--log",
        str(logdb),
        "--alarm-record",
        str(wd_record),
        "--interval",
        "0.2",
        "--max-checks",
        "2",
    )
    assert watchdog.returncode == 0, watchdog.stderr
    causes = {a["cause"] for a in _alarm_events(wd_record)}
    assert "watchdog:cycle" in causes, f"a dead run should fire the cycle stage; got {causes}"


# --- AC2b: a healthy run longer than two windows raises no watchdog alarm -----


def test_healthy_run_raises_no_watchdog_alarm(tmp_path):
    clip = write_synth_clip(tmp_path / "clip", frames=400, fps=50.0)
    config = _write_config(tmp_path / "station.yaml")
    logdb = tmp_path / "log.db"
    wd_record = tmp_path / "wd.jsonl"

    run = _run_popen(
        "run",
        "--config",
        str(config),
        "--source",
        str(clip),
        "--log",
        str(logdb),
        "--alarm-record",
        str(tmp_path / "run-alarm.jsonl"),
        "--max-cycles",
        "200",
    )
    try:
        # Both rails must be established and fresh before the watchdog starts.
        assert _wait_until_cycles(logdb, 5), "the run must be writing rows"
        with Log(logdb) as log:
            assert len(log.since(EPOCH, ["alarm_eval"])) > 0, "healthy run writes alarm_eval rows"

        # Watch across more than two full windows (window 1.0s, ~2.4s of checks).
        watchdog = _watchdog_cli(
            "--config",
            str(config),
            "--log",
            str(logdb),
            "--alarm-record",
            str(wd_record),
            "--interval",
            "0.2",
            "--max-checks",
            "12",
        )
        assert watchdog.returncode == 0, watchdog.stderr
        assert _alarm_events(wd_record) == [], "a healthy run must raise no watchdog alarm"
    finally:
        run.terminate()
        run.wait(timeout=10)


# --- AC3: a missing Log alarms rather than exiting quietly (subprocess) --------


def test_watchdog_subprocess_missing_log_alarms_not_exits_quietly(tmp_path):
    config = _write_config(tmp_path / "station.yaml")
    wd_record = tmp_path / "wd.jsonl"

    watchdog = _watchdog_cli(
        "--config",
        str(config),
        "--log",
        str(tmp_path / "never-created.db"),
        "--alarm-record",
        str(wd_record),
        "--interval",
        "0.1",
        "--max-checks",
        "1",
    )
    assert watchdog.returncode == 0, watchdog.stderr
    causes = {a["cause"] for a in _alarm_events(wd_record)}
    assert "watchdog:log" in causes
