"""Command-text parsing: resolving the directory (or directories) a
Bash tool call would commit into, without mistaking quoted prose for a
real ``git commit`` invocation.

Tokenizes with ``shlex`` (``punctuation_chars=True``, so an attached
control operator like ``true;git`` still splits into separate tokens) --
a quoted argument that merely SPELLS "git commit" collapses to ONE opaque
token, never the two adjacent bare words a real invocation produces.

TWO PRE-TOKENIZATION NORMALIZATION PASSES run before ``shlex`` ever sees
the text, because both classes of defect below happen at a layer shlex
itself has no model of:

* ``base_branch_guard_heredoc.strip_heredoc_bodies`` -- a heredoc body is
  free-form text a real shell defers to a LATER parse phase, entirely
  outside quote/operator scanning. Fed straight to a flat quote-tracking
  tokenizer, a quote CHARACTER inside the body (not a real shell quote at
  all) can close an outer quoted argument early, desyncing everything
  after it. Located by the heredoc's own operator/terminator grammar
  (regex, not quote-state) and excised wholesale before any quote
  tracking runs. When the heredoc's CONSUMING COMMAND is a shell
  interpreter, the body is command text, not data -- `_parse_commit_dirs`
  below re-parses it recursively rather than treating it as inert; see
  that module's docstring for the full reasoning.
* ``_normalize_newlines`` -- ``shlex``'s default whitespace set includes
  the newline character, so a physical line break is invisible to the
  segment tracker below: `cd`/`git` detection keys off "does a new
  simple-command segment start here", and a literal newline is exactly as
  much a segment boundary as `;` is, but never resets one. A quote-aware
  scan turns every UNQUOTED newline into a `;` (already a recognized
  control-operator token) while leaving a newline actually embedded in a
  quoted argument alone.

ONCE TOKENIZATION SUCCEEDS, THE TOKEN WALK (``_walk_command_directories``)
IS THE SOLE AUTHORITY -- nothing downstream re-parses the raw command
string. It returns EVERY ``git ... commit`` segment's resolved directory,
not just the first: a compound command can commit into more than one
checkout (`git -C a commit ... && git -C b commit ...`), and each one
must be judged on its own, independently-resolved directory.

The regex pair in ``base_branch_guard_constants`` survives ONLY as
``_fallback_touched_directory``, used when ``shlex`` still raises
``ValueError`` on unparsable input after both normalization passes --
deliberately STRICTER (less capable, more likely to deny) than the
primary token-walk path, never the reverse.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path, PurePosixPath, PureWindowsPath

from base_branch_guard_constants import (
    BACKGROUND_OPERATOR,
    CONDITIONAL_OPERATOR,
    CONTROL_OPERATOR_TOKENS,
    PIPELINE_OPERATOR,
    SEQUENCE_OPERATOR_TOKENS,
    GIT_COMMIT_RE,
    GIT_DASH_C_RE,
    GIT_VALUE_FLAGS,
    skip_env_assignments as _skip_env_assignments,
)
from base_branch_guard_heredoc import strip_heredoc_bodies as _strip_heredoc_bodies
from base_branch_guard_targets import mutating_target


def _matches_git_basename(name: str) -> bool:
    """True when *name* is a case-insensitive spelling of ``git``, with at
    most one trailing ``.exe`` suffix stripped first: ``Git.exe``/bare
    ``GIT`` match; ``git.exe.exe`` and ``Legit.exe`` do not.
    """
    lowered = name.lower()
    if lowered.endswith(".exe"):
        lowered = lowered[: -len(".exe")]
    return lowered == "git"


def _is_git_token(token: str) -> bool:
    """True when *token* denotes the git binary, however it is spelled --
    bare ``git``, a relative/absolute POSIX or Windows path ending in it,
    any case variant, or the ``.exe`` suffix. Lexical only.
    """
    return _matches_git_basename(PurePosixPath(token).name) or _matches_git_basename(
        PureWindowsPath(token).name
    )


def _resolve_against(base: Path, raw: str) -> Path:
    """*raw* resolved against *base* unless *raw* is already absolute."""
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else base / candidate


def _normalize_newlines(command: str) -> str:
    """Every UNQUOTED newline becomes a `;` segment boundary; a newline
    actually embedded inside a quoted argument is left alone.

    A lightweight quote-aware scan (single/double quotes, with a minimal
    backslash-escape skip inside double quotes) -- not a full shell
    grammar, but sufficient to distinguish "a real line break between
    commands" from "a newline that is part of one already-quoted token".
    """
    out = []
    quote = None
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if quote:
            out.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < n:
                i += 1
                out.append(command[i])
            elif ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "\n":
            out.append(";")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _fallback_touched_directory(command: str, cwd: str) -> Path | None:
    """The ``tokens is None`` fail-closed path: unparsable text (unbalanced
    quotes) cannot be tokenized reliably, so this is the ONLY place the
    regex pair still runs. Deliberately STRICTER than the primary
    token-walk path: no basename recognition, no ``-C``-from-tokens, no
    ``cd``-chain awareness -- fail-closed here means "deny with less
    information", never "deny less than the primary path would".
    """
    if not GIT_COMMIT_RE.search(command):
        return None
    dash_c = GIT_DASH_C_RE.search(command)
    if dash_c:
        return _resolve_against(Path(cwd), dash_c.group(1))
    return Path(cwd)


def _is_single_quoted(raw: str) -> bool:
    """True when *raw* -- a token from the QUOTE-PRESERVING parse (see
    `_raw_quoted_tokens`) -- is wrapped, start to end, in a single pair of
    single quotes. Single quotes suppress EVERY form of shell expansion
    with no exception -- unlike double quotes, which still let `$`/`` ` ``
    through -- so a token this function accepts is never a live
    substitution, whatever characters it contains.
    """
    return len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'"


def _dash_c_value_is_literal(value: str, raw_value: str | None) -> bool:
    """False when *value* still carries an unexpanded shell substitution
    marker (`$VAR`, `${VAR}`, `` `cmd` ``) -- text the guard sees exactly
    as typed, never as the shell would expand it. Composing such a token
    as a literal path segment fabricates a directory that only
    coincidentally resolves correctly, by walking back up to an ancestor,
    when nothing really exists at that literal name; a directory that DOES
    exist there (a nested checkout sharing the token's spelling) would
    silently resolve to the wrong repo instead.

    *raw_value* is *value*'s counterpart from the quote-preserving parse,
    or `None` when that parse could not be correlated with the primary
    one (see `_raw_quoted_tokens`). `shlex` in POSIX mode -- the primary
    tokenizer everywhere else in this module -- strips quote characters
    entirely, so `-C '$W'` (single-quoted, LITERAL text `$W`, no
    substitution at all) and `-C $W` / `-C "$W"` (live substitution) both
    collapse to the identical bare token `$W` by the time this function
    would otherwise see it. Without *raw_value* a single-quoted literal
    was misclassified as unresolvable and fell back to the effective
    directory instead of composing -- a false ALLOW when the shell's real
    target (a nested checkout literally named `$W`) is what actually
    carries the protected branch. `raw_value` recovers the distinction:
    single-quoted is always literal, regardless of what it contains.
    """
    if raw_value is not None and _is_single_quoted(raw_value):
        return True
    return "$" not in value and "`" not in value


