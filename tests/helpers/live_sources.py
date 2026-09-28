"""Fake *live* camera sources for the unplugged-camera tests.

A live device never "finishes": when its read fails (returns nothing, or raises)
the camera has been unplugged or its driver has died, and Capture must say so and
keep retrying. These fakes behave like a webcam on a timer -- about 20 frames a
second of bright, noisy, marker-free frames -- then fail in one of two ways, and
optionally come back (a replugged cable) after ``recover_after_s`` seconds.
"""

from __future__ import annotations

import threading
import time

import numpy as np


def bright_frame(rng: np.random.Generator) -> np.ndarray:
    scene = np.full((120, 120, 3), 200, np.uint8)
    noisy = scene.astype(float) + rng.normal(0, 8, scene.shape)
    return np.clip(noisy, 0, 255).astype(np.uint8)


class FailingLiveSource:
    """A live source that delivers ``frames_before`` frames, then fails.

    ``mode`` is ``"none"`` (read returns None, as ``cv2`` does for a pulled USB
    camera) or ``"raise"`` (the driver raises). With ``recover_after_s`` set, reads
    succeed again that many seconds after the first failure.
    """

    is_file = False
    fps = 0.0

    def __init__(
        self,
        frames_before: int,
        *,
        mode: str = "none",
        recover_after_s: float | None = None,
        frame_period_s: float = 0.05,
    ) -> None:
        if mode not in ("none", "raise"):
            raise ValueError(f"unknown failure mode: {mode}")
        self._frames_before = frames_before
        self._mode = mode
        self._recover_after_s = recover_after_s
        self._period = frame_period_s
        self._rng = np.random.default_rng(0)
        self._lock = threading.Lock()
        self._delivered = 0
        self.failed_at: float | None = None
        self.reopens = 0
        self.failed_reads = 0

    def read(self):
        time.sleep(self._period)  # a camera delivers on its own clock
        with self._lock:
            if self._delivered < self._frames_before or self._recovered():
                self._delivered += 1
                return bright_frame(self._rng)
            if self.failed_at is None:
                self.failed_at = time.monotonic()
            self.failed_reads += 1
        if self._mode == "raise":
            raise OSError("camera driver: device not found")
        return None

    def _recovered(self) -> bool:
        if self._recover_after_s is None or self.failed_at is None:
            return False
        return time.monotonic() - self.failed_at >= self._recover_after_s

    def reopen(self) -> None:
        with self._lock:
            self.reopens += 1

    def release(self) -> None:
        pass
