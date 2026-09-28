"""Tests for loading StationConfig from YAML, including missing-key errors (K9)."""

from pathlib import Path

import pytest
import yaml

from station_watch.config import REQUIRED_KEYS, StationConfig, load_station_config

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "station-example.yaml"


def full_config_dict():
    return yaml.safe_load(EXAMPLE.read_text())


def test_example_config_loads():
    cfg = load_station_config(EXAMPLE)
    assert isinstance(cfg, StationConfig)
    assert cfg.station_id
    assert cfg.camera_id
    assert cfg.takt_s > 0


def test_all_required_keys_present_in_example():
    data = full_config_dict()
    for key in REQUIRED_KEYS:
        assert key in data, f"example config is missing {key}"


@pytest.mark.parametrize("missing_key", REQUIRED_KEYS)
def test_missing_required_key_names_that_key(missing_key, tmp_path):
    data = full_config_dict()
    del data[missing_key]
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(KeyError) as exc:
        load_station_config(path)
    assert missing_key in str(exc.value)


def test_load_from_dict_directly():
    cfg = StationConfig.from_mapping(full_config_dict())
    assert cfg.alarm["sinks"]
