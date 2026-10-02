"""Component *detail* reads: the torque stripe, the label, and the ferrule (HF3.16).

PLAN 5.1's bench checks beyond "component present and seated" are per-component
details: a torque stripe (a paint-pen mark across a termination), a printed label, a
ferrule. This module reads all three with one open registry so a new kind is added by
registering a method, never by editing callers.

:func:`read_detail` is pure: given a frame, a pixel polygon and a detail kind it
returns a :class:`DetailReading` (``present`` / ``absent`` / ``unknown``). The four
unknown-cause gates (out_of_frame / dark / blurred / occluded) are the *shared*
:func:`station_watch.detect.regions.read_region_quality` -- the same checks rail
positions use, never copied -- so a dim, blurred or hand-covered detail reads
``unknown`` with the matching cause and a lowered confidence ceiling (K3). Each
reading also carries its raw scores (K4).

Each registered method decides ``present`` / ``absent`` from one clean crop:

* ``torque_stripe`` -- the fraction of the region whose HSV hue sits in a configured
  paint-pen ``hue_range`` (and is colourful enough to not be bare rail); ``present``
  when that fill clears ``detect.details.torque_stripe.min_fill``.
* ``label`` -- the heat-shrink label is near-white: the fraction of the region that is
  bright and desaturated (value over ``_LABEL_MIN_VALUE``, saturation under
  ``_LABEL_MAX_SATURATION``); ``present`` when that fill clears
  ``detect.details.label.min_fill``.
* ``ferrule`` -- a crimped metal sleeve at the wire end is bright and desaturated
  (metallic) *and* carries the high edge density of its crimp ridges; ``present`` when
  the metallic fill clears ``detect.details.ferrule.min_fill`` *and* the Canny edge
  density clears ``detect.details.ferrule.min_edge_density``.

Every method's thresholds live under ``detect.details.<kind>`` with the documented
defaults registered beside each method below.

:class:`DetailTracker` follows the Detector tracker protocol and reuses
:class:`~station_watch.detect.persistence.PersistenceEngine` for N-frame persistence
and ``emit_interval_s`` re-emit. A detail is judged only while its parent rail
position's confirmed state is ``present`` (read from the sibling
:class:`~station_watch.detect.positions.PositionTracker`); otherwise it emits
``part_unknown`` with cause ``parent_not_present``, so a missing component faults
once (the position) and never twice.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

from station_watch.detect.geometry import region_to_pixels
from station_watch.detect.persistence import PersistenceEngine
from station_watch.detect.regions import read_region_quality
from station_watch.records import Observation, ObservationKind

CAUSE_PARENT_NOT_PRESENT = "parent_not_present"

# Intrinsic to the hue-fill method (like regions._COLOR_DIFF_MIN): a pixel must be
# colourful and bright enough to count as paint, so the near-gray rail never fills.
_MIN_SATURATION = 80
_MIN_VALUE = 80

# Intrinsic to the label method: a heat-shrink label is near-white -- bright and
# desaturated -- so a saturated component colour beneath it never reads as label.
_LABEL_MAX_SATURATION = 60
_LABEL_MIN_VALUE = 170

# Intrinsic to the ferrule method: a crimped metal sleeve is bright and desaturated
# (metallic). Its crimp ridges, not its colour, tell it from a flat label -- hence the
# edge-density gate alongside the metallic fill.
_FERRULE_MAX_SATURATION = 60
_FERRULE_MIN_VALUE = 140


@dataclass(frozen=True)
class DetailReading:
    """One detail read from one frame (no time thresholds applied)."""

    target: str  # the detail kind read (the tracker composes the dotted slot target)
    state: str  # present | absent | unknown
    cause: str | None  # unknown cause, else None
    confidence_ceiling: float
    scores: dict


@dataclass(frozen=True)
class DetailMethod:
    """A registered detail method: a versioned name, defaults, and a reader."""

    name: str
    defaults: dict
    read: Callable[[np.ndarray, dict], tuple[str, dict]]


_METHODS: dict[str, DetailMethod] = {}
DETAIL_KINDS: tuple[str, ...] = ()

# Default detail regions (position id -> kind -> marker-unit quad). These match what
# the synthetic renderer draws, so a drawn detail and the region it is read from are
# the same bench spot. Each kind sits on its own band of the component: the label
# above the torque stripe, the ferrule below it, all inside the rail-position block so
# an un-drawn detail reads the component colour (absent), never bare rail.
DEFAULT_DETAIL_REGIONS: dict[str, dict[str, list]] = {
    "rail_pos_1": {
        "label": [[0.80, 1.92], [1.60, 1.92], [1.60, 2.04], [0.80, 2.04]],
        "torque_stripe": [[0.85, 2.05], [1.55, 2.05], [1.55, 2.25], [0.85, 2.25]],
        "ferrule": [[0.85, 2.30], [1.55, 2.30], [1.55, 2.44], [0.85, 2.44]],
    },
    "rail_pos_2": {
        "label": [[2.60, 1.92], [3.40, 1.92], [3.40, 2.04], [2.60, 2.04]],
        "torque_stripe": [[2.65, 2.05], [3.35, 2.05], [3.35, 2.25], [2.65, 2.25]],
        "ferrule": [[2.65, 2.30], [3.35, 2.30], [3.35, 2.44], [2.65, 2.44]],
    },
}


def register_detail_method(kind: str, method: DetailMethod) -> None:
    """Register a detail method under ``kind`` and refresh :data:`DETAIL_KINDS`."""
    _METHODS[kind] = method
    global DETAIL_KINDS
    DETAIL_KINDS = tuple(_METHODS)


def method_name(kind: str) -> str:
    """The versioned method name an Observation carries for ``kind``."""
    return _METHODS[kind].name


def _read_label(crop: np.ndarray, config: dict) -> tuple[str, dict]:
    """Fraction of the region that is near-white: bright and desaturated (heat-shrink label)."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    sat, val = hsv[..., 1], hsv[..., 2]
    near_white = (sat <= _LABEL_MAX_SATURATION) & (val >= _LABEL_MIN_VALUE)
    fill = float(near_white.mean())
    state = "present" if fill >= config["min_fill"] else "absent"
    scores = {"fill_fraction": round(fill, 4), "min_fill": config["min_fill"]}
    return state, scores


