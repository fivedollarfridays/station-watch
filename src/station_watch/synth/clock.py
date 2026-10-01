"""A frame-index clock for the synthetic drill: frame ``k`` is at ``k / fps``.

A synthetic drill is synthetic by definition, so its time is the frame index rather
than the wall clock. :class:`FrameClock` paces frame slots to ``fps`` on the real
clock (so a Board watching the drill sees it unfold live) but *stamps* everything in
simulated time: frame ``k`` is at ``k / fps``, and between frames simulated time
holds still. A wall-clock stall -- a loaded CI runner, a descheduled process --
therefore neither advances simulated time nor squeezes the frames after it: a fault
scheduled for twenty frames spans twenty frames, and the pipeline (stamping its Log
rows, liveness and verdicts on this clock) never sees a stall as a disconnect.

The one exception is a slot where no frame arrives at all (:meth:`FrameClock.drop`,
a pulled cable): simulated time then follows the real clock until frames resume,
because Capture's backoff between retries is real time the pipeline really spends,
and the time to recover from a pulled cable must include it.

Simulated time is what the Log is stamped with, and a Board watching the drill judges
staleness on the wall clock, so the lag a stall leaves behind is healed rather than
kept: frames run at up to :data:`CATCH_UP_SPEED` times ``fps`` until simulated time
is back on the real clock's schedule -- never a burst that would squeeze a fault's
frames into a few milliseconds.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from station_watch.clock import parse_iso, to_iso, utc_now_iso

CATCH_UP_SPEED = 2.0


class FrameClock:
    """Simulated drill time on the frame index, paced to ``fps`` on the real clock."""

    def __init__(
        self,
        fps: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall: Callable[[], str] = utc_now_iso,
    ) -> None:
        if fps <= 0:
            raise ValueError(f"FrameClock fps must be > 0, got {fps!r}")
        self._period = 1.0 / float(fps)
        self._real = monotonic
        self._sleep = sleep
        self._wall = wall
        self._lock = threading.Lock()
        self._base: datetime | None = None
        self._slot = 0  # index of the next frame slot
        self._t = 0.0  # simulated seconds of the current instant
        self._origin: float | None = None  # real time slot 0 was (or is reckoned) due
        self._last: float | None = None  # real time of the previous tick
        self._free_since: float | None = None  # real time a dropped slot began

    def tick(self) -> None:
        """Start the next frame slot: pace to it on the real clock, then stamp it."""
        with self._lock:
            self._resume_from_drop()
            due = self._due_locked()
        if due is not None:
            wait = due - self._real()
            if wait > 0:
                self._sleep(wait)
        with self._lock:
            real_now = self._real()
            if self._origin is None:
                self._origin = real_now - self._slot * self._period
            self._last = real_now
            self._t = self._slot * self._period
            self._slot += 1

    def drop(self) -> None:
        """A slot passes with no frame (a pulled cable): follow the real clock from here."""
        with self._lock:
            if self._free_since is not None:
                return
            self._t = self._slot * self._period
            self._slot += 1
            self._free_since = self._real()

    def now(self) -> float:
        """Simulated seconds since frame 0 (the time of the current frame)."""
        with self._lock:
            return self._now_locked()

    def upcoming(self) -> float:
        """Simulated seconds of the next frame slot -- when a fault decision applies."""
        with self._lock:
            if self._free_since is not None:
                return self._now_locked()
            return self._slot * self._period

    def mono(self) -> float:
        """A monotonic stand-in for Capture and liveness: simulated seconds."""
        return self.now()

    def base_iso(self) -> str:
        """The wall timestamp simulated time 0 is anchored to (fixed on first use)."""
        with self._lock:
            return to_iso(self._base_dt())

    def iso(self) -> str:
        """The current simulated instant as an ISO 8601 UTC timestamp."""
        with self._lock:
            return to_iso(self._base_dt() + timedelta(seconds=self._now_locked()))

    # -- internals (called with the lock held) ----------------------------------

    def _base_dt(self) -> datetime:
        if self._base is None:
            self._base = parse_iso(self._wall())
        return self._base

    def _due_locked(self) -> float | None:
        """Real time the next slot is due: on schedule, but never faster than catch-up."""
        if self._origin is None:
            return None
        due = self._origin + self._slot * self._period
        if self._last is not None:
            due = max(due, self._last + self._period / CATCH_UP_SPEED)
        return due

    def _now_locked(self) -> float:
        if self._free_since is None:
            return self._t
        return self._t + (self._real() - self._free_since)

    def _resume_from_drop(self) -> None:
        """Leave a dropped stretch at the first slot at or after the real-clock instant."""
        if self._free_since is None:
            return
        now = self._now_locked()
        self._slot = max(self._slot, math.ceil(now / self._period - 1e-9))
        self._free_since = None


__all__ = ["FrameClock", "CATCH_UP_SPEED"]
