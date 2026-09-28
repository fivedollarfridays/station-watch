# HF1 — Station Watch foundation: the camera-health path, end to end

**Base:** main
**Plan type:** feature
**Summary:** record formats, Capture with blind records, Log, Judge, Alarm, Watchdog and a runner, each with its own proving test, so a blind camera is proven to alarm and never to read as healthy

## Why this lane exists

Station Watch is the HackFW 2026 MADE track entry (demo Fri Oct 30). One camera watches one assembly station. It flags stalled work, a missing kitted part, and a hand in a keep-out zone, and it alarms loudly when the camera itself goes blind. The pipeline is Capture, Detect, Judge, Log, Alarm, plus a Watchdog on its own clock (see README "Pipeline" and "Design principles").

HF1 builds every component except Detect. In HF1 the only real input path is camera health: Capture's blind records carry no pixels, so they reach Judge without Detect. Judge and Alarm tests for stall, missing part and keep-out run on committed **fixture observations** in the Detect observation format fixed by HF1.1. Detect itself is HF2.

**Design rules that bind every task** (README principles table):
- K1: three outcomes per station: healthy, fault, unobservable. Unobservable never folds into healthy.
- K2: a camera is live only when a new frame with a new capture timestamp and different content arrived within the window. A connected stream is not liveness.
- K5: the alarm answers the fault; nothing optional can delay or suppress it.
- K7/K8: the Watchdog runs on a clock it does not own, and the alarm rail does not share the camera's process.
- K9: fail loud at startup. Missing camera config, station expectations or alarm rail: refuse to start and name the missing piece.
- K10: "could not get a frame" is unobservable; a crash after seeing a fault still alarms as a fault.
- K14: at least one test drives real recorded frames through the real capture path, and one drives the real alarm end to end.

**Stack.** Python 3.12, `src/station_watch/` package, `opencv-python` (Apache 2.0, includes `cv2.aruco`) and `numpy`. pytest and ruff. No detector model, no GPU, no network calls. Everything must run on macOS and on Linux; the edge box of record is decided in HF2. Every dependency added must have a license compatible with a public MIT repo (state it in the PR body).

**Repo constraints.**
- This is a PUBLIC repo. Never commit footage, private paths, hostnames, secrets, or anything from `.paircoder/context/` or `.paircoder/tasks/`. Test video is generated at test time into a temp dir (synthetic frames with sensor noise); do not commit binary video.
- Keep every source file under 400 lines; split before that. `bpsai-pair arch check --strict src tests` must pass.
- Do not edit README's "Status" section or the principles table. README "Run it" text is HF1.8's job only.
- Do not register anything persistent (cron, launchd, services) from a task worktree.
- Every task is test first: write the failing proving test, then the code.

**Models.** Every task is overridden to `claude-opus-4-8`: these are integration and correctness tasks where a module that is written but never wired does not count (see the ops memory on haiku drivers shipping unwired modules).

---

## Phase 1: Formats and storage

### HF1.1 — Project skeleton, record formats and fixture observations | Cx: 3 | P0

