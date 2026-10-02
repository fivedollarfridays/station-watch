"""Reviewer verdicts: an append-only ``verdicts.jsonl``, newest line per flag wins.

A reviewer marks each flag of a session ``correct`` or ``incorrect`` (optionally with
a note). Every mark is one appended line of ``flag_id``, ``verdict``, ``note``,
``reviewer`` and ``ts``; the *effective* verdict for a flag is the newest line that
names it. Writes are idempotent keyed by ``flag_id``: a mark whose ``verdict``,
``note`` and ``reviewer`` already match the flag's effective line appends nothing, so
re-posting the same mark never grows the file, while any change appends one line and
so keeps who changed it and when.

The HTTP server (:mod:`station_watch.audit.review_server`), the ``audit mark`` CLI
and ``audit score`` all go through this one module; HF3.7 reads the same file.
"""

from __future__ import annotations

import json
from pathlib import Path

from station_watch.audit.flags import load_flags_json

VERDICTS_FILE = "verdicts.jsonl"
VALID_VERDICTS = ("correct", "incorrect")


def verdicts_path(audit_dir) -> Path:
    """The ``verdicts.jsonl`` path under ``audit_dir`` (it need not exist yet)."""
    return Path(audit_dir) / VERDICTS_FILE


def flag_ids(audit_dir) -> set[str]:
    """Every known ``flag_id`` from ``flags.json`` (the set a mark must name)."""
    return {flag["flag_id"] for flag in load_flags_json(audit_dir)}


def read_all(audit_dir) -> list[dict]:
    """Every verdict line in file order (oldest first); ``[]`` when none were written."""
    path = verdicts_path(audit_dir)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        if line.strip():
            entries.append(json.loads(line))
    return entries


def effective(audit_dir) -> dict[str, dict]:
    """``flag_id -> newest line`` (file order is oldest first, so the last line wins)."""
    result: dict[str, dict] = {}
    for entry in read_all(audit_dir):
        result[entry["flag_id"]] = entry
    return result


def _unchanged(current: dict | None, verdict: str, note: str, reviewer: str) -> bool:
    return (
        current is not None
        and current.get("verdict") == verdict
        and current.get("note", "") == note
        and current.get("reviewer") == reviewer
    )


def append_mark(audit_dir, flag_id: str, verdict: str, note: str, reviewer: str, *, ts: str) -> bool:
    """Append one verdict line for ``flag_id`` unless it equals the effective one.

    Returns ``True`` if a line was appended, ``False`` if the mark was already the
    effective one (idempotent). Callers validate ``flag_id`` and ``verdict`` first.
    """
    current = effective(audit_dir).get(flag_id)
    if _unchanged(current, verdict, note, reviewer):
        return False
    entry = {
        "flag_id": flag_id,
        "verdict": verdict,
        "note": note,
        "reviewer": reviewer,
        "ts": ts,
    }
    path = verdicts_path(audit_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return True


__all__ = [
    "VERDICTS_FILE",
    "VALID_VERDICTS",
    "verdicts_path",
    "flag_ids",
    "read_all",
    "effective",
    "append_mark",
]
