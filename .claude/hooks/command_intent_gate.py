#!/usr/bin/env python3
"""UserPromptSubmit hook: command-vocabulary intent gate.

A prompt like ``engage backlog.md`` or ``review pr 42`` is an *invocation of a
named command with a contract*, not a natural-language request. Read as prose,
a session substitutes its own defaults for the contract: it works the backlog
inline instead of dispatching, or reads a diff inline instead of running the
review pipeline. Prose in CLAUDE.md does not hold against that -- so the
contract is injected deterministically, by pattern match, every time.

Protected shapes live in ``command_intents.json`` next to this file. Adding a
row there is a data edit; this module is only the matcher.

Runtime contract:
  * stdlib only, plain ``python3`` -- runs on operator machines with no
    PairCoder venv and no ``bpsai_pair`` import available.
  * always exits 0. A UserPromptSubmit hook exiting 2 would BLOCK the prompt;
    an advisory gate must never do that, so every failure path is fail-open.

ESCAPE HATCH (audited bypass): remove this hook's entry from
``.claude/settings.json``. That deletion is visible in ``git diff`` and in
review -- which is the point. Do not add a silent env-var kill switch.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TABLE_PATH = Path(__file__).with_name("command_intents.json")
HOOK_EVENT = "UserPromptSubmit"


def load_intents(table_path: Path) -> list[dict] | None:
    """Return the protected-command rows, or ``None`` if the table itself
    is unusable (missing, unreadable, malformed JSON, wrong top-level
    shape, or no ``intents`` list) -- distinct from ``[]``, a well-formed
    table that legitimately configures zero protected commands. Collapsing
    both into ``[]`` made a broken table read exactly like an intentional
    empty vocabulary, with no trace of the failure anywhere.
    """
    try:
        data = json.loads(table_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    intents = data.get("intents")
    if not isinstance(intents, list):
        return None
    return [row for row in intents if isinstance(row, dict)]


def first_command_line(prompt: str) -> str:
    """First non-empty line of *prompt*, stripped of whitespace and one slash.

    Matching only the first line keeps a follow-up paragraph ("...and use
    haiku for the mechanical lane") from defeating the shape match, while
    still requiring the *invocation itself* to lead the prompt.
    """
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[1:].strip() if stripped.startswith("/") else stripped
    return ""


def match_intent(prompt: str, intents: list[dict]) -> dict | None:
    """Return the first intent row whose command shape *prompt* matches.

    Command-shaped means: the row's keyword is the leading token, and the
    remainder of that line fully matches the row's ``argument_pattern``. A
    prose mention ("the review went well", "review the diff and tell me
    what you think") fails one of those two tests and never fires.
    """
    line = first_command_line(prompt)
    if not line:
        return None
    head, _, remainder = line.partition(" ")
    remainder = remainder.strip()
    if not remainder:
        return None
    for row in intents:
        keyword = str(row.get("keyword", ""))
        pattern = str(row.get("argument_pattern", ""))
        if not keyword or not pattern:
            continue
        if head.lower() != keyword.lower():
            continue
        try:
            if re.fullmatch(pattern, remainder, re.IGNORECASE):
                return row
        except re.error:
            continue
    return None


def build_output(row: dict) -> str:
    """Serialize the additionalContext envelope Claude Code expects."""
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": HOOK_EVENT,
                "additionalContext": str(row.get("contract", "")),
            }
        }
    )


def main() -> int:
    """Read the hook event on stdin; emit the contract on a shape match."""
    try:
        event = json.loads(sys.stdin.read() or "{}")
        prompt = event.get("prompt", "") if isinstance(event, dict) else ""
        intents = load_intents(TABLE_PATH)
        if intents is None:
            sys.stderr.write(
                f"command_intent_gate: classification table unreadable/"
                f"invalid ({TABLE_PATH}) -- gate disarmed for this prompt\n"
            )
            return 0
        row = match_intent(str(prompt), intents)
        if row is not None:
            sys.stdout.write(build_output(row))
    except Exception as exc:  # fail open: never block a prompt
        sys.stderr.write(f"command_intent_gate: fail-open ({exc})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
