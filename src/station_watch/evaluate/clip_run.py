"""Drive one clip through the real pipeline and collect what it produced.

The harness runs each clip in-process through the real :class:`Runner` -- the same
Capture, Detect, Judge and Alarm the ``station-watch run`` subprocess wires (the
keep-out e2e test in ``tests/test_e2e_detect.py`` establishes that in-process run
*is* the real path) -- so a keep-out clip can inject a scripted person backend the
YOLOX model would never fire on a synthetic blob. Verdicts, observations, alarm
episodes and frame rows are read back out of the Log and the record sink into a
:class:`~station_watch.evaluate.extract.ClipRun`.
"""

from __future__ import annotations

import json
from pathlib import Path

from station_watch.alarm.sink import build_sinks
from station_watch.capture.blind import BlindThresholds
from station_watch.evaluate.extract import ClipRun
from station_watch.log import Log
from station_watch.run import new_run_id
from station_watch.runner.pipeline import Runner
from station_watch.runner.startup import RunContext, resolve_stall_window_or_fail

EPOCH = "0001-01-01T00:00:00.000000+00:00"


def _read_alarms(record_path: Path) -> list[dict]:
    if not record_path.exists():
        return []
    return [json.loads(line) for line in record_path.read_text().splitlines() if line.strip()]


def run_clip(config, clip_path, work_dir, *, session, rel_path, speed, keepout_backend=None):
    """Run ``clip_path`` through the real pipeline; return a :class:`ClipRun`."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    log_path = work_dir / "log.db"
    record_path = work_dir / "alarm.jsonl"
    from station_watch.capture.source import FrameSource

    stall_window_s, stall_window_source = resolve_stall_window_or_fail(config)
    context = RunContext(
        config=config,
        source=FrameSource(str(clip_path)),
        log=Log(log_path),
        sinks=build_sinks(config.alarm, record_path=record_path),
        thresholds=BlindThresholds.from_station_config(config),
        keepout_backend=keepout_backend,
        stall_window_s=stall_window_s,
        stall_window_source=stall_window_source,
    )
    runner = Runner(context, run_id=new_run_id(), speed=speed)
    try:
        runner.run()
    finally:
        context.log.close()
        context.source.release()
    with Log(log_path) as log:
        verdicts = log.since(EPOCH, ["verdict"])
        observations = log.since(EPOCH, ["obs"])
        frames = log.since(EPOCH, ["frame"])
    return ClipRun(
        session=session,
        rel_path=rel_path,
        verdicts=verdicts,
        observations=observations,
        alarms=_read_alarms(record_path),
        frames=frames,
    )


__all__ = ["run_clip", "EPOCH"]
