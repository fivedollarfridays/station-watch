"""Component 5: the Alarm -- verdicts to episodes, on screen and sound.

The Alarm turns the Judge's ranked Verdicts into *episodes*: one open incident
per root cause. A fault opens an episode keyed by ``(kind, target)``; an
unobservable verdict opens one episode per active blind reason -- a blind camera
raises one episode for its station naming the reason, not one per tick. Repeated
identical verdicts keep an episode open; they never open a duplicate.

Recovery follows K11 (verify a fix only by re-observing): an open episode closes
only after ``recover_healthy_verdicts`` consecutive healthy verdicts, and closing
emits exactly one recovery message. Any non-healthy verdict resets that streak,
so a still-faulted or still-blind station never "recovers" on a clock alone.

The Alarm reads only the Verdict -- its state, faults and blind reasons -- and
never any explanation output (K5): the deterministic alarm fires first and cannot
be delayed or suppressed by a downstream explainer (there is none in HF1). After
every evaluation it appends an ``alarm_evaluated`` row (ts, open episodes) to the
Log so the Watchdog can judge the alarm rail's own liveness.
"""

from __future__ import annotations

from dataclasses import dataclass

from station_watch.alarm.sink import AlarmError
from station_watch.clock import utc_now_iso
from station_watch.records import AlarmEvaluated, VerdictState


@dataclass(frozen=True)
class Episode:
    """One open incident, keyed by its root ``cause``."""

    cause: str
    kind: str  # "fault" or "unobservable"
    station_id: str
    label: str  # human-readable cause, e.g. "stalled zone_press" or "frozen"
    started_ts: str
    frame_ids: tuple[int, ...]


def _fault_episode(fault, station_id: str, ts: str) -> Episode:
    return Episode(
        cause=f"{fault.kind.value}:{fault.target}",
        kind="fault",
        station_id=station_id,
        label=f"{fault.kind.value} {fault.target}",
        started_ts=ts,
        frame_ids=tuple(fault.frame_ids),
    )


def _blind_episode(reason, station_id: str, ts: str) -> Episode:
    return Episode(
        cause=f"unobservable:{reason.value}",
        kind="unobservable",
        station_id=station_id,
        label=f"unobservable ({reason.value})",
        started_ts=ts,
        frame_ids=(),
    )


class Alarm:
    """Opens and closes episodes from verdicts and announces them to the sinks."""

    def __init__(self, config, *, run_id: str, sinks, clock=utc_now_iso) -> None:
        if not sinks:
            raise AlarmError("refusing to start: no alarm sink configured (alarm.sinks is empty)")
        self._config = config
        self._run_id = run_id
        self._sinks = list(sinks)
        self._clock = clock
        self._recover_after = config.recover_healthy_verdicts
        self._open: dict[str, Episode] = {}
        self._healthy_streak = 0
        self._seq = 0

    def evaluate(self, verdict, log) -> AlarmEvaluated:
        """Open episodes for new causes, recover healed ones, log the evaluation."""
        for episode in self._episodes_for(verdict):
            if episode.cause not in self._open:
                self._open[episode.cause] = episode
                for sink in self._sinks:
                    sink.on_alarm(episode)
        self._advance_recovery(verdict)
        return self._record(verdict.ts, log)

    def _episodes_for(self, verdict) -> list[Episode]:
        station = verdict.station_id
        if verdict.state == VerdictState.FAULT:
            return [_fault_episode(f, station, verdict.ts) for f in verdict.faults]
        if verdict.state == VerdictState.UNOBSERVABLE:
            if verdict.blind_reasons:
                return [_blind_episode(r, station, verdict.ts) for r in verdict.blind_reasons]
            return [
                Episode(
                    "unobservable:unknown", "unobservable", station, "unobservable", verdict.ts, ()
                )
            ]
        return []

    def _advance_recovery(self, verdict) -> None:
        if verdict.state == VerdictState.HEALTHY:
            self._healthy_streak += 1
        else:
            self._healthy_streak = 0
        if self._open and self._healthy_streak >= self._recover_after:
            for episode in list(self._open.values()):
                for sink in self._sinks:
                    sink.on_recovery(episode, verdict.ts)
            self._open.clear()

    def _record(self, ts: str, log) -> AlarmEvaluated:
        self._seq += 1
        event = AlarmEvaluated(
            ts=ts,
            seq=self._seq,
            open_episodes=tuple(sorted(self._open)),
            run_id=self._run_id,
        )
        log.append(event)
        return event


__all__ = ["Alarm", "Episode"]
