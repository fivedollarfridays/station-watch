"""Startup wiring for ``station-watch run`` -- fail loud, name the piece (K9).

Every precondition the runner needs -- a config with all required keys, an
openable capture source, a writable Log, and at least one alarm sink -- is
checked here before the pipeline turns over. A failure raises
:class:`StartupError` whose message names the missing piece, so a misconfigured
station never limps along half-wired; the CLI turns that into a non-zero exit.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from station_watch.alarm.sink import AlarmError, Sink, build_sinks
from station_watch.capture.blind import BlindThresholds
from station_watch.capture.source import CaptureError, FrameSource
from station_watch.config import StationConfig, load_station_config
from station_watch.log import Log, LogError


class StartupError(RuntimeError):
    """A startup precondition failed; the message names the missing piece."""


@dataclass
class RunContext:
    """Everything the runner needs, all validated and opened."""

    config: StationConfig
    source: FrameSource
    log: Log
    sinks: list[Sink]
    thresholds: BlindThresholds


def parse_source(spec: str) -> int | str:
    """A bare integer is a device index; anything else is a file path/pattern."""
    return int(spec) if spec.isdigit() else spec


def load_config(path: str) -> StationConfig:
    """Load the station config or fail loud, naming the file or the missing key."""
    if not Path(path).exists():
        raise StartupError(f"config file not found: {path}")
    try:
        return load_station_config(path)
    except KeyError as exc:
        raise StartupError(f"config is missing a required key: {exc.args[0]}") from exc
    except (ValueError, OSError) as exc:
        raise StartupError(f"could not read config {path}: {exc}") from exc


def build_alarm_sinks(config: StationConfig, *, record_path: str | None) -> list[Sink]:
    """Build the configured alarm sinks or fail loud (K9: no alarm rail)."""
    try:
        return build_sinks(config.alarm, record_path=record_path)
    except AlarmError as exc:
        raise StartupError(str(exc)) from exc


def open_log(path: str) -> Log:
    """Open the Log or fail loud, naming the unwritable path."""
    try:
        return Log(path)
    except (LogError, sqlite3.Error, OSError) as exc:
        raise StartupError(f"could not open log {path}: {exc}") from exc


def open_source(spec: str) -> FrameSource:
    """Open the capture source or fail loud, naming the source that would not open."""
    try:
        return FrameSource(parse_source(spec))
    except CaptureError as exc:
        raise StartupError(str(exc)) from exc


def build_context(
    *, config_path: str, source_spec: str, log_path: str, alarm_record: str | None
) -> RunContext:
    """Validate and open everything the runner needs; raise StartupError on any gap."""
    config = load_config(config_path)
    sinks = build_alarm_sinks(config, record_path=alarm_record)
    log = open_log(log_path)
    try:
        source = open_source(source_spec)
    except StartupError:
        log.close()
        raise
    return RunContext(
        config=config,
        source=source,
        log=log,
        sinks=sinks,
        thresholds=BlindThresholds.from_station_config(config),
    )


__all__ = ["StartupError", "RunContext", "build_context", "parse_source"]
