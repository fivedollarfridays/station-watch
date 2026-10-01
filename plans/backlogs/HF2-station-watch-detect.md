# HF2: Station Watch Detect: rail positions, dwell, keep-out, and measured numbers

**Base:** main
**Plan type:** feature
**Summary:** Detect turns real frames into the HF1 observation records (DIN rail component present / absent / unknown with N-frame persistence, anchored on the ArUco fiducial; station-zone motion; keep-out entry from a local Apache-2.0 model), Judge and Alarm run on that real output, a cycle-time slope alarm catches creep before a stall, and an evaluation harness writes committed measurement files that a claims test holds the README to

## Why this lane exists

Station Watch is the HackFW 2026 MADE track entry (demo Fri Oct 30). HF1 (merged, main `3e53200`) built Capture with blind records, Log, Judge, Alarm, the runner `station-watch run` and the Watchdog `station-watch watchdog`, with 179 tests. In HF1 the only real input path is camera health; Judge and Alarm faults for stall, missing part and keep-out were proven only on committed fixture observations (`tests/fixtures/observations/*.jsonl`).

HF2 builds component 2, **Detect**: a pure function from a fresh frame to `Observation` records (the format fixed in HF1.1 in `src/station_watch/records.py`), each carrying method, confidence ceiling, frame id and detector output, with no time thresholds and no alarming. Then it puts Judge and Alarm on real Detect output, adds the K12 slope alarm, and makes every number a measured, committed number (K13).

**The station Detect watches (PLAN 5.1, revised 9/27; RESEARCH-2026-09-27 2.2).** Not a generic parts tray: a small panel-build bench. A 6x6 in backplate with a 35 mm DIN rail, terminal blocks with end stops, slotted wire duct, one or two DIN breakers or relays, ferrules on stranded wire, heat-shrink wire labels and a torque-seal paint stripe, plus a taped station zone and a taped keep-out zone, and one ArUco fiducial fixed in view. In this backlog a **rail position** (config key `rail_positions`, target ids like `rail_pos_1`) is one component position on the DIN rail, and it reads `part_present` (component present and seated), `part_absent` (empty, or present but not seated), or `part_unknown`. The HF1 record kinds keep their names (`part_present`, `part_absent`, `part_unknown`); `required_slots` in the station config now lists rail position ids.

**Out of HF2, scheduled for HF3:** per-position checks for wire label present at the termination, ferrule present, and torque stripe present. They need the real station's appearance to calibrate (small text, small crimps, paint color under the real lamp), and the physical station is not built yet. HF2 leaves room for them: a later check is a new target `rail_pos_1.torque_stripe` with the same three kinds, so no record format change is needed.

**Real clips do not exist yet.** The mock station kit arrived 9/30 and is not assembled; clip set v1 will be recorded after it is, later than PLAN section 6 dates. So every HF2 test runs on **synthetic frames generated in the test** (a rendered backplate, DIN rail, components, taped zones, ArUco marker and a hand blob, written losslessly into a temp dir by the helpers) and on HF1's committed fixtures. The real-clip evaluation is wired to a manifest path that is allowed to be absent, and when it is absent the harness says "no labeled set present" and writes no number. Each task lists, under **Closes on Kevin's clips**, what only the real labeled set can prove; those lines are not engage ACs and do not block task completion, they are tracked to closure after clip set v1 lands.

**Design rules that bind every task** (README principles table):
- K1: healthy, fault, unobservable. `part_unknown` is never treated as present (and never as absent). A keep-out zone with no positive "clear" reading is never treated as clear.
- K3: a call from a blurred, dim or partly occluded frame carries a lower `confidence_ceiling` than one from a fresh, sharp, unoccluded frame.
- K4: every observation and every fault cites the frame ids and detector outputs it came from.
- K5: the alarm answers the fault; nothing optional can delay or suppress it.
- K9: fail loud at startup: a required rail position or keep-out zone with no region defined, or a keep-out model file missing or failing its hash check, refuses to start and names the missing piece.
- K12: rate, not level: cycle time creeping up alarms on slope before a stall; the fiducial is the canary and the anchor.
- K13: every number in README lives in a committed measurement file, and a test fails if prose and numbers drift.
- K14: tests drive frames through the real `cv2.VideoCapture` file path and the real `station-watch run` process, not only in-process stubs.

**Stack.** Python 3.12, `src/station_watch/`, `opencv-python` (Apache 2.0; `cv2.aruco` and `cv2.dnn` are in the main wheel), `numpy`, `pyyaml`. pytest and ruff. No GPU required. No network calls at run time. Every dependency or model added must have a license compatible with a public MIT repo, stated in the PR body and, for a model, in the module docstring. Apache-2.0 models only (PLAN 5.2: RT-DETR or YOLOX; never Ultralytics YOLO, which is AGPL-3.0).

