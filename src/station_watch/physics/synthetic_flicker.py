"""The flicker ``--synthetic`` proving input: a 120 Hz lamp sampled at 30 fps.

``--synthetic`` is test-only (the CLI help says so). Each frame's brightness is the
120 Hz lamp waveform *integrated over the camera exposure* (:data:`EXPOSURE_S`, chosen
as a non-multiple of the 1/120 s flicker period so the integral is not trivially the
mean) at that frame's start time. Sampled at 30 fps the 120 Hz flicker lands on the
same phase every frame, so the integrated brightness is constant frame to frame: the
flicker aliases to 0 Hz and is invisible as an oscillation. The clip stays well lit
throughout, so the real Capture's dark monitor must open no ``dark`` record on it.

The frames are a plain lit field plus Gaussian sensor noise -- no marker, because the
proving claim is about luma and darkness, not the fiducial.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import yaml

from station_watch.config import load_station_config
from station_watch.evaluate.manifest import load_manifest, sha256_file
from station_watch.physics.synthetic import SyntheticInput

FLICKER_HZ = 120.0
FPS = 30
EXPOSURE_S = 0.005  # not a multiple of 1/120 s (~0.00833 s): the integral keeps a residual
CLIP_FRAMES = 60
NOISE_SIGMA = 6.0
_BASE_LUMA = 200.0  # the fully-lit field level before the flicker factor and noise
_SIZE = (120, 160)  # (height, width)
DATASET = "physics-flicker-synthetic"


def _integrated_factor(frame_index: int) -> float:
    """The 120 Hz lamp level averaged over the exposure at frame ``frame_index``'s start."""
    t0 = frame_index / FPS
    omega = 2.0 * math.pi * FLICKER_HZ
    # (1/E) * integral over [t0, t0+E] of 0.5*(1 + cos(omega * t)) dt
    integral = 0.5 * (
        EXPOSURE_S + (math.sin(omega * (t0 + EXPOSURE_S)) - math.sin(omega * t0)) / omega
    )
    return integral / EXPOSURE_S


def _render(clips_dir: Path) -> str:
    """Render the lit, flickering clip (constant after aliasing) and return its rel path."""
    from station_watch.synth.video import _write_frames

    rng = np.random.default_rng(0)
    height, width = _SIZE
    seq = []
    for i in range(CLIP_FRAMES):
        level = _BASE_LUMA * _integrated_factor(i)
        frame = np.full((height, width, 3), level, np.float64)
        noisy = np.clip(frame + rng.normal(0.0, NOISE_SIGMA, frame.shape), 0, 255).astype(np.uint8)
        seq.append(noisy)
    out_dir = clips_dir / "flicker" / "led-120hz"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = _write_frames(out_dir, seq, float(FPS))
    return str(Path(path).relative_to(clips_dir))


def _config_dict() -> dict:
    return {
        "station_id": "physics",
        "camera_id": "cam-0",
        "takt_s": 1.0,
        "grace_s": 0.5,
        "required_slots": [],
        "keepout_zones": [],
        "liveness_window_s": 5.0,
        "dark_luma_threshold": 15.0,
        "dark_window_s": 0.3,
        "frozen_frames": 10_000,
        "recover_good_frames": 3,
        "cycle_interval_s": 0.05,
        "recover_healthy_verdicts": 2,
        "fiducial": {
            "dictionary_id": "DICT_4X4_50",
            "marker_id": 0,
            "expected_center_px": [80, 60],
            "tolerance_px": 10,
        },
        "alarm": {"sinks": ["record"]},
        "watchdog": {"cycle_window_s": 1.0, "alarm_eval_window_s": 1.0, "sinks": ["record"]},
        "detect": {
            "persistence_frames": 1,
            "emit_interval_s": 1000.0,
            "rail_positions": {},
            "station_zone": {
                "id": "bench",
                "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]],
            },
            "keepout_rois": {},
            "blur_threshold": 100.0,
            "darkness_threshold": 40.0,
            "occlusion_threshold": 0.5,
        },
    }


def build_synthetic(work_dir: Path) -> SyntheticInput:
    """Render the proving clip, write config+manifest, return a scored-ready input."""
    work_dir = Path(work_dir)
    clips_dir = work_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    rel = _render(clips_dir)
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
                                    "lamp": "led",
                                    "exposure_s": EXPOSURE_S,
                                    "fps": FPS,
                                    "flicker_hz": FLICKER_HZ,
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


__all__ = ["build_synthetic", "FLICKER_HZ", "FPS", "EXPOSURE_S", "DATASET"]
