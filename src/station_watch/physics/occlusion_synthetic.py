"""The occlusion script's ``--synthetic`` proving input: hand blobs at two angles.

``--synthetic`` is test-only (the CLI help says so). It renders two short clips of
one rail position that stays seated the whole time, with a skin-toned hand blob
covering it for a *known* span of frames -- a small span at a shallow camera angle,
a larger one at a steeper angle -- and tags each clip with its ``angle_deg`` and
native ``fps`` and labels the occluded span. The occlusion script then reads each
clip back through the real Detect path and its measured occlusion-caused unknown
fraction must land within a few points of the rendered share.

Nothing here fakes the detector: the hand blob is a real skin-toned blob the
rail-position reader trips on as ``occluded`` (the same path HF2.3's
``test_detect_positions`` proves), so the only synthetic part is the drawn scene.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from station_watch.config import StationConfig, load_station_config
from station_watch.evaluate.manifest import Manifest, load_manifest, sha256_file

NATIVE_FPS = 30
CLIP_FRAMES = 100
DATASET = "physics-occlusion-synthetic"

# (angle_deg, half-open occluded frame span). A steeper angle occludes more frames.
_ANGLES = (
    (20, (30, 60)),  # 30 / 100 frames -> 0.30 occluded
    (50, (20, 80)),  # 60 / 100 frames -> 0.60 occluded
)
_RAIL = {"rail_pos_1": [[0.7, 1.9], [1.7, 1.9], [1.7, 2.5], [0.7, 2.5]]}
_STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}


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
            # persistence 2 exercises the N-frame rule; the measure drives per-frame
            # judgments by forcing emit_interval_s to 0 at run time.
            "persistence_frames": 2,
            "emit_interval_s": 10_000.0,
            "rail_positions": _RAIL,
            "station_zone": _STATION_ZONE,
            "keepout_rois": {},
            "blur_threshold": 100.0,
            "darkness_threshold": 40.0,
            "occlusion_threshold": 0.5,
        },
    }


def _script(span: tuple[int, int]) -> list[dict]:
    """One frame per index: the position is seated throughout, occluded over ``span``."""
    start, end = span
    return [
        {"positions": {"rail_pos_1": "occluded" if start <= i < end else "present"}}
        for i in range(CLIP_FRAMES)
    ]


def _render(clips_dir: Path, angle: int, span: tuple[int, int]) -> str:
    from station_watch.synth.station import write_synth_station_clip

    path, _truth = write_synth_station_clip(
        clips_dir / "physics" / f"occlusion-angle-{angle}",
        _script(span),
        rail_positions=_RAIL,
        keepout_rois={},
        station_zone=_STATION_ZONE,
        fps=float(NATIVE_FPS),
    )
    return str(Path(path).relative_to(clips_dir))


def _clip_entry(clips_dir: Path, angle: int, span: tuple[int, int]) -> dict:
    return {
        "path": _render(clips_dir, angle, span),
        "tags": {"angle_deg": angle, "fps": NATIVE_FPS},
        "occlusions": [{"target": "rail_pos_1", "start_frame": span[0], "end_frame": span[1]}],
    }


def _manifest_dict(clips_dir: Path) -> dict:
    return {
        "dataset": DATASET,
        "sessions": [
            {
                "id": f"angle-{angle}",
                "clips": [_clip_entry(clips_dir, angle, span)],
            }
            for angle, span in _ANGLES
        ],
    }


@dataclass(frozen=True)
class SyntheticInput:
    config: StationConfig
    config_sha: str
    manifest: Manifest
    backend_factory: object  # callable(clip) -> keep-out backend (unused: no keep-out zones)


def build_synthetic(work_dir: Path) -> SyntheticInput:
    """Render the two angle clips, write config+manifest, return a scored-ready input."""
    work_dir = Path(work_dir)
    clips_dir = work_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    config_path = work_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(_config_dict()))
    manifest_path = work_dir / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(_manifest_dict(clips_dir)))
    return SyntheticInput(
        config=load_station_config(config_path),
        config_sha=sha256_file(config_path),
        manifest=load_manifest(str(manifest_path), clips_dir),
        backend_factory=lambda _clip: None,
    )


__all__ = ["build_synthetic", "SyntheticInput", "DATASET", "NATIVE_FPS", "CLIP_FRAMES"]
