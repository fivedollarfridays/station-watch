"""Build and run a fault drill on the *same* runner pipeline ``station-watch run`` uses.

A synthetic drill wraps a :class:`~station_watch.synth.source.SyntheticSource` in a
:class:`~station_watch.faults.FaultSource` driven by the schedule and runs the real
:class:`~station_watch.runner.pipeline.Runner` -- Capture, Log, Judge, Alarm and cycle
rows -- with the ``record`` alarm sink. A live drill runs that same Runner on a real
camera while the operator types ``start``/``clear`` lines on stdin, each stamped on the
wall clock. Either way the run is read back from the Log and the alarm record into one
shared-format measurement file (:func:`station_watch.evaluate.provenance.write_measurement`)
plus a markdown table beside it; no second measurement format is defined.
"""

from __future__ import annotations

import math
import sys
import threading
from pathlib import Path

from station_watch.alarm.sink import RecordSink
from station_watch.clock import utc_now_iso
from station_watch.drill.marks import MarkReader, manifest_sha256, marks_from_schedule
from station_watch.drill.report import assemble_metrics, load_alarm_events
from station_watch.drill.table import render_table
from station_watch.evaluate.manifest import sha256_file
from station_watch.evaluate.provenance import DETECTOR, build_provenance, write_measurement
from station_watch.faults import FaultSource, load_fault_schedule
from station_watch.records import BlindState
from station_watch.run import new_run_id
from station_watch.runner.pipeline import Runner
from station_watch.runner.startup import build_context, load_config
from station_watch.synth.source import SyntheticSource

EPOCH = "0001-01-01T00:00:00.000000+00:00"
DEFAULT_FPS = 20.0
RECOVERY_TAIL_S = 2.0
# Bound on the wait for the runner to finish its cycle and Capture shutdown after
# the drill ends; past it the drill fails loud instead of hanging.
STOP_TIMEOUT_S = 10.0
INTERRUPTED_EXIT = 130


def _alarm_record_path(log_path: str | Path) -> Path:
    """The ``record`` sink file, beside the Log -- a transient the drill reads back."""
    log = Path(log_path)
    return log.parent / f"{log.stem}.alarms.jsonl"


def _ensure_record_sink(context, record_path: Path) -> None:
    """Guarantee a ``record`` sink is live, whatever the config named (the drill reads it)."""
    if not any(isinstance(sink, RecordSink) for sink in context.sinks):
        context.sinks.append(RecordSink(record_path))


def _synthetic_max_cycles(config, schedule, tail_s: float) -> int:
    last_clear = max((window.clear_s for window in schedule), default=0.0)
    return max(1, math.ceil((last_clear + tail_s) / config.cycle_interval_s))


def _run_pipeline(context, run_id: str, *, max_cycles: int | None) -> None:
    """Run the real Runner to completion, closing the Log afterwards."""
    runner = Runner(context, run_id=run_id, max_cycles=max_cycles)
    try:
        runner.run()
    finally:
        context.log.close()


def _read_back(log_path: str | Path, record_path: Path, config, marks) -> dict:
    """Score every fault from the Log blind opens and the alarm record events."""
    from station_watch.log import Log

    events = load_alarm_events(record_path)
    with Log(log_path) as log:
        blind = log.since(EPOCH, ["blind"])
    blind_opens = [record for record in blind if record.state == BlindState.OPENED]
    return assemble_metrics(marks, config, events, blind_opens)


def _write_outputs(out_path: str | Path, provenance: dict, metrics: dict, command: str) -> None:
    write_measurement(out_path, provenance, metrics)
    Path(out_path).with_suffix(".md").write_text(render_table(provenance, metrics, command))


