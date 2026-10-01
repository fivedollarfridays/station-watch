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
from station_watch.detect.detector import detect_targets_configured
from station_watch.detect.yolox import (
    WeightsError,
    YoloxBackend,
    default_model_path,
    verify_weights,
)
from station_watch.log import Log, LogError
from station_watch.steps import StepTimesError, resolve_stall_window


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
    keepout_backend: object | None = None
    stall_window_s: float = 0.0
    stall_window_source: str = ""


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
        raise StartupError(str(exc.args[0])) from exc
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


def build_keepout_backend(config: StationConfig):
    """The keep-out person detector when zones are configured, or ``None``.

    Loading verifies the weights file exists and matches the expected SHA-256; a
    missing or mismatched file raises :class:`StartupError` naming it (K9), so a
    station configured to watch a keep-out zone never starts half-blind to it.
    """
    if not config.keepout_zones:
        return None
    keepout = config.detect.get("keepout", {})
    model_path = keepout.get("model_path", str(default_model_path()))
    try:
        verify_weights(model_path)
    except WeightsError as exc:
        raise StartupError(str(exc)) from exc
    return YoloxBackend(model_path, score_threshold=keepout.get("score_threshold", 0.3))


def resolve_stall_window_or_fail(config: StationConfig) -> tuple[float, str]:
    """The Judge's stall window and its source, or a loud failure naming the file (K9).

    ``detect.step_times_path`` that points at a real measurement file gives
    ``p95 + grace_s``; absent, the window is ``takt_s + grace_s``; a configured path
    that does not exist (or will not parse) refuses to start.
    """
    try:
        return resolve_stall_window(config)
    except StepTimesError as exc:
        raise StartupError(str(exc)) from exc


def open_source(spec: str) -> FrameSource:
    """Open the capture source or fail loud, naming the source that would not open."""
    try:
        return FrameSource(parse_source(spec))
    except CaptureError as exc:
        raise StartupError(str(exc)) from exc


def build_context(
    *,
    config_path: str,
    source_spec: str | None,
    log_path: str,
    alarm_record: str | None,
    observations_path: str | None = None,
    source: FrameSource | None = None,
) -> RunContext:
    """Validate and open everything the runner needs; raise StartupError on any gap.

    ``source`` lets a caller supply an already-built frame source (the drill wraps a
    :class:`~station_watch.synth.source.SyntheticSource` in a
    :class:`~station_watch.faults.FaultSource`) instead of opening one from
    ``source_spec``; everything else is validated and opened identically, so the
    pipeline the drill runs is the one ``run`` wires.
    """
    config = load_config(config_path)
    if observations_path is not None and detect_targets_configured(config):
        raise StartupError(
            "--observations (fixture input) and a detect config with targets both supply "
            "observations; use one source per run (drop --observations or the detect targets)"
        )
    keepout_backend = build_keepout_backend(config)
    stall_window_s, stall_window_source = resolve_stall_window_or_fail(config)
    sinks = build_alarm_sinks(config, record_path=alarm_record)
    log = open_log(log_path)
    if source is None:
        if source_spec is None:
            log.close()
            raise StartupError("no capture source given (pass a source spec or a built source)")
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
        keepout_backend=keepout_backend,
        stall_window_s=stall_window_s,
        stall_window_source=stall_window_source,
    )


__all__ = ["StartupError", "RunContext", "build_context", "parse_source"]
