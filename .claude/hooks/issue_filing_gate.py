#!/usr/bin/env python3
"""PreToolUse hook (matcher `Bash`): detects a raw `gh` issue/PR filing call
(or a direct GitHub API write to the same endpoints) and redirects the
caller to `bpsai-pair gh issue|pr create|comment` instead of evaluating the
filing itself.

DESIGN RULING (replacing several earlier review rounds of raw-shell
parsing in this file): a PreToolUse hook that parses
arbitrary shell text to resolve a filing command's target repo, body, and
flags cannot be made complete -- there is always another wrapper/subshell/
substitution shape a regex-over-raw-text parser does not see through. So
this hook stops trying. The audience-acknowledgement, PII-pattern refusal,
and label-class checks all moved into `bpsai_pair.github_filing.commands`
(`bpsai-pair gh issue|pr create|comment`), a CLI command that receives its
own arguments directly instead of re-parsing someone else's shell
invocation. This hook's only remaining job is SCOPE: does the raw command
text contain a `gh` filing verb, or a direct (non-`gh`) API write to the
same endpoints? If so, refuse and name the replacement command -- no body
scanning, no visibility lookup, no segment/heredoc parsing.

The wrapper itself (`bpsai-pair gh ...`) never loops back through this
hook: it runs the real `gh` binary as a subprocess of the `bpsai-pair`
process, not as a Bash-tool command this hook ever sees.

THREAT MODEL: a PreToolUse regex hook cannot be a security boundary
against the agent it constrains -- an adversarial agent can `curl` the
GitHub REST API directly, and no amount of raw-text pattern matching here
closes that off. What this hook actually defends against is ACCIDENTAL
raw filing by a cooperative agent that forgot the wrapper exists; the
wrapper (`bpsai-pair gh ...`) is the real enforcement point for audience/
PII/label checks, not this hook. Review feedback that scores an
adversarial bypass of this hook itself is scoring against a threat model
this hook was never meant to cover.

THE ONE EXEMPTION: a small read-only command (`grep`/`rg`/`cat`/`less`/
`echo`/`printf`, or `git log`/`git commit -m`) may quote a filing phrase as
inert text -- `grep "gh issue create" README.md` -- without ever running
it. Disqualified by a `$( )`/backtick substitution anywhere in its own
arguments (that text executes regardless of which command reads its
output) or a code-execution flag.

Runtime contract: stdlib only, plain `python3`, no `bpsai_pair` import --
this runs on operator machines with no PairCoder venv.

FAIL-OPEN / FAIL-CLOSED SPLIT (not a blanket fail-open, despite this
docstring's own earlier wording): this hook fails open on its OWN
malformed input, and on an unexpected internal error when the raw
(unparsed) stdin text does not even look filing-shaped -- never brick an
unrelated Bash call over our own bug. It fails CLOSED, however, when an
internal error strikes while that raw text DOES look filing-shaped (see
`_FILING_SHAPED_RE`): an unscanned filing is never an acceptable trade for
avoiding a false block. See `main()`'s except-clause for the exact
branch.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path, PurePosixPath

# A real hook invocation is a fresh `python3 <this file>.py` subprocess in a
# consumer's checkout -- importing the sibling module below would otherwise
# leave `__pycache__/*.pyc` build artifacts sitting untracked in the payload
# tree. Set BEFORE the sibling import; mirrors `base_branch_guard.py`'s own
# `sys.dont_write_bytecode` shim for the exact same reason.
sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from issue_filing_gate_bypass import (
        BYPASS_ENV_NAME,
        WRAPPER_MIN_VERSION,
        grant_bypass as _grant_bypass,
        wrapper_genuinely_unavailable as _wrapper_genuinely_unavailable,
    )
except Exception:  # noqa: BLE001 -- deliberately broad: see below
    # A missing OR CORRUPT sibling must not fail OPEN (a corrupt file raises
    # at exec time, not ImportError): an uncaught ImportError kills the hook
    # before ``main()``'s fail-closed branch runs, and a PreToolUse hook dying
    # with a non-2 exit is non-blocking -- a raw filing command would sail
    # through. Degraded posture instead: the redirect still fires for
    # filing-shaped input, and the bypass NEVER grants (no probe, no auditable
    # ledger writer -- audit-or-deny means deny).
    BYPASS_ENV_NAME = "PAIRCODER_FILING_BYPASS_REASON"
    WRAPPER_MIN_VERSION = "2.49.8"  # first PUBLISHED release with the fixed exec seam

    def _grant_bypass(cwd: str, reason: str) -> bool:  # noqa: ARG001
        return False

    def _wrapper_genuinely_unavailable(command, shadow_re) -> bool:  # noqa: ARG001
        return False
finally:
    if sys.path and sys.path[0] == str(Path(__file__).resolve().parent):
        sys.path.pop(0)

DENY_PREFIX = "BLOCKED BY ENFORCEMENT GATE\n\n"

REDIRECT_MESSAGE = (
    "file through `bpsai-pair gh <issue|pr> <create|comment> ...` (same "
    "flags; adds --audience/--contains-pii; use --reply-to for in-thread "
    "replies) instead of a raw `gh`/API call -- this hook no longer "
    "evaluates filing commands itself. Requires "
    f"bpsai-pair >= {WRAPPER_MIN_VERSION} (the first release carrying the "
    "`gh` command group). If the installed CLI genuinely predates that "
    f'version, prefix the command with `{BYPASS_ENV_NAME}="..."` for an '
    "audited bypass instead."
)

CONTROL_OPERATOR_TOKENS = frozenset({";", "&", "&&", "||", "|", "\n"})

# What `main()` scans the raw, unparsed stdin text for when its own
# detection logic hits an internal error -- deliberately looser than
# `IN_SCOPE_ISSUE_PR_RE`/`IN_SCOPE_API_RE` (no verb/flag shape required):
# this is the fail-closed fallback, not the primary detector.
_FILING_SHAPED_RE = re.compile(r"gh issue|gh pr|gh api|api\.github\.com")

# `gh issue|pr create|comment|new`, anywhere in the raw text, independent
# of wrappers/subshells/substitutions -- matched on raw text on purpose
# (see module docstring): there is no tokenizer that sees through every
# wrapper shape, so scope is decided on the text itself, not a parse of it.
# `new` is `create`'s documented alias for both `gh issue` and `gh pr`
# (see each command's own `--help` ALIASES section), so it must be caught
# here too -- it is a normal cooperative-user spelling, not an adversarial
# one.
IN_SCOPE_ISSUE_PR_RE = re.compile(
    r"\bgh\b(\s+(-R|--repo|--hostname)\s*=?\s*\S+){0,3}\s+(issue|pr)\s+(create|new|comment)\b"
)

IN_SCOPE_API_RE = re.compile(
    r"\bgh\s+api\b[^|;&]*\b"
    r"(repos/[^/\s]+/[^/\s]+/(issues|pulls)(/\d+/comments)?|/issues\b)"
)

IN_SCOPE_GRAPHQL_RE = re.compile(r"\bgh\s+api\s+graphql\b")
GRAPHQL_MUTATION_RE = re.compile(
    r"\b(?:createIssue|addComment|createPullRequest|addPullRequestReview)\b"
)

# A direct (non-`gh`) GitHub API write via curl/wget/http/an interpreter's
# inline code, against api.github.com, targeting issues/pulls/comments/
# graphql, with a write-shaped flag present.
_API_HOST_RE = re.compile(r"\bapi\.github\.com\b|\bgithub\.com/api\b")
_API_WRITE_PATH_RE = re.compile(r"/(?:issues|pulls|comments)\b|\bgraphql\b")
_API_TOOL_RE = re.compile(r"\b(?:curl|wget|http|python3?)\b")

# ONE regex, one definition, for every write-shaped flag on a `gh api` call
# OR a direct (curl/wget/...) API call: `--method`, `-X`, `-f`/`--field`,
# `-F`/`--raw-field`, `-d`/`--data`, `--input`. Two separately-maintained
# patterns previously existed and the `gh api` one omitted every long-form
# flag (`--method POST ... --raw-field` sailed through unmatched); one
# definition used in both places closes that gap and keeps it closed.
_API_WRITE_MARKER_RE = re.compile(
    r"-X\s*(?:POST|PUT|PATCH|DELETE)\b"
    r"|--method\s*=?\s*(?:POST|PUT|PATCH|DELETE)\b"
    r"|-f\b|--field\b"
    r"|-F\b|--raw-field\b"
    r"|-d\b|--data\b"
    r"|--input\b"
)
API_WRITE_MARKER_RE = _API_WRITE_MARKER_RE
_DIRECT_API_WRITE_MARKER_RE = _API_WRITE_MARKER_RE

_SUBSTITUTION_RE = re.compile(r"\$\((?P<paren>.*?)\)|`(?P<tick>[^`]*)`", re.DOTALL)

# The `bpsai-pair gh ...` wrapper carries the words "gh issue create" etc. as
# ITS OWN arguments and must never be redirected to itself. Recognized by its
# command word (bare, path-qualified, or Windows `.exe`) preceding `gh`.
# Group 1 is the whole token. When it opens with a quote (group 2) the same
# quote must close it, and the directory part may then contain spaces.
_WRAPPER_INVOCATION_RE = re.compile(
    r"(?i)(?:^|[;&|\n(]|\s)((?P<q>[\"'])?(?(q)(?:[^\"'\n]*[\\/])?|[\w./\\:-]*)\bbpsai-pair(?:\.exe)?(?(q)(?P=q)))\s+gh\b"
)

# The wrapper-invocation exemption above trusts the STRING "bpsai-pair" --
# it never verifies that name actually resolves to the real installed CLI.
# A command that also defines/aliases `bpsai-pair` as a shell function, or
# reaches it through `command`/`eval`/`sh -c` (all of which can retarget
# or launder what that name runs), makes the exemption meaningless. When
# either shape is present ANYWHERE in the command text, the exemption is
# withheld for the whole command -- it is refused with the redirect line
# rather than trusted.
_WRAPPER_SHADOW_RE = re.compile(
    r"(?i)\bbpsai-pair(?:\.exe)?\s*(?:\(\s*\)|=)"  # bpsai-pair() {..} / [alias] bpsai-pair=...
    # function bpsai-pair {..} / PowerShell Set-Alias|New-Alias [-Name] bpsai-pair ...
    r"|\b(?:function\s+|(?:set|new)-alias\b[^;&|\n]*\b)bpsai-pair(?:\.exe)?\b"
)
_WRAPPER_OPAQUE_INVOKER_RE = re.compile(r"\bcommand\b|\beval\b|\bsh\s+-c\b")

_CONTROL_OPERATOR_CHAR_RE = re.compile(r"[;&|\n]")


def _segment_start(command: str, index: int) -> int:
    """Start of the segment containing *index*: just past the last control
    operator (`;`/`&`/`|`/newline) before it, or 0 if none. Used to bound
    the opaque-invoker prefix check to the current segment only."""
    start = 0
    for match in _CONTROL_OPERATOR_CHAR_RE.finditer(command, 0, index):
        start = match.end()
    return start


_READ_ONLY_EXEMPT_COMMANDS = frozenset({"grep", "rg", "cat", "less", "echo", "printf"})
_CODE_FLAGS = frozenset({"-c", "-e", "-r", "--eval"})


def _raw_text_hit(command: str) -> bool:
    if IN_SCOPE_ISSUE_PR_RE.search(command):
        return True
    if IN_SCOPE_API_RE.search(command) and API_WRITE_MARKER_RE.search(command):
        return True
    if IN_SCOPE_GRAPHQL_RE.search(command) and GRAPHQL_MUTATION_RE.search(command):
        return True
    return False


def _direct_api_hit(command: str) -> bool:
    return bool(
        _API_HOST_RE.search(command)
        and _API_WRITE_PATH_RE.search(command)
        and _API_TOOL_RE.search(command)
        and _DIRECT_API_WRITE_MARKER_RE.search(command)
    )


def _blank_gh_token(match: re.Match) -> str:
    return match.group(0)[: -len("gh")] + "__"


def _strip_trusted_wrapper_invocations(command: str) -> str:
    """Blank out each `bpsai-pair gh ...` invocation's `gh` token (and only
    that token) so the raw-text scope check below never mistakes the
    wrapper's OWN arguments for a bare `gh` filing call -- but only for
    invocations that are actually trustworthy.

    A shell-function/alias shadow of `bpsai-pair` (see `_WRAPPER_SHADOW_RE`)
    taints the whole command, same as before. An opaque invoker
    (`command`/`eval`/`sh -c`) is scoped per-invocation to the INVOCATION
    PREFIX -- the text between the previous control operator (or the start
    of the command) and this specific match -- never the whole command
    text. Searching the whole command let ordinary body/title text like
    `-b "this command fails"` disable the exemption for a perfectly
    sanctioned `bpsai-pair gh issue create ...` call; scoping to the prefix
    means only an invocation actually reached through `command`/`eval`/
    `sh -c` is refused."""
    if _WRAPPER_SHADOW_RE.search(command):
        return command

    def _replace(match: re.Match) -> str:
        prefix = command[_segment_start(command, match.start()) : match.start()]
        if _WRAPPER_OPAQUE_INVOKER_RE.search(prefix):
            return match.group(0)
        return _blank_gh_token(match)

    return _WRAPPER_INVOCATION_RE.sub(_replace, command)


def _tokenize(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"
    try:
        return list(lexer)
    except ValueError:
        return command.split()


def _segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in CONTROL_OPERATOR_TOKENS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def _is_read_only_carrier(rest: list[str]) -> bool:
    if not rest:
        return False
    if _SUBSTITUTION_RE.search(" ".join(rest)):
        return False
    word = PurePosixPath(rest[0]).name.lower()
    if word == "git":
        if len(rest) >= 2 and rest[1] == "log":
            return True
        return len(rest) >= 3 and rest[1] == "commit" and "-m" in rest[2:]
    if word in _READ_ONLY_EXEMPT_COMMANDS:
        return not any(token in _CODE_FLAGS for token in rest[1:])
    return False


def is_in_scope(command: str) -> bool:
    """True when a `gh` filing phrase or a direct-API write is present
    ANYWHERE in *command* and is not entirely carried by a read-only
    exempt command's quoted argument -- the one narrow exception below."""
    if _direct_api_hit(command) and not _is_read_only_carrier(_tokenize(command)):
        return True
    scoped = _strip_trusted_wrapper_invocations(command)
    for segment in _segments(_tokenize(scoped)):
        text = " ".join(segment)
        if not (_raw_text_hit(text) or _direct_api_hit(text)):
            continue
        if _is_read_only_carrier(segment):
            continue
        return True
    for match in _SUBSTITUTION_RE.finditer(scoped):
        inner = match.group("paren")
        if inner is None:
            inner = match.group("tick") or ""
        if _raw_text_hit(inner) or _direct_api_hit(inner):
            return True
    return False


