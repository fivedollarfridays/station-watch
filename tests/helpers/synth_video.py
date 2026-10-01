"""Thin re-export: the HF1 synthetic-clip renderer now lives in the package.

Moved to :mod:`station_watch.synth.video` in HF3A.1 so src-side tools share the one
renderer. This shim keeps ``from helpers.synth_video import ...`` working unchanged
for every HF1/HF2 test (including the private ``_write_frames`` a few of them use).
"""

from __future__ import annotations

from station_watch.synth.video import (
    _base_scene,
    _compose_frame,
    _in_window,
    _marker_tile,
    _write_frames,
    _write_png_sequence,
    write_synth_clip,
)

__all__ = [
    "_base_scene",
    "_compose_frame",
    "_in_window",
    "_marker_tile",
    "_write_frames",
    "_write_png_sequence",
    "write_synth_clip",
]
