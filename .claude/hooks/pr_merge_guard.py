#!/usr/bin/env python3
"""PreToolUse hook: fail-closed `gh pr merge` guard.

Intercepts a Bash tool call that is shaped like `gh pr merge`, asks the
shipped read-only predicate whether the PR for the current branch may be
merged, and denies (exit 2, the predicate's own output on stderr) when it
may not -- head reviewed-with-unanswered, the freshness floor tripped, or
any of the predicate's own fail-closed states.

Runtime contract: stdlib only, plain ``python3``, no ``bpsai_pair`` import
-- this runs on operator machines with no PairCoder venv (the same
contract ``base_branch_guard.py`` states). The verdict logic is therefore
NOT reimplemented here: this hook shells out to the installed CLI
(``bpsai-pair pr merge-guard-check --json``) and relays what it says. That
binary is the same remedy every hook in this payload already prescribes
(`bpsai-pair upgrade`), so requiring it on PATH adds no new dependency --
and a re-derived copy of the review-thread logic would be exactly the
drift this guard exists to prevent.

COST, STATED PLAINLY: this is a SECOND `python3` interpreter spawned on
EVERY `Bash` tool call (`base_branch_guard.py`'s own Bash entry already
pays the first), for the overwhelmingly common case that the command is
not `gh pr merge` at all. Accepted as the price of the independent-disarm
design (each guard's own opt-out marker/config is unrelated to the
other's) rather than folding this into `base_branch_guard.py` itself.

Two-sided failure posture (deliberately opposite on each side):
  * fail OPEN on THIS HOOK'S OWN malformed input -- an unparseable event or
    a non-dict payload is our bug or a garbled caller, and bricking every
    Bash call over it is the wrong trade. Same reasoning as
    ``base_branch_guard.py``'s ``except Exception: return 0`` branch, and
    the same precedent.
  * fail CLOSED on ANY failure to obtain a clean verdict -- the binary
    missing, an untrusted resolution of it, a timeout, an unexpected
    return code, unparseable stdout, or stdout with no ``allow`` key. "The
    predicate could not run" must never read as "the merge is fine"; that
    is the whole point of the gate.

LOOSENING IS ONLY THE DATED DIAL: this hook has no env kill switch and no
opt-out marker. The single way past a block **for a call this hook
actually classifies as `gh pr merge`** is
``bpsai-pair config set-gate merge_gate warn --reason "..." --until
<date>``, which the CLI resolves (and audits to
``.paircoder/history/bypass_log.jsonl``) on the other side of the call
below -- so a bypass is dated, reasoned, audited, and EXPIRING, and this
hook only ever relays the answer. ``BPSAI_PAIR_BIN`` (see
``_resolve_predicate_binary``) is NOT an exception to this -- it only
NAMES which binary answers, it never skips asking one.

PREDICATE RESOLUTION DEFAULTS TO PATH, THE SAME AS BEFORE THIS FEATURE
EXISTED: a bare `shutil.which("bpsai-pair")` trusts whatever venv happens
to be FIRST on the invoking process's ambient PATH -- not just a hostile
one, but, in ordinary multi-repo/orchestrator practice, an unrelated and
possibly OLDER checkout's venv (observed in practice: a merge on one repo
was refused because PATH resolved a different, stale `bpsai-pair` lacking
the `pr merge-guard-check` subcommand this hook depends on). Resolution
checks, in order: an explicit ``BPSAI_PAIR_BIN`` override; ONLY IF
``BPSAI_MERGE_GUARD_PREFER_REPO_TREE=1`` (an operator opt-in, OFF by
default), the event's OWN repo tree (`<cwd>/.venv` or `<cwd>/venv`); and
otherwise/finally a bare PATH search. The repo-tree step is opt-in, not
the default, because trusting it trades one risk for another -- see
`_resolve_predicate_binary`'s own docstring, and `PREFER_REPO_TREE_ENV`'s,
for why an UNTRACKED file at that path is not automatically safe just
because it isn't the PR author's own committed diff.

NOT-EVALUABLE IS A THIRD OUTCOME, DISTINCT FROM BOTH A VERDICT AND A CRASH:
a resolved `bpsai-pair` that predates the `pr merge-guard-check`
subcommand exits with Click/Typer's usage-error code rather than a 0/1
verdict. Since the same release gap means that binary's OWN `merge_gate`
dial support is unreleased too, this hook cannot ask the CLI to resolve
its own dial for this one case -- `_read_merge_gate_loosening` reads the
SAME `architecture.gate_modes.merge_gate` entry directly, stdlib-only, and
allows (with an explicit "not-evaluable" advisory, never silent) only
while a live, unexpired `warn` entry exists. Absent that entry, or any
OTHER failure shape (missing binary, crash, timeout, bad JSON), the
original unconditional deny still applies -- not-evaluable is a narrower
carve-out, not a general softening of the fail-closed posture above.

SCOPE, STATED PLAINLY (security-audit follow-up): this hook mediates one
CLI shape on one tool (`Bash` calls that look like the `gh` binary's `pr
merge` subcommand). It is not a mediator of the GitHub merge API itself --
a script or MCP tool that calls the REST/GraphQL merge endpoint directly
(`curl`, PyGithub, the web UI, ...) never passes through here at all. Real
closure of that gap is a server-side control (required status checks /
branch protection on the base branch), not something a client-side
Bash-string classifier can ever provide -- treat this hook as a
convenience guardrail for the common path, not a substitute for that.

THE LOOSENING DIAL ITSELF LIVES IN TRACKED, IN-REPO CONFIG (`merge_gate` in
`.paircoder/config.yaml`, resolved by `core/gate_dial.resolve_merge_gate`)
-- the SAME storage every other `architecture.gate_modes` gate already
uses. That means an author with push access to their own branch can, in
principle, add a `merge_gate: {mode: warn, ...}` entry to that same PR and
self-serve past the block; the compensating controls are the audited
`bypass_log.jsonl` row (visible to `bpsai-pair audit bypasses`) and the
`--reason`/`--until` requirement, not a barrier to editing the file at
all. Real closure would mean moving the dial outside the checkout the
guard protects, a change to the shared gate-dial architecture (not this
hook alone) and out of scope here.

`--auto` EVALUATES NOW, BUT GITHUB MERGES LATER: `gh pr merge --auto`
classifies as an ordinary, checkable merge (`MERGE_SHAPE_DEFAULT`), and
the predicate's verdict is accurate for the INSTANT it runs. But
"unanswered reviewer threads" is this guard's own concept, not a GitHub
required status check -- so a `--auto` merge GitHub defers until checks
pass (minutes or hours later) is never re-evaluated against a thread
posted in that gap. The 120-second freshness floor only protects the
evaluation instant against activity that JUST happened; it cannot reach
forward into an arbitrarily-delayed future merge event this hook is never
invoked for a second time to see. Treat a clean verdict on a `--auto`
merge as "clean right now", not as a guarantee that holds until GitHub
actually lands it.

CLASSIFIER ROBUSTNESS, AND ITS ACKNOWLEDGED LIMIT: `merge_shape` (the
richer classification `is_merge_command` is a thin bool wrapper around)
tokenizes with `shlex` (quote-aware, so `eval "gh pr merge 42"` no longer
reads as the literal token `"gh`), resolves the `gh` token by path/case
(`/usr/bin/GH.exe` counts), expands a same-command `alias g=gh`, and
recurses into `eval`/`sh -c`-family arguments, backtick and `$()`
substitutions, and assignment right-hand sides (so `CMD="gh pr merge 42"`
is itself treated as suspicious, even before any later `$CMD`). Text the
tokenizer cannot parse at all (unbalanced quoting, or bash idiom `shlex`'s
posix mode doesn't model) falls back to a coarser, quote-blind raw-text
scan that is deliberately MORE likely to match, never less -- an
unparseable command is exactly the ambiguous case that must deny outright,
not get a free pass. Recursion into nested command text is depth-bounded;
running out of that budget also denies outright, not "not a merge".

NOT EVERY MATCH IS SAFE TO RELAY A VERDICT FOR: the predicate always
evaluates the CURRENT branch's PR in the event's UNMODIFIED cwd
(`find_pr_for_branch`) -- so a merge-shaped segment only gets a real
predicate check when it is the command's sole/first segment, names no
explicit `[<number> | <url> | <branch>]` target, and no `--repo`/`-R`.
Anything else (`gh pr merge 42`, `git switch other && gh pr merge`,
`gh pr merge && gh pr merge 42`) denies outright -- an "allow" for the
CURRENT branch's PR would otherwise get silently relayed as cover for a
merge of a DIFFERENT PR, or one running in a DIFFERENT branch/repo context
by the time it actually executes. See `merge_shape`'s own docstring for
the exhaustive per-segment/per-nested-body walk this requires (a
first-match-wins scan would miss a second, unevaluated merge later in the
same compound command).

A SINGLE, UN-CHAINED SEGMENT CAN STILL REDIRECT THE CWD `gh` RUNS IN
(customer-fleet finding, closed by `_segment_gh_not_first_executable`):
the segment-position check above only ever asked whether a merge-shaped
segment was the command's FIRST segment, split on `;`/`&&`/`|`/`&`. `env
-C <dir> gh pr merge --squash --delete-branch` (or `env --chdir=<dir>
...`) is a single segment with no such separator and no explicit target,
so it used to pass both that check and `_segment_merge_target_ambiguous`
untouched -- yet `env -C`/`--chdir` changes the CHILD's (i.e. `gh`'s)
working directory, so the predicate answers about the branch checked out
in the hook's own event cwd while the merge itself runs against whatever
repo sits at `<dir>`. Any wrapper ahead of `gh` in the same segment
(`env -C`/`--chdir`, `pushd`, `chdir`, `sh -c`/`bash -c`,
`nohup`/`setsid`/`timeout` wrapping any of those) is refused the same
way `cd`/`git switch` already are -- `gh` must be the first EXECUTABLE
token in the segment, a bare `NAME=value` env-assignment prefix
excepted.

What this still CANNOT see: a variable assigned in one Bash tool call and
only invoked in a SEPARATE one -- this hook is handed one command string
per call, never the session's shell variable state. No regex/token
classifier can be made complete against arbitrary shell obfuscation; the
scope note above is the honest ceiling on what this layer buys.

DEGRADED MODE DOES NOT CLASSIFY -- RULING (Computer Prime, review round 5,
final): when a sibling module fails to import, `_degraded_classify` denies
EVERY command (exit 2), naming the failed sibling and the repair. Earlier
rounds tried to keep a narrower, sibling-free deny alive inside degraded
mode itself (first a two-shape regex, then a broader `gh api`/`gh
pr`-any-position regex) so an unrelated command would not be blocked by a
broken payload -- but ANY argv-only scan run without the tokenizer/
alias-resolution/quote-handling siblings that just failed to import is
exactly as bypassable as the thing it is standing in for (quote-splitting,
an alias, a nested `eval`/`sh -c` body, ...), so sharpening the regex
further only chases the next bypass. A broken payload is a STOP-THE-LINE
state, not a state safe to keep guessing a classification inside -- the
round-2 usability concern (bricking the session's Bash calls until
repaired) is the accepted trade, offset by the deny message always naming
the immediate remedy (`bpsai-pair upgrade --auto` / re-pin the payload).
"""

