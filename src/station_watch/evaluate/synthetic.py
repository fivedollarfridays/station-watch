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

import numpy as np
import yaml

from station_watch.config import StationConfig, load_station_config
from station_watch.detect.geometry import region_to_pixels
from station_watch.evaluate.manifest import Manifest, load_manifest

DATASET = "synthetic-proving"
SPEED = 4.0
_FPS = 20.0

RAIL = {
    "rail_pos_1": [[0.7, 1.9], [1.7, 1.9], [1.7, 2.5], [0.7, 2.5]],
    "rail_pos_2": [[2.5, 1.9], [3.5, 1.9], [3.5, 2.5], [2.5, 2.5]],
}
KEEPOUT = {
    "zone_press": {"region": [[4.0, 1.9], [5.3, 1.9], [5.3, 3.0], [4.0, 3.0]], "active": True}
}
STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}
MARKER_CORNERS = np.array([[30, 30], [86, 30], [86, 86], [30, 86]], dtype=np.float32)


class ClearBackend:
    """A keep-out backend that never sees a person (every clip but the keep-out one)."""

    def detect_people(self, frame):
        return []


class EnteringBackend:
    """No one for the first ``clear_calls`` frames, then a person box inside the zone."""

    def __init__(self, person_box, clear_calls):
        self._box = person_box
        self._clear_calls = clear_calls
        self._calls = 0

    def detect_people(self, frame):
        self._calls += 1
        return [] if self._calls <= self._clear_calls else [self._box]


def _person_box():
    poly = region_to_pixels(KEEPOUT["zone_press"]["region"], MARKER_CORNERS)
    x0, y0 = poly.min(axis=0)
    x1, y1 = poly.max(axis=0)
    return (float(x0), float(y0), float(x1), float(y1), 0.9)


def _config_dict() -> dict:
    return {
        "station_id": "station-1",
        "camera_id": "cam-0",
        "takt_s": 0.2,
        "grace_s": 0.1,  # 0.3 s stall window, reached inside the accelerated still tail
        "required_slots": list(RAIL),
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
            "window_s": 0.2,  # a lost marker reads blind inside the accelerated clip
        },
        "alarm": {"sinks": ["record"]},
        "watchdog": {"cycle_window_s": 1.0, "alarm_eval_window_s": 1.0, "sinks": ["record"]},
        "detect": {
            "persistence_frames": 2,
            "emit_interval_s": 1.0,
            "rail_positions": RAIL,
            "station_zone": {**STATION_ZONE, "track_motion": True},
            "keepout_rois": KEEPOUT,
            "keepout": {"min_overlap": 0.1, "persistence_frames": 2},
            "blur_threshold": 100.0,
            "darkness_threshold": 40.0,
            "occlusion_threshold": 0.5,
            "unknown_grace_s": 10_000.0,
        },
    }


_BOTH = {"rail_pos_1": "present", "rail_pos_2": "present"}
_MISS = {"rail_pos_1": "absent", "rail_pos_2": "present"}


def _f(states, *, motion=False, hide=False):
    spec = {"positions": dict(states)}
    if motion:
        spec["motion"] = True
    if hide:
        spec["hide_marker"] = True
    return spec


def _stall_script():
    """Short motion/still cycles (closed steps, each still under the window) then a
    final long still that actually stalls -- the only clip that should fault stalled."""
    cycles: list[dict] = []
    for _ in range(3):
        cycles += [_f(_BOTH, motion=True)] * 6 + [_f(_BOTH)] * 6
    return cycles + [_f(_BOTH, motion=True)] * 6 + [_f(_BOTH)] * 60  # final still starts at 42


def _specs() -> list[dict]:
    """Each clip: session, name, render script, ground-truth labels, keep-out backend.

    Non-stall clips run continuous motion so the station zone never falsely stalls
    (the Judge's stall is wall-time since the last motion, so a quiet clip would).
    """
    both = {"target_states": [("rail_pos_1", "present"), ("rail_pos_2", "present")]}
    return [
        {
            "session": "s1",
            "name": "normal",
            "script": [_f(_BOTH, motion=True) for _ in range(48)],
            "last": 47,
            **both,
            "labels": {},
        },
        {
            "session": "s1",
            "name": "missing",
            "script": [_f(_MISS, motion=True) for _ in range(48)],
            "last": 47,
            "target_states": [("rail_pos_1", "absent"), ("rail_pos_2", "present")],
            "labels": {},
        },
        {
            "session": "s1",
            "name": "blind",
            "script": [_f(_BOTH, motion=True) for _ in range(16)]
            + [_f(_BOTH, motion=True, hide=True) for _ in range(32)],
            "last": 15,
            **both,
            "extra_positions": [
                {"target": t, "state": "unknown", "start_frame": 16, "end_frame": 47}
                for t in ("rail_pos_1", "rail_pos_2")
            ],
            "labels": {"camera_faults": [{"reason": "fiducial_missing", "start_frame": 16}]},
        },
        {
            "session": "s2",
            "name": "stall",
            "script": _stall_script(),
            "last": 101,
            **both,
            "labels": {"stalls": [{"start_frame": 42, "end_frame": 101}]},
        },
        {
            "session": "s2",
            "name": "keepout",
            "script": [_f(_BOTH, motion=True) for _ in range(32)],
            "last": 31,
            **both,
            "labels": {"keepouts": [{"zone": "zone_press", "start_frame": 14, "end_frame": 31}]},
            "backend": ("enter", 6),
        },
    ]


def _load_renderer():
    """Locate the repo's ``synth_station`` renderer (a dev/proving-only helper).

    ``--synthetic`` is a proving tool run from the repository: the renderer lives
    under ``tests/helpers`` (one source of truth, shared with the e2e tests), so we
    put that directory on the path the same way the e2e tests do rather than
    duplicating ~200 lines of rendering into the shipped package.
    """
    import sys

    tests_dir = Path(__file__).resolve().parents[3] / "tests"
    if str(tests_dir) not in sys.path:
        sys.path.insert(0, str(tests_dir))
    from helpers.synth_station import write_synth_station_clip

    return write_synth_station_clip


def _render(spec, clips_dir: Path) -> str:
    write_synth_station_clip = _load_renderer()
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


def _backend_for(spec):
    kind = spec.get("backend")
    if kind and kind[0] == "enter":
        return EnteringBackend(_person_box(), clear_calls=kind[1])
    return ClearBackend()


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
    specs = _specs()
    config_path = work_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(_config_dict()))
    sessions: dict[str, list] = {}
    backends = []
    for spec in specs:
        rel = _render(spec, clips_dir)
        sessions.setdefault(spec["session"], []).append(_clip_entry(spec, rel))
        backends.append(_backend_for(spec))
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
