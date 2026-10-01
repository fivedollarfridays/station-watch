"""A fault schedule on the frame-index clock: windows span frames, not wall time.

The synthetic drill wraps a :class:`SyntheticSource` in a :class:`FaultSource` whose
schedule runs on the source's :class:`FrameClock`. These tests prove a fault window
spans exactly its share of frames even when the real clock stalls in the middle of
it (the CI flake: a stall used to squeeze a fault under its detection window), that
every fault -- frozen and cable_pulled included -- still advances simulated time,
and that the schedule's origin is frame 0, not the moment the source was built.
"""

from __future__ import annotations

import pytest

from station_watch.capture.metrics import mean_luma
from station_watch.faults import FaultSource, FaultWindow
from station_watch.synth.clock import FrameClock
from station_watch.synth.source import SyntheticSource

FPS = 20.0
PERIOD = 1.0 / FPS
_DARK = 15.0


class _Real:
    def __init__(self) -> None:
        self.t = 50.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _clocked(schedule, real: _Real) -> tuple[FaultSource, FrameClock]:
    clock = FrameClock(FPS, monotonic=real, sleep=real.sleep)
    source = SyntheticSource(fps=FPS, clock=clock)
    faults = FaultSource(
        source, schedule, dark_luma_threshold=_DARK, tolerance_px=10.0, now=clock.upcoming
    )
    return faults, clock


def test_synthetic_source_reads_advance_its_frame_clock_one_slot_each():
    real = _Real()
    clock = FrameClock(FPS, monotonic=real, sleep=real.sleep)
    source = SyntheticSource(fps=FPS, clock=clock)
    assert source.clock is clock
    source.read()
    source.read()
    assert clock.now() == pytest.approx(PERIOD)


def test_a_fault_window_spans_its_frames_even_across_a_wall_clock_stall():
    real = _Real()
    faults, _clock = _clocked([FaultWindow("lens_covered", 0.5, 1.0)], real)
    dark = []
    for index in range(30):
        if index == 12:
            real.t += 5.0  # a five-second stall in the middle of the window
        frame = faults.read()
        dark.append(mean_luma(frame) < _DARK)
    assert dark.index(True) == 10, "the window opens on frame 10 (0.5 s at 20 fps)"
    assert sum(dark) == 10, "a 0.5 s window spans exactly ten frames, stall or not"


def test_schedule_origin_is_frame_zero_not_source_construction():
    real = _Real()
    faults, _clock = _clocked([FaultWindow("lens_covered", 0.0, 0.1)], real)
    real.t += 30.0  # the runner takes a long time to start capturing
    assert mean_luma(faults.read()) < _DARK, "frame 0 is still inside the window"


def test_frozen_frames_still_advance_simulated_time():
    real = _Real()
    faults, clock = _clocked([FaultWindow("frozen", 0.1, 0.5)], real)
    for _ in range(12):
        faults.read()
    assert clock.now() == pytest.approx(11 * PERIOD)


def test_cable_pulled_drops_its_slots_and_frames_resume_after_the_clear():
    real = _Real()
    faults, clock = _clocked([FaultWindow("cable_pulled", 0.1, 0.5)], real)
    faults.read()
    faults.read()  # frames 0 and 1
    assert faults.read() is None  # slot 2 is lost
    real.t += 0.6  # Capture backs off in real time
    frame = faults.read()
    assert frame is not None, "the cable cleared at 0.5 s of simulated time"
    assert clock.now() >= 0.5
