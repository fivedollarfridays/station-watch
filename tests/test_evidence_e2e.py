"""Evidence frames, end to end through the real run (HF3.4).

These drive the real ``station-watch run`` (a subprocess for the conformance and
``--no-evidence`` criteria, the in-process Runner for the broken-writer identity
claim and the dark last-good episode) and read evidence back through the public
:class:`~station_watch.evidence.EvidenceIndex`, so what is proven is the whole
Capture -> detect-hook -> evidence path a live station produces, not a mocked
store.
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml

from station_watch.alarm.sink import build_sinks
from station_watch.capture.blind import BlindThresholds
from station_watch.capture.capture import Capture
from station_watch.capture.source import FrameSource
from station_watch.cli import build_parser
from station_watch.config import load_station_config
from station_watch.evidence import EvidenceFrame, EvidenceIndex, EvidenceStore
from station_watch.log import Log
from station_watch.records import BlindReason, BlindState
from station_watch.run import new_run_id
from station_watch.runner.pipeline import Runner
from station_watch.runner.startup import RunContext, resolve_stall_window_or_fail

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_station import write_synth_station_clip  # noqa: E402
from helpers.synth_video import write_synth_clip  # noqa: E402

EPOCH = "0001-01-01T00:00:00.000000+00:00"

RAIL = {
    "rail_pos_1": [[0.7, 1.9], [1.7, 1.9], [1.7, 2.5], [0.7, 2.5]],
    "rail_pos_2": [[2.5, 1.9], [3.5, 1.9], [3.5, 2.5], [2.5, 2.5]],
}
STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}

BASE = {
    "station_id": "station-1",
    "camera_id": "cam-0",
    "takt_s": 30.0,
    "grace_s": 5.0,
    "required_slots": list(RAIL),
    "keepout_zones": [],
    "liveness_window_s": 2.0,
    "dark_luma_threshold": 15.0,
    "dark_window_s": 0.3,
    "frozen_frames": 10_000,
    "recover_good_frames": 3,
    "cycle_interval_s": 0.05,
    "recover_healthy_verdicts": 2,
    "fiducial": {
        "dictionary_id": "DICT_4X4_50",
        "marker_id": 0,
        "expected_center_px": [58, 58],
        "tolerance_px": 10,
        "window_s": 10_000.0,
    },
    "alarm": {"sinks": ["record"]},
    "watchdog": {"cycle_window_s": 1.0, "alarm_eval_window_s": 1.0, "sinks": ["record"]},
    "detect": {
        "persistence_frames": 2,
        "emit_interval_s": 5.0,
        "rail_positions": RAIL,
        "station_zone": STATION_ZONE,
        "keepout_rois": {},
        "blur_threshold": 100.0,
        "darkness_threshold": 40.0,
        "occlusion_threshold": 0.5,
        "unknown_grace_s": 10.0,
    },
}


def _write_config(path, **over):
    cfg = {**BASE, **over}
    Path(path).write_text(yaml.safe_dump(cfg))
    return path


def _rail_script(n, pos1="present", pos2="present"):
    return [{"positions": {"rail_pos_1": pos1, "rail_pos_2": pos2}} for _ in range(n)]


def _run_cli(*args, timeout=120):
    return subprocess.run(
        [sys.executable, "-m", "station_watch", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_id_of(logdb):
    with Log(logdb) as log:
        return log.since(EPOCH, ["frame"])[0].run_id


def _fingerprints(logdb):
    with Log(logdb) as log:
        return {f.frame_id: f.fingerprint for f in log.since(EPOCH, ["frame"])}


def _fault_frame_ids(logdb):
    with Log(logdb) as log:
        verdicts = log.since(EPOCH, ["verdict"])
    ids: set[int] = set()
    for verdict in verdicts:
        for fault in verdict.faults:
            ids.update(fault.frame_ids)
    return ids


# --- AC1: E2E conformance -- every cited frame resolves, fingerprint matches ----


def test_every_cited_frame_resolves_to_a_file_with_the_logs_fingerprint(tmp_path):
    config = _write_config(tmp_path / "station.yaml")
    # rail_pos_1 is removed for the whole clip: a missing_part fault citing real frames.
    clip, _truth = write_synth_station_clip(
        tmp_path / "clip", _rail_script(60, pos1="absent"), rail_positions=RAIL, fps=20.0
    )
    evidence_dir = tmp_path / "evidence"
    logdb = tmp_path / "log.db"

    result = _run_cli(
        "run",
        "--config", str(config),
        "--source", str(clip),
        "--log", str(logdb),
        "--alarm-record", str(tmp_path / "alarm.jsonl"),
        "--evidence-dir", str(evidence_dir),
    )
    assert result.returncode == 0, result.stderr

    cited = _fault_frame_ids(logdb)
    assert cited, "the missing-part run must cite the frames its fault came from"
    run_id = _run_id_of(logdb)
    fingerprints = _fingerprints(logdb)
    index = EvidenceIndex.load(evidence_dir, run_id)
    for frame_id in sorted(cited):
        got = index.lookup(frame_id)
        assert isinstance(got, EvidenceFrame), f"cited frame {frame_id} has no evidence: {got}"
        assert got.path.exists(), got.path
        assert got.fingerprint == fingerprints[frame_id], (
            f"evidence fingerprint for frame {frame_id} must equal the Log's FrameRecord"
        )


# --- AC2: a dark blind episode's last good frame resolves to an evidence frame --


class _ReplayClock:
    """A monotonic clock that only advances when Capture's pacer sleeps.

    Lets a dark window of real mono-time elapse deterministically on any machine,
    so the dark blind opens on frame id, not on wall-clock jitter (see
    tests/test_capture_blind.py).
    """

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.0)


def test_dark_blind_last_good_frame_resolves_to_evidence(tmp_path):
    # Drive Capture directly (no detector): the only evidence it can write is the
    # last good frame before the dark blind opens, through the blind-open tee.
    clip = write_synth_clip(tmp_path / "dark", frames=30, dark_from=8, fps=20, seed=4)
    logdb = tmp_path / "dark.db"
    evidence_dir = tmp_path / "evidence"
    run_id = "run-dark"
    store = EvidenceStore(evidence_dir, run_id)
    clock = _ReplayClock()
    thresholds = BlindThresholds(
        liveness_window_s=5.0,
        dark_luma_threshold=15.0,
        dark_window_s=0.1,
        frozen_frames=10_000,
        recover_good_frames=3,
        fiducial={
            "dictionary_id": "DICT_4X4_50",
            "marker_id": 0,
            "expected_center_px": [40, 40],
            "tolerance_px": 10,
            "window_s": 10_000.0,
        },
    )
    with Log(logdb) as log:
        capture = Capture(
            FrameSource(str(clip)),
            station_id="ST",
            camera_id="CAM",
            run_id=run_id,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            evidence=store,
        )
        capture.run(log, thresholds)
        store.close()
        opened = [
            b
            for b in log.since(EPOCH, ["blind"])
            if b.reason is BlindReason.DARK and b.state is BlindState.OPENED
        ]

    assert opened, "the dark clip must open a dark blind record"
    last_good = opened[0].last_good_frame_id
    assert last_good is not None
    got = EvidenceIndex.load(evidence_dir, run_id).lookup(last_good)
    assert isinstance(got, EvidenceFrame), f"the dark episode's last good frame {last_good}: {got}"
    assert got.path.exists()


# --- AC3: a broken writer changes nothing about the run, reports once -----------


def _verdict_content(logdb):
    with Log(logdb) as log:
        verdicts = log.since(EPOCH, ["verdict"])
    return Counter(
        (v.state.value, tuple(sorted((f.kind.value, f.target, f.frame_ids) for f in v.faults)))
        for v in verdicts
    )


def _alarm_causes(record_path):
    import json

    lines = Path(record_path).read_text().splitlines() if Path(record_path).exists() else []
    events = [json.loads(x) for x in lines if x.strip()]
    return [e["cause"] for e in events if e["event"] == "alarm"]


def _cycle_count(logdb):
    with Log(logdb) as log:
        return len(log.since(EPOCH, ["cycle"]))


def _run_in_process(tmp_path, clip, config_path, *, evidence_dir, tag=""):
    config = load_station_config(config_path)
    logdb = tmp_path / f"log{tag}.db"
    record = tmp_path / f"alarm{tag}.jsonl"
    context = RunContext(
        config=config,
        source=FrameSource(str(clip)),
        log=Log(logdb),
        sinks=build_sinks(config.alarm, record_path=record),
        thresholds=BlindThresholds.from_station_config(config),
        stall_window_s=resolve_stall_window_or_fail(config)[0],
        stall_window_source="test",
    )
    runner = Runner(context, run_id=new_run_id(), speed=1000.0, evidence_dir=evidence_dir)
    try:
        runner.run()
    finally:
        context.log.close()
        context.source.release()
    return logdb, record


def test_a_broken_evidence_writer_leaves_the_run_identical_and_reports_once(
    tmp_path, monkeypatch
):
    cfg = tmp_path / "station.yaml"
    _write_config(cfg)
    clip, _t = write_synth_station_clip(
        tmp_path / "clip", _rail_script(40, pos1="absent"), rail_positions=RAIL, fps=20.0
    )

    # Baseline: evidence off.
    off_log, off_rec = _run_in_process(tmp_path, clip, cfg, evidence_dir=None, tag="_off")

    # A store whose every write raises, injected where the Runner builds it.
    import station_watch.evidence as ev

    def _boom(_thumbnail):
        raise RuntimeError("evidence disk is on fire")

    created: list[EvidenceStore] = []

    def raising_store(evidence_dir, run_id, *, thumb_width, max_files):
        reports: list[str] = []
        store = ev.EvidenceStore(
            evidence_dir,
            run_id,
            thumb_width=thumb_width,
            max_files=max_files,
            encode=_boom,
            report=reports.append,
        )
        store._test_reports = reports  # type: ignore[attr-defined]
        created.append(store)
        return store

    monkeypatch.setattr("station_watch.runner.pipeline.EvidenceStore", raising_store)
    on_log, on_rec = _run_in_process(
        tmp_path, clip, cfg, evidence_dir=str(tmp_path / "evidence"), tag="_on"
    )

    assert _verdict_content(on_log) == _verdict_content(off_log), "verdicts must be unchanged"
    assert _alarm_causes(on_rec) == _alarm_causes(off_rec), "alarms must be unchanged"
    assert _cycle_count(on_log) > 0 and _cycle_count(off_log) > 0, "both runs record cycles"

    assert created, "the Runner built the evidence store"
    store = created[0]
    assert store.write_errors > 0, "the broken writer was exercised"
    assert store.written == 0, "nothing was written"
    assert len(store._test_reports) == 1, "the failure is reported exactly once"
    # Not one usable evidence file on disk.
    assert list((tmp_path / "evidence").rglob("frame_*.jpg")) == []


# --- AC5: --no-evidence writes nothing under the evidence dir -------------------


def test_no_evidence_writes_nothing_under_the_evidence_dir(tmp_path):
    config = _write_config(tmp_path / "station.yaml")
    clip, _t = write_synth_station_clip(
        tmp_path / "clip", _rail_script(40, pos1="absent"), rail_positions=RAIL, fps=20.0
    )
    evidence_dir = tmp_path / "evidence"

    result = _run_cli(
        "run",
        "--config", str(config),
        "--source", str(clip),
        "--log", str(tmp_path / "log.db"),
        "--alarm-record", str(tmp_path / "alarm.jsonl"),
        "--evidence-dir", str(evidence_dir),
        "--no-evidence",
    )
    assert result.returncode == 0, result.stderr
    assert _fault_frame_ids(tmp_path / "log.db"), "the run still faults (so there were citations)"
    existing = list(evidence_dir.rglob("*")) if evidence_dir.exists() else []
    assert existing == [], f"--no-evidence must write nothing; found {existing}"


# --- AC7: default dir is under data/local; nothing tracked under evidence/audit --


def test_default_evidence_dir_is_under_data_local(monkeypatch):
    # The real default, with the test-suite's isolation override cleared.
    from station_watch.cli import DEFAULT_EVIDENCE_DIR

    monkeypatch.delenv("STATION_WATCH_EVIDENCE_DIR", raising=False)
    parser = build_parser()
    args = parser.parse_args(["run", "--config", "c.yaml", "--source", "0", "--log", "l.db"])
    assert args.evidence_dir == DEFAULT_EVIDENCE_DIR == "data/local/evidence"
    assert args.evidence_dir.startswith("data/local/")


def test_git_tracks_nothing_under_an_evidence_or_audit_dir():
    repo = Path(__file__).resolve().parent.parent
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    # Evidence thumbnails and audit sheets are *output*: they always land under
    # data/local/ (the run/audit defaults), which is gitignored. A stray evidence/
    # or audit/ *output* dir written elsewhere is still caught -- but the audit
    # command's own source package (src/station_watch/audit/) and its tests are
    # legitimate code, not output, so the code trees are exempt from the name check.
    def is_offender(p: str) -> bool:
        if p.startswith("data/local/"):
            return True
        parts = Path(p).parts
        if parts[:1] in (("src",), ("tests",)):
            return False
        return any(part in ("evidence", "audit") for part in parts)

    offenders = [p for p in tracked if is_offender(p)]
    assert offenders == [], f"evidence/audit output must never be committed; found {offenders}"
