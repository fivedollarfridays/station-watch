"""The naive frame-difference baseline (PLAN 3.5), scored on the same clips.

A baseline is what the real detector must beat. This one knows nothing about rail
positions, keep-out zones or cycle-time slope -- it only watches whole-frame
motion. It flags ``stalled`` when the clip holds a long enough run of near-still
frames (frame-to-frame difference below a simple threshold), and nothing else: it
reads no rail-position state (every labeled interval is a miss) and never catches a
missing part, a keep-out entry or a camera fault. Written beside the real numbers,
it makes the detector's advantage legible and honest.
"""

from __future__ import annotations

import cv2
import numpy as np

from station_watch.evaluate.extract import ClipOutcome, ground_truth_flags
from station_watch.steps import resolve_stall_window

_DEFAULT_FPS = 10.0


def _frame_diffs(clip_path) -> tuple[list[float], float]:
    cap = cv2.VideoCapture(str(clip_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or _DEFAULT_FPS
    diffs: list[float] = []
    prev = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.int16)
        if prev is not None:
            diffs.append(float(np.abs(gray - prev).mean()))
        prev = gray
    cap.release()
    return diffs, (fps if fps > 0 else _DEFAULT_FPS)


def _longest_still_run(diffs: list[float]) -> int:
    """Longest run of frames whose motion is below half the clip's mean motion."""
    if not diffs:
        return 0
    cutoff = (sum(diffs) / len(diffs)) * 0.5
    longest = run = 0
    for diff in diffs:
        run = run + 1 if diff < cutoff else 0
        longest = max(longest, run)
    return longest


def baseline_outcome(label, config, clip_path) -> ClipOutcome:
    """Score one clip with the frame-difference baseline into a :class:`ClipOutcome`."""
    diffs, fps = _frame_diffs(clip_path)
    stall_window_s = resolve_stall_window(config)[0]
    window_frames = max(1, int(round(stall_window_s * fps)))
    detected = _longest_still_run(diffs) >= window_frames
    pred_flags = {"stalled"} if detected else set()
    latencies = [stall_window_s] if ("stalled" in ground_truth_flags(label) and detected) else []
    return ClipOutcome(
        session=label.session,
        gt_flags=ground_truth_flags(label),
        pred_flags=pred_flags,
        rail_intervals=[(_norm(p["state"]), "none") for p in label.positions],
        latencies=latencies,
        time_to_alarm=[
            {
                "reason": f["reason"],
                "clip": label.rel_path,
                "start_frame": f["start_frame"],
                "seconds": None,
            }
            for f in label.camera_faults
        ],
    )


def _norm(state: str) -> str:
    return {"not_seated": "absent", "occluded": "unknown", "seated": "present"}.get(state, state)


__all__ = ["baseline_outcome"]
