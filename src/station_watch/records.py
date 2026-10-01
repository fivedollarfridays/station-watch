"""The record formats every later Station Watch component shares.

All records are frozen dataclasses that round-trip through :class:`Serde`. Each
carries three common fields:

* ``ts`` -- wall clock, ISO 8601 UTC with microseconds; the one canonical time
  the Log orders by and the Watchdog judges against.
* ``run_id`` -- a UUID4 minted once per process start (see :mod:`station_watch.run`).
* ``record_id`` -- a deterministic idempotency key, always prefixed with
  ``run_id`` and derived per type in each ``__post_init__`` below, so a replayed
  row dedups and two distinct events never collide.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from station_watch.serde import Serde


class BlindReason(StrEnum):
    DISCONNECTED = "disconnected"
    FROZEN = "frozen"
    DARK = "dark"
    FIDUCIAL_MISSING = "fiducial_missing"
    VIEW_SHIFTED = "view_shifted"


class BlindState(StrEnum):
    OPENED = "opened"
    CLEARED = "cleared"


class ObservationKind(StrEnum):
    PART_PRESENT = "part_present"
    PART_ABSENT = "part_absent"
    PART_UNKNOWN = "part_unknown"
    PERSON_IN_KEEPOUT = "person_in_keepout"
    ZONE_CLEAR = "zone_clear"
    ZONE_UNKNOWN = "zone_unknown"
    MOTION = "motion"
    NO_MOTION = "no_motion"


class VerdictState(StrEnum):
    HEALTHY = "healthy"
    FAULT = "fault"
    UNOBSERVABLE = "unobservable"


class FaultKind(StrEnum):
    STALLED = "stalled"
    MISSING_PART = "missing_part"
    KEEPOUT_ENTRY = "keepout_entry"
    CYCLE_TIME_CREEP = "cycle_time_creep"


@dataclass(frozen=True)
class Fault(Serde):
    kind: FaultKind
    target: str
    frame_ids: tuple[int, ...]


@dataclass(frozen=True)
class FrameRecord(Serde):
    station_id: str
    camera_id: str
    frame_id: int  # monotonic per camera
    ts: str  # capture wall time
    capture_mono: float  # monotonic seconds, meaningful only in the capturing process
    fingerprint: str  # hash of raw bytes
    mean_luma: float
    noise_score: float
    run_id: str
    record_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "record_id", f"{self.run_id}:frame:{self.camera_id}:{self.frame_id}"
        )


@dataclass(frozen=True)
class BlindRecord(Serde):
    station_id: str
    camera_id: str
    ts: str
    reason: BlindReason
    evidence: dict
    last_good_frame_id: int | None
    state: BlindState
    seq: int  # per-process counter
    run_id: str
    record_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "record_id",
            f"{self.run_id}:blind:{self.camera_id}:{self.reason.value}:{self.state.value}:{self.seq}",
        )


@dataclass(frozen=True)
class Observation(Serde):
    """The Detect output format (Detect itself is HF2)."""

    station_id: str
    frame_id: int
    ts: str
    kind: ObservationKind
    target: str  # slot or zone id
    method: str
    confidence_ceiling: float  # 0..1
    detector_output: dict
    run_id: str
    record_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "record_id",
            f"{self.run_id}:obs:{self.station_id}:{self.frame_id}:{self.kind.value}:{self.target}",
        )


@dataclass(frozen=True)
class Verdict(Serde):
    station_id: str
    ts: str
    state: VerdictState
    faults: tuple[Fault, ...]
    blind_reasons: tuple[BlindReason, ...]  # active reasons when unobservable
    seq: int
    run_id: str
    record_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", f"{self.run_id}:verdict:{self.station_id}:{self.seq}")


@dataclass(frozen=True)
class CycleCompleted(Serde):
    ts: str
    cycle: int
    stages: tuple[str, ...]
    run_id: str
    record_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", f"{self.run_id}:cycle:{self.cycle}")


@dataclass(frozen=True)
class AlarmEvaluated(Serde):
    ts: str
    seq: int
    open_episodes: tuple[str, ...]
    run_id: str
    record_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", f"{self.run_id}:alarm_eval:{self.seq}")


__all__ = [
    "BlindReason",
    "BlindState",
    "ObservationKind",
    "VerdictState",
    "FaultKind",
    "Fault",
    "FrameRecord",
    "BlindRecord",
    "Observation",
    "Verdict",
    "CycleCompleted",
    "AlarmEvaluated",
]
