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
)

# Required sub-keys of each nested section (``fiducial.window_s`` is optional).
REQUIRED_NESTED_KEYS = {
    "fiducial": ("dictionary_id", "marker_id", "expected_center_px", "tolerance_px"),
    "alarm": ("sinks",),
    "watchdog": ("cycle_window_s", "alarm_eval_window_s", "sinks"),
}


def _missing(dotted: str) -> KeyError:
    return KeyError(f"missing config key: {dotted}")


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
