"""Thin re-export: the HF2.1 panel-bench renderer now lives in the package.

Moved to :mod:`station_watch.synth.station` in HF3A.1 so src-side tools (the fault
drill, the physics scripts, ``evaluate --synthetic``) share the one renderer. This
shim keeps ``from helpers.synth_station import write_synth_station_clip`` working
unchanged for every HF2 test.
"""

from __future__ import annotations

from station_watch.synth.station import write_synth_station_clip

__all__ = ["write_synth_station_clip"]
