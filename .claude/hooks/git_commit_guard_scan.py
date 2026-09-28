"""Reading YAML this scanner is allowed to read -- the LOWEST layer.

`git_commit_guard.py` decides and enforces; `git_commit_guard_posture.py`
resolves a posture; this module answers the two text questions both of
them rest on: could this file be declaring `guards.enforcement.on_error`
at all, and is its YAML a shape a line scanner may claim to have read.

Split out because those two questions grew their own body of rules --
YAML's comment rule, its quoting and escaping rules, its explicit-key
syntax -- and they are the rules a security review keeps finding gaps in,
so they earn a file where they can be read in one sitting.

Imports NOTHING from either module above it: the dependency runs one way,
so this can be read and tested on its own and cannot create a cycle.

Runtime contract: stdlib only, plain ``python3``, no ``bpsai_pair``
import -- same as every other script in this payload; it runs on operator
machines with no PairCoder venv.
"""

from __future__ import annotations

import re

# Hand-scanned, not `yaml.safe_load`: this script's runtime contract is
# stdlib-only plain `python3` on an operator machine with no PairCoder
# venv, so PyYAML may not exist.
# Value prefixes this scanner cannot model: block and folded scalars
# (`|`, `>`), anchors and aliases (`&`, `*`), flow collections (`{`, `[`),
# a bare flow CLOSER with nothing to close (`}`, `]` -- never valid at
# the start of a value under any YAML reading), and quoted scalars. Each
# of them means the TEXT on the line is not the VALUE -- the whole
# premise of a line scanner.
UNMODELLABLE_VALUE_PREFIXES = ("|", ">", "&", "*", "{", "[", "}", "]", '"', "'")

# --- proving ABSENCE --------------------------------------------------
#
# `unset` is the ONE state that lets a user-level `fail-open` override a
# repo-tracked `fail-closed`, so it is the one answer this scanner may
# never reach by simply failing to recognise something. Successive review
# rounds each found another YAML shape a blacklist of "constructs we know
# we cannot read" had not heard of -- a key spelled with escapes
# (`"on\\x5ferror"`), an explicit `?` inside a flow mapping, a `\\"` fooling
# the comment stripper. So the test is inverted: absence must be PROVED
# from the text, and everything not proved resolves `unreadable`. A wrong
# `unreadable` costs an operator one notice and the DEFAULT posture; a
# wrong `unset` drops a repo's tightening.

POSTURE_KEY_TOKEN = "on_error"

# In KEY position any of these means the key's NAME cannot be read off
# the source text: a quoted scalar may spell it with escapes, an explicit
# `?` puts its colon on another line, a leading `:` IS that other line,
# and tags, anchors, aliases and merge keys all name something defined
# elsewhere. Absence cannot be proved on such a line.
AMBIGUOUS_KEY_START = (
    '"',
    "'",
    "?",
    ":",
    "&",
    "*",
    "!",
    "<",
    "|",
    ">",
    "{",
    "[",
    "%",
    "`",
    "@",
    "\\",
)

# The same indicators anywhere on a line, minus `:` (which every ordinary
# `key: value` line carries): with one of these present the line's
# structure is a guess, so a bare mention of the key on it cannot be
# shown to be prose rather than a declaration.
AMBIGUOUS_ON_LINE = tuple(char for char in AMBIGUOUS_KEY_START if char != ":")

# YAML's EXPLICIT key indicator: `? key` then `: value`, the key and its
# colon on different lines. Matched at line start AND after a flow `{`,
# `,` or `[`, because YAML allows it in both places.
EXPLICIT_KEY_INDICATOR = re.compile(r"(?:^|[{,\[])\s*\?(?:\s|$)")


def _strip_comment(line: str) -> str:
    """*line* without its trailing comment, by YAML's rules.

    Not `split("#", 1)`: a `#` opens a comment only at the start of a
    line or after whitespace, never inside a quoted scalar, and a
    backslash inside a double-quoted scalar escapes the next character
    (so `"x\\" # y"` is one scalar, not a scalar and a comment). Every one
    of those cases matters because this feeds the ABSENT gate -- text
    thrown away here is a declaration the gate never sees.
    """
    quote = ""
    index = 0
    while index < len(line):
        char = line[index]
        if quote == '"' and char == "\\":
            index += 2
            continue
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index]
        index += 1
    return line


