"""Watchdog unit tests: a second clock judges the pipeline's own liveness.

The Watchdog reads the Log's newest ``CycleCompleted`` and ``AlarmEvaluated``
rows and judges their age as *its own wall clock now minus the row's canonical
ts* against the configured windows (K7). A stale rail, a missing row, or a
missing/unreadable Log is an alarm that names the stale stage -- never a silent
pass. Alarms go through the Watchdog's *own* sink instances (K8), so the fast
unit tests here drive the ``Watchdog`` class directly with a frozen clock and a
spy sink; the two-subprocess proving drill lives in ``test_watchdog_proving.py``.
"""

import ast
import subprocess
from pathlib import Path

import pytest
import yaml

from station_watch.config import StationConfig
from station_watch.log import Log
from station_watch.records import AlarmEvaluated, CycleCompleted
from station_watch.watchdog import Watchdog, WatchdogError, age_seconds, build_watchdog

RUN_ID = "run-watchdog-test"

WATCHDOG_CONFIG = {
    "station_id": "station-1",
    "camera_id": "cam-0",
    "takt_s": 30.0,
    "grace_s": 5.0,
    "required_slots": [],
    "keepout_zones": [],
    "liveness_window_s": 2.0,
    "dark_luma_threshold": 15.0,
    "dark_window_s": 0.3,
    "frozen_frames": 10000,
    "recover_good_frames": 3,
    "cycle_interval_s": 0.1,
    "recover_healthy_verdicts": 2,
    "fiducial": {
        "dictionary_id": "DICT_4X4_50",
        "marker_id": 0,
        "expected_center_px": [40, 40],
        "tolerance_px": 10,
        "window_s": 10000.0,
    },
    "alarm": {"sinks": ["record"]},
    "watchdog": {"cycle_window_s": 2.0, "alarm_eval_window_s": 2.0, "sinks": ["record"]},
    "detect": {
        "persistence_frames": 3,
        "emit_interval_s": 5.0,
        "rail_positions": {},
        "station_zone": {"id": "bench", "region": [[-4, -3], [4, -3], [4, 3], [-4, 3]]},
        "keepout_rois": {},
        "blur_threshold": 100.0,
        "darkness_threshold": 40.0,
        "occlusion_threshold": 0.5,
    },
}


def _config(**over):
    data = {**WATCHDOG_CONFIG, **over}
    return StationConfig.from_mapping(data)


class SpySink:
    """Records every alarm and recovery the Watchdog announces to it."""

    def __init__(self):
        self.alarms = []
        self.recoveries = []

    def on_alarm(self, episode):
        self.alarms.append(episode)

    def on_recovery(self, episode, recovered_ts):
        self.recoveries.append((episode, recovered_ts))


def _seed_log(path, *, cycle_ts=None, alarm_ts=None, cycle=1, seq=1):
    with Log(path) as log:
        if cycle_ts is not None:
            log.append(CycleCompleted(ts=cycle_ts, cycle=cycle, stages=("capture",), run_id=RUN_ID))
        if alarm_ts is not None:
            log.append(AlarmEvaluated(ts=alarm_ts, seq=seq, open_episodes=(), run_id=RUN_ID))


def _watchdog(path, sink, now, **over):
    return Watchdog(_config(**over), sinks=[sink], log_path=path, clock=lambda: now)


# --- age is wall-clock now minus the row's canonical ts -----------------------


def test_age_seconds_is_now_minus_row_ts():
    row = "2026-09-28T12:00:00.000000+00:00"
    now = "2026-09-28T12:00:05.000000+00:00"
    assert age_seconds(row, now) == pytest.approx(5.0)


# --- fresh rows on both rails: no alarm ---------------------------------------


def test_fresh_rows_produce_no_alarm(tmp_path):
    logdb = tmp_path / "log.db"
    _seed_log(
        logdb,
        cycle_ts="2026-09-28T12:00:09.000000+00:00",
        alarm_ts="2026-09-28T12:00:09.000000+00:00",
    )
    sink = SpySink()
    _watchdog(logdb, sink, "2026-09-28T12:00:10.000000+00:00").check()
    assert sink.alarms == []


# --- a stale cycle rail alarms and names the cycle stage ----------------------


def test_stale_cycle_rail_alarms_and_names_cycle_stage(tmp_path):
    logdb = tmp_path / "log.db"
    _seed_log(
        logdb,
        cycle_ts="2026-09-28T12:00:00.000000+00:00",  # 10s old, window 2s
        alarm_ts="2026-09-28T12:00:09.000000+00:00",  # fresh
    )
    sink = SpySink()
    _watchdog(logdb, sink, "2026-09-28T12:00:10.000000+00:00").check()
    causes = [e.cause for e in sink.alarms]
    assert causes == ["watchdog:cycle"]
    assert "cycle" in sink.alarms[0].label


# --- a stale alarm rail alarms and names the alarm stage (the D1 case) --------