**Description:** Create the Python package skeleton: `pyproject.toml` (name `station-watch`, Python >= 3.12, deps `opencv-python`, `numpy`, `pyyaml`; dev deps `pytest`, `ruff`), `src/station_watch/__init__.py`, ruff config, and a GitHub Actions workflow `.github/workflows/ci.yml` that runs `ruff check`, `ruff format --check` and `pytest` on push and pull request. Define the record formats every later component shares, in `src/station_watch/records.py`, as frozen dataclasses with `to_dict()` / `from_dict()` round trip to JSON. **Every record carries two common fields:** `ts` (wall clock, ISO 8601 UTC with microseconds; the one canonical time the Log orders by and the Watchdog judges against) and `record_id` (a deterministic idempotency key, derived per type as listed below, so a replayed row dedups and two distinct events never collide):
- `FrameRecord`: station_id, camera_id, frame_id (monotonic per camera), ts (= capture wall time), capture_mono (monotonic seconds, meaningful only inside the capturing process), fingerprint (hash of raw bytes), mean_luma, noise_score. record_id = `frame:{camera_id}:{frame_id}`.
- `BlindRecord`: station_id, camera_id, ts, reason (one of `disconnected`, `frozen`, `dark`, `fiducial_missing`, `view_shifted`), evidence (dict: e.g. seconds since last frame, identical-frame count, mean luma over window, fiducial offset px), last_good_frame_id or null, seq (per-process counter). record_id = `blind:{camera_id}:{reason}:{ts}:{seq}`.
- `Observation` (the Detect output format; Detect itself is HF2): station_id, frame_id, ts, kind (`part_present`, `part_absent`, `part_unknown`, `person_in_keepout`, `motion`, `no_motion`), target (slot or zone id), method, confidence_ceiling (0..1), detector_output (dict). record_id = `obs:{station_id}:{frame_id}:{kind}:{target}`.
- `Verdict`: station_id, ts, state (`healthy`, `fault`, `unobservable`), faults (list of {kind: `stalled` | `missing_part` | `keepout_entry`, target, frame_ids}), reason (when unobservable), seq. record_id = `verdict:{station_id}:{seq}`.
- `CycleCompleted`: ts, cycle (int), stages (list). record_id = `cycle:{cycle}`.
- `AlarmEvaluated`: ts, seq, open_episodes (list of episode ids). record_id = `alarm_eval:{seq}`.
- `StationConfig` loaded from YAML: station_id, camera_id, takt_s, grace_s, required_slots, keepout_zones, liveness_window_s, dark_luma_threshold, dark_window_s, frozen_frames, recover_good_frames, recover_healthy_verdicts, fiducial (dictionary id, marker id, expected center px, tolerance px), alarm (sinks: list of `screen` | `sound`), watchdog (cycle_window_s, alarm_eval_window_s, sinks). HF1 is single station: one camera, one station; an episode for a blind camera names that one station. Loading a config with a missing required key raises an error that names the key (K9).
Add fixture observation sequences under `tests/fixtures/observations/` (JSONL): `normal_cycles.jsonl`, `stall.jsonl`, `missing_part.jsonl`, `keepout_entry.jsonl`, each with a short README comment line describing the scenario and the expected Judge verdict. Add `config/station-example.yaml`.

**AC:**
- [ ] `pip install -e .[dev]` then `pytest` and `ruff check` pass locally; `.github/workflows/ci.yml` exists and runs ruff check, ruff format --check and pytest
- [ ] Every record type (Frame, Blind, Observation, Verdict, CycleCompleted, AlarmEvaluated) round-trips through `to_dict`/`from_dict` and JSON, proven by tests; unknown `reason`, `kind` or `state` values are rejected; `record_id` is deterministic and matches the per-type rule above (test)
- [ ] Loading a station config missing any required key raises an error whose message names that key (test per key)
- [ ] Four fixture observation files exist and each parses into `Observation` records in a test

**Depends on:** None
**Model:** claude-opus-4-8

### HF1.2 — Log: append-only, durable, deduplicated | Cx: 3 | P0

**Description:** Implement component 4 in `src/station_watch/log.py`: one local append-only store for every record type plus the pipeline's own `cycle_completed` row (ts, cycle number, stages completed). Use SQLite in WAL mode (stdlib `sqlite3`) with a unique idempotency key per row, so a replayed row is ignored, not duplicated. Rows dedup on `record_id`. Reads are ordered by each row's canonical `ts`, not insert order. API: `append(record)`, `newest(kind)`, `since(ts, kinds)`. Opening a log path whose directory does not exist raises an error naming the path (K9).

**AC:**
- [ ] Proving test: a child process is killed (SIGKILL) mid-write loop; after reopening, the newest row is correct and intact, and replaying the last batch adds no duplicates
- [ ] `newest("cycle_completed")` and `since(...)` return rows ordered by recorded time even when inserted out of order (test)
- [ ] Log is used by at least one later task through this API (checked at HF1.8 integration)

**Depends on:** HF1.1
**Model:** claude-opus-4-8

---

## Phase 2: Capture

### HF1.3 — Capture: stamped frames from a camera or a recorded file | Cx: 5 | P0

**Description:** Implement component 1 in `src/station_watch/capture/`: a frame source abstraction over `cv2.VideoCapture` that accepts a device index or a file path, and a `Capture` loop that turns each frame into a `FrameRecord` (frame id, wall and monotonic capture time, raw-bytes fingerprint, mean luma, and a noise score: mean absolute per-pixel difference from the previous frame). File sources are paced at their native fps so a recording replays in real time (a `speed` factor is allowed for tests). Add `tests/helpers/synth_video.py` that writes a synthetic clip at test time into a temp dir using a **lossless** codec (FFV1 in `.mkv`, or a PNG image sequence read by `cv2.VideoCapture` if FFV1 is unavailable), so injected noise survives decoding: a static scene plus per-frame Gaussian sensor noise, with options to freeze (repeat identical bytes from frame N), go dark from frame N, drop out (stop), and move a drawn ArUco marker (`cv2.aruco`, DICT_4X4_50) by K pixels from frame N.

