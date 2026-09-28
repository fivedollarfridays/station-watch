"""Sinks must never stall the cycle loop and must reach the terminal at once.

The sound sink launches the platform player and returns without waiting for the
tone to finish -- a slow or wedged audio player would otherwise hold up every
cycle behind it. The screen sink flushes each line, so an alarm shows up on a
piped or redirected terminal the moment it fires, not when a buffer fills.
"""

import os
import stat
import time

from station_watch.alarm import Episode, ScreenSink, SoundSink

EPISODE = Episode(
    cause="unobservable:dark",
    kind="unobservable",
    station_id="station-1",
    label="unobservable (dark)",
    started_ts="2026-09-28T12:00:00.000000+00:00",
    frame_ids=(),
)


class _FlushCounter:
    def __init__(self):
        self.text = ""
        self.flushes = 0

    def write(self, text):
        self.text += text

    def flush(self):
        self.flushes += 1


def test_screen_sink_flushes_every_line():
    stream = _FlushCounter()
    sink = ScreenSink(stream)
    sink.on_alarm(EPISODE)
    assert "ALARM" in stream.text and stream.flushes >= 1
    before = stream.flushes
    sink.on_recovery(EPISODE, "2026-09-28T12:00:05.000000+00:00")
    assert "CLEAR" in stream.text and stream.flushes > before


def test_sound_sink_launches_the_player_without_waiting_for_it(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    started = tmp_path / "player-started"
    player = bin_dir / "afplay"
    player.write_text(f"#!/bin/sh\ntouch {started}\nsleep 5\n")
    player.chmod(player.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    sink = SoundSink(tone_path=tmp_path / "tone.wav", platform="darwin")

    began = time.monotonic()
    sink.on_alarm(EPISODE)
    elapsed = time.monotonic() - began

    assert elapsed < 1.0, f"on_alarm blocked for {elapsed:.2f}s on a 5s player"
    deadline = time.monotonic() + 3.0
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert started.exists(), "the player must actually have been launched"
