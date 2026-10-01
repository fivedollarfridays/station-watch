"""Pure metric math: precision/recall, confusion matrices, latency percentiles.

These functions take already-extracted per-clip *outcomes* (see
:mod:`station_watch.evaluate.extract`) and reduce them to the numbers a
measurement file reports. They are deliberately free of any Log, record or
OpenCV dependency so the proving test can recompute every number by hand.

* Per *flag type* (``missing_part``, ``stalled``, ``keepout_entry``,
  ``cycle_time_creep``) the unit is one clip: a clip whose ground truth contains
  the flag is a positive, and the pipeline "raised" it when any episode of that
  cause opened. ``precision = tp/(tp+fp)``; ``recall = tp/(tp+fn)`` (a false
  negative is a *miss*); both are ``None`` when their denominator is zero, which
  is honest about "no evidence either way" rather than a misleading ``1.0``.
* Per *rail-position state* (``present`` / ``absent`` / ``unknown``) the unit is
  one labeled interval: the confusion matrix counts ground-truth state against
  the state the detector read, with a ``none`` column for intervals the detector
  never read (the misses). Per-state precision/recall fall out of that matrix.
* Latency is the span from a cited frame's capture ``ts`` to the verdict ``ts``;
  we report the median and p95 over every such span.
"""

from __future__ import annotations

FLAG_TYPES = ("missing_part", "stalled", "keepout_entry", "cycle_time_creep")
RAIL_STATES = ("present", "absent", "unknown")
_MISS = "none"


def precision_recall(tp: int, fp: int, fn: int) -> tuple[float | None, float | None]:
    """Precision and recall from counts; ``None`` where the denominator is zero."""
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return precision, recall


def _flag_counts(outcomes, flag: str) -> dict:
    tp = fp = fn = tn = 0
    for outcome in outcomes:
        gt = flag in outcome.gt_flags
        pred = flag in outcome.pred_flags
        tp += gt and pred
        fp += (not gt) and pred
        fn += gt and (not pred)
        tn += (not gt) and (not pred)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def flag_metrics(outcomes) -> dict:
    """Per-flag-type ``{tp, fp, fn, tn, precision, recall}`` over the clips."""
    out: dict = {}
    for flag in FLAG_TYPES:
        counts = _flag_counts(outcomes, flag)
        precision, recall = precision_recall(counts["tp"], counts["fp"], counts["fn"])
        out[flag] = {**counts, "precision": precision, "recall": recall}
    return out


def rail_confusion(outcomes) -> dict:
    """Confusion matrix of rail-position state: ``{gt_state: {pred_state: count}}``.

    The ``none`` prediction column holds the misses -- labeled intervals the
    detector produced no reading for.
    """
    cols = (*RAIL_STATES, _MISS)
    matrix = {gt: {pred: 0 for pred in cols} for gt in RAIL_STATES}
    for outcome in outcomes:
        for gt_state, pred_state in outcome.rail_intervals:
            matrix[gt_state][pred_state] += 1
    return matrix


def rail_state_metrics(confusion: dict) -> dict:
    """Per-state precision/recall/support derived from the confusion matrix."""
    out: dict = {}
    for state in RAIL_STATES:
        tp = confusion[state][state]
        fn = sum(count for pred, count in confusion[state].items() if pred != state)
        fp = sum(confusion[gt][state] for gt in RAIL_STATES if gt != state)
        precision, recall = precision_recall(tp, fp, fn)
        out[state] = {"precision": precision, "recall": recall, "support": tp + fn}
    return out


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile (as ``numpy.percentile`` default), 0.0 if empty."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return float(ordered[low] + (ordered[high] - ordered[low]) * (rank - low))


def latency_metrics(latencies: list[float]) -> dict:
    """Count, median and p95 (seconds) of the capture-to-verdict latencies."""
    return {
        "count": len(latencies),
        "median_s": round(_percentile(latencies, 50.0), 6),
        "p95_s": round(_percentile(latencies, 95.0), 6),
    }


def _sessions(outcomes) -> list[str]:
    seen: list[str] = []
    for outcome in outcomes:
        if outcome.session not in seen:
            seen.append(outcome.session)
    return seen


def _core(outcomes) -> dict:
    confusion = rail_confusion(outcomes)
    return {
        **flag_metrics(outcomes),  # missing_part / stalled / keepout_entry / cycle_time_creep
        "rail_position_states": rail_state_metrics(confusion),
        "confusion_matrix": confusion,
        "latency": latency_metrics([lat for o in outcomes for lat in o.latencies]),
        "time_to_alarm": [t for o in outcomes for t in o.time_to_alarm],
    }


def assemble_metrics(outcomes) -> dict:
    """The full ``metrics`` block: overall numbers plus a per-session breakdown.

    Per-session results make a train / test split by recording session visible
    (never by adjacent frames). Metric keys are dotted paths under ``metrics`` --
    e.g. ``missing_part.precision`` and ``latency.p95_s`` (consumed by the README claims test).
    """
    return {
        **_core(outcomes),
        "per_session": {
            session: _core([o for o in outcomes if o.session == session])
            for session in _sessions(outcomes)
        },
    }


__all__ = [
    "FLAG_TYPES",
    "RAIL_STATES",
    "precision_recall",
    "flag_metrics",
    "rail_confusion",
    "rail_state_metrics",
    "latency_metrics",
    "assemble_metrics",
]
