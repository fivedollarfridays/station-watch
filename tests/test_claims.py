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


# --- HF3.9: the held-out claims rule (K13) ------------------------------------
#
# Once a real held-out measurement is committed, flag precision/recall numbers may
# only be cited from a held-out file -- a held-out number is the honest one, and a
# calibration-split number must never be dressed up as held-out performance. The
# rule is dormant until such a file exists, so the existing rules hold unchanged
# until then (this lane commits no held-out file).

FLAG_METRIC_RE = re.compile(r"^metrics\.[^.]+\.(precision|recall)$")


def _provenance_of(path: Path) -> dict:
    try:
        return json.loads(path.read_text()).get("provenance", {})
    except (OSError, ValueError):
        return {}


def _is_real_held_out(path: Path) -> bool:
    prov = _provenance_of(path)
    return prov.get("dataset_kind") == "real" and prov.get("split") == "held_out"


def _is_real_calibration(path: Path) -> bool:
    prov = _provenance_of(path)
    return prov.get("dataset_kind") == "real" and prov.get("split") == "calibration"


def held_out_committed(root: Path) -> bool:
    """True once any committed measurement file is a real held-out measurement."""
    measurements = root / "measurements"
    return measurements.is_dir() and any(
        _is_real_held_out(p) for p in measurements.rglob("*.json")
    )


def held_out_claim_errors(root: Path, text: str) -> list[str]:
    """Flag precision/recall claims that do not cite a held-out file, once one exists.

    Every "Measured performance" claim whose key is a flag precision or recall
    (``metrics.<flag>.precision|recall``) must cite a held-out file; a claim citing
    a calibration-split real file for those keys fails naming it, and the section
    must contain the words "held-out". Until a held-out file exists, nothing is
    flagged (the existing rules apply unchanged).
    """
    if not held_out_committed(root):
        return []
    errors: list[str] = []
    if "held-out" not in measured_section(text).lower():
        errors.append('a held-out measurement is committed but the section omits "held-out"')
    for claim in parse_claims(text):
        if not FLAG_METRIC_RE.match(claim.key):
            continue
        name = f"{claim.path}#{claim.key}"
        cited = root / claim.path
        if _is_real_held_out(cited):
            continue
        if _is_real_calibration(cited):
            errors.append(
                f"{name}: cites a calibration-split real file; a flag precision/recall "
                f"claim must cite a held-out file once one is committed"
            )
        else:
            errors.append(
                f"{name}: a held-out file is committed, so this flag precision/recall "
                f"claim must cite a held-out file"
            )
    return errors


# --- the real check: the shipped README is dormant (no held-out file committed) ---


def test_shipped_readme_passes_the_held_out_rule():
    assert not held_out_committed(ROOT)
    assert held_out_claim_errors(ROOT, _readme()) == []


# --- tests of the test: a temp tree holding a real held-out file ------------------


def _write_meas(root: Path, rel: str, *, dataset_kind: str, split: str, metrics: dict) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"provenance": {"dataset_kind": dataset_kind, "split": split}, "metrics": metrics}
    path.write_text(json.dumps(payload))
    return path


def test_held_out_rule_is_dormant_until_a_held_out_file_exists(tmp_path):
    _write_meas(tmp_path, "measurements/v1/detect.json", dataset_kind="real",
                split="calibration", metrics={"missing_part": {"precision": 1.0}})
    readme = (f"{SECTION_HEADING}\n\n<!-- claim: measurements/v1/detect.json"
              "#metrics.missing_part.precision round=2 -->1.00\n")
    assert not held_out_committed(tmp_path)
    assert held_out_claim_errors(tmp_path, readme) == []


def test_a_calibration_claim_fails_naming_it_once_a_held_out_file_exists(tmp_path):
    _write_meas(tmp_path, "measurements/v1/detect.json", dataset_kind="real",
                split="calibration", metrics={"missing_part": {"precision": 1.0}})
    _write_meas(tmp_path, "measurements/v2/detect.json", dataset_kind="real",
                split="held_out", metrics={"missing_part": {"precision": 0.9}})
    readme = (f"{SECTION_HEADING}\n\nHeld-out numbers now.\n\n"
              "<!-- claim: measurements/v1/detect.json"
              "#metrics.missing_part.precision round=2 -->1.00\n")
    errors = held_out_claim_errors(tmp_path, readme)
    assert any(
        "measurements/v1/detect.json#metrics.missing_part.precision" in e for e in errors
    ), errors


def test_citing_the_held_out_file_passes(tmp_path):
    _write_meas(tmp_path, "measurements/v2/detect.json", dataset_kind="real",
                split="held_out", metrics={"missing_part": {"precision": 0.9}})
    readme = (f"{SECTION_HEADING}\n\nHeld-out numbers now.\n\n"
              "<!-- claim: measurements/v2/detect.json"
              "#metrics.missing_part.precision round=2 -->0.90\n")
    assert held_out_claim_errors(tmp_path, readme) == []


def test_section_without_held_out_words_fails_when_a_held_out_file_is_committed(tmp_path):
    _write_meas(tmp_path, "measurements/v2/detect.json", dataset_kind="real",
                split="held_out", metrics={"missing_part": {"precision": 0.9}})
    readme = (f"{SECTION_HEADING}\n\n<!-- claim: measurements/v2/detect.json"
              "#metrics.missing_part.precision round=2 -->0.90\n")
    errors = held_out_claim_errors(tmp_path, readme)
    assert any("held-out" in e for e in errors), errors
