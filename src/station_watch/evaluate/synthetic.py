"""Generate a labeled proving set at run time with ``synth_station`` (``--synthetic``).

``--synthetic`` renders a small, hand-countable set of clips -- a normal cycle, a
missing part, a station-zone stall, a keep-out entry, and an injected camera fault
(a lost fiducial) -- across two recording sessions, each with ground truth the
harness can score itself against. Nothing binary is written outside the given work
dir; the measurement files it produces are tagged ``dataset_kind: synthetic`` so a
proving run can never be read as real performance.

The person detector is the one piece faked here: YOLOX never fires on a synthetic
blob, so the keep-out clip injects a scripted backend (exactly as the keep-out e2e
test does) -- Capture, Detect, the Judge and the Alarm are all real. The config's
stall and fiducial windows are sized against the accelerated playback so a stall
and a lost marker both register within their short clips.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

import yaml

from station_watch.config import StationConfig, load_station_config
from station_watch.evaluate.manifest import Manifest, load_manifest
from station_watch.evaluate.synthetic_specs import (
    DATASET,
    KEEPOUT,
    RAIL,
    SPEED,
    STATION_ZONE,
    backend_for,
    clip_specs,
    config_dict,
)

_FPS = 20.0


def _render(spec, clips_dir: Path) -> str:
    from station_watch.synth.station import write_synth_station_clip

    out_dir = clips_dir / spec["session"] / spec["name"]
    path, _truth = write_synth_station_clip(
        out_dir,
        spec["script"],
        rail_positions=RAIL,
        keepout_rois=KEEPOUT,
        station_zone=STATION_ZONE,
        fps=_FPS,
    )
    return str(Path(path).relative_to(clips_dir))


def _clip_entry(spec, rel_path: str) -> dict:
    positions = [
        {"target": t, "state": s, "start_frame": 0, "end_frame": spec["last"]}
        for t, s in spec["target_states"]
    ]
    positions += spec.get("extra_positions", [])
    return {"path": rel_path, "positions": positions, **spec["labels"]}


@dataclass(frozen=True)
class SyntheticSet:
    config: StationConfig
    config_path: Path
    manifest_path: Path
    manifest: Manifest
    backends: list


def build_synthetic_set(work_dir: Path) -> SyntheticSet:
    """Render the proving clips, write config+manifest, and return a scored-ready set."""
    work_dir = Path(work_dir)
    clips_dir = work_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    specs = clip_specs()
    config_path = work_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(config_dict()))
    sessions: dict[str, list] = {}
    backends = []
    for spec in specs:
        rel = _render(spec, clips_dir)
        sessions.setdefault(spec["session"], []).append(_clip_entry(spec, rel))
        backends.append(backend_for(spec))
    manifest_dict = {
        "dataset": DATASET,
        "sessions": [{"id": sid, "clips": clips} for sid, clips in sessions.items()],
    }
    manifest_path = work_dir / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(copy.deepcopy(manifest_dict)))
    manifest = load_manifest(str(manifest_path), clips_dir)
    return SyntheticSet(
        config=load_station_config(config_path),
        config_path=config_path,
        manifest_path=manifest_path,
        manifest=manifest,
        backends=backends,
    )


__all__ = ["build_synthetic_set", "SyntheticSet", "DATASET", "SPEED"]
