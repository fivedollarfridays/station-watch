"""The synthetic drill survives wall-clock stalls: it runs on the frame-index clock.

CI run 36859434510 failed ``test_committed_table_command_reproduces_the_drill`` with a
fault that opened its blind reason but never alarmed: on a loaded runner the drill
process stalled, and because the fault schedule, the pacing and every Log stamp ran on
the wall clock, a stall squeezed a fault window (or read as a disconnect of its own)
and the measurement misreported a fault the pipeline handles fine.

This reproduces that deliberately -- the real ``station-watch drill`` CLI, SIGSTOP-ed
for a second at a time throughout its run -- and requires every fault to be measured
exactly as it is on an idle machine: one alarm naming the expected reason, one
recovery, and no stall-made disconnect anywhere.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "measurements" / "synthetic" / "drill-config.yaml"

STALL_SCHEDULE = [
    {"fault": "frozen", "start_s": 0.5, "clear_s": 1.0},
    {"fault": "lens_covered", "start_s": 1.6, "clear_s": 2.1},
    {"fault": "cable_pulled", "start_s": 2.7, "clear_s": 3.7},
]
EXPECTED = {"frozen": "frozen", "lens_covered": "dark", "cable_pulled": "disconnected"}
RUN_S, STALL_S = 0.3, 1.0
DEADLINE_S = 120.0


def _stall_throughout(proc: subprocess.Popen) -> None:
    """Alternate the drill process between running and SIGSTOP-ed until it exits."""
    deadline = time.monotonic() + DEADLINE_S
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(RUN_S)
        if proc.poll() is not None:
            break
        proc.send_signal(signal.SIGSTOP)
        time.sleep(STALL_S)
        proc.send_signal(signal.SIGCONT)


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="needs POSIX job-control signals")
def test_synthetic_drill_measures_every_fault_through_wall_clock_stalls(tmp_path):
    schedule = tmp_path / "schedule.yaml"
    schedule.write_text(yaml.safe_dump({"faults": STALL_SCHEDULE}))
    out = tmp_path / "drill.json"
    proc = subprocess.Popen(
        [sys.executable, "-m", "station_watch", "drill", "--config", str(CONFIG)]
        + ["--schedule", str(schedule), "--log", str(tmp_path / "l.db"), "--out", str(out)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _stall_throughout(proc)
    finally:
        proc.send_signal(signal.SIGCONT)
        _stdout, stderr = proc.communicate(timeout=DEADLINE_S)
    assert proc.returncode == 0, stderr

    faults = json.loads(out.read_text())["metrics"]["faults"]
    assert set(faults) == set(EXPECTED), faults
    for name, reason in EXPECTED.items():
        entry = faults[name]
        assert entry["alarm"] == f"unobservable:{reason}", (name, entry)
        assert entry["blind_reason"] == reason, (name, entry)
        assert entry["exactly_one_alarm"], (name, entry)
        assert entry["exactly_one_recovery"], (name, entry)
