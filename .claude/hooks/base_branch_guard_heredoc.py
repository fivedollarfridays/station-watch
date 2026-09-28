"""Heredoc-body handling for the Bash command-text parser.

Split out of ``base_branch_guard_parser.py`` to keep that module under
the architecture caps (RB core round: interpreter-fed heredoc support
pushed the combined logic over the file-size warning threshold).

A heredoc body is free-form text a real shell defers to a LATER parse
phase, entirely outside quote/operator scanning -- fed straight to a flat
quote-tracking tokenizer, a quote CHARACTER inside the body (not a real
shell quote at all) can close an outer quoted argument early, desyncing
everything after it. ``strip_heredoc_bodies`` locates each heredoc's body
span by its own operator/terminator grammar (regex, not quote-state) and
excises it wholesale before any quote tracking runs.

ONE CLASS OF BODY IS NOT DATA, THOUGH: when the CONSUMING COMMAND is a
shell interpreter (``bash``/``sh``/``zsh``/``dash``/``ksh``, ``env
<interpreter>``, or a path-qualified/case-varied spelling of one -- see
``INTERPRETER_BASENAMES``), the body is the SCRIPT that interpreter
executes: a ``git commit`` inside a ``bash <<EOF`` body is a REAL commit,
not prose. Excising it (as every other consumer's body is excised) would
trade the quoted-prose false-positive fix for a false NEGATIVE -- blinding
the guard to a genuine protected-branch commit. So an interpreter-fed
body is STILL excised from the text handed to the OUTER tokenizer (its
content could still desync that parse the same way any other body's
could), but ``strip_heredoc_bodies`` also returns its text separately, so
the caller (``base_branch_guard_parser._parse_commit_dirs``) can re-parse
it RECURSIVELY through the same segment/commit detection, as its own
independent command string.

An unrecognized or ambiguous consumer (anything not in
``INTERPRETER_BASENAMES``) keeps the excise-only treatment -- the
advisory layer's established direction is to fail toward NOT matching
prose; ``git_commit_guard.py``'s git-native pre-commit layer is the real
enforcement backstop for whatever a wrong guess here misses.
"""

from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath, PureWindowsPath

from base_branch_guard_constants import (
    CONTROL_OPERATOR_TOKENS,
    INTERPRETER_BASENAMES,
    skip_env_assignments,
)

# `<<TAG`, `<<'TAG'`, `<<"TAG"`, `<<-TAG` -- the operator + tag grammar
# only; the body/terminator span is located separately, per operator.
# Kept split: collapses to a 94-char line at line-length=100.
HEREDOC_OPEN_RE = re.compile(
    r"<<-?[ \t]*(?P<q>['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)(?P=q)"
)  # fmt: skip


def is_interpreter_token(token: str) -> bool:
    """True when *token* denotes a shell interpreter (see
    `INTERPRETER_BASENAMES`), however it is spelled -- bare, a
    relative/absolute POSIX or Windows path ending in it, any case
    variant, or the ``.exe`` suffix. Lexical only, basename-matching.
    """
    for path_cls in (PurePosixPath, PureWindowsPath):
        lowered = path_cls(token).name.lower()
        if lowered.endswith(".exe"):
            lowered = lowered[: -len(".exe")]
        if lowered in INTERPRETER_BASENAMES:
            return True
    return False


def consumer_command_name(line_prefix: str) -> str | None:
    """The command name a heredoc operator at the END of *line_prefix*
    would feed -- the first non-flag token of the LAST simple-command
    segment on that physical line (after the most recent control
    operator, env-assignment prefixes skipped), or None if unparsable /
    empty. ``env <interpreter>`` resolves through to ``<interpreter>``,
    skipping ``env``'s own leading flags (e.g. ``env -i bash``).
    """
    try:
        lexer = shlex.shlex(line_prefix, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    last_boundary = -1
    for i, tok in enumerate(tokens):
        if tok in CONTROL_OPERATOR_TOKENS:
            last_boundary = i
    segment = tokens[last_boundary + 1 :]
    index = skip_env_assignments(segment, 0)
    if index >= len(segment):
        return None
    candidate = segment[index]
    if PurePosixPath(candidate).name.lower() == "env":
        index += 1
        while index < len(segment) and segment[index].startswith("-"):
            index += 1
        if index >= len(segment):
            return None
        candidate = segment[index]
    return candidate


# Kept split: collapses to a 99-char line at line-length=100.
def _heredoc_tags_on_line(
    command: str, first_tag: str, after: int, line_end: int
) -> list[str]:  # fmt: skip
    """Every heredoc tag opened on the SAME physical line, in order --
    `cmd <<A <<B` feeds two bodies, read consecutively, to one consumer.
    """
    tags = [first_tag]
    cursor = after
    while True:
        nxt = HEREDOC_OPEN_RE.search(command, cursor, line_end)
        if nxt is None:
            return tags
        tags.append(nxt.group("tag"))
        cursor = nxt.end()


def _heredoc_body_spans(command: str, tags: list[str], body_start: int):
    """``(spans, end)`` for *tags* read consecutively from *body_start* --
    each span is ``(body_start, term_start, term_line_end)``; *spans* is
    ``None`` if ANY tag's terminator is missing (the whole block is then
    left unstripped, same "unterminated -> leave as-is" doctrine as a
    single heredoc).
    """
    spans = []
    cursor = body_start
    for tag in tags:
        terminator_re = re.compile(rf"^[ \t]*{re.escape(tag)}[ \t]*$", re.MULTILINE)
        term_match = terminator_re.search(command, cursor)
        if term_match is None:
            return None, None
        term_line_end = command.find("\n", term_match.end())
        term_line_end = term_line_end + 1 if term_line_end != -1 else len(command)
        spans.append((cursor, term_match.start(), term_line_end))
        cursor = term_line_end
    return spans, cursor


def strip_heredoc_bodies(command: str) -> tuple[str, list[str]]:
    """Excise every heredoc BODY, returning the cleaned text plus the body
    text of every heredoc whose consumer is a shell interpreter (see the
    module docstring) -- the caller re-parses each of those recursively.

    Located by the heredoc's own grammar (regex over the raw text), never
    by quote-tracking state. Walks EVERY heredoc operator on a line, in
    order (`cmd <<A <<B` opens two), not just the first.
    """
    out = []
    nested_bodies: list[str] = []
    pos = 0
    scan_pos = 0
    while True:
        match = HEREDOC_OPEN_RE.search(command, scan_pos)
        if match is None:
            break
        line_start = command.rfind("\n", 0, match.start()) + 1
        line_end = command.find("\n", match.end())
        if line_end == -1:
            break  # operator is on the last line -- no body possible
        tags = _heredoc_tags_on_line(command, match.group("tag"), match.end(), line_end)
        spans, block_end = _heredoc_body_spans(command, tags, line_end + 1)
        if spans is None:
            scan_pos = match.end()
            continue  # unterminated -- leave this block as-is

        is_interpreter = False
        consumer = consumer_command_name(command[line_start : match.start()])
        if consumer is not None:
            is_interpreter = is_interpreter_token(consumer)

        out.append(command[pos : line_end + 1])
        for body_start, term_start, term_line_end in spans:
            if is_interpreter:
                nested_bodies.append(command[body_start:term_start])
            out.append(command[term_start:term_line_end])
        pos = block_end
        scan_pos = block_end
    out.append(command[pos:])
    return "".join(out), nested_bodies
