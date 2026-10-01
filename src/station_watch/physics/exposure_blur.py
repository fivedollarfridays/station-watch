"""``exposure_blur``: motion blur vs exposure time, and the unknown share (PLAN 3.4).

For clips tagged with a camera ``exposure_s`` and a known target ``speed_m_s``, this
measures the motion-blur length from the *edge spread across the fiducial* -- the
high-contrast marker is the one feature always findable in frame -- and converts it
to millimetres with the marker's known physical size (``fiducial_mm``). The measured
blur is reported beside the predicted ``speed * exposure`` so the two sit side by
side, and, grouped by exposure, the share of HF2 slot judgments Detect returns as
``part_unknown`` (a long exposure that smears a part out of recognition shows up as a
rising unknown share, never as a silently wrong "present").

Blur length is the *equivalent width* of the marker's left outer edge: a sharp step
blurred by a motion kernel of length ``L`` becomes a ramp whose gradient's equivalent
width ``(sum g)^2 / sum(g^2)`` is ``L``. Averaging the edge profile over the marker's
height first beats the sensor noise down so a single clean edge remains.

It reads frames and runs HF2's ``Detector``/``PositionTracker``; it never runs the
Judge. ``--synthetic`` (test only) blurs synthetic-station frames with a known kernel.
"""

from __future__ import annotations

import cv2
import numpy as np

from station_watch.clock import offset_iso
from station_watch.detect.detector import Detector
from station_watch.detect.geometry import find_marker_corners
from station_watch.records import ObservationKind

_BASE_TS = "2026-01-01T00:00:00.000000+00:00"
_RUN_ID = "physics-exposure-blur"
_HEIGHT_PAD = 6  # rows trimmed top and bottom so marker corners never skew the band
_LEFT_MARGIN = 15  # window extent left of the edge (flat backplate)
_RIGHT_MARGIN = 6  # window extent right of the edge (into the black border)


class ExposureBlur:
    """Measure motion-blur length against exposure, with the Detect unknown share."""

    name = "exposure_blur"

    def selects(self, clip) -> bool:
        """An exposure_blur input carries a camera exposure and a known target speed."""
        return "exposure_s" in clip.tags and "speed_m_s" in clip.tags

    def build_synthetic(self, work_dir):
        """The ``--synthetic`` proving input: synthetic station frames blurred by a known kernel."""
        from station_watch.physics.synthetic_blur import build_synthetic

        return build_synthetic(work_dir)

    def measure(self, clips, config, backend_factory) -> dict:
        """Measure every clip; return the metrics block grouped by exposure."""
        results = [_clip_result(clip, config) for clip in clips]
        return _assemble(results)


def _read_frames(clip_path) -> list:
    """Every frame of the clip, in order (frame id is the list index)."""
    from station_watch.capture.source import FrameSource

    source = FrameSource(str(clip_path))
    frames = []
    try:
        while True:
            frame = source.read()
            if frame is None:
                break
            frames.append(frame)
    finally:
        source.release()
    return frames


def _measure_blur_px(frame, corners) -> float:
    """Equivalent width of the marker's left outer edge: the motion-blur length in px."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float64)
    xs, ys = corners[:, 0], corners[:, 1]
    left = int(round(xs.min()))
    top, bottom = int(round(ys.min())), int(round(ys.max()))
    band = gray[top + _HEIGHT_PAD : bottom - _HEIGHT_PAD, :].mean(axis=0)
    grad = np.abs(np.diff(band))
    seg = grad[max(left - _LEFT_MARGIN, 0) : left + _RIGHT_MARGIN]
    total = float(seg.sum())
    if total <= 0:
        return 0.0
    return float(total * total / np.sum(seg * seg))


def _marker_side_px(corners) -> float:
    """The marker's drawn side length in pixels (top edge), for the mm conversion."""
    top_left, top_right = corners[0], corners[1]
    return float(np.hypot(*(top_right - top_left)))


def _clip_blur(frames, fiducial) -> tuple[float | None, float | None]:
    """Mean blur length and marker side (px) over the frames the marker is found in."""
    blur_vals, side_vals = [], []
    for frame in frames:
        corners = find_marker_corners(frame, fiducial["dictionary_id"], fiducial["marker_id"])
        if corners is None:
            continue
        blur_vals.append(_measure_blur_px(frame, corners))
        side_vals.append(_marker_side_px(corners))
    if not blur_vals:
        return None, None
    return float(np.mean(blur_vals)), float(np.mean(side_vals))


def _unknown_share(frames, config, native) -> tuple[float, dict]:
    """Run Detect over the frames; the share of slot judgments that read ``part_unknown``."""
    detector = Detector(config, _RUN_ID)
    counts = {"present": 0, "absent": 0, "unknown": 0}
    kinds = {
        ObservationKind.PART_PRESENT: "present",
        ObservationKind.PART_ABSENT: "absent",
        ObservationKind.PART_UNKNOWN: "unknown",
    }
    for i, frame in enumerate(frames):
        ts = offset_iso(_BASE_TS, i / native)
        for obs in detector.process(frame, i, ts):
            if obs.kind in kinds:
                counts[kinds[obs.kind]] += 1
    total = sum(counts.values())
    return (counts["unknown"] / total if total else 0.0), counts


def _clip_result(clip, config) -> dict:
    """One clip: measured blur (px and mm) beside the predicted smear, and the unknown share."""
    exposure_s = float(clip.tags["exposure_s"])
    speed_m_s = float(clip.tags["speed_m_s"])
    native = float(clip.tags.get("fps", 30.0))
    frames = _read_frames(clip.clip_path)
    blur_px, side_px = _clip_blur(frames, config.fiducial)
    share, counts = _unknown_share(frames, config, native)
    mm_per_px = float(clip.tags["fiducial_mm"]) / side_px if side_px else None
    return {
        "clip": clip.rel_path,
        "exposure_s": exposure_s,
        "speed_m_s": speed_m_s,
        "measured_blur_px": round(blur_px, 3) if blur_px is not None else None,
        "measured_blur_mm": round(blur_px * mm_per_px, 3) if blur_px and mm_per_px else None,
        "predicted_blur_mm": round(speed_m_s * exposure_s * 1000.0, 3),
        "unknown_share": round(share, 3),
        "judgments": counts,
    }


def _assemble(results: list[dict]) -> dict:
    """Aggregate the per-clip results, with the unknown share rolled up by exposure."""
    by_exposure: dict[str, dict] = {}
    for result in results:
        key = str(result["exposure_s"])
        bucket = by_exposure.setdefault(key, {"present": 0, "absent": 0, "unknown": 0})
        for kind, count in result["judgments"].items():
            bucket[kind] += count
    unknown_share_by_exposure = {
        key: round(bucket["unknown"] / sum(bucket.values()), 3) if sum(bucket.values()) else 0.0
        for key, bucket in by_exposure.items()
    }
    return {
        "clips_total": len(results),
        "clips": results,
        "unknown_share_by_exposure": unknown_share_by_exposure,
    }


__all__ = ["ExposureBlur"]