def _advance_past_git_flags(
    tokens: list, cursor: int, effective_dir: Path, raw_tokens: list | None
) -> tuple[int, Path | None]:
    """Walk flag tokens after a recognized `git` token; return the cursor
    positioned at the (would-be) subcommand, plus the RUNNING `-C`
    resolution folded left-to-right exactly as git composes repeated
    `-C` flags (a relative value joins onto the previous one, an
    absolute value resets it entirely).

    An unresolvable value (see `_dash_c_value_is_literal`) never composes
    -- it neither joins onto a running relative chain nor survives past
    itself, matching the fact that no answer can be known once the shell's
    real expansion is missing. A LATER absolute, literal `-C` still resets
    the composition outright and clears the unresolvable state, exactly as
    it would with no unresolvable value in the chain at all: real git's
    own reset rule does not care what an EARLIER `-C` was.

    *raw_tokens*, positionally aligned with *tokens* (see
    `_raw_quoted_tokens`), is what lets `_dash_c_value_is_literal` tell a
    single-quoted literal `$VAR` apart from a live one -- `None` when that
    alignment was not available, in which case every `-C` value is judged
    on its stripped text alone, same as before this distinction existed.
    """
    dash_c_dir = None
    dash_c_unresolvable = False
    while cursor < len(tokens):
        candidate = tokens[cursor]
        if candidate in GIT_VALUE_FLAGS and cursor + 1 < len(tokens):
            if candidate == "-C":
                value = tokens[cursor + 1]
                raw_value = raw_tokens[cursor + 1] if raw_tokens is not None else None
                if not _dash_c_value_is_literal(value, raw_value):
                    dash_c_dir = None
                    dash_c_unresolvable = True
                elif Path(value).is_absolute() or not dash_c_unresolvable:
                    base = dash_c_dir if dash_c_dir is not None else effective_dir
                    dash_c_dir = _resolve_against(base, value)
                    dash_c_unresolvable = False
                # else: a relative value composing onto an unresolvable
                # base is itself unresolvable -- stays unresolvable.
            cursor += 2
            continue
        if candidate.startswith("-") and candidate != "-":
            cursor += 1
            continue
        break
    return cursor, None if dash_c_unresolvable else dash_c_dir


