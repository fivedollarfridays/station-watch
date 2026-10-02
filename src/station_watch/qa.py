"""Session QA: a per-session unknown budget a mostly blind session cannot pass.

Over one session's Log (read-only through :class:`~station_watch.board.reader.LogReader`)
:func:`session_qa` time-weights each ``Verdict``: it holds from its ``ts`` until the
next verdict's ``ts``, and the last one holds until the newest ``cycle`` row's ``ts``.
A gap between verdicts longer than ``watchdog.cycle_window_s`` is unobservable for its
whole length -- missing data is not healthy (K1) -- and so is any ``unobservable``
verdict. Per required slot and keep-out zone it reports the fraction of *observed*
time whose newest observation was ``part_unknown`` / ``zone_unknown``.

A session passes only when its unobservable fraction is within
``qa.max_unknown_fraction`` and every per-target fraction is within
``qa.max_target_unknown_fraction`` (when that key is set). The ``station-watch qa``
command exits 0 on pass, 1 on fail, 2 on a startup error; ``station-watch run`` is
unaffected by the optional ``qa:`` section. The Log is only ever read.
"""

from __future__ import annotations

from dataclasses import dataclass

from station_watch.board.reader import BoardLogError, LogReader
from station_watch.clock import parse_iso
from station_watch.records import ObservationKind, VerdictState

QA_UNKNOWN_KEY = "qa.max_unknown_fraction"
QA_TARGET_KEY = "qa.max_target_unknown_fraction"

# SQLite reads LIMIT -1 as "every row": a session's whole verdict/observation history.
_ALL = -1
_UNKNOWN_KINDS = (ObservationKind.PART_UNKNOWN, ObservationKind.ZONE_UNKNOWN)
_OBSERVABLE_STATES = (VerdictState.HEALTHY, VerdictState.FAULT)


@dataclass(frozen=True)
class SessionQA:
    """One session's QA result (consumed by HF3.6, HF3.7 and HF3.10)."""

    status: str  # "pass" or "fail"
    reason: str
    session_s: float
    observable_s: float
    unobservable_fraction: float
    target_unknown_fraction: dict
    verdicts: int
    max_unknown_fraction: float
    max_target_unknown_fraction: float | None = None


def _to_epoch(ts: str) -> float:
    return parse_iso(ts).timestamp()


def validate_qa_config(config) -> tuple[float, float | None]:
    """Return ``(max_unknown_fraction, max_target_unknown_fraction)`` or raise (K9)."""
    qa = config.get("qa")
    if not isinstance(qa, dict) or "max_unknown_fraction" not in qa:
        raise KeyError(f"missing config key: {QA_UNKNOWN_KEY}")
    target = qa.get("max_target_unknown_fraction")
    return float(qa["max_unknown_fraction"]), (None if target is None else float(target))


def _fail(reason: str, max_unknown: float, max_target: float | None) -> SessionQA:
    return SessionQA(
        status="fail",
        reason=reason,
        session_s=0.0,
        observable_s=0.0,
        unobservable_fraction=1.0,
        target_unknown_fraction={},
        verdicts=0,
        max_unknown_fraction=max_unknown,
        max_target_unknown_fraction=max_target,
    )


def _segments(times, states, end, cycle_window_s):
    """``(start, end, observable)`` per verdict: its hold, and whether it was observed.

    A hold longer than ``cycle_window_s`` is a gap: unobservable for its whole length
    whatever the verdict said (missing data is not healthy, K1).
    """
    segments = []
    for i, state in enumerate(states):
        seg_start = times[i]
        seg_end = times[i + 1] if i + 1 < len(times) else end
        if seg_end < seg_start:
            seg_end = seg_start
        gap = (seg_end - seg_start) > cycle_window_s
        segments.append((seg_start, seg_end, (not gap) and state in _OBSERVABLE_STATES))
    return segments


def _unknown_overlap(start: float, stop: float, events) -> float:
    """Time within ``[start, stop)`` whose newest observation (ts <= t) is unknown."""
    if stop <= start or not events:
        return 0.0
    bounds = [start] + [t for t, _ in events if start < t < stop] + [stop]
    total = 0.0
    for a, b in zip(bounds, bounds[1:], strict=False):
        active = None
        for t, unknown in events:
            if t <= a:
                active = unknown
            else:
                break
        if active:
            total += b - a
    return total


def _target_fraction(target, by_target, observable_segments, observable_s) -> float:
    if observable_s <= 0:
        return 0.0
    events = by_target.get(target, [])
    unknown = sum(_unknown_overlap(s, e, events) for s, e in observable_segments)
    return unknown / observable_s


