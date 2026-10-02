"""Review-round P2s on PR #8: soak scope, soak evidence, default evidence dir.

* Both soak modes run the pipeline with evidence on (beside the soak Log), as a
  demo run has it, so the soak's memory samples include the evidence store.
* The soak table says what a ``pass`` covers (resources, cadence, false flags) and
  that detection accuracy is ``evaluate``'s job, so a pass is not over-read.
* A real ``run`` with no ``$STATION_WATCH_EVIDENCE_DIR`` writes evidence under
  ``data/local/evidence`` relative to its working directory (the conftest sets the
  variable for every other test, so this one clears it explicitly).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from station_watch.cli import build_parser
from station_watch.soak.commandline import _runner_argv, soak_evidence_dir
from station_watch.soak.report import render_table
from station_watch.synth.video import write_synth_clip

ROOT = Path(__file__).resolve().parents[1]


def test_source_soak_run_child_keeps_evidence_beside_the_log():
    # Evidence on, as a demo run has it, so the soak's RSS samples include the store.
    args = build_parser().parse_args(
        ["soak", "--config", "c.yaml", "--log", "l.db", "--source", "clip.mkv", "--minutes", "1"]
    )
    argv = _runner_argv(args)
    assert argv[3] == "run" and "--no-evidence" not in argv
    assert argv[argv.index("--evidence-dir") + 1] == soak_evidence_dir("l.db") == "l.db.evidence"


def test_synthetic_loop_child_runs_with_evidence_too(monkeypatch):
    import station_watch.soak.child as child

    seen = {}

    class _Runner:
        def __init__(self, context, **kwargs):
            seen.update(kwargs)

        def run(self):
            pass

        def stop(self):
            pass

    class _Context:
        class log:  # noqa: N801 -- stands in for the Log handle
            @staticmethod
            def close():
                pass

    monkeypatch.setattr(child, "Runner", _Runner)
    monkeypatch.setattr(child, "build_context", lambda **_: _Context())
    args = build_parser().parse_args(["soak", "--child", "--config", "c.yaml", "--log", "l.db"])
    assert child.run_child(args) == 0
    assert seen["evidence_dir"] == "l.db.evidence"


def test_soak_table_states_what_a_pass_covers():
    table = render_table(
        {"dataset_kind": "synthetic", "git_commit": "abc", "detector": "d"},
        {"status": "pass"},
        "station-watch soak ...",
    )
    assert "does not measure detection accuracy" in table
    assert "`station-watch evaluate`" in table


def test_a_real_run_without_the_env_override_writes_under_data_local_evidence(tmp_path):
    # The marker vanishes at frame 10: the fiducial blind cites frame 9 as its last
    # good frame, and that frame is kept as evidence under the default dir.
    clip = write_synth_clip(tmp_path / "clip", frames=40, fps=10.0, hide_marker_from=10)
    env = {k: v for k, v in os.environ.items() if k != "STATION_WATCH_EVIDENCE_DIR"}
    script = Path(sys.executable).parent / "station-watch"
    entry = [str(script)] if script.exists() else [sys.executable, "-m", "station_watch"]
    result = subprocess.run(
        [*entry, "run", "--config", str(ROOT / "config" / "station-example.yaml")]
        + ["--source", str(clip), "--log", str(tmp_path / "station.db")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    kept = list((tmp_path / "data" / "local" / "evidence").glob("*/*.jpg"))
    assert kept, "the default evidence dir under data/local/ received the cited frame"