def _extend_unseen(results: list, candidates: list) -> None:
    """Append each of *candidates* not already in *results*, in order.

    Used for the directories a CONDITIONAL operator leaves in play: after
    `A || cd <dir>`, the `cd` ran only if `A` failed, so a later commit
    may equally have landed in the directory that was effective BEFORE
    the operator. The guard refuses when ANY resolved directory is
    protected, so carrying both is the fail-closed answer to a branch the
    parser cannot decide -- and it is only reached when the commit did
    not name its checkout outright with `-C`.
    """
    for candidate in candidates:
        if candidate not in results:
            results.append(candidate)


def _cd_reaches_parent_shell(tokens: list, index: int, opened_by: str) -> bool:
    """True when the `cd` at *index* changes the PARENT shell's directory.

    False when it runs in a subshell the following commit never inherits.
    `&&` binds LOOSER than `|`, so `A | cd X && git commit` parses as
    `(A | cd X) && git commit`: the `cd` is a pipeline element and the
    commit runs in the parent, at the ORIGINAL directory.

    Which SIDE of the operator the `cd` sits on is the whole question,
    and the two operators are not symmetric. `|` subshells its segment
    from either side (*opened_by* or terminator). `&` only backgrounds
    the segment it TERMINATES -- a `cd` merely following one
    (`A & cd X && git commit`) is an ordinary parent-shell command, and
    treating `&` as a subshelling OPENER silently drops that `cd`.
    """
    if opened_by == PIPELINE_OPERATOR:
        return False
    for token in tokens[index:]:
        if token in CONTROL_OPERATOR_TOKENS:
            return token not in (PIPELINE_OPERATOR, BACKGROUND_OPERATOR)
    return True


def _open_segment(state: dict, token: str) -> None:
    """Fold a control operator into the walk's uncertainty state.

    `||` opens an undecided branch AND an ``&&``/``||`` chain whose every
    later member is conditional on how that branch resolved. The two are
    not the same span, which is the whole subtlety: in `A || B && C`, `B`
    is the branch that may be skipped, while `C` is skipped only when `A`
    and `B` BOTH fail -- so `C` is conditional too, just on a different
    question. Only a SEQUENCE operator ends the chain; after one, the next
    command runs whatever happened, so anything the chain deferred becomes
    a live candidate right there.
    """
    if token == CONDITIONAL_OPERATOR:
        _extend_unseen(state["conditional"], [state["effective"]])
        state["or_chain"] = True
        state["branch"] = True
        return
    if token in SEQUENCE_OPERATOR_TOKENS:
        _extend_unseen(state["conditional"], state["pending"])
        state["pending"] = []
        state["or_chain"] = False
    state["branch"] = False


def _settle_with_cd(state: dict, target: Path) -> None:
    """Move the effective directory, retiring the candidates it settles.

    Three cases, and getting them apart is the point:

    * inside the `||`'s own branch -- the `cd` may never have run, so the
      pre-`||` directory stays live;
    * elsewhere in a chain a `||` opened -- the `cd` runs only if that
      chain survived, so it settles a commit ``&&``-chained to it (which
      then ran too) but NOT one after the next sequencing operator. The
      directory it displaced is deferred to that boundary rather than
      dropped;
    * outside any such chain -- the shell is demonstrably here now, and
      every earlier candidate is retired.
    """
    if state["branch"]:
        pass
    elif state["or_chain"]:
        _extend_unseen(state["pending"], [state["effective"], *state["conditional"]])
        state["conditional"] = []
    else:
        state["conditional"] = []
    state["effective"] = target


