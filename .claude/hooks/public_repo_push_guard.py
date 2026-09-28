#!/usr/bin/env python3
"""Git-native pre-push hook: public-repo tracked-doctrine guard.

Refuses to push when THE REMOTE BEING PUSHED TO is PUBLIC and any of a fixed set
of internal-doctrine path classes (operating context, task documents,
their archives, agent-memory notes) are currently `git`-tracked. The
default posture -- track these on purpose -- is correct for a private
repo and a leak risk for a public one; this hook closes that gap at the
one place every push has to pass through, regardless of how it was
invoked (a raw shell, a script, a GUI client), the same way the
protected-base-branch guard's git-layer half closes the analogous gap for
commits.

Semantics mirror `bpsai_pair.commands.public_repo_guard.check_public_tracked_doctrine`
(the same logic backing `bpsai-pair audit public-repo-tracked`) exactly:
nothing tracked -> clean, regardless of visibility; PUBLIC + tracked ->
violation; visibility indeterminate + tracked -> a distinct unknown case,
never silently identical to clean. `DOCTRINE_TRACKED_PREFIXES` is RESTATED
here rather than imported: this script runs on operator machines with a
bare `python3` -- no `bpsai_pair` import, no venv -- the same stdlib-only
constraint every other script under this directory carries. A parity test
asserts the two prefix lists stay identical so they cannot drift apart.

MODULE LAYOUT: the visibility cache/resolver (the per-remote
`gh` check and its shared TTL cache) lives in the sibling module
`public_repo_push_guard_visibility.py`, imported with a fail-open
degraded fallback below (see the import guard further down). This
file is the executable hook entry point (unchanged wiring:
`.git/hooks/pre-push` still runs `python3 .claude/hooks/
public_repo_push_guard.py`) plus the tracked-doctrine enumeration, the
push-target resolution, the opt-out marker, and the deny/warn decision.
Mirroring `base_branch_guard.py`, a missing/broken sibling does NOT raise
an `ImportError` (which, inside a git-native pre-push hook, would block
EVERY push): it degrades LOUDLY -- an stderr banner naming the missing
payload files and one audited row on the bypass ledger -- and allows the
push (fail-open), the same trade-off the UNKNOWN-visibility case already
makes for an offline push.

UNREACHABLE-VISIBILITY POSTURE: fail CLOSED, with a stated reason. Once a
push target resolves to a real `slug` (a github.com remote), its
visibility is the one fact this guard's whole PUBLIC+tracked check turns
on, and a cached verdict may never stand in for it (see
`public_repo_push_guard_visibility.py`'s "CACHE IS DENY-ONLY"). If the
live `gh` call cannot be completed -- network down, `gh` missing or
unauthenticated, rate-limited -- the visibility is unproven, and an
unproven remote is refused exactly like a confirmed-PUBLIC one: egress of
tracked doctrine is the risk this guard exists to stop, and that risk does
not shrink because the check happened to fail. The deny names the
concrete reason `gh` could not answer, and the same audited opt-out marker
below still disarms it.

NOT-A-GITHUB-REMOTE POSTURE: fail OPEN, loudly -- a DIFFERENT case from
the one above. A push target that is not a github.com repository (a local
path, another forge, GitHub Enterprise) can never be answered by `gh` at
all, live or cached; there is no check to retry, so this hook allows the
push and prints a warning naming exactly which paths could not be ruled
out, pointing at the fail-closed CI command (`audit public-repo-tracked`)
for the authoritative check on that remote.

PUSH TARGET, NOT `origin`: git invokes a pre-push hook with the remote's
name (`$1`) and URL (`$2`), and this script resolves visibility for THAT
remote (see `push_target_slug`). Asking `gh repo view` from the working
directory answers for the DEFAULT remote instead -- so a private `origin`
alongside a public mirror would wave every doctrine path straight through
to the public one, which is precisely the leak this guard exists to stop.
The visibility cache is keyed per remote for the same reason: one
repo-wide cached verdict would re-open the same hole one push later. A
target whose URL is not a github.com repository (a local path, another
forge, GitHub Enterprise) resolves to no slug at all and is reported as
the UNKNOWN case below rather than silently answered from `origin`.

Runtime contract: stdlib only, plain `python3`, no `bpsai_pair` import --
this runs on operator machines with no PairCoder venv, same as every
other script in this directory.

LIVE ON EVERY ALLOW: an allow is decided from a LIVE `gh` check, always --
never from a cached verdict (see `public_repo_push_guard_visibility.py`'s
"CACHE IS DENY-ONLY"). The cache is consulted only to short-circuit a
DENY (a verdict already known PUBLIC within the TTL), which cannot loosen
what a live check would have decided anyway. This closes a prior TOCTOU
gap in this exact guard: a cached non-public verdict must never itself be
the reason a push carrying tracked doctrine paths is allowed through, since
a private -> public flip inside a cache window would otherwise look
exactly like a clean push.

ESCAPE HATCH (audited bypass): create
`.paircoder/hooks/public_repo_push_guard.off` in the repo root (an empty
file is enough) to push through a confirmed PUBLIC+tracked refusal
anyway. The marker is untracked, per-repo state, so its presence is
visible in `git status` -- that visibility IS the audit, same convention
the base-branch guard's own opt-out marker uses. Every push the marker
disarms appends one record to
`<repo_root>/.paircoder/history/bypass_log.jsonl` (the same unified
ledger every other guard in this project writes); the append is
best-effort and, on failure, the push is DENIED rather than silently
allowed unaudited -- an audited bypass that could not be audited is not
one this hook may grant. A symlink at the marker path is tampering, not
an opt-out, and is never honored.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

# A real hook invocation is a fresh `python3 <this file>.py` subprocess in
# a consumer's checkout -- importing the sibling module below would
# otherwise leave `__pycache__/*.pyc` build artifacts sitting untracked in
# the payload tree, dirtying `git status` in every managed repo forever.
# Set BEFORE the sibling import so nothing downstream writes bytecode;
# mirrors `base_branch_guard.py`'s own shim for the exact same reason.
sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Mirrors the CLI's public_repo_guard module DOCTRINE_TRACKED_PREFIXES,
# restated here (stdlib-only constraint -- see module docstring). A
# parity test asserts these two lists stay byte-identical.
DOCTRINE_TRACKED_PREFIXES = frozenset(
    {
        ".paircoder/context/",
        ".paircoder/tasks/",
        ".paircoder/history/archived-tasks/",
        ".claude/agent-memory/",
    }
)

OPT_OUT_MARKER = Path(".paircoder/hooks/public_repo_push_guard.off")
BYPASS_LOG_REL = Path(".paircoder/history/bypass_log.jsonl")

# `OWNER/REPO` out of any GitHub remote URL shape git accepts:
# `https://github.com/o/r(.git)`, `ssh://git@github.com/o/r.git`,
# `git@github.com:o/r.git`, `github.com/o/r`. Deliberately github.com-only
# -- a non-GitHub (or GitHub Enterprise) remote's visibility is not
# something `gh repo view` can answer, and pretending otherwise is how the
# guard ends up reporting on the WRONG repository.
_GITHUB_URL_RE = re.compile(
    r"^(?:(?:https?|ssh|git)://)?(?:[^@/]+@)?github\.com[:/]+"
    r"(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$"
)

DENY_TEMPLATE = (
    "Refusing push: this remote is PUBLIC and the following doctrine-"
    "tracked path(s) are currently tracked:\n{paths}\n\nUntrack these "
    "(`git rm --cached`) or move the repo private before pushing. To "
    "bypass (audited), create {marker}."
)

UNREACHABLE_DENY_TEMPLATE = (
    "Refusing push: this remote's visibility could not be verified live "
    "({reason}) and the following doctrine-tracked path(s) are currently "
    "tracked:\n{paths}\n\nAn unproven remote is refused, not assumed "
    "private -- retry once the check above can succeed, or bypass "
    "(audited) by creating {marker}."
)

UNKNOWN_WARNING_TEMPLATE = (
    "public_repo_push_guard: remote visibility could not be determined "
    "(gh unavailable/unauthenticated, not a GitHub remote, or offline) "
    "and the following doctrine-tracked path(s) are present:\n{paths}\n\n"
    "This could NOT be ruled out as a violation -- verify manually "
    "(`gh repo view --json visibility`). Push allowed (fail-open on "
    "unknown visibility avoids bricking offline pushes); see `bpsai-pair "
    "audit public-repo-tracked` for the fail-closed, CI-runnable check.\n"
)

ENUMERATION_FAILED_WARNING_TEMPLATE = (
    "public_repo_push_guard: could not enumerate this checkout's tracked "
    "files (`git ls-files` failed or errored) -- whether any doctrine "
    "path is tracked is UNKNOWN, not clean. Push allowed (fail-open on a "
    "guard-internal error avoids bricking the push); verify manually "
    "(`git ls-files`) and see `bpsai-pair audit public-repo-tracked` for "
    "the fail-closed, CI-runnable check.\n"
)

ADVISORY_TEMPLATE = (
    "public_repo_push_guard: disabled by {marker} -- this push to a "
    "PUBLIC remote carrying tracked doctrine path(s) was NOT refused.\n"
)

SYMLINK_WARNING_TEMPLATE = (
    "public_repo_push_guard: {marker} is a symlink -- treating as "
    "tampering, not an opt-out. Refusing the push.\n"
)

AUDIT_FAILURE_TEMPLATE = (
    "Refusing push: the bypass at {marker} could not be recorded to "
    "{log_path} ({error}), so it was not honored -- an unauditable "
    "bypass is denied rather than granted silently. Fix the log path's "
    "permissions and retry."
)


def find_tracked_doctrine_paths(repo_root: Path) -> list[str] | None:
    """Currently `git`-tracked paths under a `DOCTRINE_TRACKED_PREFIXES`
    class. Empty (never raises) when *repo_root* is not a git checkout.

    Uses `git ls-files -z` (NUL-terminated), not the plain newline-
    terminated form: git's `core.quotePath` (default on) makes a bare
    `git ls-files` emit the whole line as a C-style quoted escape string
    whenever a path contains a non-ASCII or otherwise unusual byte, which
    a plain `startswith(prefix)` check would never match. `-z` disables
    the quoting entirely (raw bytes, NUL-separated).

    Returns ``None`` -- never ``[]`` -- when the enumeration itself could
    not be completed (missing/failing `git`, timeout, non-zero exit).
    Collapsing that into ``[]`` reads identically to "examined, genuinely
    nothing tracked" to every caller downstream -- the silent-pass shape
    the silent-pass shape: an UNEXAMINED repo passed as clean.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [
        rel
        for rel in (part.strip() for part in result.stdout.split("\0"))
        if rel and any(rel.startswith(prefix) for prefix in DOCTRINE_TRACKED_PREFIXES)
    ]


