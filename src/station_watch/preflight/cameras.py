"""``preflight --list-cameras``: find the device index the operator should use.

OpenCV's device-index order is not stable across machines, so there is no way to
know in advance which index is the webcam. This probes indexes ``0..max_index``,
and for each one that opens prints the index, the reported resolution and fps, and
whether a frame actually read; an index that will not open is simply skipped (never
a failure), because most indexes on any given machine are empty.

The capture factory is injectable so the probe is testable without real hardware:
the default opens a real :class:`~station_watch.capture.source.FrameSource`, which
raises :class:`~station_watch.capture.source.CaptureError` on an index that does not
open.
"""

from __future__ import annotations

from dataclasses import dataclass

from station_watch.capture.source import CaptureError, FrameSource

# How many device indexes to try by default (0..MAX_INDEX_DEFAULT inclusive).
MAX_INDEX_DEFAULT = 5


@dataclass(frozen=True)
class CameraReport:
    """What one openable device index reported: resolution, fps, and a frame read."""

    index: int
    width: int
    height: int
    fps: float
    frame_read: bool


def probe_cameras(max_index: int, *, source_factory=None) -> list[CameraReport]:
    """Try indexes ``0..max_index``; report each that opens, skip each that does not.

    ``source_factory`` defaults to :class:`FrameSource`, looked up at call time so a
    test can swap the module's factory for a fake.
    """
    if source_factory is None:
        source_factory = FrameSource
    reports: list[CameraReport] = []
    for index in range(max_index + 1):
        try:
            source = source_factory(index)
        except CaptureError:
            continue  # an index that does not open is expected, not a failure
        try:
            width, height = source.resolution
            fps = source.fps
            frame_read = source.read() is not None
        finally:
            source.release()
        reports.append(CameraReport(index, int(width), int(height), float(fps), frame_read))
    return reports


def render_camera_list(reports: list[CameraReport]) -> str:
    """One line per openable index, or a line saying none opened."""
    if not reports:
        return "no cameras opened in the probed index range"
    lines = ["index  resolution   fps     frame"]
    for report in reports:
        lines.append(
            f"{report.index:<5}  {report.width}x{report.height:<6}  "
            f"{report.fps:<6.1f}  {'read' if report.frame_read else 'no frame'}"
        )
    return "\n".join(lines)


__all__ = ["CameraReport", "probe_cameras", "render_camera_list", "MAX_INDEX_DEFAULT"]
