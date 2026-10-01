"""Read a drill's Log and alarm record back into the per-fault time-to-alarm table.

After the run, each fault is scored from what the real pipeline actually recorded:
the blind reason that opened (from the Log's ``BlindRecord`` rows), the time from
injection to the first alarm and from clear to the recovery (from the ``record``
alarm sink file), and whether exactly one alarm and one recovery occurred. Events are
attributed to a fault by wall-clock window -- an alarm or recovery falls to the fault
whose injection it follows, up to the next fault's injection -- which is unambiguous
because a schedule's windows never overlap and the gaps between them are healthy.

A fault the pipeline never alarms on is reported with ``alarm: none`` and null times,
never dropped and never given a default: a drill that cannot see a fault must say so.
Expected reasons (:data:`EXPECTED_REASON`) are recorded beside the observed reason so
the table can flag a mismatch rather than hide it.
"""

from __future__ import annotations

import json
from pathlib import Path

from station_watch.clock import parse_iso
from station_watch.drill.marks import Mark

# The blind reason each fault should open, checked and reported when it differs.
EXPECTED_REASON: dict[str, tuple[str, ...]] = {
    "lens_covered": ("dark",),
    "lights_off": ("dark",),
    "frozen": ("frozen",),
    "bumped": ("view_shifted", "fiducial_missing"),
    "cable_pulled": ("disconnected",),
}


def load_alarm_events(path: str | Path) -> list[dict]:
    """Every JSON line the ``record`` alarm sink wrote (alarms and recoveries)."""
    file = Path(path)
    if not file.exists():
        return []
    return [json.loads(line) for line in file.read_text().splitlines() if line.strip()]


def _within(ts: str | None, lo: str, hi: str | None) -> bool:
    """``lo <= ts < hi`` on ISO strings; ``hi is None`` is open-ended."""
    return ts is not None and lo <= ts and (hi is None or ts < hi)


def _seconds_between(start_ts: str, end_ts: str) -> float:
    return (parse_iso(end_ts) - parse_iso(start_ts)).total_seconds()


def _blind_reason(blind_opens, lo: str, hi: str | None) -> str | None:
    """The reason of the first ``opened`` BlindRecord in ``[lo, hi)``, from the Log."""
    for record in blind_opens:
        if _within(record.ts, lo, hi):
            return record.reason.value
    return None


def _fault_metric(mark: Mark, next_injected: str | None, events: list[dict], blind_opens) -> dict:
    """Score one fault from the Log blind opens and the alarm record events."""
    lo, hi = mark.injected_ts, next_injected
    alarms = [e for e in events if e["event"] == "alarm" and _within(e["started_ts"], lo, hi)]
    recoveries = [
        e for e in events if e["event"] == "recovery" and _within(e.get("recovered_ts"), lo, hi)
    ]
    reason = _blind_reason(blind_opens, lo, hi)
    expected = EXPECTED_REASON.get(mark.fault, ())
    time_to_alarm = _seconds_between(lo, alarms[0]["started_ts"]) if alarms else None
    time_to_recovery = (
        _seconds_between(mark.cleared_ts, recoveries[0]["recovered_ts"])
        if recoveries and mark.cleared_ts is not None
        else None
    )
    return {
        "injected_ts": mark.injected_ts,
        "cleared_ts": mark.cleared_ts,
        "alarm": alarms[0]["cause"] if alarms else "none",
        "blind_reason": reason,
        "expected_reason": list(expected),
        "reason_matches": reason in expected,
        "alarm_count": len(alarms),
        "recovery_count": len(recoveries),
        "exactly_one_alarm": len(alarms) == 1,
        "exactly_one_recovery": len(recoveries) == 1,
        "time_to_alarm_s": time_to_alarm,
        "time_to_recovery_s": time_to_recovery,
    }


def assemble_fault_metrics(marks: list[Mark], events: list[dict], blind_opens) -> dict:
    """One scored entry per fault, keyed by fault name (collisions get a ``#n`` suffix)."""
    ordered = sorted(marks, key=lambda mark: mark.injected_ts)
    out: dict[str, dict] = {}
    for index, mark in enumerate(ordered):
        next_injected = ordered[index + 1].injected_ts if index + 1 < len(ordered) else None
        key = mark.fault
        suffix = 2
        while key in out:
            key = f"{mark.fault}#{suffix}"
            suffix += 1
        out[key] = _fault_metric(mark, next_injected, events, blind_opens)
    return out


def thresholds_in_force(config) -> dict:
    """The config thresholds that governed this run, recorded under metrics."""
    fiducial = config.fiducial
    return {
        "dark_luma_threshold": config.dark_luma_threshold,
        "dark_window_s": config.dark_window_s,
        "frozen_frames": config.frozen_frames,
        "fiducial_tolerance_px": fiducial["tolerance_px"],
        "fiducial_window_s": fiducial.get("window_s"),
        "liveness_window_s": config.liveness_window_s,
        "recover_good_frames": config.recover_good_frames,
        "recover_healthy_verdicts": config.recover_healthy_verdicts,
        "cycle_interval_s": config.cycle_interval_s,
    }


def assemble_metrics(marks: list[Mark], config, events: list[dict], blind_opens) -> dict:
    """The full metrics block: per-fault timings plus the thresholds in force."""
    return {
        "faults": assemble_fault_metrics(marks, events, blind_opens),
        "thresholds": thresholds_in_force(config),
    }


__all__ = [
    "EXPECTED_REASON",
    "load_alarm_events",
    "assemble_fault_metrics",
    "thresholds_in_force",
    "assemble_metrics",
]
