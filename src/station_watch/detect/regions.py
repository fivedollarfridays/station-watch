"""Per-region pixel reading: occupancy, seating, and the unknown causes.

A rail position is a marker-unit region (see :mod:`station_watch.detect.geometry`).
:func:`read_region` maps it to pixels through the current marker corners and reads
one of four states with *no time thresholds* -- persistence lives in
:mod:`station_watch.detect.positions`:

* ``unknown`` -- the region can't be read: it is partly ``out_of_frame``, too
  ``dark`` (mean luma below ``darkness_threshold``), too ``blurred`` (variance of
  the Laplacian below ``blur_threshold``), or ``occluded`` (a skin-toned foreground
  blob covers more than ``occlusion_threshold`` of it).
* ``empty`` / ``occupied`` -- decided by the *color-fill* method: the fraction of
  the region whose per-pixel BGR spread (max-min) clears ``_COLOR_DIFF_MIN`` -- an
  empty DIN rail is near-gray, a seated component is a solid distinct color. Canny
  edge density is recorded alongside as a corroborating score.
* ``not_seated`` -- occupied, but the component's colored extent sits more than
  ``seat_tolerance`` marker units off the region's seat (centroid offset).

Every read also returns a ``confidence_ceiling`` in ``0..1`` lowered by blur,
dimness and partial occlusion (K3), and the raw scores it decided from (K4).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from station_watch.detect.geometry import region_to_pixels

# Decision knobs for the color-fill method. The darkness/blur/occlusion cutoffs
# are station config (they vary by camera); these are intrinsic to the method.
_COLOR_DIFF_MIN = 40  # per-pixel BGR (max-min) for a pixel to read as component color
_OCCUPANCY_THRESHOLD = 0.15  # fraction of a region that must read as component color
_DEFAULT_SEAT_TOLERANCE = 0.10  # marker units a component may sit off its seat

CAUSE_FIDUCIAL_MISSING = "fiducial_missing"
CAUSE_OUT_OF_FRAME = "out_of_frame"
CAUSE_DARK = "dark"
CAUSE_BLURRED = "blurred"
CAUSE_OCCLUDED = "occluded"


def _colorfulness(bgr: np.ndarray) -> np.ndarray:
    """Per-pixel BGR spread (max channel - min channel); ~0 for gray, large for color."""
    a = bgr.astype(np.int32)
    return a.max(axis=2) - a.min(axis=2)


def _skin_mask(bgr: np.ndarray) -> np.ndarray:
    """A boolean mask of skin-toned (foreground hand) pixels, R > G > B and reddish."""
    b, g, r = (
        bgr[..., 0].astype(np.int32),
        bgr[..., 1].astype(np.int32),
        bgr[..., 2].astype(np.int32),
    )
    return (r > 95) & (g > 40) & (b > 20) & (r > g) & (g > b) & ((r - b) > 15)


def _bbox(poly: np.ndarray) -> tuple[int, int, int, int]:
    x0, y0 = poly.min(axis=0)
    x1, y1 = poly.max(axis=0)
    return int(x0), int(y0), int(x1), int(y1)


def _ceiling(
    lap_var: float, mean_luma: float, skin_fraction: float, blur_thr: float, dark_thr: float
) -> float:
    """Confidence ceiling (0..1) lowered by blur, dimness and partial occlusion (K3)."""
    blur_term = lap_var / (lap_var + blur_thr / 2) if lap_var > 0 else 0.0
    dim_term = mean_luma / (mean_luma + dark_thr / 2) if mean_luma > 0 else 0.0
    occ_term = max(0.0, 1.0 - skin_fraction)
    return round(min(blur_term, dim_term, occ_term), 4)


@dataclass(frozen=True)
class RegionQuality:
    """The shared unknown-cause read of a pixel region (no method-specific logic).

    ``cause`` is the unknown reason (``out_of_frame`` / ``dark`` / ``blurred`` /
    ``occluded``) or ``None`` when the region is readable; ``crop`` is the BGR region
    (``None`` only when ``out_of_frame``); ``ceiling`` is the K3 confidence ceiling and
    ``scores`` the K4 raw quality scores. Both :func:`read_region` and the detail
    readers (:mod:`station_watch.detect.details`) decide ``unknown`` from this one
    helper, so the four gates are never duplicated.
    """

    cause: str | None
    crop: np.ndarray | None
    ceiling: float
    scores: dict


def read_region_quality(frame: np.ndarray, poly: np.ndarray | None, detect: dict) -> RegionQuality:
    """Read the four unknown-cause gates for the pixel region ``poly`` (shared helper).

    Maps nothing -- ``poly`` is already pixels. A ``None`` poly (no marker) or a bbox
    that leaves the frame reads ``out_of_frame``; otherwise luma/Laplacian/skin decide
    ``dark`` / ``blurred`` / ``occluded`` against the station's configured thresholds.
    """
    if poly is None:
        return RegionQuality(CAUSE_OUT_OF_FRAME, None, 0.0, {})
    x0, y0, x1, y1 = _bbox(poly)
    frame_h, frame_w = frame.shape[:2]
    if x0 < 0 or y0 < 0 or x1 > frame_w or y1 > frame_h:
        return RegionQuality(
            CAUSE_OUT_OF_FRAME, None, 0.0, {"bbox": [x0, y0, x1, y1], "frame": [frame_w, frame_h]}
        )
    reg = frame[y0:y1, x0:x1]
    gray = cv2.cvtColor(reg, cv2.COLOR_BGR2GRAY)
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    mean_luma = float(gray.mean())
    skin_fraction = float(_skin_mask(reg).mean())
    blur_thr, dark_thr, occ_thr = (
        detect["blur_threshold"],
        detect["darkness_threshold"],
        detect["occlusion_threshold"],
    )
    ceiling = _ceiling(lap_var, mean_luma, skin_fraction, blur_thr, dark_thr)
    scores = {
        "lap_var": round(lap_var, 3),
        "mean_luma": round(mean_luma, 3),
        "skin_fraction": round(skin_fraction, 4),
    }
    cause = None
    if mean_luma < dark_thr:
        cause = CAUSE_DARK
    elif lap_var < blur_thr:
        cause = CAUSE_BLURRED
    elif skin_fraction > occ_thr:
        cause = CAUSE_OCCLUDED
    return RegionQuality(cause, reg, ceiling, scores)


def _seat_offset(frame: np.ndarray, poly: np.ndarray, marker_side: float) -> float:
    """Offset of the component's colored centroid from the seat center, in marker units.

    Searches a window grown one region-height above and below (a component slips off
    its seat vertically), so a shifted block is captured whole rather than clipped.
    """
    x0, y0, x1, y1 = _bbox(poly)
    height = y1 - y0
    wy0 = max(0, y0 - height)
    wy1 = min(frame.shape[0], y1 + height)
    window = frame[wy0:wy1, x0:x1]
    ys, xs = np.nonzero(_colorfulness(window) > _COLOR_DIFF_MIN)
    if len(ys) == 0:
        return 0.0
    cx, cy = xs.mean() + x0, ys.mean() + wy0
    seat_cx, seat_cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    return float(np.hypot(cx - seat_cx, cy - seat_cy) / marker_side)


def read_region(
    frame: np.ndarray, region, corners: np.ndarray, detect: dict
) -> tuple[str, str | None, float, dict]:
    """Read one rail region (marker present). Returns ``(state, cause, ceiling, scores)``.

    ``state`` is ``occupied`` / ``empty`` / ``not_seated`` / ``unknown``; ``cause`` is
    the unknown reason (or ``None``). No time thresholds -- persistence is the caller's.
    """
    poly = region_to_pixels(region, corners)
    quality = read_region_quality(frame, poly, detect)
    scores = dict(quality.scores)
    ceiling = quality.ceiling
    if quality.cause == CAUSE_OUT_OF_FRAME:
        return "unknown", CAUSE_OUT_OF_FRAME, 0.0, scores

    reg = quality.crop
    gray = cv2.cvtColor(reg, cv2.COLOR_BGR2GRAY)
    occupied_fraction = float((_colorfulness(reg) > _COLOR_DIFF_MIN).mean())
    edge_density = float((cv2.Canny(gray, 50, 150) > 0).mean())
    scores["occupied_fraction"] = round(occupied_fraction, 4)
    scores["edge_density"] = round(edge_density, 4)

    if quality.cause is not None:
        return "unknown", quality.cause, ceiling, scores
    if occupied_fraction < _OCCUPANCY_THRESHOLD:
        return "empty", None, ceiling, scores

    tolerance = detect.get("seat_tolerance", _DEFAULT_SEAT_TOLERANCE)
    marker_side = float(np.linalg.norm(corners[1] - corners[0]))
    offset = _seat_offset(frame, poly, marker_side)
    scores["seat_offset"] = round(offset, 4)
    scores["seat_tolerance"] = tolerance
    if offset > tolerance:
        return "not_seated", None, ceiling, scores
    return "occupied", None, ceiling, scores


__all__ = [
    "read_region",
    "read_region_quality",
    "RegionQuality",
    "CAUSE_FIDUCIAL_MISSING",
    "CAUSE_OUT_OF_FRAME",
    "CAUSE_DARK",
    "CAUSE_BLURRED",
    "CAUSE_OCCLUDED",
]
