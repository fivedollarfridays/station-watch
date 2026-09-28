"""A frame source over ``cv2.VideoCapture``: a device index or a file path.

Wraps the one OpenCV capture object behind a small surface -- ``read`` returns
the next BGR frame or ``None`` at end-of-stream (a camera drop-out or the last
frame of a recording) -- and remembers whether the source is a file, so the
:class:`~station_watch.capture.capture.Capture` loop knows to pace a recording at
its native fps while leaving a live camera to the hardware clock.
"""

from __future__ import annotations

import cv2
import numpy as np


class CaptureError(Exception):
    """Raised when a capture source cannot be opened."""


class FrameSource:
    """A readable stream of BGR frames from a camera index or a file path."""

    def __init__(self, source: int | str):
        self._spec = source
        self._is_file = isinstance(source, str)
        self._cap = cv2.VideoCapture(source)
        if not self._cap.isOpened():
            raise CaptureError(f"could not open capture source: {source!r}")

    @property
    def is_file(self) -> bool:
        """True for a recorded file or image sequence, False for a live device."""
        return self._is_file

    @property
    def fps(self) -> float:
        """Native frames-per-second, or 0.0 when the source does not report one."""
        reported = self._cap.get(cv2.CAP_PROP_FPS)
        return float(reported) if reported and reported > 0 else 0.0

    def read(self) -> np.ndarray | None:
        """The next frame, or ``None`` at end-of-stream / read failure."""
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        return frame

    def release(self) -> None:
        """Release the underlying capture device or file handle."""
        self._cap.release()

    def __enter__(self) -> FrameSource:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
