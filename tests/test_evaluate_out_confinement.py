"""HF3.9 fix (2): every ``evaluate --out`` is confined, and a real run needs one.

PR #5 P2: a real ``evaluate`` with no ``--out`` overwrote the previous run's
numbers in ``measurements/``. Now a real (non-synthetic) run with no ``--out``
exits 2 naming ``--out``; ``--synthetic`` still defaults to
``measurements/synthetic``. Every resolved ``--out`` (real and synthetic) goes
through HF3.3's :func:`confine_out`, so evaluate obeys the same Output confinement
rule as the other measurement writers, with ``--force-out`` to override.

The parser's wiring is exercised through :func:`commandline.handle` with
``run_evaluation`` stubbed, so these tests never spin up the real pipeline.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from station_watch.evaluate import commandline, harness


def _args(**over):
    base = dict(
        config="cfg.yaml",
        manifest="m.yaml",
        clips_dir="clips",
        out=None,
        synthetic=False,
        speed=None,
        split="calibration",
        force_out=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def captured(monkeypatch):
    """Stub ``run_evaluation``; return a dict that records the out_dir it was given."""
    seen: dict = {}

    def fake(
        *,
        config_path,
        manifest_path,
        out_dir,
        synthetic,
        clips_dir,
        speed=None,
        split="calibration",
    ):
        seen["out_dir"] = out_dir
        seen["synthetic"] = synthetic
        return 0

    monkeypatch.setattr(harness, "run_evaluation", fake)
    return seen


# --- AC3: a real run with no --out exits 2 naming --out ---------------------------


def test_real_evaluate_without_out_exits_2_naming_out(captured, capsys):
    rc = commandline.handle(_args(out=None, synthetic=False))
    assert rc == 2
    assert "--out" in capsys.readouterr().err
    assert "out_dir" not in captured  # run_evaluation was never reached


def test_synthetic_without_out_defaults_to_measurements_synthetic(captured):
    rc = commandline.handle(_args(out=None, synthetic=True, config=None))
    assert rc == 0
    assert captured["synthetic"] is True
    assert captured["out_dir"].replace("\\", "/").endswith("measurements/synthetic")


# --- AC3: every resolved --out is confined to its dataset_kind tree ---------------


def test_real_out_inside_v1_tree_is_accepted(captured):
    rc = commandline.handle(_args(out="measurements/v1/run-a", synthetic=False))
    assert rc == 0
    assert captured["out_dir"].replace("\\", "/").endswith("measurements/v1/run-a")


def test_real_out_outside_the_tree_is_refused(captured, capsys, tmp_path):
    rc = commandline.handle(_args(out=str(tmp_path / "anywhere"), synthetic=False))
    assert rc == 2
    assert "measurements/v1" in capsys.readouterr().err  # the rule is named
    assert "out_dir" not in captured


def test_force_out_overrides_confinement(captured, capsys, tmp_path):
    target = tmp_path / "anywhere"
    rc = commandline.handle(_args(out=str(target), synthetic=False, force_out=True))
    assert rc == 0
    assert captured["out_dir"] == str(target.resolve())
    assert "WARNING" in capsys.readouterr().err


def test_synthetic_out_must_land_in_the_synthetic_tree(captured, capsys):
    rc = commandline.handle(_args(out="measurements/v1/nope", synthetic=True, config=None))
    assert rc == 2
    assert "measurements/synthetic" in capsys.readouterr().err
