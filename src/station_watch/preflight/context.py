"""The shared, mutable state the ordered preflight checks hand to one another.

The checks run in order and some feed the next: ``config`` resolves the
``StationConfig`` (and the raw YAML, for the keys the dataclass drops);
``camera`` opens the one frame source; ``frames_live`` samples a handful of
frames off it that ``fiducial`` then searches for the marker. A failure to open
the camera is recorded in ``camera_error`` so the camera-dependent checks report
that same reason rather than re-opening (or silently passing).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PreflightContext:
    """What one preflight run threads through its checks, filled in as they run."""

    config_path: str
    source: str
    log_path: str
    board_port: int | None = None
    # Filled by the checks as they run (``config`` then ``camera``/``frames_live``).
    station_config: object | None = None
    raw_config: dict | None = None
    config_error: str | None = None
    frame_source: object | None = None
    camera_error: str | None = None
    frames: list = field(default_factory=list)


__all__ = ["PreflightContext"]
