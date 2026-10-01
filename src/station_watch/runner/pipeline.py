"""The Runner: one process that wires Capture, Log, Judge and Alarm together.

Capture runs on its own thread, appending frame and blind records to the Log as
fast as its source delivers them. The main thread turns a *cycle* every
``cycle_interval_s``: it appends any due fixture observations, asks the Judge for
a verdict from the current Log state, hands that verdict to the Alarm, and writes
a ``CycleCompleted`` row -- so the pipeline's own liveness is on the record for
the Watchdog to judge (K7).

``--stop-stage alarm`` (test and drill use only) truncates the per-cycle stage
list before the Alarm (after ``--stop-stage-after`` full cycles, default 0), so
Capture and the cycle loop keep running while the Alarm stops evaluating:
exactly the failure the Watchdog must catch (finding D1).

Only a *recorded file read cleanly to its end* ends the run, after a short drain
so a recovery can still land. A live device never ends it: an unplugged camera
keeps Capture retrying while every cycle judges the station unobservable. Capture
guards its own per-frame work, but if its thread exits anyway the cycle loop
notices on its next cycle (a liveness check every cycle), says so once on stderr,
and writes a ``disconnected`` BlindRecord naming the exit, so the Alarm fires and
stays up rather than the run carrying on quietly. A live run stops
on interrupt, :meth:`Runner.stop` (SIGTERM from the CLI), or ``--max-cycles``.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable

from station_watch.alarm.episodes import Alarm
from station_watch.capture.capture import Capture
from station_watch.clock import utc_now_iso
from station_watch.detect.detector import build_detector
from station_watch.judge import Judge
from station_watch.records import BlindReason, BlindRecord, BlindState, CycleCompleted
from station_watch.runner.startup import RunContext

_PIPELINE_STAGES = ("capture", "judge", "alarm")
CAPTURE_THREAD_NAME = "station-watch-capture"


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
        stop_stage_after: int = 0,
        observations_path: str | None = None,
        max_cycles: int | None = None,
        speed: float = 1.0,
        clock: Callable[[], str] = utc_now_iso,
    ) -> None:
        self._config = context.config
        self._source = context.source
        self._log = context.log
        self._thresholds = context.thresholds
        self._keepout_backend = context.keepout_backend
        self._run_id = run_id
        self._stages = resolve_stages(stop_stage)
        self._stop_stage_after = stop_stage_after
        self._observations_path = observations_path
        self._max_cycles = max_cycles
        self._speed = speed
        self._clock = clock
        self._stop = threading.Event()
        self._capture_error: BaseException | None = None
        self._capture_exit_reported = False
        self._stall_window_s = context.stall_window_s
        self._stall_window_source = context.stall_window_source
        self._judge = Judge(
            self._config, run_id=run_id, clock=clock, stall_window_s=context.stall_window_s
        )
        self._alarm = Alarm(self._config, run_id=run_id, sinks=context.sinks, clock=clock)

    def stop(self) -> None:
        """Ask the cycle loop to finish its current cycle and shut down cleanly."""
        self._stop.set()

    def run(self) -> None:
        """Start Capture, drive the cycle loop, and shut both down cleanly."""
        print(
            f"stall threshold {self._stall_window_s:.1f} s ({self._stall_window_source})",
            flush=True,
        )
        run_start_ts = self._clock()
        observations = self._load_observations(run_start_ts)
        capture = Capture(
            self._source,
            station_id=self._config.station_id,
            camera_id=self._config.camera_id,
            run_id=self._run_id,
            speed=self._speed,
            clock=self._clock,
            detector=build_detector(self._config, self._run_id, self._keepout_backend),
        )
        thread = threading.Thread(
            target=self._capture_main,
            args=(capture,),
            name=CAPTURE_THREAD_NAME,
            daemon=True,
        )
        thread.start()
        # Give the source one liveness window to deliver its first frame before
        # judging, so a camera warming up is not reported as unplugged.
        capture.first_frame.wait(self._config.liveness_window_s)
        try:
            self._cycle_loop(capture, thread, observations)
        except KeyboardInterrupt:
            pass
        finally:
            capture.stop()
            thread.join(timeout=2.0)
            self._source.release()

    def _capture_main(self, capture: Capture) -> None:
        """The Capture thread body; an escaping error is kept for the exit alarm."""
        try:
            capture.run(self._log, self._thresholds)
        except Exception as exc:
            self._capture_error = exc

    def _check_capture_alive(self, capture: Capture, thread: threading.Thread, now: str) -> None:
        """Alarm once, loudly, if the Capture thread has exited without finishing a file."""
        if self._capture_exit_reported or thread.is_alive() or capture.stream_ended:
            return
        self._capture_exit_reported = True
        exc = self._capture_error
        cause = "" if exc is None else f": {type(exc).__name__}: {str(exc)[:200]}"
        detail = f"capture thread exited unexpectedly{cause}"
        print(f"station-watch: {detail}", file=sys.stderr, flush=True)
        self._log.append(
            BlindRecord(
                station_id=self._config.station_id,
                camera_id=self._config.camera_id,
                ts=now,
                reason=BlindReason.DISCONNECTED,
                evidence={"error": detail},
                last_good_frame_id=None,
                state=BlindState.OPENED,
                seq=0,  # Capture's own BlindWatch numbers from 1
                run_id=self._run_id,
            )
        )

    def _load_observations(self, run_start_ts: str):
        if self._observations_path is None:
            return []
        from station_watch.fixtures import load_fixture_observations

        loaded = load_fixture_observations(self._observations_path, run_start_ts, self._run_id)
        return sorted(loaded, key=lambda obs: obs.ts)

    def _cycle_loop(self, capture: Capture, thread: threading.Thread, observations) -> None:
        obs_idx = 0
        cycle = 0
        drain = 0
        drain_target = self._config.recover_healthy_verdicts + 2
        while not self._stop.wait(self._config.cycle_interval_s):
            now = self._clock()
            obs_idx = self._flush_observations(observations, obs_idx, now)
            cycle += 1
            self._check_capture_alive(capture, thread, now)
            self._run_cycle(now, cycle, stream_ended=capture.stream_ended)
            if self._max_cycles is not None and cycle >= self._max_cycles:
                return
            if capture.stream_ended:
                drain += 1
                if drain >= drain_target:
                    return

    def _flush_observations(self, observations, idx: int, now: str) -> int:
        """Append every fixture observation whose rebased ts has arrived."""
        while idx < len(observations) and observations[idx].ts <= now:
            self._log.append(observations[idx])
            idx += 1
        return idx

    def _run_cycle(self, now: str, cycle: int, *, stream_ended: bool) -> None:
        stages = self._stages if cycle > self._stop_stage_after else _PIPELINE_STAGES
        verdict = self._judge.judge(self._log, now, stream_ended=stream_ended)
        if "alarm" in stages:
            self._alarm.evaluate(verdict, self._log)
        self._log.append(CycleCompleted(ts=now, cycle=cycle, stages=stages, run_id=self._run_id))


__all__ = ["Runner", "resolve_stages"]
