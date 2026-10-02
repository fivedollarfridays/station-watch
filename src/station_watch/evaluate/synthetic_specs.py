"""The ``--synthetic`` proving set's *content*: scene geometry, config, clip scripts.

Split out of :mod:`station_watch.evaluate.synthetic` (which renders and writes the
set) so the hand-countable ground truth lives on its own: the rail/keep-out/zone
geometry, the station config sized for accelerated playback, each clip's render
script and labels, and the scripted keep-out backends (YOLOX never fires on a
synthetic blob, so the keep-out clip injects a person box).
"""

from __future__ import annotations

import numpy as np

from station_watch.detect.details import DEFAULT_DETAIL_REGIONS
from station_watch.detect.geometry import region_to_pixels

# The three detail kinds, read on rail_pos_1 at their default regions. Configured as
# slot_details (so the DetailTracker reads and scores them) but not required -- a missing
# detail here is scored in the rail confusion matrix, not faulted (HF3.16).
SLOT_DETAILS = {"rail_pos_1": dict(DEFAULT_DETAIL_REGIONS["rail_pos_1"])}
_DETAIL_KINDS = ("torque_stripe", "label", "ferrule")

DATASET = "synthetic-proving"
SPEED = 4.0

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


def person_box():
    poly = region_to_pixels(KEEPOUT["zone_press"]["region"], MARKER_CORNERS)
    x0, y0 = poly.min(axis=0)
    x1, y1 = poly.max(axis=0)
    return (float(x0), float(y0), float(x1), float(y1), 0.9)


def config_dict() -> dict:
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
            "slot_details": SLOT_DETAILS,
        },
    }


_BOTH = {"rail_pos_1": "present", "rail_pos_2": "present"}
_MISS = {"rail_pos_1": "absent", "rail_pos_2": "present"}


def _f(states, *, motion=False, hide=False, details=None):
    spec = {"positions": dict(states)}
    if motion:
        spec["motion"] = True
    if hide:
        spec["hide_marker"] = True
    if details:
        spec["details"] = {"rail_pos_1": dict(details)}
    return spec


def _stall_script():
    """Short motion/still cycles (closed steps, each still under the window) then a
    final long still that actually stalls -- the only clip that should fault stalled."""
    cycles: list[dict] = []
    for _ in range(3):
        cycles += [_f(_BOTH, motion=True)] * 6 + [_f(_BOTH)] * 6
    return cycles + [_f(_BOTH, motion=True)] * 6 + [_f(_BOTH)] * 60  # final still starts at 42


def _both():
    return {"target_states": [("rail_pos_1", "present"), ("rail_pos_2", "present")]}


def _clip(session, name, script, last, labels=None, **extra) -> dict:
    spec = {"session": session, "name": name, "script": script, "last": last, **_both()}
    spec.update(labels=labels or {}, **extra)
    return spec


def _moving(states, n, **kw) -> list[dict]:
    return [_f(states, motion=True, **kw) for _ in range(n)]


def _detail_positions(last, states) -> list[dict]:
    """Manifest ``positions`` entries for rail_pos_1's detail targets over the whole clip."""
    return [
        {"target": f"rail_pos_1.{kind}", "state": state, "start_frame": 0, "end_frame": last}
        for kind, state in states.items()
    ]


def _detail_clip(session, name, missing_kind) -> dict:
    """A both-present clip with every rail_pos_1 detail drawn except ``missing_kind``.

    Scores the detail targets in the rail confusion matrix: the two drawn kinds read
    present, the omitted one reads absent. Components stay present and the clip runs
    continuous motion, so no part/stall/keep-out fault fires -- only the detail reads
    differ (HF3.16). ``missing_kind`` is not required, so a missing detail is measured,
    not faulted."""
    states = {kind: ("absent" if kind == missing_kind else "present") for kind in _DETAIL_KINDS}
    script = _moving(_BOTH, 48, details=states)
    return _clip("s1", name, script, 47, extra_positions=_detail_positions(47, states))


def _blind_clip() -> dict:
    """Marker visible for 16 frames, then hidden: both slots read unknown after."""
    unknown = [
        {"target": t, "state": "unknown", "start_frame": 16, "end_frame": 47}
        for t in ("rail_pos_1", "rail_pos_2")
    ]
    script = _moving(_BOTH, 16) + _moving(_BOTH, 32, hide=True)
    labels = {"camera_faults": [{"reason": "fiducial_missing", "start_frame": 16}]}
    return _clip("s1", "blind", script, 15, labels, extra_positions=unknown)


def clip_specs() -> list[dict]:
    """Each clip: session, name, render script, ground-truth labels, keep-out backend.

    Non-stall clips run continuous motion so the station zone never falsely stalls
    (the Judge's stall is wall-time since the last motion, so a quiet clip would).
    """
    missing = _clip("s1", "missing", _moving(_MISS, 48), 47)
    missing["target_states"] = [("rail_pos_1", "absent"), ("rail_pos_2", "present")]
    stall = {"stalls": [{"start_frame": 42, "end_frame": 101}]}
    keepout = {"keepouts": [{"zone": "zone_press", "start_frame": 14, "end_frame": 31}]}
    return [
        _clip("s1", "normal", _moving(_BOTH, 48), 47),
        missing,
        _blind_clip(),
        _detail_clip("s1", "detail_missing_stripe", "torque_stripe"),
        _detail_clip("s1", "detail_missing_label", "label"),
        _clip("s2", "stall", _stall_script(), 101, stall),
        _clip("s2", "keepout", _moving(_BOTH, 32), 31, keepout, backend=("enter", 6)),
    ]


def backend_for(spec):
    """The keep-out backend a clip runs with: scripted entry, or never a person."""
    kind = spec.get("backend")
    if kind and kind[0] == "enter":
        return EnteringBackend(person_box(), clear_calls=kind[1])
    return ClearBackend()


__all__ = [
    "DATASET",
    "KEEPOUT",
    "MARKER_CORNERS",
    "RAIL",
    "SLOT_DETAILS",
    "SPEED",
    "STATION_ZONE",
    "ClearBackend",
    "EnteringBackend",
    "backend_for",
    "clip_specs",
    "config_dict",
    "person_box",
]
