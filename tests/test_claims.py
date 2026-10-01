"""Every number in the README "Measured performance" section is a checked claim.

K13: claims are checked against committed measurements. Each number in the
"Measured performance" section carries a claim marker naming the file and the
dotted key it came from, e.g.::

    <!-- claim: measurements/synthetic/detect.json#metrics.missing_part.precision round=2 -->1.00

This test parses every marker, loads the named file and key, and fails if the
displayed value differs from the stored one (after the stated ``round=N``
rounding) or if the file or key is missing. It also fails if the section holds
any number with no marker, so prose cannot drift from the committed numbers.

The last two tests are tests of the test: changing a stored measurement, or
leaving a number unmarked, must make the checks fail and name what broke.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECTION_HEADING = "## Measured performance"

# A claim marker immediately followed by the number it stands behind. The value
# follows ``-->`` with no space, as in the K13 example.
CLAIM_RE = re.compile(
    r"<!--\s*claim:\s*(?P<path>[^#\s]+)#(?P<key>[^\s]+?)"
    r"(?:\s+round=(?P<round>\d+))?\s*-->(?P<value>-?\d+(?:\.\d+)?)%?"
)
# A bare number: a digit run not glued to a letter or digit (so "v1" and "p95"
# inside words are not numbers, but "0.94" and "35" are).
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Claim:
    path: str
    key: str
    round_to: int | None
    value: float
    shown: str


def _readme() -> str:
    return (ROOT / "README.md").read_text()


def parse_claims(text: str) -> list[Claim]:
    claims = []
    for m in CLAIM_RE.finditer(text):
        rnd = int(m["round"]) if m["round"] is not None else None
        claims.append(Claim(m["path"], m["key"], rnd, float(m["value"]), m["value"]))
    return claims


def measured_section(text: str) -> str:
    start = text.index(SECTION_HEADING)
    rest = text[start + len(SECTION_HEADING) :]
    nxt = rest.find("\n## ")
    return rest if nxt == -1 else rest[:nxt]


def _lookup(data: object, key: str):
    node = data
    for part in key.split("."):
        node = node[part]
    return node


def claim_errors(root: Path, text: str) -> list[str]:
    errors = []
    for claim in parse_claims(text):
        name = f"{claim.path}#{claim.key}"
        path = root / claim.path
        if not path.exists():
            errors.append(f"{name}: measurement file {claim.path} is missing")
            continue
        try:
            stored = _lookup(json.loads(path.read_text()), claim.key)
        except (KeyError, TypeError):
            errors.append(f"{name}: key not found in {claim.path}")
            continue
        got = float(stored)
        if claim.round_to is not None:
            got = round(got, claim.round_to)
        if got != claim.value:
            errors.append(f"{name}: README shows {claim.shown} but file has {got}")
    return errors


def unmarked_numbers(section: str) -> list[str]:
    residual = CLAIM_RE.sub(" ", section)
    return NUMBER_RE.findall(residual)


# --- the real checks against the shipped README ------------------------------


def test_readme_numbers_match_committed_measurements():
    text = _readme()
    claims = parse_claims(text)
    assert claims, "the Measured performance section must cite at least one number"
    assert not claim_errors(ROOT, text), "\n".join(claim_errors(ROOT, text))


def test_measured_section_has_no_unmarked_numbers():
    stray = unmarked_numbers(measured_section(_readme()))
    assert not stray, f"unmarked numbers in Measured performance: {stray}"


def test_section_states_no_real_numbers_until_v1_and_labels_synthetic():
    section = measured_section(_readme())
    assert "v1" in section and "no real" in section.lower()
    assert "synthetic" in section.lower()
    # Every cited file is the synthetic proving set, not a (nonexistent) v1 file.
    for claim in parse_claims(_readme()):
        assert "measurements/synthetic/" in claim.path


# --- tests of the test -------------------------------------------------------


def test_changing_a_measured_value_fails_naming_the_claim(tmp_path):
    src = ROOT / "measurements" / "synthetic" / "detect.json"
    data = json.loads(src.read_text())
    data["metrics"]["missing_part"]["precision"] = 0.5  # was 1.0
    dest = tmp_path / "measurements" / "synthetic" / "detect.json"
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps(data))
    readme = (
        f"{SECTION_HEADING}\n\n<!-- claim: measurements/synthetic/"
        "detect.json#metrics.missing_part.precision round=2 -->1.00\n"
    )
    errors = claim_errors(tmp_path, readme)
    assert any("missing_part.precision" in e for e in errors), errors


def test_missing_key_or_file_fails_naming_the_claim(tmp_path):
    readme = (
        f"{SECTION_HEADING}\n\n<!-- claim: measurements/synthetic/"
        "detect.json#metrics.nope -->1.00\n"
    )
    assert any(
        "measurements/synthetic/detect.json#metrics.nope" in e
        for e in claim_errors(tmp_path, readme)
    )


def test_an_unmarked_number_in_the_section_is_flagged():
    assert unmarked_numbers(f"{SECTION_HEADING}\n\nPrecision is 0.94 on the set.\n") == ["0.94"]


def test_a_marked_number_is_not_flagged():
    marked = (
        f"{SECTION_HEADING}\n\n<!-- claim: measurements/synthetic/"
        "detect.json#metrics.missing_part.precision round=2 -->1.00 on the set.\n"
    )
    assert unmarked_numbers(marked) == []