def _key_candidate(line: str, index: int) -> str:
    """*line* from *index*, past indentation and any list dashes."""
    candidate = line[index:].lstrip(" ")
    while candidate[:2] == "- ":
        candidate = candidate[2:].lstrip(" ")
    return candidate


# A single pathological line -- hand-crafted, not something `safe_dump`
# ever writes -- makes the per-position rescans below cost quadratic
# time: every `{`/`,` position re-slices toward the end of the line, and
# `str.rstrip`/`in` are C-optimized enough that the quadratic shape stays
# cheap well past any real config's line length. Measured, not assumed:
# a hand-crafted multi-megabyte line is where it stops being cheap
# (seconds, then tens of seconds), so the fix is a ceiling well above
# anything a real config carries (a few hundred characters per line) and
# well below where the quadratic cost becomes a stall -- a line past it
# resolves unreadable/ambiguous instead of being scanned at all.
MAX_MODELLABLE_LINE_LENGTH = 4096


def _unreadable_key_position(line: str) -> bool:
    """Does *line* open a key whose name this scanner cannot establish?

    Key positions are the start of the line and every point just after a
    flow `{` or `,`. Deliberately NOT quote-aware: a `{` or `,` inside a
    quoted scalar invents a key position that is not there, which
    over-reports ambiguity -- the safe direction, and the one thing a
    scanner arguing about quoting rules should not get wrong twice.

    A candidate is only a KEY when a colon follows it on the same line;
    otherwise it is a sequence entry or a scalar, and `- '*.log'` is not
    a key this scanner failed to read. The explicit `?` is the exception
    -- its colon is on the NEXT line, which is exactly why it is here.
    """
    if len(line) > MAX_MODELLABLE_LINE_LENGTH:
        return True
    positions = [0] + [i + 1 for i, char in enumerate(line) if char in "{,"]
    for index in positions:
        candidate = _key_candidate(line, index)
        if candidate[:1] not in AMBIGUOUS_KEY_START:
            continue
        if candidate[:1] == "?" or ":" in candidate:
            return True
    return False


def _mention_is_plain_text(line: str, start: int) -> bool:
    """Is the `on_error` at *start* in *line* ordinary text, provably?

    Two proofs, both local to the line and both narrow. It is part of a
    longer identifier (`not_on_error`, `on_error_mode`), so it names a
    different key. Or it sits inside a plain scalar VALUE: after some
    other key's `": "`, with no colon of its own after it, on a line
    carrying none of the indicators that would make its structure a
    guess. Anything else is a mention this scanner cannot rule out.
    """
    end = start + len(POSTURE_KEY_TOKEN)
    before = line[start - 1] if start else ""
    after = line[end : end + 1]
    if before.isalnum() or before == "_":
        return True
    if after.isalnum() or after in ("_", "-"):
        return True
    if any(char in line for char in AMBIGUOUS_ON_LINE):
        return False
    return ": " in line[:start] and ":" not in line[end:]


# --- decoding what a double-quoted scalar actually says ---------------
#
# YAML's double-quoted style is the ONLY one that can spell a key with
# text that is not literally in the file: `"on\\x5ferror"` is the key
# `on_error`, and a trailing backslash folds the line break away so
# `"on_\\<newline>error"` is too. Single-quoted scalars have no escapes at
# all (only `''` for a quote) and plain ones are literal, so both are
# already covered by a plain-text search.
#
# This matters in the safe direction: it is what lets a file with NO
# occurrence of the token be declared absent without inspecting its
# structure at all. `yaml.safe_dump` writes every description containing
# a non-ASCII character in exactly this style, folded, so "a backslash
# anywhere means unreadable" would fire on ordinary scaffolded repos.
DQ_ESCAPES = {
    "0": "\x00",
    "a": "\x07",
    "b": "\x08",
    "t": "\t",
    "\t": "\t",
    "n": "\n",
    "v": "\x0b",
    "f": "\x0c",
    "r": "\r",
    "e": "\x1b",
    " ": " ",
    '"': '"',
    "/": "/",
    "\\": "\\",
    "N": "\x85",
    "_": "\xa0",
    "L": "\u2028",
    "P": "\u2029",
}

