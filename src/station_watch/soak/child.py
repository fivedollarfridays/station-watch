"""The soak's runner child: the real pipeline fed by a looping normal-work source.

A soak needs a ``run`` child that never ends -- a recorded clip would, so a soak of
hours could not use one. :func:`run_child` builds the *same* :class:`Runner` that
``station-watch run`` wires (through :func:`build_context` with an injected
``source``, exactly as the drill does), but feeds it a :class:`SyntheticSource`
looping :data:`NORMAL_WORK_SCRIPT`: both rail parts present every frame, short motion
bursts (well under any stall window), no faults. Any fault such a run raises is a
false flag the soak counts against the pipeline.

It is reached through a real entry -- ``station-watch soak --child`` --
:mod:`station_watch.soak.commandline` importing this module, never a bare
``python -m station_watch.soak.child`` string, so the import-graph orphan check
(``test_watchdog``) sees it live.
"""

from __future__ import annotations

import signal
import sys

from station_watch.run import new_run_id
from station_watch.runner.pipeline import Runner
from station_watch.runner.startup import StartupError, build_context
from station_watch.synth.source import SyntheticSource

# Frames per second the looping source paces to: brisk enough that a frame always
# lands inside a realistic liveness window, modest enough not to burn the box for
# hours. Pacing is wall-clock (unlike the drill's frame clock) -- a soak measures
# real elapsed hours, so its time must be the real clock.
CHILD_FPS = 10.0

_PRESENT = {"rail_pos_1": "present", "rail_pos_2": "present"}
# Normal work: both parts present throughout, with a short motion burst every few
# frames. The bursts are a handful of frames (a fraction of a second at CHILD_FPS),
# far shorter than any stall window, so a motion-tracking station never reads the
# quiet between them as a stall -- and a still-scene station never faults either.
NORMAL_WORK_SCRIPT = [
    {"positions": _PRESENT},
    {"positions": _PRESENT},
    {"positions": _PRESENT, "motion": True},
    {"positions": _PRESENT, "motion": True},
    {"positions": _PRESENT, "motion": True},
    {"positions": _PRESENT},
    {"positions": _PRESENT},
    {"positions": _PRESENT},
]


def run_child(args) -> int:
    """Run the looping normal-work pipeline until SIGTERM; 0 on a clean shutdown."""
    source = SyntheticSource(fps=CHILD_FPS, script=NORMAL_WORK_SCRIPT, loop=True)
    try:
        context = build_context(
            config_path=args.config,
            source_spec=None,
            log_path=args.log,
            alarm_record=None,
            source=source,
        )
    except StartupError as exc:
        print(f"station-watch: {exc}", file=sys.stderr)
        return 1
    runner = Runner(context, run_id=new_run_id())
    # The supervisor stops its children with SIGTERM; finish the current cycle and
    # shut down cleanly, exactly as `station-watch run` does under a service manager.
    previous = signal.signal(signal.SIGTERM, lambda _sig, _frame: runner.stop())
    try:
        runner.run()
    finally:
        signal.signal(signal.SIGTERM, previous)
        context.log.close()
    return 0


__all__ = ["run_child", "NORMAL_WORK_SCRIPT", "CHILD_FPS"]