def _remote_slug(url: str) -> str | None:
    """`OWNER/REPO` for a github.com *url*, else None (see
    `_GITHUB_URL_RE`)."""
    match = _GITHUB_URL_RE.match((url or "").strip())
    if match is None:
        return None
    return f"{match.group('owner')}/{match.group('repo')}"


def _configured_remote_url(repo_root: Path, name: str) -> str:
    """The URL configured for remote *name*, empty when there is none.

    Reads `remote.<name>.url` rather than `git remote get-url`: the latter
    applies `url.<base>.insteadOf` rewriting, which can turn the
    GitHub URL an operator configured into a protocol-swapped or mirrored
    address the slug parser cannot read. Only consulted when git's own
    `$2` (the post-rewrite URL) is not itself github.com-shaped.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "config", "--get", f"remote.{name}.url"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def push_target_slug(repo_root: Path, argv: list[str]) -> str | None:
    """`OWNER/REPO` for the remote this push is going to, or None.

    *argv* is git's own pre-push argument pair: `[remote_name, remote_url]`
    (a bare `git push <url>` passes the URL in both). None means "no
    per-remote target could be resolved" -- either the hook was invoked
    with no arguments at all (a legacy wrapper, or a direct run), or the
    target is not a github.com repository. `repo_visibility` treats the
    two differently; see its docstring.
    """
    name = argv[0].strip() if len(argv) > 0 and argv[0] else ""
    url = argv[1].strip() if len(argv) > 1 and argv[1] else ""
    slug = _remote_slug(url) or _remote_slug(name)
    if slug is not None:
        return slug
    if name:
        return _remote_slug(_configured_remote_url(repo_root, name))
    return None


def _marker_kind(marker: Path) -> str:
    """One `lstat` read of *marker*: `"symlink"`, `"file"` (a regular
    file -- the only shape a legitimate marker takes), or `"absent"`.
    Never follows a symlink -- a symlinked marker is tampering, not an
    opt-out."""
    import stat

    try:
        mode = os.lstat(marker).st_mode
    except OSError:
        return "absent"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISREG(mode):
        return "file"
    return "absent"


def _log_bypass(
    repo_root: Path,
    *,
    target: object,
    action: str = "marker_disarm",
    verdict: str | None = None,
    source: str | None = None,
    age_seconds: float = 0.0,
) -> str | None:
    """Best-effort append one *action* record; return an error string on
    failure. The caller decides what a failure means -- `marker_disarm`
    fails closed.

    *verdict*/*source*/*age_seconds* record what visibility answer was
    being bypassed, and whether it came from a LIVE `gh` call or a cached
    (necessarily PUBLIC -- see "CACHE IS DENY-ONLY") one, so the row is
    auditable on its own after the fact rather than needing the push's
    surrounding context reconstructed.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "command": "public_repo_push_guard",
        "target": str(target),
        "bypass_type": f"public_repo_push_guard_{action}",
        "gate": "public_repo_push_guard",
        "action": action,
        "branch": "",
        "tool": "git-push",
        "verdict": verdict,
        "source": source,
        "age_seconds": age_seconds,
    }
    log_path = repo_root / BYPASS_LOG_REL
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError as exc:
        return str(exc)
    return None