# A leading `NAME=value` token, POSIX env-assignment grammar -- mirrors
# `base_branch_guard_constants.ENV_ASSIGNMENT_RE`, kept as a literal
# duplicate (not an import) per this hook's stdlib-only, no-sibling-module
# contract.
_ENV_ASSIGNMENT_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_BYPASS_TOKEN_RE = re.compile(r"^" + re.escape(BYPASS_ENV_NAME) + r"=(.*)$", re.DOTALL)


def _leading_bypass_reason(segment: list[str]) -> str | None:
    """The bypass marker's value when it is one of *segment*'s LEADING
    env-assignment tokens, else None. Scoped to a single segment (mirroring
    `base_branch_guard`'s own env-assignment-prefix precedent) so the
    marker must sit immediately ahead of the command it is meant to excuse
    -- not merely appear anywhere in a multi-segment command line."""
    reason = None
    for token in segment:
        if not _ENV_ASSIGNMENT_TOKEN_RE.match(token):
            break
        match = _BYPASS_TOKEN_RE.match(token)
        if match:
            reason = match.group(1)
    # An EMPTY (or whitespace-only) reason is not a reason: the marker's
    # whole value is the audited justification, and `REASON="" gh ...`
    # must be treated as no marker at all rather than an auditable grant
    # with a blank ledger row.
    if reason is not None and not reason.strip():
        return None
    return reason