**AC:**
- [ ] Proving test (K14): a synthetic clip generated by the helper is read through the real `cv2.VideoCapture` file path and every frame yields a `FrameRecord` with strictly increasing frame_id and capture_mono
- [ ] Noise score is above zero for live frames of a still scene and exactly zero for byte-identical frames (test)
- [ ] A test asserts that consecutive decoded frames of a still-but-live synthetic clip have different fingerprints (the codec is lossless enough for HF1.4)
- [ ] No test writes outside a temp dir; no binary video is committed

**Depends on:** HF1.1
**Model:** claude-opus-4-8

### HF1.4 — Capture blind records: disconnected, frozen, dark, fiducial missing, view shifted | Cx: 5 | P0

**Description:** Extend Capture so it never goes silent: when the source fails it emits a `BlindRecord` with a reason and evidence instead of nothing.
- `disconnected`: no frame within `liveness_window_s`, or the source read fails. Emitted from a timer, not from the frame loop, so a hung read still produces it.
- `frozen`: `frozen_frames` consecutive frames with identical fingerprints (a live still scene has sensor noise, so its fingerprints differ; README "Frozen vs still").
- `dark`: mean luma below `dark_luma_threshold` for the whole `dark_window_s`, judged over the window so a single dim frame (lighting flicker) does not trip it.
- `fiducial_missing` / `view_shifted`: the configured ArUco marker is not found for the window, or its center moved more than `tolerance_px`.
Each blind condition clears only after the configured number of good frames (K11). Write blind records and frame records to the Log (HF1.2).

**AC:**
- [ ] Proving test: replay a synthetic clip that freezes; a `frozen` BlindRecord appears within the window, and a still-but-live clip of the same length produces none
- [ ] One proving test per remaining reason using the synthetic helper, each asserting the reason and its evidence fields
- [ ] A single dark frame inside an otherwise normal clip produces no `dark` record (flicker test)
- [ ] A source that hangs on read (fake source that blocks) still yields `disconnected` within `liveness_window_s` plus one second
- [ ] Records reach the Log through the Log API, asserted by reading the Log back in the tests

**Depends on:** HF1.2, HF1.3
**Model:** claude-opus-4-8

---

## Phase 3: Judge and Alarm

### HF1.5 — Judge: healthy, fault, unobservable | Cx: 5 | P0

**Description:** Implement component 3 in `src/station_watch/judge.py`: the only place "age versus window" and "absence is a fault" live. Input: the station config and the Log: frame, blind and observation records are all read from the Log (observations are appended to the Log by whoever produces them: fixture loader now, Detect in HF2), so every input shares the Log's `ts` ordering. Output the `Verdict` record defined in HF1.1. Rules: any active blind condition makes the station unobservable, never healthy and never a fault verdict based on stale observations; a unit with `no_motion` longer than `takt_s + grace_s` is `stalled`; a required slot `part_absent` at kit check is `missing_part`; `part_unknown` never counts as present or absent; `person_in_keepout` while the zone is active is `keepout_entry`. Every fault cites the frame ids it came from (K4). Write verdicts to the Log.

**AC:**
- [ ] Proving test: `stall.jsonl` yields `stalled` only after takt plus grace; the same timeline with a real `frozen` BlindRecord from Capture (HF1.4 synthetic clip) yields `unobservable`, never `stalled` and never `healthy`
- [ ] `normal_cycles.jsonl` yields only `healthy`; `missing_part.jsonl` yields `missing_part`; `keepout_entry.jsonl` yields `keepout_entry`; each fault cites frame ids present in the input
- [ ] A `part_unknown` for a required slot never produces `healthy` for that slot nor `missing_part` (test)

**Depends on:** HF1.1, HF1.2, HF1.4
**Model:** claude-opus-4-8

### HF1.6 — Alarm: episodes to screen and sound | Cx: 5 | P0

