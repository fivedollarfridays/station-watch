"""Shell-ish tokenizing/parsing primitives for `pr_merge_guard.py`.

Split out of the main hook module (architecture: file/function-count caps).
Stdlib-only, no `bpsai_pair` import, matching the main hook's own
runtime contract. These are token/position primitives -- tokenizing,
segmenting, alias resolution, nested-command extraction, and locating
where a `gh` subcommand sits once global flags are accounted for -- not
classification: `pr_merge_guard.py`'s own `_segment_is_gh_api_merge`/
`command_requests_refused_merge` cluster builds on top of these, never
the reverse, so this module has no sibling import of its own. (Review
round 5: `_gh_subcommand_index`/`_first_non_flag_index`/
`_segment_is_gh_graphql_merge_mutation` moved here from the facade to
keep it under the function-count cap -- they are position/lookup helpers,
not verdict logic, so the split is a parsing/classification boundary,
not an arbitrary one.)
"""

from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath, PureWindowsPath

#: Shell control-operator tokens `shlex` (with `punctuation_chars=True`)
#: splits out on its own -- a merge-shape check is judged PER SEGMENT
#: between these, so three unrelated commands that only mention `gh`,
#: `pr`, `merge` in that order across DIFFERENT commands are never
#: mistaken for one `gh pr merge` call.
CONTROL_OPERATOR_TOKENS = (";", "&", "&&", "||", "|")

#: Basenames that read a `-c <script>` argument as a command string a real
#: shell would execute -- `eval`'s argument gets the same treatment below.
SHELL_C_INTERPRETER_BASENAMES = frozenset({"sh", "bash", "zsh", "ksh", "dash", "ash"})

#: `NAME=value` -- both a bare shell assignment and (positionally, after a
#: literal `alias` token) an alias declaration.
ASSIGNMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.DOTALL)

#: A backtick-quoted command substitution, read straight off the raw text:
#: `shlex` has no model of backtick quoting (unlike `'`/`"`), so a token
#: walk alone would leave `` `gh pr merge` `` glued to its neighbors.
BACKTICK_RE = re.compile(r"`([^`]*)`")

#: Bounds recursion into nested command text (eval/sh -c/assignment/
#: backtick/`$()` bodies) -- a crafted input nesting these arbitrarily
#: deep degrades to "denied, not hung", never the reverse.
_MAX_RECURSION_DEPTH = 6

#: The fallback classifier for text the primary tokenizer could not parse
#: (see `_tokenize`) -- a raw-text, quote-blind scan for `gh` ... `pr` ...
#: `merge` in order, case-insensitive. Deliberately coarser and MORE likely
#: to match than the primary path: an unparseable command is exactly the
#: ambiguous, security-relevant shape that must fail closed (get a
#: predicate check), not silently pass through unclassified.
_MERGE_RE_FLAGS = re.IGNORECASE | re.DOTALL
_FALLBACK_MERGE_RE = re.compile(r"\bgh\b.*?\bpr\b.*?\bmerge\b", _MERGE_RE_FLAGS)


#: `gh`'s own global options that take a value -- placed BEFORE the
#: subcommand (`gh -R owner/repo api ...`, `gh --repo owner/repo api ...`,
#: `gh --hostname ghe.example.com api ...`), so a fixed-position check for
#: `segment[1] == "api"` misses the subcommand entirely once one of these
#: shifts it over. `--repo=owner/repo`/`--hostname=host`
#: (`=`-joined) carry their value in the SAME token and are handled by the
#: `startswith` branch below rather than consuming a second token.
_GH_GLOBAL_VALUE_FLAGS = frozenset({"-R", "--repo", "--hostname"})

#: GraphQL mutation names that merge (or auto-merge) a pull request --
#: checked as a lowercase substring, so `mutation{mergePullRequest(...)}`
#: matches without needing a token boundary of its own.
_GRAPHQL_MERGE_MUTATIONS = ("mergepullrequest", "enablepullrequestautomerge")


