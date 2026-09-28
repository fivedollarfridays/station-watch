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
- Keep every source file under 400 lines; split before that. `bpsai-pair arch check --strict` must pass.
- Do not edit README's "Status" section or the principles table. README "Run it" text is HF1.8's job only.
- Do not register anything persistent (cron, launchd, services) from a task worktree.
- Every task is test first: write the failing proving test, then the code.

**Models.** Every task is overridden to `claude-opus-4-8`: these are integration and correctness tasks where a module that is written but never wired does not count (see the ops memory on haiku drivers shipping unwired modules).

---

## Phase 1: Formats and storage

### HF1.1 — Project skeleton, record formats and fixture observations | Cx: 3 | P0

**Description:** Create the Python package skeleton: `pyproject.toml` (name `station-watch`, Python >= 3.12, deps `opencv-python`, `numpy`, `pyyaml`; dev deps `pytest`, `ruff`), `src/station_watch/__init__.py`, ruff config, and a GitHub Actions workflow `.github/workflows/ci.yml` that runs `ruff check`, `ruff format --check` and `pytest` on push and pull request. Define the record formats every later component shares, in `src/station_watch/records.py`, as frozen dataclasses with `to_dict()` / `from_dict()` round trip to JSON:
- `FrameRecord`: station_id, camera_id, frame_id (monotonic per camera), capture_ts (wall clock, ISO 8601 UTC) and capture_mono (monotonic seconds), fingerprint (hash of raw bytes), mean_luma, noise_score.
- `BlindRecord`: station_id, camera_id, ts, reason (one of `disconnected`, `frozen`, `dark`, `fiducial_missing`, `view_shifted`), evidence (dict: e.g. seconds since last frame, identical-frame count, mean luma over window, fiducial offset px), last_good_frame_id or null.
- `Observation` (the Detect output format; Detect itself is HF2): station_id, frame_id, ts, kind (`part_present`, `part_absent`, `part_unknown`, `person_in_keepout`, `motion`, `no_motion`), target (slot or zone id), method, confidence_ceiling (0..1), detector_output (dict).
- `StationConfig` loaded from YAML: station_id, camera_id, takt_s, grace_s, required_slots, keepout_zones, liveness_window_s, dark_luma_threshold, dark_window_s, frozen_frames, fiducial (dictionary id, marker id, expected center px, tolerance px). Loading a config with a missing required key raises an error that names the key (K9).
Add fixture observation sequences under `tests/fixtures/observations/` (JSONL): `normal_cycles.jsonl`, `stall.jsonl`, `missing_part.jsonl`, `keepout_entry.jsonl`, each with a short README comment line describing the scenario and the expected Judge verdict. Add `config/station-example.yaml`.

**AC:**
- [ ] `pip install -e .[dev]` then `pytest` and `ruff check` pass locally; `.github/workflows/ci.yml` exists and runs ruff check, ruff format --check and pytest
- [ ] Every record type round-trips through `to_dict`/`from_dict` and JSON, proven by tests; unknown `reason` or `kind` values are rejected
- [ ] Loading a station config missing any required key raises an error whose message names that key (test per key)
- [ ] Four fixture observation files exist and each parses into `Observation` records in a test

**Depends on:** None
**Model:** claude-opus-4-8

### HF1.2 — Log: append-only, durable, deduplicated | Cx: 3 | P0

**Description:** Implement component 4 in `src/station_watch/log.py`: one local append-only store for every record type plus the pipeline's own `cycle_completed` row (ts, cycle number, stages completed). Use SQLite in WAL mode (stdlib `sqlite3`) with a unique idempotency key per row, so a replayed row is ignored, not duplicated. Reads are ordered by the time recorded inside the row, not insert order. API: `append(record)`, `newest(kind)`, `since(ts, kinds)`. Opening a log path whose directory does not exist raises an error naming the path (K9).

**AC:**
- [ ] Proving test: a child process is killed (SIGKILL) mid-write loop; after reopening, the newest row is correct and intact, and replaying the last batch adds no duplicates
- [ ] `newest("cycle_completed")` and `since(...)` return rows ordered by recorded time even when inserted out of order (test)
- [ ] Log is used by at least one later task through this API (checked at HF1.8 integration)

**Depends on:** HF1.1
**Model:** claude-opus-4-8

---

## Phase 2: Capture

### HF1.3 — Capture: stamped frames from a camera or a recorded file | Cx: 5 | P0

**Description:** Implement component 1 in `src/station_watch/capture/`: a frame source abstraction over `cv2.VideoCapture` that accepts a device index or a file path, and a `Capture` loop that turns each frame into a `FrameRecord` (frame id, wall and monotonic capture time, raw-bytes fingerprint, mean luma, and a noise score: mean absolute per-pixel difference from the previous frame). File sources are paced at their native fps so a recording replays in real time (a `speed` factor is allowed for tests). Add `tests/helpers/synth_video.py` that writes a synthetic clip at test time into a temp dir: a static scene plus per-frame Gaussian sensor noise, with options to freeze (repeat identical bytes from frame N), go dark from frame N, drop out (stop), and move a drawn ArUco marker (`cv2.aruco`, DICT_4X4_50) by K pixels from frame N.

**AC:**
- [ ] Proving test (K14): a synthetic clip generated by the helper is read through the real `cv2.VideoCapture` file path and every frame yields a `FrameRecord` with strictly increasing frame_id and capture_mono
- [ ] Noise score is above zero for live frames of a still scene and exactly zero for byte-identical frames (test)
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

## Phase 3: Judge, Alarm, Watchdog

### HF1.5 — Judge: healthy, fault, unobservable | Cx: 5 | P0

