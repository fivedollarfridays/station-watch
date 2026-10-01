"""A live drill ends when the operator ends it, never hangs on an endless camera.

A real camera never runs out of frames, so a live drill with no ``--max-cycles`` must
stop when stdin reaches EOF (Ctrl-D, "the drill is over"): the runner is asked to stop
at its next cycle boundary, joined with a bounded timeout, and the measurement and
table are written. A runner that will not stop fails loud instead of hanging, and an
interrupt (Ctrl-C) stops the runner, writes what was measured, and exits non-zero.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from station_watch.drill import pipeline as drill_pipeline
from station_watch.runner import startup
from station_watch.synth.source import SyntheticSource

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "measurements" / "synthetic" / "drill-config.yaml"
CHILD = Path(__file__).parent / "_synthetic_camera_child.py"


@pytest.fixture
def endless_camera(monkeypatch):
    """Make device index ``0`` an endless live-paced synthetic camera."""
    real = startup.FrameSource

    def fake_source(spec):
        return SyntheticSource(fps=20.0) if isinstance(spec, int) else real(spec)

    monkeypatch.setattr(startup, "FrameSource", fake_source)


def _paced_marks(lines, pause_s=0.3):
    """Yield stdin lines with a pause between them, then EOF."""
    for line in lines:
        time.sleep(pause_s)
        yield line + "\n"
    time.sleep(pause_s)


def _run_live(tmp_path, stdin, **kwargs):
    return drill_pipeline.run_live(
        config_path=str(CONFIG),
        log_path=str(tmp_path / "l.db"),
        out_path=str(tmp_path / "o.json"),
        source_spec="0",
        command="x",
        stdin=stdin,
        **kwargs,
    )


def test_stdin_eof_stops_an_endless_live_drill_and_writes_outputs(tmp_path, endless_camera):
    start = time.monotonic()
    rc = _run_live(tmp_path, _paced_marks(["start frozen", "clear frozen"]))
    assert time.monotonic() - start < 10.0, "EOF on stdin must end the drill promptly"
    assert rc == 0
    data = json.loads((tmp_path / "o.json").read_text())
    entry = data["metrics"]["faults"]["frozen"]
    assert entry["injected_ts"] and entry["cleared_ts"]
    assert (tmp_path / "o.md").exists(), "the table is written beside the measurement"


def test_interrupt_stops_the_runner_writes_what_was_measured_and_fails(tmp_path, endless_camera):
    def interrupted():
        yield from _paced_marks(["start bumped"])
        raise KeyboardInterrupt

    start = time.monotonic()
    rc = _run_live(tmp_path, interrupted())
    assert time.monotonic() - start < 10.0
    assert rc != 0, "an interrupted drill is incomplete and exits non-zero"
    data = json.loads((tmp_path / "o.json").read_text())
    assert "bumped" in data["metrics"]["faults"], "the mark typed before Ctrl-C is kept"


def test_a_runner_that_will_not_stop_fails_loud_instead_of_hanging(
    tmp_path, endless_camera, monkeypatch, capsys
):
    release = threading.Event()

    class _StuckRunner:
        def __init__(self, context, **_kwargs):
            self._context = context

        def stop(self):
            pass  # ignores the request -- the failure the timeout exists for

        def run(self):
            release.wait(30)

    monkeypatch.setattr(drill_pipeline, "Runner", _StuckRunner)
    monkeypatch.setattr(drill_pipeline, "STOP_TIMEOUT_S", 0.5)
    start = time.monotonic()
    try:
        rc = _run_live(tmp_path, iter(["start frozen\n"]))
    finally:
        release.set()
    assert time.monotonic() - start < 5.0, "a stuck runner must not hang the drill"
    assert rc != 0
    assert "did not stop" in capsys.readouterr().err


def _spawn_live_drill(tmp_path):
    return subprocess.Popen(
        [
            sys.executable,
            str(CHILD),
            "drill",
            "--config",
            str(CONFIG),
            "--log",
            str(tmp_path / "drill.db"),
            "--out",
            str(tmp_path / "drill.json"),
            "--live",
            "--source",
            "0",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _type_marks(proc, lines):
    for line in lines:
        time.sleep(0.5)
        proc.stdin.write(line + "\n")
        proc.stdin.flush()
    time.sleep(0.5)


def test_real_cli_live_drill_on_endless_camera_exits_on_stdin_eof(tmp_path):
    proc = _spawn_live_drill(tmp_path)
    _type_marks(proc, ["start lens_covered", "clear lens_covered"])
    _stdout, stderr = proc.communicate(timeout=20)  # closes stdin: the operator's Ctrl-D
    assert proc.returncode == 0, stderr
    out = tmp_path / "drill.json"
    assert "lens_covered" in json.loads(out.read_text())["metrics"]["faults"]
    assert out.with_suffix(".md").exists()


def test_real_cli_live_drill_ctrl_c_writes_partial_and_exits_nonzero(tmp_path):
    proc = _spawn_live_drill(tmp_path)
    _type_marks(proc, ["start frozen"])
    proc.send_signal(signal.SIGINT)
    _stdout, stderr = proc.communicate(timeout=20)
    assert proc.returncode == drill_pipeline.INTERRUPTED_EXIT, stderr
    assert "interrupted" in stderr
    out = tmp_path / "drill.json"
    assert "frozen" in json.loads(out.read_text())["metrics"]["faults"]
