"""HTTP-client / direct-API merge-endpoint primitives for `pr_merge_guard.py`.

Split out of the main hook module (architecture: file/function-count caps).
Stdlib-only, no `bpsai_pair` import, matching the main hook's own runtime
contract. These started as pure lexical primitives -- recognizing an
HTTP-client executable, stripping a trailing query/fragment off a path
argument, telling whether a segment names a REST merge path or GraphQL
merge mutation -- with `pr_merge_guard.py`'s own `_segment_is_gh_api_merge`
building on top of them, never the reverse.

Review round 6, P1 (a stray `gh` token that is not the invoked program
never suppresses the HTTP-client backstop; the two branches are OR-ed,
fail closed) moved the two verdict-shaped helpers this module now also
carries -- `gh_first_segment_is_api_merge`/`http_client_segment_is_api_merge`
-- here too, alongside `_segment_declares_get_method`/
`_segment_has_file_fed_body`, which the gh-first helper needs: keeping
BOTH halves of `_segment_is_gh_api_merge`'s OR in the same module (and its
one now-necessary `pr_merge_guard_parse` import) means `pr_merge_guard.py`
itself calls a single, ready-made verdict for each branch rather than
duplicating the gh-first rule body at each call site.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath

from pr_merge_guard_parse import (
    _first_non_flag_index,
    _GRAPHQL_MERGE_MUTATIONS,
    _gh_subcommand_index,
    _segment_is_gh_graphql_merge_mutation,
)

#: A REST path shaped like `repos/<owner>/<repo>/pulls/<number>/merge` --
#: used only by `_segment_is_gh_api_merge`'s unrecognized-global-flag
#: fallback, to tell an `api` token that is actually the `gh api`
#: subcommand apart from one that is merely some OTHER argument's value
#: (a PR title, a comment body, ...) that happens to read "api".
_PULLS_MERGE_PATH_RE = re.compile(r"(?:^|/)pulls/\d+/merge/?$", re.IGNORECASE)

#: Trailing `?query` or `#fragment` on a `gh api` path argument -- gh
#: passes these straight through to the REST route, so
#: `repos/o/r/pulls/1/merge?sha=abc` (or `#fragment`) is still a merge
#: call. Stripped before the endswith/regex suffix checks below, both of
#: which are anchored at the end of the token and would otherwise miss it
#: (review round 7).
_QUERY_OR_FRAGMENT_RE = re.compile(r"[?#].*$", re.DOTALL)

#: Executables that can place a direct HTTP call to GitHub's REST/GraphQL
#: API without ever going through `gh` -- used only by
#: `_segment_is_gh_api_merge`'s non-`gh`-first fallback (the gh-api
#: backstop prefix-blindness fix, round 2) to tell a genuine direct-merge
#: call (`curl -X PUT https://api.github.com/repos/o/r/pulls/1/merge`)
#: apart from an ordinary command that merely names a path ending in
#: `/merge` (`ls docs/merge`, `curl https://example.com/docs/merge`).
_HTTP_CLIENT_EXECUTABLES = frozenset({"curl", "wget", "http", "https", "xh"})


def _is_http_client_spelling(token: str) -> bool:
    """True when *token* denotes one of `_HTTP_CLIENT_EXECUTABLES`, however
    it is spelled -- bare name, a relative/absolute POSIX or Windows path
    ending in it, any case variant, or the `.exe` suffix. Mirrors
    `_is_gh_spelling`'s lexical-only recognition."""
    for name in (PurePosixPath(token).name, PureWindowsPath(token).name):
        lowered = name.lower()
        if lowered.endswith(".exe"):
            lowered = lowered[: -len(".exe")]
        if lowered in _HTTP_CLIENT_EXECUTABLES:
            return True
    return False


def _strip_query_fragment(token: str) -> str:
    return _QUERY_OR_FRAGMENT_RE.sub("", token)


def _segment_names_merge_endpoint(segment: list) -> bool:
    """True when any token in *segment* names a REST merge path (any
    argument ending in `/merge`, ignoring a trailing `?query`/`#fragment`)
    or a GraphQL `mergePullRequest` mutation."""
    for token in segment:
        lowered = _strip_query_fragment(token.lower())
        if lowered.rstrip("/").endswith("/merge"):
            return True
        if any(mutation in lowered for mutation in _GRAPHQL_MERGE_MUTATIONS):
            return True
    return False


#: Flags that hand `gh api` a request body sourced from a FILE or STDIN
#: rather than argv text -- `--input <file-or-->`, or a `-f`/`-F`/
#: `--field`/`--raw-field key=@file` gh-native file reference. Whatever the
#: body actually contains (a `mergePullRequest` mutation, a REST merge
#: payload) is invisible to any argv-only scan. Moved here (review round 6,
#: P1) alongside `_segment_declares_get_method`/`_segment_has_file_fed_body`,
#: which `gh_first_segment_is_api_merge` needs.
_FILE_FED_BODY_FLAGS = ("--input",)
_FIELD_FLAGS = ("-f", "-field", "--field", "-F", "--raw-field")


