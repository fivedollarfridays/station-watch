"""Turn one clip's run (records) plus its ground truth into a scored outcome.

:class:`ClipRun` is what the harness collects after driving a clip through the
real pipeline: the verdicts, observations, alarm episodes and frame rows it
produced. :func:`extract_outcome` pairs that with a :class:`ClipLabel` and
reduces it to a :class:`ClipOutcome` -- the small, record-free structure
:mod:`station_watch.evaluate.metrics` aggregates. Keeping extraction separate
from the metric math lets the proving test build outcomes by hand and check every
number exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from station_watch.clock import parse_iso
from station_watch.evaluate.metrics import FLAG_TYPES
from station_watch.records import ObservationKind, VerdictState

_OBS_STATE = {
    ObservationKind.PART_PRESENT: "present",
    ObservationKind.PART_ABSENT: "absent",
    ObservationKind.PART_UNKNOWN: "unknown",
}
_GT_STATE_ALIASES = {"not_seated": "absent", "occluded": "unknown", "seated": "present"}


@dataclass(frozen=True)
class ClipRun:
    """Everything one clip produced on the real pipeline path."""

    session: str
    rel_path: str
    verdicts: list = field(default_factory=list)
    observations: list = field(default_factory=list)
    alarms: list = field(default_factory=list)  # RecordSink alarm dicts
    frames: list = field(default_factory=list)


@dataclass(frozen=True)
class ClipOutcome:
    """A scored clip: flag hits/misses, rail-state pairs, latencies, times-to-alarm."""

    session: str
    gt_flags: set
    pred_flags: set
    rail_intervals: list  # (gt_state, pred_state) pairs
    latencies: list  # capture-ts -> verdict-ts seconds
    time_to_alarm: list  # {reason, clip, start_frame, seconds|None}


def _gt_flags(label) -> set:
    flags: set = set()
    # The component-level ``missing_part`` flag is about rail positions, not details: a
    # detail target (dotted ``<pid>.<kind>``) reads present/absent into the rail
    # confusion matrix (HF3.16), and whether a missing detail *faults* is governed by
    # required_slots at run time, not by this clip-level flag. So an absent detail never
    # by itself sets the missing_part flag here.
    if any(_norm_state(p["state"]) == "absent" for p in label.positions if "." not in p["target"]):
        flags.add("missing_part")
    if label.stalls:
        flags.add("stalled")
    if label.keepouts:
        flags.add("keepout_entry")
    if label.creeps:
        flags.add("cycle_time_creep")
    return flags


def _pred_flags(run) -> set:
    flags: set = set()
    for alarm in run.alarms:
        if alarm.get("event") != "alarm":
            continue
        flag = str(alarm.get("cause", "")).split(":", 1)[0]
        if flag in FLAG_TYPES:
            flags.add(flag)
    return flags


def _norm_state(state: str) -> str:
    return _GT_STATE_ALIASES.get(state, state)


def _rail_intervals(label, run) -> list:
    by_target: dict[str, list] = {}
    for obs in run.observations:
        if obs.kind in _OBS_STATE:
            by_target.setdefault(obs.target, []).append(obs)
    pairs = []
    for interval in label.positions:
        pairs.append((_norm_state(interval["state"]), _predicted_state(by_target, interval)))
    return pairs


def _predicted_state(by_target, interval) -> str:
    start, end = interval["start_frame"], interval["end_frame"]
    in_window = [
        obs for obs in by_target.get(interval["target"], []) if start <= obs.frame_id <= end
    ]
    if not in_window:
        return "none"
    latest = max(in_window, key=lambda obs: (obs.ts, obs.frame_id))
    return _OBS_STATE[latest.kind]


def _frame_ts(run) -> dict:
    return {frame.frame_id: frame.ts for frame in run.frames}


def _latencies(run, frame_ts) -> list:
    out = []
    for verdict in run.verdicts:
        if verdict.state is not VerdictState.FAULT:
            continue
        for fault in verdict.faults:
            cited = [frame_ts[fid] for fid in fault.frame_ids if fid in frame_ts]
            if not cited:
                continue
            seconds = (parse_iso(verdict.ts) - parse_iso(min(cited))).total_seconds()
            out.append(seconds)
    return out


def _time_to_alarm(label, run, frame_ts) -> list:
    blind_alarms = [
        a
        for a in run.alarms
        if a.get("event") == "alarm" and str(a.get("cause", "")).startswith("unobservable:")
    ]
    out = []
    for fault in label.camera_faults:
        start = fault["start_frame"]
        t0 = frame_ts.get(start)
        seconds = None
        if t0 is not None:
            later = [a for a in blind_alarms if a["started_ts"] >= t0]
            if later:
                first = min(later, key=lambda a: a["started_ts"])
                seconds = (parse_iso(first["started_ts"]) - parse_iso(t0)).total_seconds()
        out.append(
            {
                "reason": fault["reason"],
                "clip": run.rel_path,
                "start_frame": start,
                "seconds": seconds,
            }
        )
    return out


def ground_truth_flags(label) -> set:
    """The flag types a clip's ground truth contains (shared with the baseline)."""
    return _gt_flags(label)


def extract_outcome(label, run) -> ClipOutcome:
    """Score one clip: pair its ground truth with what the pipeline produced."""
    frame_ts = _frame_ts(run)
    return ClipOutcome(
        session=label.session,
        gt_flags=_gt_flags(label),
        pred_flags=_pred_flags(run),
        rail_intervals=_rail_intervals(label, run),
        latencies=_latencies(run, frame_ts),
        time_to_alarm=_time_to_alarm(label, run, frame_ts),
    )


__all__ = ["ClipRun", "ClipOutcome", "extract_outcome", "ground_truth_flags"]