def _cd_may_fail(tokens: list, index: int) -> bool:
    """True when the `cd` at *index* is the LEFT operand of `||` -- its own
    success is uncertain (a missing/unreadable target fails it), and on
    failure bash never changes directory at all. The RHS of that `||` then
    runs in whatever directory was in force BEFORE this `cd`, not its
    target -- so that pre-`cd` directory has to stay a live candidate
    alongside the target, exactly as it does for a `cd` that sits on the
    OTHER side of a `||` (`_open_segment`'s `CONDITIONAL_OPERATOR` case).
    Without this, `cd /missing || git commit` resolved to ONLY the missing
    target, discarding the directory the commit actually lands in when the
    `cd` fails.
    """
    return index + 2 < len(tokens) and tokens[index + 2] == CONDITIONAL_OPERATOR


def _raw_quoted_tokens(command: str, expected_len: int) -> list | None:
    """*command* tokenized WITHOUT quote-stripping (`posix=False`), for
    recovering the quote character the primary POSIX-mode parse (used
    for everything else in this module) discards -- the only way to tell
    a single-quoted, always-literal `-C` value apart from an unquoted or
    double-quoted one carrying a live substitution (see
    `_dash_c_value_is_literal`).

    Returns `None` -- never a mismatched list -- whenever this parse
    cannot be trusted to line up with the primary one position-for-
    position: it raises (non-POSIX mode is lenient about things POSIX
    mode rejects, so a text the primary parse already found unparsable
    could still tokenize here), or it disagrees on how many tokens the
    SAME text splits into. Every caller treats `None` as "no quote
    information available" and falls back to judging the stripped token
    alone, exactly as this module did before the distinction existed --
    the conservative direction, never the permissive one.
    """
    try:
        lexer = shlex.shlex(command, posix=False, punctuation_chars=True)
        lexer.whitespace_split = True
        raw = list(lexer)
    except ValueError:
        return None
    return raw if len(raw) == expected_len else None


