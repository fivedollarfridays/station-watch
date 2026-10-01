"""Synthetic station frames, now importable from the package (HF3A.1).

The HF1/HF2 renderers used to live under ``tests/helpers``; they moved here so the
src-side tools -- the fault drill, the physics scripts, and ``evaluate
--synthetic`` -- share the one renderer. ``tests/helpers/synth_video.py`` and
``tests/helpers/synth_station.py`` remain thin re-exports so every HF1 and HF2 test
keeps importing them unchanged.

* :func:`write_synth_clip` -- the HF1 static-scene clip writer.
* :func:`write_synth_station_clip` -- the HF2.1 panel-bench clip writer + ground truth.
* :class:`SyntheticSource` -- a live-paced source over the HF2.1 renderer.

The submodules pull in OpenCV, so they are imported lazily to keep merely importing
the package cheap (as :mod:`station_watch.evaluate` does for its heavy harness).
"""

from __future__ import annotations

__all__ = ["write_synth_clip", "write_synth_station_clip", "SyntheticSource"]


def __getattr__(name: str):
    if name == "write_synth_clip":
        from station_watch.synth.video import write_synth_clip

        return write_synth_clip
    if name == "write_synth_station_clip":
        from station_watch.synth.station import write_synth_station_clip

        return write_synth_station_clip
    if name == "SyntheticSource":
        from station_watch.synth.source import SyntheticSource

        return SyntheticSource
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
