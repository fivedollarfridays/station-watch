"""Resolve each cited frame id to the :class:`FrameCell` the sheet will show.

The resolver honours K4 (never a substitute frame): a cited frame is shown only as
*its own* evidence thumbnail, or -- with ``--clip`` -- the clip frame that still
hashes to the Log's fingerprint, or else the :class:`MissingEvidence` reason and no
image at all. A frame whose evidence has stored marker corners gets the configured
zones drawn on it; one without a marker is shown plain with "overlay unavailable".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from station_watch.audit.html import FrameCell
from station_watch.audit.overlay import draw_overlay
from station_watch.detect.geometry import find_marker_corners
from station_watch.evidence_image import downscale, encode_jpeg
from station_watch.evidence_index import EvidenceFrame, EvidenceIndex, frame_from_clip

# Bound for a clip-sourced thumbnail (evidence thumbnails are already downscaled).
_CLIP_THUMB_WIDTH = 320


@dataclass(frozen=True)
class ThumbContext:
    """Everything the resolver needs that is the same for every frame of a build."""

    index: EvidenceIndex
    regions: list[tuple[str, list]]
    clip: Path | None
    fingerprints: dict[int, str]
    dictionary_id: str
    marker_id: int


def resolve_cell(frame_id: int, ctx: ThumbContext) -> FrameCell:
    """The cited frame as a :class:`FrameCell`: its own thumbnail, a clip frame, or a reason."""
    found = ctx.index.lookup(frame_id)
    if isinstance(found, EvidenceFrame):
        image = cv2.imread(str(found.path))
        if image is None:
            return FrameCell(frame_id, found.ts, None, False, "unreadable")
        return _draw_cell(frame_id, found.ts, image, found.corners, ctx.regions)
    cell = _from_clip(frame_id, ctx)
    if cell is not None:
        return cell
    return FrameCell(frame_id, None, None, False, found.reason)


def _from_clip(frame_id: int, ctx: ThumbContext) -> FrameCell | None:
    """The clip's own frame, downscaled, only if it still hashes to the Log fingerprint."""
    fingerprint = ctx.fingerprints.get(frame_id)
    if ctx.clip is None or fingerprint is None:
        return None
    frame = frame_from_clip(ctx.clip, frame_id, fingerprint)
    if not isinstance(frame, np.ndarray):
        return None
    thumb, _scale = downscale(frame, _CLIP_THUMB_WIDTH)
    corners = find_marker_corners(thumb, ctx.dictionary_id, ctx.marker_id)
    corner_list = None if corners is None else corners.tolist()
    return _draw_cell(frame_id, None, thumb, corner_list, ctx.regions)


def _draw_cell(frame_id, ts, image, corners, regions) -> FrameCell:
    """Encode ``image`` for the sheet, drawing the zones when a marker was found."""
    if corners is None:
        return FrameCell(frame_id, ts, encode_jpeg(image), False, None)
    drawn = draw_overlay(image, corners, regions)
    return FrameCell(frame_id, ts, encode_jpeg(drawn), True, None)


__all__ = ["ThumbContext", "resolve_cell"]
