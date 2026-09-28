#!/usr/bin/env python3
"""PreToolUse hook (matcher ``Agent``): dispatch-contract WARN-FIRST gate.

An Agent-tool dispatch with an implementation-shaped brief (a driver or
general-purpose subagent asked to write/fix/build/ship something) bypasses
every governed gate the ``engage`` CLI enforces on task-file dispatch --
there is no task id, no registered acceptance criteria, no lifecycle. This
hook does not refuse that dispatch (see MODE below); it makes the gap
visible, every time, deterministically.

A dispatch CARRIES a contract when its prompt embeds the fenced block
documented in ``docs/orchestration/dispatch-contract.md``::

    --- dispatch-contract ---
    task: <id-or-issue>
    repo: <target>
    verification: <gate battery>
    --- end ---

Classification table lives in ``dispatch_intents.json`` next to this file
(implementation-shaped subagent types, the implementation-verb pattern, the
contract-fence pattern). Adding/removing a type or verb is a data edit; this
module is only the matcher.

MODE (warn+log only, today): an implementation-shaped dispatch with no
contract block gets an injected ``additionalContext`` warning (the same
envelope shape ``command_intent_gate.py`` uses for ``UserPromptSubmit``) and
a logged row -- it is NEVER refused. ``dispatch_intents.json``'s ``arming``
key is the documented flip point for a future fail-closed mode; the code
does not implement any value other than ``warn`` yet.

STAMPING SEAM (for downstream telemetry rollups): every warning fired here
is ALSO appended, best-effort, to
``<repo_root>/.paircoder/history/intervention_events.jsonl`` -- one JSON
object matching ``bpsai_pair.orchestration.intervention_events``'s on-disk
schema exactly: ``{"timestamp", "event_class": "gate_warning", "task_id",
"detail", "metadata"}``. This hook is stdlib-only (see below) and cannot
import that module to call ``write_intervention_event`` directly, so
:func:`emit_gate_warning_row` reproduces the schema by hand -- any reader
of that file (e.g. a rollup consuming ``event_class == "gate_warning"``)
needs no import of this hook at all, only the schema. Keep the two in sync
if either shape changes.

OPT-OUT: an ``.paircoder/hooks/dispatch_contract_gate.off`` marker file
(created the same way as ``base_branch_guard.py``'s own escape hatch)
suppresses the warning for a repo. Its presence/absence is read with a
single ``os.lstat`` (:func:`_marker_kind`, copied from
``upgrade_session_hooks.py``'s ``_guard_marker_kind`` / ``guard_opted_out``
pattern rather than reinvented) so a symlinked marker reads as tampering,
not an opt-out, and the warning still fires. Suppressing a warning is a
workflow bypass under this project's fail-closed-audited-bypass doctrine:
it is only honored when the audit row lands in
``.paircoder/history/bypass_log.jsonl``; if that write fails, the warning
fires anyway (an unauditable bypass is not granted).

Runtime contract:
  * stdlib only, plain ``python3`` -- runs on operator machines with no
    PairCoder venv and no ``bpsai_pair`` import available.
  * always exits 0. A PreToolUse hook exiting 2 would DENY the tool call;
    this gate is advisory only and must never block a dispatch.

ESCAPE HATCH (audited bypass): create the marker above, or remove this
hook's entry from ``.claude/settings.json``. Both are visible in
``git diff``/``git status`` -- which is the point.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

TABLE_PATH = Path(__file__).with_name("dispatch_intents.json")
HOOK_EVENT = "PreToolUse"
TARGET_TOOL = "Agent"

OPT_OUT_MARKER = Path(".paircoder/hooks/dispatch_contract_gate.off")
BYPASS_LOG_REL = Path(".paircoder/history/bypass_log.jsonl")
INTERVENTION_LOG_REL = Path(".paircoder/history/intervention_events.jsonl")

WARNING_TEMPLATE = (
    "PairCoder dispatch-contract gate (WARN-FIRST): this Agent-tool dispatch "
    "looks implementation-shaped (subagent_type={subagent_type!r}) but the "
    "prompt carries no dispatch-contract block.\n\n"
    "Wrap the brief per docs/orchestration/dispatch-contract.md:\n\n"
    "--- dispatch-contract ---\n"
    "task: <id-or-issue>\n"
    "repo: <target>\n"
    "verification: <gate battery>\n"
    "--- end ---\n\n"
    "Advisory only -- this dispatch was NOT blocked."
)


def load_intents(table_path: Path) -> dict | None:
    """Return the classification table, or ``None`` if it is unusable
    (missing, unreadable, malformed JSON, or the wrong top-level shape) --
    distinct from ``{}``, which reads to `is_implementation_shaped` as "no
    subagent type is ever implementation-shaped", a deliberately different
    piece of information from "this table could not be read at all".
    """
    try:
        data = json.loads(table_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


WILDCARD_SUBAGENT_TYPE = "*"


def is_implementation_shaped(tool_input: dict, intents: dict) -> bool:
    """True when *tool_input* names an in-set subagent type AND an
    implementation-verb prompt.

    ``subagent_type`` absent from *tool_input* entirely (the observed
    no-explicit-type shape) reads as ``None``, matched against the
    configured ``null`` entry -- distinct from an empty string, which is
    never emitted and never configured. The sentinel ``"*"`` in the
    configured list (see ``dispatch_intents.json``'s tuning-knob comment)
    matches EVERY subagent_type, including named/repo-specific agents the
    shipped default list does not enumerate.
    """
    allowed_types = intents.get("implementation_subagent_types", [])
    subagent_type = tool_input.get("subagent_type")
    # Kept split: collapses to a 90-char line at line-length=100.
    if (
        WILDCARD_SUBAGENT_TYPE not in allowed_types
        and subagent_type not in allowed_types
    ):  # fmt: skip
        return False
    pattern = intents.get("implementation_verb_pattern", "")
    try:
        # Kept split: collapses to a 95-char line at line-length=100.
        return (
            re.search(pattern, str(tool_input.get("prompt", "")), re.IGNORECASE)
            is not None
        )  # fmt: skip
    except re.error:
        return False


def has_contract_block(prompt: str, intents: dict) -> bool:
    """True when *prompt* embeds the fenced dispatch-contract block."""
    pattern = intents.get("contract_fence_pattern", "")
    try:
        return re.search(pattern, prompt, re.IGNORECASE | re.DOTALL) is not None
    except re.error:
        return False


def extract_task_id(prompt: str, intents: dict) -> str:
    """The lane a logged warning attributes to.

    A ``task:`` line in *prompt* (inside a full contract fence, or a bare
    partial/malformed one -- a dispatch missing the closing fence still
    warns, but is not nameless) wins; otherwise every warning buckets under
    one stable, documented lane (``uncontracted_task_id_bucket``) so the
    WG.5 per-lane rollup aggregates repeated warnings instead of minting a
    singleton lane per ``tool_use_id`` (trio round-2 P1).
    """
    pattern = intents.get("task_id_pattern", "")
    try:
        match = re.search(pattern, prompt, re.IGNORECASE | re.MULTILINE)
    except re.error:
        match = None
    if match:
        value = match.group(1).strip()
        if value:
            return value
    return str(intents.get("uncontracted_task_id_bucket", "uncontracted"))


def build_output(message: str) -> str:
    """Serialize the additionalContext envelope Claude Code expects."""
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": HOOK_EVENT,
                "additionalContext": message,
            }
        }
    )


def _marker_kind(marker: Path) -> str:
    """lstat-based kind of *marker*: ``"file"``, ``"symlink"``, or ``"absent"``.

    Mirrors ``base_branch_guard.py``'s ``_marker_kind`` /
    ``upgrade_session_hooks.py``'s ``_guard_marker_kind`` exactly (one
    ``os.lstat`` read, taken fresh) so this gate's own opt-out can never
    drift onto ``Path.is_file()``'s symlink-following semantics.
    """
    try:
        mode = os.lstat(marker).st_mode
    except OSError:
        return "absent"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISREG(mode):
        return "file"
    return "absent"


def find_repo_root(start: Path) -> Path | None:
    """Walk up from *start* to the nearest ``.git`` (dir or worktree file)."""
    for directory in (start, *start.parents):
        marker = directory / ".git"
        if marker.is_dir() or marker.is_file():
            return directory
    return None


def _append_jsonl(path: Path, entry: dict) -> str | None:
    """Best-effort append; return an error string on failure, else None."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError as exc:
        return str(exc)
    return None


