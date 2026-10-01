"""The evaluation harness (K13): run a labeled clip set through the real pipeline.

``station-watch evaluate --config <yaml> --manifest <path> --out <dir>`` drives
every clip a :mod:`~station_watch.evaluate.manifest` lists through the real
Capture -> Detect -> Judge -> Alarm path and writes committed JSON measurement
files -- precision/recall per flag type and per rail-position state, the confusion
matrix, capture-to-verdict latency, time to alarm, the HF2.4 ``step_times.json``,
and the same numbers for the naive frame-difference baseline -- each stamped with
full provenance (dataset, manifest/config SHA-256, git commit, detector, date).

Honesty is the point of the module: with no labeled set it refuses to invent one
(see :mod:`~station_watch.evaluate.manifest`), and a ``--synthetic`` set is always
tagged ``dataset_kind: synthetic`` so it can never be passed off as real
performance. See :func:`station_watch.evaluate.harness.run_evaluation`.

The heavy harness (OpenCV, the real pipeline) is imported lazily via
``__getattr__`` so merely building the CLI's argument parser stays cheap.
"""

from __future__ import annotations

from station_watch.evaluate.manifest import ManifestError, NoLabeledSetError

_LAZY = {"run_evaluation", "EXIT_NO_LABELED_SET", "EXIT_MISSING_CLIP"}

__all__ = [
    "run_evaluation",
    "EXIT_NO_LABELED_SET",
    "EXIT_MISSING_CLIP",
    "ManifestError",
    "NoLabeledSetError",
]


def __getattr__(name):
    if name in _LAZY:
        from station_watch.evaluate import harness

        return getattr(harness, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
