"""StationConfig: the per-station settings loaded from YAML.

HF1 is single station -- one camera, one station. Loading a config that is
missing any required key -- top-level or nested, e.g. ``watchdog.cycle_window_s``
-- raises a ``KeyError`` whose message names the full dotted key (K9), so a
misconfigured station fails loudly and specifically at startup, never mid-loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REQUIRED_KEYS = (
    "station_id",
    "camera_id",
    "takt_s",
    "grace_s",
    "required_slots",
    "keepout_zones",
    "liveness_window_s",
    "dark_luma_threshold",
    "dark_window_s",
    "frozen_frames",
    "recover_good_frames",
    "cycle_interval_s",
    "recover_healthy_verdicts",
    "fiducial",
    "alarm",
    "watchdog",
    "detect",
)

# Required sub-keys of each nested section (``fiducial.window_s`` is optional).
REQUIRED_NESTED_KEYS = {
    "fiducial": ("dictionary_id", "marker_id", "expected_center_px", "tolerance_px"),
    "alarm": ("sinks",),
    "watchdog": ("cycle_window_s", "alarm_eval_window_s", "sinks"),
    "detect": (
        "persistence_frames",
        "emit_interval_s",
        "rail_positions",
        "station_zone",
        "keepout_rois",
        "blur_threshold",
        "darkness_threshold",
        "occlusion_threshold",
    ),
}


def _missing(dotted: str) -> KeyError:
    return KeyError(f"missing config key: {dotted}")


def _validate_detect_coverage(data: dict[str, Any]) -> None:
    """Every required slot needs a rail position and every keep-out zone an ROI (K9)."""
    detect = data["detect"]
    for slot in data["required_slots"]:
        if slot not in detect["rail_positions"]:
            raise _missing(f"detect.rail_positions.{slot}")
    for zone in data["keepout_zones"]:
        if zone not in detect["keepout_rois"]:
            raise _missing(f"detect.keepout_rois.{zone}")


def _validate_slope(detect: dict[str, Any]) -> None:
    """``detect.slope`` (optional): well-formed, and only with station-zone motion (K9/K12).

    Cycle-time creep is computed from station-zone ``motion`` / ``no_motion``
    observations, which Detect emits only when ``detect.station_zone.track_motion``
    is true -- a slope section without it would be an alarm that can never fire.
    """
    if "slope" not in detect:
        return
    slope = detect["slope"]
    if not isinstance(slope, dict):
        raise ValueError(f"config key detect.slope must be a mapping, got {type(slope).__name__}")
    for key in ("window_steps", "min_s_per_step"):
        if key not in slope:
            raise _missing(f"detect.slope.{key}")
    window = slope["window_steps"]
    if isinstance(window, bool) or not isinstance(window, int) or window < 2:
        raise ValueError(
            f"config key detect.slope.window_steps must be an int >= 2, got {window!r}"
        )
    rate = slope["min_s_per_step"]
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate <= 0:
        raise ValueError(
            f"config key detect.slope.min_s_per_step must be a number > 0, got {rate!r}"
        )
    if detect["station_zone"].get("track_motion") is not True:
        raise ValueError(
            "config key detect.slope requires detect.station_zone.track_motion: true "
            "(cycle_time_creep is measured from station-zone motion; without it the alarm "
            "can never fire)"
        )


def validate_required_keys(data: dict[str, Any]) -> None:
    """Raise ``KeyError`` naming the first missing required key, as a dotted path."""
    for key in REQUIRED_KEYS:
        if key not in data:
            raise _missing(key)
    for section, keys in REQUIRED_NESTED_KEYS.items():
        value = data[section]
        if not isinstance(value, dict):
            raise ValueError(f"config key {section} must be a mapping, got {type(value).__name__}")
        for key in keys:
            if key not in value:
                raise _missing(f"{section}.{key}")
    _validate_detect_coverage(data)
    _validate_slope(data["detect"])


@dataclass(frozen=True)
class StationConfig:
    station_id: str
    camera_id: str
    takt_s: float
    grace_s: float
    required_slots: list
    keepout_zones: list
    liveness_window_s: float
    dark_luma_threshold: float
    dark_window_s: float
    frozen_frames: int
    recover_good_frames: int
    cycle_interval_s: float
    recover_healthy_verdicts: int
    fiducial: dict
    alarm: dict
    watchdog: dict
    detect: dict

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> StationConfig:
        validate_required_keys(data)
        return cls(**{key: data[key] for key in REQUIRED_KEYS})


def load_station_config(path: str | Path) -> StationConfig:
    """Load and validate a StationConfig from a YAML file."""
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError(f"station config must be a YAML mapping, got {type(data).__name__}")
    return StationConfig.from_mapping(data)