**Description:** Implement component 5 in `src/station_watch/alarm/`: opens and closes episodes from verdicts, one episode per root cause (a blind camera opens one episode for its station, not one per tick; the episode names the blind reason). Fires on faults and on unobservable. Sinks come from `alarm.sinks` in the station config and are pluggable: a screen sink (terminal line with station, cause, start time and cited frames) and a sound sink (macOS `afplay` of a bundled short tone, Linux `aplay`/terminal bell fallback; generate the tone file at build time or ship a tiny WAV you create, no third-party audio). An episode recovers only after the configured number of healthy verdicts (K11), and recovery emits exactly one recovery message. Alarm never reads any explanation output (K5). At startup with no alarm sink configured, refuse to start and name the missing sink (K9). Alarm writes an `alarm_evaluated` row (ts, open episodes) to the Log after each evaluation, for the Watchdog.

**AC:**
- [ ] Proving test: a fake source that disconnects (the "pulled cable"), with real Capture, Log, Judge and Alarm composed in-process by the test, produces exactly one alarm; reconnecting produces exactly one recovery, after the configured healthy count
- [ ] Fixture fault verdicts (stall, missing part, keep-out) each open one episode that cites frames; repeated identical verdicts do not open duplicates
- [ ] Startup without a sink fails with a message naming the sink (test)
- [ ] `alarm_evaluated` rows appear in the Log after each evaluation (test)

**Depends on:** HF1.5
**Model:** claude-opus-4-8

## Phase 4: Wire it, then watch it

### HF1.7 — Runner: `station-watch run` wires the pipeline | Cx: 5 | P0

**Description:** Add a CLI `station-watch` (console script) with `run --config <station.yaml> --source <device index or file> --log <path>` that wires Capture, Log, Judge and Alarm in one process and appends a `CycleCompleted` row to the Log after every full cycle. Accept `--observations <jsonl>`, which appends fixture observations to the Log on their timeline so Judge reads them like any other input (Detect replaces this in HF2); label it in help text as fixture input. Accept `--stop-stage alarm` (test and drill use only, clearly labeled) so a run can keep Capture and the cycle loop going while Alarm stops evaluating: the exact condition the Watchdog must catch. Startup fails loud (K9): missing config key, missing or unopenable source, no alarm sink, or unwritable log each exit non-zero and name the missing piece.

**AC:**
- [ ] Proving test (K14): the real `station-watch run` subprocess on a synthetic clip that goes dark partway writes one `dark` BlindRecord, opens one alarm episode, recovers once after the clip returns to normal, and writes `CycleCompleted` rows throughout (all read back from the Log)
- [ ] With `--stop-stage alarm`, Capture and `CycleCompleted` rows continue and `AlarmEvaluated` rows stop (test)
- [ ] Each startup failure named above exits non-zero with a message naming the missing piece (test per case)

**Depends on:** HF1.4, HF1.5, HF1.6
**Model:** claude-opus-4-8

### HF1.8 — Watchdog: a second clock with its own rail, plus "Run it" | Cx: 5 | P0

**Description:** Implement component 8 in `src/station_watch/watchdog.py`, started as its own process with `station-watch watchdog --config ... --log ...`. On its own timer it reads the Log's newest `CycleCompleted` and newest `AlarmEvaluated` rows and judges their age as its own **wall clock now minus the row's canonical `ts`** (same machine, so wall time is comparable across processes; monotonic time is not and must not be compared across processes) against `watchdog.cycle_window_s` and `watchdog.alarm_eval_window_s`. It alarms through its own sink instances from `watchdog.sinks`, never through the pipeline process (K7, K8). A missing or unreadable Log is itself an alarm, not a pass. The message names the stale stage. This is the fix for finding D1 in the README. Then add a "Run it" section to README (install, run on a webcam, run on a file, run the watchdog) and change nothing else in README. Grep the package for modules with no importers and wire or remove them.

**AC:**
- [ ] Proving test: `station-watch run --stop-stage alarm` and `station-watch watchdog` as two subprocesses on a synthetic clip; the watchdog fires within `alarm_eval_window_s` plus one timer tick and names the alarm stage, while Capture keeps writing frames
- [ ] Killing the run process entirely fires the watchdog for the cycle stage; a healthy run longer than two windows produces no watchdog alarm (tests)
- [ ] Watchdog with a missing Log path alarms rather than exiting quietly (test)
- [ ] Every module under `src/station_watch/` is imported by the runner or watchdog path (a test asserts this)
- [ ] README has a "Run it" section whose commands are exercised by tests; README Status and principles sections unchanged
- [ ] CI green: ruff check, ruff format --check, pytest; `bpsai-pair arch check --strict src tests` passes

**Depends on:** HF1.7
**Model:** claude-opus-4-8
