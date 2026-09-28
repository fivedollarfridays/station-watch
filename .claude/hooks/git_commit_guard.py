#!/usr/bin/env python3
"""Git-native pre-commit hook: protected base-branch enforcement.

``base_branch_guard.py`` (the PreToolUse hook) matches COMMAND TEXT emitted
by Claude tool calls -- a ``git commit`` typed into a shell it never sees,
or run through a wrapper its regex does not match, sails through
ungoverned. It also evaluates at command START, before the command has
actually done anything, so state can drift between the check and the
operation it was checking.

This script closes both gaps by moving enforcement into git's OWN commit
lifecycle instead of guessing at it from text: installed as
``.git/hooks/pre-commit`` (see ``bpsai_pair.commands.upgrade_git_hook``), it
fires on every commit regardless of how it was invoked -- Claude, a raw
shell, a script, a GUI client -- because git itself is calling it, evaluated
against the ACTUAL branch at the ACTUAL moment of the commit. The
PreToolUse guard still runs too, as fast-path advice; this is the
enforcement.

Reuses ``base_branch_guard.py``'s branch/marker/bypass-log primitives (same
directory, imported as a sibling module) rather than re-implementing them,
so the marker opt-out, bypass logging, and ``BPSAI_ENGAGE_MODE`` exemption
stay byte-compatible with the PreToolUse guard's semantics -- one source of
truth for what "protected" and "opted out" mean, enforced at two layers.

DEGRADED MEANS REFUSE, NOT WAVE THROUGH: that shared import is
also a shared FAILURE mode. One missing or renamed
``base_branch_guard_*.py`` used to take BOTH layers down at once -- the
facade degraded to its advisory fail-open, and this hook hit an
``ImportError`` (or an ``AttributeError`` on a hollow, partially-bound
facade), printed one stderr line, exited 0, and let a commit land on a
protected branch with no audit row anywhere. The advisory layer's
fail-open is the right trade for ITS blast radius (bricking every
Edit/Write over one broken payload file is worse than the violation);
the enforcement layer's is not. So this script now: (a) checks payload
integrity and facade completeness before trusting either, and (b) on any
defect, evaluates the commit through a SELF-CONTAINED path that imports
nothing -- refusing a protected-branch commit, auditing the refusal, and
still honoring the two ratified exemptions (worktree-scoped engage
marker, persistent opt-out marker) so the refusal stays recoverable.
Unmanaged checkouts and feature branches are unaffected, degraded or not.

DECLARED POSTURE, TIGHTEN-ONLY FROM REPO SCOPE (ratified
2026-08-26): what this hook does on its own defect is declared, not
assumed, through ``guards.enforcement.on_error`` (``fail-closed``
default) and documented in ``docs/config-reference.md`` -- but the two
directions come from different SCOPES. A repo may TIGHTEN: a
``fail-closed`` in the repo-tracked ``.paircoder/config.yaml`` is
honoured. The LOOSENING direction is honoured only from the user/machine
-level ``~/.paircoder/preferences.yaml``, outside every checkout, because
a repo-tracked ``fail-open`` would let the artifact this gate protects
author the gate's own posture -- via, among other routes, a GitHub web-UI
commit no local hook observes. A repo-level ``fail-open`` is REFUSED,
announced with the remedy named, and recorded in the ledger as a refused
declaration. A symlink at either path is treated as tampering, and a
user-level file that resolves back INSIDE the guarded repo is ignored.

WHAT THAT SCOPE DOES NOT CLOSE, stated plainly: the boundary is a
COMMIT boundary, not a process boundary. It defends against a posture
ARRIVING in a repo -- a pull request, a web-UI edit, a synced template.
It does not defend against a same-UID local process, which includes the
agent this gate constrains: that process can write ``~/.paircoder/``
directly, and the CLI ships a verb (``config prefs set``) that does
exactly that. That residual is accepted, not overlooked. Its compensating
controls are two: the grant is audited and announced by the CLI at the
moment it is made, and the loosened posture is announced HERE on every
invocation for as long as it is active. A loosening you can see on every
commit is a different animal from one nobody ever hears about.

Both postures audit; only the exit code differs -- except that a
bypass the ledger could NOT record is refused rather than granted,
and an error inside the degraded evaluator itself always
refuses (the code that reads the declaration is part of what failed).
While a non-default posture is active it is ANNOUNCED on every
invocation, healthy or degraded: a silently weakened guard is
indistinguishable from a healthy one. Under the default, a hook bug
blocking a legitimate commit is recoverable through the already-audited
escape hatches -- the persistent ``.paircoder/hooks/base_branch_guard.off``
marker or the worktree-scoped engage exemption -- without editing this
script or disabling the gate.

SELF-CHECK IS A CONTRACT, NOT A CONVENIENCE: ``--self-check`` reports
this hook's own armed/degraded state (exit 0 armed, 1 not, reason on
stdout) -- and "armed" means all three of: payload complete, the
integrity cross-check clean, and git actually wired to call this hook
(`core.hooksPath`-aware). A fresh clone has the payload and no
`.git/hooks/pre-commit`, and used to be reported ARMED. ``base_branch_guard.py``'s degraded banner CALLS it rather than
asserting from memory what this layer is doing -- the banner used to
claim "git_commit_guard.py still enforced real commits" in the exact
failure where it did not.

KNOWN SOFT SPOT: the ``BPSAI_ENGAGE_MODE`` exemption is a plain
environment variable, readable and settable by the very process this
enforcement layer exists to constrain -- a raw shell that can set env
vars can set this one too. It stays (it is a REQUIRED, ratified-AC
exemption: engage drivers must never be blocked by this guard, and it
must stay byte-compatible with ``base_branch_guard.py``'s own exemption),
but it is now AUDITED: every commit it exempts on an otherwise-protected
branch appends a ``gate: "git_commit_guard", action: "engage_mode_exempt"``
record to the bypass log, so the exemption's use stays visible even
though its trigger is self-reported. Stronger run-binding (an identity
this script cannot spoof from inside the same process) is tracked
follow-up work, not a gap this audit-only mitigation closes.

WORKTREE-SCOPED, LIKE THE PRETOOLUSE GUARD: the flag alone is no longer
sufficient -- ``guard._engage_mode_exempts`` (the shared primitive this
module reuses rather than re-implementing) also requires the commit's own
checkout root to resolve inside the run's declared worktree
(``guard.ENGAGE_WORKTREE_MARKER``). A driver dispatched into one checkout
committing onto a DIFFERENT protected checkout it happens to have on disk
-- the exact incident shape this fix closes -- is refused even though the
flag is present, because that checkout was never the one the run declared.
Fail-closed on a missing scope too: a commit carrying the mode flag and NO
declared worktree gets no exemption, since the specimen this guard's own
docstring calls a "KNOWN SOFT SPOT" is reproduced precisely by omitting
the companion.

No "target is the marker file itself" case here, unlike the PreToolUse
guard: by the time ``pre-commit`` fires, a marker the operator created
already exists on disk (however it got there), so the ordinary disarm
check already covers committing the marker's own creation. There is no
chicken-and-egg problem to special-case at this layer.

Runtime contract: stdlib only, plain ``python3``, no ``bpsai_pair``
import -- same as ``base_branch_guard.py``; this runs on operator machines
with no PairCoder venv.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# `base_branch_guard.py` is now a facade over several sibling modules --
# importing all of them (transitively, below) would otherwise leave
# `__pycache__/*.pyc` build artifacts untracked in every managed repo's
# payload tree. Set BEFORE the import so nothing this process imports
# writes bytecode.
sys.dont_write_bytecode = True

HOOKS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOKS_DIR))
try:
    import base_branch_guard as guard  # noqa: E402
except Exception as _sibling_import_error:  # noqa: E402
    # NOT an exit: a broken sibling no longer ends this hook's judgement,
    # it only downgrades it to the self-contained degraded path below
    # The failure is captured as a plain string here because
    # Python deletes an `except ... as name` binding at block exit, long
    # before main() reads it.
    guard = None  # noqa: E402
    _IMPORT_DEFECT: str | None = f"sibling import failed: {_sibling_import_error}"
    _IMPORT_ERROR_TYPE = type(_sibling_import_error).__name__
else:
    _IMPORT_DEFECT = None
    _IMPORT_ERROR_TYPE = ""

# The posture READER, a sibling shipped alongside this script (it is in
# `upgrade_session_hooks.BUNDLED_HOOK_FILES`, ordered before this file so
# a torn sync cannot leave the importer without it). Guarded for the same
# reason the facade import above is: an ImportError at module scope would
# refuse EVERY commit in the checkout, feature branches included, which is
# a blast radius this layer has never claimed. Without the reader this
# gate cannot know what was declared, so it does not guess -- the stubs
# below take the hard default and say why.
try:
    from git_commit_guard_posture import (  # noqa: E402
        FAIL_CLOSED,
        FAIL_OPEN,
        posture_facts,
        posture_notices,
        resolve_posture,
        user_posture_path,
    )
except Exception as _posture_import_error:  # noqa: E402
    _POSTURE_READER_DEFECT: str | None = str(_posture_import_error)
    # Deliberate copies, not a second source: a drift test pins them to
    # the reader's own values.
    FAIL_CLOSED = "fail-closed"  # noqa: E402
    FAIL_OPEN = "fail-open"  # noqa: E402

    def user_posture_path() -> Path | None:  # noqa: E402
        return None

    def resolve_posture(repo_root: Path) -> tuple:  # noqa: E402
        return FAIL_CLOSED, None, "unset", "unset"

    def posture_notices(repo_root: Path) -> list:  # noqa: E402
        return [POSTURE_READER_MISSING.format(error=_POSTURE_READER_DEFECT)]

    def posture_facts(repo_state: str, posture: str) -> tuple:  # noqa: E402
        return ()

else:
    _POSTURE_READER_DEFECT = None

# Every file that must be present for this hook's payload to be whole --
# the shared advisory modules AND this hook's own posture reader and the
# scanner that reader rests on. Checked on EVERY invocation, not at sync
# time: `upgrade_session_hooks.BUNDLED_HOOK_FILES` is a sync-time list in
# the CLI, so between a partial sync and the next `upgrade` nothing
# noticed a hole. A drift test keeps this tuple, that one, and the real
# payload directory in agreement -- derived from the IMPORT GRAPH of both
# entry-point hooks, not from a filename prefix, so the next sibling
# cannot fall out of the sweep by being named something else.
#
# The reader is here as well as behind a guarded import: the fallback
# keeps a missing one from bricking feature branches, this sweep keeps
# `--self-check` honest. Fail-closed and HONEST are both required.
REQUIRED_PAYLOAD_FILES = (
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
    "git_commit_guard_posture.py",
    "git_commit_guard_scan.py",
)

# Every `guard.<name>` this script reaches for. A partially-bound facade
# (`from X import ...` binds names as it goes, so an import failing
# halfway leaves the earlier names reachable) imports "successfully" and
# only reveals the hole at attribute-access time, mid-commit. Checked up
# front so a hollow facade is a DEFECT, never a surprise `AttributeError`
# swallowed by main()'s fail-open.
REQUIRED_GUARD_ATTRS = (
    "AUDIT_FAILURE_TEMPLATE",
    "BYPASS_LOG_REL",
    "DENY_TEMPLATE",
    "OPT_OUT_MARKER",
    "_engage_mode_exempts",
    "_evaluate_marker_disarm",
    "_log_marker_bypass",
    "_marker_kind",
    "_write_bypass_entry",
    "current_branch",
    "find_checkout",
    "protected_branches",
)

# `bpsai-pair init` writes this ONE-SHOT marker when the checkout is
# already sitting on a protected branch at init time (the common case --
# the operator has not created a feature branch yet), so the very first
# scaffolding commit is not denied out-of-box. Unlike
# `base_branch_guard.OPT_OUT_MARKER` (permanent by design), this one is
# consumed -- deleted -- immediately after its bypass is durably logged,
# so a second commit on the same protected branch is guarded normally
# again. It is scoped to this git-native hook only; it has no bearing on
# the PreToolUse guard's Edit/Write checks.
INIT_ONCE_MARKER = Path(".paircoder/hooks/git_commit_guard.init-once")

INIT_ONCE_SYMLINK_WARNING = (
    "git_commit_guard: {marker} is a symlink -- treating as tampering, "
    "not a bypass. Guard remains ARMED.\n"
)

INIT_ONCE_CONSUMED_ADVISORY = (
    "git_commit_guard: one-shot init bypass consumed ({marker}) -- "
    "protected branch ({branch}) is guarded normally starting with the "
    "next commit.\n"
)


# --- Degraded posture -------------------------------------------------
# Everything below is deliberately SELF-CONTAINED: it must stay reachable
# when the sibling modules that would normally supply checkout
# resolution, the protected-branch set, the engage exemption and the
# ledger writer are exactly what is broken. Duplication here is the
# point, not missed extraction.

DEGRADED_PROTECTED = ("main", "master", "dev")


DEGRADED_OPT_OUT_MARKER = Path(".paircoder/hooks/base_branch_guard.off")

DEGRADED_BYPASS_LOG_REL = Path(".paircoder/history/bypass_log.jsonl")

DEGRADED_BANNER = (
    "\n"
    "==========================================================\n"
    "git_commit_guard: DEGRADED -- {defect}\n"
    "  This enforcement gate cannot trust the shared guard\n"
    "  payload, so a protected-branch commit is REFUSED (unless a\n"
    "  USER-level `guards.enforcement.on_error: fail-open` is\n"
    "  declared -- a repo may only TIGHTEN this gate) instead\n"
    "  of waved through. Either way the degradation is audited.\n"
    "  Re-sync the payload (`bpsai-pair upgrade` -- no CLI version\n"
    "  change needed, it re-syncs from your installed CLI) to\n"
    "  restore normal operation. If that does not help, the\n"
    "  installed CLI itself may be stale (`pip install --upgrade\n"
    "  bpsai-pair`).\n"
    "==========================================================\n"
)

DEGRADED_DENY_TEMPLATE = (
    "Protected base branch ({branch}). This enforcement gate is DEGRADED "
    "({defect}), so it refuses rather than assuming it may allow. Re-sync "
    "the `.claude/hooks` payload with `bpsai-pair upgrade` (no CLI version "
    "change needed), or commit from "
    "a feature branch. If this repo is legitimately exempt, the audited "
    "escape hatch still works while degraded: create "
    ".paircoder/hooks/base_branch_guard.off. To declare the opposite "
    "posture, set `guards.enforcement.on_error: fail-open` in {user} -- "
    "a repo-level declaration cannot loosen this gate."
)

DEGRADED_FAIL_OPEN_ADVISORY = (
    "git_commit_guard: DEGRADED on a protected branch ({branch}) -- allowed "
    "anyway because {source} declares "
    "`guards.enforcement.on_error: fail-open`. Audited as "
    "`git_commit_guard_{action}` in {log}.\n"
)

# An unrecordable bypass is not one this gate may grant -- the declared
# posture says what happens when the gate CAN record, not whether it must.
DEGRADED_UNAUDITABLE_DENY_TEMPLATE = (
    "Protected base branch ({branch}). This enforcement gate is DEGRADED "
    "({defect}) and {source} declares `guards.enforcement.on_error: "
    "fail-open` -- but the bypass could NOT be written to the audit ledger, "
    "so it was not granted. Fix the ledger path "
    "(.paircoder/history/bypass_log.jsonl) or re-sync the payload with "
    "`bpsai-pair upgrade` (no CLI version change needed)."
)

# Said when the posture reader itself is missing. Not silence: a gate
# that cannot read its own posture has fallen back to the hard default,
# and an operator with a declared `fail-open` is entitled to know their
# declaration is not being read.
POSTURE_READER_MISSING = (
    "git_commit_guard: the posture reader (git_commit_guard_posture.py) could "
    "not be loaded ({error}) -- no declared enforcement posture is being "
    "read; posture is fail-closed. Re-sync the payload with `bpsai-pair "
    "upgrade` (no CLI version change needed).\n"
)

AUDIT_WRITE_FAILED = (
    "git_commit_guard: NOT AUDITED -- could not append `{action}` to {log} ({error}).\n"
)

# A loosened posture that could not be SAID is not one this gate may
# apply. Same rule as the unauditable bypass one frame down: the
# announcement is the compensating control for a fail-open, so a
# fail-open the terminal never heard about is refused instead of
# granted. Scoped to the grant itself -- a failed announcement adds no
# refusal on a healthy commit, on a feature branch, or under the
# ratified engage exemption, all of which short-circuit before here.
POSTURE_UNANNOUNCEABLE_DENY = (
    "Protected base branch ({branch}). This enforcement gate is DEGRADED "
    "({defect}) and a `fail-open` posture is declared -- but the gate could "
    "not ANNOUNCE that posture, so the bypass was not granted. A silently "
    "weakened guard is indistinguishable from a healthy one. Fix stderr, or "
    "re-sync the payload with `bpsai-pair upgrade`."
)

# Whether THIS invocation managed to say the posture out loud. Read by
# the degraded verdict, which will not grant a bypass it could not
# announce. Defaults to True for the `--verdict`/`--self-check` probes,
# which deliberately do not announce (their caller discards stderr) and
# must still report what a real commit would get.
_POSTURE_ANNOUNCED = True

DEGRADED_SYMLINK_WARNING = (
    "git_commit_guard: {marker} is a symlink -- treating as tampering, "
    "not a bypass. Guard remains ARMED.\n"
)

# Kept split here and below: each collapses to a 89-100 char line at
# line-length=100, which a consumer config formats differently than this
# repo does -- the payload must be format-clean under both.
SELF_CHECK_OK = (
    "git_commit_guard: self-check ok -- payload complete, integrity "
    "cross-check clean, pre-commit hook wired: enforcement ARMED\n"
)

# ARMED is a claim about whether GIT WILL CALL this hook, and presence of
# the payload does not establish it: `git clone` never copies
# `.git/hooks`, so every fresh clone of every managed repo was reported
# armed while the enforcement layer was not wired at all. The self-check
# now looks, and says which of the two it verified.
SELF_CHECK_NOT_WIRED = (
    "git_commit_guard: self-check FAILED -- payload complete but NOT WIRED "
    "({detail}); this layer does not run for commits in this checkout. Run "
    "`bpsai-pair upgrade` to install the pre-commit hook.\n"
)

SELF_CHECK_FAILED = (
    "git_commit_guard: self-check FAILED -- DEGRADED ({defect}); commits on "
    "protected branches are {outcome} by this layer\n"
)

# What each posture means for the commit this hook is asked about. The
# advisory banner quotes the self-check line verbatim, so it inherits the
# banner's own rule: state what is true for THIS repo, not what is true in
# general.
SELF_CHECK_OUTCOME = {
    FAIL_CLOSED: "REFUSED",
    FAIL_OPEN: "ALLOWED (`fail-open` declared in {source})",
}


def _already_recorded(
    repo_root: Path, *, action: str, branch: str, code: str
) -> bool:  # fmt: skip
    """Is this posture FACT already in the ledger for *branch*?

    DECODED, not substring-matched. This ledger is tracked, so the party
    the scope rule constrains can append to it through a pull request --
    and a substring scan meant one forged, JSON-undecodable line
    ("noise \"action\": ... noise") suppressed the row permanently while
    `audit bypasses` skipped the forged line entirely. Undecodable lines
    are now somebody else's finding (`core.bypass_log` reports them); here
    they are simply not a match.
    """
    for line in _read_text_or_empty(repo_root / DEGRADED_BYPASS_LOG_REL).splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("action") != action or entry.get("branch") != branch:
            continue
        # The bounded code is part of the key, not decoration: two
        # declaration states share one action, so keying on the action
        # alone recorded whichever came first and swallowed the
        # transition to the other one entirely.
        if entry.get("target") == code:
            return True
    return False


def _record_posture_fact(
    repo_root: Path, *, branch: str, action: str, code: str
) -> None:  # fmt: skip
    """Record a posture FACT about this repo in the ledger, ONCE.

    Two facts use this: a repo-level `fail-open` this gate refused, and a
    `fail-open` that was actually in force. Both have to survive the
    terminal -- `audit bypasses` is how a fleet finds repos asking for a
    loosening, and how a discarded stderr stops being the end of the
    story.

    Written once per repo per branch key and STATE rather than once per
    commit: this
    ledger is TRACKED, auto-staged and committed, and a per-commit row for
    as long as the state persists would put this gate's own noise into
    every diff in the repo. Unprotected branches share one key (the empty
    string), since the fact is about the repo, not the branch.

    Only bounded vocabulary reaches the row -- never the source path,
    which names a home directory and would be published with the ledger.
    """
    if _already_recorded(repo_root, action=action, branch=branch, code=code):
        return
    _audit_or_warn(repo_root, action=action, branch=branch, code=code)


def announce_posture() -> None:
    """Say the posture out loud, before anything else can go wrong.

    Real commit invocations only: the ``--self-check``/``--verdict`` probes
    are spawned by the advisory layer with ``capture_output=True``, which
    throws stderr away -- those report the posture on STDOUT instead, via
    ``self_check_line``.

    Never raises -- an announcement that failed must not be what decides an
    ordinary commit -- but it is not silent about failing either: it
    records that it could not speak, and the degraded verdict refuses to
    grant a fail-open it could not announce.
    """
    global _POSTURE_ANNOUNCED
    _POSTURE_ANNOUNCED = True
    try:
        checkout = _degraded_checkout(Path.cwd())
        if checkout is None or not (checkout[0] / ".paircoder").is_dir():
            return
        for line in posture_notices(checkout[0]):
            sys.stderr.write(line)
        sys.stderr.flush()
        posture, _source, repo_state, _user_state = resolve_posture(checkout[0])
        branch = _degraded_protected_branch(checkout[1]) or ""
        for action, code in posture_facts(repo_state, posture):
            _record_posture_fact(checkout[0], branch=branch, action=action, code=code)
    except Exception:
        _POSTURE_ANNOUNCED = False


def defect(code: str, message: str) -> tuple:
    """A defect as ``(ledger code, operator message)``.

    The two are separated because they have different audiences and
    different safety rules. ``.paircoder/history/bypass_log.jsonl`` is
    TRACKED, auto-staged by this hook, and committed -- in a public repo
    -- so what lands there must be bounded and free of local absolute
    paths; raw exception text is neither. The operator's copy on stderr
    is not published and keeps the whole message.
    """
    return code[:200], message


def payload_defect() -> tuple | None:
    """Why this hook may not trust the shared payload, or None if it may.

    Ordered cheapest-and-most-actionable first: a missing FILE names
    itself (the operator's remedy is a re-sync), an import failure
    carries the interpreter's own message, and a hollow facade names the
    attributes that are gone. Payload basenames and attribute names are
    this module's OWN vocabulary and safe to record; an interpreter
    message is not, so only its exception class is.
    """
    missing = [
        name for name in REQUIRED_PAYLOAD_FILES if not (HOOKS_DIR / name).is_file()
    ]  # fmt: skip
    if missing:
        names = ", ".join(sorted(missing))
        return defect(
            f"missing_payload_files:{names}", f"missing payload file(s): {names}"
        )  # fmt: skip
    if _IMPORT_DEFECT is not None:
        return defect(f"sibling_import_failed:{_IMPORT_ERROR_TYPE}", _IMPORT_DEFECT)
    if _POSTURE_READER_DEFECT is not None:
        # A DIFFERENT guarded import than the one above (`base_branch_guard`
        # facade vs. `git_commit_guard_posture`, transitively including
        # `git_commit_guard_scan`) -- a file listed in
        # `REQUIRED_PAYLOAD_FILES` can be PRESENT (so the missing-file
        # sweep above says nothing) yet still unloadable, and until now
        # `_POSTURE_READER_DEFECT` was computed at import time but never
        # consulted here: the guard itself already falls back to
        # the fail-closed default in that state, but `--self-check`
        # reported "payload complete ... enforcement ARMED" regardless.
        # Kept split: collapses to a 98-char line at line-length=100.
        return defect(
            "posture_reader_import_failed", f"posture reader: {_POSTURE_READER_DEFECT}"
        )  # fmt: skip
    hollow = [name for name in REQUIRED_GUARD_ATTRS if not hasattr(guard, name)]
    if hollow:
        names = ", ".join(hollow)
        return defect(
            f"hollow_facade:{names}",
            f"hollow base_branch_guard facade, missing: {names}",
        )
    return None


def _read_text_or_empty(path: Path) -> str:
    """*path*'s text, or ``""`` when it cannot be read at all.

    Every read on the degraded path goes through this. An unreadable or
    non-UTF-8 `.git` file somewhere up the walk used to raise, and the
    exception became a REFUSAL two frames later -- breaking the one
    promise this path makes, that feature branches and non-checkouts are
    never bricked. "Cannot establish a protected branch" is not the same
    answer as "protected".
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _degraded_checkout(start: Path) -> tuple[Path, Path] | None:
    """``(repo_root, git_dir)`` for *start*, worktree layout included."""
    for directory in (start, *start.parents):
        marker = directory / ".git"
        if marker.is_dir():
            return directory, marker
        if marker.is_file():
            text = _read_text_or_empty(marker).strip()
            if text.startswith("gitdir:"):
                gitdir = Path(text.split(":", 1)[1].strip())
                if not gitdir.is_absolute():
                    gitdir = directory / gitdir
                return directory, gitdir
    return None


def _degraded_protected_branch(git_dir: Path) -> str | None:
    """The current branch IF it is protected, else None.

    Branch from ``HEAD``; protected set = the usual defaults PLUS this
    remote's actual default branch, because a repo whose default is
    neither ``main`` nor ``master`` would otherwise go unprotected
    precisely while degraded -- the hole this whole path exists to close.
    """
    head = _read_text_or_empty(git_dir / "HEAD").strip()
    if not head.startswith("ref:"):
        return None
    branch = _after(head.split(":", 1)[1].strip(), "refs/heads/")
    if branch is None:
        return None
    protected = set(DEGRADED_PROTECTED)
    common = git_dir
    commondir = _read_text_or_empty(git_dir / "commondir").strip()
    if commondir:
        candidate = Path(commondir)
        common = candidate if candidate.is_absolute() else git_dir / candidate
    default = _after(
        _read_text_or_empty(common / "refs" / "remotes" / "origin" / "HEAD").strip(),
        "ref: refs/remotes/origin/",
    )
    if default:
        protected.add(default)
    return branch if branch in protected else None


def _after(text: str, prefix: str) -> str | None:
    """The part of *text* after *prefix*, or None if it does not start
    there. Branch names are paths: ``feature/main`` is not ``main``."""
    if not text.startswith(prefix) or len(text) <= len(prefix):
        return None
    return text[len(prefix) :]


def _degraded_engage_exempts(repo_root: Path, env: dict) -> bool:
    """The ratified ``BPSAI_ENGAGE_MODE`` exemption, worktree-scoped.

    Answers the same question as the PAYLOAD copy of
    ``base_branch_guard_engage.engage_mode_exempts`` on the same inputs --
    fail-closed on a missing scope (the flag alone exempts nothing), and
    a lexically-similar sibling directory (``.../foo-evil`` beside a
    declared ``.../foo``) is not "inside". A parity TEST holds the two in
    agreement; this docstring used to claim byte-compatibility instead,
    and went on claiming it after the primitive changed under it.

    ``os.path.realpath`` on both sides, matching that primitive: a run
    that declares its worktree through a symlinked path addresses the
    same directory, and refusing it here would block an engage driver in
    exactly the state where recovery is hardest. The mirror-image escape
    (a symlink smuggling a literal path INTO a declared worktree) does
    not arise at this layer -- *repo_root* is derived from ``Path.cwd()``,
    and ``getcwd`` is canonical -- but resolving anyway is what keeps the
    two layers from disagreeing, which is the failure this evaluator
    exists to prevent.
    """
    if env.get("BPSAI_ENGAGE_MODE") != "1":
        return False
    declared = [
        os.path.realpath(part)
        for part in env.get("BPSAI_ENGAGE_WORKTREE", "").split(os.pathsep)
        if part
    ]
    if not declared:
        return False
    target = os.path.realpath(str(repo_root))
    return any(
        target == worktree or target.startswith(worktree + os.sep)
        for worktree in declared
    )  # fmt: skip


def _degraded_audit(
    repo_root: Path, *, action: str, branch: str, code: str
) -> str | None:  # fmt: skip
    """Append one typed row to the shared bypass ledger; return an error
    string on failure, else None.

    Self-contained on purpose (see the section header): the sibling that
    normally owns this write may be the broken one. The caller decides
    what a write failure means -- a refusal records best-effort, a
    marker-granted bypass does not proceed unaudited.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "command": "git_commit_guard",
        "target": code,
        "bypass_type": f"git_commit_guard_{action}",
        "gate": "git_commit_guard",
        "action": action,
        "branch": branch,
        "tool": "git-commit",
    }
    log_path = repo_root / DEGRADED_BYPASS_LOG_REL
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError as exc:
        return str(exc)
    return None


def _audit_or_warn(
    repo_root: Path, *, action: str, branch: str, code: str
) -> str | None:  # fmt: skip
    """`_degraded_audit`, with the failure said OUT LOUD.

    Every caller used to discard this return, so a degraded gate whose
    ledger write failed was invisible to `bpsai-pair audit bypasses` AND
    silent on the terminal -- the secondary finding of the audit this
    path exists to answer, reproduced inside its own fix. Callers still decide what a failure MEANS; this
    only guarantees it is never unsaid.
    """
    error = _degraded_audit(repo_root, action=action, branch=branch, code=code)
    if error is not None:
        sys.stderr.write(
            AUDIT_WRITE_FAILED.format(
                action=f"git_commit_guard_{action}",
                log=DEGRADED_BYPASS_LOG_REL,
                error=error,
            )
        )
    return error


def _degraded_posture_verdict(
    repo_root: Path, *, branch: str, defect: tuple, kind: str, audit: bool = True
) -> str | None:
    """Audit the degradation, then return a deny reason or None per the
    repo's DECLARED posture.

    The declaration governs the exit code ONLY when the audit row was
    actually written. A fail-open the ledger could not record is refused
    instead of granted -- the same doctrine `_consume_init_once_marker`
    and the marker-disarm branch already apply, and the same one seed
    doctrine 04 states: every bypass is explicit, reasoned, and LOGGED.
    """
    posture, source, _repo_state, _user_state = resolve_posture(repo_root)
    silent = posture == FAIL_OPEN and not _POSTURE_ANNOUNCED
    granted = posture == FAIL_OPEN and not silent
    action = f"{kind}_fail_open" if granted else f"{kind}_refusal"
    error = (
        _audit_or_warn(repo_root, action=action, branch=branch, code=defect[0])
        if audit
        else None
    )  # fmt: skip
    if silent:
        return POSTURE_UNANNOUNCEABLE_DENY.format(branch=branch, defect=defect[1])
    if posture == FAIL_OPEN and error is None:
        sys.stderr.write(
            DEGRADED_FAIL_OPEN_ADVISORY.format(
                branch=branch,
                action=action,
                log=DEGRADED_BYPASS_LOG_REL,
                source=source,
            )
        )
        return None
    if posture == FAIL_OPEN:
        return DEGRADED_UNAUDITABLE_DENY_TEMPLATE.format(
            branch=branch, defect=defect[1], source=source
        )  # fmt: skip
    user = user_posture_path()
    return DEGRADED_DENY_TEMPLATE.format(branch=branch, defect=defect[1], user=user)


def evaluate_degraded(
    cwd: Path, env: dict, defect: tuple, *, kind: str, audit: bool = True
) -> str | None:
    """Return a deny reason, or None to allow, using no sibling imports.

    Same short-circuit order as the healthy path: unmanaged checkouts and
    unprotected branches are allowed before any marker, exemption or
    posture declaration is consulted, so nothing is audited for a commit
    that was never going to be denied.
    """
    checkout = _degraded_checkout(cwd)
    if checkout is None:
        return None
    repo_root, git_dir = checkout
    if not (repo_root / ".paircoder").is_dir():
        return None
    branch = _degraded_protected_branch(git_dir)
    if branch is None:
        return None
    if _degraded_engage_exempts(repo_root, env):
        # Return deliberately not acted on: the engage exemption is a
        # ratified AC (a driver is never blocked by this guard), so an
        # unauditable exemption is still honored -- but never unsaid.
        if audit:
            _audit_or_warn(
                repo_root,
                action="degraded_engage_exempt",
                branch=branch,
                code=defect[0],
            )
        return None
    marker = repo_root / DEGRADED_OPT_OUT_MARKER
    if marker.is_symlink():
        sys.stderr.write(
            DEGRADED_SYMLINK_WARNING.format(marker=DEGRADED_OPT_OUT_MARKER)
        )  # fmt: skip
    elif marker.is_file() and (
        not audit
        or _audit_or_warn(
            repo_root, action="degraded_marker_disarm", branch=branch, code=defect[0]
        )  # fmt: skip
        is None
    ):
        return None
    return _degraded_posture_verdict(
        repo_root, branch=branch, defect=defect, kind=kind, audit=audit
    )


def integrity_defect(healthy_checkout: tuple | None) -> tuple | None:
    """A SECOND OPINION on the protected-branch question, or None.

    Payload integrity was presence-only: ten `is_file()` calls and an
    attribute sweep cannot see CONTENT, so a sibling edited to answer
    `protected_branches() -> set()` (or `find_checkout() -> None`) left
    every check green and the commit allowed, with `--self-check`
    reporting the layer armed. Hashing the payload at hook time only
    moves the tamper target, so instead this compares the two
    independent implementations the decomposition already gave us: the
    shared primitives the healthy path uses, and this module's own
    self-contained reads. They must agree that a commit is NOT on a
    protected branch before it is allowed; disagreement is a defect, not
    a tie to be broken.

    Deliberately one-directional: only "self-contained says protected,
    shared primitives say otherwise" is a defect. The reverse is already
    a denial, and the exemption paths (engage marker, opt-out marker, the
    one-shot init marker) legitimately allow protected-branch commits
    AFTER this question is settled, so they never reach here.
    """
    own = _degraded_checkout(Path.cwd())
    if own is None:
        return None
    repo_root, git_dir = own
    if not (repo_root / ".paircoder").is_dir():
        return None
    self_branch = _degraded_protected_branch(git_dir)
    if self_branch is None:
        return None
    if healthy_checkout is not None:
        healthy_branch = guard.current_branch(healthy_checkout[1])
        if healthy_branch is not None and healthy_branch in guard.protected_branches(
            healthy_checkout[1]
        ):
            return None
    return defect(
        "layer_disagreement",
        "payload integrity: this hook's own read says the checkout is on "
        f"protected branch {self_branch}, the shared guard primitives say it "
        "is not -- a sibling module is present but not answering truthfully",
    )


def _audit_evaluator_error(*, kind: str, exc: BaseException) -> None:
    """Best-effort row for a refusal the evaluator itself could not reason
    about -- never raises, whatever state the checkout is in."""
    try:
        own = _degraded_checkout(Path.cwd())
        if own is None:
            return
        _audit_or_warn(
            own[0],
            action=f"{kind}_refusal",
            branch="",
            code=f"evaluator_error:{type(exc).__name__}",
        )
    except Exception:
        pass


def judge_degraded(defect: tuple, *, kind: str = "degraded") -> int:
    """Banner + self-contained verdict, exit code per the declared posture.

    Never returns 0 on an error of its OWN: if even this path raises,
    there is nothing left that could have judged the commit, and the
    alternative is exactly the unaudited fail-open this path exists to
    retire.
    """
    sys.stderr.write(DEGRADED_BANNER.format(defect=defect[1]))
    try:
        reason = evaluate_degraded(Path.cwd(), dict(os.environ), defect, kind=kind)
    except Exception as exc:
        # The declared posture is NOT consulted here, and the docs say
        # so: the code that reads the declaration is part of what just
        # failed, so nothing is left that could have judged this commit.
        # The row is still attempted so the outage is not invisible.
        sys.stderr.write(
            f"git_commit_guard: degraded evaluation failed ({exc}) -- refusing.\n"
        )  # fmt: skip
        _audit_evaluator_error(kind=kind, exc=exc)
        return 1
    if reason is None:
        return 0
    sys.stderr.write(f"BLOCKED BY ENFORCEMENT GATE\n\n{reason}\n")
    return 1


def _config_hooks_path(config_path: Path) -> str | None:
    """``core.hooksPath`` from a git config file, or None.

    Section-aware mini-INI scan, same posture as the YAML scanner above:
    stdlib only (no `git` subprocess from inside a git hook), and
    anything it cannot read confidently reads as unset.
    """
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return None
    section = ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].split(";", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().split(None, 1)[0].lower()
            continue
        if section != "core" or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip().lower() == "hookspath":
            return value.strip().strip('"') or None
    return None


def _git_hooks_dir(repo_root: Path, git_dir: Path) -> Path:
    """Where git looks for this repo's hooks."""
    common = git_dir
    commondir = git_dir / "commondir"
    if commondir.is_file():
        candidate = Path(commondir.read_text(encoding="utf-8").strip())
        common = candidate if candidate.is_absolute() else git_dir / candidate
    configured = _config_hooks_path(common / "config")
    if configured is None:
        return git_dir / "hooks"
    path = Path(configured)
    return path if path.is_absolute() else repo_root / path


def wiring_defect() -> str | None:
    """Why git would not call this hook for a commit here, or None.

    Checked only for ``--self-check``: a hook that is RUNNING is wired by
    definition, so this never gates a real commit. A ``core.hooksPath``
    set in global or system config is not visible to a stdlib-only read
    of the repo's own config -- so the "no pre-commit" answer says where
    it looked rather than asserting a negative it cannot establish.
    """
    own = _degraded_checkout(Path.cwd())
    if own is None:
        return "not inside a git checkout, so nothing here invokes this hook"
    repo_root, git_dir = own
    hook = _git_hooks_dir(repo_root, git_dir) / "pre-commit"
    try:
        where = hook.relative_to(repo_root)
    except ValueError:
        where = hook
    if not hook.is_file():
        return f"no pre-commit hook at {where} (a global core.hooksPath is not visible here)"
    if not os.access(hook, os.X_OK):
        return f"{where} is not executable"
    try:
        text = hook.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"{where} is unreadable ({type(exc).__name__})"
    if "git_commit_guard" not in text:
        return f"{where} does not invoke git_commit_guard.py"
    return None


def _self_check_integrity() -> tuple | None:
    """The second opinion, run for `--self-check` too.

    A presence-complete payload whose CONTENT lies is exactly the state
    in which the old wording printed `enforcement ARMED`. The self-check
    answers a question the banner then quotes verbatim, so it may not
    stop at the cheapest evidence.
    """
    if guard is None:  # already reported as an import defect
        return None
    try:
        return integrity_defect(guard.find_checkout(Path.cwd()))
    except Exception as exc:
        return defect(
            f"integrity_check_failed:{type(exc).__name__}",
            f"payload integrity could not be checked ({type(exc).__name__})",
        )


def self_check_line(found: tuple | None) -> tuple[str, int]:
    """``--self-check``: 0 armed, 1 degraded, one-line reason on stdout.

    Called by ``base_branch_guard.py``'s degraded banner so that banner
    reports what it VERIFIED about this layer instead of what is normally
    true of it -- including this repo's DECLARED posture, since "degraded"
    means different things in a fail-closed and a fail-open repo.
    """
    if found is None:
        found = _self_check_integrity()
    if found is not None:
        posture, source = FAIL_CLOSED, None
        try:
            checkout = _degraded_checkout(Path.cwd())
            if checkout is not None:
                posture, source = resolve_posture(checkout[0])[:2]
        except Exception:
            pass  # unreadable checkout -> report the default, never a guess
        return (
            SELF_CHECK_FAILED.format(
                defect=found[1],
                outcome=SELF_CHECK_OUTCOME[posture].format(source=source),
            ),
            1,
        )
    try:
        wiring = wiring_defect()
    except Exception as exc:
        wiring = f"wiring could not be checked ({type(exc).__name__})"
    if wiring is not None:
        return SELF_CHECK_NOT_WIRED.format(detail=wiring), 1
    return SELF_CHECK_OK, 0


def self_check(found: tuple | None) -> int:
    """``--self-check``: print the one-line answer, exit 0 only if armed."""
    text, code = self_check_line(found)
    sys.stdout.write(text)
    return code


def verdict(found: tuple | None) -> int:
    """``--verdict``: what THIS layer would do with a commit here, without
    doing it -- and without writing a single ledger row.

    Exists for the advisory layer (``base_branch_guard.py``), which while
    degraded cannot import the siblings that answer "is this branch
    protected, is this session engage-exempt, is the opt-out marker
    present". A third copy of those rules is the drift this whole PR is
    retiring, so it asks the layer that already carries a self-contained
    copy. Two prefixed lines on stdout: ``state:`` (the self-check
    answer, so one probe serves both the banner and the decision) and
    ``verdict:``; exit 0 allow, 1 deny, 2 unknown.
    """
    state, _ = self_check_line(found)
    sys.stdout.write("state: " + state)
    try:
        reason = evaluate_degraded(
            Path.cwd(),
            dict(os.environ),
            defect("advisory_probe", "advisory probe"),
            kind="probe",
            audit=False,
        )
    except Exception as exc:
        sys.stdout.write(f"verdict: unknown -- probe failed ({type(exc).__name__})\n")
        return 2
    if reason is None:
        sys.stdout.write("verdict: allow -- no protected-branch rule applies here\n")
        return 0
    sys.stdout.write(f"verdict: deny -- {reason}\n")
    return 1


def _stage_bypass_log(repo_root: Path) -> None:
    """Best-effort `git add` of the bypass log this hook just wrote to.

    `git add -A` (the smoke/adopt-scaffolding sequence) snapshots the index
    BEFORE this hook runs, so the audit record it appends is invisible to
    that commit unless the hook stages it itself -- the same technique any
    pre-commit auto-fixer uses to fold its own edits into the commit it is
    gating. Never raises: a failed `git add` here still leaves the write
    already durable on disk (the bypass IS audited); it only means the log
    surfaces as a new untracked file instead of riding this commit.
    """
    try:
        subprocess.run(
            ["git", "add", "--", str(guard.BYPASS_LOG_REL)],
            cwd=str(repo_root),
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass


# Kept split: collapses to a 91-char line at line-length=100.
def _consume_init_once_marker(
    repo_root: Path, *, branch: str, marker: Path
) -> str | None:  # fmt: skip
    """Return a deny reason, or None to allow -- consuming *marker* on the
    allow path.

    The bypass-log append happens BEFORE the marker is deleted: a write
    failure denies the commit (an unauditable bypass is not one this hook
    may grant) but leaves the marker in place for a retry, rather than
    spending the one shot on a bypass nothing recorded. A symlink at the
    marker path is tampering, never honored, and is left untouched.
    """
    if guard._marker_kind(marker) == "symlink":
        sys.stderr.write(INIT_ONCE_SYMLINK_WARNING.format(marker=INIT_ONCE_MARKER))
        return guard.DENY_TEMPLATE.format(branch=branch)
    error = guard._log_marker_bypass(
        repo_root,
        action="init_first_commit",
        branch=branch,
        tool="git-commit",
        target=marker,
    )
    if error is not None:
        return guard.AUDIT_FAILURE_TEMPLATE.format(
            branch=branch,
            action="init_first_commit",
            marker=INIT_ONCE_MARKER,
            log_path=guard.BYPASS_LOG_REL,
            error=error,
        )
    _stage_bypass_log(repo_root)
    try:
        marker.unlink()
    except OSError:
        pass
    # Kept split: collapses to a 96-char line at line-length=100.
    sys.stderr.write(
        INIT_ONCE_CONSUMED_ADVISORY.format(marker=INIT_ONCE_MARKER, branch=branch)
    )  # fmt: skip
    return None


def _log_engage_exemption(repo_root: Path, *, branch: str) -> None:
    """Best-effort audit of the ``BPSAI_ENGAGE_MODE`` exemption, appended
    only when it actually flips THIS commit from denied to allowed (i.e.
    ``guard._engage_mode_exempts`` already confirmed the flag applies AND
    that the run's declared worktree covers *repo_root*).

    Deliberately NOT fail-closed on a write failure, unlike the marker
    disarm path: the exemption is a REQUIRED, ratified-AC guarantee (an
    engage driver's commit must never be blocked by this guard), so an
    unauditable exemption is still honored here rather than turned into a
    block -- the audit trail is best-effort visibility, not a condition
    of the exemption itself. It is still SAID: this arm used to throw the
    writer's error away in silence while its degraded twin announced the
    same failure, so an exemption granted against a ledger nobody could
    write left no trace in either place. Honored, never unsaid.

    Routed through ``guard._write_bypass_entry`` (the sibling module's
    shared writer) rather than opening ``guard.BYPASS_LOG_REL`` directly,
    so the unified-sink + one-time legacy-ledger migration note live in
    exactly one place, reused by both hook scripts.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "command": "git_commit_guard",
        "target": str(repo_root),
        "bypass_type": "engage_mode_commit_exempt",
        "gate": "git_commit_guard",
        "action": "engage_mode_exempt",
        "branch": branch,
        "tool": "git-commit",
    }
    error = guard._write_bypass_entry(repo_root, entry)
    if error is not None:
        sys.stderr.write(
            AUDIT_WRITE_FAILED.format(
                action="engage_mode_commit_exempt",
                log=guard.BYPASS_LOG_REL,
                error=error,
            )
        )


def evaluate_commit(repo_root: Path, git_dir: Path, env: dict) -> str | None:
    """Return a deny reason, or None to allow this commit.

    Allowed without further checks when: the checkout is not
    paircoder-managed, or HEAD is not on a protected branch -- checked
    FIRST, before the engage exemption or either marker's on-disk
    presence is consulted, so none of it is audited for a commit that
    would have been allowed anyway (mirrors ``base_branch_guard.py``'s own
    "unprotected branches short-circuit before any marker logic"
    doctrine). Once a branch is confirmed protected: the engage exemption
    (audited when it fires; scoped to the run's declared worktree via
    ``guard._engage_mode_exempts``, which refuses outright when no worktree
    was declared -- see the module docstring's "WORKTREE-SCOPED" section),
    then the one-shot init marker
    (scoped to exactly one commit, checked before the persistent opt-out so
    it is never shadowed by it), then the persistent opt-out marker via the
    same audited disarm path ``base_branch_guard.py`` uses for every
    non-marker-write edit.
    """
    if not (repo_root / ".paircoder").is_dir():
        return None
    branch = guard.current_branch(git_dir)
    if branch is None or branch not in guard.protected_branches(git_dir):
        return None
    if guard._engage_mode_exempts([repo_root], env):
        _log_engage_exemption(repo_root, branch=branch)
        return None
    init_marker = repo_root / INIT_ONCE_MARKER
    if guard._marker_kind(init_marker) != "absent":
        return _consume_init_once_marker(repo_root, branch=branch, marker=init_marker)
    marker = repo_root / guard.OPT_OUT_MARKER
    return guard._evaluate_marker_disarm(
        repo_root,
        branch=branch,
        tool="git-commit",
        target=marker,
        marker=marker,
        shape="ambiguous",
    )


def main(argv: list[str] | None = None) -> int:
    """Fail CLOSED on a policy match AND on this guard's own defects.

    A pre-commit hook blocks on any nonzero exit -- this returns 1 on a
    deny (git's own convention), not the PreToolUse guard's exit-2
    convention, since the two run under different protocols.

    "Our own errors" no longer means "allow". A broken
    payload, a hollow facade, or an unexpected exception anywhere in the
    healthy path all route into ``refuse_degraded``, which re-judges the
    commit with self-contained logic: unmanaged checkouts and feature
    branches still pass (a guard bug must not brick ordinary work
    fleet-wide), a protected-branch commit is REFUSED and audited. The
    old blanket ``return 0`` traded exactly the case this gate exists for
    against a blast radius it never actually had.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    found = payload_defect()
    if "--self-check" in args:
        return self_check(found)
    if "--verdict" in args:
        return verdict(found)
    announce_posture()
    if found is not None:
        return judge_degraded(found)
    try:
        checkout = guard.find_checkout(Path.cwd())
        reason = (
            evaluate_commit(checkout[0], checkout[1], dict(os.environ))
            if checkout is not None
            else None
        )
        if reason is None:
            tampered = integrity_defect(checkout)
            if tampered is not None:
                return judge_degraded(tampered, kind="integrity")
    except Exception as exc:
        # NOT fail-open: an unexpected error is judged by the same
        # self-contained path a broken payload gets, under its own
        # audited action so the two stay distinguishable in the ledger.
        return judge_degraded(
            defect(f"guard_error:{type(exc).__name__}", f"guard error: {exc}"),
            kind="guard_error",
        )
    if reason is None:
        return 0
    sys.stderr.write(f"BLOCKED BY ENFORCEMENT GATE\n\n{reason}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