def _collect_observations(reader, targets):
    by_target = {t: [] for t in targets}
    if targets:
        for obs in reader.iter_newest("observation", limit=_ALL):
            if obs.target in by_target:
                by_target[obs.target].append((_to_epoch(obs.ts), obs.kind in _UNKNOWN_KINDS))
    for events in by_target.values():
        events.sort()
    return by_target


def session_qa(config, log_path) -> SessionQA:
    """Score one session's Log; never raises for a missing or undecodable Log."""
    max_unknown, max_target = validate_qa_config(config)
    cycle_window_s = float(config["watchdog"]["cycle_window_s"])
    targets = list(config.get("required_slots") or []) + list(config.get("keepout_zones") or [])
    try:
        with LogReader(log_path) as reader:
            verdicts = list(reader.iter_newest("verdict", limit=_ALL))
            newest_cycle = reader.newest("cycle")
            by_target = _collect_observations(reader, targets)
    except BoardLogError as exc:
        return _fail(str(exc), max_unknown, max_target)

    verdicts.reverse()  # iter_newest is newest-first; walk oldest-first
    if not verdicts:
        return _fail("no verdicts", max_unknown, max_target)

    times = [_to_epoch(v.ts) for v in verdicts]
    states = [v.state for v in verdicts]
    end = times[-1]
    if newest_cycle is not None:
        end = max(end, _to_epoch(newest_cycle.ts))
    session_s = end - times[0]

    segments = _segments(times, states, end, cycle_window_s)
    observable_segments = [(s, e) for s, e, obs in segments if obs]
    observable_s = sum(e - s for s, e in observable_segments)
    unobservable_fraction = 0.0 if session_s <= 0 else (session_s - observable_s) / session_s

    target_unknown_fraction = {
        t: _target_fraction(t, by_target, observable_segments, observable_s) for t in targets
    }
    return _verdict(
        max_unknown,
        max_target,
        session_s,
        observable_s,
        unobservable_fraction,
        target_unknown_fraction,
        len(verdicts),
    )


def _verdict(
    max_unknown, max_target, session_s, observable_s, unobservable_fraction, targets, n
) -> SessionQA:
    reasons = []
    if unobservable_fraction > max_unknown:
        reasons.append(
            f"unobservable fraction {unobservable_fraction:.3f} exceeds max {max_unknown}"
        )
    if max_target is not None:
        for target, frac in sorted(targets.items()):
            if frac > max_target:
                reasons.append(
                    f"target {target} unknown fraction {frac:.3f} exceeds max {max_target}"
                )
    return SessionQA(
        status="fail" if reasons else "pass",
        reason="; ".join(reasons) if reasons else "pass",
        session_s=session_s,
        observable_s=observable_s,
        unobservable_fraction=unobservable_fraction,
        target_unknown_fraction=targets,
        verdicts=n,
        max_unknown_fraction=max_unknown,
        max_target_unknown_fraction=max_target,
    )


def add_parser(sub) -> None:
    """Add the ``qa`` subparser and its arguments to ``sub``."""
    qa = sub.add_parser(
        "qa",
        help="score one session's Log against a per-session unknown budget",
        description="Read one session's Log (read-only) and time-weight its verdicts into an "
        "unobservable fraction plus per-slot/zone unknown fractions; pass only within the qa: "
        "thresholds. Exit 0 on pass, 1 on fail, 2 on a startup error. The Log is never written.",
    )
    qa.add_argument("--config", required=True, help="station config YAML (needs a qa: section)")
    qa.add_argument("--log", required=True, help="path to the session's append-only Log")
    qa.add_argument("--out", help="write the result as a measurement file (needs --dataset-kind)")
    qa.add_argument(
        "--dataset-kind",
        choices=["real", "synthetic"],
        help="which measurement tree --out must land in (real -> measurements/v1|v2, "
        "synthetic -> measurements/synthetic)",
    )
    qa.add_argument(
        "--force-out", action="store_true", help="override --out confinement (one warning line)"
    )


def handle(args) -> int:
    """Run the ``qa`` subcommand from parsed ``args``; return an exit code."""
    from station_watch.qa_report import run_qa

    return run_qa(
        config_path=args.config,
        log_path=args.log,
        out=args.out,
        dataset_kind=args.dataset_kind,
        force_out=args.force_out,
    )


__all__ = [
    "SessionQA",
    "session_qa",
    "validate_qa_config",
    "add_parser",
    "handle",
    "QA_UNKNOWN_KEY",
]
