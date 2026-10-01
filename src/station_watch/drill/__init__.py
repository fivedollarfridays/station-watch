"""The ``station-watch drill`` subcommand: fault injection and the time-to-alarm table.

A drill injects the five camera faults an operator can cause and measures how fast the
real pipeline alarms and recovers. Synthetic drills render the faults into frames and
are reproducible in CI; live drills run a real camera on the bench. Both run the one
:class:`~station_watch.runner.pipeline.Runner` ``station-watch run`` uses and write one
HF2.8-format measurement file, so a drill's numbers are traceable exactly as an
evaluation's are. The CLI wiring lives in :mod:`station_watch.drill.commandline`.
"""

from __future__ import annotations

__all__: list[str] = []