def _read_ferrule(crop: np.ndarray, config: dict) -> tuple[str, dict]:
    """Fraction of bright, desaturated metallic pixels, gated on crimp-ridge edge density."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    sat, val = hsv[..., 1], hsv[..., 2]
    metallic = (sat <= _FERRULE_MAX_SATURATION) & (val >= _FERRULE_MIN_VALUE)
    fill = float(metallic.mean())
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    edge_density = float((cv2.Canny(gray, 50, 150) > 0).mean())
    present = fill >= config["min_fill"] and edge_density >= config["min_edge_density"]
    scores = {
        "fill_fraction": round(fill, 4),
        "edge_density": round(edge_density, 4),
        "min_fill": config["min_fill"],
        "min_edge_density": config["min_edge_density"],
    }
    return ("present" if present else "absent"), scores


def _read_torque_stripe(crop: np.ndarray, config: dict) -> tuple[str, dict]:
    """Fraction of the region whose HSV hue is in the configured paint-pen range."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    lo, hi = config["hue_range"]
    in_paint = (hue >= lo) & (hue <= hi) & (sat >= _MIN_SATURATION) & (val >= _MIN_VALUE)
    fill = float(in_paint.mean())
    state = "present" if fill >= config["min_fill"] else "absent"
    scores = {"fill_fraction": round(fill, 4), "hue_range": [lo, hi], "min_fill": config["min_fill"]}
    return state, scores


# Registered in the order HF3.17 consumes: ``DETAIL_KINDS == ("label", "ferrule",
# "torque_stripe")``. Each name is versioned so a method change is visible in the Log.
register_detail_method(
    "label",
    DetailMethod(
        name="label:white_fill:v1",
        defaults={"min_fill": 0.3},
        read=_read_label,
    ),
)
register_detail_method(
    "ferrule",
    DetailMethod(
        name="ferrule:metallic_edges:v1",
        defaults={"min_fill": 0.2, "min_edge_density": 0.04},
        read=_read_ferrule,
    ),
)
register_detail_method(
    "torque_stripe",
    DetailMethod(
        name="torque_stripe:hue_fill:v1",
        defaults={"min_fill": 0.15, "hue_range": [20, 40]},
        read=_read_torque_stripe,
    ),
)