def test_stale_alarm_rail_alarms_and_names_alarm_stage(tmp_path):
    logdb = tmp_path / "log.db"
    _seed_log(
        logdb,
        cycle_ts="2026-09-28T12:00:09.000000+00:00",  # fresh
        alarm_ts="2026-09-28T12:00:00.000000+00:00",  # 10s old, window 2s
    )
    sink = SpySink()
    _watchdog(logdb, sink, "2026-09-28T12:00:10.000000+00:00").check()
    causes = [e.cause for e in sink.alarms]
    assert causes == ["watchdog:alarm"]
    assert "alarm" in sink.alarms[0].label


# --- absent rows are silence, not a pass (K1: absence is a state) -------------


def test_no_alarm_eval_rows_at_all_fires_the_alarm_stage(tmp_path):
    logdb = tmp_path / "log.db"
    _seed_log(logdb, cycle_ts="2026-09-28T12:00:09.000000+00:00")  # cycles, but no alarm_eval
    sink = SpySink()
    clock = {"now": "2026-09-28T12:00:10.000000+00:00"}
    watchdog = Watchdog(_config(), sinks=[sink], log_path=logdb, clock=lambda: clock["now"])
    watchdog.check()  # first check: inside the startup grace (see test_watchdog_grace.py)
    _seed_log(logdb, cycle_ts="2026-09-28T12:00:12.000000+00:00", cycle=2)
    clock["now"] = "2026-09-28T12:00:12.500000+00:00"  # past the 2s grace, cycles still fresh
    watchdog.check()
    assert [e.cause for e in sink.alarms] == ["watchdog:alarm"]


# --- AC3: a missing Log alarms rather than passing quietly --------------------


def test_missing_log_alarms(tmp_path):
    sink = SpySink()
    _watchdog(tmp_path / "never-written.db", sink, "2026-09-28T12:00:10.000000+00:00").check()
    assert [e.cause for e in sink.alarms] == ["watchdog:log"]
    assert "log" in sink.alarms[0].label.lower()


def test_unreadable_log_alarms(tmp_path):
    logdb = tmp_path / "log.db"
    logdb.write_bytes(b"this is not a sqlite database")
    sink = SpySink()
    _watchdog(logdb, sink, "2026-09-28T12:00:10.000000+00:00").check()
    assert [e.cause for e in sink.alarms] == ["watchdog:log"]


# --- an open episode recovers once its rail is fresh again --------------------


def test_stale_rail_recovers_when_fresh_again(tmp_path):
    logdb = tmp_path / "log.db"
    _seed_log(
        logdb,
        cycle_ts="2026-09-28T12:00:00.000000+00:00",
        alarm_ts="2026-09-28T12:00:09.000000+00:00",
    )
    sink = SpySink()
    now = ["2026-09-28T12:00:10.000000+00:00"]
    watchdog = Watchdog(_config(), sinks=[sink], log_path=logdb, clock=lambda: now[0])
    watchdog.check()
    assert [e.cause for e in sink.alarms] == ["watchdog:cycle"]
    # A fresh cycle row (new cycle number, so it is not deduped) lands on both
    # rails and the watchdog checks again.
    _seed_log(
        logdb,
        cycle_ts="2026-09-28T12:00:15.000000+00:00",
        alarm_ts="2026-09-28T12:00:15.000000+00:00",
        cycle=2,
        seq=2,
    )
    now[0] = "2026-09-28T12:00:15.500000+00:00"
    watchdog.check()
    assert [e.cause for e, _ in sink.recoveries] == ["watchdog:cycle"]


# --- an alarm episode opened once stays open, not re-announced each tick ------


def test_persistent_staleness_opens_episode_only_once(tmp_path):
    logdb = tmp_path / "log.db"
    _seed_log(
        logdb,
        cycle_ts="2026-09-28T12:00:00.000000+00:00",
        alarm_ts="2026-09-28T12:00:09.000000+00:00",
    )
    sink = SpySink()
    watchdog = _watchdog(logdb, sink, "2026-09-28T12:00:10.000000+00:00")
    watchdog.check()
    watchdog.check()
    assert len(sink.alarms) == 1


# --- the Watchdog refuses to start with no sink of its own (K8) ---------------


def test_watchdog_refuses_without_a_sink(tmp_path):
    with pytest.raises(WatchdogError):
        Watchdog(_config(), sinks=[], log_path=tmp_path / "log.db")


def test_build_watchdog_uses_the_watchdog_sinks_block(tmp_path):
    config = {**WATCHDOG_CONFIG, "watchdog": {**WATCHDOG_CONFIG["watchdog"], "sinks": ["record"]}}
    config_path = tmp_path / "station.yaml"
    config_path.write_text(yaml.safe_dump(config))
    record = tmp_path / "wd.jsonl"
    watchdog = build_watchdog(str(config_path), str(tmp_path / "log.db"), record_path=str(record))
    assert isinstance(watchdog, Watchdog)


# --- AC4: every module under src/station_watch is on the run/watchdog path ----


