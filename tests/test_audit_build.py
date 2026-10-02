"""`audit build` writes flags.json + a proof sheet and never touches the Log."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

from station_watch.audit.build import build_audit
from station_watch.clock import offset_iso
from station_watch.log import Log
from station_watch.records import Fault, FaultKind, FrameRecord, Verdict, VerdictState

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import HEALTH_CONFIG  # noqa: E402

EPOCH = "2026-10-01T00:00:00.000000+00:00"
RUN = "run-build"


def _t(offset: float) -> str:
    return offset_iso(EPOCH, offset)


def _config(path: Path) -> Path:
    data = {
        **HEALTH_CONFIG,
        "required_slots": ["rail_pos_1", "rail_pos_2"],
        "detect": {
            **HEALTH_CONFIG["detect"],
            "rail_positions": {
                "rail_pos_1": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
                "rail_pos_2": [[1.5, 0.0], [2.5, 0.0], [2.5, 1.0], [1.5, 1.0]],
            },
        },
    }
    path.write_text(yaml.safe_dump(data))
    return path


def _frame(frame_id, offset):
    return FrameRecord(
        station_id="station-1",
        camera_id="cam-0",
        frame_id=frame_id,
        ts=_t(offset),
        capture_mono=float(frame_id),
        fingerprint=f"fp-{frame_id}",
        mean_luma=100.0,
        noise_score=1.0,
        run_id=RUN,
    )


def _verdict(seq, offset, target, frame_ids, run_id=RUN):
    return Verdict(
        station_id="station-1",
        ts=_t(offset),
        state=VerdictState.FAULT,
        faults=(Fault(kind=FaultKind.MISSING_PART, target=target, frame_ids=tuple(frame_ids)),),
        blind_reasons=(),
        seq=seq,
        run_id=run_id,
    )


def _two_fault_log(path: Path) -> Path:
    with Log(path) as log:
        log.append(_frame(1, 0))
        log.append(_verdict(1, 0, "rail_pos_1", [1]))  # frame 1 has evidence
        log.append(_verdict(2, 1, "rail_pos_2", [99]))  # frame 99 has none
    return path


def _write_evidence(evidence_dir: Path, frame_id: int, corners, run_id: str = RUN) -> None:
    run_dir = evidence_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    image = np.full((120, 120, 3), 40, dtype=np.uint8)
    name = f"frame_{frame_id:08d}.jpg"
    cv2.imwrite(str(run_dir / name), image)
    entry = {
        "frame_id": frame_id,
        "ts": _t(0),
        "fingerprint": f"fp-{frame_id}",
        "path": name,
        "width": 120,
        "height": 120,
        "scale": 0.5,
        "corners": corners,
    }
    (run_dir / "index.jsonl").write_text(json.dumps(entry) + "\n")


def test_build_writes_flags_json_and_a_sheet_with_image_and_missing_reason(tmp_path):
    cfg = _config(tmp_path / "station.yaml")
    log = _two_fault_log(tmp_path / "log.db")
    evidence = tmp_path / "evidence"
    _write_evidence(evidence, 1, [[20.0, 20.0], [40.0, 20.0], [40.0, 40.0], [20.0, 40.0]])
    out = tmp_path / "out"

    rc = build_audit(config_path=cfg, log_path=log, evidence_dir=evidence, clip=None, out=out)
    assert rc == 0

    flags = json.loads((out / "flags.json").read_text())
    assert [f["flag_id"] for f in flags] == [
        f"{RUN}:missing_part:rail_pos_1:1",
        f"{RUN}:missing_part:rail_pos_2:2",
    ]
    assert flags[0]["frame_ids"] == [1]

    sheet = (out / "index.html").read_text()
    assert "data:image/jpeg;base64," in sheet  # frame 1's thumbnail is embedded
    assert "no evidence: not_captured" in sheet  # frame 99 has no evidence (AC3)
    # frame 1 had stored corners, so its overlay was drawn (no "unavailable" note for it)
    assert "overlay unavailable" not in sheet


def test_an_empty_session_yields_an_empty_flag_list_and_a_no_flags_sheet(tmp_path):
    cfg = _config(tmp_path / "station.yaml")
    log = tmp_path / "log.db"
    with Log(log) as handle:
        handle.append(_frame(1, 0))  # frames, but no verdicts or blinds -> no flags
    out = tmp_path / "out"

    build_audit(
        config_path=cfg, log_path=log, evidence_dir=tmp_path / "evidence", clip=None, out=out
    )
    assert json.loads((out / "flags.json").read_text()) == []
    assert "no flags in this session" in (out / "index.html").read_text()


def test_build_is_deterministic_and_leaves_the_log_bytes_unchanged(tmp_path):
    cfg = _config(tmp_path / "station.yaml")
    log = _two_fault_log(tmp_path / "log.db")
    evidence = tmp_path / "evidence"
    _write_evidence(evidence, 1, [[20.0, 20.0], [40.0, 20.0], [40.0, 40.0], [20.0, 40.0]])

    before = (tmp_path / "log.db").read_bytes()
    build_audit(config_path=cfg, log_path=log, evidence_dir=evidence, clip=None, out=tmp_path / "a")
    build_audit(config_path=cfg, log_path=log, evidence_dir=evidence, clip=None, out=tmp_path / "b")
    after = (tmp_path / "log.db").read_bytes()

    assert after == before, "audit build must never write the Log (AC5)"
    assert (tmp_path / "a" / "flags.json").read_bytes() == (
        tmp_path / "b" / "flags.json"
    ).read_bytes(), "flag ids/order must be identical across two builds (AC2)"


def test_a_two_run_log_resolves_each_flag_against_its_own_runs_evidence(tmp_path):
    # frame_id restarts at every run, so run B's frame 1 is not run A's frame 1 (K4):
    # with evidence kept only for run A, run B's citation of frame 1 must say so,
    # never borrow run A's thumbnail.
    cfg = _config(tmp_path / "station.yaml")
    log = tmp_path / "log.db"
    with Log(log) as handle:
        handle.append(_verdict(1, 0, "rail_pos_1", [1], run_id="run-a"))
        handle.append(_verdict(1, 10, "rail_pos_1", [1], run_id="run-b"))
    evidence = tmp_path / "evidence"
    _write_evidence(evidence, 1, None, run_id="run-a")
    out = tmp_path / "out"

    build_audit(config_path=cfg, log_path=log, evidence_dir=evidence, clip=None, out=out)

    flags = json.loads((out / "flags.json").read_text())
    assert [f["run_id"] for f in flags] == ["run-a", "run-b"]
    sheet = (out / "index.html").read_text()
    assert sheet.count("data:image/jpeg;base64,") == 1, "only run A's frame 1 has evidence"
    assert "no evidence: not_captured" in sheet