import sys

# A real hook invocation is a fresh `python3 <this file>.py` subprocess in a
# consumer's checkout; writing `__pycache__/*.pyc` would leave untracked
# build artifacts in the payload tree, dirtying `git status` in every
# managed repo forever. Set BEFORE any further import (including the
# sibling ones below).
sys.dont_write_bytecode = True

import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

#: `pr_merge_guard_trust.py`/`_predicate.py`/`_gate.py`/`_parse.py`/
#: `_http.py` -- split out of this module for the architecture
#: file/function-count caps (predicate binary resolution+trust, the
#: not-evaluable dated-loosening reader, the shell-ish tokenizing
#: primitives, and the HTTP-client/direct-API merge-endpoint primitives,
#: respectively). Loaded
#: from THIS file's own directory, exactly like `base_branch_guard.py`'s
#: sibling-module split, so a real hook invocation (a fresh `python3 <this
#: file>.py` subprocess in a consumer's checkout) finds them regardless of
#: the process's own cwd.
#:
#: `_parse` is imported first only because `main`'s ordinary (non-degraded)
#: path needs its names unconditionally, not because import order buys any
#: correctness here: `main` checks `_SIBLING_IMPORT_ERROR` itself -- right
#: after reading stdin, before touching any of these names -- and on a
#: failure here runs `_degraded_classify` instead, an inline fallback in
#: THIS file that needs no sibling at all. So a partial-sync payload (a
#: CLASSIFICATION sibling missing/broken) never reaches a `NameError`; it
#: takes the degraded branch instead. `_trust.py` is NOT one of the
#: imports this `try` catches (review round 6, P1) -- it is a dependency
#: of `_predicate.py` alone, imported there with its own narrower
#: fallback, so a broken `_trust.py` alone denies only merge-shaped
#: commands (untrusted predicate binary) rather than tripping this
#: deny-every-command branch for unrelated Bash calls too.
sys.path.insert(0, str(Path(__file__).resolve().parent))
_SIBLING_IMPORT_ERROR = None
try:
    from pr_merge_guard_parse import (
        _MAX_RECURSION_DEPTH,
        _FALLBACK_MERGE_RE,
        _alias_table,
        _first_executable_index,
        _is_gh_spelling,
        _nested_command_texts,
        _resolve_aliases,
        _segments,
        _tokenize,
    )
    from pr_merge_guard_gate import (
        NOT_EVALUABLE_WARN_TEMPLATE,
        _read_merge_gate_loosening,
        _write_not_evaluable_bypass_row,
        not_evaluable_fail_closed_message,
    )
    from pr_merge_guard_predicate import (
        FAIL_CLOSED_TEMPLATE,
        PREDICATE_ARGV,
        ask_predicate,
    )
    from pr_merge_guard_http import (
        gh_first_segment_is_api_merge,
        http_client_segment_is_api_merge,
    )
    # `pr_merge_guard_trust` is deliberately NOT imported here (review
    # round 6, P1): it answers "is this resolved predicate binary safe to
    # run", not a classification question, and `pr_merge_guard_predicate`
    # already imports it directly (with its own fallback when trust is
    # unavailable -- see that module). Importing it a second time here,
    # for no name this module uses, meant a broken `_trust` sibling alone
    # tripped `_SIBLING_IMPORT_ERROR` and denied EVERY Bash command, not
    # just merge-shaped ones -- the full classifier (`_parse`/`_gate`/
    # `_predicate`) was available the whole time.
