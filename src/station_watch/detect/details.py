"""Component *detail* reads: the torque stripe now, label and ferrule next (HF3.16).

PLAN 5.1's bench checks beyond "component present and seated" are per-component
details: a torque stripe (a paint-pen mark across a termination), a printed label, a
ferrule. This module reads them with one open registry so a new kind is added by
registering a method, never by editing callers.

:func:`read_detail` is pure: given a frame, a pixel polygon and a detail kind it
returns a :class:`DetailReading` (``present`` / ``absent`` / ``unknown``). The four
unknown-cause gates (out_of_frame / dark / blurred / occluded) are the *shared*
:func:`station_watch.detect.regions.read_region_quality` -- the same checks rail
positions use, never copied -- so a dim, blurred or hand-covered stripe reads
``unknown`` with the matching cause and a lowered confidence ceiling (K3). Each
reading also carries its raw scores (K4).

The ``torque_stripe`` method reads the fraction of the region whose HSV hue sits in a
configured paint-pen ``hue_range`` (and is colourful enough to not be bare rail); it
is ``present`` when that fill clears ``min_fill``. Both thresholds live in
``detect.details.torque_stripe`` with the documented defaults below.

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
# the synthetic renderer draws, so a drawn stripe and the region it is read from are
# the same bench spot. HF3.16 extends this with label and ferrule regions.
DEFAULT_DETAIL_REGIONS: dict[str, dict[str, list]] = {
    "rail_pos_1": {"torque_stripe": [[0.85, 2.05], [1.55, 2.05], [1.55, 2.25], [0.85, 2.25]]},
    "rail_pos_2": {"torque_stripe": [[2.65, 2.05], [3.35, 2.05], [3.35, 2.25], [2.65, 2.25]]},
}


def register_detail_method(kind: str, method: DetailMethod) -> None:
    """Register a detail method under ``kind`` and refresh :data:`DETAIL_KINDS`."""
    _METHODS[kind] = method
    global DETAIL_KINDS
    DETAIL_KINDS = tuple(_METHODS)


def method_name(kind: str) -> str:
    """The versioned method name an Observation carries for ``kind``."""
    return _METHODS[kind].name


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
