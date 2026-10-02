"""The HF3 command surface is documented, and nothing private is tracked.

The README "Run it" section shows every HF3 command (audit, qa, soak, preflight with
``--list-cameras`` and ``--cold-start``, the run evidence options, held-out
evaluate) and links the demo and camera docs; ``test_watchdog`` checks that each of
its commands parses. ``git ls-files`` holds no evidence frames, audit sheets, reviewer
verdicts, Logs or local runtime data, and ``.gitignore`` still carries every
private-data entry this public repo depends on.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (what the line must start with, an option it must carry or None)
RUN_IT_COMMANDS = [
    ("station-watch audit build", None),
    ("station-watch audit review", None),
    ("station-watch audit mark", None),
    ("station-watch audit score", None),
    ("station-watch audit rate", None),
    ("station-watch qa", None),
    ("station-watch soak", "--synthetic-loop"),
    ("station-watch preflight", "--board-port"),
    ("station-watch preflight", "--list-cameras"),
    ("station-watch preflight", "--cold-start"),
    ("station-watch run", "--evidence-dir"),
    ("station-watch run", "--no-evidence"),
    ("station-watch evaluate", "--split held_out"),
]

PRIVATE_IGNORES = [
    "data/local/",
    "*.mp4",
    "*.mkv",
    "*.avi",
    ".env",
    ".env.*",
    ".paircoder/context/",
    ".paircoder/tasks/",
    ".paircoder/history/",
]


def _run_it() -> str:
    text = (ROOT / "README.md").read_text()
    rest = text[text.index("## Run it") + len("## Run it") :]
    nxt = rest.find("\n## ")
    return rest if nxt == -1 else rest[:nxt]


def _commands(section: str) -> list[str]:
    return [ln.strip() for ln in section.splitlines() if ln.strip().startswith("station-watch ")]


def test_run_it_shows_every_hf3_command():
    commands = _commands(_run_it())
    for prefix, option in RUN_IT_COMMANDS:
        assert any(c.startswith(prefix) and (option is None or option in c) for c in commands), (
            f"README Run it has no `{prefix}` command{f' with {option}' if option else ''}"
        )


def test_run_it_links_the_demo_and_camera_docs():
    section = _run_it()
    assert "(docs/DEMO.md)" in section
    assert "(docs/CAMERAS.md)" in section


def _tracked() -> list[str]:
    return subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()


def test_no_evidence_audit_sheets_verdicts_or_logs_are_tracked():
    offenders = [
        path
        for path in _tracked()
        if path.startswith("data/local/")
        or path.endswith(("verdicts.jsonl", "flags.json", ".db", ".sqlite", ".db-wal"))
        or (path.endswith(".html") and "audit" in path)
        or "/evidence/" in f"/{path}"
    ]
    assert offenders == [], f"private runtime output must never be committed: {offenders}"


def test_gitignore_keeps_every_private_data_entry():
    entries = {ln.strip() for ln in (ROOT / ".gitignore").read_text().splitlines()}
    missing = [entry for entry in PRIVATE_IGNORES if entry not in entries]
    assert missing == [], f".gitignore lost private-data entries: {missing}"