## Rules for every task

- **Public repo.** This is a PUBLIC repo. Never commit footage, video, images of the real station, binary model weights (`*.onnx`, `*.pt`, `*.pth`, `*.tflite`, `*.task`, `*.bin`), private paths, hostnames or secrets. Synthetic frames are generated at test time into a temp dir; nothing binary is committed.
- `.paircoder/context/`, `.paircoder/tasks/`, `.paircoder/history/`, agent memory (`.claude/agent-memory/`) and `.env` stay gitignored. Do not modify `.gitignore`. Stage files by name; never `git add -A`.
- **TDD.** Every task is test first: write the failing proving test, then the code.
- **Every module has its own proving test**, and every new module is imported by the `station-watch run`, `station-watch watchdog` or `station-watch evaluate` path (the HF1.8 orphan test in `tests/test_watchdog.py` must keep passing with the new entry point added to its roots).
- **Gate.** `ruff check`, `ruff format --check`, `pytest`, and `bpsai-pair arch check --strict src tests` all pass. Keep every source file under 400 lines; split before that.
- Do not edit README's "Status" section or the principles table. README changes belong to HF2.3 ("Run it" Detect paragraph) and HF2.9 (measured numbers section) only.
- Do not register anything persistent (cron, launchd, services) from a task worktree.
- **Merge gate for the orchestrator (not a driver step).** Engage reporting tasks "done" is not evidence: HF1 had a P0 hidden behind "8 done". Before merging, the orchestrator runs `station-watch run` (with Detect enabled, on a synthetic rail clip) and `station-watch watchdog` on the real path, reads the Log back, and greps `src/station_watch/` for modules with no importers. Any unwired module blocks the merge.

**Models.** Every task is overridden to `claude-opus-4-8`: these are integration and correctness tasks where a module that is written but never wired does not count.

---

## Phase 1: Geometry and synthetic station

### HF2.1 — Detect config, fiducial-anchored geometry, synthetic rail frames, and the Capture clock carry-over | Cx: 5 | P0