DQ_HEX_ESCAPES = {"x": 2, "u": 4, "U": 8}

# Where a `"` can actually OPEN a scalar. Anywhere else it is content --
# an apostrophe-style quote inside prose, say -- and treating it as an
# opener would only invent decode failures.
DQ_OPENS_AFTER = " \t\n{[,:-?"


def _normalize_breaks(text: str) -> str:
    """*text* with CRLF and lone-CR line breaks written as ``\n``.

    The line-based half of this scanner gets this free from
    ``str.splitlines``; the character walk below does not, and YAML's
    fold is a backslash followed by a LINE BREAK. Under CRLF the next
    character is ``\r``, which is not an escape -- so every ordinary
    folded description in a Windows or ``autocrlf`` checkout would read
    as an undecodable scalar. Normalized once, at the entry point, so
    both halves answer about the same document.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _decode_double_quoted(text: str, start: int) -> tuple:
    """``(decoded scalar, index past it)`` for the scalar at *start*.

    ``(None, -1)`` when it cannot be decoded -- an unknown escape, a short
    hex escape, or no closing quote. That is a REFUSAL, not a guess: a
    scalar this cannot read is one it cannot rule out.
    """
    out: list = []
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == '"':
            return "".join(out), index + 1
        if char == "\\":
            index += 1
            escape = text[index : index + 1]
            if escape == "\n":
                index += 1
                while index < len(text) and text[index] in " \t":
                    index += 1
                continue
            width = DQ_HEX_ESCAPES.get(escape)
            if width:
                digits = text[index + 1 : index + 1 + width]
                if len(digits) < width:
                    return None, -1
                try:
                    out.append(chr(int(digits, 16)))
                except ValueError:
                    return None, -1
                index += 1 + width
                continue
            if escape not in DQ_ESCAPES:
                return None, -1
            out.append(DQ_ESCAPES[escape])
            index += 1
            continue
        if char == "\n":
            # An ordinary break folds to one space, whitespace on either
            # side of it dropped.
            while out and out[-1] in " \t":
                out.pop()
            out.append(" ")
            index += 1
            while index < len(text) and text[index] in " \t":
                index += 1
            continue
        out.append(char)
        index += 1
    return None, -1


# Where a plain scalar ends INSIDE a flow collection. In block context
# these are ordinary text -- `note: yes, 'tis so` is one scalar -- and
# ending the scalar at the comma re-arms the walk mid-sentence, so the
# apostrophe after it opens a scalar that runs to the next apostrophe
# anywhere in the file and swallows whatever lies between.
PLAIN_STOPS = ",}]{["


def _flow_step(char: str, flow_depth: int) -> tuple:
    """``(flow depth, at a scalar start)`` after structural punctuation.

    Its own function only so the walk below stays readable: the depth is
    what separates "inside `{...}`, where a comma is punctuation" from
    "in block context, where a comma is a letter".
    """
    if char in "{[":
        return flow_depth + 1, True
    if char in "}]":
        return max(flow_depth - 1, 0), False
    if char == ",":
        return flow_depth, flow_depth > 0
    return flow_depth, True


def _multiline_scalar_indent_ok(text: str, start: int, end: int) -> bool:
    """Does the scalar spanning ``text[start:end]`` stay indented?

    Every line after the first of a multi-line quoted scalar must not be
    SHALLOWER than its opener -- but PyYAML accepts EQUAL indent (checked
    against `yaml.safe_load`, not assumed -- the stricter `<=`
    reading a false `undecodable` on an ordinary equal-indent
    description). The rule still earns its place as a SECOND opinion: a
    phantom scalar -- one opened where the walk lost its place -- swallows
    whole block mappings, whose lines run back out SHALLOWER than the
    opener, which this still catches even when the accounting leg is
    satisfied by the phantom span itself.
    """
    line_start = text.rfind("\n", 0, start) + 1
    head = text[line_start:start]
    opener_indent = len(head) - len(head.lstrip(" "))
    for line in text[start:end].split("\n")[1:]:
        if line.strip() and len(line) - len(line.lstrip(" ")) < opener_indent:
            return False
    return True


def _consume_plain(text: str, index: int, in_flow: bool = False) -> int:
    """Index just past the plain scalar starting at *index*.

    Plain scalars are where a stray `"` most often hides -- `note: he said
    "hi` is one string to YAML. Ending it correctly is what keeps that
    quote from being read as an opener, and ending it at the RIGHT place
    is what keeps ordinary prose punctuation from ending it early.
    """
    while index < len(text):
        char = text[index]
        if char == "\n" or (in_flow and char in PLAIN_STOPS):
            return index
        if char == "#" and index and text[index - 1] in " \t":
            return index - 1
        if char == ":" and text[index + 1 : index + 2] in ("", " ", "\t", "\n"):
            return index
        index += 1
    return index


def _consume_single_quoted(text: str, index: int) -> int:
    """Index just past the single-quoted scalar opening at *index*.

    ``-1`` when it never closes. YAML gives these no escapes at all, only
    `''` for a literal quote -- so nothing inside one can spell a key that
    plain text does not, but plenty inside one can derail a scanner that
    does not know it is there.
    """
    index += 1
    while index < len(text):
        if text[index] == "'":
            if text[index + 1 : index + 2] == "'":
                index += 2
                continue
            return index + 1
        index += 1
    return -1


def _consume_block_scalar(text: str, index: int) -> int:
    """Index just past the `|`/`>` block whose header starts at *index*."""
    line_start = text.rfind("\n", 0, index) + 1
    # A short local, not the expression inline: the payload must be
    # `ruff format` clean at 88 AND at a consumer's 100, and the inline
    # form splits at one and collapses at the other.
    header = text[line_start:index]
    header_indent = len(header) - len(header.lstrip(" "))
    index = text.find("\n", index)
    if index < 0:
        return len(text)
    for line in text[index + 1 :].split("\n"):
        if line.strip() and len(line) - len(line.lstrip(" ")) <= header_indent:
            break
        index += len(line) + 1
    return min(index + 1, len(text))


def _double_quoted_scalars(text: str) -> tuple:
    """``(every decoded double-quoted scalar, the walk stayed in step)``.

    Two legs, deliberately independent, because a `"` inside a single-
    quoted, plain or block scalar is content to YAML but would be an
    OPENER to a walker that only models double quotes: an odd number of
    them pairs the stray quote with a real key's opening quote and
    swallows the key, and the file then reads as declaring nothing.

    The first leg is context: every scalar style is recognized and skipped
    at the position where it can start, so a quote inside one is never an
    opener. The second is accounting: every backslash in the document must
    land inside some scalar or comment this walk recognized. YAML
    structure carries no backslashes, so one in structure position is the
    wreckage of a walk that lost its place -- whatever caused it.
    """
    scalars: list = []
    accounted: list = []
    index = 0
    at_scalar = True
    flow_depth = 0
    while index < len(text):
        char = text[index]
        if char == "#" and (index == 0 or text[index - 1] in " \t\n"):
            end = text.find("\n", index)
            end = len(text) if end < 0 else end
            accounted.append((index, end))
            index = end
            continue
        if char in " \t":
            index += 1
            continue
        if char == "\n" or char in "{[,}]":
            flow_depth, at_scalar = _flow_step(char, flow_depth)
            index += 1
            continue
        if char in ":-?" and text[index + 1 : index + 2] in ("", " ", "\t", "\n"):
            at_scalar = True
            index += 1
            continue
        if at_scalar and char in "&*!":
            # An anchor, alias or tag is a PROPERTY of the node, not the
            # node -- the scalar it decorates still follows.
            while index < len(text) and text[index] not in " \t\n":
                index += 1
            continue
        if not at_scalar:
            index += 1
            continue
        if char == '"':
            decoded, end = _decode_double_quoted(text, index)
            if decoded is None or not _multiline_scalar_indent_ok(text, index, end):
                return tuple(scalars), False
            scalars.append(decoded)
            accounted.append((index, end))
            index, at_scalar = end, False
            continue
        if char == "'":
            end = _consume_single_quoted(text, index)
            if end < 0 or not _multiline_scalar_indent_ok(text, index, end):
                return tuple(scalars), False
            accounted.append((index, end))
            index, at_scalar = end, False
            continue
        if char in "|>":
            end = _consume_block_scalar(text, index)
            accounted.append((index, end))
            index, at_scalar = end, True
            continue
        end = _consume_plain(text, index, flow_depth > 0)
        accounted.append((index, end))
        index, at_scalar = max(end, index + 1), False
    return tuple(scalars), _escapes_all_accounted_for(text, accounted)


def _escapes_all_accounted_for(text: str, regions: list) -> bool:
    """Does every backslash in *text* lie inside a region the walk knew?

    The independent leg. YAML structure -- indentation, `:`, `-`, flow
    punctuation -- carries no backslashes, so one outside every scalar and
    comment the walk recognized means the walk is not where it thinks it
    is, and nothing may be concluded about what the document declares.
    Ordinary `safe_dump` fold output keeps every backslash inside a scalar,
    which is what makes this affordable.
    """
    position = 0
    for start, end in regions:
        if "\\" in text[position:start]:
            return False
        position = max(position, end)
    return "\\" not in text[position:]


# What a search for the key found, and therefore what may be concluded.
PROOF_ABSENT = "absent"  # nothing in this file can be declaring the key
PROOF_TOKEN = "token"  # the literal token is here; the line rules decide
PROOF_ESCAPED = "escaped"  # a double-quoted scalar DECODES to the token
PROOF_UNDECODABLE = "undecodable"  # a double-quoted scalar cannot be read


def _absent_proof(text: str) -> str:
    """Can *text* be shown to declare nothing, and if not, why not?

    YAML cannot synthesize a mapping key out of text that is not in the
    document: a key's name appears literally, or inside a double-quoted
    scalar as escapes that decode to it. Aliases, merge keys, tags and
    anchors all point at a node whose key text is written somewhere. So a
    file with neither is absent, whatever else its structure looks like --
    and the structural rules have nothing to decide.
    """
    document = _normalize_breaks(text)
    if any(POSTURE_KEY_TOKEN in _strip_comment(raw) for raw in document.splitlines()):
        return PROOF_TOKEN
    decoded, complete = _double_quoted_scalars(document)
    if not complete:
        return PROOF_UNDECODABLE
    if any(POSTURE_KEY_TOKEN in scalar for scalar in decoded):
        return PROOF_ESCAPED
    return PROOF_ABSENT


def _token_line_scan(text: str) -> bool:
    """Could *text* carry a `guards.enforcement.on_error` declaration?

    The inverse of a proof of absence, and the ABSENT gate's whole
    implementation: this returns False -- the answer that lets a
    user-level `fail-open` through -- only for a file every line of which
    is plainly readable AND carries no mention of the key that is not
    demonstrably something else.

    A file with a document marker, a tab or any non-ASCII whitespace, a
    key this scanner cannot name, or a mention it cannot place, reads as
    a declaration it must go on to resolve -- which, for all of those
    shapes, means `unreadable`: fail-closed and announced.
    """
    for raw in text.splitlines():
        if raw.strip() in ("---", "...") or raw.startswith("--- "):
            return True
        if len(raw) > MAX_MODELLABLE_LINE_LENGTH:
            return True
        line = _strip_comment(raw)
        if any(char.isspace() and char != " " for char in line):
            return True
        if _unreadable_key_position(line):
            return True
        found = line.find(POSTURE_KEY_TOKEN)
        while found != -1:
            if not _mention_is_plain_text(line, found):
                return True
            found = line.find(POSTURE_KEY_TOKEN, found + 1)
    return False


# `bpsai-pair init` scaffolds bare `[]`/`{}` throughout a config
# (`blocked_directories: []`, `roles: {}`), so treating every flow
# collection in the file as unmodellable made an ordinary scaffolded repo
# unable to declare the key at all. A collection that
# opens and closes on ONE physical line cannot leak a continuation into
# the per-line matcher below -- it has no concept of flow depth, so it
# only ever reads a line's OWN first colon, and a single-line flow value
# is consumed whole as that line's value, never inspected. A collection
# that spans a line break is not proven safe this way: a continuation
# line can look like a fresh block-style key to that same matcher, so it
# stays unmodellable, same as before.
FLOW_COLLECTION_PREFIXES = ("{", "[")


# Which closer each opener demands. A bare depth COUNT balances `{]` --
# wrong closer, same net change -- so the walk below tracks a stack of
# expected closers instead: type has to match, not just count.
_FLOW_CLOSER_FOR = {"{": "}", "[": "]"}


def _flow_collection_closes_on_this_line(value: str) -> bool:
    """Does the flow collection opening *value* close before *value* ends,
    with nothing but whitespace left over?

    A small, independent bracket walk -- not the main character walk
    above, which tracks a whole document's flow depth for a different
    purpose (deciding where a PLAIN scalar ends). This only has to answer
    one local question, so it reuses the quoted-scalar consumers to skip
    over bracket characters that are quoted CONTENT rather than flow
    punctuation, and gives up (False, the safe answer) on anything it
    cannot close within *value* itself.

    Balanced by COUNT is not the same as well-formed: `{]` balances a
    bare depth counter (one open, one close) but no YAML parser accepts a
    `{` closed by `]`, and `{} garbage` returns to depth zero with real
    content still trailing. Both let a document `yaml.safe_load` rejects
    outright read as an ordinary line. The
    closer stack below requires the TYPE to match, and once it empties
    -- the outer collection's own close -- anything left in *value* that
    is not whitespace means this is not the whole value, so it is not
    safe to read past.
    """
    closers: list = []
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char == '"':
            _decoded, end = _decode_double_quoted(value, index)
            if end < 0:
                return False
            index = end
            continue
        if char == "'":
            end = _consume_single_quoted(value, index)
            if end < 0:
                return False
            index = end
            continue
        if char in _FLOW_CLOSER_FOR:
            closers.append(_FLOW_CLOSER_FOR[char])
            index += 1
            continue
        if char in "}]":
            if not closers or closers[-1] != char:
                return False
            closers.pop()
            index += 1
            if not closers:
                return value[index:].strip() == ""
            continue
        index += 1
    return False


def _unmodellable(text: str) -> bool:
    """True when *text* uses YAML this scanner cannot read faithfully.

    Deliberately blunt: one unmodellable construct anywhere in the file
    makes every answer about that file a guess. A guess in the loosening
    direction is the whole failure class this gate exists to prevent, and
    a guess that happens to be right is still a guess -- so the file reads
    as unreadable, which is fail-closed AND announced.
    """
    if "\t" in text:
        return True
    for raw in text.splitlines():
        if raw.strip() in ("---", "...") or raw.startswith("--- "):
            return True
        if len(raw) > MAX_MODELLABLE_LINE_LENGTH:
            return True
        if EXPLICIT_KEY_INDICATOR.match(raw):
            return True
        line = _strip_comment(raw).rstrip()
        if ":" not in line:
            continue
        _key, _, value = line.strip().partition(":")
        value = value.strip()
        prefix = value[:1]
        if prefix in FLOW_COLLECTION_PREFIXES:
            if not _flow_collection_closes_on_this_line(value):
                return True
            continue
        if prefix in UNMODELLABLE_VALUE_PREFIXES:
            return True
    return False


def _declares_the_key(text: str) -> bool:
    """Could *text* carry a `guards.enforcement.on_error` declaration?

    Two gates in order. The first asks whether the key's NAME is in the
    document at all -- as plain text, or as escapes that decode to it. A
    file with neither is absent outright, and the structural rules below
    never run: they exist to place a token that is there, and a config
    that never mentions the key was being refused for punctuation in an
    unrelated multi-line scalar.

    The second is the line scan: given a token, is every occurrence of it
    demonstrably something other than the declaration, on a line whose
    structure this scanner can read?
    """
    proof = _absent_proof(text)
    if proof != PROOF_TOKEN:
        return proof != PROOF_ABSENT
    return _token_line_scan(text)