def run_synthetic(
    *,
    config_path: str,
    log_path: str,
    out_path: str,
    schedule_path: str,
    command: str,
    fps: float = DEFAULT_FPS,
) -> int:
    """Run a synthetic fault drill from a schedule and write its measurement + table."""
    config = load_config(config_path)
    schedule = load_fault_schedule(schedule_path)
    source = FaultSource(
        SyntheticSource(fps=fps),
        schedule,
        dark_luma_threshold=config.dark_luma_threshold,
        tolerance_px=config.fiducial["tolerance_px"],
    )
    run_start_ts = utc_now_iso()
    marks = marks_from_schedule(schedule, run_start_ts)
    record_path = _alarm_record_path(log_path)
    context = build_context(
        config_path=config_path,
        source_spec=None,
        log_path=log_path,
        alarm_record=str(record_path),
        source=source,
    )
    _ensure_record_sink(context, record_path)
    _run_pipeline(
        context, new_run_id(), max_cycles=_synthetic_max_cycles(config, schedule, RECOVERY_TAIL_S)
    )
    metrics = _read_back(log_path, record_path, config, marks)
    provenance = build_provenance(
        dataset=f"drill:{Path(schedule_path).stem}",
        dataset_kind="synthetic",
        manifest_sha256=sha256_file(schedule_path),
        config_sha256=sha256_file(config_path),
        detector=DETECTOR,
        clips=0,
        sessions=[],
    )
    _write_outputs(out_path, provenance, metrics, command)
    return 0


def _read_marks(stdin) -> tuple[list, str, bool]:
    """Read stdin marks until EOF (Ctrl-D) or Ctrl-C; report whether it was cut short."""
    reader = MarkReader()
    interrupted = False
    try:
        for line in stdin:
            reader.feed(line)
    except KeyboardInterrupt:
        interrupted = True
    marks, manifest = reader.result()
    return marks, manifest, interrupted


def _stop_runner(runner, thread: threading.Thread) -> bool:
    """Ask the runner to stop at its next cycle boundary; ``False`` if it would not."""
    runner.stop()
    thread.join(timeout=STOP_TIMEOUT_S)
    if not thread.is_alive():
        return True
    print(
        f"station-watch: drill runner did not stop within {STOP_TIMEOUT_S:.0f} s of the "
        "end of input; no measurement written",
        file=sys.stderr,
        flush=True,
    )
    return False


def run_live(
    *,
    config_path: str,
    log_path: str,
    out_path: str,
    source_spec: str,
    command: str,
    max_cycles: int | None = None,
    stdin=None,
) -> int:
    """Run a live fault drill on a real source, stamping stdin start/clear marks.

    The drill ends when stdin does (Ctrl-D): the runner is stopped at its next cycle
    boundary and the measurement written. Ctrl-C does the same but exits non-zero, as
    the drill is incomplete; a runner that will not stop fails loud rather than hang.
    """
    config = load_config(config_path)
    record_path = _alarm_record_path(log_path)
    context = build_context(
        config_path=config_path,
        source_spec=source_spec,
        log_path=log_path,
        alarm_record=str(record_path),
    )
    _ensure_record_sink(context, record_path)
    runner = Runner(context, run_id=new_run_id(), max_cycles=max_cycles)
    thread = threading.Thread(target=runner.run, name="station-watch-drill", daemon=True)
    thread.start()
    marks, manifest, interrupted = _read_marks(stdin if stdin is not None else sys.stdin)
    if not _stop_runner(runner, thread):
        return 1
    context.log.close()
    metrics = _read_back(log_path, record_path, config, marks)
    provenance = build_provenance(
        dataset="drill:live",
        dataset_kind="real",
        manifest_sha256=manifest_sha256(manifest),
        config_sha256=sha256_file(config_path),
        detector=DETECTOR,
        clips=0,
        sessions=[],
    )
    _write_outputs(out_path, provenance, metrics, command)
    if interrupted:
        print("station-watch: drill interrupted; wrote what was measured", file=sys.stderr)
        return INTERRUPTED_EXIT
    return 0


__all__ = ["run_synthetic", "run_live", "DEFAULT_FPS"]