**Description:** Lay the ground every Detect task stands on.
1. **Carry-over from HF1:** in `src/station_watch/runner/pipeline.py`, pass `clock=self._clock` into `Capture(...)` so frame records and verdicts share the runner's injected clock. Add a test that a runner built with a fake clock produces `FrameRecord.ts` values from that clock.
2. **Config:** add a required top-level `detect` section to `StationConfig` (and `REQUIRED_NESTED_KEYS`), with: `persistence_frames` (N consecutive agreeing visible frames before a state is emitted), `emit_interval_s` (a target's current state is re-emitted at least this often), `rail_positions` (map of position id to a region), `station_zone` (id and region), `keepout_rois` (map of zone id to region and `active: true|false`), and thresholds for blur, darkness and occlusion. A **region** is a quadrilateral in **marker units** relative to the fiducial's center and axes (1.0 = one marker side), so it maps into pixels through the marker's four detected corners. Validation (K9): every id in `required_slots` must have a `detect.rail_positions` entry and every id in `keepout_zones` a `detect.keepout_rois` entry, else loading fails naming the dotted key (e.g. `detect.rail_positions.rail_pos_2`). Update `config/station-example.yaml` (empty positions and zones still allowed, comments explaining marker units) and `tests/fixtures/station-slots.yaml` (rename slots to rail position ids and update the four fixture JSONL files to match, keeping their scenarios and expected verdicts).
3. **Geometry:** extend `src/station_watch/capture/fiducial.py` (or a new `src/station_watch/detect/geometry.py`) with `find_marker_corners(...)` and `region_to_pixels(region, corners)` returning a pixel polygon, plus `None` when the marker is not found. Capture's existing `find_marker_center` keeps working.
4. **Synthetic station helper:** `tests/helpers/synth_station.py`, built on the HF1 `synth_video.py` writer (lossless FFV1 or PNG sequence into a temp dir, Gaussian sensor noise). It renders the panel bench seen from above: backplate, a horizontal DIN rail, components at rail positions (terminal blocks as narrow blocks, a breaker or relay as a wider block, distinct colors), slotted wire duct strips, a taped station-zone outline and a taped keep-out-zone outline, and the ArUco marker (DICT_4X4_50, id 0) fixed on the backplate. Per-frame scripting knobs: which positions hold a component, a component shifted off its seat by K px, a skin-toned hand blob at a given position and size (occluding a position, or inside the keep-out zone), motion in the station zone (a moving tool or hand blob), dim lighting, blur, and marker hidden. The helper returns both the capture path and the per-frame ground truth it rendered (a list of dicts), so later tests and the harness's synthetic mode get labels for free.

Exposes (consumed by HF2.2 to HF2.8): `find_marker_corners(frame, dictionary_id, marker_id) -> np.ndarray | None` (shape (4, 2), float32, ArUco corner order); `region_to_pixels(region, corners) -> np.ndarray | None` (shape (4, 2), int32; `None` when corners is `None`); `StationConfig.detect` as a dict with the keys above; `tests/helpers/synth_station.py::write_synth_station_clip(dir_path, script, **render_opts) -> tuple[Path, list[dict]]`, where each ground-truth dict is `{"frame_id": int, "positions": {id: "present"|"absent"|"not_seated"|"occluded"}, "motion": bool, "keepout": {zone_id: bool}, "marker_visible": bool, "dim": bool, "blur": bool}`.

**AC:**
- [ ] Runner-with-fake-clock test proves `Capture` receives the runner's clock (frame `ts` values come from it)
- [ ] Loading a config whose `required_slots` names a position missing from `detect.rail_positions` fails naming that dotted key; same for `keepout_zones` vs `detect.keepout_rois`; missing `detect` or any required `detect` sub-key fails naming it (test per case)
- [ ] `region_to_pixels` maps a marker-unit region to the expected pixel polygon for a marker rendered at two different positions and scales (test), and returns `None` when the marker is absent
- [ ] `synth_station` writes a clip into a temp dir that is read back through the real `cv2.VideoCapture` file path, and its returned ground truth has one entry per frame (test); no binary file is committed
- [ ] HF1 tests still pass with the renamed fixture targets; the four fixture files keep their expected verdicts

**Depends on:** None
**Model:** claude-opus-4-8

---

## Phase 2: Detect

### HF2.2 — Detect rail positions: present and seated / absent / unknown, with N-frame persistence | Cx: 5 | P0

**Description:** Implement the rail-position half of component 2 in `src/station_watch/detect/`. Split it in two:
- **Pure per-frame reading** `read_positions(frame, frame_id, ts, config) -> list[PositionReading]`: find the marker corners; if the marker is missing, every position reads `unknown` (cause `fiducial_missing`). Otherwise map each region to pixels; a region partly outside the frame reads `unknown` (`out_of_frame`); a region that is too dark, too blurred (variance of the Laplacian below threshold), or occluded (a foreground blob, such as the skin-toned hand, covering more than the configured fraction of the region) reads `unknown` with that cause. A visible region reads `occupied` or `empty` from features compared against the configured empty-rail appearance (for example edge density plus mean color difference; name the method). An occupied region whose component extent is offset from the seat by more than `seat_tolerance` (marker units) reads `not_seated`. No time thresholds here.
- **Persistence** `PositionTracker`: emits `part_present` only after `persistence_frames` consecutive visible frames read `occupied` and seated; `part_absent` only after N consecutive frames read `empty` or `not_seated` (with `detector_output.cause` = `empty` or `not_seated`); anything else, including any `unknown` frame, which resets the streak, emits `part_unknown` with the cause. It emits an `Observation` on every state change and re-emits the current state every `emit_interval_s`. Each observation has `method` naming the detector and its version, `confidence_ceiling` lowered by blur, dimness and partial occlusion (K3), `detector_output` carrying the raw scores and the frame ids of the persistence streak (K4).
`part_unknown` must never be emitted as, or folded into, `part_present`.

Exposes (consumed by HF2.3): `PositionTracker(config, run_id)` with `update(frame, frame_id, ts, corners) -> list[Observation]` and `unknown_all(frame_id, ts, cause, detail) -> list[Observation]`; `read_positions(...)` stays a pure function.

**AC:**
- [ ] Proving test on a synthetic rail clip through the real file path: positions with components read `part_present` only after N frames; an empty position reads `part_absent`; each observation cites frame ids that exist in the clip
- [ ] A hand blob covering a filled position for fewer than N frames, then lifting, yields `part_unknown` during the cover and `part_present` only after N clean frames again; it never yields `part_present` while covered (test)
- [ ] Marker hidden: every position reads `part_unknown` with cause `fiducial_missing` (test)
- [ ] A component shifted off its seat beyond tolerance reads `part_absent` with cause `not_seated`; within tolerance it reads `part_present` (test)
- [ ] Blurred or dim frames lower `confidence_ceiling` relative to sharp frames of the same scene (test)
- [ ] The camera bumped within `fiducial.tolerance_px` still maps regions correctly, because regions follow the marker (test with a shifted marker)

**Closes on Kevin's clips (not an engage AC):** region and threshold calibration on the real backplate and lamp; rail position states checked against labels on clip set v1.

**Interface:** From HF2.1: `find_marker_corners(frame, dictionary_id, marker_id) -> np.ndarray | None` (shape (4, 2), float32); `region_to_pixels(region, corners) -> np.ndarray | None` (shape (4, 2), int32); `config.detect` keys `persistence_frames`, `emit_interval_s`, `rail_positions` (id to marker-unit quadrilateral), blur, dark and occlusion thresholds; `write_synth_station_clip(dir_path, script, **render_opts) -> (path, truth)` with per-frame truth dicts carrying `positions: {id: present|absent|not_seated|occluded}`. Observations are `station_watch.records.Observation` (HF1.1 format, unchanged); record_id is derived by the dataclass, never built by hand.

**Depends on:** HF2.1
**Model:** claude-opus-4-8

### HF2.3 — Wire Detect into Capture and the runner | Cx: 3 | P0

**Description:** Detect only counts once `station-watch run` uses it. Give `Capture` an optional `detector` hook called on each processed frame (after the frame record and fiducial check, same pixels) whose observations are appended through the same serialized Log writer. The runner builds the detector from the `detect` config whenever any rail position, station zone or keep-out zone is configured. A Detect exception on a frame must not kill Capture and must not look like a disconnected camera: it emits `part_unknown` (and, once HF2.6 lands, `zone_unknown`) for every configured target with cause `detect_error` and the error text, reported once on stderr per episode. `--observations` stays as labeled fixture input; passing both `--observations` and a config with Detect targets is refused with a message (one source of observations per run). Add a short paragraph to README "Run it" saying Detect runs when the config lists rail positions or zones, and nothing else in README.

Exposes (consumed by HF2.4, HF2.6, HF2.7): `src/station_watch/detect/detector.py::Detector(config, run_id)` composing trackers that share the tracker protocol `update(frame, frame_id, ts, corners) -> list[Observation]` and `unknown_all(frame_id, ts, cause, detail) -> list[Observation]`; `Detector.process(frame, frame_id, ts) -> list[Observation]` finds the marker once per frame and fans out; `Detector.add_tracker(tracker)`; `Capture(..., detector=Detector | None)`.

**AC:**
- [ ] Proving test (K14): the real `station-watch run` subprocess on a synthetic rail clip with one position empty writes Detect `Observation` rows (method is the detector, not `fixture`), and the Log shows a `missing_part` verdict citing frames from the clip
- [ ] A detector that raises on one frame produces `part_unknown` with cause `detect_error` for every position, Capture keeps writing frames, and no `disconnected` BlindRecord is written (test)
- [ ] `--observations` together with Detect targets in the config exits non-zero naming the conflict (test)
- [ ] The HF1.8 orphan test passes with every new `detect` module reached from the run path
- [ ] **Counterparty:** `src/station_watch/judge.py::Judge` consumes the Observation rows Detect appends to the Log (named artifact; Judge reads them through `JudgeInputs`, no side channel)
- [ ] **E2E Conformance:** a real `station-watch run` subprocess on a synthetic rail clip produces Detect rows and a Judge verdict citing them, both read back from the Log the run wrote (not a unit test of either side)

**Interface:** From HF2.2: `PositionTracker(config, run_id)`, `update(frame, frame_id, ts, corners) -> list[Observation]`, `unknown_all(frame_id, ts, cause, detail) -> list[Observation]`. Log appends go through `Log.append(record)` (HF1.2, idempotent on `record_id`, one serialized writer per process). The runner already loads fixtures with `load_fixture_observations(path, run_start_ts, run_id)` (HF1.1).

**Depends on:** HF2.2
**Model:** claude-opus-4-8

---

## Phase 3: Dwell, slope, keep-out

### HF2.4 — Station-zone motion and dwell against measured step times | Cx: 5 | P0

**Description:** Add the motion half of Detect: for the configured `station_zone`, compute frame-to-frame change inside the zone polygon (mapped through the marker) and emit `motion` / `no_motion` with N-frame persistence. Calibrate against sensor noise: the threshold sits above the zone's measured noise floor (HF1's noise score idea), so a live still scene reads `no_motion`, never `motion`. Unknown frames (marker missing, dark) emit nothing for the zone, so Judge's existing blind handling governs. Then make the stall threshold come from **measured step times**: add `src/station_watch/steps.py` with `step_durations(observations)` (a step is a contiguous `motion` run in the station zone, bounded by `no_motion`) and `step_stats(durations)` (count, p50, p95). The Judge's stall window becomes `p95 + grace_s` when the config's `detect.step_times_path` points at an existing measurement file (written by HF2.8), else `takt_s + grace_s`. The runner prints at startup which threshold is in use and where it came from, for example `stall threshold 35.0 s (configured takt; no measured step times)`; a configured path that does not exist fails loud (K9).