def _walk_command_directories(command: str, cwd: str) -> list[Path]:
    """Every ``git ... commit`` segment's resolved directory, in order.

    The effective directory tracks leading ``cd`` tokens (composing with
    an explicit ``-C`` on top of it) across the WHOLE token stream, reset
    at each control-operator-marked segment boundary -- continuing the
    walk after each match, rather than returning at the first one, is
    what lets a compound command commit into more than one checkout be
    judged on each of its own targets.

    A `||` boundary is the one the walk cannot decide: the segment it
    opens runs only when the previous one FAILED, so the directory in
    force before it stays a candidate for later commits that did not name
    their checkout with `-C`. See `_extend_unseen`.

    That candidate is bounded, not permanent -- and the bound is not the
    `||`'s own segment. `A || B && C` runs `C` only when the GROUP
    succeeded, and it can fail (both operands failing), so a `cd` in `C`
    has settled nothing either. What settles a commit is `&&` sitting
    between it and the `cd`: the commit runs only if the whole chain did,
    the `cd` included. A `;`, `&` or newline decouples them, and there
    the displaced directory has to come back. See `_open_segment` and
    `_settle_with_cd`.

    Both bounds earn their keep in opposite directions. Without any, one
    `||` anywhere poisoned every later commit and turned ordinary
    `mkdir -p <wt> || true ; cd <wt> && git commit` into a refusal.
    Bounding it at the `||`'s own segment instead let
    `false || false && cd <wt> ; git commit --no-verify` through while
    bash committed on the protected trunk.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    tokens = list(lexer)  # may raise ValueError -- caller handles fallback
    raw_tokens = _raw_quoted_tokens(command, len(tokens))

    state = {
        "effective": Path(cwd),
        "conditional": [],  # live candidates for the next commit
        "pending": [],  # candidates a sequencing operator will make live
        "or_chain": False,
        "branch": False,
    }
    opened_by = ""
    segment_start = True
    index = 0
    results: list[Path] = []
    while index < len(tokens):
        # The env-assignment skip runs BEFORE the operator test so that an
        # assignment which is its OWN complete simple-command (`VAR=1 &&
        # cd X`, and the `;` spelling) lands on the operator that opens
        # the next segment and is handled as one -- re-arming
        # `segment_start`. Handling it after the test instead let the
        # operator read as an ordinary token, silently dropping the
        # segment reset and with it the `cd` that operator introduces.
        if segment_start:
            index = _skip_env_assignments(tokens, index)
            if index >= len(tokens):
                break
        token = tokens[index]
        if token in CONTROL_OPERATOR_TOKENS:
            _open_segment(state, token)
            opened_by = token
            segment_start = True
            index += 1
            continue
        # Kept split: collapses to a 99-char line at line-length=100.
        if (
            segment_start
            and token == "cd"
            and index + 1 < len(tokens)
            and not tokens[index + 1].startswith("-")
        ):  # fmt: skip
            if _cd_reaches_parent_shell(tokens, index, opened_by):
                cd_value = tokens[index + 1]
                raw_value = raw_tokens[index + 1] if raw_tokens is not None else None
                if _dash_c_value_is_literal(cd_value, raw_value):
                    pre_cd = state["effective"]
                    moved = _resolve_against(state["effective"], cd_value)
                    _settle_with_cd(state, moved)
                    if _cd_may_fail(tokens, index):
                        _extend_unseen(state["conditional"], [pre_cd])
                # else: an unresolved shell substitution (`$VAR`, `${VAR}`,
                # `` `cmd` ``) as the `cd` target -- composing it as a
                # literal path segment fabricates a directory that only
                # coincidentally resolves correctly (walking back up to an
                # ancestor when nothing exists at that literal name); a
                # directory that DOES exist there would silently resolve
                # to the wrong repo instead. Dropped entirely, same as
                # `-C` (see `_dash_c_value_is_literal`'s docstring): the
                # effective directory stays exactly where it already was
                # -- the tool's own PreToolUse `cwd` for a leading `cd`.
            index += 2
            segment_start = False
            continue
        segment_start = False
        if _is_git_token(token):
            # Kept split: collapses to a 90-char line at line-length=100.
            cursor, dash_c_dir = _advance_past_git_flags(
                tokens, index + 1, state["effective"], raw_tokens
            )  # fmt: skip
            if cursor < len(tokens) and tokens[cursor] == "commit":
                if dash_c_dir is not None:
                    results.append(dash_c_dir)
                else:
                    results.append(state["effective"])
                    _extend_unseen(results, state["conditional"])
                index = cursor + 1
                continue
        index += 1
    return results


def _parse_commit_dirs(command: str, cwd: str) -> list[Path]:
    """Every ``git ... commit`` directory in *command*, recursing into any
    interpreter-fed heredoc body found along the way (see
    `base_branch_guard_heredoc`'s module docstring) as its own
    independent command string, evaluated against the SAME *cwd* -- a
    documented simplification: a `cd` preceding the interpreter call in
    the OUTER command is not tracked into the recursion, trading a little
    precision on a rare compound shape for a self-contained recursive
    step (the primitive this function reuses to parse the outer text also
    parses each nested one). Never raises: an unparsable nested body
    contributes nothing rather than aborting the whole call.
    """
    cleaned, nested_bodies = _strip_heredoc_bodies(command)
    normalized = _normalize_newlines(cleaned)
    try:
        results = _walk_command_directories(normalized, cwd)
    except ValueError:
        fallback = _fallback_touched_directory(normalized, cwd)
        results = [fallback] if fallback is not None else []
    for body in nested_bodies:
        results.extend(_parse_commit_dirs(body, cwd))
    return results


def touched_directories(event: dict) -> list[Path]:
    """Every directory a tool call would mutate/commit into.

    An Edit/Write-family call resolves to exactly one directory (the
    target's parent). A Bash call resolves to every ``git ... commit``
    directory `_parse_commit_dirs` finds, including inside any
    interpreter-fed heredoc script.
    """
    target = mutating_target(event)
    if target is not None:
        return [target if target.is_dir() else target.parent]
    if str(event.get("tool_name", "")) != "Bash":
        return []
    tool_input = event.get("tool_input") or {}
    cwd = str(event.get("cwd") or os.getcwd())
    raw_command = str(tool_input.get("command", ""))
    return _parse_commit_dirs(raw_command, cwd)


def touched_directory(event: dict) -> Path | None:
    """The FIRST directory `touched_directories` would resolve, or None.

    Kept for callers that only need one directory to look at (unit tests
    written against the pre-multi-commit contract, and any future caller
    that genuinely only cares about "is there a git-commit shape at all").
    """
    dirs = touched_directories(event)
    return dirs[0] if dirs else None