def _module_name(root: Path, file: Path) -> str:
    rel = file.relative_to(root.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _internal_import_targets(file: Path) -> set[str]:
    tree = ast.parse(file.read_text())
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("station_watch"):
            targets.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("station_watch"):
                    targets.add(alias.name)
    return targets


def test_every_module_is_reached_from_a_cli_entry():
    pkg = Path(__file__).resolve().parent.parent / "src" / "station_watch"
    files = [f for f in pkg.rglob("*.py") if "__pycache__" not in f.parts]
    modules = {_module_name(pkg, f): f for f in files}

    def ancestors(name: str) -> set[str]:
        parts = name.split(".")
        return {".".join(parts[: i + 1]) for i in range(len(parts))}

    # Roots: the package itself (its __init__ runs on any import) and the
    # `python -m station_watch` / console-script entry, which reaches every
    # subcommand through `cli.build_parser`/`main` -- the `run` and `watchdog`
    # paths, the HF3A `drill`, `measure` and `board` paths, and the evaluate /
    # fetch-model paths beside them. A module no command imports is dead code.
    reached: set[str] = set()
    frontier = {"station_watch", "station_watch.__main__", "station_watch.cli"}
    while frontier:
        name = frontier.pop()
        if name in reached or name not in modules:
            continue
        reached.add(name)
        for target in _internal_import_targets(modules[name]):
            for ancestor in ancestors(target):
                if ancestor in modules and ancestor not in reached:
                    frontier.add(ancestor)

    # Every subcommand's entry module is part of the live command surface, not dead
    # code: run, watchdog, evaluate, drill, measure, board, qa, audit, soak and
    # preflight must each be reachable from the CLI.
    for entry in (
        "station_watch.runner.pipeline",
        "station_watch.watchdog",
        "station_watch.evaluate.commandline",
        "station_watch.drill.commandline",
        "station_watch.physics.commandline",
        "station_watch.board.commandline",
        "station_watch.qa",
        "station_watch.audit.commandline",
        "station_watch.soak.commandline",
        "station_watch.preflight.commandline",
    ):
        assert entry in reached, f"{entry} entry point is not reached from the CLI"

    orphans = sorted(set(modules) - reached)
    assert orphans == [], f"modules not reached from a CLI subcommand entry point: {orphans}"


# --- AC5: the README "Run it" commands are real station-watch invocations -----


def _readme() -> str:
    return (Path(__file__).resolve().parent.parent / "README.md").read_text()


def _run_it_section(text: str) -> str:
    start = text.index("## Run it")
    rest = text[start + len("## Run it") :]
    nxt = rest.find("\n## ")
    return rest if nxt == -1 else rest[:nxt]


def test_readme_run_it_commands_parse_against_the_real_cli():
    import shlex

    from station_watch.cli import build_parser

    section = _run_it_section(_readme())
    commands = [
        line.strip() for line in section.splitlines() if line.strip().startswith("station-watch ")
    ]
    assert commands, "the Run it section must contain station-watch commands"
    parser = build_parser()
    for command in commands:
        parser.parse_args(shlex.split(command)[1:])  # raises SystemExit on a bad command
    # Install, a webcam run, a file run, and the watchdog are all shown.
    assert "pip install" in section
    assert any("--source 0" in c for c in commands)
    assert any("watchdog" in c for c in commands)
    # HF3A subcommands are shown too: drill (synthetic and live), measure, board.
    assert any(c.startswith("station-watch drill") and "--schedule" in c for c in commands)
    assert any(c.startswith("station-watch drill") and "--live" in c for c in commands)
    assert any(c.startswith("station-watch measure ") for c in commands)
    assert any(c.startswith("station-watch board") for c in commands)


# --- AC3: no footage, images, binary video or weights are tracked -------------


def test_no_footage_images_or_weights_are_tracked_by_git():
    # Footage never leaves the box and weights are fetched locally, never
    # committed; sample images and other binary blobs stay out of the public repo
    # too. The committed clip *manifests* are YAML/JSONL, not video, so this scans
    # only for the binary suffixes the .gitignore keeps out (HF3A.7).
    root = Path(__file__).resolve().parent.parent
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    binary_suffixes = (
        # footage / video
        ".mp4",
        ".mkv",
        ".mov",
        ".avi",
        ".webm",
        # images
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".tiff",
        # model weights
        ".onnx",
        ".pt",
        ".pth",
        ".tflite",
        ".pb",
        ".h5",
        ".weights",
    )
    offenders = [f for f in tracked if f.lower().endswith(binary_suffixes)]
    assert offenders == [], f"footage, images or weights must never be committed: {offenders}"


def test_readme_status_and_principles_sections_are_unchanged():
    text = _readme()
    assert "## Status" in text and "## Design principles" in text
    # The D1 rationale and the K7/K8 rows the Watchdog answers stay put.
    assert "Why the Watchdog exists (finding D1)." in text
    assert "| K7 |" in text and "| K8 |" in text
    assert "Building. Work began **Monday, September 28, 2026**" in text
