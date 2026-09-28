"""The README's own file-source command, on the shipped example config, works.

This runs the exact ``station-watch run --config config/station-example.yaml
--source clip.mkv --log station.db`` line from the README "Run it" section (in a
scratch directory holding a copy of the shipped config) over the synth helper's
default clip, made to go dark for three seconds and come back. The shipped config
is camera-health only, so the camera going dark must open exactly one alarm, the
recovery must close it exactly once, and the run must end healthy. The screen
sink is read off stdout; the sound sink plays through a silent stub player.
"""

import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from station_watch.log import Log
from station_watch.records import VerdictState

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_video import write_synth_clip  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EPOCH = "0001-01-01T00:00:00.000000+00:00"


def _readme_file_command() -> list[str]:
    for line in (ROOT / "README.md").read_text().splitlines():
        if line.startswith("station-watch run") and "clip.mkv" in line:
            return shlex.split(line)
    raise AssertionError("README must show the recorded-file station-watch run command")


def _silent_players(bin_dir: Path) -> None:
    bin_dir.mkdir()
    for name in ("afplay", "aplay"):
        player = bin_dir / name
        player.write_text("#!/bin/sh\nexit 0\n")
        player.chmod(player.stat().st_mode | stat.S_IEXEC)


def _entry_point() -> list[str]:
    script = Path(sys.executable).parent / "station-watch"
    return [str(script)] if script.exists() else [sys.executable, "-m", "station_watch"]


def test_readme_file_command_alarms_once_on_dark_and_recovers_once(tmp_path):
    work = tmp_path / "work"
    (work / "config").mkdir(parents=True)
    shutil.copy(ROOT / "config" / "station-example.yaml", work / "config")
    clip = write_synth_clip(work, frames=100, dark_from=20, dark_until=50)  # 10 fps default
    command = _readme_file_command()
    if clip.name != "clip.mkv":  # no FFV1 writer here: the helper wrote a PNG sequence
        command[command.index("clip.mkv")] = str(clip)
    _silent_players(tmp_path / "bin")
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}"}

    result = subprocess.run(
        [*_entry_point(), *command[1:]],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr

    lines = result.stdout.splitlines()
    alarms = [line for line in lines if line.startswith("ALARM")]
    clears = [line for line in lines if line.startswith("CLEAR")]
    assert len(alarms) == 1 and "unobservable (dark)" in alarms[0], result.stdout
    assert len(clears) == 1 and "unobservable (dark)" in clears[0], result.stdout
    with Log(work / "station.db") as log:
        evals = log.since(EPOCH, ["alarm_eval"])
        final = log.newest("verdict")
    assert any("unobservable:dark" in e.open_episodes for e in evals)
    assert evals[-1].open_episodes == ()
    assert final.state == VerdictState.HEALTHY
