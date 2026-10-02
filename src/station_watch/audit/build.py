"""Build one session's proof sheet: ``flags.json`` plus a self-contained ``index.html``.

Reads the Log read-only through :class:`~station_watch.board.reader.LogReader`,
collects every flag (:func:`~station_watch.audit.flags.collect_flags`), resolves
each cited frame to its own evidence thumbnail with the configured zones drawn on
it (K4: never a substitute frame), and writes both files under ``out``. The Log is
never written; the only files touched are ``out``'s.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from station_watch.audit.flags import FLAGS_FILE, collect_flags
from station_watch.audit.html import render_sheet
from station_watch.audit.overlay import collect_regions
from station_watch.audit.thumbnails import ThumbContext, resolve_cell
from station_watch.board.reader import LogReader, UndecodableRow
from station_watch.config import load_station_config
from station_watch.evidence_index import EvidenceIndex

_ALL = -1


def build_audit(*, config_path, log_path, evidence_dir, clip, out) -> int:
    """Write ``<out>/flags.json`` and ``<out>/index.html`` for ``log_path``; return 0.

    A Log can hold several runs, and ``frame_id`` restarts at every run, so each
    flag's frames resolve against *its own* run's evidence and fingerprints (K4),
    never another run's frame of the same number.
    """
    config = load_station_config(config_path)
    with LogReader(log_path) as reader:
        flags = collect_flags(reader)
        run_ids = sorted({flag.run_id for flag in flags}) or [_run_id(reader, flags)]
        fingerprints = _fingerprints(reader) if clip is not None else {}
    contexts = {
        run_id: _context(config, evidence_dir, run_id, clip, fingerprints.get(run_id, {}))
        for run_id in run_ids
    }
    rendered = [
        (flag, [resolve_cell(fid, contexts[flag.run_id]) for fid in flag.frame_ids])
        for flag in flags
    ]
    title = f"station-watch audit — {config.station_id} ({', '.join(run_ids)})"
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / FLAGS_FILE).write_text(json.dumps([asdict(f) for f in flags], indent=2) + "\n")
    (out_dir / "index.html").write_text(render_sheet(title, rendered))
    return 0


def _context(config, evidence_dir, run_id, clip, fingerprints) -> ThumbContext:
    """The resolver context for one run: its evidence index and its frame fingerprints."""
    return ThumbContext(
        index=EvidenceIndex.load(evidence_dir, run_id),
        regions=collect_regions(config),
        clip=Path(clip) if clip is not None else None,
        fingerprints=fingerprints,
        dictionary_id=config.fiducial["dictionary_id"],
        marker_id=int(config.fiducial["marker_id"]),
    )


def _run_id(reader, flags) -> str:
    """The session's run id: from the flags, else from the newest record of any kind."""
    if flags:
        return flags[0].run_id
    for kind in ("frame", "verdict", "cycle", "blind"):
        record = reader.newest(kind)
        if record is not None:
            return record.run_id
    return ""


def _fingerprints(reader) -> dict[str, dict[int, str]]:
    """``run_id -> frame_id -> fingerprint`` (only needed for a ``--clip`` fallback)."""
    by_run: dict[str, dict[int, str]] = {}
    for frame in reader.iter_newest("frame", limit=_ALL):
        if isinstance(frame, UndecodableRow):
            continue  # no fingerprint to match a clip frame against
        by_run.setdefault(frame.run_id, {})[frame.frame_id] = frame.fingerprint
    return by_run


__all__ = ["build_audit"]
