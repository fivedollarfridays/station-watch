"""The fps sweep's ``--synthetic`` proving input: a keep-out reach of known duration.

``--synthetic`` is test-only (the CLI help says so). It renders one short clip with a
person reaching into a keep-out zone for a hand-counted frame span, tagged with its
native capture ``fps`` and labeled with that keep-out interval, so the sweep can
subsample it and check the measured frames-in-event against the predicted ``f * d``.

The one piece faked is the person detector: the real YOLOX backend never fires on a
synthetic blob, so :class:`PixelPersonBackend` reports a person box whenever the
rendered skin blob is present in the frame (subsampling-invariant -- it reads the
frame, not a call counter). Everything else is the real Detect path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from station_watch.config import StationConfig, load_station_config
from station_watch.detect.geometry import region_to_pixels
from station_watch.evaluate.manifest import Manifest, load_manifest, sha256_file

NATIVE_FPS = 30
EVENT_START = 30
EVENT_END = 90  # half-open [30, 90): a 2.0 s reach at 30 fps
CLIP_FRAMES = 120
DATASET = "physics-fps-sweep-synthetic"

_MARKER_XY = (30, 30)
_MARKER_PX = 56
_ZONE = {"region": [[4.0, 1.9], [5.3, 1.9], [5.3, 3.0], [4.0, 3.0]], "active": True}
_STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}
_SKIN_BGR = (120, 150, 200)  # the hand blob colour synth_station draws


def _corners() -> np.ndarray:
    x, y = _MARKER_XY
    s = _MARKER_PX
    return np.array([[x, y], [x + s, y], [x + s, y + s], [x, y + s]], dtype=np.float32)


def _zone_box() -> tuple[float, float, float, float, float]:
    """The keep-out zone's pixel bounding box, used as the scripted person box."""
    poly = region_to_pixels(_ZONE["region"], _corners())
    x0, y0 = poly.min(axis=0)
    x1, y1 = poly.max(axis=0)
    return (float(x0), float(y0), float(x1), float(y1), 0.9)


class PixelPersonBackend:
    """A scripted keep-out backend: a person box whenever the frame holds skin pixels."""

    def __init__(self, box: tuple) -> None:
        self._box = box

    def detect_people(self, frame):
        # The rendered hand blob is ~1200+ solid skin pixels; the noisy backplate
        # trips a few hundred near-skin pixels, so the threshold sits well above that
        # floor -- a person box only when the blob is actually in frame.
        skin = np.all(np.abs(frame.astype(int) - np.array(_SKIN_BGR)) < 40, axis=2)
        return [self._box] if int(skin.sum()) > 700 else []


def _config_dict() -> dict:
    return {
        "station_id": "physics",
        "camera_id": "cam-0",
        "takt_s": 1.0,
        "grace_s": 0.5,
        "required_slots": [],
        "keepout_zones": ["zone_press"],
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
            "rail_positions": {},
            "station_zone": _STATION_ZONE,
            "keepout_rois": {"zone_press": _ZONE},
            "keepout": {"min_overlap": 0.1, "persistence_frames": 1},
            "blur_threshold": 100.0,
            "darkness_threshold": 40.0,
            "occlusion_threshold": 0.5,
        },
    }


def _render(clips_dir: Path) -> str:
    from station_watch.synth.station import write_synth_station_clip

    script = [
        {"keepout": {"zone_press": True}} if EVENT_START <= i < EVENT_END else {}
        for i in range(CLIP_FRAMES)
    ]
    path, _truth = write_synth_station_clip(
        clips_dir / "physics" / "fps-reach",
        script,
        keepout_rois={"zone_press": _ZONE},
        station_zone=_STATION_ZONE,
        fps=float(NATIVE_FPS),
    )
    return str(Path(path).relative_to(clips_dir))


@dataclass(frozen=True)
class SyntheticInput:
    config: StationConfig
    config_sha: str
    manifest: Manifest
    backend_factory: object  # callable(clip) -> keep-out backend


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
                                "tags": {"fps": NATIVE_FPS},
                                "keepouts": [
                                    {
                                        "zone": "zone_press",
                                        "start_frame": EVENT_START,
                                        "end_frame": EVENT_END,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )
    )
    box = _zone_box()
    return SyntheticInput(
        config=load_station_config(config_path),
        config_sha=sha256_file(config_path),
        manifest=load_manifest(str(manifest_path), clips_dir),
        backend_factory=lambda _clip: PixelPersonBackend(box),
    )


__all__ = ["build_synthetic", "SyntheticInput", "PixelPersonBackend", "DATASET"]
