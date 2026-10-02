"""Config K9 reaches the Detect section: every gap fails loud, by dotted name.

A ``detect`` section that is missing, missing a required sub-key, or that does not
cover a declared ``required_slots`` / ``keepout_zones`` id must fail at load time
naming the exact dotted key -- so a station is never half-configured such that a
required part or keep-out zone has no region to read and silently never alarms.
"""

from __future__ import annotations

import copy

import pytest
import yaml

from station_watch.config import StationConfig, load_station_config

BASE = {
    "station_id": "station-1",
    "camera_id": "cam-0",
    "takt_s": 30.0,
    "grace_s": 5.0,
    "required_slots": ["rail_pos_1", "rail_pos_2"],
    "keepout_zones": ["zone_press"],
    "liveness_window_s": 3.0,
    "dark_luma_threshold": 15.0,
    "dark_window_s": 2.0,
    "frozen_frames": 30,
    "recover_good_frames": 5,
    "cycle_interval_s": 1.0,
    "recover_healthy_verdicts": 3,
    "fiducial": {
        "dictionary_id": "DICT_4X4_50",
        "marker_id": 0,
        "expected_center_px": [40, 40],
        "tolerance_px": 10,
    },
    "alarm": {"sinks": ["screen"]},
    "watchdog": {"cycle_window_s": 10.0, "alarm_eval_window_s": 10.0, "sinks": ["screen"]},
    "detect": {
        "persistence_frames": 3,
        "emit_interval_s": 5.0,
        "rail_positions": {
            "rail_pos_1": [[-3, -0.5], [-1, -0.5], [-1, 0.5], [-3, 0.5]],
            "rail_pos_2": [[1, -0.5], [3, -0.5], [3, 0.5], [1, 0.5]],
        },
        "station_zone": {"id": "bench", "region": [[-5, -3], [5, -3], [5, 3], [-5, 3]]},
        "keepout_rois": {
            "zone_press": {"region": [[2, -3], [5, -3], [5, -1], [2, -1]], "active": True}
        },
        "blur_threshold": 100.0,
        "darkness_threshold": 40.0,
        "occlusion_threshold": 0.5,
    },
}

DETECT_SUBKEYS = [
    "persistence_frames",
    "emit_interval_s",
    "rail_positions",
    "station_zone",
    "keepout_rois",
    "blur_threshold",
    "darkness_threshold",
    "occlusion_threshold",
]


def _load(tmp_path, data):
    path = tmp_path / "station.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_station_config(path)


def test_base_config_loads_and_exposes_detect_dict(tmp_path):
    cfg = _load(tmp_path, copy.deepcopy(BASE))
    assert isinstance(cfg, StationConfig)
    assert isinstance(cfg.detect, dict)
    assert cfg.detect["persistence_frames"] == 3
    assert set(cfg.detect["rail_positions"]) == {"rail_pos_1", "rail_pos_2"}


def test_missing_detect_section_names_detect(tmp_path):
    data = copy.deepcopy(BASE)
    del data["detect"]
    with pytest.raises(KeyError) as exc:
        _load(tmp_path, data)
    assert "detect" in str(exc.value)


@pytest.mark.parametrize("subkey", DETECT_SUBKEYS)
def test_missing_detect_subkey_names_dotted_key(tmp_path, subkey):
    data = copy.deepcopy(BASE)
    del data["detect"][subkey]
    with pytest.raises(KeyError) as exc:
        _load(tmp_path, data)
    assert f"detect.{subkey}" in str(exc.value)


def test_required_slot_without_rail_position_names_dotted_key(tmp_path):
    data = copy.deepcopy(BASE)
    del data["detect"]["rail_positions"]["rail_pos_2"]
    with pytest.raises(KeyError) as exc:
        _load(tmp_path, data)
    assert "detect.rail_positions.rail_pos_2" in str(exc.value)


def test_keepout_zone_without_roi_names_dotted_key(tmp_path):
    data = copy.deepcopy(BASE)
    del data["detect"]["keepout_rois"]["zone_press"]
    with pytest.raises(KeyError) as exc:
        _load(tmp_path, data)
    assert "detect.keepout_rois.zone_press" in str(exc.value)


def test_empty_positions_and_zones_are_allowed(tmp_path):
    data = copy.deepcopy(BASE)
    data["required_slots"] = []
    data["keepout_zones"] = []
    data["detect"]["rail_positions"] = {}
    data["detect"]["keepout_rois"] = {}
    cfg = _load(tmp_path, data)
    assert cfg.detect["rail_positions"] == {}
    assert cfg.detect["keepout_rois"] == {}


# --- HF3.15: dotted detail slots (<position_id>.<kind>) and detect.slot_details ---

STRIPE_REGION = [[0.85, 2.05], [1.55, 2.05], [1.55, 2.25], [0.85, 2.25]]


def _with_detail(data):
    data["required_slots"] = ["rail_pos_1", "rail_pos_1.torque_stripe"]
    data["detect"]["slot_details"] = {"rail_pos_1": {"torque_stripe": STRIPE_REGION}}
    return data


def test_required_detail_with_region_loads(tmp_path):
    cfg = _load(tmp_path, _with_detail(copy.deepcopy(BASE)))
    assert cfg.detect["slot_details"]["rail_pos_1"]["torque_stripe"] == STRIPE_REGION
    assert "rail_pos_1.torque_stripe" in cfg.required_slots


def test_required_detail_without_slot_details_region_names_dotted_key(tmp_path):
    data = copy.deepcopy(BASE)
    data["required_slots"] = ["rail_pos_1.torque_stripe"]
    # no detect.slot_details at all
    with pytest.raises(KeyError) as exc:
        _load(tmp_path, data)
    assert "detect.slot_details.rail_pos_1.torque_stripe" in str(exc.value)


def test_required_detail_with_unregistered_kind_names_dotted_key(tmp_path):
    data = copy.deepcopy(BASE)
    data["required_slots"] = ["rail_pos_1.not_a_kind"]
    data["detect"]["slot_details"] = {"rail_pos_1": {"not_a_kind": STRIPE_REGION}}
    with pytest.raises(ValueError) as exc:
        _load(tmp_path, data)
    assert "rail_pos_1.not_a_kind" in str(exc.value)


def test_required_detail_whose_position_has_no_rail_position_names_key(tmp_path):
    data = copy.deepcopy(BASE)
    data["required_slots"] = ["ghost.torque_stripe"]
    data["detect"]["slot_details"] = {"ghost": {"torque_stripe": STRIPE_REGION}}
    with pytest.raises(KeyError) as exc:
        _load(tmp_path, data)
    assert "detect.rail_positions.ghost" in str(exc.value)
