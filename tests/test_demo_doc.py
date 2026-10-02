"""``docs/DEMO.md``, the demo-day run-of-show, is held to the real code.

Every ``station-watch`` command in it (fenced lines and inline spans) parses with the
real argparse parser; every camera fault name it tells the operator to type or
schedule exists in ``faults.FAULT_NAMES``; and it carries a section for every demo
beat, its recovery, and each backup plan. (Its numbers are checked by
``test_claims.py``.)
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from station_watch import cli
from station_watch.faults import FAULT_NAMES

DOC = Path(__file__).resolve().parents[1] / "docs" / "DEMO.md"

EXPECTED_HEADINGS = [
    "## Setup checklist",
    "## Preflight",
    "## Cold start",
    "## Demo beats",
    "### Beat: normal cycle",
    "### Beat: a part left off",
    "### Beat: a missing torque stripe",
    "### Beat: a pulled label",
    "### Beat: work stopped",
    "### Beat: a hand in the keep-out zone",
    "### Beat: the lens covered",
    "## Backup plans",
    "### Backup: power",
    "### Backup: wifi",
    "### Backup: camera",
    "## After the demo",
]

# A fault the operator types to the live drill or reads in a drill schedule, written
# as a code span: `start <fault>`, `clear <fault>` or `fault: <fault>`.
FAULT_USE_RE = re.compile(r"`(?:start |clear |fault:\s*)([a-z_]+)`")


def station_watch_commands(text: str) -> list[str]:
    fenced = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("station-watch ")]
    return fenced + re.findall(r"`(station-watch [^`]+)`", text)


def fault_names_used(text: str) -> set[str]:
    return set(FAULT_USE_RE.findall(text))


def test_demo_doc_has_every_beat_recovery_and_backup_heading():
    lines = DOC.read_text().splitlines()
    for heading in EXPECTED_HEADINGS:
        assert heading in lines, f"docs/DEMO.md is missing the heading {heading!r}"


def test_every_beat_says_what_to_trigger_say_and_how_it_recovers():
    text = DOC.read_text()
    beats = re.split(r"^### Beat: ", text, flags=re.M)[1:]
    assert len(beats) == 7
    for beat in beats:
        body = beat.split("\n## ")[0]
        for label in ("**Trigger:**", "**Say:**", "**Recovers:**"):
            assert label in body, f"beat {beat.splitlines()[0]!r} has no {label}"


def test_every_station_watch_command_in_the_demo_doc_parses():
    commands = station_watch_commands(DOC.read_text())
    assert len(commands) >= 5
    parser = cli.build_parser()
    for command in commands:
        args = shlex.split(command)
        assert args[0] == "station-watch"
        parser.parse_args(args[1:])  # SystemExit on a command the CLI does not accept


def test_every_fault_name_the_demo_doc_uses_exists():
    used = fault_names_used(DOC.read_text())
    assert "lens_covered" in used
    assert used <= FAULT_NAMES, f"unknown fault names: {sorted(used - FAULT_NAMES)}"


def test_an_invented_fault_name_is_caught():
    assert fault_names_used("type `start lens_smudged` to the drill") == {"lens_smudged"}
    assert not fault_names_used("type `start lens_smudged`") <= FAULT_NAMES
