# GENERATED COPY of bpsai_pair/guards/repo_visibility.py -- do not edit.
"""Remote-visibility resolution (rate-limit cache + live `gh` check) for
`public_repo_push_guard`.

Split out of `public_repo_push_guard.py`: the file had grown to
the 15-function architecture cap, and the visibility cache/resolver is the
self-contained cluster that leaves cleanly. See
`public_repo_push_guard.py`'s module docstring for the "LIVE ON EVERY
PUSH" and "PUSH TARGET, NOT `origin`" rationale this cache mechanism
serves.

CACHE IS DENY-ONLY: a cached verdict may only ever be used to short-circuit
a DENY (a PUBLIC verdict still inside its TTL skips the `gh` round-trip,
since re-checking cannot make a confirmed-public remote any safer to
allow). A cached non-public verdict is NEVER used to grant an allow -- a
private -> public flip inside the TTL window would otherwise look exactly
like a clean push, which is the TOCTOU hole this module exists to close.
Every allow is therefore decided from a LIVE check, every time.

CANONICAL SOURCE: this file, `bpsai_pair.guards.repo_visibility`, is the
one place this trust model is written down. It has exactly two
consumers, and neither ever executes code sourced from a repo it is
auditing:

- The pre-push hook payload, `.claude/hooks/public_repo_push_guard_visibility.py`
  (and its cookiecutter template copy), is a VERBATIM byte-for-byte copy
  of this module plus a one-line "generated from ... -- do not edit"
  header comment. A unit test asserts byte-identity between this module
  and both payload copies, so drift fails CI instead of silently
  re-diverging. The hook imports ONLY its sibling payload file (a plain
  `sys.path`-relative import) -- it never reaches into the package,
  since it must run standalone with plain `python3` on an operator
  machine with no PairCoder venv (stdlib only, no `bpsai_pair` import).
  This sibling is a REQUIRED payload file: the main guard's
  `_DEGRADED_REQUIRED_FILES` names it, and a partial-sync repo missing it
  makes the main guard degrade LOUDLY (fail-open + audited row) rather
  than raise an `ImportError` that would brick every push.

- `bpsai_pair.commands.public_repo_guard` (the CLI's `audit
  public-repo-tracked` / `fleet audit` path) imports THIS module
  normally, as ordinary package code. A round-3 review finding was that
  the CLI previously loaded the hook's payload copy OUT OF THE AUDITED
  REPO via `importlib.util.spec_from_file_location` and executed it --
  meaning `bpsai-pair audit`/`fleet audit` would run arbitrary code
  planted in any repo it was pointed at. Importing the package module
  instead of a repo-sourced file removes that execution path entirely:
  the CLI's own copy of this logic never depends on what a target repo's
  checkout happens to contain.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

# `gh`'s stderr can carry a token-shaped string (an expired PAT echoed back
# in an auth error, a URL with a query-string credential) that must never
# reach a deny message a user pastes into a chat or ticket. Classify by
# shape instead of ever interpolating the raw text: an HTTP status class
# when `gh` printed one, else a fixed catch-all -- paired with one fixed
# remedy line so the reason is still actionable.
_HTTP_STATUS_RE = re.compile(r"\bHTTP (\d)\d\d\b")
_GH_FAILURE_REMEDY = "check `gh auth status` and network connectivity, then retry"


def _classify_gh_stderr(stderr: str) -> str:
    match = _HTTP_STATUS_RE.search(stderr)
    if match:
        return f"gh reported an HTTP {match.group(1)}xx error"
    lowered = stderr.lower()
    if "rate limit" in lowered:
        return "gh reported it is rate-limited"
    if "auth" in lowered:
        return "gh reported an authentication problem"
    return "gh reported a failure"


# Bounded to minutes, not hours: this cache exists purely to bound the
# `gh` call rate, not to trust a stale verdict. It is only ever consulted
# to short-circuit a DENY (see the module docstring), so a short TTL costs
# nothing in safety and only trims repeat `gh` calls on a hot push loop.
VISIBILITY_CACHE_TTL_SECONDS = 10 * 60
_VISIBILITY_CACHE_FILENAME = "repo-visibility.json"


class CacheEntry(NamedTuple):
    """A cache read's outcome: `visibility` is None on a miss/stale/corrupt
    entry, in which case `age_seconds` is meaningless (reported as 0.0)."""

    visibility: str | None
    age_seconds: float


class VisibilityResult(NamedTuple):
    """`repo_visibility`'s full answer, kept together so a caller can audit
    exactly what was decided and from where.

    `visibility`: "PUBLIC"/"PRIVATE"/"INTERNAL", or None when it could not
    be determined at all (a live check failure -- see `reason`).
    `source`: "cached" only for the PUBLIC-short-circuit path described in
    the module docstring; "live" otherwise.
    `age_seconds`: the cache entry's age when `source == "cached"`, else 0.0.
    `reason`: a short human-readable cause, populated only when a LIVE
    check could not resolve a visibility (network/auth/rate-limit/etc.).
    """

    visibility: str | None
    source: str
    age_seconds: float
    reason: str | None


def _visibility_cache_path(paircoder_dir: Path) -> Path:
    return paircoder_dir / "cache" / _VISIBILITY_CACHE_FILENAME


def _load_cache_document(paircoder_dir: Path) -> dict:
    """The whole cache file as a dict -- `{}` when missing or unreadable."""
    try:
        # Kept split: collapses to a 92-char line at line-length=100.
        data = json.loads(
            _visibility_cache_path(paircoder_dir).read_text(encoding="utf-8")
        )  # fmt: skip
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_visibility_cache(paircoder_dir: Path, slug: str | None = None) -> CacheEntry:
    """The cached visibility for *slug* (or the default remote when None),
    with its age in seconds -- `CacheEntry(None, 0.0)` on a miss/stale/
    corrupt/unusable entry.

    A `checked_at` that parses but carries no timezone is a cache MISS,
    not an error: subtracting a naive `datetime` from an aware one raises
    `TypeError`, which -- uncaught -- propagates all the way to `main`'s
    blanket fail-open and turns one malformed cache file into a guard that
    silently allows every push. `TypeError` is caught alongside
    `ValueError` here so the entry is simply re-checked live instead.
    """
    document = _load_cache_document(paircoder_dir)
    entry: object = document
    if slug is not None:
        remotes = document.get("remotes")
        entry = remotes.get(slug) if isinstance(remotes, dict) else None
    if not isinstance(entry, dict):
        return CacheEntry(None, 0.0)
    checked_at, visibility = entry.get("checked_at"), entry.get("visibility")
    if not isinstance(checked_at, str) or not isinstance(visibility, str):
        return CacheEntry(None, 0.0)
    try:
        checked = datetime.fromisoformat(checked_at)
        age = (datetime.now(timezone.utc) - checked).total_seconds()
    except (TypeError, ValueError):
        return CacheEntry(None, 0.0)
    if not (0 <= age <= VISIBILITY_CACHE_TTL_SECONDS):
        return CacheEntry(None, 0.0)
    return CacheEntry(visibility, age)


def _write_visibility_cache(
    paircoder_dir: Path,
    visibility: str,
    slug: str | None = None,
) -> None:
    """Best-effort cache write -- never raises (a read-only `.paircoder/`
    must not fail the check it is only trying to speed up). Shares the
    cache file `bpsai_pair.commands.public_repo_guard` uses, so this hook
    and `audit public-repo-tracked` do not double the `gh` call rate.

    A per-remote verdict is stored under `remotes[<slug>]`, leaving the
    top-level `visibility`/`checked_at` pair (the default-remote verdict
    the CLI module reads and writes) untouched -- one repo-wide verdict
    answering for every remote is the cache half of the mis-targeting bug.
    """
    path = _visibility_cache_path(paircoder_dir)
    record = {
        "visibility": visibility,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    document = _load_cache_document(paircoder_dir)
    if slug is None:
        document.update(record)
    else:
        remotes = document.get("remotes")
        document["remotes"] = (
            {**remotes, slug: record} if isinstance(remotes, dict) else {slug: record}
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document), encoding="utf-8")
    except OSError:
        pass


def _fetch_visibility(
    repo_root: Path, slug: str | None = None
) -> tuple[str | None, str | None]:
    """`(visibility, reason)` from `gh repo view [<slug>] --json visibility`.

    `visibility` is "PUBLIC"/"PRIVATE"/"INTERNAL" on success. On failure it
    is None and `reason` names why -- not a GitHub remote, `gh` missing/
    unauthenticated, network down, rate-limited, or a malformed response --
    so a fail-closed caller can state a concrete cause rather than a bare
    "unknown". Never raises.

    Without *slug* this asks about the working directory's DEFAULT remote,
    which is only the right question when no push target could be resolved
    at all.
    """
    command = ["gh", "repo", "view", "--json", "visibility"]
    if slug:
        # `--` stops flag parsing for what follows: a slug that begins
        # with `-` (never expected from a real git remote, but this is a
        # git-native hook parsing untrusted-shaped remote config) can
        # never be read as a `gh` flag instead of a positional argument.
        command += ["--", slug]
    try:
        result = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return None, "gh timed out after 15s"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"gh unavailable ({type(exc).__name__}); {_GH_FAILURE_REMEDY}"
    if result.returncode != 0:
        return None, (
            f"{_classify_gh_stderr(result.stderr)} (exit {result.returncode}); "
            f"{_GH_FAILURE_REMEDY}"
        )
    if not result.stdout.strip():
        return None, "gh returned no output"
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "gh output was not valid JSON"
    visibility = data.get("visibility")
    if not isinstance(visibility, str):
        return None, "gh output was missing a visibility field"
    return visibility, None


def repo_visibility(
    repo_root: Path,
    paircoder_dir: Path,
    *,
    slug: str | None = None,
    use_cache: bool = True,
) -> VisibilityResult:
    """The push target's visibility, deciding an ALLOW from a LIVE `gh`
    check every time (see the module docstring's "CACHE IS DENY-ONLY"):
    a cache hit is consulted ONLY to short-circuit a DENY when it is
    already known PUBLIC and still within `VISIBILITY_CACHE_TTL_SECONDS`.
    Anything else -- a cache miss, an expired entry, or a cached non-public
    verdict -- falls straight through to a live check; a cached "private"
    or "internal" answer is never trusted to grant an allow on its own.

    A live PUBLIC result is cached afterward so a following push can take
    the short-circuit path above. A live PRIVATE/INTERNAL result is never
    written: this cache is consulted ONLY to short-circuit a DENY, so a
    non-public verdict would be a write nothing ever reads back -- dead
    weight that only widens the window for the file to be tampered with.
    An indeterminate live result (`visibility is None`) is likewise never
    cached, so a transient `gh` failure gets retried next push rather than
    sticking.
    """
    if use_cache:
        cached = _read_visibility_cache(paircoder_dir, slug)
        if cached.visibility == "PUBLIC":
            return VisibilityResult("PUBLIC", "cached", cached.age_seconds, None)
    visibility, reason = _fetch_visibility(repo_root, slug)
    if visibility == "PUBLIC":
        _write_visibility_cache(paircoder_dir, visibility, slug)
    return VisibilityResult(visibility, "live", 0.0, reason)
