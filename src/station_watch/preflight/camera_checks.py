"""The camera-facing preflight checks: camera, frames_live, fiducial, camera_stability.

``camera`` opens the configured source (naming it on failure, K9); ``frames_live``
reads several frames within ``liveness_window_s`` and insists they have distinct
fingerprints and nonzero inter-frame noise (K2: a frozen or dead sensor repeats
identical bytes); ``fiducial`` finds the marker once per sampled frame through
HF3.1's corners and measures its center against ``expected_center_px``. The two
later checks never open the camera themselves -- if ``camera`` failed they report
that reason, so a camera-dependent check never passes on a camera that never opened.
``camera_stability`` (HF3.13) samples the fiducial center for ``drift_s`` seconds and
WARNs on drift past half ``tolerance_px``; ``camera.gimbal: true`` is always a WARN.
"""

from __future__ import annotations

import time

from station_watch.capture.metrics import fingerprint, noise_score
from station_watch.detect.geometry import find_marker_corners, marker_center
from station_watch.preflight.context import PreflightContext
from station_watch.preflight.result import FAIL, PASS, WARN, CheckResult
from station_watch.runner.startup import StartupError, open_source

# "Several" live frames: enough to tell a noisy live sensor from a frozen one.
_LIVE_FRAME_TARGET = 3

# A gimbal is never a hard fail (the station can run), but it is always a WARN: a
# gimbal drifts and raises view_shifted, so it must be locked before going live.
_GIMBAL_WARNING = "a gimbal can drift and raise view_shifted; lock it before going live"


def check_camera(ctx: PreflightContext) -> CheckResult:
    """The source opens; a failure names it and blocks the camera-dependent checks."""
    try:
        ctx.frame_source = open_source(ctx.source)
    except StartupError as exc:
        ctx.camera_error = str(exc)
        return CheckResult("camera", FAIL, str(exc))
    return CheckResult("camera", PASS, f"source opened: {ctx.source}")


def _read_live_frames(source, window_s: float, target: int) -> list:
    """Read up to ``target`` frames off ``source`` within ``window_s`` wall-clock seconds."""
    frames: list = []
    deadline = time.monotonic() + window_s
    while len(frames) < target and time.monotonic() < deadline:
        frame = source.read()
        if frame is None:
            break
        frames.append(frame)
    return frames


def check_frames_live(ctx: PreflightContext) -> CheckResult:
    """Within ``liveness_window_s``, several frames arrive with distinct fingerprints (K2)."""
    if ctx.camera_error is not None:
        return CheckResult("frames_live", FAIL, f"camera did not open: {ctx.camera_error}")
    if ctx.station_config is None:
        return CheckResult("frames_live", FAIL, "config did not load")
    window = float(ctx.station_config.liveness_window_s)
    frames = _read_live_frames(ctx.frame_source, window, _LIVE_FRAME_TARGET)
    ctx.frames = frames
    if len(frames) < _LIVE_FRAME_TARGET:
        return CheckResult(
            "frames_live",
            FAIL,
            f"only {len(frames)} frame(s) within {window}s (need {_LIVE_FRAME_TARGET})",
        )
    prints = {fingerprint(frame) for frame in frames}
    noises = [noise_score(frames[i], frames[i - 1]) for i in range(1, len(frames))]
    if len(prints) < len(frames) or min(noises) <= 0.0:
        return CheckResult(
            "frames_live",
            FAIL,
            f"not live: {len(prints)}/{len(frames)} distinct, min noise {min(noises):.3f} "
            "(frozen or dead sensor?)",
        )
    return CheckResult(
        "frames_live",
        PASS,
        f"{len(frames)} distinct frames, noise {min(noises):.2f}-{max(noises):.2f} in {window}s",
    )


