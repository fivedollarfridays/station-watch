"""Fault *marks*: when each fault was injected and cleared, on the wall clock.

A drill measures time-to-alarm from the moment a fault was injected, so every fault
is carried as a :class:`Mark` -- its name and its injection and clear timestamps.

* In a **synthetic** run the schedule's ``start_s`` / ``clear_s`` offsets are
  rebased onto the run's start time (:func:`marks_from_schedule`), so a window's
  injection is the exact wall-clock instant the :class:`~station_watch.faults.FaultSource`
  begins transforming frames.
* In a **live** run the operator types ``start <fault>`` / ``clear <fault>`` lines on
  stdin and each line is stamped as it is read (:class:`MarkReader`); the clear
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


class MarkReader:
    """Accumulates stamped ``start``/``clear`` lines one at a time.

    Each line is stamped on the wall clock as it arrives. A ``start`` opens a mark; a
    later matching ``clear`` closes it (a mark never cleared keeps ``cleared_ts`` as
    ``None``). Unrecognised lines are ignored but still stamped into the manifest, so
    the record is of exactly what was typed.

    Holding the state between lines means an interrupted read (Ctrl-C) still has
    every mark typed before it, so a cut-short drill can write what it measured.
    """

    def __init__(self, *, clock: Callable[[], str] = utc_now_iso) -> None:
        self._clock = clock
        self._marks: list[Mark] = []
        self._open_index: dict[str, int] = {}
        self._stamped: list[str] = []

    def feed(self, raw: str) -> None:
        """Stamp one input line; record it as a mark if it is a ``start``/``clear``."""
        line = raw.strip()
        if not line:
            return
        ts = self._clock()
        self._stamped.append(f"{ts} {line}")
        parsed = _parse_line(line)
        if parsed is None:
            return
        verb, fault = parsed
        if verb == "start":
            self._open_index[fault] = len(self._marks)
            self._marks.append(Mark(fault=fault, injected_ts=ts, cleared_ts=None))
        elif fault in self._open_index:
            idx = self._open_index.pop(fault)
            injected = self._marks[idx].injected_ts
            self._marks[idx] = Mark(fault=fault, injected_ts=injected, cleared_ts=ts)

    def result(self) -> tuple[list[Mark], str]:
        """The marks so far and the stamped-lines manifest text."""
        manifest = "\n".join(self._stamped) + ("\n" if self._stamped else "")
        return list(self._marks), manifest


def manifest_sha256(text: str) -> str:
    """Hex SHA-256 of the stamped-marks manifest (live mode's provenance hash)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = ["Mark", "MarkReader", "marks_from_schedule", "manifest_sha256"]
