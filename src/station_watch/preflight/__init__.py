"""The ``station-watch preflight`` command: PASS or FAIL per item before going live.

Each check answers with a frozen :class:`~station_watch.preflight.result.CheckResult`
(``PASS``/``FAIL``/``WARN``/``SKIP``) and runs under a timeout that never lets it raise
(K10). :func:`~station_watch.preflight.runner.run_preflight` runs the ordered
:data:`~station_watch.preflight.runner.CHECKS` registry against one station and returns
the per-check results; the CLI prints one aligned line per check (or ``--json``) and
exits 0 only when no check is a FAIL. HF3.13 and HF3.17 consume ``CheckResult``,
``run_preflight`` and ``CHECKS`` from here.
"""

from __future__ import annotations

from station_watch.preflight.result import CheckResult
from station_watch.preflight.runner import CHECKS, run_preflight

__all__ = ["CheckResult", "CHECKS", "run_preflight"]