class DoctrineCheckResult(NamedTuple):
    """`check_public_tracked_doctrine`'s full answer -- mirrors
    `bpsai_pair.commands.public_repo_guard.check_public_tracked_doctrine`'s
    result shape (minus the dataclass, this script is stdlib-only and
    self-contained), plus the visibility provenance this hook audits on.

    `violations`: tracked paths to DENY for -- populated for a confirmed
    PUBLIC match (live or a cached PUBLIC short-circuit) AND for a LIVE
    check that could not resolve a visibility at all (see
    `deny_kind`/`reason`): an unproven remote is refused, never allowed.
    `unknown_tracked`: tracked paths in the fail-OPEN case only -- the push
    target is not a github.com remote at all, so there is no check to run
    or retry.
    `deny_kind`: `"public"` or `"unreachable"` when `violations` is
    populated, else None.
    `source`/`age_seconds`/`reason`: the visibility verdict's provenance,
    as returned by `repo_visibility`.
    `enumeration_failed`: True when `find_tracked_doctrine_paths` itself
    could not examine the checkout -- distinct from "examined, nothing
    tracked", and visibility is never consulted in that case: with no idea
    whether anything is tracked, a visibility answer cannot settle
    anything. Both lists are empty on a genuine clean result AND on an
    enumeration failure; callers must check `enumeration_failed` to tell
    them apart.
    """

    violations: list[str]
    unknown_tracked: list[str]
    deny_kind: str | None
    source: str | None
    age_seconds: float
    reason: str | None
    enumeration_failed: bool


