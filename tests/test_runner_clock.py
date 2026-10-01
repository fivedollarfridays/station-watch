"""The Runner hands its injected clock to Capture, so frames share the run clock.

Every record the pipeline writes -- frames, verdicts, cycles -- is ordered by one
``ts``. If Capture stamped frames from the wall clock while the Judge and Alarm
ran on an injected (test or replay) clock, the two timelines would drift and the
Judge could read a frame as stale or fresh by accident. This proves a Runner
built with a fake clock produces ``FrameRecord.ts`` values from *that* clock.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from station_watch.log import Log
from station_watch.runner.pipeline import Runner
from station_watch.runner.startup import build_context

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import HEALTH_CONFIG  # noqa: E402
from helpers.synth_video import write_synth_clip  # noqa: E402

EPOCH = "0001-01-01T00:00:00.000000+00:00"
FAKE_TS = "2026-09-28T12:00:00.000000+00:00"


def test_runner_clock_reaches_capture_frame_timestamps(tmp_path):
    clip = write_synth_clip(tmp_path / "clip", frames=12, fps=50.0)
    config_path = tmp_path / "station.yaml"
    config_path.write_text(yaml.safe_dump({**HEALTH_CONFIG, "liveness_window_s": 0.5}))
    logdb = tmp_path / "log.db"

    context = build_context(
        config_path=str(config_path),
        source_spec=str(clip),
        log_path=str(logdb),
        alarm_record=str(tmp_path / "alarm.jsonl"),
    )
    runner = Runner(context, run_id="run-clock-test", clock=lambda: FAKE_TS)
    runner.run()

    with Log(logdb) as log:
        frames = log.since(EPOCH, ["frame"])

    assert frames, "Capture must have written frame records"
    assert all(f.ts == FAKE_TS for f in frames), "every frame ts must come from the runner's clock"


def test_runner_monotonic_reaches_capture_mono(tmp_path):
    # The synthetic drill runs the pipeline on a frame-index clock; Capture's
    # ``capture_mono`` (and its liveness timer) must come from that same clock, or a
    # wall-clock stall would read as a disconnect the drill never injected.
    import itertools

    clip = write_synth_clip(tmp_path / "clip", frames=12, fps=50.0)
    config_path = tmp_path / "station.yaml"
    config_path.write_text(yaml.safe_dump({**HEALTH_CONFIG, "liveness_window_s": 0.5}))
    logdb = tmp_path / "log.db"
    context = build_context(
        config_path=str(config_path),
        source_spec=str(clip),
        log_path=str(logdb),
        alarm_record=str(tmp_path / "alarm.jsonl"),
    )
    ticks = itertools.count(5000.0, 0.001)
    runner = Runner(context, run_id="run-mono-test", monotonic=lambda: next(ticks))
    runner.run()

    with Log(logdb) as log:
        frames = log.since(EPOCH, ["frame"])
    assert frames, "Capture must have written frame records"
    assert all(f.capture_mono >= 5000.0 for f in frames), "capture_mono comes from the runner"
