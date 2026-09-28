"""The worktree-scoped ``BPSAI_ENGAGE_MODE`` exemption.

A driver dispatched by ``ClaudeCodeAdapter`` into ONE checkout's worktree
must not have the mode flag exempt a COMPLETELY DIFFERENT protected
checkout it happens to have on disk. ``_engage_mode_exempts`` requires the
call's effective directory (or every protected target's, for a multi-key
event) to resolve INSIDE a declared worktree before the flag exempts
anything -- fail-closed on a missing scope.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from base_branch_guard_bypass_log import log_marker_bypass
from base_branch_guard_constants import (
    BYPASS_LOG_REL,
    ENGAGE_MARKER,
    ENGAGE_WORKTREE_MARKER,
)

# Mirrors `git_commit_guard.py`'s `AUDIT_WRITE_FAILED` template for the
# same exemption. Deliberately does not change the outcome (the exemption
# stays honored either way -- see `log_engage_exemption`'s docstring):
# only whether a failed append is SAID.
# Kept split: collapses to a 106-char line at line-length=100.
AUDIT_WRITE_FAILED = (
    "base_branch_guard: NOT AUDITED -- could not append `{action}` to "
    "{log} ({error}).\n"
)  # fmt: skip


def engage_worktree_paths(env: dict) -> list[Path]:
    """The run's declared worktree path(s) from ``ENGAGE_WORKTREE_MARKER``
    (``os.pathsep``-joined by whoever set it), or ``[]`` when unset.

    An unset marker and an empty/whitespace one collapse to the same empty
    result deliberately: both mean "no worktree was declared", and
    ``engage_mode_exempts`` refuses on either.
    """
    raw = env.get(ENGAGE_WORKTREE_MARKER, "")
    return [Path(p) for p in raw.split(os.pathsep) if p]


def path_within_worktrees(target: Path, worktrees: list) -> bool:
    """True when *target* IS one of *worktrees*, or sits below one of them.

    Directory-boundary-aware: a lexically-similar sibling directory
    (``.../foo-evil`` next to a declared ``.../foo``) must never read as
    "inside" merely because it shares a string prefix -- never a bare
    ``str.startswith``.

    Resolves symlinks (``os.path.realpath``, stdlib, never raises on a
    missing final component) for BOTH *target* and each worktree before
    comparing. A lexical-only compare reasoned this check only NARROWS an
    exemption, so an unresolved symlink was assumed safe -- but a symlink
    planted INSIDE a declared worktree, pointing at a DIFFERENT protected
    checkout (``<worktree>/escape -> /other/protected/repo``), is
    OVER-inclusion, not under-inclusion: the literal target path still
    lexically starts with the worktree prefix, so an unresolved compare
    reports "contained" even though the write's real destination is a
    different repo entirely. ``realpath`` is safe to call even when
    *target*'s final component does not exist yet (the normal case for a
    new file): it resolves every EXISTING prefix symlink and appends the
    missing tail literally.

    SYMLINKS ONLY -- not the whole aliasing class. A bind mount or a
    hardlinked directory inside a declared worktree still reads as
    contained, because ``realpath`` has nothing to resolve. Left as-is
    deliberately: setting either up needs root, and an operator with
    mount privileges has strictly better options than this exemption.
    Stated so the next reader does not assume the class is closed.
    """
    normalized_target = os.path.realpath(str(target))
    for worktree in worktrees:
        normalized_worktree = os.path.realpath(str(worktree))
        if normalized_target == normalized_worktree or normalized_target.startswith(
            normalized_worktree + os.sep
        ):
            return True
    return False


def engage_mode_exempts(targets, env: dict) -> bool:
    """True when ``ENGAGE_MARKER`` exempts a call touching every path in
    *targets* (one directory for the ambiguous Bash-commit shape, or every
    independently-resolved protected key for an Edit/Write-family call).

    FAIL-CLOSED on a missing scope: a marker with NO declared worktree
    exempts nothing.
    """
    if env.get(ENGAGE_MARKER) != "1":
        return False
    worktrees = engage_worktree_paths(env)
    if not worktrees:
        return False
    return all(path_within_worktrees(t, worktrees) for t in targets)


# Kept split: collapses to a 91-char line at line-length=100.
def log_engage_exemption(
    repo_root: Path, *, branch: str, tool: str, target: Path
) -> None:  # fmt: skip
    """Best-effort audit of the ``BPSAI_ENGAGE_MODE`` exemption, appended
    only when it actually flips THIS call from denied to allowed.

    Deliberately NOT fail-closed on a write failure: this is a REQUIRED,
    ratified-AC guarantee (an engage driver's call must never be blocked
    by this guard), so an unauditable exemption is still honored rather
    than turned into a block. It is still SAID -- a bare, unbound call
    here would throw the writer's error away in silence, the same
    discard already closed for `git_commit_guard.py`'s twin of this
    function. Honored, never unsaid.
    """
    error = log_marker_bypass(
        repo_root,
        action="engage_mode_exempt",
        branch=branch,
        tool=tool,
        target=target,
    )
    if error is not None:
        sys.stderr.write(
            AUDIT_WRITE_FAILED.format(
                action="engage_mode_exempt",
                log=BYPASS_LOG_REL,
                error=error,
            )
        )