def check_public_tracked_doctrine(
    repo_root: Path,
    paircoder_dir: Path,
    *,
    slug: str | None = None,
    have_target: bool = False,
    use_cache: bool = True,
) -> DoctrineCheckResult:
    tracked = find_tracked_doctrine_paths(repo_root)
    if tracked is None:
        return DoctrineCheckResult([], [], None, None, 0.0, None, True)
    if not tracked:
        return DoctrineCheckResult([], [], None, None, 0.0, None, False)
    if slug is None and have_target:
        # Not a github.com remote at all -- see the module docstring's
        # NOT-A-GITHUB-REMOTE POSTURE. There is no `gh` check to make,
        # live or cached, so this is the one fail-OPEN case left.
        return DoctrineCheckResult([], tracked, None, None, 0.0, None, False)
    visibility, source, age_seconds, reason = repo_visibility(
        repo_root,
        paircoder_dir,
        slug=slug,
        use_cache=use_cache,
    )
    if visibility == "PUBLIC":
        return DoctrineCheckResult(
            tracked, [], "public", source, age_seconds, None, False
        )
    if visibility is None:
        # A LIVE check that could not be completed -- see the module
        # docstring's UNREACHABLE-VISIBILITY POSTURE. Fail CLOSED: an
        # unproven remote is refused exactly like a confirmed-PUBLIC one.
        return DoctrineCheckResult(
            tracked, [], "unreachable", source, age_seconds, reason, False
        )
    return DoctrineCheckResult([], [], None, source, age_seconds, None, False)


