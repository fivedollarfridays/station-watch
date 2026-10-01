# HF3A — Station Watch, early HF3: fault drill, physics scripts and the Board, no bench needed

**Base:** main
**Summary:** fault injection with a time-to-alarm table, physics measurement scripts that report "no input present" until the bench exists, and a read-only Board that reads only the Log

**Runs after HF2 merges.** Base is `main` after the HF2 lane (backlog `plans/backlogs/HF2-station-watch-detect.md`, PR #3: Detect on DIN-rail positions anchored to the ArUco fiducial, Judge on Detect output, slope alarm, keep-out, evaluation harness, claims test) has landed. Do not start this lane on a `main` that lacks HF2: HF3A.1 reuses HF2.1's synthetic station renderer, and HF3A.2 to HF3A.5 consume HF2's `Detector`, the HF2.8 manifest and the HF2.8 measurement file format. Where the HF2 code on `main` differs from the names quoted below, the code on `main` wins; do not build a second version.

## Why this lane exists

Station Watch is the HackFW 2026 MADE track entry (demo Fri Oct 30). HF1 built Capture with blind records, Log, Judge, Alarm, the runner (`station-watch run`) and the Watchdog (`station-watch watchdog`). HF2 builds Detect, Judge on Detect output, keep-out, the evaluation harness and the claims test.

HF3 in the plan holds three pieces of code that need **no physical bench and no real footage** to build and test: the fault injection script with its time-to-alarm table, the physics measurement scripts, and the Board (component 7). The mock DIN-rail station is not built yet, so this lane pulls those three forward. Everything here is tested on frames generated at test time. The bench runs (fps sweep, exposure, flicker, occlusion by angle, the live fault drill) happen later on real hardware with the same scripts. Held-out evaluation on clip set v2 and HF3 review hardening stay in the later HF3 lane; they need footage.

This lane does **not** duplicate HF2. It does not build or change Detect, the Judge rules, the slope alarm, keep-out detection, the evaluation harness, the manifest format or the claims test. Where it needs them it consumes them as merged on `main` (see each task's **Interface:** block). In particular, HF2.8 already reports time to alarm for faults **labeled in recorded clips**; HF3A.2 is different: it **injects** faults into a running pipeline (synthetic now, physical on the bench later) and times the alarm and the recovery live.

**Design rules that bind every task** (README principles table):
- K1: three outcomes per station: healthy, fault, unobservable. Unobservable never folds into healthy. On the Board, missing or stale data is shown as UNKNOWN or STALE, never as OK.
- K2: a camera is live only when a new frame with a new capture timestamp and different content arrived within the window.
- K5: the alarm answers the fault; nothing optional can delay or suppress it. The fault drill only observes the alarm; it never drives it.
- K13: every number lives in a committed measurement file. A measurement script with no input writes `"status": "no_input"` and no numbers. It never fills in synthetic or estimated numbers as if they were measured.
- K14: the fault drill drives the real runner pipeline (the same code path `station-watch run` uses) and reads results back from the real Log and alarm record sink.
- The Board is read-only. It never writes the Log, never sends an alarm, never controls anything.

**Stack.** Python 3.12, `src/station_watch/` package, `opencv-python` and `numpy` (already deps), stdlib for everything else (the Board's web page uses `http.server`, no web framework). pytest and ruff. No detector model is added in this lane. No network calls beyond the Board binding to `127.0.0.1`. Everything runs on macOS and Linux. Any new dependency must have a license compatible with a public MIT repo (state it in the PR body); the expectation is none.

**Repo constraints.**
- This is a PUBLIC repo. Never commit footage, images, binary video, model weights, private paths, hostnames or secrets. Test frames are generated at test time into a temp dir. `.paircoder/context/`, `.paircoder/tasks/`, `.paircoder/history/` and `.env` stay ignored; do not change `.gitignore`.
- Bench clips live under `data/local/` (already gitignored). Scripts read them from a path given on the command line; no default path points outside the repo.
- Keep every source file under 400 lines; split before that. `bpsai-pair arch check --strict src` and `bpsai-pair arch check --strict tests` must both pass.
- Do not edit README's "Status" section or the principles table. README "Run it" additions are HF3A.7's job only.
- Do not register anything persistent (cron, launchd, services) from a task worktree.
- Every task is test first: write the failing proving test, then the code. Every module gets its own proving test.

**Models.** Every task is overridden to `claude-opus-4-8`: these are integration and correctness tasks where a module that is written but never wired does not count.

**After engage.** The orchestrator, not a driver, runs the real path before the PR is called done (engage reporting tasks "done" is not evidence): `station-watch drill` on the example config with synthetic faults, each physics script with no input (expect `no_input` files), and `station-watch board` against the Log the drill produced, opened in a browser.

---

## Phase 1: Synthetic frames and faults in the package

### HF3A.1 — Synthetic frame source and a fault-injecting source wrapper | Cx: 3 | P0

**Description:** The drill and the physics scripts run from `src/`, so they need a synthetic frame renderer importable from the package. HF2.1 adds `write_synth_station_clip` (backplate, DIN rail, components, taped zones, ArUco marker, hand blob, sensor noise) and HF2.8's `evaluate --synthetic` uses it at run time. Reuse that renderer from wherever it lives on `main`; only if it is still reachable solely under `tests/helpers/`, move the rendering into `src/station_watch/synth/` and leave `tests/helpers/synth_video.py` and `tests/helpers/synth_station.py` as thin re-exports so every HF1 and HF2 test passes unchanged. Add `SyntheticSource`, a live-paced source with the same `read()` / `reopen()` / `release()` / `fps` surface as `capture.source.FrameSource`, producing fresh noisy frames at a set fps. Add `src/station_watch/faults.py` with `FaultSource`, which wraps any source (synthetic or a real `FrameSource`) and applies a schedule of faults, each with a start and clear time on the wall clock. The five faults, as the camera would see them: `lens_covered` (near-black frames that keep sensor noise), `frozen` (the last good frame's bytes repeated exactly), `bumped` (the image translated so the fiducial lands outside `fiducial.tolerance_px`), `cable_pulled` (`read()` returns nothing and `reopen()` fails until clear), `lights_off` (global luma drop below `dark_luma_threshold` with sensor noise kept, distinct from lens covered by keeping scene structure). A fault schedule loads from YAML (`fault`, `start_s`, `clear_s`) and rejects unknown fault names and overlapping windows with an error naming the row.

**AC:**
- [ ] Every existing HF1 and HF2 test passes unchanged; no second synthetic renderer is written (the drill frames come from the HF2.1 renderer)
- [ ] Proving test: each of the five faults, applied by `FaultSource` to a `SyntheticSource`, produces the frame property its name promises (zero frames for `cable_pulled`, identical fingerprints for `frozen`, mean luma under threshold with nonzero noise for `lens_covered` and `lights_off`, fiducial offset beyond tolerance for `bumped`) and normal frames after `clear_s`
- [ ] `FaultSource` wraps a `FrameSource` opened on a generated file as well as a `SyntheticSource` (test)
- [ ] A schedule with an unknown fault name or overlapping windows fails to load with a message naming the row (test)

**Depends on:** None
**Model:** claude-opus-4-8

## Phase 2: Fault drill and time-to-alarm

### HF3A.2 — `station-watch drill`: fault injection and the time-to-alarm table | Cx: 5 | P0

**Description:** Add a `drill` subcommand: `station-watch drill --config <station.yaml> --log <path> --out <measurement file> (--schedule <faults.yaml> | --live --source <device index>)`. In synthetic mode it builds a `SyntheticSource` wrapped in `FaultSource` with the schedule and runs the **same runner pipeline object** `station-watch run` uses (Capture, Log, Judge, Alarm, cycle rows), with the `record` alarm sink. It is labeled in help text as a drill. In live mode (`--live`) the source is a real camera and the faults are physical; the operator types `start <fault>` and `clear <fault>` lines on stdin and each line is stamped with wall-clock time as the injection or clear time. After the run, it reads back from the Log and the alarm record file, per fault: the blind reason that opened, time from injection to the first alarm (alarm record ts minus injection ts), time from clear to the recovery message, and whether exactly one alarm and exactly one recovery occurred. It writes one measurement file per run in the HF2.8 format (provenance plus metrics; `dataset_kind` is `synthetic` for a schedule run and `real` for a live run; `manifest_sha256` is the schedule file's SHA-256, or of the stamped stdin marks in live mode) with the config thresholds in force under metrics, and a markdown table beside it. Synthetic runs write under `measurements/synthetic/`; live bench runs go under `measurements/v1/` later. A fault that never alarms is reported as `alarm: none` with a null time, never dropped or given a default. Commit the synthetic run's measurement file and table under the measurement directory, clearly labeled synthetic; the live table is produced later on the bench.

**Interface:**
- Consumes HF1 records from the Log via `Log.since(ts, kinds)` / `Log.newest(kind, **match)`: `BlindRecord` (station_id, camera_id, ts, reason in `disconnected|frozen|dark|fiducial_missing|view_shifted`, state `opened|cleared`, seq), `AlarmEvaluated` (ts, seq, open_episodes), `CycleCompleted`. Kinds are `blind`, `alarm_eval`, `cycle`.
- Consumes the HF1 `record` alarm sink file: one JSON line per alarm and per recovery, carrying station, cause (`unobservable:<reason>` or `<fault kind>:<target>`), start ts and cited frames. Episode cause strings come from `alarm/episodes.py`.
- Consumes the HF2.8 measurement file format: JSON `{"provenance": {"dataset", "dataset_kind": "real"|"synthetic", "manifest_sha256", "config_sha256", "git_commit", "detector", "clips", "sessions", "date_utc"}, "metrics": {...}}`, metric keys addressable as dotted paths under `metrics` (e.g. `faults.cable_pulled.time_to_alarm_s`), so the HF2.9 claims test can cite them. Reuse HF2.8's writer; do not define a second format.
- Expected reason per fault, which the table checks and reports when it differs: `lens_covered` and `lights_off` to `dark`, `frozen` to `frozen`, `bumped` to `view_shifted` or `fiducial_missing`, `cable_pulled` to `disconnected`.

**AC:**
- [ ] Proving test (K14): `station-watch drill` as a subprocess with a schedule of all five faults and short test windows writes a measurement file where every fault has exactly one alarm, exactly one recovery, the expected blind reason, and a non-null time to alarm and time to recovery, all read back from the Log and the alarm record file
- [ ] A test asserts the drill runs the runner's own pipeline class (the same object `station-watch run` constructs), not a copy
- [ ] A fault the pipeline cannot see (a schedule window shorter than the dark window) is reported with `alarm: none` and a null time, not omitted (test)
- [ ] Live mode reads `start <fault>` / `clear <fault>` lines from stdin and stamps them on the wall clock; tested by piping lines to a run on a generated file source
- [ ] The committed `measurements/synthetic/` drill file and markdown table exist, say synthetic in `dataset_kind` and in the table heading, and are reproduced by the command written in the table's header
- [ ] **E2E Conformance:** a real `station-watch drill` subprocess writes a Log, and a real `station-watch board --once` subprocess on that Log shows the drill's open and recovered episodes (producer and consumer both on the real CLI path)
- [ ] Startup without a schedule in synthetic mode, or without a source in live mode, exits non-zero naming the missing piece (test)

**Depends on:** HF3A.1, HF3A.6
**Model:** claude-opus-4-8

## Phase 3: Physics measurement scripts (run on the bench later, honest now)

### HF3A.3 — Measurement scaffold and the fps sweep | Cx: 3 | P0

**Description:** Add `src/station_watch/physics/` with a shared runner used by every physics script, exposed as `station-watch measure <name> --clips <manifest> --out <file>`. The clip list is the HF2.8 manifest; add optional per-clip `tags` (for example `fps`, `exposure_s`, `speed_m_s`, `lamp`, `angle_deg`) in a backward-compatible way, so HF2's loader and tests are unchanged. Shared behavior: if `--clips` is missing, the manifest is absent, or it lists no clips tagged for this measurement, the script prints `no input present: <reason>`, writes `measurements/physics/<name>.json` as `{"status": "no_input", "reason": ..., "command": ..., "provenance": {"git_commit", "date_utc"}}` with **no `metrics` key at all** (so the claims test can never cite it), and exits zero; it never substitutes synthetic or estimated numbers. (This differs on purpose from `station-watch evaluate`, which writes nothing: a physics table that does not exist yet should say so in the repo.) A `--synthetic` flag exists for tests only (help text says so), writes only into the given `--out`, and stamps `dataset_kind: synthetic`. First script, `fps_sweep`: for each labeled keep-out reach event in the clips, subsample the clip to 30, 15 and 5 fps by dropping frames, run HF2's Detect on the kept frames, and report per fps: frames that fell inside each event, events detected, detection rate, and the predicted minimum frames `f * d` beside the measured count (PLAN 3.4, "frame rate vs event duration"). Commit the `no_input` file for the sweep.

**Interface:**
- Consumes the HF2.8 manifest (YAML: clips by session, each with a clip path relative to a clips directory under `data/local/clips/`, and labeled intervals per target including keep-out entries), extended only by optional `tags`.
- Consumes HF2.3's `Detector(config, run_id)` with `Detector.process(frame, frame_id, ts) -> list[Observation]`, and HF2.6's `KeepoutTracker(config, run_id, backend)` with the injectable backend `detect_people(frame) -> list[tuple[x0, y0, x1, y1, score]]` (tests use a scripted fake backend; no weights needed). Observations are HF1 `Observation` records; keep-out kinds are `person_in_keepout`, `zone_clear`, `zone_unknown`.
- Real results write the HF2.8 measurement format under `measurements/v1/`; `no_input` files go under `measurements/physics/`.

**AC:**
- [ ] Proving test: with no `--clips`, `station-watch measure fps_sweep` writes a file whose status is `no_input`, which names the reason, and which contains no numeric result fields
- [ ] With `--synthetic` on generated clips of a keep-out reach of known duration and a scripted fake backend, the sweep's measured frames-in-event at 30, 15 and 5 fps match `f * d` within one frame, and every output is stamped `dataset_kind: synthetic` (test)
- [ ] The committed fps sweep file has status `no_input`; a test asserts every committed file under `measurements/physics/` is `no_input` with no `metrics` key, and that no physics result anywhere is committed with `dataset_kind: synthetic` outside `measurements/synthetic/`
- [ ] HF2's manifest loader tests pass unchanged with the optional `tags` added

**Depends on:** HF3A.1
**Model:** claude-opus-4-8

### HF3A.4 — Exposure and motion blur, and lighting flicker | Cx: 3 | P1

**Description:** Two scripts on the HF3A.3 scaffold. `exposure_blur`: clips tagged with exposure time and a known hand or target speed; measure blur length in pixels from the edge spread across the fiducial or a high-contrast target, convert to mm with the fiducial's known size, and report it beside the predicted `speed * exposure` (PLAN 3.4, "motion blur"), plus the share of slot judgments HF2's Detect returns as unknown at each exposure. `flicker`: clips tagged with lamp type and camera exposure; compute the per-frame mean luma series, its peak-to-peak swing and dominant frequency after aliasing at the clip's fps, and whether Capture's dark monitor opened a `dark` blind record during the clip (it must not on flicker alone; PLAN 3.4, "lighting and flicker"). Both write `no_input` honestly without clips. Commit both `no_input` files.

**Interface:**
- Consumes HF3A.3's scaffold (`station-watch measure <name>`, the `no_input` contract, the `--synthetic` stamp, manifest `tags`) and HF2's `Detector`, HF2.2 `PositionTracker` slot observations (`part_present` / `part_absent` / `part_unknown`, cause in `detector_output.cause`) and the HF2.8 measurement format as listed in HF3A.3.
- Consumes HF1 Capture's dark monitor through the real `Capture` and a temp `Log`, reading `BlindRecord` rows with reason `dark`; it does not reimplement the threshold.

**AC:**
- [ ] Proving test: synthetic frames from the HF2.1 renderer blurred with a known kernel length give a measured blur length within 15 percent of that length
- [ ] Proving test: synthetic frames with 120 Hz luma modulation integrated over a non-multiple of 1/120 s exposure, sampled at 30 fps, report the expected aliased frequency, and the real Capture opens no `dark` blind record on them
- [ ] Without clips both scripts write `no_input` files with no numeric results; the committed files are `no_input` (test)

**Depends on:** HF3A.3
**Model:** claude-opus-4-8

### HF3A.5 — Occlusion by camera angle | Cx: 2 | P1

**Description:** Script `occlusion` on the HF3A.3 scaffold. Clips are tagged with camera angle (degrees from overhead). For each angle, run HF2's Detect with its N-frame persistence rule over the clip and report the fraction of slot judgments that are unknown (and how many of those are caused by occlusion versus other causes), the longest unknown run in seconds, and the count of slot judgments made (PLAN 3.4, "occlusion"). Writes `no_input` honestly without clips. Commit the `no_input` file.

**Interface:**
- Consumes HF3A.3's scaffold and HF2.2's `PositionTracker` through `Detector.process`: slot observations `part_present` / `part_absent` / `part_unknown` with target = rail position id and `detector_output.cause` (`occluded`, `fiducial_missing`, dark, blur, `out_of_frame`), plus the HF2.8 measurement format.

**AC:**
- [ ] Proving test: synthetic clips from the HF2.1 renderer with a hand blob occluding a rail position for a known share of frames, at two tagged angles, report occlusion-caused unknown fractions matching the rendered shares within 5 percentage points, stamped `dataset_kind: synthetic`
- [ ] Without clips the script writes a `no_input` file with no numeric results; the committed file is `no_input` (test)

**Depends on:** HF3A.3
**Model:** claude-opus-4-8

## Phase 4: Board

### HF3A.6 — Board: a read-only operator screen from the Log | Cx: 5 | P0

**Description:** Implement component 7 as `station-watch board --config <station.yaml> --log <path> [--port 8765] [--once]`. It opens the Log **read-only** (a new `Log.open_readonly(path)` or `LogReader` using an SQLite `mode=ro` URI; the existing `Log` constructor creates schema and so must not be used here) and builds one view model per station: station state from the newest `Verdict` with how long that state has held; newest frame age (now minus the newest `FrameRecord` ts) marked STALE beyond `liveness_window_s`; blind reasons from open `BlindRecord`s, counted separately from faults; active flags from the newest verdict's faults, each with its cited frame ids; alarm state from the newest `AlarmEvaluated` (open episode ids and the row's age, marked STALE beyond `watchdog.alarm_eval_window_s`). Every fault and observation kind on `main` renders, including HF2's additions (for example `zone_unknown`, `zone_clear` and the slope alarm's fault kind); a kind the Board does not know renders by its name, never crashes and never shows as OK. Missing or unreadable Log, no verdict yet, or a stale verdict shows UNKNOWN with the reason, never OK (K1). `--once` prints the view as plain text and exits; otherwise a stdlib `http.server` bound to `127.0.0.1` serves one auto-refreshing HTML page and a JSON view; every non-GET method returns 405. The Board imports nothing from Alarm sinks or the runner and has no code path that writes.

**Interface:**
- Consumes HF1 records from the Log, with the kinds HF2 added: `Verdict` (station_id, ts, state `healthy|fault|unobservable`, faults list of {kind `stalled|missing_part|keepout_entry` plus HF2.5's slope fault, target, frame_ids}, blind_reasons, seq), `Observation` (for `zone_unknown` / `part_unknown` causes), `FrameRecord` (station_id, camera_id, frame_id, ts), `BlindRecord` (reason, state `opened|cleared`, ts), `AlarmEvaluated` (ts, open_episodes as episode cause strings `unobservable:<reason>` or `<fault kind>:<target>`), `CycleCompleted` (ts, cycle). Kinds are `verdict`, `frame`, `blind`, `alarm_eval`, `cycle`.
- Consumes the HF1 station config (`liveness_window_s`, `watchdog.alarm_eval_window_s`, station_id, camera_id).
- Read-only: no `append`, no schema creation, no sink.

**AC:**
- [ ] Proving test: a fixture Log with a healthy run, then a `dark` blind record, then a stall fault renders (in `--once` text and in the HTML page) the right state, frame age, blind reason, the stall flag with its cited frame ids, and the open episodes
- [ ] Proving test: after rendering, the Log file's bytes and row count are unchanged, and an attempted write through the Board's reader raises (SQLite read-only)
- [ ] A missing Log, an empty Log, a stale verdict and a stale `alarm_evaluated` row each render UNKNOWN or STALE with the reason, never OK (test per case)
- [ ] The HTTP server answers GET for the page and JSON view, returns 405 for POST, PUT and DELETE, and binds only to 127.0.0.1 (test)
- [ ] The Board's modules import nothing from `station_watch.alarm` or `station_watch.runner` (test)
- [ ] **E2E Conformance:** a real `station-watch run` subprocess on a synthetic clip that goes dark partway writes a Log, and a real `station-watch board --once` subprocess on that Log shows the `dark` blind reason, the open episode, and a frame age (both on the real CLI path, not fixtures)

**Depends on:** None
**Model:** claude-opus-4-8

## Phase 5: Wire it

### HF3A.7 — Wiring, README "Run it", CI | Cx: 2 | P0

**Description:** Update the HF1 orphan-module test so every module under `src/station_watch/` must be imported by the run, watchdog, drill, measure or board path, and fix any orphan. Add to README's "Run it" section the commands for `station-watch drill` (synthetic and live), `station-watch measure <name>` (and what `no_input` means), and `station-watch board`; change nothing else in README. HF2.9's claims test holds README numbers to measurement files; add no numbers to README in this lane (the drill table is synthetic and the physics files are `no_input`).

**AC:**
- [ ] The orphan-module test covers the drill, measure and board entry points and passes
- [ ] README "Run it" commands for drill, measure and board are exercised by tests; README Status, principles and "Measured performance" sections unchanged; HF2.9's claims test passes
- [ ] No footage, images, binary video or weights are tracked (`git ls-files` check in a test or CI step); `.gitignore` unchanged
- [ ] CI green: ruff check, ruff format --check, pytest; `bpsai-pair arch check --strict src` and `bpsai-pair arch check --strict tests` both pass

**Depends on:** HF3A.2, HF3A.4, HF3A.5, HF3A.6
**Model:** claude-opus-4-8

---

## Delivery Summary

| ID | Title | Cx | Priority | Model | Depends on |
|---|---|---|---|---|---|
| HF3A.1 | Synthetic frame source and a fault-injecting source wrapper | 3 | P0 | claude-opus-4-8 | None |
| HF3A.2 | `station-watch drill`: fault injection and the time-to-alarm table | 5 | P0 | claude-opus-4-8 | HF3A.1, HF3A.6 |
| HF3A.3 | Measurement scaffold and the fps sweep | 3 | P0 | claude-opus-4-8 | HF3A.1 |
| HF3A.4 | Exposure and motion blur, and lighting flicker | 3 | P1 | claude-opus-4-8 | HF3A.3 |
| HF3A.5 | Occlusion by camera angle | 2 | P1 | claude-opus-4-8 | HF3A.3 |
| HF3A.6 | Board: a read-only operator screen from the Log | 5 | P0 | claude-opus-4-8 | None |
| HF3A.7 | Wiring, README "Run it", CI | 2 | P0 | claude-opus-4-8 | HF3A.2, HF3A.4, HF3A.5, HF3A.6 |

Total Cx: 23.

## Priority Order

1. HF3A.1 (unblocks the drill and the physics scripts)
2. HF3A.6 (independent; can run beside HF3A.1; HF3A.2's E2E check reads its output)
3. HF3A.2 (the fault drill; "all five faults alarm and recover" is the HF3 milestone)
4. HF3A.3
5. HF3A.4
6. HF3A.5
7. HF3A.7