Exposes (consumed by HF2.5 and HF2.8): `steps.step_durations(observations) -> list[Step]` where `Step` is a frozen dataclass `(target, start_frame_id, end_frame_id, start_ts, end_ts, duration_s)`; `steps.step_stats(durations) -> dict` with `count`, `p50_s`, `p95_s`; `steps.write_step_times(path, stats, provenance)`; the `step_times.json` schema `{"provenance": {...}, "metrics": {"count": int, "p50_s": float, "p95_s": float}}`, which HF2.4 reads and HF2.8 writes; `MotionTracker` following the HF2.3 tracker protocol.

**AC:**
- [ ] Proving test: a synthetic clip with periodic tool motion in the station zone, then a still period longer than the window, run through real Capture and Detect, yields `stalled` only after the window, citing the still frames
- [ ] A still-but-live (noise only) zone reads `no_motion`; a frozen feed is caught by Capture's `frozen` record and Judge says `unobservable`, never `stalled` (test)
- [ ] `step_durations` and `step_stats` are correct on hand-built observation sequences (test)
- [ ] With a step-times measurement file present, the stall window equals its p95 plus grace; absent path key, the window is `takt_s + grace_s`; a configured path that does not exist refuses to start naming it; the startup line states the source (tests)
- [ ] **Counterparty:** `src/station_watch/steps.py::write_step_times(path, stats, provenance)` is the producer of `step_times.json` (HF2.8 calls it), and the runner's startup is its consumer (named artifacts)
- [ ] **E2E Conformance:** a real `station-watch run` on a normal synthetic clip, then `write_step_times` over the steps read from that run's Log, then a second real `station-watch run` with `detect.step_times_path` set prints the measured source at startup and stalls at p95 plus grace