def _format_deny_message(result: DoctrineCheckResult, paths: str) -> str:
    template = (
        DENY_TEMPLATE if result.deny_kind == "public" else UNREACHABLE_DENY_TEMPLATE
    )
    return template.format(paths=paths, marker=OPT_OUT_MARKER, reason=result.reason)


def evaluate(
    repo_root: Path,
    remote_args: list[str] | None = None,
) -> tuple[str | None, str | None]:
    """Return `(deny_reason, warning)`. `deny_reason` set -> refuse the
    push (nonzero exit); `warning` set -> printed to stderr but the push
    proceeds. Never both at once.

    *remote_args* is git's pre-push argument pair (`[name, url]`); it is
    what makes this a check of the remote being pushed TO rather than of
    whatever `origin` happens to be.
    """
    paircoder_dir = repo_root / ".paircoder"
    have_target = bool(remote_args)
    slug = push_target_slug(repo_root, remote_args or [])
    result = check_public_tracked_doctrine(
        repo_root,
        paircoder_dir,
        slug=slug,
        have_target=have_target,
    )
    if result.enumeration_failed:
        return None, ENUMERATION_FAILED_WARNING_TEMPLATE
    if result.violations:
        paths = "\n".join(f"  - {p}" for p in result.violations)
        deny_message = _format_deny_message(result, paths)
        marker = repo_root / OPT_OUT_MARKER
        kind = _marker_kind(marker)
        if kind == "symlink":
            sys.stderr.write(SYMLINK_WARNING_TEMPLATE.format(marker=OPT_OUT_MARKER))
            return deny_message, None
        if kind == "file":
            error = _log_bypass(
                repo_root,
                target=marker,
                verdict=result.deny_kind,
                source=result.source,
                age_seconds=result.age_seconds,
            )
            if error is not None:
                return (
                    AUDIT_FAILURE_TEMPLATE.format(
                        marker=OPT_OUT_MARKER,
                        log_path=BYPASS_LOG_REL,
                        error=error,
                    ),
                    None,
                )
            return None, ADVISORY_TEMPLATE.format(marker=OPT_OUT_MARKER)
        return deny_message, None
    if result.unknown_tracked:
        paths = "\n".join(f"  - {p}" for p in result.unknown_tracked)
        return None, UNKNOWN_WARNING_TEMPLATE.format(paths=paths)
    return None, None


# The visibility cache/resolver (`repo_visibility` + its cache/live-`gh`
# helpers) lives in the sibling `public_repo_push_guard_visibility.py`.
# Imported with a fail-open degraded fallback below: a bare
# `from public_repo_push_guard_visibility import repo_visibility` in a
# partial-payload consumer would raise `ImportError` inside this
# git-native pre-push hook -- and an ImportError here blocks EVERY push.
# So a missing/broken sibling degrades exactly the way
# `base_branch_guard.py`'s facade does: it NEVER bricks a push, it reports
# the missing payload file(s) and writes one audited degraded row.
try:
    from public_repo_push_guard_visibility import repo_visibility  # noqa: F401
