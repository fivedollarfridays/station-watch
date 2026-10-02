"""Collect every flag of a session's Log into deterministic :class:`AuditFlag`\\ s.

A *flag* is anything the session asked an operator to look at: a **fault episode**
(a contiguous run of verdicts carrying the same ``(kind, target)`` fault) or a
**blind episode** (an ``opened`` :class:`BlindRecord` paired, if it ever clears,
with its ``cleared`` one). Each gets a deterministic ``flag_id`` so two builds of
the same Log name the same flags (HF3.5 AC2):

* fault: ``{run_id}:{fault kind}:{target}:{first verdict seq}``
* blind: ``{run_id}:blind:{reason}:{opened seq}``

The Log is read-only through :class:`~station_watch.board.reader.LogReader`;
:func:`collect_flags` never writes it. HF3.6 and HF3.7 consume both
:class:`AuditFlag` and :func:`collect_flags`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from station_watch.board.reader import UndecodableRow
from station_watch.records import BlindRecord, BlindState, Verdict

# SQLite reads LIMIT -1 as "every row": a session's whole verdict/blind history.
_ALL = -1

# The file ``audit build`` writes and HF3.6/HF3.7 read back (a list of flag dicts).
FLAGS_FILE = "flags.json"


def load_flags_json(audit_dir) -> list[dict]:
    """Read ``<audit dir>/flags.json`` back into the list of flag dicts build wrote.

    Each dict carries the :class:`AuditFlag` fields (``flag_id``, ``kind``, ``target``,
    ``station_id``, ``run_id``, ``opened_ts``, ``closed_ts``, ``frame_ids``). Raises
    ``OSError`` if the file is missing and ``ValueError`` if it is not a JSON list.
    """
    data = json.loads((Path(audit_dir) / FLAGS_FILE).read_text())
    if not isinstance(data, list):
        raise ValueError(f"{FLAGS_FILE} must be a JSON list of flags, got {type(data).__name__}")
    return data


@dataclass(frozen=True)
class AuditFlag:
    """One flag of the session (consumed by HF3.6 and HF3.7, and ``flags.json``)."""

    flag_id: str
    kind: str  # fault kind value, or "unobservable:<reason>" for a blind episode
    target: str  # slot/zone for a fault; the blind reason for a blind episode
    station_id: str
    run_id: str
    opened_ts: str
    closed_ts: str | None  # last verdict ts for a fault; cleared ts (or None) for a blind
    frame_ids: tuple[int, ...]


def collect_flags(reader) -> list[AuditFlag]:
    """Every fault and blind episode of the Log, in a deterministic order.

    Verdicts and blind records are walked oldest-first (``iter_newest`` is
    newest-first). A row the reader could not rebuild is its own
    ``unknown:undecodable`` flag and takes no part in episode pairing. The result is sorted by
    ``(opened_ts, kind, target, flag_id)`` so the list -- and so ``flags.json`` --
    is byte-identical across two builds of the same Log.
    """
    verdict_rows = list(reversed(list(reader.iter_newest("verdict", limit=_ALL))))
    blind_rows = list(reversed(list(reader.iter_newest("blind", limit=_ALL))))
    bad = [r for r in verdict_rows + blind_rows if isinstance(r, UndecodableRow)]
    verdicts = [r for r in verdict_rows if isinstance(r, Verdict)]
    blinds = [r for r in blind_rows if isinstance(r, BlindRecord)]
    flags = _fault_flags(verdicts) + _blind_flags(blinds) + [_unknown_flag(r) for r in bad]
    flags.sort(key=lambda f: (f.opened_ts, f.kind, f.target, f.flag_id))
    return flags


def _unknown_flag(row: UndecodableRow) -> AuditFlag:
    """A row the reader could not rebuild: listed as its own UNKNOWN flag, never dropped."""
    return AuditFlag(
        flag_id=row.record_id,
        kind="unknown:undecodable",
        target=row.reason,
        station_id="",
        run_id=row.run_id,
        opened_ts=row.ts,
        closed_ts=None,
        frame_ids=(),
    )


def _fault_flags(verdicts) -> list[AuditFlag]:
    """One :class:`AuditFlag` per contiguous ``(run_id, kind, target)`` fault episode.

    Keyed by run too: a fault still present when one run ends and another starts is
    two sessions, so two episodes, never one stitched across runs.
    """
    open_episodes: dict = {}
    flags: list[AuditFlag] = []
    for verdict in verdicts:
        present = {(verdict.run_id, f.kind, f.target): f for f in verdict.faults}
        for key in list(open_episodes):
            if key not in present:
                flags.append(_finish_fault(key, open_episodes.pop(key)))
        for key, fault in present.items():
            episode = open_episodes.get(key)
            if episode is None:
                episode = {
                    "first_seq": verdict.seq,
                    "opened_ts": verdict.ts,
                    "station_id": verdict.station_id,
                    "run_id": verdict.run_id,
                    "frame_ids": set(),
                }
                open_episodes[key] = episode
            episode["closed_ts"] = verdict.ts
            episode["frame_ids"].update(fault.frame_ids)
    for key in list(open_episodes):
        flags.append(_finish_fault(key, open_episodes.pop(key)))
    return flags


def _finish_fault(key, episode) -> AuditFlag:
    _run_id, kind, target = key
    return AuditFlag(
        flag_id=f"{episode['run_id']}:{kind.value}:{target}:{episode['first_seq']}",
        kind=kind.value,
        target=target,
        station_id=episode["station_id"],
        run_id=episode["run_id"],
        opened_ts=episode["opened_ts"],
        closed_ts=episode["closed_ts"],
        frame_ids=tuple(sorted(episode["frame_ids"])),
    )


def _blind_flags(blinds) -> list[AuditFlag]:
    """One :class:`AuditFlag` per ``opened`` blind, paired FIFO with its ``cleared``."""
    waiting: dict = {}  # (run_id, reason) -> FIFO of still-open episode dicts
    episodes: list[dict] = []
    for record in blinds:
        if record.state is BlindState.OPENED:
            episode = {
                "reason": record.reason.value,
                "opened_seq": record.seq,
                "opened_ts": record.ts,
                "closed_ts": None,
                "station_id": record.station_id,
                "run_id": record.run_id,
                "last_good": record.last_good_frame_id,
            }
            episodes.append(episode)
            waiting.setdefault((record.run_id, record.reason), []).append(episode)
        else:
            queue = waiting.get((record.run_id, record.reason))
            if queue:
                queue.pop(0)["closed_ts"] = record.ts
    return [_finish_blind(episode) for episode in episodes]


def _finish_blind(episode) -> AuditFlag:
    last_good = episode["last_good"]
    return AuditFlag(
        flag_id=f"{episode['run_id']}:blind:{episode['reason']}:{episode['opened_seq']}",
        kind=f"unobservable:{episode['reason']}",
        target=episode["reason"],
        station_id=episode["station_id"],
        run_id=episode["run_id"],
        opened_ts=episode["opened_ts"],
        closed_ts=episode["closed_ts"],
        frame_ids=() if last_good is None else (last_good,),
    )


__all__ = ["AuditFlag", "collect_flags", "load_flags_json", "FLAGS_FILE"]