# Kept split: collapses to an 89-char line at line-length=100.
def log_marker_disarm(
    repo_root: Path, tool_input: dict, tool_use_id: str
) -> str | None:  # fmt: skip
    """Audit the opt-out marker suppressing a warning; error string on failure."""
    entry = {
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "command": "dispatch_contract_gate",
        "target": tool_use_id or "unknown",
        "reason": "opt-out marker present",
        "bypass_type": "dispatch_contract_gate_marker_disarm",
        "gate": "dispatch_contract_gate",
        "action": "marker_disarm",
        "tool": TARGET_TOOL,
    }
    return _append_jsonl(repo_root / BYPASS_LOG_REL, entry)


def emit_gate_warning_row(
    repo_root: Path,
    task_id: str,
    tool_use_id: str,
    subagent_type: str | None,
    message: str,
) -> str | None:
    """Append the stamping-seam row; see the module docstring's schema note.

    ``task_id`` is the ROLLUP LANE (:func:`extract_task_id`) -- NOT the
    harness ``tool_use_id``, which is per-dispatch-unique and would mint a
    singleton lane every time (trio round-2 P1). ``tool_use_id`` is still
    carried in ``metadata`` for per-dispatch traceability/dedup.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_class": "gate_warning",
        "task_id": task_id,
        "detail": message,
        "metadata": {
            "gate": "dispatch_contract_gate",
            "subagent_type": subagent_type,
            "tool_use_id": tool_use_id or "unknown",
        },
    }
    return _append_jsonl(repo_root / INTERVENTION_LOG_REL, entry)


def _last_jsonl_entry(path: Path) -> dict | None:
    """The last well-formed JSON object in *path*, or None (absent, empty,
    unreadable, or every line malformed)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            return None
        return entry if isinstance(entry, dict) else None
    return None


