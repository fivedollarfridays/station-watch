"""Write-target discovery, change-shape classification, and the
agent-memory write exemption.

Everything here is path-only, derived from the event's own ``tool_input``
(or the checkout data ``evaluate()`` already resolved) -- never a second
source of truth like a ``git diff --cached`` subprocess.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from base_branch_guard_constants import (
    AGENT_MEMORY_BOUNDARY,
    BOOKKEEPING_SHAPE_FILES,
    BOOKKEEPING_SHAPE_ROOTS,
    DENY_TEMPLATE_BY_SHAPE,
    GENERIC_DENY_TEMPLATE,
    MUTATING_TOOLS,
    PATH_KEYS,
    UPGRADE_SHAPE_FILES,
    UPGRADE_SHAPE_ROOT,
    UPGRADE_SHAPE_SUFFIXES,
)


def mutating_targets(event: dict) -> list[Path]:
    """Every absolute path key an Edit/Write-family tool call would write.

    A `NotebookEdit` `tool_input` can carry both `file_path` and
    `notebook_path`; only `notebook_path` is the real write target, but a
    malformed/adversarial event can populate both. Returning every present
    key -- instead of collapsing to one with `.get(a) or .get(b)` -- lets
    `evaluate` judge each independently rather than silently trusting
    whichever key happened to win the `or`.
    """
    tool = str(event.get("tool_name", ""))
    if tool not in MUTATING_TOOLS:
        return []
    tool_input = event.get("tool_input") or {}
    cwd = str(event.get("cwd") or os.getcwd())
    targets: list[Path] = []
    for key in PATH_KEYS:
        raw = tool_input.get(key)
        if not raw:
            continue
        candidate = Path(str(raw))
        targets.append(candidate if candidate.is_absolute() else Path(cwd) / candidate)
    return targets


def mutating_target(event: dict) -> Path | None:
    """One write-target path for callers that only need somewhere to look.

    Thin wrapper over `mutating_targets` for the Bash-command parser, which
    only needs A path to resolve the touched checkout -- which present key
    wins here carries no security meaning, unlike in `evaluate`, where
    EVERY present key must be judged independently.
    """
    targets = mutating_targets(event)
    return targets[0] if targets else None


def classify_change_shape(protected: list[tuple[Path, tuple[Path, Path, str]]]) -> str:
    """The shared change shape across every entry in *protected* (each
    ``(target, (repo_root, git_dir, branch))`` pair `evaluate()` already
    resolved): ``"upgrade"``, ``"bookkeeping"``, ``"code"``, or
    ``"ambiguous"`` when different entries disagree.

    Matched by whole path segments (via `Path.parts`): `evil.claude/` or
    `.paircoder/tasks-backup/` must never false-match a real boundary.
    `_deny_reason` treats an unrecognized OR ambiguous shape identically --
    a mixed-shape call must not guess which half of the context is "real".
    """
    shapes = set()
    for target, (repo_root, _git_dir, _branch) in protected:
        try:
            rel = os.path.relpath(os.path.normpath(str(target)), str(repo_root))
        except ValueError:
            shapes.add("code")
            continue
        parts = Path(rel).parts
        if not parts or parts[0] == os.pardir:
            shapes.add("code")
            continue
        if parts[0] == UPGRADE_SHAPE_ROOT:
            shapes.add("upgrade")
            continue
        posix_rel = "/".join(parts)
        if posix_rel in UPGRADE_SHAPE_FILES or parts[-1] in UPGRADE_SHAPE_SUFFIXES:
            shapes.add("upgrade")
        elif posix_rel in BOOKKEEPING_SHAPE_FILES:
            shapes.add("bookkeeping")
        elif len(parts) >= 2 and "/".join(parts[:2]) in BOOKKEEPING_SHAPE_ROOTS:
            shapes.add("bookkeeping")
        else:
            shapes.add("code")
    return shapes.pop() if len(shapes) == 1 else "ambiguous"


def deny_reason(branch: str, shape: str) -> str:
    """The deny message for *branch*, worded for *shape*.

    ``"code"`` keeps the original engage-or-feature-branch dual remedy;
    every other shape (including an unrecognized or ambiguous one) gets
    the plain, always-executable feature-branch-only remedy.
    """
    template = DENY_TEMPLATE_BY_SHAPE.get(shape, GENERIC_DENY_TEMPLATE)
    return template.format(branch=branch)


# Kept split: collapses to a 94-char line at line-length=100.
def _agent_memory_prefix_has_symlink(
    all_parts: tuple[str, ...], boundary_index: int
) -> bool:  # fmt: skip
    """True when any path prefix from the `.claude` boundary down to the
    full target is a symlink, per a fresh ``lstat`` at each step.

    `FileNotFoundError` on a prefix means that component doesn't exist yet
    (the normal case for a brand-new note) -- treated as "no symlink". Any
    OTHER `OSError` DECLINES the exemption (returns True: "treat as if a
    symlink were found", since a bypass this hook cannot verify is not one
    it may grant).
    """
    for end in range(boundary_index + 1, len(all_parts) + 1):
        try:
            mode = os.lstat(Path(*all_parts[:end])).st_mode
        except FileNotFoundError:
            return False
        except OSError:
            return True
        if stat.S_ISLNK(mode):
            return True
    return False


def is_agent_memory_write(target: Path, repo_root: Path) -> bool:
    """True when *target* is a file strictly inside *repo_root*'s
    ``.claude/agent-memory``, anchored to the checkout ROOT (never matched
    as a substring anywhere in the path), with no symlink anywhere along
    the boundary-to-target prefix walk.
    """
    normalized_target = Path(os.path.normpath(str(target)))
    normalized_root = Path(os.path.normpath(str(repo_root)))
    try:
        rel_parts = normalized_target.relative_to(normalized_root).parts
    except ValueError:
        return False
    boundary, name = AGENT_MEMORY_BOUNDARY
    if len(rel_parts) < 2 or rel_parts[0] != boundary or rel_parts[1] != name:
        return False
    boundary_index = len(normalized_root.parts)
    if _agent_memory_prefix_has_symlink(normalized_target.parts, boundary_index):
        return False
    return True
