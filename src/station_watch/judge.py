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
  ``part_present`` *and* every configured keep-out zone read ``zone_clear`` (K1). A
  ``part_unknown`` slot or a ``zone_unknown`` zone is neither safe nor a positive
  fault, and a zone with no reading at all is not clear, so with nothing else wrong
  the station is unobservable, not healthy.

A person in a configured keep-out zone is a ``keepout_entry`` fault; an inactive
zone reads ``zone_clear`` (Detect ignores its person boxes), so it never faults.
"""

from __future__ import annotations

from station_watch.clock import parse_iso, utc_now_iso
from station_watch.judge_inputs import JudgeInputs
from station_watch.judge_rules import (
    all_keepout_zones_clear,
    all_required_slots_present,
    keepout_faults,
    missing_part_faults,
    stall_faults,
)
from station_watch.records import BlindReason, Fault, Verdict, VerdictState
from station_watch.steps import cycle_time_creep_faults


class Judge:
    """Renders a :class:`Verdict` from the current Log state and appends it."""

    def __init__(
        self, config, *, run_id: str, clock=utc_now_iso, stall_window_s: float | None = None
    ) -> None:
        self._config = config
        self._run_id = run_id
        self._clock = clock
        self._seq = 0
        # The stall window's source is resolved at startup (measured step times vs
        # configured takt, see :mod:`station_watch.steps`); default here keeps the
        # Judge usable standalone (tests, no runner) on ``takt_s + grace_s``.
        self._stall_window = (
            stall_window_s if stall_window_s is not None else config.takt_s + config.grace_s
        )
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
        config = self._config
        if all_required_slots_present(by_target, config.required_slots) and all_keepout_zones_clear(
            by_target, config.keepout_zones
        ):
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
            *stall_faults(by_target, now, self._stall_window),
            *missing_part_faults(by_target, self._config.required_slots),
            *keepout_faults(by_target, self._config.keepout_zones),
            *cycle_time_creep_faults(by_target, self._config, self._stall_window),
        ]


__all__ = ["Judge"]