def is_recent_duplicate_warning(
    repo_root: Path,
    subagent_type: str | None,
    intents: dict,
) -> bool:
    """True when the LAST logged row for this repo is the same kind
    (``gate_warning``) and ``subagent_type``, within the configured window.

    Throttles the SINK only (trio round-2 P2d) -- a deduped warning still
    fires on the wire every time; only the repeat write to
    ``intervention_events.jsonl`` is skipped, one read of the last line, no
    full-file scan.
    """
    window = intents.get("warn_dedup_window_minutes", 0)
    try:
        window = float(window)
    except (TypeError, ValueError):
        return False
    if window <= 0:
        return False
    last = _last_jsonl_entry(repo_root / INTERVENTION_LOG_REL)
    if last is None:
        return False
    if last.get("event_class") != "gate_warning":
        return False
    if last.get("metadata", {}).get("subagent_type") != subagent_type:
        return False
    try:
        last_ts = datetime.fromisoformat(str(last.get("timestamp", "")))
    except ValueError:
        return False
    now = datetime.now(last_ts.tzinfo) if last_ts.tzinfo else datetime.now()
    age_minutes = (now - last_ts).total_seconds() / 60
    return 0 <= age_minutes < window


def evaluate(event: dict) -> str | None:
    """Return the warning message for *event*, or None if nothing fires."""
    if event.get("tool_name") != TARGET_TOOL:
        return None
    tool_input = event.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    intents = load_intents(TABLE_PATH)
    if intents is None:
        sys.stderr.write(
            f"dispatch_contract_gate: classification table unreadable/"
            f"invalid ({TABLE_PATH}) -- gate disarmed for this dispatch\n"
        )
        return None
    if not is_implementation_shaped(tool_input, intents):
        return None
    prompt = str(tool_input.get("prompt", ""))
    if has_contract_block(prompt, intents):
        return None

    repo_root = find_repo_root(Path(event.get("cwd", ".")))
    tool_use_id = str(event.get("tool_use_id", ""))
    if repo_root is not None and _marker_kind(repo_root / OPT_OUT_MARKER) == "file":
        error = log_marker_disarm(repo_root, tool_input, tool_use_id)
        if error is None:
            return None
        sys.stderr.write(
            f"dispatch_contract_gate: opt-out marker present but the bypass "
            f"could not be audited ({error}) -- warning still armed\n"
        )

    subagent_type = tool_input.get("subagent_type")
    message = WARNING_TEMPLATE.format(subagent_type=subagent_type)
    if repo_root is not None and not is_recent_duplicate_warning(
        repo_root,
        subagent_type,
        intents,
    ):
        task_id = extract_task_id(prompt, intents)
        emit_gate_warning_row(repo_root, task_id, tool_use_id, subagent_type, message)
    return message


def main() -> int:
    """Read the hook event on stdin; emit the warning on a match. Always 0."""
    try:
        event = json.loads(sys.stdin.read() or "{}")
        message = evaluate(event) if isinstance(event, dict) else None
        if message is not None:
            sys.stdout.write(build_output(message))
    except Exception as exc:  # fail open: never block a dispatch
        sys.stderr.write(f"dispatch_contract_gate: fail-open ({exc})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
