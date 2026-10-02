"""Render a synthetic panel-bench clip (seen from above) plus its ground truth.

This builds on the HF1 :mod:`station_watch.synth.video` writer (lossless FFV1
``.mkv`` or a PNG sequence into a temp dir, with Gaussian sensor noise) and
renders the scene Detect reads: a backplate, a horizontal DIN rail with
components at rail positions (distinct colors), slotted wire-duct strips, taped
station-zone and keep-out-zone outlines, and a fixed DICT_4X4_50 id-0 ArUco
marker on the backplate.

Positions and zones are placed through the *same* fiducial-anchored geometry a
detector reads them with (:func:`station_watch.detect.geometry.region_to_pixels`),
so a component is drawn exactly where the matching config region will be sampled.

``write_synth_station_clip(dir_path, script, **render_opts)`` returns the capture
path and the per-frame ground truth it rendered. ``script`` is a list of per-frame
specs; each spec is a dict with any of:

* ``positions``: ``{position_id: "present"|"absent"|"not_seated"|"occluded"}``
* ``keepout``: ``{zone_id: True}`` -- a skin-toned hand blob inside that zone
* ``motion``: a moving tool/hand blob in the station zone
* ``dim`` / ``blur`` -- dim lighting / defocus for that frame
* ``hide_marker`` -- the marker is not drawn (the view loses its canary)

Each ground-truth entry is
``{"frame_id", "positions", "motion", "keepout", "marker_visible", "dim", "blur"}``.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from station_watch.detect.details import DEFAULT_DETAIL_REGIONS
from station_watch.detect.geometry import region_to_pixels
from station_watch.synth.video import _marker_tile, _write_frames

# Default bench layout, in MARKER UNITS relative to the fiducial's center/axes.
_MARKER_XY = (30, 30)
_MARKER_PX = 56
_SIZE = (320, 480)  # (height, width)
_RAIL_POSITIONS = {
    "rail_pos_1": [[0.7, 1.9], [1.7, 1.9], [1.7, 2.5], [0.7, 2.5]],
    "rail_pos_2": [[2.5, 1.9], [3.5, 1.9], [3.5, 2.5], [2.5, 2.5]],
}
_STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}
_KEEPOUT_ROIS = {
    "zone_press": {"region": [[4.0, 1.9], [5.3, 1.9], [5.3, 3.0], [4.0, 3.0]], "active": True}
}
_COMPONENT_COLORS = {"rail_pos_1": (200, 120, 60), "rail_pos_2": (60, 180, 90)}
_DEFAULT_COLOR = (170, 170, 170)
# Paint-pen colours drawn for each detail kind (BGR); torque_stripe is a vivid yellow
# whose hue (~30) sits in the reader's default torque_stripe hue_range.
_DETAIL_COLORS = {"torque_stripe": (0, 255, 255)}
_SKIN = (120, 150, 200)
_TOOL = (40, 40, 40)
_SHIFT_PX = 12
_HAND_RADIUS = 20


def _marker_corners(marker_xy, marker_px) -> np.ndarray:
    """Analytic ArUco corners of the axis-aligned marker drawn at ``marker_xy``."""
    x, y = marker_xy
    s = marker_px
    return np.array([[x, y], [x + s, y], [x + s, y + s], [x, y + s]], dtype=np.float32)


def _backplate(size) -> np.ndarray:
    """The static backplate shared by every frame."""
    height, width = size
    frame = np.full((height, width, 3), 70, np.uint8)
    cv2.rectangle(frame, (4, 4), (width - 4, height - 4), (110, 110, 110), 2)
    return frame


def _draw_bench(frame, station_poly, keepout_polys, rail_y) -> None:
    """DIN rail, slotted wire-duct strips, and taped zone outlines."""
    width = frame.shape[1]
    cv2.rectangle(frame, (0, rail_y - 8), (width, rail_y + 8), (150, 150, 150), -1)
    for dy in (-42, 42):
        band = rail_y + dy
        cv2.rectangle(frame, (0, band - 6), (width, band + 6), (90, 90, 110), -1)
        for x in range(0, width, 16):
            cv2.line(frame, (x, band - 6), (x, band + 6), (60, 60, 80), 1)
    cv2.polylines(frame, [station_poly.reshape(-1, 1, 2)], True, (0, 220, 220), 2)
    for poly in keepout_polys.values():
        cv2.polylines(frame, [poly.reshape(-1, 1, 2)], True, (0, 0, 230), 2)


def _draw_marker(frame, marker_xy, marker_px, tile) -> None:
    x, y = marker_xy
    frame[y : y + marker_px, x : x + marker_px] = tile


def _draw_component(frame, poly, color, state) -> None:
    """A filled component block in ``poly``; ``not_seated`` is shifted off its seat."""
    x0, y0 = poly.min(axis=0)
    x1, y1 = poly.max(axis=0)
    dy = _SHIFT_PX if state == "not_seated" else 0
    cv2.rectangle(frame, (int(x0), int(y0 + dy)), (int(x1), int(y1 + dy)), color, -1)


def _draw_detail(frame, poly, color) -> None:
    """A filled paint-pen detail (e.g. a torque stripe) drawn on the component."""
    x0, y0 = poly.min(axis=0)
    x1, y1 = poly.max(axis=0)
    cv2.rectangle(frame, (int(x0), int(y0)), (int(x1), int(y1)), color, -1)


def _draw_blob(frame, poly, color) -> None:
    """A skin-toned hand blob centered on ``poly`` (occludes a position or a zone)."""
    cx, cy = poly.mean(axis=0)
    cv2.circle(frame, (int(cx), int(cy)), _HAND_RADIUS, color, -1)


def _draw_motion(frame, station_poly, frame_id) -> None:
    """A tool blob whose position tracks ``frame_id`` so motion frames differ."""
    x0, y0 = station_poly.min(axis=0)
    x1, y1 = station_poly.max(axis=0)
    span = max(int(x1 - x0) - 30, 1)
    tx = int(x0) + (frame_id * 17) % span
    ty = int((y0 + y1) // 2)
    cv2.rectangle(frame, (tx, ty - 6), (tx + 24, ty + 6), _TOOL, -1)


def _finish(frame, *, dim, blur, rng, sigma) -> np.ndarray:
    """Apply dim lighting, Gaussian sensor noise, and optional defocus blur."""
    out = frame.astype(np.float64)
    if dim:
        out *= 0.25
    out = np.clip(out + rng.normal(0.0, sigma, out.shape), 0, 255).astype(np.uint8)
    if blur:
        out = cv2.GaussianBlur(out, (9, 9), 0)
    return out


def _truth_details(spec) -> dict:
    """The details drawn this frame: ``{position_id: {kind: "present"|"absent"}}``."""
    return {
        pid: {kind: state for kind, state in kinds.items()}
        for pid, kinds in spec.get("details", {}).items()
    }


def _ground_truth(frame_id, spec, rail_ids, zone_ids, marker_visible) -> dict:
    spec_pos = spec.get("positions", {})
    spec_keepout = spec.get("keepout", {})
    return {
        "frame_id": frame_id,
        "positions": {rid: spec_pos.get(rid, "absent") for rid in rail_ids},
        "details": _truth_details(spec),
        "motion": bool(spec.get("motion", False)),
        "keepout": {zid: bool(spec_keepout.get(zid, False)) for zid in zone_ids},
        "marker_visible": marker_visible,
        "dim": bool(spec.get("dim", False)),
        "blur": bool(spec.get("blur", False)),
    }


def _render_frame(spec, frame_id, ctx) -> np.ndarray:
    """One frame: base scene, marker, components, hand/motion blobs, then sensor FX."""
    frame = _backplate(ctx["size"])
    _draw_bench(frame, ctx["station_poly"], ctx["keepout_polys"], ctx["rail_y"])
    if not spec.get("hide_marker", False):
        _draw_marker(frame, ctx["marker_xy"], ctx["marker_px"], ctx["tile"])
    spec_pos = spec.get("positions", {})
    for rid in ctx["rail_ids"]:
        state = spec_pos.get(rid, "absent")
        if state in ("present", "not_seated", "occluded"):
            _draw_component(
                frame, ctx["rail_polys"][rid], ctx["colors"].get(rid, _DEFAULT_COLOR), state
            )
        if state == "occluded":
            _draw_blob(frame, ctx["rail_polys"][rid], _SKIN)
    for pid, kinds in spec.get("details", {}).items():
        for kind, detail_state in kinds.items():
            if detail_state == "present":
                _draw_detail(frame, ctx["detail_polys"][pid][kind], _DETAIL_COLORS[kind])
    for zid in ctx["zone_ids"]:
        if spec.get("keepout", {}).get(zid):
            _draw_blob(frame, ctx["keepout_polys"][zid], _SKIN)
    if spec.get("motion", False):
        _draw_motion(frame, ctx["station_poly"], frame_id)
    return _finish(
        frame,
        dim=spec.get("dim", False),
        blur=spec.get("blur", False),
        rng=ctx["rng"],
        sigma=ctx["sigma"],
    )


def _build_context(render_opts) -> dict:
    """Resolve layout, polygons, and render state shared by every frame."""
    marker_xy = render_opts.get("marker_xy", _MARKER_XY)
    marker_px = render_opts.get("marker_px", _MARKER_PX)
    rail_positions = render_opts.get("rail_positions", _RAIL_POSITIONS)
    station_zone = render_opts.get("station_zone", _STATION_ZONE)
    keepout_rois = render_opts.get("keepout_rois", _KEEPOUT_ROIS)
    corners = _marker_corners(marker_xy, marker_px)
    rail_polys = {rid: region_to_pixels(region, corners) for rid, region in rail_positions.items()}
    detail_regions = render_opts.get("detail_regions", DEFAULT_DETAIL_REGIONS)
    detail_polys = {
        pid: {kind: region_to_pixels(region, corners) for kind, region in kinds.items()}
        for pid, kinds in detail_regions.items()
    }
    keepout_polys = {
        zid: region_to_pixels(roi["region"], corners) for zid, roi in keepout_rois.items()
    }
    rail_centers = [poly.mean(axis=0)[1] for poly in rail_polys.values()]
    rail_y = int(np.mean(rail_centers)) if rail_centers else _SIZE[0] // 2
    return {
        "size": render_opts.get("size", _SIZE),
        "marker_xy": marker_xy,
        "marker_px": marker_px,
        "tile": _marker_tile(marker_px),
        "rail_ids": list(rail_positions),
        "zone_ids": list(keepout_rois),
        "rail_polys": rail_polys,
        "detail_polys": detail_polys,
        "station_poly": region_to_pixels(station_zone["region"], corners),
        "keepout_polys": keepout_polys,
        "rail_y": rail_y,
        "colors": render_opts.get("components", _COMPONENT_COLORS),
        "rng": np.random.default_rng(render_opts.get("seed", 0)),
        "sigma": render_opts.get("noise_sigma", 6.0),
    }


def write_synth_station_clip(
    dir_path: Path | str, script: list[dict], **render_opts
) -> tuple[Path, list[dict]]:
    """Render ``script`` into a clip under ``dir_path``; return (capture path, ground truth).

    The returned path is what a real ``cv2.VideoCapture`` opens (an ``.mkv`` file
    or a PNG-sequence pattern). Pass ``dir_path`` under a test temp dir so nothing
    is written outside it. ``render_opts`` overrides: ``size`` (h, w), ``fps``,
    ``noise_sigma``, ``seed``, ``marker_xy``, ``marker_px``, ``rail_positions``,
    ``station_zone``, ``keepout_rois``, ``components`` (id -> BGR color).
    """
    dir_path = Path(dir_path)
    dir_path.mkdir(parents=True, exist_ok=True)
    ctx = _build_context(render_opts)
    seq: list[np.ndarray] = []
    truth: list[dict] = []
    for frame_id, spec in enumerate(script):
        seq.append(_render_frame(spec, frame_id, ctx))
        marker_visible = not spec.get("hide_marker", False)
        truth.append(
            _ground_truth(frame_id, spec, ctx["rail_ids"], ctx["zone_ids"], marker_visible)
        )
    path = _write_frames(dir_path, seq, render_opts.get("fps", 10.0))
    return path, truth
