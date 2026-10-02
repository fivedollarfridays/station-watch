"""`station-watch soak`: run the real pipeline for hours and judge it for leaks.

The soak starts three real child subcommands against one Log -- ``run`` (through
:data:`runner_argv`, the seam HF3.11 swaps for its synthetic loop), ``watchdog``
and ``board`` -- samples them every ``--sample-s`` (:mod:`station_watch.soak.sampler`),
reduces the samples against the required ``soak:`` thresholds
(:mod:`station_watch.soak.verdict`) and HF3.3's session QA, and writes a measurement
file plus a markdown table (:mod:`station_watch.soak.supervisor`). It exits 0 only
when nothing grew past its limit and every child stayed up.
"""

from __future__ import annotations

__all__: list[str] = []
