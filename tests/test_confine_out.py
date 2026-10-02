"""``confine_out``: a measurement file may only land in its dataset's own tree.

A ``synthetic`` file must be written inside ``measurements/synthetic/`` and a
``real`` file inside ``measurements/v1/`` or ``measurements/v2/``. A ``..`` path
or a symlink that escapes those trees is refused with a ``ValueError`` naming the
rule, so a proving run can never overwrite a real-dataset measurement by mistake.
``--force-out`` overrides the refusal with exactly one warning line.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from station_watch.evaluate.provenance import confine_out


@pytest.fixture
def measurements(tmp_path, monkeypatch):
    for sub in ("synthetic", "v1", "v2"):
        (tmp_path / "measurements" / sub).mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_synthetic_inside_is_allowed(measurements):
    out = confine_out(Path("measurements/synthetic/x.json"), "synthetic")
    assert out == (measurements / "measurements/synthetic/x.json").resolve()


def test_real_inside_v1_and_v2_is_allowed(measurements):
    assert confine_out(Path("measurements/v1/x.json"), "real")
    assert confine_out(Path("measurements/v2/x.json"), "real")


def test_synthetic_outside_raises_naming_rule(measurements):
    with pytest.raises(ValueError, match="synthetic"):
        confine_out(Path("measurements/v1/x.json"), "synthetic")


def test_real_outside_raises_naming_rule(measurements):
    with pytest.raises(ValueError, match="real"):
        confine_out(Path("measurements/synthetic/x.json"), "real")


def test_dotdot_escape_raises(measurements):
    with pytest.raises(ValueError):
        confine_out(Path("measurements/synthetic/../../outside/x.json"), "synthetic")


def test_symlink_escape_raises(measurements):
    link = measurements / "measurements" / "synthetic" / "link"
    link.symlink_to(measurements / "outside", target_is_directory=True)
    with pytest.raises(ValueError):
        confine_out(Path("measurements/synthetic/link/x.json"), "synthetic")


def test_force_out_overrides_with_one_warning(measurements, capsys):
    out = confine_out(Path("outside/x.json"), "synthetic", force_out=True)
    assert out == (measurements / "outside/x.json").resolve()
    warnings = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert len(warnings) == 1


def test_unknown_dataset_kind_raises(measurements):
    with pytest.raises(ValueError):
        confine_out(Path("measurements/synthetic/x.json"), "bogus")
