"""Predicate binary resolution and invocation for `pr_merge_guard.py`.

Split out of the main hook module (architecture: file/function-count caps).
Answers "which `bpsai-pair` do we trust, and what did it say" -- the main
module owns classifying a Bash command as `gh pr merge`-shaped and acting
on the verdict this module returns. Stdlib-only, no `bpsai_pair` import,
matching the main hook's own runtime contract.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

try:
    from pr_merge_guard_trust import _check_trust, _is_git_tracked

    _TRUST_IMPORT_ERROR: str | None = None
except Exception as _trust_import_error:  # noqa: E402
    # Decoupled from `_check_trust`/`_is_git_tracked`'s own import (review
    # round 6, P1): a broken/missing `pr_merge_guard_trust.py` used to
    # cascade into THIS module failing to import too, which then tripped
    # `pr_merge_guard.py`'s top-level `_SIBLING_IMPORT_ERROR` and denied
    # EVERY command, not just merges -- `_trust` answers only "is this
    # resolved binary safe to run", a question the classifier
    # (`_parse`/`_gate`) never needs. With the fallbacks below, this
    # module still imports fine; only the predicate-binary trust
    # decision degrades, so a merge-shaped command still gets denied
    # (untrusted predicate == fail closed) while every non-merge command
    # keeps full classification.
    _TRUST_IMPORT_ERROR = str(_trust_import_error)

    def _check_trust(resolved: str) -> str:  # noqa: ARG001
        return (
            "the trust-check module could not be imported "
            f"({_TRUST_IMPORT_ERROR}); treating the predicate binary as "
            "untrusted until the payload is repaired"
        )

    def _is_git_tracked(cwd: str, path: str) -> None:  # noqa: ARG001
        return None


PREDICATE_ARGV = ["bpsai-pair", "pr", "merge-guard-check", "--json"]
PREDICATE_TIMEOUT = 60

#: The lowest `bpsai-pair` release carrying the `pr merge-guard-check`
#: subcommand this hook depends on. Read by `claude_payload_manifest.
#: compute_min_cli_version` (a plain text scan, not an import -- this file
#: runs stdlib-only on operator machines with no `bpsai_pair` package
#: available) and folded into the release-pinned payload manifest's
#: `min_cli_version` field, so `bpsai-pair upgrade` can refuse to WRITE
#: this hook to a repo when the CLI doing the writing is itself older than
#: what the hook it is about to ship requires -- the exact skew this
#: constant exists to prevent from being silently reproduced on write.
MIN_CLI_VERSION = "2.49.2"

#: An operator's explicit override -- the only way to NAME a predicate
#: binary directly, taking priority over both the repo-tree search and PATH.
BIN_OVERRIDE_ENV = "BPSAI_PAIR_BIN"

#: Security-review follow-up (P0): the repo-tree search below trusts any
#: UNTRACKED file at a conventional venv path inside the checkout -- which
#: closes the "PR author commits a malicious script" vector (via
#: `_is_git_tracked`) but opens a WORSE one this hook's own threat model
#: exists to police: the SAME session about to run the guarded `gh pr
#: merge` call already has unrestricted shell access to this checkout
#: BEFORE that call (e.g. a prompt-injected agent), and could simply
#: create `.venv/bin/bpsai-pair` itself, entirely untracked, moments
#: earlier -- a file the bare-PATH-only resolution this hook shipped with
#: before this feature would never have looked at, let alone trusted.
#: Gating the repo-tree search ENTIRELY behind this operator opt-in (an
#: environment variable, never PR-branch or session-writable content the
#: same way) keeps today's shipped default -- `BPSAI_PAIR_BIN` then bare
#: PATH, exactly as before this feature existed -- unchanged for every
#: repo that hasn't deliberately accepted the repo-tree tradeoff. Only a
#: fleet that already knows it has the PATH-shadowing problem (an
#: unrelated venv earlier on the invoking process's ambient PATH) turns
#: this on, with full knowledge that doing so ALSO means trusting an
#: untracked file freshly written inside the checkout being merged.
PREFER_REPO_TREE_ENV = "BPSAI_MERGE_GUARD_PREFER_REPO_TREE"

#: Conventional venv directory names checked inside the event's OWN repo
#: tree (its `cwd`) before ever falling back to a bare PATH search -- see
#: `_resolve_predicate_binary`'s docstring for why PATH alone is not
#: trustworthy here.
_REPO_VENV_DIRS = (".venv", "venv")
_REPO_VENV_SCRIPT_SUBDIRS = ("bin", "Scripts")
_REPO_VENV_SCRIPT_NAMES = ("bpsai-pair", "bpsai-pair.exe")

#: Click/Typer's own exit code for a usage error (e.g. "No such command") --
#: distinct from a genuine crash/missing-binary code, and from the
#: predicate's own 0/1 verdict codes. A `bpsai-pair` old enough to lack the
#: `pr merge-guard-check` subcommand this hook depends on exits exactly this
#: way against the hook's fixed, flag-free argv.
NOT_EVALUABLE_RETURNCODE = 2

#: Click's own phrasing for an unrecognized (sub)command -- e.g. "Error: No
#: such command 'merge-guard-check'." (subcommand missing) or "Error: No
#: such command 'pr'." (the whole `pr` group missing, an even older CLI).
#: Required ALONGSIDE `NOT_EVALUABLE_RETURNCODE`: exit 2 alone is Click's
#: generic usage-error code and does not by itself distinguish "this CLI
#: predates the subcommand" from "this CLI has the subcommand but rejected
#: the argv for an unrelated reason" -- only the former is safe to treat as
#: merely not-evaluable rather than an unconditional deny. The command
#: name is matched explicitly (not a bare "no such command" substring,
#: security-review follow-up round 2): a generic match could misclassify
#: an unrelated Click usage error mentioning that phrase incidentally.
#:
#: ACKNOWLEDGED FORWARD-COMPAT GAP (security-review follow-up round 9):
#: matching the literal name `pr` couples this signature to the CURRENT
#: command layout -- a FUTURE `bpsai-pair` that renames or reorganizes the
#: `pr` group would also produce "No such command 'pr'" against this
#: hook's fixed argv, misclassifying a genuinely NEWER CLI the same way as
#: a genuinely OLDER one. Gated behind the same operator opt-in as the
#: rest of this carve-out (`NOT_EVALUABLE_OPT_IN_ENV` in the main module),
#: so the blast radius is a fleet that has already accepted the
#: not-evaluable tradeoff, not every repo by default.
_NOT_EVALUABLE_RE = re.compile(
    r"no such command\s+'(?:pr|merge-guard-check)'",
    re.IGNORECASE,
)

FAIL_CLOSED_TEMPLATE = (
    "This `gh pr merge` could not be checked: {why}.\n"
    "The merge guard fails CLOSED -- a predicate that could not run is not\n"
    "a clean bill of health.\n"
    "Remedy: make the CLI reachable (`pip install --upgrade bpsai-pair`,\n"
    "then `bpsai-pair upgrade`) and retry, or run\n"
    "`{argv}` yourself to see the failure."
)


def _repo_tree_candidates(cwd: str) -> list:
    """Candidate predicate paths inside *cwd*'s OWN tree, checked before any
    bare PATH search -- the conventional `.venv`/`venv` locations a repo's
    own `pip install -e .` (or plain `pip install bpsai-pair`) populates.
    Order matters only in that every candidate here is tried before PATH;
    among themselves, first existing+executable wins."""
    candidates = []
    for venv_dir in _REPO_VENV_DIRS:
        for subdir in _REPO_VENV_SCRIPT_SUBDIRS:
            for name in _REPO_VENV_SCRIPT_NAMES:
                candidates.append(os.path.join(cwd, venv_dir, subdir, name))
    return candidates


def _resolve_predicate_binary(cwd: str):
    """`(absolute_path, None)` or `(None, reason)`.

    A bare PATH search (what `subprocess.run(["bpsai-pair", ...])` does on
    its own) trusts whatever the FIRST matching entry contains -- not just a
    hostile one (the original security-audit concern this function's trust
    check was built for), but, in ordinary practice, whatever venv an
    UNRELATED tool happened to put first on the invoking process's PATH: a
    landing driver's orchestrator venv, another repo's activated
    environment, and so on (observed in practice -- a merge on one repo was
    refused because the PATH-resolved `bpsai-pair` was a different, older
    checkout that predated the very subcommand this hook depends on).

    Resolution order, each candidate still subject to `_check_trust` once
    found:
      1. `BPSAI_PAIR_BIN` -- an operator's explicit, deliberate override
         (an environment variable, never PR-branch content -- unlike the
         repo tree below, nothing about it is attacker-controlled by a
         commit).
      2. ONLY IF `BPSAI_MERGE_GUARD_PREFER_REPO_TREE=1` (an operator
         opt-in -- see `PREFER_REPO_TREE_ENV`): the event's OWN repo tree
         (`_repo_tree_candidates`), insulated from whatever else happens
         to be on the ambient PATH, and ALSO subject to `_is_git_tracked`:
         a candidate git considers part of the checkout's tracked content
         is refused outright, since that is exactly the PR's own committed
         diff, not a local install. This still does NOT defend against an
         untracked file the CURRENT session planted moments earlier (see
         `PREFER_REPO_TREE_ENV`'s own docstring) -- it is opt-in precisely
         because that residual trust extension is not free.
      3. A bare PATH search (`shutil.which`) -- the DEFAULT remedy (both
         absent the opt-in above, and as the last resort once it's been
         tried), so `bpsai-pair upgrade` stays a valid remedy everywhere it
         always was.
    """
    override = os.environ.get(BIN_OVERRIDE_ENV)
    if override:
        resolved = os.path.realpath(override)
        if not (os.path.isfile(resolved) and os.access(resolved, os.X_OK)):
            return None, f"{BIN_OVERRIDE_ENV}={override!r} is not an executable file"
        failure = _check_trust(resolved)
        return (None, failure) if failure else (resolved, None)

    if os.environ.get(PREFER_REPO_TREE_ENV) == "1":
        for candidate in _repo_tree_candidates(cwd):
            if not (os.path.isfile(candidate) and os.access(candidate, os.X_OK)):
                continue
            # Resolve BEFORE the tracked check (cross-module review
            # follow-up): a venv script is routinely a symlink, and
            # checking trackedness against the pre-resolve candidate path
            # while checking trust against the post-resolve target meant
            # the two defenses could evaluate two DIFFERENT filesystem
            # objects. Checking the resolved target for BOTH is strictly
            # more conservative: a symlink resolving outside the repo
            # entirely makes `_is_git_tracked` return `None` (git can't
            # answer for a path outside its own tree), which the "continue
            # on not-False" rule below already treats as untrusted.
            resolved = os.path.realpath(candidate)
            if _is_git_tracked(cwd, resolved) is not False:
                # Tracked (part of the PR's own committed content) OR
                # undeterminable -- never trust either; move on to the
                # next candidate rather than treat "can't verify" as "safe".
                continue
            if _check_trust(resolved) is None:
                return resolved, None
            # An untrusted candidate does NOT deny outright (cross-module
            # review follow-up): a single misconfigured directory
            # permission on ONE conventional venv path must not cost a
            # repo the bare-PATH remedy `bpsai-pair upgrade` always
            # promises. Try the remaining repo-tree candidates, then fall
            # through to PATH below, exactly as if this one had never
            # existed.

    resolved = shutil.which(PREDICATE_ARGV[0])
    if resolved is None:
        if os.environ.get(PREFER_REPO_TREE_ENV) == "1":
            where = f"this repo's own tree ({', '.join(_REPO_VENV_DIRS)}) or on PATH"
        else:
            where = "on PATH"
        return None, f"`{PREDICATE_ARGV[0]}` was not found {where}"
    resolved = os.path.realpath(resolved)
    failure = _check_trust(resolved)
    return (None, failure) if failure else (resolved, None)


def ask_predicate(cwd: str) -> tuple:
    """``(payload, failure, not_evaluable)`` from one predicate invocation.

    *payload* is non-``None`` only on a genuine verdict; *failure* is a
    short human reason otherwise. ``not_evaluable`` is ``True`` only for the
    one failure shape that gets a softer treatment in ``main`` -- the
    resolved binary ran, exited Click/Typer's usage-error code
    (`NOT_EVALUABLE_RETURNCODE`), AND its output carries Click's own "No
    such command" signature -- which happens when it PREDATES the `pr
    merge-guard-check` subcommand entirely. Security-review follow-up: exit
    2 alone is Click's generic usage-error code for ANY malformed
    invocation, including a NEWER predicate that recognizes the
    subcommand but rejects this hook's fixed argv for an unrelated reason
    (a renamed flag, an added required option) -- that binary COULD have
    rendered a real verdict, so it must stay an unconditional deny, not
    fall into the softer not-evaluable path. Every other failure shape
    (crash, timeout, missing binary) also remains an unconditional deny
    exactly as before.
    """
    binary, failure = _resolve_predicate_binary(cwd)
    if failure is not None:
        return None, failure, False
    argv = [binary, *PREDICATE_ARGV[1:]]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=PREDICATE_TIMEOUT,
            cwd=cwd,
        )
    except Exception as exc:
        # The exception's own message can embed the resolved binary's
        # path/argv or OS-level detail about the invoking environment --
        # never trusted content, but not this hook's to relay verbatim to
        # a transcript either (review round 6, P2). The class name alone
        # (`TimeoutExpired`, `PermissionError`, ...) is enough to point an
        # operator at the right remedy.
        return (
            None,
            f"the predicate could not be run ({type(exc).__name__})",
            False,
        )
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == NOT_EVALUABLE_RETURNCODE and _NOT_EVALUABLE_RE.search(
        output,
    ):
        return (
            None,
            (
                "the resolved `bpsai-pair` does not recognize `pr "
                "merge-guard-check` (exit 2, a usage error naming a missing "
                "command) -- likely a CLI that predates this subcommand"
            ),
            True,
        )
    # 0 = allow, 1 = block. Anything else (other than the usage-error code
    # handled above) is a crash or a missing binary (127) -- never a verdict.
    if proc.returncode not in (0, 1):
        return (
            None,
            f"the predicate exited {proc.returncode} instead of a verdict",
            False,
        )
    try:
        payload = json.loads(proc.stdout or "")
    except Exception:
        return None, "the predicate did not return parseable JSON", False
    if not isinstance(payload, dict) or "allow" not in payload:
        return None, "the predicate returned no `allow` verdict", False
    return payload, None, False