**Closes on Kevin's clips (not an engage AC):** real p95 step time from normal cycles in clip set v1; stall flags checked on the labeled stall clips.

**Interface:** From HF2.3: `Detector.add_tracker(tracker)`; tracker protocol `update(frame, frame_id, ts, corners) -> list[Observation]` and `unknown_all(frame_id, ts, cause, detail) -> list[Observation]`. From HF2.1: `region_to_pixels`, `config.detect.station_zone`, `write_synth_station_clip` with a `motion` truth flag. Judge (HF1.5) reads observations from the Log; the existing stall rule (`no_motion` streak older than the window) is kept, only the window's source changes.

**Depends on:** HF2.3
**Model:** claude-opus-4-8

### HF2.5 — Cycle-time slope alarm (K12) | Cx: 3 | P0

**Description:** Rate, not level. Add fault kind `cycle_time_creep` to `FaultKind` (records round-trip tests updated). In the Judge, over the last `detect.slope.window_steps` completed steps (from HF2.4's `step_durations`), fit a least-squares slope of step duration against step index; when the slope is at least `detect.slope.min_s_per_step` and the newest step is still under the stall window, raise `cycle_time_creep` on the station zone, citing the first and last frame ids of every step in the window (K4). Fewer than `window_steps` steps: no slope verdict either way. Alarm opens one episode for it like any other fault and recovers after the configured healthy verdicts (K11).

**AC:**
- [ ] Proving test: a synthetic clip whose motion steps lengthen steadily, each under the stall window, raises `cycle_time_creep` before any `stalled`, and Alarm opens exactly one episode
- [ ] Steady step times with ordinary jitter raise no `cycle_time_creep` (test); fewer than `window_steps` steps raise none (test)
- [ ] The fault cites frame ids present in the clip (test)

**Closes on Kevin's clips (not an engage AC):** a slope threshold chosen from the spread of real normal cycles, not a guess.

**Interface:** From HF2.4: `step_durations(observations) -> list[Step]`, `Step(target, start_frame_id, end_frame_id, start_ts, end_ts, duration_s)`, and the Judge's current stall window. `Fault(kind, target, frame_ids)` (HF1.1) is unchanged apart from the new `FaultKind` value.

**Depends on:** HF2.4
**Model:** claude-opus-4-8

### HF2.6 — Keep-out detection with a local Apache-2.0 person or hand model | Cx: 5 | P1

**Description:** Add keep-out to Detect in `src/station_watch/detect/keepout.py` behind a small backend interface (`detect_people(frame) -> list[Box with score]`). The model backend runs an Apache-2.0 detector locally through `cv2.dnn` (no new Python dependency); the default is YOLOX (Apache-2.0, https://github.com/Megvii-BaseDetection/YOLOX) in ONNX form, person class, unless the driver finds and documents a better Apache-2.0 hand model. The module docstring states the model, its license, its source URL and the expected SHA-256. Weights are never committed: add `station-watch fetch-model` that downloads the file from its official release into `data/local/models/` (already gitignored) and verifies the SHA-256 the driver records from that download; this is the only network call in the package and it is never made by `run`. Startup with keep-out zones configured and the weights missing or failing the hash refuses to start, naming the file (K9). Zone logic: a box overlapping an active zone polygon by more than `detect.keepout.min_overlap` for N consecutive frames emits `person_in_keepout`; N consecutive clear frames emit a new kind `zone_clear`; marker missing, detector error or no model output emits a new kind `zone_unknown`. Add both kinds to `ObservationKind`. Judge change (K1): a configured keep-out zone counts as clear only on a latest `zone_clear`; `zone_unknown` or no reading makes the station `unobservable`, never `healthy`. Update the HF1 fixtures (`normal_cycles.jsonl` and the others) with `zone_clear` rows so their expected verdicts hold.

Exposes (consumed by HF2.7): `ObservationKind.ZONE_CLEAR` (`zone_clear`) and `ObservationKind.ZONE_UNKNOWN` (`zone_unknown`); `KeepoutTracker(config, run_id, backend)` following the HF2.3 tracker protocol; the backend protocol `detect_people(frame) -> list[tuple[x0, y0, x1, y1, score]]`, injectable for tests; `station-watch fetch-model`.

**AC:**
- [ ] Zone logic proving test with an injected fake backend returning scripted boxes over a synthetic clip: entry yields `person_in_keepout` after N frames and a `keepout_entry` fault citing those frames; leaving yields `zone_clear`; an inactive zone never faults (tests)
- [ ] A keep-out zone with only `zone_unknown`, or no reading at all, never yields `healthy` (test)
- [ ] Startup with keep-out zones configured and missing or hash-mismatched weights exits non-zero naming the file (test)
- [ ] `station-watch run` makes no network connection: a test runs it with socket creation blocked and it still completes on a synthetic clip with the fake backend
- [ ] A test asserts no model weight file (`*.onnx`, `*.pt`, `*.pth`, `*.tflite`, `*.task`, `*.bin`) is tracked by git
- [ ] A real-model test runs YOLOX on a generated frame when the weights are present locally and is skipped with the reason "model weights not present" when they are not; the skip is visible in the pytest summary, never a silent pass
- [ ] The PR body states the model license (Apache-2.0) and source

**Closes on Kevin's clips (not an engage AC):** keep-out flags verified by eye on real clips (PLAN 6, Thu 10/8); whether a person detector sees a hand from the camera's angle, or a hand model is needed.

**Interface:** From HF2.3: `Detector.add_tracker(tracker)` and the tracker protocol `update(frame, frame_id, ts, corners) -> list[Observation]`, `unknown_all(frame_id, ts, cause, detail) -> list[Observation]`. From HF2.1: `region_to_pixels`, `config.detect.keepout_rois` (id to region plus `active`), `write_synth_station_clip` with a `keepout` truth flag. Startup failures raise `StartupError` (HF1.7, `runner/startup.py`) with a message naming the missing piece.

**Depends on:** HF2.3
**Model:** claude-opus-4-8

---

## Phase 4: Real output, end to end, and measured numbers

### HF2.7 — Judge and Alarm on real Detect output, end to end | Cx: 5 | P0

**Description:** Re-run HF1's fixture scenarios on real Detect output. For each of `normal_cycles`, `stall`, `missing_part` and `keepout_entry`, build a synthetic rail clip that depicts the same scenario (the keep-out case uses the fake backend), run it through the real `station-watch run` subprocess with a `record` alarm sink, and assert the same Judge verdict and the same Alarm episodes the fixture tests assert. Add one more: the operator's hand passes over a filled rail position briefly. Decide and implement how a short `part_unknown` is treated, under this rule: the verdict while unknown is never `healthy`, but an unknown that lasts less than `detect.unknown_grace_s` does not open an alarm episode, while a longer one does; blind-camera `unobservable` still opens at once, exactly as in HF1. Write the rule into the Alarm module docstring.

**AC:**
- [ ] Four end-to-end proving tests (K14), one per HF1 fixture scenario, each through `station-watch run` on a synthetic clip with real Capture and Detect, reaching the same verdicts and exactly the episodes the fixture tests reach (read back from the Log and the record sink)
- [ ] A brief hand pass over a filled position opens no episode and never reads `healthy` while covered; a cover longer than `unknown_grace_s` opens exactly one episode and recovers once after the hand leaves (tests)
- [ ] A frozen or dark clip still opens its blind episode immediately, with no grace (test)
- [ ] `station-watch watchdog` beside a Detect-enabled run with `--stop-stage alarm` still fires and names the alarm stage (test)

**Interface:** From HF2.6: kinds `zone_clear`, `zone_unknown`, `person_in_keepout`; injectable backend `detect_people(frame) -> list[(x0, y0, x1, y1, score)]`. From HF2.5: fault kind `cycle_time_creep`. From HF2.3: `station-watch run --config --source --log` with Detect enabled by config. From HF1.6: the `record` alarm sink writes one line per alarm and per recovery; `--stop-stage alarm` from HF1.7.

**Depends on:** HF2.5, HF2.6
**Model:** claude-opus-4-8

### HF2.8 — Evaluation harness writing committed measurement files (K13) | Cx: 5 | P0

**Description:** Add `station-watch evaluate --config <yaml> --manifest <path> --out <dir>` in `src/station_watch/evaluate/`. The **manifest** (YAML, format defined and documented in the module) lists clips by session, each with a clip path relative to a clips directory and labeled intervals per target (rail position states, stall intervals, keep-out entries, injected camera faults with their start frame). Labels are text and can be committed (`measurements/labels/`); clips stay local under `data/local/clips/` (gitignored). The harness runs each clip through the real Capture, Detect, Judge and Alarm path at an accelerated `speed`, then writes JSON measurement files: precision and recall per flag type (`missing_part`, `stalled`, `keepout_entry`, `cycle_time_creep`) and per rail-position state, the confusion matrix including misses, latency from capture `ts` to verdict `ts` (median and p95), time to alarm per injected fault, the HF2.4 step stats (`step_times.json`, the file HF2.4's stall window reads), and the same numbers for the naive frame-difference baseline (PLAN 3.5). Every file carries provenance: dataset name and kind (`real` or `synthetic`), manifest SHA-256, config SHA-256, git commit, detector method and version, clip count, sessions, and the UTC date. Results are reported per session so a train / test split by recording session is visible (never by adjacent frames).
Honesty rules: when `--manifest` is absent, or points at a file that does not exist, the harness prints `no labeled set present: <path>`, exits with a distinct non-zero code, and writes no measurement file. When the manifest lists a clip that is missing, it fails naming the clip; it never reports numbers over a partial set. A `--synthetic` mode generates a labeled set with `synth_station` at run time (nothing binary written outside a temp dir) and writes files tagged `dataset_kind: synthetic` under `measurements/synthetic/`; commit those so the harness is proven, and never present them as real performance.

Exposes (consumed by HF2.9): every measurement file is JSON `{"provenance": {"dataset": str, "dataset_kind": "real"|"synthetic", "manifest_sha256": str, "config_sha256": str, "git_commit": str, "detector": str, "clips": int, "sessions": [str], "date_utc": str}, "metrics": {...}}`, with metric keys addressable as dotted paths under `metrics` (for example `missing_part.precision`, `latency.p95_s`).

**AC:**
- [ ] Proving test: `station-watch evaluate --synthetic` produces measurement files whose precision, recall and latency are recomputed correctly from its own ground truth (test checks a hand-countable small set exactly)
- [ ] Absent or missing manifest: message `no labeled set present`, distinct non-zero exit, no file written (test)
- [ ] A manifest naming a missing clip fails naming that clip and writes nothing (test)
- [ ] Every measurement file carries the provenance fields above (test); synthetic files carry `dataset_kind: synthetic`
- [ ] The baseline is computed on the same clips and written beside the main numbers (test)
- [ ] `measurements/synthetic/` files are committed; `station-watch evaluate` is reached by the orphan test's roots
- [ ] **E2E Conformance:** a real `station-watch evaluate --synthetic` subprocess writes `step_times.json`, and a real `station-watch run` subprocess with `detect.step_times_path` pointing at it prints the measured threshold source at startup (producer and consumer both on the real CLI path)

**Closes on Kevin's clips (not an engage AC):** the first real precision, recall and latency numbers on clip set v1 (`measurements/v1/`), the real `step_times.json`, and per-session results once a second session exists.

**Interface:** From HF2.1: `write_synth_station_clip(...) -> (path, truth)` and its truth dict schema. From HF2.4: `step_stats(durations) -> {count, p50_s, p95_s}` and the `step_times.json` schema `{provenance, metrics: {count, p50_s, p95_s}}`. From HF2.7: the end-to-end run path whose verdicts and episodes are read back from the Log.

**Depends on:** HF2.7
**Model:** claude-opus-4-8

### HF2.9 — Claims test: README numbers must match measurement files | Cx: 3 | P0

**Description:** Add a "Measured performance" section to README (the only README change besides HF2.3's paragraph). Every number in it is written with a claim marker naming its source, for example `<!-- claim: measurements/v1/detect.json#missing_part.precision -->0.94`, and states whether the dataset is real or synthetic. Add `tests/test_claims.py`: it parses every claim marker in README, loads the named file and key, and fails if the value differs (after the stated rounding) or the file or key is missing; it also fails if the "Measured performance" section contains any number with no claim marker. While no real measurement file exists, the section says plainly that no real numbers exist yet because labeled clip set v1 has not been recorded, and may cite only synthetic harness numbers, labeled as synthetic. Finish with the wiring sweep: grep `src/station_watch/` for modules with no importers and wire or remove them.

**AC:**
- [ ] Proving test: changing one measurement value in a temp copy (or one README number) makes the claims test fail, naming the claim (test of the test)
- [ ] An unmarked number in the "Measured performance" section fails the claims test (test)
- [ ] README states that no real numbers exist until clip set v1 is recorded; any synthetic number is labeled synthetic and matches its file
- [ ] README Status and principles sections unchanged (the HF1.8 test still passes)
- [ ] Orphan test passes; CI green: ruff check, ruff format --check, pytest; `bpsai-pair arch check --strict src tests` passes

**Closes on Kevin's clips (not an engage AC):** README carries real numbers from `measurements/v1/`, each held by a claim marker.

**Interface:** From HF2.8: measurement files are JSON `{provenance: {dataset, dataset_kind, manifest_sha256, config_sha256, git_commit, detector, clips, sessions, date_utc}, metrics: {...}}`; a claim names `<file path>#<dotted key under metrics>`; files live under `measurements/synthetic/` (committed now) and `measurements/v1/` (after clip set v1).

**Depends on:** HF2.8
**Model:** claude-opus-4-8

---

## Delivery Summary

| Task | Title | Cx | Priority | Depends on | Model |
|---|---|---|---|---|---|
| HF2.1 | Detect config, fiducial-anchored geometry, synthetic rail frames, Capture clock carry-over | 5 | P0 | None | claude-opus-4-8 |
| HF2.2 | Rail positions: present and seated / absent / unknown, N-frame persistence | 5 | P0 | HF2.1 | claude-opus-4-8 |
| HF2.3 | Wire Detect into Capture and the runner | 3 | P0 | HF2.2 | claude-opus-4-8 |
| HF2.4 | Station-zone motion and dwell against measured step times | 5 | P0 | HF2.3 | claude-opus-4-8 |
| HF2.5 | Cycle-time slope alarm (K12) | 3 | P0 | HF2.4 | claude-opus-4-8 |
| HF2.6 | Keep-out with a local Apache-2.0 model | 5 | P1 | HF2.3 | claude-opus-4-8 |
| HF2.7 | Judge and Alarm on real Detect output, end to end | 5 | P0 | HF2.5, HF2.6 | claude-opus-4-8 |
| HF2.8 | Evaluation harness and committed measurement files (K13) | 5 | P0 | HF2.7 | claude-opus-4-8 |
| HF2.9 | Claims test: README numbers match measurement files | 3 | P0 | HF2.8 | claude-opus-4-8 |

Total: 39 Cx. `bpsai-pair calibration recommend-model` returns haiku by routing for these task types; every task is overridden to `claude-opus-4-8` on purpose (integration tasks; HF1 lesson that a written but unwired module does not count).

## Priority Order

1. HF2.1 (geometry and synthetic station; everything stands on it)
2. HF2.2 (rail position states)
3. HF2.3 (Detect on the real run path)
4. HF2.4 (motion, dwell, measured step times)
5. HF2.5 (slope alarm)
6. HF2.6 (keep-out; P1 because PLAN 6 names keep-out third in the cut order, after the MCU stretch and Explain)
7. HF2.7 (fixture scenarios re-proven on real Detect output)
8. HF2.8 (evaluation harness)
9. HF2.9 (claims test and wiring sweep)