def _bypass_reason_for_command(command: str) -> str | None:
    """The bypass reason from the marker prefix on whichever segment is
    actually the in-scope filing/API call, else None. Reuses the exact
    same segment/read-only-carrier logic `is_in_scope` uses, so a reason
    can never be picked up from a segment that would not itself have
    triggered a refusal (e.g. an unrelated later command, or a read-only
    carrier quoting the marker as inert text)."""
    scoped = _strip_trusted_wrapper_invocations(command)
    for segment in _segments(_tokenize(scoped)):
        text = " ".join(segment)
        if not (_raw_text_hit(text) or _direct_api_hit(text)):
            continue
        if _is_read_only_carrier(segment):
            continue
        reason = _leading_bypass_reason(segment)
        if reason is not None:
            return reason
    return None


def main() -> int:
    raw_stdin = ""
    try:
        raw_stdin = sys.stdin.read()
        event = json.loads(raw_stdin or "{}")
        if not isinstance(event, dict) or event.get("tool_name") != "Bash":
            return 0
        tool_input = event.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str) or not command.strip():
            return 0
        in_scope = is_in_scope(command)
        if in_scope:
            # Inside the try deliberately: an exception anywhere in the
            # bypass path (a broken probe, a bad ledger write) must still
            # hit the fail-closed branch below rather than silently
            # skipping the bypass and falling through to an ordinary deny
            # that never happened.
            bypass_reason = _bypass_reason_for_command(command)
            if bypass_reason is not None and _wrapper_genuinely_unavailable(
                command, _WRAPPER_SHADOW_RE
            ):
                cwd = str(event.get("cwd") or os.getcwd())
                if _grant_bypass(cwd, bypass_reason):
                    return 0
    except Exception as exc:
        # Failing open unconditionally here meant any bug in this hook's
        # own detection logic silently let a raw filing command straight
        # through with zero PII/audience/label enforcement. Fail CLOSED
        # whenever the raw (unparsed) input text still looks filing-shaped
        # -- an internal error is never a reason to risk an unscanned
        # filing -- and fail open only when nothing here even looks like a
        # filing command, so an unrelated Bash call is never bricked over
        # an unrelated bug.
        if _FILING_SHAPED_RE.search(raw_stdin):
            sys.stderr.write(
                f"{DENY_PREFIX}issue_filing_gate: fail-closed on internal "
                f"error ({exc}) with filing-shaped text present -- refusing "
                "rather than risk an unscanned filing\n"
            )
            return 2
        sys.stderr.write(f"issue_filing_gate: fail-open ({exc})\n")
        return 0

    if not in_scope:
        return 0
    sys.stderr.write(f"{DENY_PREFIX}issue_filing_gate: {REDIRECT_MESSAGE}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
