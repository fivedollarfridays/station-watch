"""Draw the configured rail/station/keep-out regions onto an evidence thumbnail.

The regions are the *same* fiducial-anchored, marker-unit quads Detect reads
(``detect.rail_positions``, ``detect.station_zone``, ``detect.keepout_rois``), and
they are mapped into the thumbnail through the *same*
:func:`~station_watch.detect.geometry.region_to_pixels` geometry Detect uses --
here with the marker corners already scaled into the thumbnail's pixel space
(``EvidenceFrame.corners``). Corners of ``None`` mean no marker was found in the
frame, so nothing is drawn and the caller shows "overlay unavailable".
"""

from __future__ import annotations

import cv2
import numpy as np

from station_watch.detect.geometry import region_to_pixels

# Distinct BGR outline colours per region kind, drawn with a 1px polyline.
_COLORS = {
    "rail": (0, 255, 0),  # green
    "station": (0, 255, 255),  # yellow
    "keepout": (0, 0, 255),  # red
}


def collect_regions(config) -> list[tuple[str, list]]:
    """``(kind, marker-unit region)`` for every rail, the station zone and each keep-out."""
    detect = config.detect
    regions: list[tuple[str, list]] = []
    for region in detect["rail_positions"].values():
        regions.append(("rail", region))
    regions.append(("station", detect["station_zone"]["region"]))
    for roi in detect["keepout_rois"].values():
        regions.append(("keepout", roi["region"]))
    return regions


def draw_overlay(image: np.ndarray, corners, regions: list[tuple[str, list]]) -> np.ndarray:
    """A copy of ``image`` with each region's outline drawn through ``corners``.

    ``corners`` are the marker's four corners in *this image's* pixel space (the
    thumbnail), so :func:`region_to_pixels` lands each region exactly where Detect
    sampled it. Returns the drawn copy; the original is left untouched.
    """
    out = image.copy()
    corner_px = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    for kind, region in regions:
        poly = region_to_pixels(region, corner_px)
        if poly is None:
            continue
        cv2.polylines(out, [poly.reshape(-1, 1, 2)], True, _COLORS[kind], 1)
    return out


__all__ = ["collect_regions", "draw_overlay"]
