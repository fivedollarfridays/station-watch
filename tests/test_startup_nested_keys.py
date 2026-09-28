"""K9 reaches nested config: every required sub-key fails startup loud, by dotted name.

A config missing ``watchdog.cycle_window_s`` (or any other required sub-key) must
not start a run or a watchdog that later dies on a ``KeyError`` mid-loop -- or,
worse, limps along with a rail that never fires. Both ``station-watch run`` and
``station-watch watchdog`` exit non-zero and name the full dotted key.
"""

import copy
import sys
from pathlib import Path

import pytest
import yaml

from station_watch.cli import main

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import HEALTH_CONFIG  # noqa: E402

NESTED_KEYS = [
    "fiducial.dictionary_id",
    "fiducial.marker_id",
    "fiducial.expected_center_px",
    "fiducial.tolerance_px",
    "alarm.sinks",
    "watchdog.cycle_window_s",
    "watchdog.alarm_eval_window_s",
    "watchdog.sinks",
]


def _config_without(tmp_path, dotted):
    section, key = dotted.split(".")
    data = copy.deepcopy(HEALTH_CONFIG)
    del data[section][key]
    path = tmp_path / "station.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def _argv(command, config, tmp_path):
    common = ["--config", str(config), "--log", str(tmp_path / "log.db")]
    if command == "run":
        return ["run", *common, "--source", str(tmp_path / "clip.mkv")]
    return ["watchdog", *common, "--max-checks", "1", "--interval", "0.01"]


@pytest.mark.parametrize("command", ["run", "watchdog"])
@pytest.mark.parametrize("dotted", NESTED_KEYS)
def test_missing_nested_key_exits_nonzero_naming_dotted_key(tmp_path, capsys, command, dotted):
    config = _config_without(tmp_path, dotted)

    code = main(_argv(command, config, tmp_path))

    assert code != 0
    assert f"missing config key: {dotted}" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["run", "watchdog"])
@pytest.mark.parametrize("section", ["fiducial", "alarm", "watchdog"])
def test_nested_section_not_a_mapping_exits_nonzero_naming_it(tmp_path, capsys, command, section):
    data = copy.deepcopy(HEALTH_CONFIG)
    data[section] = ["not", "a", "mapping"]
    config = tmp_path / "station.yaml"
    config.write_text(yaml.safe_dump(data))

    code = main(_argv(command, config, tmp_path))

    assert code != 0
    assert section in capsys.readouterr().err


@pytest.mark.parametrize("command", ["run", "watchdog"])
def test_missing_top_level_key_uses_same_message(tmp_path, capsys, command):
    data = {k: v for k, v in HEALTH_CONFIG.items() if k != "takt_s"}
    config = tmp_path / "station.yaml"
    config.write_text(yaml.safe_dump(data))

    code = main(_argv(command, config, tmp_path))

    assert code != 0
    assert "missing config key: takt_s" in capsys.readouterr().err
