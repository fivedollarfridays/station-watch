"""Component 8: the Watchdog -- a second clock on its own rail (K7, K8).

The Watchdog runs as its own process (``station-watch watchdog --config ...
--log ...``). On its own timer it reads the Log's newest ``CycleCompleted`` and
``AlarmEvaluated`` rows and judges their age as *its own wall clock now minus the
row's canonical ``ts``* -- both processes share one machine, so wall time is
comparable across them; monotonic time is not and is never compared across
processes. A rail whose newest row is older than its window (``cycle_window_s`` /
``alarm_eval_window_s``), or that has no row at all, is silent -- and silence is
an alarm that names the stale stage, never a pass (K1). A missing or unreadable
Log is itself an alarm.

Startup grace: a rail with *no rows yet* is given one of its own windows,
measured on the Watchdog's clock from its first check, before its absence counts
as silence -- a Watchdog started alongside the run should not alarm on a pipeline
that has not turned its first cycle. A stale row is never graced, and neither is
a missing Log.

The Watchdog alarms through its *own* sink instances, built from
``watchdog.sinks`` -- never through the pipeline process's rail (K8), so a stuck
pipeline cannot also silence the alarm about it. This is the fix for finding D1:
stop the Alarm while Capture keeps running, and this second clock still fires.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from station_watch.alarm.episodes import Episode
from station_watch.alarm.sink import AlarmError, build_sinks
from station_watch.clock import parse_iso, utc_now_iso
from station_watch.log import Log, LogError
from station_watch.runner.startup import StartupError, load_config

# Each rail: the stage the message names, the Log kind it reads, and the config
# key holding its staleness window.
_RAILS = (
    ("cycle", "cycle", "cycle_window_s"),
    ("alarm", "alarm_eval", "alarm_eval_window_s"),
)


class WatchdogError(RuntimeError):
    """The Watchdog cannot start (e.g. no sink of its own to alarm through)."""


class WatchdogLogError(RuntimeError):
    """The Log is missing or unreadable -- itself an alarm, not a pass."""


def age_seconds(row_ts: str, now: str) -> float:
    """Seconds between a row's canonical ts and the Watchdog's own wall clock."""
    return (parse_iso(now) - parse_iso(row_ts)).total_seconds()


def _stage_episode(station_id: str, stage: str, now: str, detail: str) -> Episode:
    return Episode(
        cause=f"watchdog:{stage}",
        kind="watchdog",
        station_id=station_id,
        label=f"{stage} stage silent ({detail})",
        started_ts=now,
        frame_ids=(),
    )


def _log_episode(station_id: str, now: str, detail: str) -> Episode:
    return Episode(
        cause="watchdog:log",
        kind="watchdog",
        station_id=station_id,
        label=f"log missing or unreadable ({detail})",
        started_ts=now,
        frame_ids=(),
    )


class Watchdog:
    """Judges the pipeline's own liveness on its own clock and its own sinks."""

    def __init__(self, config, *, sinks, log_path, clock=utc_now_iso, sleep=time.sleep) -> None:
        if not sinks:
            raise WatchdogError(
                "refusing to start: no watchdog sink configured (watchdog.sinks is empty)"
            )
        self._station_id = config.station_id
        self._windows = {stage: float(config.watchdog[key]) for stage, _kind, key in _RAILS}
        self._log_path = Path(log_path)
        self._sinks = list(sinks)
        self._clock = clock
        self._sleep = sleep
        self._open: dict[str, Episode] = {}
        self._started: str | None = None

    def check(self) -> dict[str, Episode]:
        """One judgement pass: open new silences, recover healed rails."""
        now = self._clock()
        if self._started is None:
            self._started = now
        self._reconcile(self._evaluate(now), now)
        return dict(self._open)

    def run(self, *, interval_s: float, max_checks: int | None = None) -> None:
        """Check, then sleep one tick, until interrupted or ``max_checks`` reached."""
        checks = 0
        while True:
            self.check()
            checks += 1
            if max_checks is not None and checks >= max_checks:
                return
            self._sleep(interval_s)

    def _evaluate(self, now: str) -> dict[str, Episode]:
        try:
            rows = self._newest_rows()
        except WatchdogLogError as exc:
            return {"watchdog:log": _log_episode(self._station_id, now, str(exc))}
        stale: dict[str, Episode] = {}
        for stage, kind, _key in _RAILS:
            episode = self._stale_episode(stage, rows[kind], now)
            if episode is not None:
                stale[episode.cause] = episode
        return stale

    def _newest_rows(self) -> dict:
        if not self._log_path.exists():
            raise WatchdogLogError(f"missing: {self._log_path}")
        try:
            with Log(self._log_path) as log:
                return {kind: log.newest(kind) for _stage, kind, _key in _RAILS}
        except (LogError, sqlite3.Error, OSError) as exc:
            raise WatchdogLogError(f"unreadable: {self._log_path}: {exc}") from exc

    def _stale_episode(self, stage: str, row, now: str) -> Episode | None:
        window = self._windows[stage]
        if row is None:
            waited = age_seconds(self._started or now, now)
            if waited <= window:
                return None  # startup grace: the run has not written this rail yet
            detail = f"no rows in {waited:.1f}s since watchdog start > {window:.1f}s window"
            return _stage_episode(self._station_id, stage, now, detail)
        age = age_seconds(row.ts, now)
        if age > window:
            detail = f"last row {age:.1f}s old > {window:.1f}s window"
            return _stage_episode(self._station_id, stage, now, detail)
        return None

    def _reconcile(self, stale: dict[str, Episode], now: str) -> None:
        for cause, episode in stale.items():
            if cause not in self._open:
                self._open[cause] = episode
                for sink in self._sinks:
                    sink.on_alarm(episode)
        for cause in [c for c in self._open if c not in stale]:
            episode = self._open.pop(cause)
            for sink in self._sinks:
                sink.on_recovery(episode, now)


def build_watchdog(
    config_path: str,
    log_path: str,
    *,
    record_path: str | None = None,
    clock=utc_now_iso,
    sleep=time.sleep,
) -> Watchdog:
    """Load the config and build the Watchdog with its own sinks (fail loud, K9)."""
    config = load_config(config_path)
    try:
        sinks = build_sinks(config.watchdog, record_path=record_path)
    except AlarmError as exc:
        raise StartupError(str(exc)) from exc
    return Watchdog(config, sinks=sinks, log_path=log_path, clock=clock, sleep=sleep)


__all__ = [
    "Watchdog",
    "WatchdogError",
    "WatchdogLogError",
    "age_seconds",
    "build_watchdog",
]