except Exception as _sibling_import_error:  # noqa: E402
    # A broken/missing sibling can no longer reach the full tokenizer-backed
    # classifier, so `main` falls back to `_degraded_classify` -- an inline,
    # sibling-free deny of EVERY command (not a classification guess; see
    # `_degraded_classify`'s own docstring for the round-5 ruling, and the
    # module docstring's "Two-sided failure posture").
    # Captured into a
    # plain string HERE, not read from `_sibling_import_error` inside
    # `main()`: Python deletes an `except ... as name` binding when the
    # except block exits, and `main()` is not actually CALLED until
    # `__main__` much later -- referencing it there would raise `NameError`.
    _SIBLING_IMPORT_ERROR = str(_sibling_import_error)

#: Tokens, in order, that make a segment a merge call once resolved
#: (`gh` matched by path/case via `_is_gh_spelling`, the rest lowercased).
MERGE_TOKENS = ("gh", "pr", "merge")

DENY_PREFIX = "BLOCKED BY ENFORCEMENT GATE\n\n"

#: Security-review follow-up: the not-evaluable carve-out reads its
#: loosening from the SAME working tree/branch the merge is being
#: evaluated for -- an author with push access can commit a
#: `merge_gate: warn` entry to their own branch (the SAME accepted
#: tradeoff the primary warn path already lives with), and this carve-out
#: makes that tradeoff reachable through a SECOND, environmentally-
#: triggered path (a stale/PATH-shadowed predicate) that requires no
#: deliberate operator action at all -- "meaningfully expands the bypass
#: surface" versus the pre-existing behavior, where an unreachable
#: predicate always failed closed with no allow path whatsoever. Gating
#: the ENTIRE not-evaluable carve-out behind this environment variable --
#: an operator-controlled signal, never PR-branch content, same trust
#: boundary as `BPSAI_PAIR_BIN` -- keeps today's behavior (unconditional
#: deny) as the true default: absent this opt-in, a not-evaluable
#: predicate is exactly as fail-closed as it always was, and the softer
#: path exists only for an operator/fleet that has explicitly turned it on
#: (e.g. one that already knows it runs a mixed-version predicate fleet).
NOT_EVALUABLE_OPT_IN_ENV = "BPSAI_MERGE_GUARD_ALLOW_NOT_EVALUABLE"


def _segment_is_merge_shaped(segment: list) -> bool:
    """True when *segment*'s tokens contain `gh`, then `pr`, then `merge`
    as an ordered subsequence (flags and arguments may sit anywhere else
    in the segment, including between the three)."""
    remaining = list(MERGE_TOKENS)
    for token in segment:
        if not remaining:
            break
        want = remaining[0]
        matched = _is_gh_spelling(token) if want == "gh" else token.lower() == want
        if matched:
            remaining.pop(0)
    return not remaining


