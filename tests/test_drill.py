"""HF3A.2: ``station-watch drill`` -- fault injection and the time-to-alarm table.

The drill runs the *same* runner pipeline ``station-watch run`` uses against injected
camera faults (synthetic from a schedule, or live with stdin marks) and reads the run
back from the Log and the ``record`` alarm sink into one HF2.8-format measurement file
plus a markdown table. These tests prove: all five faults are measured end to end (K14),
the drill uses the runner's own :class:`Runner` (not a copy), a fault the pipeline cannot
see is reported ``alarm: none`` rather than dropped, live stdin marks are stamped on the
wall clock, the committed synthetic artefacts exist and reproduce, the Board (real CLI)
shows a drill's open and recovered episodes, and a missing schedule/source fails loud.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

from station_watch.clock import parse_iso

EPOCH = "0001-01-01T00:00:00.000000+00:00"
ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "measurements" / "synthetic" / "drill-config.yaml"
SCHEDULE = ROOT / "measurements" / "synthetic" / "drill-schedule.yaml"
DRILL_JSON = ROOT / "measurements" / "synthetic" / "drill.json"
DRILL_MD = ROOT / "measurements" / "synthetic" / "drill.md"

FIVE = ("lens_covered", "frozen", "bumped", "cable_pulled", "lights_off")
EXPECTED = {
    "lens_covered": ("dark",),
    "frozen": ("frozen",),
    "bumped": ("view_shifted", "fiducial_missing"),
    "cable_pulled": ("disconnected",),
    "lights_off": ("dark",),
}

RAIL = {
    "rail_pos_1": [[0.7, 1.9], [1.7, 1.9], [1.7, 2.5], [0.7, 2.5]],
    "rail_pos_2": [[2.5, 1.9], [3.5, 1.9], [3.5, 2.5], [2.5, 2.5]],
}
STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}


def _run_cli(*args, timeout=180):
    return subprocess.run(
        [sys.executable, "-m", "station_watch", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _load(path):
    return json.loads(Path(path).read_text())


def _write_schedule(path, faults):
    Path(path).write_text(yaml.safe_dump({"faults": faults}))
    return path


# --- AC1 (K14): all five faults measured end to end through the real pipeline -----


def test_drill_measures_every_fault_time_to_alarm_from_log_and_record(tmp_path):
    out = tmp_path / "drill.json"
    log = tmp_path / "log.db"
    result = _run_cli(
        "drill",
        "--config",
        str(CONFIG),
        "--schedule",
        str(SCHEDULE),
        "--log",
        str(log),
        "--out",
        str(out),
    )
    assert result.returncode == 0, result.stderr

    faults = _load(out)["metrics"]["faults"]
    assert set(faults) == set(FIVE), faults
    for name in FIVE:
        entry = faults[name]
        assert entry["exactly_one_alarm"], (name, entry)
        assert entry["exactly_one_recovery"], (name, entry)
        assert entry["time_to_alarm_s"] is not None, (name, entry)
        assert entry["time_to_recovery_s"] is not None, (name, entry)
        assert entry["blind_reason"] in EXPECTED[name], (name, entry)
        assert entry["reason_matches"], (name, entry)

    # The numbers were read back from the Log and the alarm record, which really exist.
    from station_watch.log import Log

    with Log(log) as opened:
        assert opened.since(EPOCH, ["blind"]), "blind rows were written to the Log"
    assert (tmp_path / "log.alarms.jsonl").exists(), "the record alarm sink was written"


# --- AC2: the drill runs the runner's own pipeline class, not a copy -------------


def test_drill_imports_the_runners_own_pipeline_class():
    from station_watch.drill import pipeline
    from station_watch.runner.pipeline import Runner

    assert pipeline.Runner is Runner


def test_drill_instantiates_the_real_runner(tmp_path, monkeypatch):
    from station_watch.drill import pipeline as drill_pipeline
    from station_watch.runner import pipeline as runner_pipeline

    real_runner = runner_pipeline.Runner
    seen: dict = {}

    class _SpyRunner(real_runner):
        def __init__(self, context, **kwargs):
            seen["base_is_real"] = type(self).__mro__[1] is real_runner
            super().__init__(context, **kwargs)

    monkeypatch.setattr(drill_pipeline, "Runner", _SpyRunner)
    schedule = _write_schedule(
        tmp_path / "s.yaml", [{"fault": "frozen", "start_s": 0.5, "clear_s": 1.3}]
    )
    rc = drill_pipeline.run_synthetic(
        config_path=str(CONFIG),
        log_path=str(tmp_path / "l.db"),
        out_path=str(tmp_path / "o.json"),
        schedule_path=str(schedule),
        command="x",
    )
    assert rc == 0
    assert seen.get("base_is_real") is True, "the drill constructed the runner's own Runner"


# --- AC3: a fault the pipeline cannot see is reported, never dropped -------------


def test_fault_shorter_than_dark_window_reports_alarm_none(tmp_path):
    from station_watch.drill import pipeline as drill_pipeline

    # A 0.1 s dark flash is shorter than dark_window_s (0.3) -- the pipeline cannot see it.
    schedule = _write_schedule(
        tmp_path / "s.yaml", [{"fault": "lens_covered", "start_s": 0.5, "clear_s": 0.6}]
    )
    out = tmp_path / "o.json"
    drill_pipeline.run_synthetic(
        config_path=str(CONFIG),
        log_path=str(tmp_path / "l.db"),
        out_path=str(out),
        schedule_path=str(schedule),
        command="x",
    )
    faults = _load(out)["metrics"]["faults"]
    assert "lens_covered" in faults, "an unseen fault is never dropped from the table"
    assert faults["lens_covered"]["alarm"] == "none"
    assert faults["lens_covered"]["time_to_alarm_s"] is None


# --- AC4: live mode reads start/clear lines from stdin, stamped on the wall clock -


def test_live_mode_reads_and_stamps_stdin_marks(tmp_path):
    from station_watch.drill import pipeline as drill_pipeline
    from station_watch.synth.station import write_synth_station_clip

    script = [{"positions": {"rail_pos_1": "present", "rail_pos_2": "present"}} for _ in range(20)]
    clip, _truth = write_synth_station_clip(
        tmp_path / "clip",
        script,
        rail_positions=RAIL,
        station_zone=STATION_ZONE,
        keepout_rois={},
        fps=20.0,
    )
    stdin = io.StringIO("start frozen\nclear frozen\n")
    out = tmp_path / "o.json"
    rc = drill_pipeline.run_live(
        config_path=str(CONFIG),
        log_path=str(tmp_path / "l.db"),
        out_path=str(out),
        source_spec=str(clip),
        command="x",
        stdin=stdin,
    )
    assert rc == 0

    data = _load(out)
    assert data["provenance"]["dataset_kind"] == "real"
    assert data["provenance"]["manifest_sha256"], "the stamped stdin marks are the live manifest"
    entry = data["metrics"]["faults"]["frozen"]
    assert entry["injected_ts"] and entry["cleared_ts"], "both marks were stamped"
    # The stamps are wall-clock ISO timestamps, clear after inject.
    assert parse_iso(entry["cleared_ts"]) >= parse_iso(entry["injected_ts"])


# --- AC5: the committed synthetic artefacts exist, say synthetic, and reproduce ---


def _reproduce_command(markdown: str) -> list[str]:
    lines = markdown.splitlines()
    start = lines.index("Reproduce:")
    fence = next(i for i in range(start, len(lines)) if lines[i].strip() == "```")
    return lines[fence + 1].strip().split()


def test_committed_synthetic_drill_files_exist_and_say_synthetic():
    assert DRILL_JSON.exists() and DRILL_MD.exists()
    assert _load(DRILL_JSON)["provenance"]["dataset_kind"] == "synthetic"
    heading = DRILL_MD.read_text().splitlines()[0]
    assert "synthetic" in heading.lower(), heading


def test_committed_table_command_reproduces_the_drill(tmp_path):
    tokens = _reproduce_command(DRILL_MD.read_text())
    assert tokens[:2] == ["station-watch", "drill"], tokens
    args = tokens[1:]  # drop the "station-watch" prog name; keep the "drill" subcommand
    args[args.index("--out") + 1] = str(tmp_path / "repro.json")
    args[args.index("--log") + 1] = str(tmp_path / "repro.db")

    result = _run_cli(*args)
    assert result.returncode == 0, result.stderr
    faults = _load(tmp_path / "repro.json")["metrics"]["faults"]
    assert set(faults) == set(FIVE)
    for name in FIVE:
        assert faults[name]["exactly_one_alarm"], (name, faults[name])
        assert faults[name]["reason_matches"], (name, faults[name])


# --- AC6 (E2E): the Board (real CLI) shows the drill's open and recovered episodes -


def _board_once(config, log):
    return _run_cli("board", "--config", str(config), "--log", str(log), "--once")


def _wait_board_shows(config, log, *needles, deadline_s=25.0):
    """Poll the real Board CLI until one snapshot contains every needle (or time out)."""
    start = time.monotonic()
    while time.monotonic() - start < deadline_s:
        result = _board_once(config, log)
        if result.returncode == 0 and all(needle in result.stdout for needle in needles):
            return result.stdout
        time.sleep(0.1)
    return None


def test_e2e_board_shows_drill_open_and_recovered_episodes(tmp_path):
    # One wide dark window gives a stable interval to snapshot the open episode, and a
    # later short frozen window keeps the drill running (healthy) long after the dark
    # one recovers, so the recovered view is read from a *live* run. The drill stamps
    # its Log on the frame clock, so a Board read after the drill exits could call
    # the last verdict stale on a slow machine; reading it live does not depend on that.
    schedule = _write_schedule(
        tmp_path / "s.yaml",
        [
            {"fault": "lens_covered", "start_s": 1.0, "clear_s": 4.0},
            {"fault": "frozen", "start_s": 10.0, "clear_s": 10.3},
        ],
    )
    log = tmp_path / "log.db"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "station_watch",
            "drill",
            "--config",
            str(CONFIG),
            "--schedule",
            str(schedule),
            "--log",
            str(log),
            "--out",
            str(tmp_path / "o.json"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Wait for the alarm episode itself, not just the blind reason: the BlindRecord
        # opens a cycle before AlarmEvaluated lists the episode.
        open_view = _wait_board_shows(CONFIG, log, "unobservable:dark")
        assert open_view is not None, "the board never showed the drill's open dark episode"
        recovered = _wait_board_shows(CONFIG, log, "HEALTHY", "open episodes: none")
        assert recovered is not None, "the board never showed the drill's dark episode recovered"
    finally:
        proc.wait(timeout=60)
    assert proc.returncode == 0, proc.stderr.read() if proc.stderr else ""


# --- AC7: a missing schedule (synthetic) or source (live) fails loud, naming it ---


def test_synthetic_without_schedule_exits_nonzero_naming_it(tmp_path):
    result = _run_cli(
        "drill",
        "--config",
        str(CONFIG),
        "--log",
        str(tmp_path / "l.db"),
        "--out",
        str(tmp_path / "o.json"),
    )
    assert result.returncode != 0
    assert "schedule" in (result.stderr + result.stdout).lower()


def test_live_without_source_exits_nonzero_naming_it(tmp_path):
    result = _run_cli(
        "drill",
        "--config",
        str(CONFIG),
        "--log",
        str(tmp_path / "l.db"),
        "--out",
        str(tmp_path / "o.json"),
        "--live",
    )
    assert result.returncode != 0
    assert "source" in (result.stderr + result.stdout).lower()


# --- unit coverage for the marks and the shared measurement writer ---------------


def test_marks_from_schedule_rebases_offsets_onto_the_run_start():
    from station_watch.drill.marks import marks_from_schedule
    from station_watch.faults import FaultWindow

    marks = marks_from_schedule(
        [FaultWindow("frozen", 2.0, 5.0)], "2026-01-01T00:00:00.000000+00:00"
    )
    assert marks[0].injected_ts == "2026-01-01T00:00:02.000000+00:00"
    assert marks[0].cleared_ts == "2026-01-01T00:00:05.000000+00:00"


def test_mark_reader_pairs_start_and_clear_and_hashes_the_manifest():
    from station_watch.drill.marks import MarkReader, manifest_sha256

    clock = iter(["t0", "t1", "t2"]).__next__
    reader = MarkReader(clock=clock)
    for line in ["start bumped", "noise", "clear bumped"]:
        reader.feed(line)
    marks, manifest = reader.result()
    assert [(m.fault, m.injected_ts, m.cleared_ts) for m in marks] == [("bumped", "t0", "t2")]
    assert manifest_sha256(manifest) == manifest_sha256(manifest)  # deterministic
    assert "noise" in manifest  # unrecognised lines are still recorded in the manifest
