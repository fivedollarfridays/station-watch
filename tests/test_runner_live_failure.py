"""An unplugged live camera must alarm, never read healthy, and never end the run.

Each test drives the *real* Runner (real Capture thread, Log, Judge and Alarm, the
record sink standing in for the buzzer) over a fake live device that delivers
frames and then fails -- ``read()`` returning None the way ``cv2`` does for a
pulled USB camera, or raising the way a dead driver does. A live device never
"finishes": the failure must land as a ``disconnected`` BlindRecord, open one
alarm episode, keep every later verdict off healthy, and leave the Runner running
(retrying the device) until it is told to stop.
"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from station_watch.alarm.sink import RecordSink
from station_watch.capture.blind import BlindThresholds
from station_watch.log import Log
from station_watch.records import BlindReason, BlindState, VerdictState
from station_watch.runner.pipeline import Runner
from station_watch.runner.startup import RunContext

sys.path.insert(0, str(Path(__file__).parent))
from helpers.live_sources import FailingLiveSource  # noqa: E402
from helpers.records import health_config  # noqa: E402

EPOCH = "0001-01-01T00:00:00.000000+00:00"
RUN = "run-live-failure"


def _start(tmp_path, source, **config_over):
    config = health_config(liveness_window_s=1.0, **config_over)
    log = Log(tmp_path / "log.db")
    record = tmp_path / "alarm.jsonl"
    context = RunContext(
        config=config,
        source=source,
        log=log,
        sinks=[RecordSink(record)],
        thresholds=BlindThresholds.from_station_config(config),
    )
    runner = Runner(context, run_id=RUN)
    thread = threading.Thread(target=runner.run, daemon=True)
    thread.start()
    return runner, thread, log, record


def _events(record):
    if not record.exists():
        return []
    return [json.loads(line) for line in record.read_text().splitlines() if line.strip()]


def _wait_for(predicate, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _disconnected(log, state):
    return [
        b
        for b in log.since(EPOCH, ["blind"])
        if b.reason == BlindReason.DISCONNECTED and b.state == state
    ]


def _assert_unplugged_alarms_and_keeps_running(tmp_path, mode):
    source = FailingLiveSource(frames_before=15, mode=mode)
    runner, thread, log, record = _start(tmp_path, source)
    try:
        assert _wait_for(lambda: _disconnected(log, BlindState.OPENED), 5.0)
        assert _wait_for(lambda: any(e["event"] == "alarm" for e in _events(record)), 5.0)
        time.sleep(1.5)  # well past the liveness window: the run must still be up
        assert thread.is_alive(), "a failing live camera must not end the run"
        assert source.failed_reads > 1, "Capture must keep retrying the device"
    finally:
        runner.stop()
        thread.join(timeout=5.0)
    assert not thread.is_alive()

    opened = _disconnected(log, BlindState.OPENED)
    assert len(opened) == 1
    assert _disconnected(log, BlindState.CLEARED) == []
    alarms = [e for e in _events(record) if e["event"] == "alarm"]
    assert [a["cause"] for a in alarms] == ["unobservable:disconnected"]
    verdicts = [v for v in log.since(opened[0].ts, ["verdict"]) if v.ts > opened[0].ts]
    assert verdicts, "the cycle loop must keep judging after the failure"
    assert all(v.state != VerdictState.HEALTHY for v in verdicts)


def test_unplugged_camera_read_returns_none_alarms_and_runner_keeps_running(tmp_path):
    _assert_unplugged_alarms_and_keeps_running(tmp_path, "none")


def test_unplugged_camera_read_raises_alarms_and_runner_keeps_running(tmp_path):
    _assert_unplugged_alarms_and_keeps_running(tmp_path, "raise")


@pytest.mark.parametrize("mode", ["none", "raise"])
def test_replugged_camera_clears_and_recovers_exactly_once(tmp_path, mode):
    source = FailingLiveSource(frames_before=15, mode=mode, recover_after_s=1.5)
    runner, thread, log, record = _start(tmp_path, source)
    try:
        assert _wait_for(lambda: _disconnected(log, BlindState.CLEARED), 8.0)
        assert _wait_for(lambda: any(e["event"] == "recovery" for e in _events(record)), 5.0)
        time.sleep(0.5)
        assert thread.is_alive()
    finally:
        runner.stop()
        thread.join(timeout=5.0)

    assert len(_disconnected(log, BlindState.OPENED)) == 1
    assert len(_disconnected(log, BlindState.CLEARED)) == 1
    events = _events(record)
    assert [e["event"] for e in events] == ["alarm", "recovery"]
    assert source.reopens >= 1, "a dropped live device is reopened, not abandoned"
    assert log.newest("verdict", run_id=RUN).state == VerdictState.HEALTHY
