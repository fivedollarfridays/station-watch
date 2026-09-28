"""Component 5: the Alarm -- verdicts to episodes on screen and sound."""

from station_watch.alarm.episodes import Alarm, Episode
from station_watch.alarm.sink import (
    AlarmError,
    RecordSink,
    ScreenSink,
    Sink,
    build_sinks,
)
from station_watch.alarm.sound import SoundSink
from station_watch.alarm.tone import default_tone_path, generate_tone_wav

__all__ = [
    "Alarm",
    "Episode",
    "AlarmError",
    "Sink",
    "RecordSink",
    "ScreenSink",
    "SoundSink",
    "build_sinks",
    "default_tone_path",
    "generate_tone_wav",
]