def _segment_declares_get_method(segment: list) -> bool:
    """True when *segment*'s LAST `-X`/`--method` occurrence names GET --
    the one shape provably read-only regardless of any file-fed body flag
    also present.

    Two fixes (review round 7): gh, like curl, honours the LAST of a
    repeated `-X`/`--method` flag, so `-X GET -X POST` is a POST, not a
    GET -- every occurrence is scanned and only the last one's value
    decides the verdict, rather than returning True on the first GET seen
    even when a later flag overrides it. And the `--method=` branch used
    to upper-case the value and then compare it against the LOWER-case
    literal `"get"`, which could never match (`--method=GET` was silently
    never recognized) -- both sides are now upper-cased before comparing.
    """
    last_method = None
    index = 0
    while index < len(segment):
        token = segment[index]
        lowered = token.lower()
        if lowered in ("-x", "--method"):
            nxt = segment[index + 1] if index + 1 < len(segment) else ""
            last_method = nxt.strip().upper()
            index += 2
            continue
        if lowered.startswith("--method="):
            last_method = lowered.split("=", 1)[1].strip().upper()
        elif lowered.startswith("-x") and lowered != "-x":
            last_method = lowered[2:].strip().upper()
        index += 1
    return last_method == "GET"


def _segment_has_file_fed_body(segment: list) -> bool:
    """True when *segment* supplies its `gh api` request body from a file
    or stdin -- `--input <anything, including `-` for stdin>`, or a
    `-f`/`-F`/`--field`/`--raw-field key=@file` gh-native file reference.
    Neither shape puts the body's actual content on argv, so a scan of the
    literal tokens (`/merge`, `mergepullrequest`) can never see what it
    contains."""
    for index, token in enumerate(segment):
        lowered = token.lower()
        if lowered in _FILE_FED_BODY_FLAGS or lowered.startswith("--input="):
            return True
        if lowered in _FIELD_FLAGS:
            nxt = segment[index + 1] if index + 1 < len(segment) else ""
            if "=@" in nxt:
                return True
            continue
        if "=@" in token and any(lowered.startswith(flag) for flag in _FIELD_FLAGS):
            return True
    return False


def gh_first_segment_is_api_merge(segment: list) -> bool:
    """True when *segment* -- already resolved so `segment[0]` is a `gh`
    spelling -- is a `gh api` call naming a REST merge endpoint, a GraphQL
    `mergePullRequest`/`enablePullRequestAutoMerge` mutation, or a call
    whose request body is file-/stdin-fed and not provably a GET.

    The gh-first rule body `pr_merge_guard.py`'s `_segment_is_gh_api_merge`
    used to apply inline once it resolved `gh` to `segment[0]` (a direct
    call) OR re-sliced the segment at a LATER `gh` spelling (a wrapper
    ahead of it, e.g. `nohup gh api ...`) -- factored out here (review
    round 6, P1) so both call sites -- and the OR'd HTTP-client verdict
    computed alongside the wrapper-slice one -- share one rule body rather
    than risking the two drifting apart."""
    if _segment_is_gh_graphql_merge_mutation(segment):
        return True
    subcommand = _gh_subcommand_index(segment)
    if subcommand < len(segment) and segment[subcommand].lower() == "api":
        pass
    else:
        fallback = _first_non_flag_index(segment)
        if fallback < len(segment) and segment[fallback].lower() == "api":
            pass
        else:
            has_api = any(t.lower() == "api" for t in segment[1:])
            stripped = [_strip_query_fragment(t) for t in segment[1:]]
            has_merge_path = any(_PULLS_MERGE_PATH_RE.search(t) for t in stripped)
            if not (has_api and has_merge_path):
                return False
    if _segment_names_merge_endpoint(segment[1:]):
        return True
    file_fed_body = _segment_has_file_fed_body(segment)
    declares_get = _segment_declares_get_method(segment)
    if file_fed_body and not declares_get:
        return True
    return False


def http_client_segment_is_api_merge(segment: list) -> bool:
    """True when *segment* -- the remainder of a command starting at its
    first executable, whatever that executable turns out to be -- names a
    non-`gh` HTTP client (`_is_http_client_spelling`: `curl`, `wget`,
    `http`/`https`, `xh`) ANYWHERE in it, and also names a
    `repos/.../pulls/<n>/merge` path (`_PULLS_MERGE_PATH_RE`, which also
    matches a full `https://api.github.com/repos/.../pulls/<n>/merge` URL)
    or a `mergePullRequest` mutation token -- a genuine direct merge call
    that bypasses `gh` (and every gh-level check) entirely.

    Computed independently of `gh_first_segment_is_api_merge` -- callers OR
    the two together -- so a `gh` spelling appearing ANYWHERE in the same
    segment purely as some OTHER argument's value (a `curl -A gh` user
    agent, a `wget -O gh` output filename, `-o gh`, ...) never pre-empts
    this check the way re-slicing the segment at that stray token would
    (review round 6, P1: the regression this closes)."""
    if not any(_is_http_client_spelling(token) for token in segment):
        return False
    stripped = [_strip_query_fragment(t) for t in segment]
    if any(_PULLS_MERGE_PATH_RE.search(t) for t in stripped):
        return True
    return any(
        mutation in t.lower() for t in stripped for mutation in _GRAPHQL_MERGE_MUTATIONS
    )
