"""Reduce a soak's samples to a pass/fail against the required ``soak:`` thresholds.

Every threshold lives in a required ``soak:`` config section; a missing key fails
loud naming the dotted path (K9), via :func:`load_soak_thresholds`. The reduction,
:func:`soak_verdict`, judges five things over the *post-warmup* samples:

* **RSS growth** per child -- the least-squares slope of resident KB against elapsed
  seconds, reported as MB/h; any child above ``max_rss_growth_mb_per_h`` fails.
* **Log growth** -- the slope of the Log's bytes, reported as MB/h, against
  ``max_log_mb_per_h``.
* **Board render** -- the p95 of the successful render times against
  ``max_board_render_s``; *any* failed render fails the soak outright.
* **Verdict cadence** -- the largest stretch of elapsed time with no new verdict ts
  against ``max_verdict_gap_s`` (plus verdicts-per-minute, reported).
* **False flags** -- faults raised while the source is known-normal work, against
  ``max_false_flags``.

Fewer than three post-warmup samples is ``insufficient_samples``, never a pass: a
soak that barely ran has not earned a clean bill of health.
"""

from __future__ import annotations

from station_watch.runner.startup import StartupError

SOAK_KEYS = (
    "max_rss_growth_mb_per_h",
    "max_log_mb_per_h",
    "max_board_render_s",
    "max_verdict_gap_s",
    "max_false_flags",
    "warmup_s",
)
_MIN_SAMPLES = 3
_KB_PER_MB = 1024.0
_BYTES_PER_MB = 1024.0 * 1024.0
_SECONDS_PER_HOUR = 3600.0


def load_soak_thresholds(config) -> dict:
    """Return every ``soak.*`` threshold as a float, or raise ``StartupError`` (K9)."""
    soak = config.get("soak")
    if not isinstance(soak, dict):
        raise StartupError("missing config key: soak")
    thresholds = {}
    for key in SOAK_KEYS:
        if key not in soak:
            raise StartupError(f"missing config key: soak.{key}")
        thresholds[key] = float(soak[key])
    return thresholds


def _slope(points: list[tuple[float, float]]) -> float:
    """Least-squares slope (dy/dx) of ``points``; 0.0 for <2 points or no x spread."""
    n = len(points)
    if n < 2:
        return 0.0
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    denom = sum((x - mean_x) ** 2 for x, _ in points)
    if denom == 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / denom


def _p95(values: list[float]) -> float:
    """The 95th percentile of ``values`` (nearest-rank); 0.0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(0.95 * (len(ordered) - 1))))
    return ordered[rank]


def _rss_growth(samples: list, name: str) -> float:
    """MB/h of RSS growth for ``name`` over the samples that saw it alive."""
    points = [
        (s.elapsed_s, float(s.rss_kb[name])) for s in samples if s.rss_kb.get(name) is not None
    ]
    return _slope(points) * _SECONDS_PER_HOUR / _KB_PER_MB


def _max_verdict_gap(samples: list) -> float:
    """The longest elapsed stretch with no new verdict ts across ``samples``.

    While the newest verdict ts is unchanged between samples, no verdict arrived; the
    span from the last change (or the start) to the next change -- and the trailing
    span to the final sample -- is a candidate gap. The largest is the worst stall.
    """
    gap = 0.0
    anchor = samples[0].elapsed_s
    previous = samples[0].newest_verdict_ts
    for sample in samples[1:]:
        if sample.newest_verdict_ts != previous:
            gap = max(gap, sample.elapsed_s - anchor)
            anchor = sample.elapsed_s
            previous = sample.newest_verdict_ts
    return max(gap, samples[-1].elapsed_s - anchor)


def _verdicts_per_minute(samples: list) -> float:
    span = samples[-1].elapsed_s - samples[0].elapsed_s
    if span <= 0:
        return 0.0
    return (samples[-1].verdicts - samples[0].verdicts) / (span / 60.0)


def _process_names(samples: list) -> list[str]:
    names: list[str] = []
    for sample in samples:
        for name in sample.rss_kb:
            if name not in names:
                names.append(name)
    return names


def _check_rss(samples, thresholds, metrics, failures) -> None:
    limit = thresholds["max_rss_growth_mb_per_h"]
    # Nested per process, so ``metrics.rss_growth_mb_per_h.run`` is a dotted claim path.
    per_process = metrics.setdefault("rss_growth_mb_per_h", {})
    for name in _process_names(samples):
        growth = _rss_growth(samples, name)
        per_process[name] = growth
        if growth > limit:
            failures.append(f"RSS growth for {name} {growth:.1f} MB/h exceeds max {limit:.1f} MB/h")


def _check_log(samples, thresholds, metrics, failures) -> None:
    limit = thresholds["max_log_mb_per_h"]
    growth = _slope([(s.elapsed_s, float(s.log_bytes)) for s in samples])
    growth_mb_per_h = growth * _SECONDS_PER_HOUR / _BYTES_PER_MB
    metrics["log_growth_mb_per_h"] = growth_mb_per_h
    if growth_mb_per_h > limit:
        failures.append(f"Log growth {growth_mb_per_h:.1f} MB/h exceeds max {limit:.1f} MB/h")


def _check_board(samples, thresholds, metrics, failures) -> None:
    limit = thresholds["max_board_render_s"]
    renders = [s.board_render_s for s in samples if s.board_render_s is not None]
    board_errors = [s.board_error for s in samples if s.board_error is not None]
    p95 = _p95(renders)
    metrics["board_render_p95_s"] = p95
    metrics["board_render_failures"] = len(board_errors)
    if board_errors:
        failures.append(f"Board render failed: {board_errors[0]}")
    elif p95 > limit:
        failures.append(f"Board render p95 {p95:.3f}s exceeds max {limit:.3f}s")


def _check_cadence(samples, thresholds, metrics, failures) -> None:
    gap = _max_verdict_gap(samples)
    metrics["max_verdict_gap_s"] = gap
    metrics["verdicts_per_minute"] = _verdicts_per_minute(samples)
    if gap > thresholds["max_verdict_gap_s"]:
        failures.append(
            f"verdict gap {gap:.1f}s exceeds max {thresholds['max_verdict_gap_s']:.1f}s"
        )


def _check_false_flags(thresholds, false_flags, metrics, failures) -> None:
    limit = thresholds["max_false_flags"]
    metrics["false_flags"] = false_flags
    if false_flags > limit:
        failures.append(f"false flags {false_flags} exceeds max {int(limit)}")


def soak_verdict(samples, thresholds: dict, *, false_flags: int) -> dict:
    """Reduce ``samples`` to ``{status, failures, metrics}`` against ``thresholds``."""
    warmup = thresholds["warmup_s"]
    post = [s for s in samples if s.elapsed_s >= warmup]
    metrics: dict = {"post_warmup_samples": len(post)}
    if len(post) < _MIN_SAMPLES:
        return {"status": "insufficient_samples", "failures": [], "metrics": metrics}
    failures: list[str] = []
    _check_rss(post, thresholds, metrics, failures)
    _check_log(post, thresholds, metrics, failures)
    _check_board(post, thresholds, metrics, failures)
    _check_cadence(post, thresholds, metrics, failures)
    _check_false_flags(thresholds, false_flags, metrics, failures)
    return {
        "status": "fail" if failures else "pass",
        "failures": failures,
        "metrics": metrics,
    }


__all__ = ["SOAK_KEYS", "load_soak_thresholds", "soak_verdict"]
