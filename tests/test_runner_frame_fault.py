"""A per-frame processing error must never silently kill Capture (review P1).

Each test drives the *real* Runner (real Capture thread, Log, Judge, Alarm, the
record sink standing in for the buzzer) over a healthy fake live camera, then
injects a fault into Capture's per-frame processing by swapping the fingerprint
function for one that raises. The failure must land as one ``disconnected``
BlindRecord carrying the error, raise one alarm, be reported once on stderr, and
leave the Capture thread alive; once the fault stops, good frames clear the
record and the alarm recovers exactly once. If the Capture thread exits anyway,
the Runner notices on its next cycle and alarms loudly instead of carrying on.
"""

import json
import sys
import threading
import time
from pathlib import Path

import station_watch.capture.capture as capture_mod
from station_watch.alarm.sink import RecordSink
from station_watch.capture.blind import BlindThresholds
from station_watch.log import Log
from station_watch.records import BlindReason, BlindState, VerdictState
from station_watch.runner.pipeline import CAPTURE_THREAD_NAME, Runner
from station_watch.runner.startup import RunContext

sys.path.insert(0, str(Path(__file__).parent))
from helpers.live_sources import FailingLiveSource  # noqa: E402
from helpers.records import health_config  # noqa: E402

EPOCH = "0001-01-01T00:00:00.000000+00:00"
RUN = "run-frame-fault"


def _start(tmp_path, source):
    config = health_config(liveness_window_s=1.0)
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


def _capture_threads():
    return [t for t in threading.enumerate() if t.name == CAPTURE_THREAD_NAME and t.is_alive()]


def _faulty_fingerprint(fault: threading.Event):
    real = capture_mod.fingerprint

    def fingerprint(frame):
        if fault.is_set():
            raise RuntimeError("injected fingerprint failure " + "x" * 400)
        return real(frame)

    return fingerprint


def test_per_frame_error_opens_one_record_alarms_and_capture_survives(
    tmp_path, monkeypatch, capsys
):
    fault = threading.Event()
    monkeypatch.setattr(capture_mod, "fingerprint", _faulty_fingerprint(fault))
    source = FailingLiveSource(frames_before=10**9)
    runner, thread, log, record = _start(tmp_path, source)
    try:
        assert _wait_for(lambda: log.newest("verdict", run_id=RUN) is not None, 5.0)
        fault.set()
        assert _wait_for(lambda: _disconnected(log, BlindState.OPENED), 5.0)
        assert _wait_for(lambda: any(e["event"] == "alarm" for e in _events(record)), 5.0)
        time.sleep(1.0)  # ~20 more failing frames: still one record, thread alive
        assert _capture_threads(), "a per-frame error must not kill the Capture thread"
        assert len(_disconnected(log, BlindState.OPENED)) == 1
        fault.clear()
        assert _wait_for(lambda: _disconnected(log, BlindState.CLEARED), 5.0)
        assert _wait_for(lambda: any(e["event"] == "recovery" for e in _events(record)), 5.0)
    finally:
        runner.stop()
        thread.join(timeout=5.0)

    opened = _disconnected(log, BlindState.OPENED)
    assert len(opened) == 1
    error = opened[0].evidence["error"]
    assert error.startswith("RuntimeError: injected fingerprint failure")
    assert len(error) <= len("RuntimeError: ") + 200
    assert len(_disconnected(log, BlindState.CLEARED)) == 1
    assert [e["event"] for e in _events(record)] == ["alarm", "recovery"]
    assert log.newest("verdict", run_id=RUN).state == VerdictState.HEALTHY
    err = capsys.readouterr().err
    assert err.count("injected fingerprint failure") == 1, err


def test_capture_thread_exit_is_noticed_and_alarms_loudly(tmp_path, monkeypatch, capsys):
    def dies(self, *_args, **_kwargs):
        raise RuntimeError("capture loop blew up")

    monkeypatch.setattr(capture_mod.Capture, "_drive", dies)
    source = FailingLiveSource(frames_before=10**9)
    runner, thread, log, record = _start(tmp_path, source)
    try:
        assert _wait_for(lambda: _disconnected(log, BlindState.OPENED), 5.0)
        assert _wait_for(lambda: any(e["event"] == "alarm" for e in _events(record)), 5.0)
        time.sleep(0.5)
        assert thread.is_alive(), "the runner keeps cycling (and alarming) after Capture dies"
    finally:
        runner.stop()
        thread.join(timeout=5.0)

    opened = _disconnected(log, BlindState.OPENED)
    assert len(opened) == 1
    assert "capture thread exited" in opened[0].evidence["error"]
    alarms = [e for e in _events(record) if e["event"] == "alarm"]
    assert [a["cause"] for a in alarms] == ["unobservable:disconnected"]
    err = capsys.readouterr().err
    assert err.count("capture thread exited") == 1, err
