"""The unified bypass ledger: symlink-safe marker reads, and the
best-effort, migration-aware append both hook scripts share.

``git_commit_guard.py`` calls into this module (via the
``base_branch_guard`` facade) for its own marker reads and ledger writes,
so the unified-sink + one-time legacy-ledger migration-note mechanics live
in exactly one place.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

from base_branch_guard_constants import (
    BYPASS_LOG_REL,
    BYPASS_TYPE_BY_ACTION,
    LEGACY_BYPASS_LOG_REL,
)


def same_path(a: Path, b: Path) -> bool:
    """Path equality WITHOUT resolving symlinks.

    ``Path.resolve()`` follows symlinks in every path component, so a
    symlink planted at *b* (the marker) would make an edit of whatever it
    points at compare equal to *b* itself.
    """
    return os.path.normpath(str(a)) == os.path.normpath(str(b))


def marker_kind(marker: Path) -> str:
    """One ``lstat`` read of *marker*, taken fresh at the point of use.

    Returns ``"symlink"``, ``"file"`` (a regular file -- the only shape a
    legitimate marker takes), or ``"absent"``. A single read at the actual
    decision point avoids a TOCTOU window between two separate stat calls.
    """
    try:
        mode = os.lstat(marker).st_mode
    except OSError:
        return "absent"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISREG(mode):
        return "file"
    return "absent"


def _migration_note(repo_root: Path) -> dict:
    """The one-time record noting a legacy ledger's presence.

    Never reads its content -- the legacy file is history, not a source to
    replay; this only records that it exists at migration time.
    """
    return {
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "command": "ledger_migration",
        "target": str(LEGACY_BYPASS_LOG_REL),
        "gate": "ledger_migration",
        "action": "legacy_ledger_present",
        "branch": "",
        "tool": "",
    }


def _needs_migration_note(repo_root: Path, log_path: Path) -> bool:
    """True when a legacy ledger exists AND the unified ledger does not
    already carry a migration note for it.

    Any read failure (missing file, malformed line, foreign schema) reads
    as "no note found yet", never raised.
    """
    if not (repo_root / LEGACY_BYPASS_LOG_REL).is_file():
        return False
    try:
        with open(log_path, "r", encoding="utf-8") as existing:
            for line in existing:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("gate") == "ledger_migration":
                    return False
    except OSError:
        pass
    return True


def write_bypass_entry(repo_root: Path, entry: dict) -> str | None:
    """Best-effort append *entry* to the unified bypass ledger; return an
    error string on failure.

    The write itself must never crash the caller (a read-only
    ``.paircoder/`` would then brick every edit); the caller decides
    whether to fail closed on a non-None return.
    """
    log_path = repo_root / BYPASS_LOG_REL
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        needs_note = _needs_migration_note(repo_root, log_path)
        with open(log_path, "a", encoding="utf-8") as handle:
            if needs_note:
                handle.write(json.dumps(_migration_note(repo_root)) + "\n")
            handle.write(json.dumps(entry) + "\n")
    except OSError as exc:
        return str(exc)
    return None


def ledger_writable(repo_root: Path) -> str | None:
    """Probe whether this repo's unified ledger could be appended to;
    return the same error string ``write_bypass_entry`` would.

    Deliberately side-effect-free with respect to ENTRIES: it opens the
    ledger for append and writes nothing, so a call that is ultimately
    DENIED leaves no allow-side record behind in any repo.
    """
    log_path = repo_root / BYPASS_LOG_REL
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8"):
            pass
    except OSError as exc:
        return str(exc)
    return None


def log_marker_bypass(
    repo_root: Path, *, action: str, branch: str | None, tool: str, target: Path
) -> str | None:
    """Best-effort append one bypass record; return an error string on failure.

    Every allow-because-of-the-marker outcome is a workflow bypass under
    this project's fail-closed-audited-bypass doctrine, so it must be
    logged -- but the log write itself must never crash the hook.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "command": "base_branch_guard",
        "target": str(target),
        "bypass_type": BYPASS_TYPE_BY_ACTION.get(action, action),
        "gate": "base_branch_guard",
        "action": action,
        "branch": branch or "",
        "tool": tool,
    }
    return write_bypass_entry(repo_root, entry)
