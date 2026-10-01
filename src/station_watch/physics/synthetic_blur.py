"""The exposure_blur ``--synthetic`` proving input: HF2.1 frames, a known blur kernel.

``--synthetic`` is test-only (the CLI help says so). It renders a short clip with the
HF2.1 station renderer, then blurs every frame with a horizontal box kernel of a known
length (:data:`KERNEL_LEN` pixels) -- a stand-in for the smear a moving target leaves
at a given exposure -- so the blur measurement can be checked against a length it was
given. The clip is tagged with a camera exposure, a target speed and the marker's
physical size, the same per-clip tags a real exposure sweep carries.

Everything downstream is the real Detect path: the measurement finds the marker with
ArUco and reads the rail positions through HF2.2's ``PositionTracker``.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import yaml

from station_watch.config import load_station_config
from station_watch.evaluate.manifest import load_manifest, sha256_file
from station_watch.physics.synthetic import SyntheticInput

KERNEL_LEN = 9  # the horizontal motion-blur length, in pixels, applied to every frame
NATIVE_FPS = 30
CLIP_FRAMES = 6
EXPOSURE_S = 0.02
SPEED_M_S = 0.32
FIDUCIAL_MM = 40.0
DATASET = "physics-exposure-blur-synthetic"

_STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}
_RAIL_POSITIONS = {
    "rail_pos_1": [[0.7, 1.9], [1.7, 1.9], [1.7, 2.5], [0.7, 2.5]],
    "rail_pos_2": [[2.5, 1.9], [3.5, 1.9], [3.5, 2.5], [2.5, 2.5]],
}


def _hblur(frame: np.ndarray) -> np.ndarray:
    """A horizontal box blur of length :data:`KERNEL_LEN` -- a left-to-right motion smear."""
    kernel = np.zeros((1, KERNEL_LEN), np.float64)
    kernel[0, :] = 1.0 / KERNEL_LEN
    return cv2.filter2D(frame, -1, kernel, borderType=cv2.BORDER_REPLICATE)


def _read_frames(clip_path) -> list[np.ndarray]:
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


def _render_and_blur(clips_dir: Path) -> str:
    """Render a sharp HF2.1 clip, blur every frame by a known kernel, write the blurred clip."""
    from station_watch.synth.station import write_synth_station_clip
    from station_watch.synth.video import _write_frames

    script = [{"positions": {"rail_pos_1": "present"}} for _ in range(CLIP_FRAMES)]
    sharp_path, _truth = write_synth_station_clip(
        clips_dir / "exposure" / "sharp", script, fps=float(NATIVE_FPS), seed=1
    )
    blurred = [_hblur(frame) for frame in _read_frames(sharp_path)]
    blurred_dir = clips_dir / "exposure" / "blurred"
    blurred_dir.mkdir(parents=True, exist_ok=True)
    blurred_path = _write_frames(blurred_dir, blurred, float(NATIVE_FPS))
    return str(Path(blurred_path).relative_to(clips_dir))


def _config_dict() -> dict:
    return {
        "station_id": "physics",
        "camera_id": "cam-0",
        "takt_s": 1.0,
        "grace_s": 0.5,
        "required_slots": [],
        "keepout_zones": [],
        "liveness_window_s": 2.0,
        "dark_luma_threshold": 15.0,
        "dark_window_s": 0.3,
        "frozen_frames": 10_000,
        "recover_good_frames": 3,
        "cycle_interval_s": 0.05,
        "recover_healthy_verdicts": 2,
        "fiducial": {
            "dictionary_id": "DICT_4X4_50",
            "marker_id": 0,
            "expected_center_px": [58, 58],
            "tolerance_px": 10,
        },
        "alarm": {"sinks": ["record"]},
        "watchdog": {"cycle_window_s": 1.0, "alarm_eval_window_s": 1.0, "sinks": ["record"]},
        "detect": {
            "persistence_frames": 1,
            "emit_interval_s": 1000.0,
            "rail_positions": _RAIL_POSITIONS,
            "station_zone": _STATION_ZONE,
            "keepout_rois": {},
            "blur_threshold": 100.0,
            "darkness_threshold": 40.0,
            "occlusion_threshold": 0.5,
        },
    }


def build_synthetic(work_dir: Path) -> SyntheticInput:
    """Render + blur the proving clip, write config+manifest, return a scored-ready input."""
    work_dir = Path(work_dir)
    clips_dir = work_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    rel = _render_and_blur(clips_dir)
    config_path = work_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(_config_dict()))
    manifest_path = work_dir / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "dataset": DATASET,
                "sessions": [
                    {
                        "id": "synthetic",
                        "clips": [
                            {
                                "path": rel,
                                "tags": {
                                    "exposure_s": EXPOSURE_S,
                                    "speed_m_s": SPEED_M_S,
                                    "fiducial_mm": FIDUCIAL_MM,
                                    "fps": NATIVE_FPS,
                                    "blur_kernel_px": KERNEL_LEN,
                                },
                            }
                        ],
                    }
                ],
            }
        )
    )
    return SyntheticInput(
        config=load_station_config(config_path),
        config_sha=sha256_file(config_path),
        manifest=load_manifest(str(manifest_path), clips_dir),
        backend_factory=lambda _clip: None,
    )


__all__ = ["build_synthetic", "KERNEL_LEN", "DATASET"]
