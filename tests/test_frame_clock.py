"""The synthetic drill's frame-index clock (:class:`station_watch.synth.clock.FrameClock`).

A synthetic drill is synthetic by definition, so its time is the frame index: frame
``k`` is at ``k / fps``. These tests drive the clock with a hand-advanced *real* clock
and prove that a wall-clock stall (a loaded CI runner, a descheduled process) neither
advances simulated time nor squeezes frames afterwards -- the property that keeps a
fault window from collapsing under its detection window. Only a pulled cable (no frame
arrives at all) lets simulated time follow the real clock, because Capture's backoff
between retries is real time the pipeline really spends.
"""

from __future__ import annotations

import math

import pytest

from station_watch.clock import parse_iso
from station_watch.synth.clock import FrameClock

FPS = 20.0
PERIOD = 1.0 / FPS


class _Real:
    """A hand-advanced stand-in for ``time.monotonic``; ``sleep`` advances it."""

    def __init__(self) -> None:
        self.t = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def _clock(real: _Real) -> FrameClock:
    return FrameClock(FPS, monotonic=real, sleep=real.sleep)


def test_frame_k_is_stamped_at_k_over_fps():
    real = _Real()
    clock = _clock(real)
    stamps = []
    for _ in range(4):
        clock.tick()
        stamps.append(clock.now())
    assert stamps == pytest.approx([0.0, PERIOD, 2 * PERIOD, 3 * PERIOD])


def test_ticks_are_paced_to_fps_on_the_real_clock():
    real = _Real()
    clock = _clock(real)
    clock.tick()  # frame 0: no wait
    clock.tick()  # frame 1: the real clock did not move on its own, so wait one period
    assert real.sleeps and math.isclose(real.sleeps[-1], PERIOD, rel_tol=1e-9)


def test_a_wall_clock_stall_neither_advances_sim_time_nor_squeezes_later_frames():
    real = _Real()
    clock = _clock(real)
    clock.tick()
    clock.tick()
    real.t += 3.0  # the process is descheduled for three seconds
    assert clock.now() == pytest.approx(PERIOD), "a stall is invisible to simulated time"
    clock.tick()
    assert clock.now() == pytest.approx(2 * PERIOD), "the next frame is the next slot"
    # After the stall there is no burst of catch-up frames that would squeeze a
    # fault window's frames into a few milliseconds: never faster than 2x fps.
    real.sleeps.clear()
    clock.tick()
    assert real.sleeps and real.sleeps[-1] >= PERIOD / 2 - 1e-9


def test_lag_behind_the_wall_clock_heals_at_up_to_twice_fps():
    # Simulated time is stamped into the Log, and the Board judges staleness on the
    # wall clock, so a stall's lag must not persist: frames run at up to 2x fps
    # (never a burst) until simulated time is back on the real clock's schedule.
    real = _Real()
    clock = _clock(real)
    start = real.t
    clock.tick()
    real.t += 1.0  # a one-second stall
    for _ in range(60):
        clock.tick()
    lag = (real.t - start) - clock.now()
    assert lag == pytest.approx(0.0, abs=PERIOD), "the stall's lag has healed"
    real.sleeps.clear()
    clock.tick()
    assert math.isclose(real.sleeps[-1], PERIOD, rel_tol=1e-9), "back to plain fps"


def test_upcoming_is_the_time_of_the_next_frame_slot():
    real = _Real()
    clock = _clock(real)
    assert clock.upcoming() == 0.0
    clock.tick()
    assert clock.upcoming() == pytest.approx(PERIOD)


def test_a_dropped_slot_lets_sim_time_follow_the_real_clock_until_frames_resume():
    real = _Real()
    clock = _clock(real)
    clock.tick()  # frame 0 at 0.0
    clock.drop()  # the cable is pulled: slot 1 passes with no frame
    assert clock.now() == pytest.approx(PERIOD)
    real.t += 0.75  # Capture backs off for real time between retries
    assert clock.now() == pytest.approx(PERIOD + 0.75)
    assert clock.upcoming() == pytest.approx(PERIOD + 0.75)
    clock.tick()  # frames resume on the first slot at or after that moment
    assert clock.now() == pytest.approx(0.8)
    real.t += 5.0  # a stall after frames resume is invisible again
    clock.tick()
    assert clock.now() == pytest.approx(0.85)


def test_iso_is_the_base_timestamp_plus_sim_time_and_mono_is_sim_time():
    real = _Real()
    clock = _clock(real)
    base = parse_iso(clock.base_iso())
    for _ in range(3):
        clock.tick()
    assert (parse_iso(clock.iso()) - base).total_seconds() == pytest.approx(2 * PERIOD)
    assert clock.mono() == pytest.approx(2 * PERIOD)


def test_fps_must_be_positive():
    with pytest.raises(ValueError):
        FrameClock(0.0)
