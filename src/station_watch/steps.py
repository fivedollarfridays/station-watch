"""Measured step times: the stall window's source, and the file HF2.8 writes.

A *step* is one unit of work in the station zone: a contiguous run of ``motion``
observations, closed (bounded) by the ``no_motion`` that ends it. Its duration is
the span from motion onset to that cessation -- motion starting, then stopping --
which is what a cycle takes when the line is running.

* :func:`step_durations` pulls the closed steps out of a flat observation list
  (ignoring every other kind and keeping targets apart), newest last.
* :func:`step_stats` reduces their durations to ``count`` / ``p50_s`` / ``p95_s``.
* :func:`write_step_times` / :func:`read_step_times` round-trip the committed
  ``step_times.json`` -- ``{"provenance": {...}, "metrics": {count, p50_s, p95_s}}``
  -- which HF2.8's evaluation harness produces and HF2.4 reads.
* :func:`resolve_stall_window` turns a config into the Judge's stall window plus a
  human source string: the measured ``p95 + grace_s`` when ``detect.step_times_path``
  points at a real file, else ``takt_s + grace_s``. A configured path that does not
  exist (or will not parse) raises :class:`StepTimesError` so the runner fails loud
  at startup, naming the file (K9).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from station_watch.clock import parse_iso
from station_watch.records import Observation, ObservationKind

_MOTION_KINDS = (ObservationKind.MOTION, ObservationKind.NO_MOTION)


class StepTimesError(ValueError):
    """A configured ``step_times_path`` is missing or not a valid measurement file."""


@dataclass(frozen=True)
class Step:
    """One step: motion onset (start) to the no_motion that ended it (end)."""

    target: str
    start_frame_id: int
    end_frame_id: int
    start_ts: str
    end_ts: str
    duration_s: float


def _order_key(obs: Observation) -> tuple[str, int]:
    return (obs.ts, obs.frame_id)


def _step(target: str, start: Observation, end: Observation) -> Step:
    duration = (parse_iso(end.ts) - parse_iso(start.ts)).total_seconds()
    return Step(target, start.frame_id, end.frame_id, start.ts, end.ts, duration)


def step_durations(observations) -> list[Step]:
    """Closed motion runs (a motion onset bounded by a no_motion), per station zone."""
    by_target: dict[str, list[Observation]] = defaultdict(list)
    for obs in observations:
        if obs.kind in _MOTION_KINDS:
            by_target[obs.target].append(obs)
    steps: list[Step] = []
    for target, zone_obs in by_target.items():
        run_start: Observation | None = None
        for obs in sorted(zone_obs, key=_order_key):
            if obs.kind is ObservationKind.MOTION:
                if run_start is None:
                    run_start = obs
            elif run_start is not None:  # NO_MOTION closes the run
                steps.append(_step(target, run_start, obs))
                run_start = None
    return sorted(steps, key=lambda s: (s.start_ts, s.start_frame_id))


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile (as ``numpy.percentile`` default), 0.0 if empty."""
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    rank = (pct / 100.0) * (len(values) - 1)
    low = int(rank)
    high = min(low + 1, len(values) - 1)
    return float(values[low] + (values[high] - values[low]) * (rank - low))


def step_stats(durations) -> dict:
    """Count, p50 and p95 (seconds) of the step durations; zeros when there are none."""
    values = sorted(step.duration_s for step in durations)
    return {
        "count": len(values),
        "p50_s": round(_percentile(values, 50.0), 4),
        "p95_s": round(_percentile(values, 95.0), 4),
    }


def write_step_times(path: str | Path, stats: dict, provenance: dict) -> None:
    """Write the committed ``step_times.json`` (HF2.8's producer of the file)."""
    payload = {
        "provenance": dict(provenance),
        "metrics": {
            "count": int(stats["count"]),
            "p50_s": float(stats["p50_s"]),
            "p95_s": float(stats["p95_s"]),
        },
    }
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def read_step_times(path: str | Path) -> dict:
    """Read a ``step_times.json`` and return its ``metrics`` mapping."""
    data = json.loads(Path(path).read_text())
    metrics = data["metrics"]
    # Touch the keys the stall window needs so a malformed file fails here, named.
    _ = metrics["count"], metrics["p50_s"], metrics["p95_s"]
    return metrics


def resolve_stall_window(config) -> tuple[float, str]:
    """The Judge's stall window and a human source string, from the config.

    ``p95 + grace_s`` when ``detect.step_times_path`` names a real file, else
    ``takt_s + grace_s``. A configured path that is absent or unparseable raises
    :class:`StepTimesError` (the runner turns it into a loud startup failure).
    """
    grace = config.grace_s
    path = config.detect.get("step_times_path")
    if not path:
        return config.takt_s + grace, "configured takt; no measured step times"
    if not Path(path).exists():
        raise StepTimesError(f"detect.step_times_path does not exist: {path}")
    try:
        p95 = float(read_step_times(path)["p95_s"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise StepTimesError(
            f"detect.step_times_path is not a valid step_times file ({path}): {exc}"
        ) from exc
    return p95 + grace, f"measured step times {path} (p95 {p95:.1f} s)"


__all__ = [
    "Step",
    "StepTimesError",
    "step_durations",
    "step_stats",
    "write_step_times",
    "read_step_times",
    "resolve_stall_window",
]