def check_fiducial(ctx: PreflightContext) -> CheckResult:
    """The marker is found once per sampled frame and within ``tolerance_px`` of its center."""
    if ctx.station_config is None:
        return CheckResult("fiducial", FAIL, "config did not load")
    if ctx.camera_error is not None:
        return CheckResult("fiducial", FAIL, f"camera did not open: {ctx.camera_error}")
    if not ctx.frames:
        return CheckResult("fiducial", FAIL, "no live frames to search for the marker")
    fid = ctx.station_config.fiducial
    dictionary_id, marker_id = fid["dictionary_id"], int(fid["marker_id"])
    expected = fid["expected_center_px"]
    tolerance = float(fid["tolerance_px"])
    offsets = []
    for frame in ctx.frames:
        center = marker_center(find_marker_corners(frame, dictionary_id, marker_id))
        if center is None:
            return CheckResult("fiducial", FAIL, f"marker {marker_id} not found in every frame")
        offsets.append(((center[0] - expected[0]) ** 2 + (center[1] - expected[1]) ** 2) ** 0.5)
    worst = max(offsets)
    if worst > tolerance:
        return CheckResult(
            "fiducial", FAIL, f"marker off by {worst:.1f}px (tolerance {tolerance}px)"
        )
    return CheckResult(
        "fiducial",
        PASS,
        f"marker within {worst:.1f}px of {list(expected)} (tolerance {tolerance}px)",
    )


def _sample_marker_centers(source, window_s: float, dictionary_id: str, marker_id: int) -> list:
    """Marker centers read off ``source`` within ``window_s`` wall-clock seconds.

    A file source exhausts (EOS) long before the window on a fast disk; a live
    camera samples for the full window. Frames with no marker are skipped rather
    than aborting -- a brief miss is not a drift reading.
    """
    centers: list = []
    deadline = time.monotonic() + window_s
    while time.monotonic() < deadline:
        frame = source.read()
        if frame is None:
            break
        center = marker_center(find_marker_corners(frame, dictionary_id, marker_id))
        if center is not None:
            centers.append(center)
    return centers


def _max_drift(centers: list) -> float:
    """The largest distance any sampled center sits from the first one, in pixels."""
    first = centers[0]
    return max(
        ((c[0] - first[0]) ** 2 + (c[1] - first[1]) ** 2) ** 0.5 for c in centers
    )


def _measure_drift(ctx: PreflightContext) -> tuple[float | None, float, str]:
    """Sample the fiducial for ``drift_s``: ``(drift or None, limit, describing text)``."""
    fid = ctx.station_config.fiducial
    tolerance = float(fid["tolerance_px"])
    limit = tolerance / 2.0
    centers = _sample_marker_centers(
        ctx.frame_source, ctx.drift_s, fid["dictionary_id"], int(fid["marker_id"])
    )
    if len(centers) < 2:
        return None, limit, (
            f"only {len(centers)} frame(s) with the marker in {ctx.drift_s}s; "
            "cannot measure drift"
        )
    drift = _max_drift(centers)
    if drift > limit:
        return drift, limit, (
            f"fiducial center drifted {drift:.1f}px over {ctx.drift_s}s "
            f"(limit {limit:.1f}px = half tolerance {tolerance}px)"
        )
    return drift, limit, (
        f"fiducial center steady within {drift:.1f}px over {ctx.drift_s}s (limit {limit:.1f}px)"
    )


def check_camera_stability(ctx: PreflightContext) -> CheckResult:
    """A gimbal always WARNs; any camera WARNs when the fiducial drifts past half tolerance.

    The drift is sampled for every camera, gimbal or not, so a gimbal WARN also carries
    the measured drift beside the standing lock-it reminder.
    """
    gimbal = bool(((ctx.raw_config or {}).get("camera") or {}).get("gimbal", False))
    if ctx.station_config is None or ctx.camera_error is not None:
        # Could not check: a FAIL naming why (K10), never softened to a WARN by the
        # gimbal reminder, which rides along in the detail.
        if ctx.station_config is None:
            reason = "config did not load"
        else:
            reason = f"camera did not open: {ctx.camera_error}"
        detail = f"{reason}; {_GIMBAL_WARNING}" if gimbal else reason
        return CheckResult("camera_stability", FAIL, detail)
    drift, limit, text = _measure_drift(ctx)
    if gimbal:
        return CheckResult("camera_stability", WARN, f"{_GIMBAL_WARNING}; {text}")
    if drift is None or drift > limit:
        return CheckResult("camera_stability", WARN, text)
    return CheckResult("camera_stability", PASS, text)


__all__ = ["check_camera", "check_frames_live", "check_fiducial", "check_camera_stability"]
