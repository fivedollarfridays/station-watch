"""Station Watch: camera-health monitoring for a single assembly station.

This package defines the shared record formats, station configuration and
fixture loading that every later pipeline component (Capture, Log, Judge, Alarm,
Watchdog, runner) builds on.
"""

from __future__ import annotations

from station_watch.clock import offset_iso, parse_iso, utc_now_iso
from station_watch.config import REQUIRED_KEYS, StationConfig, load_station_config
from station_watch.fixtures import load_fixture_observations
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
    "new_run_id",
    "utc_now_iso",
    "parse_iso",
    "offset_iso",
]
