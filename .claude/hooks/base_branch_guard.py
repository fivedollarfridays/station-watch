#!/usr/bin/env python3
"""PreToolUse hook: protected base-branch guard.

Denies file mutations and git commits while a paircoder-managed checkout is
sitting on its default branch or ``dev``, unless the session carries the
engage-driver dispatch marker. That single gate catches the wrong-role,
wrong-branch and wrong-session-type failures at once -- including an
orchestrator session drifting into inline implementation on dev.

Two-sided failure posture:
  * fail CLOSED on the policy match itself -- a matched violation is denied,
    exit 2, reason on stderr (the convention `bpsai_pair.commands.enforce`
    already uses).
  * fail OPEN on the guard's own errors -- a hook that bricks all editing
    because of an odd filesystem or a malformed event is worse than the
    violation it guards. Errors are logged to stderr and the tool is allowed.

The engage driver marker is ``BPSAI_ENGAGE_MODE=1``, set on every headless
driver subprocess by ``ClaudeCodeAdapter._build_env``. Engage owns
branch-cutting for its drivers, so its sessions are exempt by construction.

Runtime contract: stdlib only, plain ``python3``, no ``bpsai_pair`` import --
this runs on operator machines with no PairCoder venv.

ESCAPE HATCH (audited bypass): create ``.paircoder/hooks/base_branch_guard.off``
in the repo root (an empty file is enough). Writing that specific path is
itself exempted from this guard, so the hatch is reachable even from a
session the guard has already blocked -- unlike editing
``.claude/settings.json``, which is a mutating call the guard would deny on
the very branch it is meant to rescue you from. The marker is untracked,
per-repo state (not payload), so its presence is visible in ``git status``
-- that visibility IS the audit. Do not add a silent env kill switch. The
guard prints an advisory to stderr whenever the marker actually disarms a
match, so opt-out use stays visible even when nothing is denied.

PERSISTENCE IS INTENTIONAL: the marker does not expire and survives
``upgrade``/config-sync. The use case is ops-journal repos that are
legitimately, permanently exempt -- not a one-shot unblock. The
compensating control for that permanence is audit logging, below.

AUDITED, NOT JUST VISIBLE: ``git status`` visibility is necessary but not
sufficient -- both places the marker disarms an outcome (creating/editing
the marker itself, and the marker being present when it flips an
otherwise-denied edit to allowed) append one record to
``<repo_root>/.paircoder/history/bypass_log.jsonl`` -- the SAME ledger the
CLI's own ``core.bypass_log`` writes. The append is best-effort, but on
failure the guard does NOT fall back to exempting unaudited: it denies
instead, since an audited bypass that could not be audited is not one this
hook may grant.

TAMPERING, NOT AN OPT-OUT: a symlink AT the marker path is never honored as
the marker, in either direction -- the guard stays ARMED, a warning goes to
stderr, and no bypass is logged.

UNPROTECTED BRANCHES SHORT-CIRCUIT BEFORE ANY MARKER LOGIC: the marker
(present, absent, written, or tampered) has zero effect on the outcome of a
call on a branch this guard does not protect.

EVERY PRESENT PATH KEY IS JUDGED, NOT JUST ONE: ``NotebookEdit`` carries
both ``file_path`` and ``notebook_path`` in its ``tool_input``, and only
``notebook_path`` is the actual write target -- but a malformed or
adversarial event can populate both, so every present key is resolved and
judged independently.

CONTEXT-AWARE REMEDY, KEYED ON CHANGE SHAPE: the deny message names a
shape-appropriate remedy (``upgrade``/``bookkeeping``/``code``) instead of
unconditionally prescribing ``bpsai-pair engage``, derived solely from the
same path data already resolved for each protected target.

WORKTREE-SCOPED ENGAGE EXEMPTION: ``BPSAI_ENGAGE_MODE=1`` only exempts a
call whose target(s) resolve inside the run's OWN declared worktree
(``BPSAI_ENGAGE_WORKTREE``) -- fail-closed on a missing scope.

DEGRADED BANNER CLAIMS ONLY WHAT IT VERIFIED: when the sibling
import fails, the banner names the missing payload files and reports the
enforcement layer's OWN answer to ``git_commit_guard.py --self-check``
(or says UNVERIFIED). It no longer recites that the commit layer "still
enforced real commits" -- that claim was false in the very failure it was
printed for, since that layer imports this same facade.

MODULE LAYOUT: this file is the executable hook entry point (unchanged
wiring: ``.claude/settings.json`` still runs
``python3 .claude/hooks/base_branch_guard.py``) AND the stable import
facade every consumer -- ``git_commit_guard.py`` foremost -- imports from.
The actual logic lives in sibling modules in this same directory
(``base_branch_guard_constants``/``_checkout``/``_targets``/``_parser``/
``_bypass_log``/``_engage``/``_arms``/``_evaluate``); every symbol a
consumer could previously reach as ``base_branch_guard.<name>`` is
re-exported below, under its ORIGINAL name (private names included, via an
alias where the underlying implementation was renamed to a public name in
its new home module).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# A real hook invocation is a fresh `python3 <this file>.py` subprocess in
# a consumer's checkout -- importing the sibling modules below (or being
# imported itself, by `git_commit_guard.py`) would otherwise leave
# `__pycache__/*.pyc` build artifacts sitting untracked in the payload
# tree, dirtying `git status` in every managed repo forever. Set BEFORE
# any import (including the sibling ones) so nothing downstream in this
# process writes bytecode; mirrors the test harness's own
# `sys.dont_write_bytecode` shim for the exact same reason.
sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from base_branch_guard_arms import (
        decide_then_commit as _decide_then_commit,
        evaluate_marker_disarm as _evaluate_marker_disarm,
        evaluate_marker_write as _evaluate_marker_write,
        grant_agent_memory_exemption as _grant_agent_memory_exemption,
    )
    from base_branch_guard_bypass_log import (
        ledger_writable as _ledger_writable,
        log_marker_bypass as _log_marker_bypass,
        marker_kind as _marker_kind,
        same_path as _same_path,
        write_bypass_entry as _write_bypass_entry,
    )
    from base_branch_guard_checkout import (
        current_branch,
        find_checkout,
        protected_branches,
        resolve_checkout as _resolve_checkout,
        _strip_prefix,
    )
    from base_branch_guard_constants import (
        ADVISORY_TEMPLATE,
        AGENT_MEMORY_BOUNDARY,
        AUDIT_FAILURE_TEMPLATE,
        BOOKKEEPING_DENY_TEMPLATE,
        BOOKKEEPING_SHAPE_FILES,
        BOOKKEEPING_SHAPE_ROOTS,
        BYPASS_LOG_REL,
        BYPASS_TYPE_BY_ACTION as _BYPASS_TYPE_BY_ACTION,
        CONTROL_OPERATOR_TOKENS as _CONTROL_OPERATOR_TOKENS,
        DEFAULT_PROTECTED,
        DENY_TEMPLATE,
        DENY_TEMPLATE_BY_SHAPE as _DENY_TEMPLATE_BY_SHAPE,
        ENGAGE_MARKER,
        ENGAGE_WORKTREE_MARKER,
        ENV_ASSIGNMENT_RE as _ENV_ASSIGNMENT_RE,
        GENERIC_DENY_TEMPLATE,
        GIT_COMMIT_RE,
        GIT_DASH_C_RE,
        GIT_VALUE_FLAGS as _GIT_VALUE_FLAGS,
        LEGACY_BYPASS_LOG_REL,
        MUTATING_TOOLS,
        OPT_OUT_MARKER,
        PATH_KEYS,
        SYMLINK_WARNING_TEMPLATE,
        UPGRADE_DENY_TEMPLATE,
        UPGRADE_SHAPE_FILES,
        UPGRADE_SHAPE_ROOT,
        UPGRADE_SHAPE_SUFFIXES,
    )
    from base_branch_guard_engage import (
        engage_mode_exempts as _engage_mode_exempts,
        engage_worktree_paths as _engage_worktree_paths,
        log_engage_exemption as _log_engage_exemption,
        path_within_worktrees as _path_within_worktrees,
    )
    from base_branch_guard_evaluate import evaluate
    from base_branch_guard_parser import (
        _fallback_touched_directory,
        _is_git_token,
        _matches_git_basename,
        _resolve_against,
        _skip_env_assignments,
        touched_directories,
        touched_directory,
    )
    from base_branch_guard_targets import (
        classify_change_shape,
        deny_reason as _deny_reason,
        is_agent_memory_write,
        mutating_target,
        mutating_targets,
    )

    # Every name above is part of the facade's re-export contract (see the
    # module docstring's "MODULE LAYOUT" section) -- listed here, not
    # actually unused, so the linter's unused-import check doesn't flag a
    # consumer-facing re-export as dead code.
    __all__ = [
        "ADVISORY_TEMPLATE",
        "AGENT_MEMORY_BOUNDARY",
        "AUDIT_FAILURE_TEMPLATE",
        "BOOKKEEPING_DENY_TEMPLATE",
        "BOOKKEEPING_SHAPE_FILES",
        "BOOKKEEPING_SHAPE_ROOTS",
        "BYPASS_LOG_REL",
        "DEFAULT_PROTECTED",
        "DENY_TEMPLATE",
        "ENGAGE_MARKER",
        "ENGAGE_WORKTREE_MARKER",
        "GENERIC_DENY_TEMPLATE",
        "GIT_COMMIT_RE",
        "GIT_DASH_C_RE",
        "LEGACY_BYPASS_LOG_REL",
        "MUTATING_TOOLS",
        "OPT_OUT_MARKER",
        "PATH_KEYS",
        "SYMLINK_WARNING_TEMPLATE",
        "UPGRADE_DENY_TEMPLATE",
        "UPGRADE_SHAPE_FILES",
        "UPGRADE_SHAPE_ROOT",
        "UPGRADE_SHAPE_SUFFIXES",
        "_BYPASS_TYPE_BY_ACTION",
        "_CONTROL_OPERATOR_TOKENS",
        "_DENY_TEMPLATE_BY_SHAPE",
        "_ENV_ASSIGNMENT_RE",
        "_GIT_VALUE_FLAGS",
        "_decide_then_commit",
        "_deny_reason",
        "_engage_mode_exempts",
        "_engage_worktree_paths",
        "_evaluate_marker_disarm",
        "_evaluate_marker_write",
        "_fallback_touched_directory",
        "_grant_agent_memory_exemption",
        "_is_git_token",
        "_ledger_writable",
        "_log_engage_exemption",
        "_log_marker_bypass",
        "_marker_kind",
        "_matches_git_basename",
        "_path_within_worktrees",
        "_resolve_against",
        "_resolve_checkout",
        "_same_path",
        "_skip_env_assignments",
        "_strip_prefix",
        "_write_bypass_entry",
        "classify_change_shape",
        "current_branch",
        "evaluate",
        "find_checkout",
        "is_agent_memory_write",
        "mutating_target",
        "mutating_targets",
        "protected_branches",
        "touched_directories",
        "touched_directory",
    ]
except Exception as _sibling_import_error:  # noqa: E402
    # DEGRADED, NOT SILENT (round-2 trio review, P2): a missing, unreadable,
    # or syntax-broken sibling still fails OPEN -- bricking every Bash/
    # Edit/Write call because ONE payload file is broken is the wrong
    # trade-off for the ADVISORY layer (git_commit_guard.py's git-native
    # pre-commit hook is the real enforcement backstop and is unaffected,
    # since it only reuses functions it successfully imported). But
    # degraded operation used to be INVISIBLE: this hook's own
    # `BUNDLED_HOOK_FILES` gap (a real production bug this decomposition
    # found) proves partial-sync happens for real, not just in theory. Two
    # compensating controls, both best-effort and never allowed to flip
    # fail-open into fail-closed themselves: an unmissable stderr banner
    # naming the broken module, and one typed row per invocation appended
    # to the SAME unified bypass ledger every other exemption in this
    # guard writes to (`bypass_type: "base_branch_guard_degraded_fail_
    # open"`, registered for `bpsai-pair audit bypasses`/`summary` the
    # same way the sibling modules' own bypass rows are).
    #
    # Self-contained by necessity: the very modules that would normally
    # supply checkout resolution and ledger-writing (`base_branch_guard_
    # checkout`/`_bypass_log`) are exactly what may have failed to import
    # here, so this branch duplicates the minimum of each rather than
    # depending on them.
    #
    # The error is captured into a plain string HERE, not read from
    # `_sibling_import_error` inside `main()` below: Python deletes an
    # `except ... as name` binding when the except block exits, and
    # `main()` (a closure over this scope) is not actually CALLED until
    # `__main__` much later -- by then the name is gone, and referencing
    # it would raise `NameError`, not report the import failure.
    _sibling_import_message = str(_sibling_import_error)
    _degraded_module_name = getattr(_sibling_import_error, "name", None) or (
        "a base_branch_guard sibling module"
    )

    # Deliberately narrow: `\bgit\b ... \bcommit\b` on the SAME line as a
    # rough stand-in for the primary parser's tokenized detection, which
    # is unavailable here (its own module may be the broken one). False
    # positives on this narrow check only deny a Bash call that merely
    # LOOKS like a commit while degraded -- annoying, never a bypass;
    # false negatives fall through to the ordinary fail-open below, same
    # as any other Bash call while degraded.
    _DEGRADED_GIT_COMMIT_RE = re.compile(r"\bgit\b[^\n]*?\bcommit\b")

    # This banner used to assert "git_commit_guard.py (the
    # git-native pre-commit layer) still enforced real commits" -- printed
    # verbatim in the exact failure where that layer was ALSO down,
    # because it imports this same facade. The claim was never verified;
    # it recited what is normally true. It is now replaced by two things
    # this hook can actually establish for itself: which payload files are
    # missing, and what the commit layer says about ITSELF when asked.
    _DEGRADED_BANNER = (
        "\n"
        "==========================================================\n"
        "base_branch_guard: DEGRADED -- sibling module import failed\n"
        "  ({module}: {message})\n"
        "  payload: {payload}\n"
        "  Protected-branch enforcement for Edit/Write/Bash calls from\n"
        "  THIS hook is NOT ACTIVE.\n"
        "  commit layer: {commit_layer}\n"
        "  Re-sync the .claude/hooks payload: run `bpsai-pair upgrade`\n"
        "  from your installed CLI (no CLI version change needed -- it\n"
        "  re-syncs hooks from what is already installed). If that does\n"
        "  not restore this layer, the installed CLI itself may be stale\n"
        "  or broken -- fix the install (e.g. `pip install --upgrade\n"
        "  bpsai-pair`) first, then re-run upgrade.\n"
        "==========================================================\n"
    )

    # This facade's own copy of the required-file list, deliberately not
    # imported from anywhere: the module that would hold it shared is
    # exactly the kind of module that may be missing here.
    _DEGRADED_REQUIRED_FILES = (
        "base_branch_guard.py",
        "base_branch_guard_arms.py",
        "base_branch_guard_bypass_log.py",
        "base_branch_guard_checkout.py",
        "base_branch_guard_constants.py",
        "base_branch_guard_engage.py",
        "base_branch_guard_evaluate.py",
        "base_branch_guard_heredoc.py",
        "base_branch_guard_parser.py",
        "base_branch_guard_targets.py",
    )

    _DEGRADED_HOOKS_DIR = Path(__file__).resolve().parent

    def _degraded_payload_report() -> str:
        """Which required payload files are absent, by name (AC3).

        A partial sync is the trigger for this whole path, so naming the
        gap is the single most useful line in the banner -- an import
        traceback names only the FIRST casualty.
        """
        # Kept split: collapses to a 101-char line at line-length=100.
        missing = [
            name
            for name in _DEGRADED_REQUIRED_FILES
            if not (_DEGRADED_HOOKS_DIR / name).is_file()
        ]  # fmt: skip
        if not missing:
            return "all required files present (a file is present but unusable)"
        return "MISSING file(s): " + ", ".join(missing)

    # Probed only where the answer changes an outcome. This hook is
    # PreToolUse -- it fires on EVERY Edit/Write/Bash -- and a degraded
    # payload can persist for a whole session, so a per-call interpreter
    # spawn was a real interactive-latency regression. On calls that
    # cannot be a commit, the banner says the layer was not probed rather
    # than implying it was checked.
    _COMMIT_LAYER_NOT_PROBED = (
        "not probed for this call (probed only on commit-shaped commands)"
    )  # fmt: skip

    def _degraded_commit_layer(cwd: str) -> tuple:
        """``(status line, verdict)`` from ONE probe of the commit layer.

        *verdict* is ``"allow"``/``"deny"``/``None`` (unknown). Run in the
        EVENT's cwd, not this process's: in a multi-checkout session they
        differ, and reporting another repo's answer is the same
        "asserts what it did not verify about this call" failure this
        banner exists to retire.
        """
        script = _DEGRADED_HOOKS_DIR / "git_commit_guard.py"
        if not script.is_file():
            return "UNVERIFIED -- git_commit_guard.py is not installed here", None
        try:
            probe = subprocess.run(
                [sys.executable, str(script), "--verdict"],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=cwd,
            )
        except Exception as exc:
            return f"UNVERIFIED -- probe failed ({type(exc).__name__}: {exc})", None
        state = verdict = None
        for line in (probe.stdout or "").splitlines():
            if line.startswith("state: "):
                state = line[len("state: ") :]
            elif line.startswith("verdict: "):
                verdict = line[len("verdict: ") :]
        if state is None:
            return f"UNVERIFIED -- no answer (rc={probe.returncode})", None
        if verdict is None or probe.returncode not in (0, 1):
            return state, None
        return state, ("allow" if probe.returncode == 0 else "deny")

    def _degraded_repo_root(start: Path) -> Path | None:
        for directory in (start, *start.parents):
            if (directory / ".git").exists() and (directory / ".paircoder").is_dir():
                return directory
        return None

    def _log_degraded_fail_open(cwd: str) -> None:
        """Best-effort, audited note that this call ran degraded --
        never raises, never turns the fail-open into a fail-closed on
        its own failure (the ledger write is a courtesy here, not a
        gate, unlike every OTHER bypass this guard logs)."""
        repo_root = _degraded_repo_root(Path(cwd))
        if repo_root is None:
            return
        timestamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
        entry = {
            "timestamp": timestamp,
            "command": "base_branch_guard",
            "target": _sibling_import_message,
            "bypass_type": "base_branch_guard_degraded_fail_open",
            "gate": "base_branch_guard",
            "action": "degraded_fail_open",
            "branch": "",
            "tool": "",
        }
        log_path = repo_root / ".paircoder" / "history" / "bypass_log.jsonl"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def main() -> int:  # type: ignore[no-redef]
        event: dict = {}
        try:
            parsed = json.loads(sys.stdin.read() or "{}")
            if isinstance(parsed, dict):
                event = parsed
        except Exception:
            pass
        cwd = str(event.get("cwd") or os.getcwd())

        command = ""
        if str(event.get("tool_name", "")) == "Bash":
            command = str((event.get("tool_input") or {}).get("command", ""))
        commit_shaped = bool(command) and bool(_DEGRADED_GIT_COMMIT_RE.search(command))
        status, verdict = (
            _degraded_commit_layer(cwd)
            if commit_shaped
            else (_COMMIT_LAYER_NOT_PROBED, None)
        )  # fmt: skip

        sys.stderr.write(
            _DEGRADED_BANNER.format(
                module=_degraded_module_name,
                message=_sibling_import_message,
                payload=_degraded_payload_report(),
                commit_layer=status,
            )
        )
        _log_degraded_fail_open(cwd)

        if not commit_shaped:
            return 0
        # The degraded deny used to be unconditional: no branch, no engage
        # marker, no opt-out marker. That blocked engage drivers on their
        # own feature branches -- a ratified AC, and the exact commit the
        # ENFORCEMENT layer allows -- so the two layers contradicted each
        # other while the banner reported on neither. This layer cannot
        # import the siblings that answer those questions (they are what
        # broke), and a third copy of the rules is the drift this whole
        # change is retiring, so it relays the commit layer's verdict.
        if verdict == "allow":
            sys.stderr.write(
                "base_branch_guard: DEGRADED -- allowing this commit-shaped "
                "Bash call: the commit layer reports it would not refuse a "
                f"commit here ({status.strip()}).\n"
            )
            return 0
        why = (
            "the commit layer would refuse a commit here"
            if verdict == "deny"
            else f"no verdict could be obtained from the commit layer ({status.strip()})"
        )
        sys.stderr.write(
            "base_branch_guard: DEGRADED -- denying this Bash call because its "
            f"text looks like a git commit and {why}; every other call is "
            "still allowed fail-open while degraded.\n"
        )
        return 2

else:

    def main() -> int:
        """Read the hook event on stdin; deny (2) on a policy match, else allow."""
        try:
            event = json.loads(sys.stdin.read() or "{}")
            # Kept split: collapses to a 91-char line at line-length=100.
            reason = (
                evaluate(event, dict(os.environ)) if isinstance(event, dict) else None
            )  # fmt: skip
        except Exception as exc:  # fail open: never brick editing on our own bug
            sys.stderr.write(f"base_branch_guard: fail-open ({exc})\n")
            return 0
        if reason is None:
            return 0
        sys.stderr.write(f"BLOCKED BY ENFORCEMENT GATE\n\n{reason}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
