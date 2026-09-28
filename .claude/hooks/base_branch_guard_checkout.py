"""Checkout discovery and protected-branch resolution.

Everything needed to answer "is *this directory* inside a paircoder-managed
git checkout, and is that checkout currently on a protected branch": the
worktree-aware ``.git`` walk, ``HEAD``/remote-default parsing, and the
combined resolution ``evaluate()`` (and ``git_commit_guard.py``) call for
every path key they judge.
"""

from __future__ import annotations

from pathlib import Path

from base_branch_guard_constants import DEFAULT_PROTECTED


def find_checkout(start: Path) -> tuple[Path, Path] | None:
    """Return ``(repo_root, git_dir)`` for *start*, or None if not in a repo.

    Handles the worktree layout, where ``.git`` is a file pointing at
    ``<common>/worktrees/<name>`` rather than a directory.
    """
    for directory in (start, *start.parents):
        marker = directory / ".git"
        if marker.is_dir():
            return directory, marker
        if marker.is_file():
            text = marker.read_text(encoding="utf-8").strip()
            if text.startswith("gitdir:"):
                gitdir = Path(text.split(":", 1)[1].strip())
                if not gitdir.is_absolute():
                    gitdir = directory / gitdir
                return directory, gitdir
    return None


def _strip_prefix(ref: str, prefix: str) -> str | None:
    """The part of *ref* after *prefix*, or None if it does not start there.

    Branch names are paths: ``feature/main`` is not ``main``. Taking the last
    path segment instead collapses them, and a guard that reads a feature
    branch as a base branch denies every edit on it.
    """
    # Kept split: collapses to a 92-char line at line-length=100.
    return (
        ref[len(prefix) :]
        if ref.startswith(prefix) and len(ref) > len(prefix)
        else None
    )  # fmt: skip


def current_branch(git_dir: Path) -> str | None:
    """Branch name from ``HEAD``, or None when detached/unreadable."""
    head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref:"):
        return None
    return _strip_prefix(head.split(":", 1)[1].strip(), "refs/heads/")


def protected_branches(git_dir: Path) -> set[str]:
    """``dev`` + the usual defaults + this remote's actual default branch."""
    protected = set(DEFAULT_PROTECTED)
    common = git_dir
    commondir = git_dir / "commondir"
    if commondir.is_file():
        candidate = Path(commondir.read_text(encoding="utf-8").strip())
        common = candidate if candidate.is_absolute() else git_dir / candidate
    origin_head = common / "refs" / "remotes" / "origin" / "HEAD"
    if origin_head.is_file():
        text = origin_head.read_text(encoding="utf-8").strip()
        if text.startswith("ref:"):
            # Kept split: collapses to a 90-char line at line-length=100.
            default = _strip_prefix(
                text.split(":", 1)[1].strip(), "refs/remotes/origin/"
            )  # fmt: skip
            if default:
                protected.add(default)
    return protected


def resolve_checkout(directory: Path) -> tuple[Path, Path, str] | None:
    """``(repo_root, git_dir, branch)`` if *directory* sits inside a
    paircoder-managed checkout currently on a PROTECTED branch, else None.

    Takes *directory* as-is -- no file-to-parent normalization. Callers
    starting from a path key that might name a file (rather than an
    already-resolved directory) do that conversion themselves before
    calling this, so a single shared helper serves both the per-target
    loop in ``evaluate`` and the Bash git-commit directory, which is
    already a directory and must not be re-adjusted.
    """
    checkout = find_checkout(directory)
    if checkout is None:
        return None
    repo_root, git_dir = checkout
    if not (repo_root / ".paircoder").is_dir():
        return None
    branch = current_branch(git_dir)
    if branch is None or branch not in protected_branches(git_dir):
        return None
    return repo_root, git_dir, branch
