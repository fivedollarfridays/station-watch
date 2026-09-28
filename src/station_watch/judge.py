"""Component 3: the Judge -- healthy, fault, or unobservable.

The Judge is the only place "age versus window" and "absence is a fault" live.
It reads the station config and the Log: frame, blind and observation records all
land in the Log on one canonical ``ts`` ordering (observations are appended by
whoever produces them -- the fixture loader now, Detect in HF2), so the Judge
never has to reconcile two clocks. It renders one :class:`Verdict` per call and
writes it back to the Log.

The three outcomes are ranked (README K1: unobservable never folds into healthy):

* **unobservable** wins outright when any blind condition is active. A blind
  camera is judged on no observations at all -- never healthy, and never a fault
  read off stale frames the camera can no longer refresh.
* **fault** is a positive detection, cited to the frames it came from (K4): a unit
  with ``no_motion`` for longer than ``takt_s + grace_s`` is ``stalled``; a
  required slot whose latest reading is ``part_absent`` is ``missing_part``; a
  person in an active keep-out zone is ``keepout_entry``.
* **healthy** requires positive confirmation -- every required slot read
  ``part_present``. A ``part_unknown`` slot is neither present nor absent (README
  point 2), so it raises no ``missing_part`` fault yet cannot read healthy either;
  with nothing else wrong the station is unobservable, not healthy.

Keep-out zones are treated as active for the single running HF1 station, so a
person in a configured zone is a fault; the "while active" gate is where a future
schedule would attach.
"""

from __future__ import annotations

from station_watch.clock import parse_iso, utc_now_iso
from station_watch.judge_inputs import JudgeInputs
from station_watch.records import (
    BlindReason,
    Fault,
    FaultKind,
    Observation,
    ObservationKind,
    Verdict,
    VerdictState,
)

_PART_KINDS = frozenset(
    {ObservationKind.PART_PRESENT, ObservationKind.PART_ABSENT, ObservationKind.PART_UNKNOWN}
)
_ALL_KINDS = frozenset(ObservationKind)


def _order_key(obs: Observation) -> tuple[str, int]:
    return (obs.ts, obs.frame_id)


class Judge:
    """Renders a :class:`Verdict` from the current Log state and appends it."""

    def __init__(self, config, *, run_id: str, clock=utc_now_iso) -> None:
        self._config = config
        self._run_id = run_id
        self._clock = clock
        self._seq = 0
        self._inputs = JudgeInputs(camera_id=config.camera_id, run_id=run_id)

    def judge(self, log, now_ts: str | None = None, *, stream_ended: bool = False) -> Verdict:
        """Judge the station as of ``now_ts`` (default: now), writing the verdict.

        ``stream_ended`` is set by the Runner once a *recorded file* has been read
        to its end: the last frames are then the whole story, not a camera that
        went quiet, so frame freshness (K2) is not held against them.
        """
        now = now_ts if now_ts is not None else self._clock()
        self._inputs.refresh(log, now)
        active = self._inputs.active_blind_reasons()
        if not stream_ended and self._frames_stale(log, now):
            active.add(BlindReason.DISCONNECTED)
        verdict = self._verdict(sorted(active, key=lambda r: r.value), now)
        log.append(verdict)
        return verdict

    def _frames_stale(self, log, now: str) -> bool:
        """K2 in the Judge itself: no frame of this run within the liveness window."""
        newest = log.newest("frame", run_id=self._run_id, until=now)
        if newest is None:
            return True
        age_s = (parse_iso(now) - parse_iso(newest.ts)).total_seconds()
        return age_s > self._config.liveness_window_s

    def _verdict(self, active, now: str) -> Verdict:
        if active:
            return self._build(VerdictState.UNOBSERVABLE, (), tuple(active), now)
        by_target = self._inputs.by_target
        faults = self._faults(by_target, now)
        if faults:
            return self._build(VerdictState.FAULT, tuple(faults), (), now)
        if self._all_required_slots_present(by_target):
            return self._build(VerdictState.HEALTHY, (), (), now)
        return self._build(VerdictState.UNOBSERVABLE, (), (), now)

    def _build(self, state, faults, blind_reasons, now: str) -> Verdict:
        self._seq += 1
        return Verdict(
            station_id=self._config.station_id,
            ts=now,
            state=state,
            faults=faults,
            blind_reasons=blind_reasons,
            seq=self._seq,
            run_id=self._run_id,
        )

    def _faults(self, by_target, now: str) -> list[Fault]:
        return [
            *self._stall_faults(by_target, now),
            *self._missing_part_faults(by_target),
            *self._keepout_faults(by_target),
        ]

    def _stall_faults(self, by_target, now: str) -> list[Fault]:
        window = self._config.takt_s + self._config.grace_s
        faults = []
        for target, observations in by_target.items():
            motion = self._latest(observations, {ObservationKind.MOTION})
            streak = self._no_motion_streak(observations, motion)
            if not streak:
                continue
            reference_ts = motion.ts if motion is not None else streak[0].ts
            age_s = (parse_iso(now) - parse_iso(reference_ts)).total_seconds()
            if age_s > window:
                frame_ids = tuple(sorted(obs.frame_id for obs in streak))
                faults.append(Fault(FaultKind.STALLED, target, frame_ids))
        return faults

    def _missing_part_faults(self, by_target) -> list[Fault]:
        faults = []
        for slot in self._config.required_slots:
            latest = self._latest(by_target.get(slot, []), _PART_KINDS)
            if latest is not None and latest.kind == ObservationKind.PART_ABSENT:
                faults.append(Fault(FaultKind.MISSING_PART, slot, (latest.frame_id,)))
        return faults

    def _keepout_faults(self, by_target) -> list[Fault]:
        faults = []
        for zone in self._config.keepout_zones:
            observations = by_target.get(zone, [])
            latest = self._latest(observations, _ALL_KINDS)
            if latest is None or latest.kind != ObservationKind.PERSON_IN_KEEPOUT:
                continue
            frame_ids = self._trailing_frames(observations, ObservationKind.PERSON_IN_KEEPOUT)
            faults.append(Fault(FaultKind.KEEPOUT_ENTRY, zone, frame_ids))
        return faults

    def _all_required_slots_present(self, by_target) -> bool:
        for slot in self._config.required_slots:
            latest = self._latest(by_target.get(slot, []), _PART_KINDS)
            if latest is None or latest.kind != ObservationKind.PART_PRESENT:
                return False
        return True

    def _no_motion_streak(self, observations, motion) -> list[Observation]:
        no_motion = [obs for obs in observations if obs.kind == ObservationKind.NO_MOTION]
        if motion is not None:
            no_motion = [obs for obs in no_motion if _order_key(obs) > _order_key(motion)]
        return sorted(no_motion, key=_order_key)

    def _trailing_frames(self, observations, kind) -> tuple[int, ...]:
        trailing = []
        for obs in sorted(observations, key=_order_key, reverse=True):
            if obs.kind != kind:
                break
            trailing.append(obs.frame_id)
        return tuple(sorted(trailing))

    def _latest(self, observations, kinds):
        candidates = [obs for obs in observations if obs.kind in kinds]
        return max(candidates, key=_order_key) if candidates else None


__all__ = ["Judge"]
