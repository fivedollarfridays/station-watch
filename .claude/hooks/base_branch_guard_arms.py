"""The audited allow/deny arms: marker-write, marker-disarm, the
agent-memory exemption grant, and the decide-then-commit ordering that
makes a multi-repo call's audit trail all-or-nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

from base_branch_guard_bypass_log import (
    ledger_writable,
    log_marker_bypass,
    marker_kind,
)
from base_branch_guard_constants import (
    ADVISORY_TEMPLATE,
    AUDIT_FAILURE_TEMPLATE,
    BYPASS_LOG_REL,
    OPT_OUT_MARKER,
    SYMLINK_WARNING_TEMPLATE,
)
from base_branch_guard_targets import deny_reason


def decide_then_commit(plans: list, tool: str, arm) -> str | None:
    """Run *arm* over every participating repo in DECIDE mode, then -- only
    if not one of them refused -- in COMMIT mode.

    An arm that writes as it goes leaves repo A's "allowed" record behind
    when repo B turns out to deny, so the ledger ends up asserting an
    allow for a call that was refused. Nothing is appended until every
    participating repo has agreed.

    Each marker is lstat'd exactly ONCE, here, at the decision point, and
    that single reading is handed to both passes: re-reading it for the
    commit pass would reopen a TOCTOU window.
    """
    resolved = [(t, r, b, m, marker_kind(m)) for t, r, b, m in plans]
    for commit in (False, True):
        for target, repo_root, branch, marker, kind in resolved:
            reason = arm(
                repo_root,
                branch=branch,
                tool=tool,
                target=target,
                marker=marker,
                kind=kind,
                commit=commit,
            )
            if reason is not None:
                return reason
    return None


def _audit_failure(branch: str, action: str, error: str) -> str:
    return AUDIT_FAILURE_TEMPLATE.format(
        branch=branch,
        action=action,
        marker=OPT_OUT_MARKER,
        log_path=BYPASS_LOG_REL,
        error=error,
    )


def evaluate_marker_write(
    repo_root: Path,
    *,
    branch: str,
    tool: str,
    target: Path,
    marker: Path,
    kind: str | None = None,
    commit: bool = True,
) -> str | None:
    """The write-exemption arm: *target* is the marker path itself.

    A symlinked marker is never exempted -- tampering, not an opt-out.
    *commit* False runs the DECIDE half only: the same symlink check and
    the same audit-writability requirement, via a probe that appends no
    row.
    """
    kind = marker_kind(marker) if kind is None else kind
    if kind == "symlink":
        sys.stderr.write(SYMLINK_WARNING_TEMPLATE.format(marker=OPT_OUT_MARKER))
        return deny_reason(branch, "ambiguous")
    # Kept split: collapses to a 100-char line at line-length=100.
    error = (
        log_marker_bypass(
            repo_root, action="marker_write", branch=branch, tool=tool, target=target
        )
        if commit
        else ledger_writable(repo_root)
    )  # fmt: skip
    if error is None:
        return None
    return _audit_failure(branch, "marker_write", error)


def evaluate_marker_disarm(
    repo_root: Path,
    *,
    branch: str,
    tool: str,
    target: Path,
    marker: Path,
    shape: str,
    kind: str | None = None,
    commit: bool = True,
) -> str | None:
    """The disarm arm: does the marker's presence flip this deny to allow?

    *shape* selects the deny wording when the marker does not disarm the
    call. The advisory is written to stderr only AFTER the bypass-log
    append has succeeded.
    """
    kind = marker_kind(marker) if kind is None else kind
    if kind == "symlink":
        sys.stderr.write(SYMLINK_WARNING_TEMPLATE.format(marker=OPT_OUT_MARKER))
        return deny_reason(branch, shape)
    if kind != "file":
        return deny_reason(branch, shape)
    if not commit:
        error = ledger_writable(repo_root)
        return None if error is None else _audit_failure(branch, "marker_disarm", error)
    error = log_marker_bypass(
        repo_root, action="marker_disarm", branch=branch, tool=tool, target=target
    )
    if error is not None:
        return _audit_failure(branch, "marker_disarm", error)
    sys.stderr.write(ADVISORY_TEMPLATE.format(marker=OPT_OUT_MARKER, branch=branch))
    return None


def grant_agent_memory_exemption(
    by_repo: dict[Path, tuple[Path, Path, Path, str]],
    tool: str,
) -> str | None:
    """Audit + allow the agent-memory exemption across every DISTINCT repo
    in *by_repo*. Returns a deny reason if the call cannot be fully
    audited, else None to allow.

    DECIDE (probe every repo's ledger is writable) before COMMIT (append):
    a later repo's unauditable state must not leave an earlier repo with a
    durable "allowed" record for a call that, overall, could not be fully
    audited.
    """
    for _target, repo_root, _git_dir, branch in by_repo.values():
        error = ledger_writable(repo_root)
        if error is not None:
            return _audit_failure(branch, "agent_memory_write", error)
    for target, repo_root, _git_dir, branch in by_repo.values():
        error = log_marker_bypass(
            repo_root,
            action="agent_memory_write",
            branch=branch,
            tool=tool,
            target=target,
        )
        if error is not None:
            return _audit_failure(branch, "agent_memory_write", error)
    return None
