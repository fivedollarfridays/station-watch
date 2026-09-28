"""A tiny WAV tone generated with the standard library -- no third-party audio.

The alarm's sound sink plays a short beep. Rather than depend on an audio
library or ship a binary of unknown provenance, the tone is a few thousand
16-bit samples of a sine wave written by :func:`generate_tone_wav` using the
standard-library ``wave`` module. The bundled copy lives beside this module and
is (re)generated on demand if it is ever missing.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

_SAMPLE_RATE = 8000
_DURATION_S = 0.2
_FREQUENCY_HZ = 880.0
_AMPLITUDE = 0.6
_TONE_FILENAME = "alarm_tone.wav"


def default_tone_path() -> Path:
    """Path to the tone bundled beside this module."""
    return Path(__file__).resolve().parent / "assets" / _TONE_FILENAME


def generate_tone_wav(path: str | Path) -> Path:
    """Write a short mono sine-wave WAV to ``path`` (creating parents)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = int(_SAMPLE_RATE * _DURATION_S)
    peak = int(_AMPLITUDE * 32767)
    frames = bytearray()
    for i in range(count):
        sample = int(peak * math.sin(2.0 * math.pi * _FREQUENCY_HZ * i / _SAMPLE_RATE))
        frames += struct.pack("<h", sample)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(_SAMPLE_RATE)
        wav.writeframes(bytes(frames))
    return path


__all__ = ["default_tone_path", "generate_tone_wav"]
