"""Build one read-only view model per station from the Log (component 7).

Each field comes from the newest row of its kind, judged against the station
config's own windows:

* **state** -- the newest ``Verdict``'s state and how long it has held (walking
  back over the contiguous same-state streak, at most ``HELD_WALK_LIMIT`` verdicts;
  a longer streak reads ``held >= ...``, a true lower bound). No verdict, or a verdict older
  than ``watchdog.cycle_window_s`` (the rate the pipeline writes one), is UNKNOWN
  with the reason -- never OK (K1).
* **frames** -- now minus the newest ``FrameRecord`` ts, marked STALE beyond
  ``liveness_window_s``.
* **blind reasons** -- the reasons whose newest ``BlindRecord`` is still
  ``opened`` (one indexed row per reason, however old; counted separately from
  faults).
* **flags** -- the newest verdict's faults, each rendered by its raw kind name
  with its cited frame ids, so a fault kind the Board does not special-case still
  renders and is never dropped.
* **alarm** -- the newest ``AlarmEvaluated``'s open episode strings and its age,
  marked STALE beyond ``watchdog.alarm_eval_window_s``.

A missing or unreadable Log yields an UNKNOWN view rather than raising, so the
Board always renders something and never reads a gap as healthy.
"""

from __future__ import annotations

from dataclasses import dataclass

from station_watch.board.reader import HELD_WALK_LIMIT, BoardLogError, LogReader
from station_watch.clock import parse_iso
from station_watch.records import BlindReason, BlindState


@dataclass(frozen=True)
class Flag:
    """One active fault from the newest verdict, rendered by its raw kind name."""

    kind: str
    target: str
    frame_ids: tuple[int, ...]


@dataclass(frozen=True)
class StationView:
    """The read-only screen model for one station; ``ok`` is never true when UNKNOWN."""

    station_id: str
    ok: bool
    state: str
    state_detail: str
    state_held_s: float | None
    state_stale: bool
    frame_age_s: float | None
    frame_stale: bool
    frame_detail: str
    blind_reasons: tuple[str, ...] = ()
    flags: tuple[Flag, ...] = ()
    alarm_open_episodes: tuple[str, ...] = ()
    alarm_age_s: float | None = None
    alarm_stale: bool = False
    alarm_detail: str = ""


def _age(ts: str, now: str) -> float:
    return (parse_iso(now) - parse_iso(ts)).total_seconds()


def _unknown_view(station_id: str, reason: str) -> StationView:
    return StationView(
        station_id=station_id,
        ok=False,
        state="UNKNOWN",
        state_detail=reason,
        state_held_s=None,
        state_stale=True,
        frame_age_s=None,
        frame_stale=True,
        frame_detail=reason,
        alarm_stale=True,
        alarm_detail=reason,
    )


def _held_seconds(reader: LogReader, newest, now: str) -> tuple[float, bool]:
    """Seconds the newest state has held, and whether the streak outran the walk."""
    held_since, walked = newest.ts, 0
    for verdict in reader.iter_newest("verdict", limit=HELD_WALK_LIMIT):
        walked += 1
        if verdict.state != newest.state:
            return _age(held_since, now), False
        held_since = verdict.ts
    return _age(held_since, now), walked >= HELD_WALK_LIMIT


def _state_fields(reader: LogReader, verdict, config, now: str) -> dict:
    if verdict is None:
        return {"state": "UNKNOWN", "detail": "no verdict yet", "held": None, "stale": True}
    age = _age(verdict.ts, now)
    window = float(config.watchdog["cycle_window_s"])
    if age > window:
        detail = f"verdict STALE ({age:.1f}s old > {window:.1f}s window)"
        return {"state": "UNKNOWN", "detail": detail, "held": None, "stale": True}
    held, at_least = _held_seconds(reader, verdict, now)
    return {
        "state": verdict.state.value.upper(),
        "detail": f"held {'>= ' if at_least else ''}{held:.1f}s",
        "held": held,
        "stale": False,
    }


def _frame_fields(reader: LogReader, config, now: str) -> dict:
    frame = reader.newest("frame")
    if frame is None:
        return {"age": None, "stale": True, "detail": "no frames yet"}
    age = _age(frame.ts, now)
    window = float(config.liveness_window_s)
    stale = age > window
    detail = f"{age:.1f}s old" + (f" STALE (> {window:.1f}s window)" if stale else "")
    return {"age": age, "stale": stale, "detail": detail}


def _open_blind_reasons(reader: LogReader) -> tuple[str, ...]:
    """Reasons whose newest BlindRecord is still ``opened`` (one row per reason)."""
    open_reasons = []
    for reason in BlindReason:
        record = reader.newest_with_reason("blind", reason.value)
        if record is not None and record.state == BlindState.OPENED:
            open_reasons.append((record.ts, reason.value))
    return tuple(reason for _, reason in sorted(open_reasons, reverse=True))


def _alarm_fields(reader: LogReader, config, now: str) -> dict:
    row = reader.newest("alarm_eval")
    if row is None:
        return {"episodes": (), "age": None, "stale": True, "detail": "no alarm evaluations yet"}
    age = _age(row.ts, now)
    window = float(config.watchdog["alarm_eval_window_s"])
    stale = age > window
    detail = f"{age:.1f}s old" + (f" STALE (> {window:.1f}s window)" if stale else "")
    return {"episodes": tuple(row.open_episodes), "age": age, "stale": stale, "detail": detail}


def _assemble(config, reader: LogReader, now: str) -> StationView:
    verdict = reader.newest("verdict")
    state = _state_fields(reader, verdict, config, now)
    frames = _frame_fields(reader, config, now)
    alarm = _alarm_fields(reader, config, now)
    blind_reasons = _open_blind_reasons(reader)
    faults = verdict.faults if verdict else ()
    flags = tuple(Flag(f.kind.value, f.target, tuple(f.frame_ids)) for f in faults)
    ok = (
        state["state"] == "HEALTHY"
        and not frames["stale"]
        and not alarm["stale"]
        and not blind_reasons
        and not flags
        and not alarm["episodes"]
    )
    return StationView(
        station_id=config.station_id,
        ok=ok,
        state=state["state"],
        state_detail=state["detail"],
        state_held_s=state["held"],
        state_stale=state["stale"],
        frame_age_s=frames["age"],
        frame_stale=frames["stale"],
        frame_detail=frames["detail"],
        blind_reasons=blind_reasons,
        flags=flags,
        alarm_open_episodes=alarm["episodes"],
        alarm_age_s=alarm["age"],
        alarm_stale=alarm["stale"],
        alarm_detail=alarm["detail"],
    )


def build_view(config, log_path, *, now: str) -> StationView:
    """The station's read-only view; a missing/unreadable Log is UNKNOWN, never OK."""
    try:
        reader = LogReader(log_path)
    except BoardLogError as exc:
        return _unknown_view(config.station_id, str(exc))
    try:
        with reader:
            return _assemble(config, reader, now)
    except BoardLogError as exc:
        return _unknown_view(config.station_id, str(exc))


__all__ = ["Flag", "StationView", "build_view"]