**Description:** Implement component 3 in `src/station_watch/judge.py`: the only place "age versus window" and "absence is a fault" live. Input: the station config, the latest Capture state (frame and blind records from the Log), and observations. Output a `Verdict` record (add it to `records.py`): station_id, ts, state (`healthy`, `fault`, `unobservable`), faults (list of `stalled`, `missing_part`, `keepout_entry` with target and cited frame ids), reason when unobservable. Rules: any active blind condition makes the station unobservable, never healthy and never a fault verdict based on stale observations; a unit with `no_motion` longer than `takt_s + grace_s` is `stalled`; a required slot `part_absent` at kit check is `missing_part`; `part_unknown` never counts as present or absent; `person_in_keepout` while the zone is active is `keepout_entry`. Every fault cites the frame ids it came from (K4). Write verdicts to the Log.

**AC:**
- [ ] Proving test: `stall.jsonl` yields `stalled` only after takt plus grace; the same timeline with a real `frozen` BlindRecord from Capture (HF1.4 synthetic clip) yields `unobservable`, never `stalled` and never `healthy`
- [ ] `normal_cycles.jsonl` yields only `healthy`; `missing_part.jsonl` yields `missing_part`; `keepout_entry.jsonl` yields `keepout_entry`; each fault cites frame ids present in the input
- [ ] A `part_unknown` for a required slot never produces `healthy` for that slot nor `missing_part` (test)

**Depends on:** HF1.1, HF1.2, HF1.4
**Model:** claude-opus-4-8

### HF1.6 — Alarm: episodes to screen and sound | Cx: 5 | P0

**Description:** Implement component 5 in `src/station_watch/alarm/`: opens and closes episodes from verdicts, one episode per root cause (one blind camera opens one episode naming every station it took dark, not one per station per tick). Fires on faults and on unobservable. Sinks are pluggable: a screen sink (terminal line with station, cause, start time and cited frames) and a sound sink (macOS `afplay` of a bundled short tone, Linux `aplay`/terminal bell fallback; generate the tone file at build time or ship a tiny WAV you create, no third-party audio). An episode recovers only after the configured number of healthy verdicts (K11), and recovery emits exactly one recovery message. Alarm never reads any explanation output (K5). At startup with no alarm sink configured, refuse to start and name the missing sink (K9). Alarm writes an `alarm_evaluated` row (ts, open episodes) to the Log after each evaluation, for the Watchdog.

**AC:**
- [ ] Proving test: a fake source that disconnects (the "pulled cable") through real Capture, Judge and Alarm produces exactly one alarm; reconnecting produces exactly one recovery, after the configured healthy count
- [ ] Fixture fault verdicts (stall, missing part, keep-out) each open one episode that cites frames; repeated identical verdicts do not open duplicates
- [ ] Startup without a sink fails with a message naming the sink (test)
- [ ] `alarm_evaluated` rows appear in the Log after each evaluation (test)

**Depends on:** HF1.5
**Model:** claude-opus-4-8

### HF1.7 — Watchdog: a second clock with its own rail | Cx: 5 | P0

**Description:** Implement component 8 in `src/station_watch/watchdog.py` as its own entry point (`python -m station_watch.watchdog --config ... --log ...`), meant to run as a separate process. It judges the Log's newest `cycle_completed` row and newest `alarm_evaluated` row against their windows using its own monotonic clock, and alarms through its own sink instance, never through the Alarm component's process (K7, K8). A missing or unreadable Log is itself an alarm, not a pass. This is the fix for finding D1 in the README: if Alarm stops while Capture keeps running, nothing else would notice.

**AC:**
- [ ] Proving test: run the pipeline and the watchdog as separate processes; stop only the Alarm stage while Capture keeps writing frames; the watchdog fires within its window, and the message names the stale stage
- [ ] Stopping everything (no new cycle rows) also fires; a healthy pipeline produces no watchdog alarm over a run longer than two windows
- [ ] Watchdog with a missing Log path alarms rather than exiting quietly (test)

**Depends on:** HF1.2, HF1.6
**Model:** claude-opus-4-8

---

## Phase 4: Wire it and prove it

### HF1.8 — Runner, end-to-end test, and "Run it" in the README | Cx: 5 | P0

**Description:** Add a CLI `station-watch` (console script) with `run --config <station.yaml> --source <device index or file> --log <path>` that wires Capture, Judge, Log and Alarm in one process and writes `cycle_completed` rows, and `watchdog ...` that starts the Watchdog as a separate process. Accept `--observations <jsonl>` to feed fixture observations until Detect exists (HF2), clearly labeled in help text as fixture input. Add an end-to-end test that runs the real CLI in a subprocess on a synthetic clip that goes dark partway, with the watchdog as a second subprocess, and asserts: one `dark` blind record, one alarm episode, one recovery after the clip returns to normal, no watchdog alarm; then kills the alarm stage and asserts the watchdog alarm. Add a "Run it" section to README (install, run on a webcam, run on a file, run the watchdog) and nothing else in README. Grep the package for modules with no importers and wire or remove them.

**AC:**
- [ ] End-to-end test drives the real `station-watch run` and `station-watch watchdog` subprocesses on a synthetic clip and passes (K14)
- [ ] `station-watch run` with a missing config key, missing source, or no alarm sink exits non-zero and names the missing piece (test)
- [ ] Every module under `src/station_watch/` is imported by the runner path or a public entry point (a test asserts this by import graph or by listing)
- [ ] README has a "Run it" section whose commands are exercised by tests or CI; README Status and principles sections unchanged
- [ ] CI green: ruff check, ruff format --check, pytest; `bpsai-pair arch check --strict` passes

**Depends on:** HF1.4, HF1.5, HF1.6, HF1.7
**Model:** claude-opus-4-8