except Exception as _sibling_import_error:  # noqa: E402
    # The error is captured into a plain string HERE, not read from
    # `_sibling_import_error` inside `main()`: Python deletes an
    # `except ... as name` binding when the except block exits, and
    # `main()` is not CALLED until `__main__` much later -- by then the
    # name is gone, and referencing it would raise `NameError`, not report
    # the import failure. Same shape as `base_branch_guard.py`.
    _sibling_import_message = str(_sibling_import_error)
    _degraded_module_name = getattr(_sibling_import_error, "name", None) or (
        "a public_repo_push_guard sibling module"
    )

    _DEGRADED_BANNER = (
        "\n"
        "==========================================================\n"
        "public_repo_push_guard: DEGRADED -- sibling module import failed\n"
        "  ({module}: {message})\n"
        "  payload: {payload}\n"
        "  Public-remote tracked-doctrine enforcement for THIS push is\n"
        "  NOT ACTIVE -- the push is ALLOWED (fail-open) so a partial\n"
        "  payload never bricks every push, the same trade-off the\n"
        "  UNKNOWN-visibility case already makes for an offline push.\n"
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
        "public_repo_push_guard.py",
        "public_repo_push_guard_visibility.py",
    )

    _DEGRADED_HOOKS_DIR = Path(__file__).resolve().parent

    def _degraded_payload_report() -> str:
        """Which required payload files are absent, by name.

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

    def _degraded_repo_root(start: Path) -> Path | None:
        for directory in (start, *start.parents):
            if (directory / ".git").exists() and (directory / ".paircoder").is_dir():
                return directory
        return None

    def _log_degraded_fail_open(cwd: str) -> None:
        """Best-effort, audited note that this push ran degraded -- never
        raises, never turns the fail-open into a fail-closed on its own
        failure. Written to the SAME unified bypass ledger every other
        exemption in this guard writes to, under a `bypass_type` registered
        for `bpsai-pair audit bypasses`."""
        repo_root = _degraded_repo_root(Path(cwd))
        if repo_root is None:
            return
        timestamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
        entry = {
            "timestamp": timestamp,
            "command": "public_repo_push_guard",
            "target": _sibling_import_message,
            "bypass_type": "public_repo_push_guard_degraded_fail_open",
            "gate": "public_repo_push_guard",
            "action": "degraded_fail_open",
            "branch": "",
            "tool": "git-push",
        }
        log_path = repo_root / BYPASS_LOG_REL
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def main() -> int:  # type: ignore[no-redef]
        """DEGRADED: a required sibling failed to import. NEVER brick a
        push over a partial payload -- allow it (fail-open, exit 0), but
        loudly (an stderr banner naming the missing file) and with one
        audited row, mirroring `base_branch_guard.py`'s degraded posture.
        The only enforcement lost here is the tracked-doctrine leak guard;
        allowing the push is the same trade-off the UNKNOWN-visibility case
        already makes for an offline push."""
        cwd = os.getcwd()
        sys.stderr.write(
            _DEGRADED_BANNER.format(
                module=_degraded_module_name,
                message=_sibling_import_message,
                payload=_degraded_payload_report(),
            )
        )
        _log_degraded_fail_open(cwd)
        return 0

else:

    def main() -> int:
        """Fail OPEN on the guard's own errors; fail CLOSED on a confirmed
        PUBLIC + tracked match. A pre-push hook aborts the push on any
        nonzero exit -- git's own convention, matching `git_commit_guard.py`'s
        pre-commit posture."""
        try:
            repo_root = Path.cwd()
            if not (repo_root / ".paircoder").is_dir():
                return 0
            # git's own pre-push arguments: remote name, then remote URL.
            deny_reason, warning = evaluate(repo_root, sys.argv[1:3])
        except Exception as exc:  # fail open: never brick a push on our own bug
            sys.stderr.write(f"public_repo_push_guard: fail-open ({exc})\n")
            return 0
        if warning:
            sys.stderr.write(warning)
        if deny_reason is None:
            return 0
        sys.stderr.write(f"BLOCKED BY ENFORCEMENT GATE\n\n{deny_reason}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
