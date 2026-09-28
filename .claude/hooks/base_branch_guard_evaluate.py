"""``evaluate()``: the guard's single allow/deny decision point.

Allowed without further checks when: the tool is not mutating, or -- per
present path key / per commit segment, each resolved independently -- a
key is outside any git checkout, its checkout is not paircoder-managed, or
its HEAD is not on a protected branch. Only keys that DO resolve to a
protected-branch checkout ("protected" below) reach the further, AUDITED
paths -- the engage-driver session exemption, writing the opt-out marker
itself, the marker being present when it would otherwise have denied the
call, or (Edit/Write only) a write strictly inside the checkout's own
``.claude/agent-memory``.

A Bash call can name MORE THAN ONE commit target (a compound command
committing into several checkouts); every one of them is resolved and
judged independently, deduped by repo root, exactly like a multi-key
Edit/Write event -- the call is denied if ANY of them is a protected,
undisarmed mutation, regardless of what any other one resolves to.
"""

from __future__ import annotations

import os
from pathlib import Path

from base_branch_guard_arms import (
    decide_then_commit,
    evaluate_marker_disarm,
    evaluate_marker_write,
    grant_agent_memory_exemption,
)
from base_branch_guard_bypass_log import same_path
from base_branch_guard_checkout import resolve_checkout
from base_branch_guard_constants import GIT_COMMIT_RE, OPT_OUT_MARKER
from base_branch_guard_engage import engage_mode_exempts, log_engage_exemption
from base_branch_guard_parser import touched_directories
from base_branch_guard_targets import (
    classify_change_shape,
    is_agent_memory_write,
    mutating_targets,
)


def _unresolvable_bash_cwd(event: dict) -> str | None:
    """The ``"unresolvable cwd; evaluated <path>"`` deny reason, or None.

    ``touched_directories`` falls back to ``os.getcwd()`` -- THIS GUARD
    PROCESS's own cwd, unrelated to the tool call -- whenever the
    PreToolUse event carries no ``cwd`` at all, then silently judges
    whatever checkout happens to sit there. Unlike an unresolvable `cd`/
    `-C` VALUE (which still has a trustworthy anchor: the tool's own
    reported ``cwd``), a MISSING event ``cwd`` has no trustworthy anchor
    at all -- so this is refused outright, naming the guessed path,
    rather than silently judging the session cwd. Scoped to the shape
    that actually depends on it: a Bash call whose command is git-commit
    shaped (`GIT_COMMIT_RE`, the same fallback pattern the tokenizer
    itself falls back to on unparsable text) with no `cwd` key present.
    """
    if str(event.get("tool_name", "")) != "Bash":
        return None
    if event.get("cwd"):
        return None
    command = str((event.get("tool_input") or {}).get("command", ""))
    if not GIT_COMMIT_RE.search(command):
        return None
    return f"unresolvable cwd; evaluated {os.getcwd()}"


def _resolve_protected(
    paths: list[Path],
    *,
    files: bool = False,
) -> list[tuple[Path, tuple[Path, Path, str]]]:
    """Every path in *paths* that resolves to a protected-branch checkout,
    paired with that resolution.

    *files* True (the Edit/Write shape) treats a non-directory *path* as a
    FILE and resolves from its parent instead. False (the Bash
    commit-directory shape) takes *path* as-is -- it is already a
    directory the parser resolved, and a nonexistent one must not be
    silently walked up to its parent.
    """
    protected = []
    for path in paths:
        directory = path if not files or path.is_dir() else path.parent
        resolved = resolve_checkout(directory)
        if resolved is not None:
            protected.append((path, resolved))
    return protected


def _dedupe_by_repo(protected) -> dict[Path, tuple[Path, Path, Path, str]]:
    """One representative ``(target, repo_root, git_dir, branch)`` per
    DISTINCT repo root among *protected* -- so a same-repo multi-key event
    still yields exactly one audit entry."""
    by_repo: dict[Path, tuple[Path, Path, Path, str]] = {}
    for target, (repo_root, git_dir, branch) in protected:
        by_repo.setdefault(repo_root, (target, repo_root, git_dir, branch))
    return by_repo


def _evaluate_bash_commits(event: dict, env: dict) -> str | None:
    """The Bash-command path: every commit segment's directory judged
    independently, deduped by repo, shape fixed as ``"ambiguous"`` (a git
    commit resolves to a directory, never specific staged files)."""
    unresolvable = _unresolvable_bash_cwd(event)
    if unresolvable is not None:
        return unresolvable
    tool = str(event.get("tool_name", ""))
    protected = _resolve_protected(touched_directories(event), files=False)
    if not protected:
        return None
    by_repo = _dedupe_by_repo(protected)
    if engage_mode_exempts([d for d, _r in protected], env):
        for directory, repo_root, _git_dir, branch in by_repo.values():
            log_engage_exemption(repo_root, branch=branch, tool=tool, target=directory)
        return None
    # Kept split: collapses to a 99-char line at line-length=100.
    return decide_then_commit(
        [(d, r, b, r / OPT_OUT_MARKER) for d, r, _g, b in by_repo.values()],
        tool,
        lambda repo_root, **kwargs: evaluate_marker_disarm(
            repo_root, shape="ambiguous", **kwargs
        ),
    )  # fmt: skip


# Kept split: collapses to a 90-char line at line-length=100.
def _evaluate_mutating_targets(
    event: dict, targets: list[Path], env: dict
) -> str | None:  # fmt: skip
    """The Edit/Write-family path: every present path key judged
    independently, with the marker-write and agent-memory exemptions
    layered on top of the ordinary disarm arm."""
    tool = str(event.get("tool_name", ""))
    protected = _resolve_protected(targets, files=True)
    if not protected:
        return None
    by_repo = _dedupe_by_repo(protected)

    if engage_mode_exempts([t for t, _r in protected], env):
        for target, repo_root, _git_dir, branch in by_repo.values():
            log_engage_exemption(repo_root, branch=branch, tool=tool, target=target)
        return None

    if all(is_agent_memory_write(t, r[0]) for t, r in protected):
        return grant_agent_memory_exemption(by_repo, tool)

    if all(same_path(t, r[0] / OPT_OUT_MARKER) for t, r in protected):
        return decide_then_commit(
            [(t, r, b, r / OPT_OUT_MARKER) for t, r, _g, b in by_repo.values()],
            tool,
            evaluate_marker_write,
        )

    shape = classify_change_shape(protected)
    disarm_by_repo: dict[Path, tuple[Path, Path, Path, str]] = {}
    for target, (repo_root, git_dir, branch) in protected:
        if same_path(target, repo_root / OPT_OUT_MARKER):
            continue
        disarm_by_repo.setdefault(repo_root, (target, repo_root, git_dir, branch))
    # Kept split: collapses to a 93-char line at line-length=100.
    return decide_then_commit(
        [(t, r, b, r / OPT_OUT_MARKER) for t, r, _g, b in disarm_by_repo.values()],
        tool,
        lambda repo_root, **kwargs: evaluate_marker_disarm(
            repo_root, shape=shape, **kwargs
        ),
    )  # fmt: skip


def evaluate(event: dict, env: dict) -> str | None:
    """Return a deny reason, or None to allow."""
    targets = mutating_targets(event)
    if not targets:
        return _evaluate_bash_commits(event, env)
    return _evaluate_mutating_targets(event, targets, env)
