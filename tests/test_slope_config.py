"""K9 for ``detect.slope``: the cycle-time-creep section fails loud at load, by dotted key.

``cycle_time_creep`` reads its steps from station-zone ``motion`` / ``no_motion``
observations, which only exist when ``detect.station_zone.track_motion`` is true.
A ``detect.slope`` section without that opt-in would be an alarm that can never
fire, so loading refuses it. The section's own keys are validated at load too, so a
typo never surfaces as a ``KeyError`` from the Judge mid-run.
"""

import copy
import sys
from pathlib import Path

import pytest
import yaml

from station_watch.cli import main
from station_watch.config import StationConfig

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import HEALTH_CONFIG  # noqa: E402


def _data(slope=None, track_motion=True):
    data = copy.deepcopy(HEALTH_CONFIG)
    if track_motion is not None:
        data["detect"]["station_zone"]["track_motion"] = track_motion
    if slope is not None:
        data["detect"]["slope"] = slope
    return data


def _good_slope(**over):
    return {"window_steps": 5, "min_s_per_step": 2.0, **over}


def test_valid_slope_with_track_motion_loads():
    config = StationConfig.from_mapping(_data(slope=_good_slope()))
    assert config.detect["slope"]["window_steps"] == 5


@pytest.mark.parametrize("track_motion", [None, False])
def test_slope_without_track_motion_fails_loud_naming_the_key(track_motion):
    with pytest.raises(ValueError, match=r"detect\.station_zone\.track_motion"):
        StationConfig.from_mapping(_data(slope=_good_slope(), track_motion=track_motion))


@pytest.mark.parametrize("key", ["window_steps", "min_s_per_step"])
def test_slope_missing_key_names_dotted_key(key):
    slope = _good_slope()
    del slope[key]
    with pytest.raises(KeyError, match=rf"missing config key: detect\.slope\.{key}"):
        StationConfig.from_mapping(_data(slope=slope))


@pytest.mark.parametrize("value", [1, 0, -3, 2.5, "5", True, None])
def test_window_steps_must_be_int_at_least_two(value):
    with pytest.raises(ValueError, match=r"detect\.slope\.window_steps"):
        StationConfig.from_mapping(_data(slope=_good_slope(window_steps=value)))


@pytest.mark.parametrize("value", [0, 0.0, -1.0, "2", True, None])
def test_min_s_per_step_must_be_positive_number(value):
    with pytest.raises(ValueError, match=r"detect\.slope\.min_s_per_step"):
        StationConfig.from_mapping(_data(slope=_good_slope(min_s_per_step=value)))


def test_slope_section_must_be_a_mapping():
    with pytest.raises(ValueError, match=r"detect\.slope"):
        StationConfig.from_mapping(_data(slope=[5, 2.0]))


def test_run_refuses_to_start_on_slope_without_track_motion(tmp_path, capsys):
    path = tmp_path / "station.yaml"
    path.write_text(yaml.safe_dump(_data(slope=_good_slope(), track_motion=None)))
    argv = ["run", "--config", str(path), "--log", str(tmp_path / "log.db")]

    code = main([*argv, "--source", str(tmp_path / "clip.mkv")])

    assert code != 0
    assert "detect.station_zone.track_motion" in capsys.readouterr().err