def _is_gh_spelling(token: str) -> bool:
    """True when *token* denotes the `gh` binary, however it is spelled --
    bare `gh`, a relative/absolute POSIX or Windows path ending in it, any
    case variant, or the `.exe` suffix. Lexical only (mirrors
    `base_branch_guard_parser._is_git_token`'s `git` recognition)."""
    for name in (PurePosixPath(token).name, PureWindowsPath(token).name):
        lowered = name.lower()
        if lowered.endswith(".exe"):
            lowered = lowered[: -len(".exe")]
        if lowered == "gh":
            return True
    return False


def _tokenize(command: str):
    """*command* split into shell-ish tokens, quote-aware. `None` on
    unbalanced quoting (Python's `shlex` posix mode also doesn't fully
    model bash -- e.g. ANSI-C `$'...'` quoting can itself raise) --
    callers fall back to `_FALLBACK_MERGE_RE`, deliberately STRICTER than
    this primary path, never the reverse (same posture as
    `base_branch_guard_parser._fallback_touched_directory`)."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return None


def _alias_table(tokens: list) -> dict:
    """`{name: [value tokens]}` for every `alias name=value` shape in
    *tokens* -- closes the `alias g=gh; g pr merge` evasion by letting
    `_resolve_aliases` splice in what the alias actually stands for."""
    table = {}
    for index, token in enumerate(tokens):
        if token != "alias" or index + 1 >= len(tokens):
            continue
        match = ASSIGNMENT_RE.match(tokens[index + 1])
        if match:
            table[match.group(1)] = match.group(2).split()
    return table


def _resolve_aliases(tokens: list, aliases: dict) -> list:
    """*tokens* with every occurrence of a known alias name spliced out
    for its value tokens, up to a few rounds (an alias may itself expand
    to another alias name). A no-op when nothing in *tokens* declared one.
    """
    if not aliases:
        return tokens
    result = tokens
    for _ in range(4):
        changed = False
        expanded = []
        for token in result:
            replacement = aliases.get(token)
            if replacement is not None:
                expanded.extend(replacement)
                changed = True
            else:
                expanded.append(token)
        result = expanded
        if not changed:
            break
    return result


def _segments(tokens: list) -> list:
    """*tokens* split at control-operator tokens."""
    segments: list = []
    current: list = []
    for token in tokens:
        if token in CONTROL_OPERATOR_TOKENS:
            segments.append(current)
            current = []
        else:
            current.append(token)
    segments.append(current)
    return segments


def _nested_command_texts(tokens: list, command: str) -> list:
    """Command text embedded as DATA that a real shell would still
    execute, from two sources:

    * TOKEN-based -- an `eval`/`sh -c`-family argument, or the right-hand
      side of a `NAME=value` assignment. Assigning a merge-shaped string
      is itself treated as suspicious -- a later bare `$NAME` invocation
      of it in a SEPARATE Bash call is invisible to a hook that only ever
      sees one command string at a time, so the earlier assignment is the
      only place this hook can still catch that shape.
    * RAW-TEXT-based -- every backtick and top-level `$( ... )`
      command-substitution body in *command*, found by scanning the raw
      string rather than walking *tokens*: both quoting forms can end up
      glued into ONE opaque `shlex` token (backticks aren't a quote
      character `shlex` knows at all; a `$()` substitution WRAPPED IN
      DOUBLE QUOTES, e.g. `echo "$(gh pr merge 42)"`, collapses to the
      single token `$(gh pr merge 42)` since `shlex` only tracks `'`/`"`
      nesting, not `$()` inside them). The `$()` scan is depth-tracked
      (not a naive regex) so a nested call (`$(echo $(gh pr merge))`)
      doesn't truncate at the first inner `)`.
    """
    nested = [match.group(1) for match in BACKTICK_RE.finditer(command)]
    index, length = 0, len(command)
    while index < length:
        if command[index] == "$" and index + 1 < length and command[index + 1] == "(":
            depth = 1
            cursor = index + 2
            while cursor < length and depth:
                if command[cursor] == "(":
                    depth += 1
                elif command[cursor] == ")":
                    depth -= 1
                cursor += 1
            nested.append(command[index + 2 : cursor - 1])
            index = cursor
        else:
            index += 1
    index = 0
    while index < len(tokens):
        token = tokens[index]
        basename = PurePosixPath(token).name.lower()
        if basename == "eval" and index + 1 < len(tokens):
            nested.append(tokens[index + 1])
        elif basename in SHELL_C_INTERPRETER_BASENAMES:
            cursor = index + 1
            ops = CONTROL_OPERATOR_TOKENS
            while cursor < len(tokens) and tokens[cursor] not in ops:
                if tokens[cursor] == "-c" and cursor + 1 < len(tokens):
                    nested.append(tokens[cursor + 1])
                    break
                cursor += 1
        match = ASSIGNMENT_RE.match(token)
        if match:
            nested.append(match.group(2))
        index += 1
    return nested


#: Substitution/subshell markers that make a `NAME=value` assignment's
#: value NOT a plain literal -- `$(`/backtick command substitution,
#: `${` parameter expansion, `<(`/`>(` process substitution. Any of these
#: EXECUTE (or open a live fd to) arbitrary text before `gh` itself ever
#: runs, so an assignment carrying one is not the transparent prefix a
#: real shell treats a bare literal assignment as.
_SUBSTITUTION_MARKERS = ("$(", "`", "${", "<(", ">(")

#: Characters that end a raw shell word outside of quotes -- whitespace
#: plus the single-character control operators. (`&&`/`||` are each
#: still a run of one of these chars, so checking membership is enough
#: to find the word boundary; which MULTI-char operator it is doesn't
#: matter here, only that the word has ended.)
_RAW_WORD_BREAK_CHARS = frozenset(";&|") | frozenset(" \t\n\r\f\v")


def _assignment_value_is_plain_literal(value: str) -> bool:
    """True when *value* (a `NAME=value` assignment's right-hand side,
    ALREADY DEQUOTED by `shlex`) contains none of `_SUBSTITUTION_MARKERS`
    -- i.e. running the assignment can have no side effect beyond setting
    the variable. Conservative fallback for when the raw command text
    isn't available to `_first_executable_index` (or doesn't line up with
    *value*'s token) -- a quoted-literal marker (`FOO='${literal}'`) reads
    as live here, since dequoting has already erased the quotes that made
    it literal; callers that DO have the raw text should prefer
    `_raw_value_has_live_marker` instead, which stays quote-aware."""
    return not any(marker in value for marker in _SUBSTITUTION_MARKERS)


def _raw_word_span(text: str, start: int) -> int:
    """Index in *text* just past the end of the shell word beginning at
    *start* (already past any leading whitespace) -- quote- and
    backslash-aware, so a `_RAW_WORD_BREAK_CHARS` character sitting
    inside a quoted span or right after a backslash doesn't end the word
    early. Mirrors `shlex`'s own quoting rules closely enough for the one
    thing this is used for: finding where an assignment's raw, as-written
    text ends, not re-tokenizing it."""
    index = start
    length = len(text)
    quote = None
    while index < length:
        char = text[index]
        if quote is not None:
            if (
                quote == '"'
                and char == "\\"
                and index + 1 < length
                and text[index + 1] in '"\\$`'
            ):
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char == "\\" and index + 1 < length:
            index += 2
            continue
        if char in ("'", '"'):
            quote = char
            index += 1
            continue
        if char in _RAW_WORD_BREAK_CHARS:
            break
        index += 1
    return index


def _raw_value_has_live_marker(text: str, start: int, end: int) -> bool:
    """True when `text[start:end]` (an assignment's raw, as-written value)
    contains one of `_SUBSTITUTION_MARKERS` somewhere a real shell would
    still expand it -- i.e. NOT inside a single-quoted span and NOT
    immediately backslash-escaped. A double-quoted or bare occurrence
    still counts as live: bash performs `$(...)`/`${...}` and backtick
    expansion inside double quotes too, only single quotes and a leading
    backslash suppress it."""
    index = start
    quote = None
    while index < end:
        char = text[index]
        if quote == "'":
            if char == "'":
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == "\\" and index + 1 < end and text[index + 1] in '"\\$`':
                index += 2
                continue
            if char == '"':
                quote = None
                index += 1
                continue
            # else: still inside double quotes -- fall through to the
            # marker check below, since bash keeps expanding there.
        else:
            if char == "\\" and index + 1 < end:
                index += 2
                continue
            if char in ("'", '"'):
                quote = char
                index += 1
                continue
        if any(text.startswith(marker, index) for marker in _SUBSTITUTION_MARKERS):
            return True
        index += 1
    return False


def _first_executable_index(segment: list, command: str | None = None) -> int:
    """Index of *segment*'s first token that is not a bare `NAME=value`
    shell-assignment prefix carrying a plain literal value -- the
    position `gh` itself must sit at for a merge-shaped segment to be
    safe to relay a predicate verdict for (see `pr_merge_guard.py`'s
    `_segment_gh_not_first_executable`). Unlike `_first_non_flag_index`/
    `_gh_subcommand_index` below (which skip FLAG-shaped tokens once
    `gh` is already known to be `segment[0]`), this looks for the first
    EXECUTABLE at all, so a wrapper (`env`, `pushd`, `sh`, `nohup`, ...)
    ahead of `gh` is never mistaken for an env-var prefix. A bare literal
    assignment (`FOO=bar gh ...`) is the one prefix real shells treat as
    transparent -- `gh` still runs directly, in the same process's cwd
    -- so, and only so, it is skipped here. An assignment whose value
    carries a LIVE command/process substitution (`FOO=$(...)`,
    `` FOO=`...` ``, `FOO=${...}`, `FOO=<(...)`/`FOO=>(...)`) executes
    that text BEFORE `gh` runs and is treated as its own executable
    instead -- the same "something earlier could change the context"
    risk a leading wrapper poses.

    *command* (when given) is the RAW, pre-tokenization command text --
    `segment` is walked in lockstep against it word-by-word so a
    single-quoted or backslash-escaped marker (`FOO='${literal}' gh ...`,
    `` FOO=\\$\\{literal\\} gh ... ``) reads as the literal it is, not as
    a live substitution: `shlex` has already stripped that quoting by the
    time it produces `segment`'s tokens, so a check against the DEQUOTED
    token alone (`_assignment_value_is_plain_literal`) cannot tell the
    two apart. If the raw text at the expected offset doesn't actually
    start with this token's `NAME=` (e.g. alias resolution spliced in a
    token from elsewhere in the command), the walk falls back to the
    dequoted check for that token rather than trust a misaligned read --
    fail CLOSED, same as every other ambiguous shape in this module."""
    raw_index = 0
    for index, token in enumerate(segment):
        match = ASSIGNMENT_RE.match(token)
        if not match:
            return index
        if command is None:
            if _assignment_value_is_plain_literal(match.group(2)):
                continue
            return index
        while raw_index < len(command) and command[raw_index] in " \t\n\r\f\v":
            raw_index += 1
        word_end = _raw_word_span(command, raw_index)
        raw_word = command[raw_index:word_end]
        name_prefix = token[: token.index("=") + 1]
        if not raw_word.startswith(name_prefix):
            if _assignment_value_is_plain_literal(match.group(2)):
                raw_index = word_end
                continue
            return index
        if _raw_value_has_live_marker(raw_word, len(name_prefix), len(raw_word)):
            return index
        raw_index = word_end
    return len(segment)


def _gh_subcommand_index(segment: list) -> int:
    """Index of *segment*'s first token after `gh` that isn't one of `gh`'s
    OWN global options (or that option's value) -- the position a
    subcommand name like `api` actually sits at once any global flag
    (`-R`/`--repo`/`--hostname`, boolean or not) precedes it. Returns
    ``len(segment)`` when every remaining token is consumed as an option/
    value (no subcommand present)."""
    index = 1
    while index < len(segment):
        token = segment[index]
        if token in _GH_GLOBAL_VALUE_FLAGS:
            index += 2
            continue
        if any(token.startswith(flag + "=") for flag in _GH_GLOBAL_VALUE_FLAGS):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return index


def _first_non_flag_index(segment: list) -> int:
    """Index of *segment*'s first token after `gh` that doesn't start with
    `-` -- a coarser subcommand guess than `_gh_subcommand_index`'s
    known-flag walk, for when an unrecognized global flag's VALUE (not
    just the flag itself) shifts the real subcommand token further right
    than that walk accounts for. Returns ``len(segment)`` when every
    remaining token looks like a flag."""
    for index in range(1, len(segment)):
        if not segment[index].startswith("-"):
            return index
    return len(segment)


def _gh_subcommand_index_assuming_value_flags(segment: list) -> int:
    """Index of *segment*'s first token after `gh` once every LEADING
    `-`-prefixed token is treated as consuming a value of its own --
    unlike `_first_non_flag_index` (which assumes each is boolean), this
    covers an unrecognized global flag that takes a value (`gh --newflag
    v api ...`), where the real subcommand sits one token further right
    than either `_gh_subcommand_index` or `_first_non_flag_index` lands.
    Returns ``len(segment)`` once the walk runs past the segment's end."""
    index = 1
    while index < len(segment) and segment[index].startswith("-"):
        index += 2
    return min(index, len(segment))


