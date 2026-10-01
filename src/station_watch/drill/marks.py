"""Fault *marks*: when each fault was injected and cleared, on the wall clock.

A drill measures time-to-alarm from the moment a fault was injected, so every fault
is carried as a :class:`Mark` -- its name and its injection and clear timestamps.

* In a **synthetic** run the schedule's ``start_s`` / ``clear_s`` offsets are
  rebased onto the run's start time (:func:`marks_from_schedule`), so a window's
  injection is the exact wall-clock instant the :class:`~station_watch.faults.FaultSource`
  begins transforming frames.
* In a **live** run the operator types ``start <fault>`` / ``clear <fault>`` lines on
  stdin and each line is stamped as it is read (:func:`read_stdin_marks`); the clear
  pairs with the matching open start. The stamped lines are also the live run's
  manifest (their SHA-256 is the provenance ``manifest_sha256``), so what the operator
  did is itself the record.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from station_watch.clock import offset_iso, utc_now_iso
from station_watch.faults import FAULT_NAMES, FaultWindow


@dataclass(frozen=True)
class Mark:
    """One fault occurrence: when it was injected and when it was cleared."""

    fault: str
    injected_ts: str
    cleared_ts: str | None


def marks_from_schedule(windows: Iterable[FaultWindow], run_start_ts: str) -> list[Mark]:
    """Rebase a synthetic schedule's offsets onto the run's start, oldest first."""
    marks = [
        Mark(
            fault=window.fault,
            injected_ts=offset_iso(run_start_ts, window.start_s),
            cleared_ts=offset_iso(run_start_ts, window.clear_s),
        )
        for window in windows
    ]
    return sorted(marks, key=lambda mark: mark.injected_ts)


def _parse_line(line: str) -> tuple[str, str] | None:
    """Split a ``start <fault>`` / ``clear <fault>`` line, or ``None`` if it is neither."""
    parts = line.split()
    if len(parts) != 2 or parts[0] not in ("start", "clear") or parts[1] not in FAULT_NAMES:
        return None
    return parts[0], parts[1]


def read_stdin_marks(
    lines: Iterable[str], *, clock: Callable[[], str] = utc_now_iso
) -> tuple[list[Mark], str]:
    """Read ``start``/``clear`` lines, stamping each on the wall clock as it arrives.

    Returns the marks (one per ``start``, its ``cleared_ts`` set by a later matching
    ``clear`` or ``None`` if never cleared) and the stamped-lines text that is the live
    run's manifest. Unrecognised lines are ignored but still stamped into the manifest,
    so the record is of exactly what was typed.
    """
    marks: list[Mark] = []
    open_index: dict[str, int] = {}
    stamped: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        ts = clock()
        stamped.append(f"{ts} {line}")
        parsed = _parse_line(line)
        if parsed is None:
            continue
        verb, fault = parsed
        if verb == "start":
            open_index[fault] = len(marks)
            marks.append(Mark(fault=fault, injected_ts=ts, cleared_ts=None))
        elif fault in open_index:
            idx = open_index.pop(fault)
            marks[idx] = Mark(fault=fault, injected_ts=marks[idx].injected_ts, cleared_ts=ts)
    manifest = "\n".join(stamped) + ("\n" if stamped else "")
    return marks, manifest


def manifest_sha256(text: str) -> str:
    """Hex SHA-256 of the stamped-marks manifest (live mode's provenance hash)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = ["Mark", "marks_from_schedule", "read_stdin_marks", "manifest_sha256"]
