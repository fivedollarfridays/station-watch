"""The Judge's per-target rules: pure functions over observations grouped by target.

Split out of :mod:`station_watch.judge` so the Judge class holds only the verdict
flow (blind first, then faults, then positive-confirmation healthy). Each rule here
reads the latest observation of the relevant kinds per target and never touches
the Log:

* :func:`stall_faults` -- a ``no_motion`` streak older than the stall window.
* :func:`missing_part_faults` -- a required slot whose latest reading is ``part_absent``.
* :func:`keepout_faults` -- a zone whose latest reading is ``person_in_keepout``.
* :func:`all_required_slots_present` / :func:`all_keepout_zones_clear` -- the K1
  positive confirmation healthy requires (unknown or no reading is never safe).
"""

from __future__ import annotations

from station_watch.clock import parse_iso
from station_watch.records import Fault, FaultKind, Observation, ObservationKind

PART_KINDS = frozenset(
    {ObservationKind.PART_PRESENT, ObservationKind.PART_ABSENT, ObservationKind.PART_UNKNOWN}
)
KEEPOUT_KINDS = frozenset(
    {ObservationKind.PERSON_IN_KEEPOUT, ObservationKind.ZONE_CLEAR, ObservationKind.ZONE_UNKNOWN}
)


def _order_key(obs: Observation) -> tuple[str, int]:
    return (obs.ts, obs.frame_id)


def latest(observations, kinds) -> Observation | None:
    """The newest observation (by ts, then frame) whose kind is in ``kinds``."""
    candidates = [obs for obs in observations if obs.kind in kinds]
    return max(candidates, key=_order_key) if candidates else None


def _no_motion_streak(observations, motion) -> list[Observation]:
    no_motion = [obs for obs in observations if obs.kind == ObservationKind.NO_MOTION]
    if motion is not None:
        no_motion = [obs for obs in no_motion if _order_key(obs) > _order_key(motion)]
    return sorted(no_motion, key=_order_key)


def _trailing_frames(observations, kind) -> tuple[int, ...]:
    trailing = []
    for obs in sorted(observations, key=_order_key, reverse=True):
        if obs.kind != kind:
            break
        trailing.append(obs.frame_id)
    return tuple(sorted(trailing))


def stall_faults(by_target, now: str, window_s: float) -> list[Fault]:
    """``stalled`` for every target with no motion for longer than ``window_s``."""
    faults = []
    for target, observations in by_target.items():
        motion = latest(observations, {ObservationKind.MOTION})
        streak = _no_motion_streak(observations, motion)
        if not streak:
            continue
        reference_ts = motion.ts if motion is not None else streak[0].ts
        age_s = (parse_iso(now) - parse_iso(reference_ts)).total_seconds()
        if age_s > window_s:
            frame_ids = tuple(sorted(obs.frame_id for obs in streak))
            faults.append(Fault(FaultKind.STALLED, target, frame_ids))
    return faults


def missing_part_faults(by_target, required_slots) -> list[Fault]:
    """``missing_part`` for each required slot whose latest part reading is absent.

    A dotted detail slot (``<position_id>.<kind>``) raises no ``missing_part`` while
    its parent position already has one -- a missing component is one fault (the
    position), never also one per detail it carried.
    """
    faults = []
    for slot in required_slots:
        newest = latest(by_target.get(slot, []), PART_KINDS)
        if newest is not None and newest.kind == ObservationKind.PART_ABSENT:
            faults.append(Fault(FaultKind.MISSING_PART, slot, (newest.frame_id,)))
    faulted = {f.target for f in faults}
    return [f for f in faults if "." not in f.target or f.target.split(".", 1)[0] not in faulted]


def keepout_faults(by_target, keepout_zones) -> list[Fault]:
    """``keepout_entry`` for each zone whose latest reading is a person in it."""
    faults = []
    for zone in keepout_zones:
        observations = by_target.get(zone, [])
        newest = latest(observations, KEEPOUT_KINDS)
        if newest is None or newest.kind != ObservationKind.PERSON_IN_KEEPOUT:
            continue
        frame_ids = _trailing_frames(observations, ObservationKind.PERSON_IN_KEEPOUT)
        faults.append(Fault(FaultKind.KEEPOUT_ENTRY, zone, frame_ids))
    return faults


def all_required_slots_present(by_target, required_slots) -> bool:
    """Every required slot's latest part reading is ``part_present``."""
    for slot in required_slots:
        newest = latest(by_target.get(slot, []), PART_KINDS)
        if newest is None or newest.kind != ObservationKind.PART_PRESENT:
            return False
    return True


def all_keepout_zones_clear(by_target, keepout_zones) -> bool:
    """K1: a configured keep-out zone is clear only on a latest ``zone_clear``.

    A ``zone_unknown`` reading, or none at all, is not clear -- with nothing else
    wrong the station is unobservable, never healthy (a zone a person could be in
    is never read as safe without positive confirmation).
    """
    for zone in keepout_zones:
        newest = latest(by_target.get(zone, []), KEEPOUT_KINDS)
        if newest is None or newest.kind != ObservationKind.ZONE_CLEAR:
            return False
    return True


__all__ = [
    "KEEPOUT_KINDS",
    "PART_KINDS",
    "all_keepout_zones_clear",
    "all_required_slots_present",
    "keepout_faults",
    "latest",
    "missing_part_faults",
    "stall_faults",
]
