"""`station-watch soak --synthetic-loop`: the looping normal-work source and child.

``--synthetic-loop`` runs the soak against a :class:`SyntheticSource` looping a
normal-work script instead of a recorded ``--source``; the runner child is reached
through the real entry ``station-watch soak --child``. These tests prove the
command-line contract (the mode routing, the ``synthetic``-only rule, the
source/loop exclusivity) at the ``handle`` seam, leaving the full three-child soak
to the end-to-end subprocess test.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import station_watch.soak.child as child_mod
import station_watch.soak.commandline as commandline
from station_watch.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]


def _parse(argv: list[str]):
    return build_parser().parse_args(["soak", *argv])


_LOOP = ["--config", "c.yaml", "--log", "l.db", "--out", "measurements/synthetic/soak.json"]


def test_synthetic_loop_with_dataset_real_is_refused_naming_the_rule(capsys):
    args = _parse([*_LOOP, "--synthetic-loop", "--dataset-kind", "real", "--minutes", "0.1"])
    code = commandline.handle(args)
    err = capsys.readouterr().err
    assert code != 0
    assert "--synthetic-loop" in err and "synthetic" in err


def test_source_and_synthetic_loop_together_is_refused(capsys):
    args = _parse(
        [
            *_LOOP,
            "--synthetic-loop",
            "--source",
            "clip.mp4",
            "--dataset-kind",
            "synthetic",
            "--minutes",
            "0.1",
        ]
    )
    code = commandline.handle(args)
    assert code != 0
    assert "--source" in capsys.readouterr().err


def test_neither_source_nor_synthetic_loop_is_refused(capsys):
    args = _parse([*_LOOP, "--dataset-kind", "synthetic", "--minutes", "0.1"])
    code = commandline.handle(args)
    assert code != 0
    assert "--synthetic-loop" in capsys.readouterr().err


def test_child_flag_routes_to_run_child(monkeypatch):
    seen = {}

    def fake_run_child(args) -> int:
        seen["config"] = args.config
        seen["log"] = args.log
        return 0

    monkeypatch.setattr(child_mod, "run_child", fake_run_child)
    args = _parse(["--child", "--config", "c.yaml", "--log", "l.db"])
    assert commandline.handle(args) == 0
    assert seen == {"config": "c.yaml", "log": "l.db"}


def test_synthetic_loop_runner_argv_targets_the_soak_child(monkeypatch):
    captured = {}

    def fake_run_soak(args, *, runner_argv):
        captured["argv"] = runner_argv
        return 0

    monkeypatch.setattr("station_watch.soak.supervisor.run_soak", fake_run_soak, raising=True)
    args = _parse([*_LOOP, "--synthetic-loop", "--dataset-kind", "synthetic", "--minutes", "0.1"])
    assert commandline.handle(args) == 0
    argv = captured["argv"]
    assert argv[:4] == [__import__("sys").executable, "-m", "station_watch", "soak"]
    assert "--child" in argv and "c.yaml" in argv and "l.db" in argv
    assert "--source" not in argv


# --------------------------------------------------------------------------- #
# AC1: E2E -- a real `soak --synthetic-loop` subprocess, three children, pass
# --------------------------------------------------------------------------- #
def _entry_point() -> list[str]:
    script = Path(sys.executable).parent / "station-watch"
    return [str(script)] if script.exists() else [sys.executable, "-m", "station_watch"]


def _test_config(tmp_path) -> Path:
    # The committed synthetic soak config, but with a short warmup (so a <1 min run
    # still yields three post-warmup samples) and loose growth thresholds: a 30 s run
    # is all start-up -- its RSS/Log slope is steep warm-up, not a leak -- so a short
    # test can only prove the mechanism, not the real committed bounds (those are
    # judged by the committed 10-minute soak, whose warmup excludes the start-up).
    data = yaml.safe_load((ROOT / "measurements" / "synthetic" / "soak-config.yaml").read_text())
    data["soak"] = {
        "max_rss_growth_mb_per_h": 5000.0,
        "max_log_mb_per_h": 100000.0,
        "max_board_render_s": 5.0,
        "max_verdict_gap_s": 30.0,
        "max_false_flags": 0,
        "warmup_s": 6.0,
    }
    cfg = tmp_path / "soak-config.yaml"
    cfg.write_text(yaml.safe_dump(data))
    return cfg


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_e2e_synthetic_loop_starts_three_children_samples_stops_and_passes(tmp_path):
    (tmp_path / "measurements" / "synthetic").mkdir(parents=True)
    cfg = _test_config(tmp_path)
    port = _free_port()
    out_rel = "measurements/synthetic/soak.json"

    result = subprocess.run(
        [
            *_entry_point(),
            "soak",
            "--config",
            str(cfg),
            "--log",
            str(tmp_path / "soak.db"),
            "--out",
            out_rel,
            "--dataset-kind",
            "synthetic",
            "--synthetic-loop",
            "--minutes",
            "0.5",
            "--sample-s",
            "3",
            "--board-port",
            str(port),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    metrics = json.loads((tmp_path / out_rel).read_text())["metrics"]
    assert metrics["status"] == "pass", metrics["failures"]
    assert metrics["post_warmup_samples"] >= 3
    for name in ("run", "watchdog", "board"):  # three children, each sampled for RSS
        assert f"rss_growth_mb_per_h.{name}" in metrics
    assert "log_growth_mb_per_h" in metrics
    assert "board_render_p95_s" in metrics
    assert "max_verdict_gap_s" in metrics
    assert metrics["false_flags"] == 0

    table = (tmp_path / "measurements" / "synthetic" / "soak.md").read_text()
    assert "soak — synthetic" in table and "--synthetic-loop" in table

    # No orphan child: the board port is free again after the soak returned.
    with socket.socket() as sock:
        sock.settimeout(2.0)
        with pytest.raises((ConnectionRefusedError, OSError)):
            sock.connect(("127.0.0.1", port))
            sock.sendall(b"GET /view.json HTTP/1.0\r\n\r\n")
            if not sock.recv(1):
                raise ConnectionRefusedError