#: `gh pr merge`'s own value-taking flags (`gh pr merge --help`, INHERITED
#: FLAGS excluded -- `--repo`/`-R` is handled separately, below, since it
#: changes what "ambiguous" means rather than merely consuming a value).
#: Needed so a flag's VALUE is never mistaken for the positional
#: `[<number> | <url> | <branch>]` target argument.
_MERGE_VALUE_FLAGS = frozenset(
    {
        "-A",
        "--author-email",
        "-b",
        "--body",
        "-F",
        "--body-file",
        "--match-head-commit",
        "-t",
        "--subject",
    }
)

_REPO_FLAGS = frozenset({"-R", "--repo"})

MERGE_SHAPE_NONE = "none"
MERGE_SHAPE_DEFAULT = "default"
MERGE_SHAPE_AMBIGUOUS = "ambiguous"


def _segment_merge_target_ambiguous(segment: list) -> bool:
    """True when a `gh ... pr merge` segment names `--repo`/`-R` (in ANY
    position -- an inherited `gh` flag isn't bound to appearing after the
    subcommand: `gh --repo owner/name pr merge 42` is valid) or a bare
    positional `[<number> | <url> | <branch>]` argument to `merge` itself.

    gh's own default with NEITHER present is "the pull request that
    belongs to the current branch" -- exactly what this hook already
    evaluates via `find_pr_for_branch`. Anything else names a PR (or a
    repo) this hook has no way to verify is the SAME one whose readiness
    it is about to relay as a verdict: `gh pr merge 42` on a clean current
    branch must never read as "PR 42 is clean" when PR 42 is a completely
    unrelated, possibly-blocked PR.
    """
    seen_merge = False
    skip_next = False
    for token in segment:
        if skip_next:
            skip_next = False
            continue
        if (
            token in _REPO_FLAGS
            or token.startswith("--repo=")
            or (token.startswith("-R") and token != "-R")
        ):
            return True
        if token.lower() == "merge":
            seen_merge = True
            continue
        if not seen_merge:
            continue
        if token in _MERGE_VALUE_FLAGS or any(
            token == flag or token.startswith(flag + "=") for flag in _MERGE_VALUE_FLAGS
        ):
            skip_next = "=" not in token
            continue
        if token.startswith("-"):
            continue
        return True
    return False


def _segment_gh_not_first_executable(segment: list, command: str) -> bool:
    """True when *segment* invokes `gh` only from BEHIND another
    executable -- `env -C <dir> gh pr merge`, `env --chdir=<dir> gh pr
    merge`, `pushd <dir> gh pr merge`, `chdir <dir> gh pr merge`, `sh -c`/
    `bash -c gh pr merge`, or any of those further wrapped in `nohup`/
    `setsid`/`timeout`.

    CUSTOMER-FLEET FINDING this closes: `env -C <dir> gh pr merge
    --squash --delete-branch` (or `env --chdir=<dir> ...`) is a SINGLE
    segment (no `;`/`&&`/`|`/`&`) naming no explicit `[<number> | <url> |
    <branch>]` target or `--repo` -- so it passed both the
    segment-position check (`index != 0`, below) and
    `_segment_merge_target_ambiguous` untouched, and used to classify
    `MERGE_SHAPE_DEFAULT`. But `env -C`/`--chdir` changes the CHILD
    process's (i.e. `gh`'s) working directory -- the predicate is asked
    about the PR for the branch checked out in the hook's own event cwd,
    while the merge itself then runs against whatever repo/branch sits at
    `<dir>`, unseen by this hook's `event["cwd"]` read. Exactly the same
    "the merge actually runs in a different context than the one just
    evaluated" risk `index != 0` already denies for a leading `cd`/`git
    switch`, just expressed at the single-segment argv level instead of a
    `&&` chain.

    The general rule catches every wrapper shape in one strike rather
    than enumerating each: `gh` must be the first EXECUTABLE token in the
    segment (`_first_executable_index`), skipping only a bare
    `NAME=value` shell-assignment prefix -- `FOO=bar gh pr merge` runs
    `gh` directly, in the unmodified cwd, so it alone is exempt. *command*
    is the raw, pre-tokenization command text, walked in lockstep against
    *segment* so a single-quoted or backslash-escaped substitution marker
    in an assignment's value reads as the literal it is (see
    `_first_executable_index`'s own docstring).

    The SAME assignment-prefix exemption, via the SAME `_first_executable_
    index` helper, applies on the direct-API backstop too
    (`_segment_is_gh_api_merge`, the gh-api backstop prefix-blindness fix,
    2026-09-16) -- a bare `NAME=value` prefix
    ahead of `gh api .../merge` still refuses, but any other wrapper in
    that position used to read the wrapper itself as `segment[0]`,
    concluded it wasn't a `gh` spelling, and silently allowed. Both
    call sites resolve `gh` through this one helper so neither can drift
    out of sync with the other again."""
    index = _first_executable_index(segment, command)
    return not (index < len(segment) and _is_gh_spelling(segment[index]))


