"""Station Watch: camera-health monitoring for a single assembly station.

This package defines the shared record formats, station configuration and
fixture loading that every later pipeline component (Capture, Log, Judge, Alarm,
Watchdog, runner) builds on.
"""

from __future__ import annotations

from station_watch.clock import offset_iso, parse_iso, utc_now_iso
from station_watch.config import REQUIRED_KEYS, StationConfig, load_station_config
from station_watch.fixtures import load_fixture_observations
from station_watch.judge import Judge
from station_watch.records import (
    AlarmEvaluated,
    BlindReason,
    BlindRecord,
    BlindState,
    CycleCompleted,
    Fault,
    FaultKind,
    FrameRecord,
    Observation,
    ObservationKind,
    Verdict,
    VerdictState,
)
from station_watch.run import new_run_id

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "AlarmEvaluated",
    "BlindReason",
    "BlindRecord",
    "BlindState",
    "CycleCompleted",
    "Fault",
    "FaultKind",
    "FrameRecord",
    "Observation",
    "ObservationKind",
    "Verdict",
    "VerdictState",
    "StationConfig",
    "REQUIRED_KEYS",
    "load_station_config",
    "load_fixture_observations",
    "Judge",
    "new_run_id",
    "utc_now_iso",
    "parse_iso",
    "offset_iso",
    "SyntheticSource",
    "write_synth_station_clip",
    "FaultSource",
    "FaultWindow",
    "FaultScheduleError",
    "load_fault_schedule",
    "FAULT_NAMES",
]


def __getattr__(name: str):
    """Lazily expose the synthetic-source and fault-injection building blocks.

    These pull in OpenCV, so they stay out of the eager imports above -- merely
    ``import station_watch`` must not drag in the heavy rendering stack. The drill
    and physics scripts reach them as ``station_watch.SyntheticSource`` /
    ``station_watch.FaultSource`` (HF3A.1).
    """
    if name in ("SyntheticSource", "write_synth_station_clip"):
        import station_watch.synth as synth

        return getattr(synth, name)
    if name in (
        "FaultSource",
        "FaultWindow",
        "FaultScheduleError",
        "load_fault_schedule",
        "FAULT_NAMES",
    ):
        import station_watch.faults as faults

        return getattr(faults, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
