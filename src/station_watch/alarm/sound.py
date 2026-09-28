"""The sound alarm sink: a short bundled tone, no third-party audio.

macOS plays the tone with ``afplay``; Linux with ``aplay``; anywhere else (or
when no player is on PATH) it falls back to the terminal bell. Playback runs
through an injectable runner and is wrapped so a missing or failing audio player
can never delay or crash the alarm -- the record and screen sinks have already
fired by then.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from station_watch.alarm.sink import Sink
from station_watch.alarm.tone import default_tone_path, generate_tone_wav


def _default_runner(command) -> None:
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


class SoundSink(Sink):
    """Plays a short tone on each alarm and recovery."""

    def __init__(self, *, tone_path=None, runner=None, platform: str = sys.platform, bell=None):
        self._tone_path = Path(tone_path) if tone_path is not None else default_tone_path()
        self._runner = runner if runner is not None else _default_runner
        self._platform = platform
        self._bell = bell if bell is not None else sys.stderr

    def on_alarm(self, episode) -> None:
        self._play()

    def on_recovery(self, episode, recovered_ts: str) -> None:
        self._play()

    def _play(self) -> None:
        command = self._command()
        try:
            if command is None:
                self._bell.write("\a")
                self._bell.flush()
            else:
                self._runner(command)
        except Exception:
            pass  # a failing audio player must never delay or crash the alarm

    def _command(self):
        self._ensure_tone()
        path = str(self._tone_path)
        if self._platform == "darwin":
            return ["afplay", path]
        if self._platform.startswith("linux") and shutil.which("aplay"):
            return ["aplay", "-q", path]
        return None

    def _ensure_tone(self) -> None:
        if not self._tone_path.exists():
            generate_tone_wav(self._tone_path)


__all__ = ["SoundSink"]