def merge_shape(command: str, _depth: int = 0) -> str:
    """Classify *command* -- or anything it would hand to a nested shell
    -- as one of the `MERGE_SHAPE_*` constants:

    * `MERGE_SHAPE_NONE` -- not `gh pr merge`-shaped anywhere.
    * `MERGE_SHAPE_DEFAULT` -- a plain `gh pr merge` naming no explicit
      target/repo, standing ALONE as the command's first (and only
      merge-shaped) segment, so gh's own default (the current branch's PR,
      evaluated in the event's own unmodified cwd/branch) is exactly what
      this hook's predicate call evaluates. Safe to ask.
    * `MERGE_SHAPE_AMBIGUOUS` -- names an explicit target/repo this hook
      cannot verify is the SAME PR (see `_segment_merge_target_ambiguous`),
      OR is not the command's sole/first segment (`git switch other &&
      gh pr merge` executes the merge in a DIFFERENT branch context than
      the one just evaluated; `gh pr merge && gh pr merge 42` has a SECOND
      merge -- possibly of a different PR entirely -- that a first-segment-
      only check would silently never inspect), OR the text was too
      obfuscated/deeply-nested to classify with confidence at all. Never
      safe to ask the predicate and relay "allow" -- the predicate would
      be answering about the WRONG PR, or about a context the merge no
      longer runs in by the time it executes.

    EVERY segment and EVERY nested body is inspected before returning
    (never short-circuits on the first merge-shaped hit, except to return
    AMBIGUOUS the moment one is found -- the worst-case answer is safe to
    return immediately, but DEFAULT is only safe once nothing WORSE was
    found anywhere else in the command).

    See the module docstring's "CLASSIFIER ROBUSTNESS" section for the
    tokenizer's own coverage and acknowledged limits.
    """
    if _depth > _MAX_RECURSION_DEPTH:
        # Fail CLOSED: a command nesting eval/sh -c/backtick this deep is
        # already far outside anything ordinary, and "ran out of
        # recursion budget" must never relay an "allow" for the wrong PR.
        return MERGE_SHAPE_AMBIGUOUS
    tokens = _tokenize(command)
    if tokens is None:
        # Same reasoning: text `shlex` could not even tokenize is exactly
        # the case where this hook cannot trust its own target-ambiguity
        # read either, so a fallback match denies outright rather than
        # asking the predicate about (possibly) the wrong PR.
        if _FALLBACK_MERGE_RE.search(command):
            return MERGE_SHAPE_AMBIGUOUS
        return MERGE_SHAPE_NONE
    tokens = _resolve_aliases(tokens, _alias_table(tokens))
    overall = MERGE_SHAPE_NONE
    for index, segment in enumerate(_segments(tokens)):
        if not _segment_is_merge_shaped(segment):
            continue
        # A merge-shaped segment is only safe to relay a predicate verdict
        # for when it is the FIRST thing the command does: anything
        # earlier (a `cd`, a `git switch`/`checkout`, or simply another
        # command) could change the cwd/branch the merge actually runs in
        # -- a context this hook has no cheap way to re-derive, unlike
        # `base_branch_guard`'s dedicated cd/branch-tracking machinery.
        # (A segment AFTER the merge poses no such risk -- it can't change
        # a context the merge already consumed -- so only POSITION 0 is
        # exempt here, not "only one segment total".)
        if (
            index != 0
            or _segment_merge_target_ambiguous(segment)
            or _segment_gh_not_first_executable(segment, command)
        ):
            return MERGE_SHAPE_AMBIGUOUS
        overall = MERGE_SHAPE_DEFAULT
    for text in _nested_command_texts(tokens, command):
        shape = merge_shape(text, _depth + 1)
        if shape == MERGE_SHAPE_AMBIGUOUS:
            return MERGE_SHAPE_AMBIGUOUS
        if shape == MERGE_SHAPE_DEFAULT:
            overall = MERGE_SHAPE_DEFAULT
    return overall


def is_merge_command(command: str, _depth: int = 0) -> bool:
    """True when *command* is `gh pr merge`-shaped at all (default target
    OR ambiguous). Kept for callers that only need the yes/no answer --
    `merge_shape` above is the richer classification `main()` actually
    acts on."""
    return merge_shape(command, _depth) != MERGE_SHAPE_NONE


#: No admin bypass anywhere: `--admin` on `gh pr merge` bypasses GitHub's
#: own required status checks/reviews outright, and a direct `gh
#: api`/GraphQL merge call bypasses THIS guard's predicate entirely --
#: neither is a case the predicate is ever consulted about, and
#: neither can be loosened by the `merge_gate` dial (that dial loosens an
#: ordinary blocked VERDICT; these two shapes never reach a verdict at
#: all). Checked before `merge_shape`'s own NONE/DEFAULT/AMBIGUOUS
#: classification -- a `gh api .../merge` call names no `pr`/`merge` tokens
#: in the order `_segment_is_merge_shaped` requires, so it would otherwise
#: classify as `MERGE_SHAPE_NONE` and sail through unchecked.
REFUSED_MERGE_MESSAGE = (
    "This command requests `--admin` on a `gh pr merge`, or calls GitHub's "
    "merge API/mutation directly (`gh api .../merge`, a GraphQL "
    "`mergePullRequest`) -- both are refused unconditionally, with no "
    "`merge_gate` loosening able to allow them. `--admin` bypasses "
    "GitHub's own required status checks/reviews; a direct API/GraphQL "
    "call bypasses this guard's predicate entirely. Neither is ever a "
    "case this guard evaluates and allows.\n"
    "Remedy: merge without `--admin` via a plain `gh pr merge` once "
    "required checks are green, or ask an operator why an admin bypass "
    "seemed necessary."
)

#: `gh pr merge`'s admin-bypass flag -- a bare boolean, no value.
_ADMIN_FLAG = "--admin"


def _segment_requests_admin_bypass(segment: list) -> bool:
    """True when *segment* names `--admin` in ANY spelling -- the bare
    flag, or an `=`-joined boolean-assignment form (`--admin=true`,
    `--admin=false`). Every spelling is refused, including `=false`
    (review round 7): gh's own boolean-flag parsing for the value half is
    not something this argv-only scan can verify, so an explicit mention
    of the flag fails closed rather than trusting that a `false`-looking
    suffix is what actually reaches gh."""
    admin_eq = _ADMIN_FLAG + "="
    return any(token == _ADMIN_FLAG or token.startswith(admin_eq) for token in segment)


