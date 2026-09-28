"""The Runner: one process that wires Capture, Log, Judge and Alarm together.

Capture runs on its own thread, appending frame and blind records to the Log as
fast as its source delivers them. The main thread turns a *cycle* every
``cycle_interval_s``: it appends any due fixture observations, asks the Judge for
a verdict from the current Log state, hands that verdict to the Alarm, and writes
a ``CycleCompleted`` row -- so the pipeline's own liveness is on the record for
the Watchdog to judge (K7).

``--stop-stage alarm`` (test and drill use only) truncates the per-cycle stage
list before the Alarm, so Capture and the cycle loop keep running while the Alarm
stops evaluating: exactly the failure the Watchdog must catch (finding D1). A
file source ends the run when it is exhausted, after a short drain so a recovery
can still land; a live device runs until interrupted or ``--max-cycles``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from station_watch.alarm.episodes import Alarm
from station_watch.capture.capture import Capture
from station_watch.clock import utc_now_iso
from station_watch.judge import Judge
from station_watch.records import CycleCompleted
from station_watch.runner.startup import RunContext

_PIPELINE_STAGES = ("capture", "judge", "alarm")


def resolve_stages(stop_stage: str | None) -> tuple[str, ...]:
    """The stages that run each cycle, truncated before ``stop_stage`` if given."""
    if stop_stage is None:
        return _PIPELINE_STAGES
    return _PIPELINE_STAGES[: _PIPELINE_STAGES.index(stop_stage)]


class Runner:
    """Wires the pipeline and drives the cycle loop until the source is done."""

    def __init__(
        self,
        context: RunContext,
        *,
        run_id: str,
        stop_stage: str | None = None,
        observations_path: str | None = None,
        max_cycles: int | None = None,
        speed: float = 1.0,
        clock: Callable[[], str] = utc_now_iso,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = context.config
        self._source = context.source
        self._log = context.log
        self._thresholds = context.thresholds
        self._run_id = run_id
        self._stages = resolve_stages(stop_stage)
        self._observations_path = observations_path
        self._max_cycles = max_cycles
        self._speed = speed
        self._clock = clock
        self._sleep = sleep
        self._judge = Judge(self._config, run_id=run_id, clock=clock)
        self._alarm = Alarm(self._config, run_id=run_id, sinks=context.sinks, clock=clock)

    def run(self) -> None:
        """Start Capture, drive the cycle loop, and shut both down cleanly."""
        run_start_ts = self._clock()
        observations = self._load_observations(run_start_ts)
        capture = Capture(
            self._source,
            station_id=self._config.station_id,
            camera_id=self._config.camera_id,
            run_id=self._run_id,
            speed=self._speed,
        )
        thread = threading.Thread(
            target=capture.run, args=(self._log, self._thresholds), daemon=True
        )
        thread.start()
        try:
            self._cycle_loop(thread, observations)
        except KeyboardInterrupt:
            pass
        finally:
            capture.stop()
            thread.join(timeout=2.0)
            self._source.release()

    def _load_observations(self, run_start_ts: str):
        if self._observations_path is None:
            return []
        from station_watch.fixtures import load_fixture_observations

        loaded = load_fixture_observations(self._observations_path, run_start_ts, self._run_id)
        return sorted(loaded, key=lambda obs: obs.ts)

    def _cycle_loop(self, thread: threading.Thread, observations) -> None:
        obs_idx = 0
        cycle = 0
        drain = 0
        drain_target = self._config.recover_healthy_verdicts + 2
        while True:
            self._sleep(self._config.cycle_interval_s)
            now = self._clock()
            obs_idx = self._flush_observations(observations, obs_idx, now)
            cycle += 1
            self._run_cycle(now, cycle)
            if self._max_cycles is not None and cycle >= self._max_cycles:
                return
            if not thread.is_alive():
                drain += 1
                if drain >= drain_target:
                    return

    def _flush_observations(self, observations, idx: int, now: str) -> int:
        """Append every fixture observation whose rebased ts has arrived."""
        while idx < len(observations) and observations[idx].ts <= now:
            self._log.append(observations[idx])
            idx += 1
        return idx

    def _run_cycle(self, now: str, cycle: int) -> None:
        verdict = self._judge.judge(self._log, now)
        if "alarm" in self._stages:
            self._alarm.evaluate(verdict, self._log)
        self._log.append(
            CycleCompleted(ts=now, cycle=cycle, stages=self._stages, run_id=self._run_id)
        )


__all__ = ["Runner", "resolve_stages"]
