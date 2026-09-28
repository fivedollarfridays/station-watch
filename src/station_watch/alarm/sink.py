"""Alarm sinks: where an alarm and its recovery are announced.

Sinks are pluggable and named in ``alarm.sinks`` in the station config. Every
test uses the :class:`RecordSink`, which appends one JSON line per alarm and per
recovery to a file, so CI proves the whole path without an audio device. The
:class:`ScreenSink` writes a terminal line naming the station, cause, start time
and cited frames; the :class:`SoundSink` (see :mod:`station_watch.alarm.sound`)
plays a short bundled tone.

Startup with no sink configured refuses to start and names the missing rail
(K9): :func:`build_sinks` raises on an empty or absent ``alarm.sinks``, and the
:class:`~station_watch.alarm.episodes.Alarm` refuses an empty sink list.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


class AlarmError(RuntimeError):
    """Raised when the Alarm cannot start or a sink cannot be built."""


class Sink:
    """The sink interface: announce an alarm, and later its recovery."""

    def on_alarm(self, episode) -> None:
        raise NotImplementedError

    def on_recovery(self, episode, recovered_ts: str) -> None:
        raise NotImplementedError


class RecordSink(Sink):
    """Appends one JSON line per alarm and per recovery to a file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def on_alarm(self, episode) -> None:
        self._append(
            {
                "event": "alarm",
                "station_id": episode.station_id,
                "cause": episode.cause,
                "label": episode.label,
                "started_ts": episode.started_ts,
                "frame_ids": list(episode.frame_ids),
            }
        )

    def on_recovery(self, episode, recovered_ts: str) -> None:
        self._append(
            {
                "event": "recovery",
                "station_id": episode.station_id,
                "cause": episode.cause,
                "label": episode.label,
                "started_ts": episode.started_ts,
                "recovered_ts": recovered_ts,
            }
        )

    def _append(self, row: dict) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")


class ScreenSink(Sink):
    """Writes a terminal line for each alarm and recovery."""

    def __init__(self, stream=None) -> None:
        self._stream = stream if stream is not None else sys.stdout

    def on_alarm(self, episode) -> None:
        print(
            f"ALARM  {episode.station_id}  {episode.label}  "
            f"since {episode.started_ts}  frames={list(episode.frame_ids)}",
            file=self._stream,
        )

    def on_recovery(self, episode, recovered_ts: str) -> None:
        print(
            f"CLEAR  {episode.station_id}  {episode.label}  recovered {recovered_ts}",
            file=self._stream,
        )


def build_sinks(alarm_config, *, record_path=None, screen_stream=None) -> list[Sink]:
    """Build the sinks named in ``alarm_config['sinks']`` (K9: refuse if none)."""
    names = alarm_config.get("sinks") if isinstance(alarm_config, dict) else None
    if not names:
        raise AlarmError(
            "refusing to start: no alarm sink configured (alarm.sinks is missing or empty)"
        )
    return [_build_one(name, record_path, screen_stream) for name in names]


def _build_one(name: str, record_path, screen_stream) -> Sink:
    if name == "screen":
        return ScreenSink(screen_stream)
    if name == "sound":
        from station_watch.alarm.sound import SoundSink

        return SoundSink()
    if name == "record":
        if record_path is None:
            raise AlarmError("alarm sink 'record' requires a record_path")
        return RecordSink(record_path)
    raise AlarmError(f"unknown alarm sink: {name}")


__all__ = ["AlarmError", "Sink", "RecordSink", "ScreenSink", "build_sinks"]