def _segment_is_gh_api_merge(segment: list, command: str) -> bool:
    """True when *segment* is a `gh api` call naming a REST merge endpoint
    (any argument ending in `/merge`), a GraphQL `mergePullRequest`
    mutation -- checked independently of `MERGE_TOKENS`, which requires the
    literal token `pr` and would never match either shape -- OR a call
    whose request body is file-/stdin-fed (`--input`, `-F key=@file`) and
    not provably a GET: the body's actual content (which could be either
    of the above) is invisible to this or any argv-only scan, so it is
    refused on the file-fed shape alone rather than trusted absent
    evidence.

    `gh` is located via `_first_executable_index(segment, command)` --
    the SAME helper `_segment_gh_not_first_executable` uses for the `gh pr
    merge` shape -- rather than trusting a raw `segment[0]`
    read: a bare `NAME=value` assignment prefix is skipped (`GH_HOST=x gh
    api .../merge` still classifies), but any OTHER wrapper ahead of `gh`
    in the same segment (`env -C`/`--chdir`, `pushd`, `chdir`, `sh -c`/
    `bash -c`, `nohup`/`setsid`/`timeout`, ...) used to read as
    `segment[0]` not being `gh` at all and silently return False --
    turning this backstop's UNCONDITIONAL refusal into a one-token bypass
    -- the gh-api backstop prefix-blindness fix (2026-09-16) closes this.
    Since a wrapper's presence means this scan can no longer
    trust its own resolved position for the subcommand/endpoint walk
    below, that shape fails CLOSED -- but only in the two cases where it
    can plausibly BE a merge call (review round 2 of the gh-api backstop
    prefix-blindness fix: the first cut refused ANY non-`gh`-first segment
    carrying a token ending in
    `/merge` -- `ls docs/merge`, `cat notes/merge`, `curl
    https://example.com/docs/merge`, ... -- none of which can call
    GitHub's merge API):

    1. A `gh` spelling (`_is_gh_spelling`) appears ANYWHERE in the
       segment from the resolved index onward -- the actual wrapper
       shape (`env -C`/`nohup`/`setsid`/`timeout`/... ahead of a real
       `gh api .../merge`). A wrapper ahead of `gh` is transparent: the
       segment is re-sliced at that `gh` token and re-evaluated from
       there with the FULL gh-first rules (GraphQL-mutation check,
       subcommand/endpoint resolution, the `/merge` +
       `mergepullrequest` token scan, and the file-fed-body +
       declares-GET backstop, all in `gh_first_segment_is_api_merge`) --
       not the narrower literal `/merge`-suffix/`mergepullrequest`-
       substring scan alone, which is blind to a file-/stdin-fed body
       (`nohup gh api graphql --input q.json`, `env -C /r gh api -X POST
       repos/o/r/pulls/1/reviews --input body.json`) the same way a
       direct, unwrapped `gh api` call would be (the gh-api backstop
       prefix-blindness fix, round 5, 2026-09-16).
    2. INDEPENDENTLY of (1), the ORIGINAL (unsliced) remainder of the
       segment from the resolved index onward is also checked for a
       non-`gh` HTTP client (`_is_http_client_spelling`: `curl`, `wget`,
       `http`/`https`, `xh`) naming a `repos/.../pulls/<n>/merge` path
       (`_PULLS_MERGE_PATH_RE`, which also matches a full
       `https://api.github.com/repos/.../pulls/<n>/merge` URL) or a
       `mergePullRequest` mutation token -- a genuine direct merge that
       bypasses `gh` (and every gh-level check) entirely
       (`http_client_segment_is_api_merge`). The narrower
       `_PULLS_MERGE_PATH_RE` is used here, not the loose
       `_segment_names_merge_endpoint` "ends with /merge" suffix check,
       because an HTTP client can legitimately hit any URL ending in
       `/merge` that ISN'T GitHub's merge endpoint (`curl
       https://example.com/docs/merge`).

    The two are OR'ed, fail closed (review round 6, P1): a stray `gh`
    spelling that is merely SOME OTHER argument's value -- `curl -A gh
    -X PUT .../pulls/1/merge` (a `gh` user-agent string), `curl -X PUT
    .../pulls/1/merge -o gh` (an output filename), `wget --method=PUT
    .../pulls/1/merge -O gh` -- used to make (1) find that stray `gh`
    token, re-slice there, and evaluate a one-token segment that can
    never look like `gh api ...`, returning False and SHORT-CIRCUITING
    (2) (which never ran at all): the token that is not the invoked
    program silently disarmed the HTTP-client backstop it has nothing to
    do with. Since (1) and (2) now both always run and are OR'ed, that
    same stray `gh` token still makes (1) return False, but (2) --
    evaluated over the UNSLICED remainder, so it still sees the `curl`/
    `wget` that (1)'s re-slice discarded -- still catches the real merge
    call. A genuine wrapper shape (`nohup gh api .../merge`) is
    unaffected: (1) finds the same `gh` and returns True either way, and
    (2)'s own scan (`nohup` is not an HTTP-client spelling) stays False, so
    the OR contributes nothing new there. `grep gh docs/merge` stays
    allowed the same way: re-sliced from that `gh` token, `docs/merge` no
    longer resolves to `gh`'s `api` subcommand, so (1) falls through to
    False, and (2) sees no HTTP-client spelling in `grep`/`gh`/
    `docs/merge` either, so both sides of the OR stay False.

    Every other non-`gh`-first segment (a path name, an unrelated
    command, an HTTP call to some other endpoint) falls through to
    False on both sides.

    Once `gh` is confirmed, `gh_first_segment_is_api_merge` looks up the
    `api` subcommand via `_gh_subcommand_index`, not a fixed `segment[1]`,
    so a `gh` global flag ahead of it (`gh -R o/r api ...`, `gh --repo o/r
    api ...`) still classifies. `_gh_subcommand_index` only recognizes
    `gh`'s KNOWN value-taking global flags (`_GH_GLOBAL_VALUE_FLAGS`) --
    that allowlist can only ever list upstream `gh`'s flags as of today,
    and will go stale the moment a new one ships, so when the computed
    position doesn't land on `api` this falls back to EITHER of two
    narrower signals rather than scanning the whole segment for a bare
    `api` token (review round 3, P2: a literal `api` token can be some
    UNRELATED argument's value, e.g. `gh pr create --title api`, and
    matching it anywhere over-blocks): the first token after the leading
    run of flag-shaped tokens is itself `api` (`_first_non_flag_index`,
    covering an unrecognized BOOLEAN global flag), OR an `api` token
    appears anywhere in the segment AND the segment also names a
    `repos/.../pulls/<n>/merge` path (`_PULLS_MERGE_PATH_RE`, covering an
    unrecognized VALUE-taking global flag, whose value shifts even that
    first-non-flag guess off of `api`) -- in that second case the
    merge-path token itself is the corroborating evidence, not the bare
    `api` token alone. A THIRD fallback (`_segment_is_gh_graphql_merge_mutation`,
    review round 4, P2) corroborates the same unrecognized-value-taking-flag
    shape via a GraphQL merge mutation name instead of a REST path, since a
    GraphQL body carries no `pulls/<n>/merge` path for
    `_PULLS_MERGE_PATH_RE` to match against.

    The HTTP-client allowlist (`_HTTP_CLIENT_EXECUTABLES`: `curl`, `wget`,
    `http`/`https`, `xh`) is a stated, argv-level limit: a language
    runtime or any other client calling the endpoint directly is out of
    scope by design, not a gap to close by growing the list."""
    if not segment:
        return False
    index = _first_executable_index(segment, command)
    if index >= len(segment):
        return False
    if _is_gh_spelling(segment[index]):
        return gh_first_segment_is_api_merge(segment[index:])
    http_client_verdict = http_client_segment_is_api_merge(segment[index:])
    gh_slice_verdict = any(
        _is_gh_spelling(segment[position])
        and gh_first_segment_is_api_merge(segment[position:])
        for position in range(index, len(segment))
    )
    return gh_slice_verdict or http_client_verdict