def _kind_config(detect_config: dict, kind: str) -> dict:
    """The method's thresholds: documented defaults overlaid by ``detect.details.<kind>``."""
    user = detect_config.get("details", {}).get(kind, {})
    return {**_METHODS[kind].defaults, **user}


def read_detail(frame, poly, detail_kind: str, detect_config: dict) -> DetailReading:
    """Pure read of one detail region. ``poly`` is already pixels (``None`` -> unknown)."""
    method = _METHODS[detail_kind]
    quality = read_region_quality(frame, poly, detect_config)
    scores = dict(quality.scores)
    if quality.cause is not None:
        return DetailReading(detail_kind, "unknown", quality.cause, quality.ceiling, scores)
    state, fill_scores = method.read(quality.crop, _kind_config(detect_config, detail_kind))
    scores.update(fill_scores)
    return DetailReading(detail_kind, state, None, quality.ceiling, scores)


def detail_targets(config) -> list[str]:
    """The dotted ``<position_id>.<kind>`` targets the detail tracker reads."""
    slot_details = config.detect.get("slot_details", {})
    return [f"{pid}.{kind}" for pid, kinds in slot_details.items() for kind in kinds]


def detail_targets_configured(config) -> bool:
    """True when the config lists any ``detect.slot_details`` region to read."""
    return bool(config.detect.get("slot_details"))


class DetailTracker:
    """Persist per-frame detail readings into part_present/absent/unknown Observations.

    Composed after the :class:`PositionTracker` in the same Detector, so each frame's
    parent-position confirmed state is already up to date when a detail is judged.
    """

    def __init__(self, config, run_id: str, position_tracker) -> None:
        self.detect = config.detect
        self.slot_details = config.detect.get("slot_details", {})
        self._positions = position_tracker
        self._engine = PersistenceEngine(config, run_id, detail_targets(config))

    def update(self, frame, frame_id: int, ts: str, corners) -> list[Observation]:
        """Read every configured detail, gating each on its parent position's state."""
        out: list[Observation] = []
        for pid, kinds in self.slot_details.items():
            parent_present = (
                self._positions is not None
                and self._positions.confirmed_state(pid) is ObservationKind.PART_PRESENT
            )
            for kind in kinds:
                reading = self._read(frame, corners, pid, kind, parent_present)
                observation = self._apply(f"{pid}.{kind}", kind, reading, frame_id, ts)
                if observation is not None:
                    out.append(observation)
        return out

    def unknown_all(self, frame_id: int, ts: str, cause: str, detail: str) -> list[Observation]:
        """Force a ``part_unknown`` reading for every detail (whole-frame failure)."""
        out: list[Observation] = []
        for target in self._engine.tracks:
            kind = target.split(".", 1)[1]
            observation = self._engine.apply(
                target, "unknown", cause, 0.0, {"detail": detail}, method_name(kind), frame_id, ts
            )
            if observation is not None:
                out.append(observation)
        return out

    def _read(self, frame, corners, pid, kind, parent_present) -> DetailReading:
        if not parent_present:
            return DetailReading(kind, "unknown", CAUSE_PARENT_NOT_PRESENT, 0.0, {})
        poly = region_to_pixels(self.slot_details[pid][kind], corners)
        return read_detail(frame, poly, kind, self.detect)

    def _apply(self, target, kind, reading: DetailReading, frame_id, ts) -> Observation | None:
        cause = reading.cause if reading.state == "unknown" else None
        return self._engine.apply(
            target,
            reading.state,
            cause,
            reading.confidence_ceiling,
            reading.scores,
            method_name(kind),
            frame_id,
            ts,
        )


__all__ = [
    "CAUSE_PARENT_NOT_PRESENT",
    "DEFAULT_DETAIL_REGIONS",
    "DETAIL_KINDS",
    "DetailMethod",
    "DetailReading",
    "DetailTracker",
    "detail_targets",
    "detail_targets_configured",
    "read_detail",
    "read_region_quality",
    "register_detail_method",
]
