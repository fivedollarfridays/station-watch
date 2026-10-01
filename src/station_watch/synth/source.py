"""A live-paced synthetic frame source with the ``FrameSource`` surface.

:class:`SyntheticSource` renders fresh, noisy station frames from the *same*
station renderer the proving clips use (:mod:`station_watch.synth.station`), one
frame per :meth:`read`, paced to a set ``fps`` on the wall clock -- so a bench-free
drill can drive the real Capture loop against synthetic frames exactly as it would
a live camera. No second renderer is introduced: every frame comes from
:func:`station_watch.synth.station._render_frame`, whose per-frame Gaussian noise
makes consecutive frames differ (distinct fingerprints, non-zero noise) while the
scene itself holds still.

The surface matches :class:`station_watch.capture.source.FrameSource`:
``read()`` / ``reopen()`` / ``release()`` and the ``fps`` property (plus
``is_file`` for the Capture loop). ``monotonic`` and ``sleep`` are injectable so
tests can pace deterministically without real waits.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np

from station_watch.synth.station import _build_context, _render_frame

# A healthy station frame: both default rail positions filled, marker in view.
_DEFAULT_SPEC = {"positions": {"rail_pos_1": "present", "rail_pos_2": "present"}}


class SyntheticSource:
    """A readable stream of freshly rendered, noisy synthetic station frames."""

    is_file = False

    def __init__(
        self,
        *,
        fps: float = 20.0,
        spec: dict | None = None,
        render_opts: dict | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if fps <= 0:
            raise ValueError(f"SyntheticSource fps must be > 0, got {fps!r}")
        self._fps = float(fps)
        self._period = 1.0 / self._fps
        self._spec = _DEFAULT_SPEC if spec is None else spec
        self._ctx = _build_context(render_opts or {})
        self._monotonic = monotonic
        self._sleep = sleep
        self._next_due: float | None = None
        self._frame_id = 0

    @property
    def fps(self) -> float:
        """The pacing rate, frames per second (always reported, unlike a device)."""
        return self._fps

    def read(self) -> np.ndarray | None:
        """Pace to ``fps`` on the wall clock, then render and return the next frame."""
        now = self._monotonic()
        if self._next_due is None:
            self._next_due = now
        wait = self._next_due - now
        if wait > 0:
            self._sleep(wait)
        self._next_due += self._period
        frame = _render_frame(self._spec, self._frame_id, self._ctx)
        self._frame_id += 1
        return frame

    def reopen(self) -> None:
        """A synthetic source has no device to reopen; pacing simply resumes."""
        self._next_due = None

    def release(self) -> None:
        """Nothing to release; present for FrameSource parity."""
        return None

    def __enter__(self) -> SyntheticSource:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