def command_requests_refused_merge(command: str, _depth: int = 0) -> bool:
    """True when *command* -- or anything nested inside it (eval/`sh -c`/
    assignment/backtick/`$()` bodies, via the same `_nested_command_texts`
    walk `merge_shape` uses) -- requests an admin-bypass merge or calls
    GitHub's merge API/mutation directly. See `REFUSED_MERGE_MESSAGE`."""
    if _depth > _MAX_RECURSION_DEPTH:
        return True
    tokens = _tokenize(command)
    if tokens is None:
        lowered = command.lower()
        if "gh" not in lowered:
            return False
        if _ADMIN_FLAG in command and _FALLBACK_MERGE_RE.search(command):
            return True
        return "mergepullrequest" in lowered or bool(
            re.search(r"\bapi\b.*?/merge\b", lowered, re.DOTALL),
        )
    tokens = _resolve_aliases(tokens, _alias_table(tokens))
    for segment in _segments(tokens):
        if _segment_is_gh_api_merge(segment, command):
            return True
        merge_shaped = _segment_is_merge_shaped(segment)
        admin_bypass = _segment_requests_admin_bypass(segment)
        if merge_shaped and admin_bypass:
            return True
    for text in _nested_command_texts(tokens, command):
        if command_requests_refused_merge(text, _depth + 1):
            return True
    return False


AMBIGUOUS_TARGET_MESSAGE = (
    "This `gh pr merge` either names an explicit target/`--repo` this "
    "guard cannot verify is the SAME pull request its predicate would "
    "evaluate, is not the command's first action (something earlier -- a "
    "`cd`, a `git switch`/`checkout`, another `gh pr merge` -- could "
    "change the branch/repo context the merge actually runs in by the "
    "time it executes), or reaches `gh` only from behind another "
    "executable already in the SAME segment (`env -C`/`--chdir`, "
    "`pushd`, `chdir`, `sh -c`/`bash -c`, `nohup`/`setsid`/`timeout`, or "
    "any other wrapper -- each can redirect the working directory `gh` "
    "actually runs in without this hook's own cwd read ever seeing it; a "
    "bare `NAME=value` env-assignment prefix, e.g. `FOO=bar gh pr "
    "merge`, is the one exception and stays allowed). The only shape "
    "this guard can safely check is a plain `gh pr merge` -- no "
    "arguments, `gh` itself the first executable in the segment, "
    "standing ALONE as the only thing the command does -- gh's own "
    "default target then matches exactly the unmodified context the "
    "predicate resolves.\n"
    "Remedy: check the specific PR first with `bpsai-pair pr "
    "merge-guard-check --pr <N>`, then run a standalone `gh pr merge` "
    "from a branch already checked out to that PR, with no wrapper "
    "command ahead of `gh`."
)


def merge_command(event: dict) -> tuple:
    """``(command, shape)`` for *event* -- ``shape`` is one of the
    ``MERGE_SHAPE_*`` constants; ``command`` is ``""`` (shape always
    ``MERGE_SHAPE_NONE``) when the call isn't a non-empty Bash command."""
    if event.get("tool_name") != "Bash":
        return "", MERGE_SHAPE_NONE
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return "", MERGE_SHAPE_NONE
    command = str(tool_input.get("command", ""))
    if not command:
        return "", MERGE_SHAPE_NONE
    return command, merge_shape(command)


def _handle_not_evaluable_failure(cwd: str, failure: str) -> tuple:
    """``(allowed, failure)`` for a not-evaluable predicate failure --
    extracted from `main` to keep it under the function-length cap.
    ``allowed`` is True only when the dated
    `merge_gate: warn` carve-out was honored AND audited; *failure* is
    unchanged on a plain deny, or amended to also name an audit-write
    failure when the ledger write itself failed (see the module
    docstring's NOT-EVALUABLE section, and `main`'s own security-review
    follow-up note preserved here: the ledger write is this path's ONLY
    compensating control, so it failing must never read as "still fine")."""
    if os.environ.get(NOT_EVALUABLE_OPT_IN_ENV) != "1":
        return False, failure
    loosening = _read_merge_gate_loosening(cwd)
    if loosening is None:
        return False, failure
    reason, until = loosening
    audit_failure = _write_not_evaluable_bypass_row(
        cwd,
        reason=reason,
        until=until,
        why=failure,
    )
    if audit_failure is None:
        message = NOT_EVALUABLE_WARN_TEMPLATE.format(
            why=failure,
            until=until,
            reason=reason,
        )
        sys.stderr.write(message + "\n")
        return True, failure
    return False, (
        f"{failure}; additionally, the not-evaluable loosening "
        f"could not be audited ({audit_failure}), so it was not honored"
    )


