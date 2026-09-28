"""The unplugged-camera case through a real ``station-watch run`` process.

``_fake_camera_child.py`` runs the production CLI with only the hardware read
swapped for a fake live camera that fails after a few frames. The process must
write a ``disconnected`` BlindRecord, raise the alarm through its sink, never
judge healthy after the failure, stay up retrying the device, and shut down
cleanly (exit 0) on SIGTERM.
"""

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

from station_watch.log import Log
from station_watch.records import BlindReason, BlindState, VerdictState

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import HEALTH_CONFIG  # noqa: E402

CHILD = Path(__file__).parent / "_fake_camera_child.py"
EPOCH = "0001-01-01T00:00:00.000000+00:00"


def _opened(logdb):
    if not logdb.exists():
        return []
    with Log(logdb) as log:
        return [
            b
            for b in log.since(EPOCH, ["blind"])
            if b.reason == BlindReason.DISCONNECTED and b.state == BlindState.OPENED
        ]


def _alarms(record):
    if not record.exists():
        return []
    rows = [json.loads(line) for line in record.read_text().splitlines() if line.strip()]
    return [r for r in rows if r["event"] == "alarm"]


def test_run_process_with_unplugged_camera_alarms_and_stays_up(tmp_path):
    config = tmp_path / "station.yaml"
    config.write_text(yaml.safe_dump({**HEALTH_CONFIG, "liveness_window_s": 1.0}))
    logdb, record = tmp_path / "log.db", tmp_path / "alarm.jsonl"
    args = ["run", "--config", str(config), "--source", "0", "--log", str(logdb)]
    proc = subprocess.Popen(
        [sys.executable, str(CHILD), "15", "none", "none", *args, "--alarm-record", str(record)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline and not (_opened(logdb) and _alarms(record)):
            assert proc.poll() is None, proc.communicate()[1]
            time.sleep(0.1)
        assert _opened(logdb), "the unplugged camera must write a disconnected record"
        assert [a["cause"] for a in _alarms(record)] == ["unobservable:disconnected"]
        time.sleep(1.5)
        assert proc.poll() is None, "a failing live camera must not end the run"
    finally:
        proc.send_signal(signal.SIGTERM)
        _out, err = proc.communicate(timeout=15)
    assert proc.returncode == 0, err

    opened = _opened(logdb)
    with Log(logdb) as log:
        after = [v for v in log.since(opened[0].ts, ["verdict"]) if v.ts > opened[0].ts]
    assert after and all(v.state != VerdictState.HEALTHY for v in after)