def _segment_is_gh_graphql_merge_mutation(segment: list) -> bool:
    """True when *segment* invokes `gh api graphql` naming a merge-shaped
    GraphQL mutation (`mergePullRequest`, `enablePullRequestAutoMerge`).
    The `api` subcommand is resolved the same way
    `_segment_is_gh_api_merge` resolves it -- `_gh_subcommand_index`
    (known global flags), then `_first_non_flag_index` (an unrecognized
    BOOLEAN global flag), then `_gh_subcommand_index_assuming_value_flags`
    (an unrecognized VALUE-taking global flag, `gh --newflag v api
    graphql -f query='mutation{mergePullRequest...}'`) -- rather than
    scanning the whole segment for a bare `api` token: a literal `api` (or
    `graphql`, or a mutation name) can be some UNRELATED flag's VALUE,
    e.g. `gh issue create --title api --body graphql --label
    mergePullRequest`, which names none of `gh`'s subcommands `api` at
    all and must not be treated as a GraphQL merge call. Only once the
    resolved subcommand actually reads `api` does `graphql` + a mutation
    name get scanned for, over the remaining tokens in any position --
    the flag that shifted them can still land anywhere after `api`
    itself."""
    if not segment or not _is_gh_spelling(segment[0]):
        return False
    subcommand = _gh_subcommand_index(segment)
    if not (subcommand < len(segment) and segment[subcommand].lower() == "api"):
        subcommand = _first_non_flag_index(segment)
        if not (subcommand < len(segment) and segment[subcommand].lower() == "api"):
            subcommand = _gh_subcommand_index_assuming_value_flags(segment)
            if not (subcommand < len(segment) and segment[subcommand].lower() == "api"):
                return False
    lowered_tokens = [token.lower() for token in segment[1:]]
    if "graphql" not in lowered_tokens:
        return False
    for token in lowered_tokens:
        for mutation in _GRAPHQL_MERGE_MUTATIONS:
            if mutation in token:
                return True
    return False