def _degraded_classify(event: dict) -> int:
    """`main`'s classifier while a CLASSIFICATION sibling failed to import.

    RULING (Computer Prime, review round 5, final; scoped round 6, P1):
    degraded mode does not classify at all -- it DENIES EVERY command (2),
    naming the failed sibling and the repair. A broken payload is a
    stop-the-line state, not a state safe to keep guessing a
    classification inside: round 4's narrower `gh api`/`gh pr`-shaped deny
    (see git history) still let a quote-split (`g''h pr merge`) or an
    alias evade its raw-text regex -- the fix is not a sharper regex (any
    argv-only scan run WITHOUT the tokenizer/alias-resolution siblings
    that just failed to import is exactly as bypassable), it is refusing
    to run one at all while those siblings are unavailable. The round-2
    usability concern (bricking the whole session's shell) is the
    accepted trade for THIS narrower trigger -- `_SIBLING_IMPORT_ERROR` is
    now set only by `_parse`/`_gate`/`_predicate` failing to import (the
    classifier itself); `_trust` failing alone no longer reaches here at
    all (see the module-docstring import block) -- it degrades only the
    predicate-binary trust decision, so only merge-shaped commands get
    denied, not every command.

    *event* is accepted only to match `main`'s call signature and the
    other classifier entry points; nothing about it is inspected.
    """
    why = (
        f"a sibling module import failed ({_SIBLING_IMPORT_ERROR}); "
        "degraded mode denies every command until the payload is repaired "
        "-- this must be repaired OUT-OF-BAND, from a terminal outside "
        "this agent session (re-pin the payload: `bpsai-pair upgrade "
        "--auto`, run from the repo root) -- this guard denies every Bash "
        "command, including that remedy, until the payload is whole"
    )
    sys.stderr.write(DENY_PREFIX + why + "\n")
    return 2


def main() -> int:
    """Read the hook event on stdin; deny (2) on a blocked merge, else 0."""
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except Exception:
        # Fail OPEN -- our own input, our own bug (see the docstring's
        # two-sided posture, and base_branch_guard.py's identical branch).
        return 0
    if not isinstance(event, dict):
        return 0
    try:
        if _SIBLING_IMPORT_ERROR is not None:
            # Checked here, after stdin is read but before any
            # classification that needs a sibling name:
            # `merge_command`/`merge_shape`/`command_requests_refused_merge`
            # all consume names bound only by the `pr_merge_guard_parse`
            # import. `_degraded_classify` needs none of them. Inside this
            # `try` (review round 4, P2) so an exception IN
            # `_degraded_classify` itself -- not just in the full-classifier
            # path below -- also denies closed via the same `except`
            # rather than propagating out of `main` and exiting 1.
            return _degraded_classify(event)
        command, shape = merge_command(event)
        if command and command_requests_refused_merge(command):
            # Checked BEFORE the NONE early-return: `gh api .../merge`
            # names no `pr`/`merge` token subsequence and classifies as
            # NONE on its own.
            sys.stderr.write(DENY_PREFIX + REFUSED_MERGE_MESSAGE + "\n")
            return 2
        if shape == MERGE_SHAPE_NONE:
            return 0
        if shape == MERGE_SHAPE_AMBIGUOUS:
            # Never consult the predicate here: it would answer about the
            # CURRENT BRANCH's PR, which this command did not actually
            # name.
            sys.stderr.write(DENY_PREFIX + AMBIGUOUS_TARGET_MESSAGE + "\n")
            return 2

        cwd = str(event.get("cwd") or os.getcwd())
        payload, failure, not_evaluable = ask_predicate(cwd)
        if failure is not None:
            argv = " ".join(PREDICATE_ARGV)
            if not_evaluable:
                allowed, failure = _handle_not_evaluable_failure(cwd, failure)
                if allowed:
                    return 0
                # Distinct from the generic FAIL_CLOSED_TEMPLATE below: a
                # not-evaluable predicate has a SECOND remedy (the audited
                # env-var + dated-loosening carve-out) that must be named
                # here, whether the carve-out wasn't configured at all or
                # its own audit write just failed -- see
                # not_evaluable_fail_closed_message's own docstring.
                why_msg = not_evaluable_fail_closed_message(failure, argv)
                sys.stderr.write(DENY_PREFIX + why_msg + "\n")
                return 2
            why_msg = FAIL_CLOSED_TEMPLATE.format(why=failure, argv=argv)
            sys.stderr.write(DENY_PREFIX + why_msg + "\n")
            return 2
        if payload.get("allow"):
            if payload.get("bypassed"):
                # Allowed only by a dated loosening -- say so. An audited
                # bypass that is invisible at the moment it fires is one
                # nobody reviews (the visibility control
                # base_branch_guard.py's own marker advisory establishes).
                sys.stderr.write(str(payload.get("message", "")) + "\n")
            return 0
        sys.stderr.write(DENY_PREFIX + str(payload.get("message", "")) + "\n")
        return 2
    except Exception as exc:
        # A guard must never crash open: once the command has been read,
        # ANY unexpected error (a NameError from an unforeseen partial-
        # import state, a KeyError from a malformed predicate payload, ...)
        # denies closed here instead of propagating out of `main` and
        # exiting 1 (non-blocking) the way an uncaught exception otherwise
        # would.
        # Class only, not the message (review round 6, P2, same reasoning
        # as `pr_merge_guard_predicate.ask_predicate`'s own exception
        # handler): an unforeseen exception's text can embed argv/path/
        # payload detail this hook never verified, so it is not this
        # hook's to relay verbatim to a transcript.
        why = f"an internal error occurred ({type(exc).__name__})"
        sys.stderr.write(DENY_PREFIX + why + "\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
