# HF3 — Station Watch output QA: audit, held-out evaluation, soak and demo readiness

**Base:** main
**Summary:** every flag gets a proof sheet a reviewer marks right or wrong, reviewed precision and a false-alarm rate per hour come from those marks, held-out evaluation refuses to score clips the thresholds were set on, a mostly blind session fails its QA, a soak harness catches growth over hours, a preflight command and a run-of-show make demo day repeatable, and per-slot label, ferrule and torque-stripe checks join Detect

**Runs after HF3A merges.** Base is `main` at or after `c05ff75` (HF1, HF2 and HF3A merged: Capture, Log, Judge, Alarm, runner, Watchdog, Detect, evaluation harness, claims test, fault drill, physics scripts, Board). Where the code on `main` differs from the names quoted below, the code on `main` wins; do not build a second version of anything that exists.

## Why this lane exists

Station Watch is the HackFW 2026 MADE track entry (demo Fri Oct 30). HF1 to HF3A built the pipeline and proved it on synthetic frames. What is missing is the output side: a way to show that each flag the system raised was right, numbers that were not tuned on the clips they are reported on, proof the system survives hours of running, and a repeatable demo-day start. PLAN section 6 also puts the per-slot checks (label present at a termination, ferrule present, torque stripe present) in HF3.

The physical bench (DIN rail panel) is not built yet. Kevin will record clip set v1 (calibration) and, on a different day, a held-out clip set v2. So every task here is built and tested now on frames generated at test time, and every harness that needs real data reports "no input", "no labeled set" or "no reviewed flags" honestly, writing no numbers. Each task lists, under **Closes on Kevin's clips**, what only real footage or the real bench can prove; those lines are not engage ACs and do not block task completion. They are tracked to closure after the clips land.

This lane also closes the P2 findings left open in the PR #5 and PR #6 review dispositions that matter before demo day (HF3.1, HF3.2). One of them is already closed on `main`: the synthetic renderer now lives in `src/station_watch/synth/` (HF3A.1), so `evaluate --synthetic` no longer depends on `tests/helpers/`.

**Design rules that bind every task** (README principles table):
- K1: three outcomes per station: healthy, fault, unobservable. Unobservable never folds into healthy. Missing, stale or undecodable data is UNKNOWN or STALE, never OK, on every surface (Board, audit sheet, QA, preflight).
- K3: a call from a blurred, dim or occluded frame carries a lower confidence ceiling.
- K4: every flag cites frame ids; the audit sheet shows those exact frames and never substitutes another frame.
- K5: the alarm answers the fault. Evidence capture, audit, QA, soak sampling and preflight only observe; none of them can delay, suppress or drive the alarm. A failure in any of them never raises into Capture, Judge or Alarm.
- K9: fail loud at startup, naming the missing piece (the dotted config key, the file, the device index).
- K10: probes never raise; "could not check" is a FAIL or UNKNOWN with the reason, never a PASS.
- K13: every number lives in a committed measurement file in the one format `{"provenance": {...}, "metrics": {...}}` written by `station_watch.evaluate.provenance.write_measurement`. A harness with no input writes a status with **no `metrics` key** (as `measure` does for `no_input`), never synthetic or estimated numbers as if measured. Synthetic results go under `measurements/synthetic/` stamped `dataset_kind: synthetic`; real results under `measurements/v1/` (calibration) or `measurements/v2/` (held out).
- K14: the proving tests drive the real CLI path (`station-watch run`, `audit`, `qa`, `soak`, `preflight`, `board`) as subprocesses at least once per command.
- Read-only means read-only: the audit and QA commands open the Log through the Board's `LogReader` (SQLite `mode=ro`), never through `Log`, whose constructor creates schema.

**Stack.** Python 3.12, `src/station_watch/` package, `opencv-python`, `numpy`, `pyyaml` (already deps), stdlib for everything else (HTTP via `http.server`, RSS via `ps`, no web framework, no `psutil`). pytest and ruff. No new runtime dependency is expected; any new dependency must have a license compatible with a public MIT repo and be named in the PR body. Playwright is **not** a dev dependency of this repo, so browser QA is a headless HTTP plus HTML assertion test (HF3.14). No network calls beyond loopback (`127.0.0.1`).

**Repo constraints.**
- This is a PUBLIC repo. Never commit footage, images, binary video, model weights, evidence frames, audit sheets, reviewer verdicts, private paths, hostnames or secrets. Test frames, evidence and audit output are generated at test time into a temp dir. Real evidence, audit sheets and verdicts live under `data/local/` (already gitignored). `.paircoder/context/`, `.paircoder/tasks/`, `.paircoder/history/` and `.env` stay ignored; **never touch `.gitignore`**.
- Keep every source file under 400 lines; split before that. `bpsai-pair arch check --strict src` and `bpsai-pair arch check --strict tests` must both pass.
- Do not edit README's "Status" section or the principles table. README changes are HF3.18's job only (plus HF3.9's claims-test rule, which changes no README text).
- Do not register anything persistent (cron, launchd, services) from a task worktree.
- **TDD for every task:** write the failing proving test first, then the code. Every new module gets its own proving test, and the HF1 orphan-module test (`tests/test_watchdog.py`) must keep passing: every module under `src/station_watch/` is reached from a CLI entry point.

**Models.** Every task is overridden to `claude-opus-4-8`, as in HF1, HF2 and HF3A: these are integration and correctness tasks, and haiku-tier drivers have shipped modules that were written but never wired into the real path. A module that is not wired does not count.

**After engage.** The orchestrator, not a driver, runs the real path before the PR is called done (engage reporting tasks "done" is not evidence): `station-watch run` on a synthetic clip with evidence on, `station-watch audit build` and `audit review` on that Log opened in a browser and marked, `audit score` and `audit rate` (expect honest statuses), `station-watch qa` pass and fail cases, a short `station-watch soak --synthetic-loop`, `station-watch preflight` on the Mac mini's webcam, `station-watch board` against the soak Log, and `station-watch evaluate --split held_out` refusing a leaked manifest.

---

## Phase 1: Carry-over fixes the rest of the lane builds on

### HF3.1 — Pipeline carry-overs: ArUco once per frame, stall window default, measure errors, motion mask, per-clip backends | Cx: 4 | P1

**Description:** Close the pipeline P2s from the PR #5 and #6 dispositions, each with a regression test written first. (1) **ArUco detected once per frame:** Capture finds the marker corners once per frame with `find_marker_corners` and derives the fiducial center from them (a `marker_center(corners)` helper in `detect/geometry.py`), then passes the corners into the detect hook; `Detector.process(frame, frame_id, ts, corners=None)` uses given corners and only finds them itself when called without (the physics scripts still call it that way). `capture/fiducial.find_marker_center` is kept only if something else still needs it; otherwise it goes and the orphan test stays green. (2) `RunContext.stall_window_s` defaults to `None`, so the Judge's `takt_s + grace_s` fallback engages when a context is built without it. (3) `station-watch measure` catches `StartupError` and the `ValueError` from `physics/occlusion._native_fps` (turned into a `ManifestError`-family error naming the clip) and prints `station-watch: <message>` with a non-zero exit, like `drill` does. (4) The drill and measure "Reproduce:" command strings are built with `shlex.join`. (5) `detect/motion.py` frame differencing applies the current frame's mask only to a previous gray computed under the same mask (store the previous zone crop with the corners it was cut with, and forget it when the corners move beyond a small tolerance), so marker jitter cannot read as motion. (6) A real `evaluate` builds one keep-out backend per clip, as the synthetic path already does.

**AC:**
- [ ] A test counts `cv2.aruco` marker detection calls (or calls to `find_marker_corners`) during one frame through the real Capture with Detect on, and asserts exactly one
- [ ] `Detector.process` given corners does not search for the marker; called without corners it still finds them (tests); every existing Detect, Capture and physics test passes unchanged
- [ ] A `RunContext` built without `stall_window_s` gives a Judge whose stall window is `takt_s + grace_s` (test)
- [ ] `station-watch measure` with a config that raises `StartupError`, and with an `angle_deg` clip that reports no fps, each exit non-zero with one `station-watch:` line naming the cause and no traceback (subprocess tests)
- [ ] Reproduce commands for drill and measure round-trip through `shlex.split` to the original argv when a path contains a space or `$` (test)
- [ ] A synthetic still scene whose marker jitters by 2 px between frames reads `no_motion`, and a real moving blob still reads `motion` (test through `MotionTracker`)
- [ ] A real-path evaluate on two generated clips constructs two distinct backend instances (test with a counting factory)

**Closes on Kevin's clips (not an engage AC):** per-frame latency on the real camera with Detect on, before and after the single ArUco pass.

**Depends on:** None
**Model:** claude-opus-4-8

### HF3.2 — Board hardening carry-overs: UNKNOWN on decode failure, headers, threading, indexed blind reasons | Cx: 3 | P0

**Description:** Close the Board P2s from the PR #6 dispositions. (1) `build_view` renders UNKNOWN with the reason when a Log row cannot be decoded (an unknown record kind gives `KeyError` in `_rebuild`, schema drift gives `ValueError` or `TypeError` in `from_dict`, bad JSON gives `JSONDecodeError`): the reader wraps these as `BoardLogError` naming the record id. (2) Every response carries `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`. A request with an `Origin` header that is not the Board's own loopback origin gets 403, so cross-origin reads are refused by the code, not only by the absence of CORS headers. (3) The server is a `ThreadingHTTPServer` with daemon threads and a per-request socket timeout, so one stalled connection cannot starve the operator's page. (4) Rejected requests (403 and 405) are logged as one line on stderr (method, path, reason, no headers); successful GETs stay quiet. (5) The open-blind-reason lookup is bounded: the Log writer adds an expression index `records_blind_reason ON records (kind, json_extract(body, '$.reason'), ts)` with `CREATE INDEX IF NOT EXISTS`, and `LogReader.newest_matching` uses a query whose expression matches it exactly. A Log written before the index exists still renders correctly (the read-only Board cannot add the index; it falls back to the current query and says so once on stderr).

**Interface:**
- The `LogReader` API stays as on `main`: `LogReader(path)`, `newest(kind)`, `newest_matching(kind, field, value)`, `iter_newest(kind, *, limit)`, context manager, raising only `BoardLogError`. HF3.5, HF3.3 and HF3.14 consume it and rely on "raises only `BoardLogError`".
- `make_server(config, log_path, *, port, clock)` keeps its signature and still binds only to `127.0.0.1`.

**AC:**
- [ ] A Log holding a row of an unknown record kind, and a row whose body fails `from_dict`, each render UNKNOWN naming the record id in `--once` text and in the HTML page; neither raises (tests)
- [ ] Every response (200, 403, 404, 405) carries `X-Content-Type-Options: nosniff` and `Cache-Control: no-store` (test)
- [ ] A GET with `Origin: http://evil.example` gets 403; a GET with no Origin or the Board's own origin gets 200 (test)
- [ ] A client that opens a socket and sends nothing does not block a second client's GET from returning within 2 s (test)
- [ ] 403 and 405 responses write one stderr line each; a 200 GET writes none (test)
- [ ] `EXPLAIN QUERY PLAN` for the blind-reason lookup on a Log written by the current `Log` names `records_blind_reason`, and a test with 20,000 blind rows of another reason shows the lookup for an absent reason visits a bounded number of rows (count with a `sqlite3` progress handler or `EXPLAIN` step count, not rows returned)
- [ ] A Log created without the new index renders the same view (test)
- [ ] **Counterparty:** `board/view.py::build_view` and `station-watch board` read through the hardened `LogReader` (named artifact; HF3.3, HF3.5 and HF3.10 also consume it)
- [ ] **E2E Conformance:** a real `station-watch run` subprocess on a synthetic clip that goes dark writes a Log carrying `records_blind_reason`, and a real `station-watch board --once` subprocess on it shows the `dark` blind reason; the same `board --once` on a copy of that Log with one undecodable row appended prints UNKNOWN naming the record

**Depends on:** None
**Model:** claude-opus-4-8

## Phase 2: Session QA and evidence

### HF3.3 — Session QA: a per-session unknown budget a mostly blind session cannot pass | Cx: 3 | P0

**Description:** Add `src/station_watch/qa.py` and `station-watch qa --config <station.yaml> --log <path> [--out <file>]`. Over one session's Log (read-only through `LogReader`), compute time-weighted fractions: each `Verdict` holds from its `ts` until the next verdict's `ts`; the last one holds until the newest `cycle` row's `ts`. A gap between verdicts longer than `watchdog.cycle_window_s` counts as unobservable for its whole length (missing data is not healthy, K1). Report `observable_s` (healthy plus fault time), `session_s`, `unobservable_fraction`, and per required slot and keep-out zone the fraction of observed time whose newest observation was `part_unknown` or `zone_unknown`. The session passes only when `unobservable_fraction <= qa.max_unknown_fraction` and every per-target unknown fraction is `<= qa.max_target_unknown_fraction` (when set). A Log with no verdicts fails as "no verdicts". Thresholds come from a new optional config section `qa:`; `station-watch qa` with no `qa.max_unknown_fraction` refuses to start naming that dotted key (K9); `station-watch run` is unaffected by the section. Exit 0 on pass, 1 on fail, 2 on startup error. `--out` writes the result as a measurement file (provenance plus `metrics` with the fractions, `status: pass|fail` in metrics); without `--out` it prints only. Add a `qa:` block, commented, to `config/station-example.yaml` and a filled one to `measurements/synthetic/config.yaml` only if needed by tests (prefer test-local configs).

**Interface:**
- Exposes, consumed by HF3.6, HF3.7 and HF3.11: `session_qa(config, log_path) -> SessionQA`, a frozen dataclass with `status` (`"pass"` or `"fail"`), `reason` (str), `session_s`, `observable_s`, `unobservable_fraction` (floats), `target_unknown_fraction` (dict of target id to float), `verdicts` (int), `max_unknown_fraction` (float). Never raises for a missing or undecodable Log: that is `status="fail"` with the reason.
- Consumes `LogReader` from HF3.2 (`iter_newest("verdict", limit=...)`, `newest("cycle")`) and HF1 records `Verdict` (state `healthy|fault|unobservable`, ts) and `Observation` (kinds `part_unknown`, `zone_unknown`).

**AC:**
- [ ] Proving test: a fixture Log that is 80 percent unobservable fails a 0.2 budget and names the fraction; the same Log passes a 0.9 budget (subprocess, exit codes 1 and 0)
- [ ] A Log with a verdict gap longer than `watchdog.cycle_window_s` counts the gap as unobservable (test with hand-computed expected fraction)
- [ ] A Log with no verdicts, a missing Log, and an undecodable row each fail with the reason, never pass (tests)
- [ ] A slot whose newest observation is `part_unknown` for most of the session fails `qa.max_target_unknown_fraction` and names the slot (test)
- [ ] `station-watch qa` without `qa.max_unknown_fraction` exits 2 naming the key; `station-watch run` with no `qa:` section starts as before (tests)
- [ ] The Log file's bytes are unchanged after `qa` runs (test)
- [ ] **E2E Conformance:** a real `station-watch run` subprocess on a synthetic clip that is dark for most of its length, then a real `station-watch qa` subprocess on that Log, exits 1 naming the unobservable fraction; the same pair on a clean clip exits 0

**Closes on Kevin's clips (not an engage AC):** the budget value, set from the unknown fraction of good v1 sessions, and a deliberately bad-angle session that fails it.

**Depends on:** HF3.2
**Model:** claude-opus-4-8

### HF3.4 — Evidence frames: keep a thumbnail of every frame a flag can cite | Cx: 4 | P0

**Description:** The Log stores frame metadata, never pixels, so a flag's cited frames are gone once the run moves on. Add `src/station_watch/evidence.py` with `EvidenceStore`, owned by Capture's detect hook: every frame for which Detect emitted at least one `Observation`, and the last good frame before each blind record opens (`BlindRecord.last_good_frame_id`), is written as a downscaled JPEG to `<evidence_dir>/<run_id>/frame_<frame_id:08d>.jpg`, with one JSON line per frame in `<evidence_dir>/<run_id>/index.jsonl`: `frame_id`, `ts`, `fingerprint` (the `FrameRecord` fingerprint of the full frame), `path`, `width`, `height`, `scale`, and the marker `corners` scaled into thumbnail pixels (or null). Fault citations come from observation frame ids, so this keeps every citable frame while writing far fewer frames than the stream. Writes go through a bounded queue on one writer thread; a full queue drops the frame and counts it, and a write error is counted and reported once, never raised into Capture (K5, K10). Retention: `evidence.max_files` (oldest pruned first) and `evidence.thumb_width` from an optional `evidence:` config section with documented defaults. `station-watch run` gains `--evidence-dir` (default `data/local/evidence`) and `--no-evidence`. Add `EvidenceIndex.load(evidence_dir, run_id)` with `lookup(frame_id)` returning the entry or a reason (`not_captured`, `pruned`, `dropped`, `write_failed`). For recorded-file sessions, add `frame_from_clip(clip_path, frame_id, fingerprint)` that re-reads one frame and returns it only if its fingerprint matches, else a reason.

**Interface:**
- Consumes HF3.1: Capture finds marker corners once per frame and calls the detect hook with them; `marker_center(corners)`.
- Exposes, consumed by HF3.5: `EvidenceIndex.load(evidence_dir: Path, run_id: str) -> EvidenceIndex`; `EvidenceIndex.lookup(frame_id: int) -> EvidenceFrame | MissingEvidence`; `EvidenceFrame` (frozen) with `frame_id`, `ts`, `fingerprint`, `path` (absolute), `width`, `height`, `scale`, `corners` (list of four `[x, y]` in thumbnail pixels, or None); `MissingEvidence` (frozen) with `frame_id`, `reason` in `not_captured|pruned|dropped|write_failed`. `frame_from_clip(clip_path, frame_id, fingerprint) -> np.ndarray | MissingEvidence` (reason `fingerprint_mismatch` or `not_in_clip`).
- Consumes HF1 `FrameRecord` (frame_id, fingerprint, ts), `Observation.frame_id`, `BlindRecord.last_good_frame_id`.

**AC:**
- [ ] **E2E Conformance:** a real `station-watch run` subprocess on a synthetic clip with a part removed writes evidence, and every frame id cited by every `Verdict` fault in the Log resolves through `EvidenceIndex.lookup` to a file whose stored fingerprint equals the Log's `FrameRecord` fingerprint
- [ ] A dark blind episode's `last_good_frame_id` resolves to an evidence frame (test)
- [ ] A writer that raises on every write leaves the run's verdicts, alarms and cycle rows identical to a run with evidence off, and reports the failure once (test)
- [ ] With `evidence.max_files` small, the oldest frames are pruned and `lookup` returns `pruned` for them, never another frame (test)
- [ ] `--no-evidence` writes nothing under the evidence dir (test)
- [ ] `frame_from_clip` returns the frame for a matching fingerprint and `fingerprint_mismatch` for a wrong one (test)
- [ ] The default evidence dir is under `data/local/`; a test asserts `git ls-files` lists nothing under any evidence or audit dir

**Closes on Kevin's clips (not an engage AC):** evidence disk use per hour on the real camera, and the `max_files` value chosen from it.

**Depends on:** HF3.1
**Model:** claude-opus-4-8

## Phase 3: Audit, reviewed precision and false-alarm rate

### HF3.5 — `station-watch audit build`: a proof sheet for every flag | Cx: 4 | P0

**Description:** Add `src/station_watch/audit/` and `station-watch audit build --config <station.yaml> --log <path> [--evidence-dir data/local/evidence] [--clip <file>] [--out <dir>]`. Read the Log read-only through `LogReader` and collect every flag of the session: each fault episode (a contiguous run of verdicts carrying the same `(kind, target)` fault, its opening and closing verdict ts and the union of cited frame ids) and each blind episode (an `opened` `BlindRecord` paired with its `cleared` one, if any). Give each a deterministic `flag_id`: `{run_id}:{fault kind}:{target}:{first verdict seq}` for faults and `{run_id}:blind:{reason}:{opened seq}` for blind episodes. Write `<out>/flags.json` (the flag list) and a self-contained `<out>/index.html` proof sheet: per flag, its kind, target, verdict state, opened and closed timestamps and duration, and every cited frame as a thumbnail with the configured rail positions, station zone and keep-out zones drawn on it from the stored marker corners (the same `region_to_pixels` geometry Detect reads with), plus each frame's capture ts and frame id. A cited frame with no evidence shows the `MissingEvidence` reason in its place, never a substitute frame (K4); with `--clip` it falls back to `frame_from_clip`. No marker in the thumbnail means no overlay and the words "overlay unavailable: fiducial not found". Thumbnails are embedded as data URIs so the sheet opens from disk. The default `--out` is `data/local/audit/<log file stem>/`. The command never writes the Log.

**Interface:**
- Consumes HF3.4: `EvidenceIndex.load(evidence_dir, run_id)`, `lookup(frame_id) -> EvidenceFrame | MissingEvidence` (fields as listed in HF3.4), `frame_from_clip(clip_path, frame_id, fingerprint)`.
- Consumes HF3.2's `LogReader` (raises only `BoardLogError`) and HF1 records: `Verdict` (station_id, ts, state, faults of `Fault(kind, target, frame_ids)`, seq, run_id), `BlindRecord` (reason, state `opened|cleared`, seq, last_good_frame_id, ts), `FrameRecord` (frame_id, ts, fingerprint).
- Consumes `detect.geometry.region_to_pixels(region, corners)` and the config's `detect.rail_positions`, `detect.station_zone`, `detect.keepout_rois`.
- Exposes, consumed by HF3.6 and HF3.7: `collect_flags(reader) -> list[AuditFlag]`; `AuditFlag` (frozen) with `flag_id`, `kind` (fault kind value, or `unobservable:<reason>` for blind), `target` (str, the blind reason for blind episodes), `station_id`, `run_id`, `opened_ts`, `closed_ts` (str or None), `frame_ids` (tuple of int); and `flags.json` as a JSON list of those fields.

**AC:**
- [ ] **E2E Conformance:** a real `station-watch run` subprocess on a synthetic clip with a missing part and a dark stretch, then a real `station-watch audit build` subprocess, writes `flags.json` with exactly one `missing_part` flag and one `unobservable:dark` flag, and `index.html` showing each cited frame id with an embedded image
- [ ] Flag ids are identical across two builds of the same Log (test)
- [ ] A cited frame with no evidence shows its `MissingEvidence` reason and no image for that frame (test parses the HTML)
- [ ] The overlay is drawn on a thumbnail whose stored corners exist, and the "overlay unavailable" text appears when they are null (test checks pixels of the drawn region outline and the text)
- [ ] The Log file's bytes are unchanged after `audit build` (test)
- [ ] With no flags in the session the sheet says "no flags in this session" and `flags.json` is an empty list (test)

**Closes on Kevin's clips (not an engage AC):** overlays checked by eye on the real bench: the drawn rail positions land on the real components.

**Depends on:** HF3.4, HF3.2
**Model:** claude-opus-4-8

### HF3.6 — `station-watch audit review` and `audit score`: reviewer verdicts and reviewed precision | Cx: 4 | P0

**Description:** Reviewer marks and the number they feed. `station-watch audit review --audit <dir> [--port 8766]` serves the HF3.5 sheet on `127.0.0.1` with a correct / incorrect control and an optional note per flag. Marking posts JSON to `POST /verdict` `{"flag_id", "verdict": "correct"|"incorrect", "note"}` with header `X-Audit-Token` carrying a random token generated at server start and embedded in the served page. The server appends one line per mark to `<audit dir>/verdicts.jsonl` (`flag_id`, `verdict`, `note`, `ts`); the newest line per flag wins, so re-marking is safe and repeat posts are idempotent. A CLI equivalent, `station-watch audit mark --audit <dir> <flag_id> correct|incorrect [--note ...]`, writes the same file. Then `station-watch audit score --audit <dir> --config <station.yaml> --log <path> --dataset-kind real|synthetic --out <file>` writes a reviewed-precision measurement file: per flag kind and overall, `flags`, `reviewed`, `correct`, `incorrect`, `unreviewed`, and `precision = correct / reviewed` (null when nothing of that kind was reviewed), plus the session's QA result. Status rules (statuses go at the top level, beside provenance, with no `metrics` key): no reviewed flags gives `status: no_reviewed_flags`; a session that fails HF3.3 QA gives `status: qa_failed` with the QA reason. `--dataset-kind synthetic` may only write under `measurements/synthetic/`; real goes under `measurements/v1/` or `measurements/v2/`. Provenance `manifest_sha256` is the SHA-256 of `verdicts.jsonl` and `flags.json` concatenated; `dataset` is the Log's run id(s).

**Interface:**
- Consumes HF3.5: `collect_flags(reader) -> list[AuditFlag]` and `<audit dir>/flags.json` (list of `flag_id`, `kind`, `target`, `station_id`, `run_id`, `opened_ts`, `closed_ts`, `frame_ids`).
- Consumes HF3.3: `session_qa(config, log_path) -> SessionQA` with `status`, `reason`, `unobservable_fraction`.
- `POST /verdict` contract: request body JSON as above; 200 `{"ok": true, "flag_id": ...}` on success; 400 for an unknown `flag_id`, a verdict other than `correct|incorrect`, or a body that is not JSON; 403 for a missing or wrong `X-Audit-Token`, a Host other than `127.0.0.1:<port>` or `localhost:<port>`, or an `Origin` that is not the server's own; 405 for methods other than GET and POST. No other state changes. Idempotent: posting the same mark twice leaves the same effective verdict.
- Writes with `write_measurement` from `evaluate/provenance.py`; does not define a second format.

**AC:**
- [ ] Proving test: a real `audit review` server subprocess accepts a POST with the page's token and appends to `verdicts.jsonl`; a POST without the token, with a wrong Host, or with a foreign Origin gets 403 and appends nothing (tests)
- [ ] An unknown `flag_id` or an invalid verdict gets 400 (test); re-marking a flag changes its effective verdict (test)
- [ ] `audit score` on 3 reviewed flags (2 correct, 1 incorrect) and 1 unreviewed writes precision 2/3, `reviewed` 3, `unreviewed` 1 for that kind (subprocess test, hand-computed)
- [ ] With no marks, `audit score` writes `status: no_reviewed_flags` and no `metrics` key; on a session failing QA it writes `status: qa_failed` and no `metrics` key (tests)
- [ ] `--dataset-kind synthetic` with an `--out` outside `measurements/synthetic/` exits non-zero naming the rule (test)
- [ ] The server binds only to 127.0.0.1, and the Log's bytes are unchanged after review and score (tests)
- [ ] **E2E Conformance:** real subprocesses `station-watch run` (synthetic clip with a missing part), `audit build`, `audit review` (marked by an HTTP POST with the page's token) and `audit score` chain end to end, and the score file's `correct` count matches the mark

**Closes on Kevin's clips (not an engage AC):** Kevin marks every flag from a real v1 session and a v2 session; the reviewed precision files under `measurements/v1/` and `measurements/v2/` are committed.

**Depends on:** HF3.5, HF3.3
**Model:** claude-opus-4-8

### HF3.7 — `station-watch audit rate`: false-alarm rate per hour on normal operation | Cx: 3 | P0

**Description:** `station-watch audit rate --audit <dir> --config <station.yaml> --log <path> --dataset-kind real|synthetic --out <file> [--min-hours 1.0]` measures flags per hour from a long normal-work session. The denominator is the session's observable hours from HF3.3 (`observable_s / 3600`; blind time is not "normal operation" and never inflates the hours). Report `observed_hours`, `session_hours`, flags per hour split by kind, and, using the HF3.6 verdicts, `true_per_hour` (correct), `false_per_hour` (incorrect) and `unreviewed_per_hour`, overall and per kind. Unreviewed flags are never counted as true or false. Status rules (no `metrics` key in each): `observed_hours < --min-hours` gives `status: insufficient_duration` naming both numbers; a session failing QA gives `status: qa_failed`. Blind episodes are reported per hour in their own block, separate from detection flags, because a camera fault is not a false alarm of the detector.

**Interface:**
- Consumes HF3.5 `flags.json` / `collect_flags` (fields listed in HF3.5) and HF3.6 `verdicts.jsonl` lines (`flag_id`, `verdict` in `correct|incorrect`, `note`, `ts`; newest line per flag wins).
- Consumes HF3.3 `session_qa(...) -> SessionQA` (`status`, `observable_s`, `session_s`).
- Writes with `write_measurement`; synthetic output only under `measurements/synthetic/`, as in HF3.6.

**AC:**
- [ ] Proving test: a fixture Log of 2 observable hours with 4 flags (1 correct, 2 incorrect, 1 unreviewed) gives 2.0 flags per hour, 0.5 true, 1.0 false, 0.5 unreviewed per hour (subprocess test, hand-computed)
- [ ] An hour of blind time in the session does not change `observed_hours` (test)
- [ ] Under `--min-hours` writes `status: insufficient_duration` and no `metrics` key; a QA-failing session writes `status: qa_failed` (tests)
- [ ] Blind episodes appear only in their own block, never in the detection flag rates (test)
- [ ] **Counterparty:** reads the `verdicts.jsonl` written by HF3.6's `station-watch audit mark` and the `flags.json` written by HF3.5's `audit build` (named artifacts)
- [ ] **E2E Conformance:** real subprocesses `station-watch run` (synthetic clip), `audit build`, `audit mark` and `audit rate --min-hours` (set below the clip's length) chain end to end and write rates whose false count matches the marks

**Closes on Kevin's clips (not an engage AC):** a real normal-work session of at least the minimum length on the bench, every flag marked, `measurements/v1/false_alarms.json` committed.

**Depends on:** HF3.6
**Model:** claude-opus-4-8

## Phase 4: Held-out evaluation

### HF3.8 — Manifest split and the calibration provenance check | Cx: 4 | P0

**Description:** Extend the HF2.8 manifest, backward compatibly: each session may carry `split: calibration | held_out` (absent means `calibration`, so every existing manifest loads as before) and `recorded_on: YYYY-MM-DD`. `ClipLabel` gains `split`, `recorded_on` and `clip_sha256` (SHA-256 of the clip file, computed at load). Measurement provenance written by `evaluate` gains `split` and `clip_sha256s` (sorted list). `station-watch evaluate` gains `--split calibration|held_out` (default `calibration`) and scores only that split's clips. For `--split held_out`, before running any clip, it collects the **calibration provenance** for the config in force: the file named by `detect.step_times_path` if set, plus every file listed under a new optional config key `calibration.provenance` (the measurement files thresholds were set from). It refuses, writing nothing and exiting with a new distinct code `EXIT_CALIBRATION_LEAK = 5`, when: a held-out clip's SHA-256 appears in any calibration file's `clip_sha256s`; a held-out session id appears in any calibration file's `sessions`; a held-out session's `recorded_on` equals a calibration session's `recorded_on` in the same manifest; a held-out session has no `recorded_on`; or a calibration file lacks `clip_sha256s` ("cannot prove separation: calibration provenance predates clip hashes; re-run calibration"). The refusal message names the clip, session or file. When the config names no calibration provenance at all, held-out runs proceed and provenance records `calibration: none`. A held-out result's provenance carries `split: held_out` and a `calibration` list of each calibration file's path and SHA-256.

**AC:**
- [ ] An HF2-format manifest with no `split` loads unchanged, every clip `split == "calibration"`; all HF2 manifest and evaluate tests pass unchanged (test)
- [ ] Proving test: a generated held-out clip byte-identical to a clip listed in a calibration `step_times.json` makes `evaluate --split held_out` exit 5 naming the clip, with no file written (subprocess test)
- [ ] A shared session id, a shared `recorded_on` date, a missing `recorded_on` on a held-out session, and a calibration file without `clip_sha256s` each exit 5 naming the cause (tests)
- [ ] A clean held-out run on generated clips writes files whose provenance has `split: held_out`, sorted `clip_sha256s`, and the calibration file list with SHA-256s (test)
- [ ] `--split calibration` writes `split: calibration` and `clip_sha256s` into every file, including `step_times.json` (test)
- [ ] **E2E Conformance:** a real `station-watch evaluate --split calibration` subprocess writes `step_times.json`, a config whose `detect.step_times_path` names it is used by a real `evaluate --split held_out` subprocess, which exits 5 on a manifest reusing a calibration clip and writes held-out files on a manifest of fresh clips

**Closes on Kevin's clips (not an engage AC):** clip set v1 labeled `split: calibration` and v2, recorded on a different day, labeled `split: held_out`; the first real held-out run.

**Depends on:** None
**Model:** claude-opus-4-8

### HF3.9 — Calibration hygiene and the held-out claims rule | Cx: 3 | P0

**Description:** Three fixes that keep held-out numbers honest. (1) `step_times.json` is computed only from calibration-split clips labeled as normal work (no `stalls`, `keepouts`, `camera_faults` or `creeps` intervals), closing the PR #5 P2 where fault clips biased the stall window; its provenance records `step_clips` (the count used). With no normal calibration clips, `step_times.json` is not written and the CLI says why. (2) A real (non-synthetic) `evaluate` without `--out` exits 2 naming `--out`, closing the PR #5 P2 where real runs overwrote each other in `measurements/`. (3) Extend `tests/test_claims.py`: when any committed file under `measurements/` has `provenance.dataset_kind == "real"` and `provenance.split == "held_out"`, every README claim in "Measured performance" whose key is a flag `precision` or `recall` (`metrics.<flag>.precision|recall`) must cite a held-out file, a claim citing a calibration-split real file for those keys fails naming it, and the section must contain the words "held-out". Until a held-out file exists the existing rules apply unchanged. Add tests of the test using temp measurement trees, as the existing claims tests do.

**Interface:**
- Consumes HF3.8's provenance fields: `split` (`calibration|held_out`), `clip_sha256s`, `dataset_kind`; and `ClipLabel.split` plus the labeled interval lists (`stalls`, `keepouts`, `camera_faults`, `creeps`) to pick normal clips.
- `write_step_times(path, stats, provenance)` keeps its signature; `provenance` gains `step_clips`.

**AC:**
- [ ] Proving test: a generated calibration set with one normal clip and one long stall clip gives a `step_times.json` p95 computed from the normal clip only, with `step_clips: 1` (test, hand-checked against the normal clip's steps)
- [ ] With no normal calibration clips, no `step_times.json` is written and the output says why (test)
- [ ] Real `evaluate` without `--out` exits 2 naming `--out`; `--synthetic` without `--out` still defaults to `measurements/synthetic` (tests)
- [ ] Tests of the test: with a temp tree holding a real held-out file, a README claim citing a real calibration file for `metrics.missing_part.precision` fails naming it; citing the held-out file passes; the section without "held-out" fails
- [ ] The shipped README passes the claims test unchanged (no held-out file is committed in this lane)
- [ ] **E2E Conformance:** a real `station-watch evaluate --split calibration` subprocess writes `step_times.json` from the normal clip only, and a real `station-watch run` subprocess whose config names that file starts with a stall window of its p95 plus `grace_s` (read from the startup line)

**Closes on Kevin's clips (not an engage AC):** README precision and recall cite `measurements/v2/` held-out numbers, labeled held-out, once v2 is evaluated.

**Depends on:** HF3.8, HF3.1
**Model:** claude-opus-4-8

## Phase 5: Soak

### HF3.10 — Soak sampler and the growth verdict | Cx: 3 | P1

**Description:** Add `src/station_watch/soak/sampler.py` and `src/station_watch/soak/verdict.py`, pure and testable without long runs. The sampler takes one `Sample` every interval: elapsed seconds; RSS in KB per named child process (read with `ps -o rss= -p <pid>`, stdlib only, macOS and Linux; a dead pid is recorded as dead, not zero); Log file bytes and WAL bytes; row counts per kind through `LogReader` (read-only); Board `/view.json` render time in seconds (timed HTTP GET to `127.0.0.1:<port>`, a failure recorded as a failure with the reason, not a time); total verdicts and the newest verdict ts. The verdict reduces samples against thresholds from a required `soak:` config section (`max_rss_growth_mb_per_h`, `max_log_mb_per_h`, `max_board_render_s`, `max_verdict_gap_s`, `max_false_flags`, `warmup_s`): RSS growth is the least-squares slope over post-warmup samples per process; Log growth is bytes per hour after warmup; Board render is the p95 of successful renders and any failed render fails the soak; verdict cadence is verdicts per minute and the largest gap between consecutive verdict ts; false flags are faults raised while the source is a synthetic normal loop (every one is false by construction). Any threshold crossed fails with the name and both numbers. A missing `soak.*` key fails loud naming it (K9). Fewer than three post-warmup samples gives `status: insufficient_samples`, never a pass.

**Interface:**
- Exposes, consumed by HF3.11: `take_sample(*, elapsed_s, pids: dict[str, int], log_path, board_port: int | None) -> Sample`; `Sample` (frozen) with `elapsed_s`, `rss_kb` (dict name to int or None when dead), `log_bytes`, `wal_bytes`, `rows` (dict kind to int), `board_render_s` (float or None), `board_error` (str or None), `verdicts`, `newest_verdict_ts`; `soak_verdict(samples, thresholds: dict, *, false_flags: int) -> dict` returning `{"status": "pass"|"fail"|"insufficient_samples", "failures": [str], "metrics": {...}}`; `load_soak_thresholds(config) -> dict` raising `StartupError` naming the dotted key.
- Consumes HF3.2's `LogReader` and the Board's `/view.json` GET endpoint (200 with the JSON view; Host must be `127.0.0.1:<port>`).

**AC:**
- [ ] Proving test: a synthetic sample series with RSS rising 50 MB/h fails a 10 MB/h threshold naming the process and both numbers; a flat series passes (test)
- [ ] A verdict gap above `max_verdict_gap_s`, a failed Board render, and false flags above the limit each fail naming the cause (tests)
- [ ] `take_sample` on a real child process and a temp Log returns a positive RSS and correct row counts; on a dead pid records it as dead (test)
- [ ] Fewer than three post-warmup samples gives `insufficient_samples` (test); a missing `soak.max_board_render_s` raises naming the key (test)
- [ ] **E2E Conformance:** `take_sample` against a real `station-watch run` subprocess and a real `station-watch board` subprocess on the same Log returns a positive RSS for both, nonzero frame and verdict row counts, and a Board render time

**Depends on:** HF3.2
**Model:** claude-opus-4-8

### HF3.11 — `station-watch soak`: run, watchdog and Board for hours on a looping source | Cx: 4 | P1

**Description:** Add `station-watch soak --config <station.yaml> --log <path> --out <file> (--synthetic-loop | --source <device index or file>) (--hours H | --minutes M) [--sample-s 30] [--board-port 8765]`. It starts three child processes against one Log: the runner (with `--synthetic-loop`, a child entry `python -m station_watch.soak.child` that builds the same `Runner` `station-watch run` uses through `build_context(source=...)`, fed by a `SyntheticSource` looping a normal-work script: parts present, motion bursts shorter than the stall window, no faults; with `--source`, the real `station-watch run`), `station-watch watchdog`, and `station-watch board`. Add looping to `SyntheticSource` (`script=[...]`, `loop=True`) without changing its existing single-spec behavior. Every `--sample-s` it takes a sample (HF3.10). A child that exits early fails the soak with its name and exit code. At the end it stops all children (SIGTERM, then SIGKILL after a bounded wait), runs HF3.3 QA on the Log, applies `soak_verdict`, and writes a measurement file (`dataset_kind` synthetic for the loop, real for a camera; metrics hold the verdict metrics, the QA fractions and the duration) plus a markdown table beside it with the reproduce command in its header. Exit 0 only on pass. Commit a synthetic soak of at least 10 minutes under `measurements/synthetic/soak.json` with its table, clearly labeled synthetic, reproduced by the command in the table header; tests use a run of well under a minute with thresholds set for the test.

**Interface:**
- Consumes HF3.10: `take_sample(...) -> Sample`, `soak_verdict(samples, thresholds, *, false_flags) -> dict`, `load_soak_thresholds(config)` (fields as listed in HF3.10).
- Consumes HF3.3: `session_qa(config, log_path) -> SessionQA` (`status`, `unobservable_fraction`, `observable_s`).
- Consumes the HF1 CLI contracts: `station-watch run --config --source --log`, `station-watch watchdog --config --log`, and the HF3A/HF3.2 `station-watch board --config --log --port` (loopback, GET `/view.json`), each stopping cleanly on SIGTERM; `build_context(...)` with an injected `source`, as the drill uses it.

**AC:**
- [ ] **E2E Conformance:** `station-watch soak --synthetic-loop --minutes 0.5` as a real subprocess starts three children, samples at least three times, stops every child (no orphan pids after exit), and writes a passing measurement file with RSS, Log growth, Board render, verdict cadence and zero false flags
- [ ] A soak whose watchdog child is killed mid-run fails naming `watchdog` and its exit code (test)
- [ ] A soak with a deliberately tiny `max_rss_growth_mb_per_h` fails naming the process (test)
- [ ] The looping `SyntheticSource` repeats its script; existing `SyntheticSource` and drill tests pass unchanged (test)
- [ ] The committed `measurements/synthetic/soak.json` and table exist, say synthetic in `dataset_kind` and the table heading, and cover at least 10 minutes

**Closes on Kevin's clips (not an engage AC):** a multi-hour soak on the real camera on the edge box of record, committed under `measurements/v1/soak.json`.

**Depends on:** HF3.10, HF3.3
**Model:** claude-opus-4-8

## Phase 6: Demo readiness

### HF3.12 — `station-watch preflight`: PASS or FAIL per item before going live | Cx: 4 | P0

**Description:** Add `src/station_watch/preflight/` and `station-watch preflight --config <station.yaml> --source <device index or file> --log <path> [--board-port 8765] [--json]`. Each check is a function returning `CheckResult(name, status, detail)` with status `PASS`, `FAIL`, `WARN` or `SKIP`; each runs under a timeout and never raises (an exception or a timeout is `FAIL` with the reason, K10). Checks, in order: `config` (loads and validates, K9 message on failure); `camera` (the source opens); `frames_live` (within `liveness_window_s`, several frames arrive with distinct fingerprints and nonzero noise, K2); `fiducial` (marker found once per frame through HF3.1's corners, center within `fiducial.tolerance_px` of `expected_center_px`, detail gives the measured offset); `model_weights` (only when `keepout_zones` is non-empty: weights present and SHA-256 verified via `verify_weights`, else `SKIP` naming why); `log_writable` (the Log's directory exists and a probe SQLite file can be created and removed there; an existing Log opens); `disk_space` (free space at the Log and evidence dirs at least `preflight.min_free_gb`, default stated in the config comment); `clock` (wall clock not earlier than the newest Log row ts and the year is plausible; the detail says NTP sync is not checked); `alarm_sinks` (every configured sink constructs, the alarm sound asset exists); `board` (with `--board-port`, a GET of the Board's JSON view on `127.0.0.1:<port>` returns 200 within 2 s; without it, `SKIP`). Prints one aligned line per check and a final `PREFLIGHT PASS` or `PREFLIGHT FAIL`; `--json` prints the results as JSON. Exit 0 only when no check is `FAIL`. Preflight only reads the camera and the Log; it writes nothing but its probe file, which it removes.

**Interface:**
- Consumes HF3.1: `find_marker_corners(frame, dictionary_id, marker_id)` and `marker_center(corners)`.
- Consumes `runner.startup.load_config`, `parse_source`, `open_source` (raising `StartupError` / `CaptureError` with the source named), `detect.yolox.verify_weights`, `runner.startup.build_alarm_sinks`, and the Board's JSON view over loopback HTTP (the path `board/server.py` serves).
- Exposes, consumed by HF3.13 and HF3.17: `CheckResult` (frozen: `name`, `status` in `PASS|FAIL|WARN|SKIP`, `detail`), `run_preflight(config_path, source, log_path, *, board_port=None, checks=None) -> list[CheckResult]`, and a registry `CHECKS` (ordered name to callable) that HF3.13 extends with a new check without editing the others.

**AC:**
- [ ] **E2E Conformance:** `station-watch preflight` as a real subprocess on a generated synthetic clip with the marker in place prints PASS for config, camera, frames_live, fiducial, log_writable, disk_space, clock and alarm_sinks, SKIP for model_weights and board, and exits 0
- [ ] A clip with the marker hidden gives `fiducial FAIL` and exit 1; a source that cannot open gives `camera FAIL` naming it and later camera-dependent checks `FAIL` or `SKIP` with that reason, never PASS (tests)
- [ ] A frozen clip (identical frames) gives `frames_live FAIL` (test)
- [ ] An unwritable Log directory gives `log_writable FAIL`; a check that raises or exceeds its timeout reports `FAIL` with the reason (tests)
- [ ] **Counterparty:** `board/server.py::make_server`, run as a real `station-watch board` subprocess on an ephemeral port, answers the `board` check, which reports PASS; with nothing listening it reports `board FAIL` (named artifact, test)
- [ ] `--json` output parses and matches the text results; the Log's bytes are unchanged (tests)

**Closes on Kevin's clips (not an engage AC):** preflight run on the edge box of record with the real camera, bench and lamp, all PASS, the day before demo day and on the morning of it.

**Depends on:** HF3.1
**Model:** claude-opus-4-8

### HF3.13 — Webcam sources: a generic UVC webcam and the DJI Pocket 3, with a gimbal warning | Cx: 2 | P1

**Description:** Make `--source <device index>` a documented, tested path. Add `station-watch preflight --list-cameras [--max-index 5]`, which tries device indexes 0 to N, and for each that opens prints the index, the reported resolution and fps, and whether a frame was read; it never fails on an index that does not open (OpenCV index order is not stable across machines, so this is how the operator finds the right one). Add an optional config key `camera.gimbal: true|false` and a preflight check `camera_stability`: when `camera.gimbal` is true it always reports `WARN` with "a gimbal can drift and raise view_shifted; lock it before going live"; for any camera it samples the fiducial center for `--drift-s` seconds (default 5) and reports `WARN` when the center moves more than half of `fiducial.tolerance_px`, with the measured drift. Write `docs/CAMERAS.md`: how to pick the index with `--list-cameras`, a generic UVC webcam setup, and the DJI Pocket 3 used as a USB webcam with the gimbal locked. Device steps not yet verified on the hardware are marked "unverified" in the doc; never invent menu names.

**Interface:**
- Consumes HF3.12: `CheckResult`, the `CHECKS` registry (adds `camera_stability` after `fiducial`), `run_preflight(...)`.
- Consumes `runner.startup.parse_source` (`"0"` to int, a path to str) and `capture.source.FrameSource(int)` raising `CaptureError` naming the index.

**AC:**
- [ ] `parse_source("0")` is the int 0 and `parse_source("clip.mkv")` a path; `station-watch run --source 7` with no such device exits non-zero naming index 7 (tests, the latter with a fake capture factory or skipped with a reason when a device 7 exists)
- [ ] `--list-cameras` with a fake capture factory where only index 1 opens prints index 1 with its resolution and fps and exits 0 (test)
- [ ] `camera.gimbal: true` gives `camera_stability WARN` with the gimbal text; a synthetic source whose marker drifts by more than half the tolerance gives `WARN` with the measured drift, and a steady one `PASS` (tests)
- [ ] `docs/CAMERAS.md` exists, every `station-watch` command in it parses with the real argparse parser (test), and it contains no unmarked device menu claims (the word "unverified" appears beside each Pocket 3 device step until verified)

**Closes on Kevin's clips (not an engage AC):** the index, resolution and fps of the Mac mini's UVC webcam and the Pocket 3 in webcam mode recorded in `docs/CAMERAS.md` from `--list-cameras`; a 10-minute run on the locked Pocket 3 with no `view_shifted` alarm; the "unverified" marks removed for steps Kevin confirmed.

**Depends on:** HF3.12
**Model:** claude-opus-4-8

### HF3.14 — Board browser QA under load: never OK on stale or unknown | Cx: 3 | P1

**Description:** Playwright is not a dev dependency, so this is a headless HTTP plus HTML assertion suite against the real server. Build a large Log (at least 200,000 rows across frames, observations, verdicts, blind episodes and alarm evaluations) in a temp dir once per module (a fixture with a fast bulk insert through `Log`), start `make_server` on an ephemeral port, and assert with `html.parser` and the JSON view: the page and JSON show the station state, every active flag with its cited frame ids, the frame age, the open blind reasons and the alarm episodes; render time p95 over 20 requests, and over 4 concurrent clients, stays under a generous bound stated in the test (and printed, so the real number is visible in CI logs). A table-driven property test covers the K1 rule: across combinations of newest verdict state (healthy, fault, unobservable, none), verdict age (fresh, stale), frame age (fresh, stale), alarm evaluation age (fresh, stale) and open blind reasons (none, one), the page shows the healthy/OK status only when the verdict is healthy and fresh, frames and alarm evaluation are fresh and no blind reason is open; every other combination shows UNKNOWN, STALE, FAULT or the blind reason. If the `playwright` package happens to be importable, one extra test loads the page in a headless browser and checks the same status text; it is skipped otherwise (`pytest.importorskip`), and no dependency is added.

**Interface:**
- Consumes HF3.2: `make_server(config, log_path, *, port, clock)` bound to 127.0.0.1 with GET `/` (HTML) and `/view.json`, Host check, `nosniff` header, UNKNOWN on undecodable rows; `build_view(config, log_path, *, now)` and `render_view_json(view)` as on `main`.

**AC:**
- [ ] Proving test: on the 200,000-row Log the page and JSON show state, flags with frame ids, frame age, blind reasons and alarm episodes, and the render bound holds for sequential and 4-way concurrent requests
- [ ] The K1 property table covers every combination listed and passes; a deliberately broken renderer that shows OK for a stale verdict makes it fail (test of the test)
- [ ] A Log with an undecodable newest row shows UNKNOWN in the page (test)
- [ ] No new dependency in `pyproject.toml`; the optional Playwright test is skipped cleanly when the package is absent
- [ ] **E2E Conformance:** a real `station-watch board` subprocess (not an in-process server) serves the 200,000-row Log and the same page and JSON assertions pass against it over HTTP

**Closes on Kevin's clips (not an engage AC):** the operator screen test from PLAN 6 (Thu 10/15): someone who has not seen it reads the Board unaided on the real bench.

**Depends on:** HF3.2
**Model:** claude-opus-4-8

## Phase 7: Per-slot detail checks (PLAN HF3)

### HF3.15 — Slot detail readers and the renderer: label, ferrule and torque stripe | Cx: 4 | P1

**Description:** PLAN 5.1 lists the bench checks beyond "component present and seated": label present at a termination, ferrule present, torque stripe present. Add `src/station_watch/detect/details.py` with a pure per-frame reader `read_detail(frame, poly, detail_kind, detect_config) -> DetailReading` for three kinds, each with a named, versioned method: `label` (fraction of near-white, low-saturation pixels in the region, the heat-shrink label), `ferrule` (fraction of bright, low-saturation metallic pixels with high edge density at the wire end), `torque_stripe` (fraction of pixels inside a configured HSV hue range, the paint pen color). Each reads `present`, `absent` or `unknown`; unknown causes (`out_of_frame`, `dark`, `blurred`, `occluded`) come from the same checks `detect/regions.py` uses, refactored into a shared helper rather than copied; each reading carries a confidence ceiling lowered by blur, dimness and partial occlusion (K3) and the raw scores (K4). Thresholds live in `detect.details.<kind>` (`min_fill`, and `hue_range` for the stripe) with documented defaults. Extend the synthetic renderer (`synth/station.py`) with a per-frame spec key `details: {position_id: {label|ferrule|torque_stripe: "present"|"absent"}}` that draws a white label band, a silver ferrule and a colored stripe on the component at default detail regions in marker units, and returns them in the ground truth; frames without `details` render exactly as before.

**Interface:**
- Consumes HF3.1's per-frame corners and `detect.geometry.region_to_pixels(region, corners)`; the existing renderer entry `write_synth_station_clip(dir_path, script, **render_opts) -> (path, truth)` and `_render_frame` used by `SyntheticSource`.
- Exposes, consumed by HF3.16: `read_detail(frame, poly, detail_kind, detect_config) -> DetailReading` with `DetailReading` (frozen: `target`, `state` in `present|absent|unknown`, `cause` or None, `confidence_ceiling`, `scores` dict); `DETAIL_KINDS = ("label", "ferrule", "torque_stripe")`; `DEFAULT_DETAIL_REGIONS` (position id to kind to marker-unit quad) matching what the renderer draws; renderer truth entries gain `details: {position_id: {kind: state}}`.

**AC:**
- [ ] Proving test per kind: across at least 5 noise seeds, synthetic frames with the detail drawn read `present` and without it read `absent`
- [ ] Dim, blurred, and hand-occluded detail regions read `unknown` with the matching cause and a lower confidence ceiling than a clean read (tests)
- [ ] A torque stripe drawn outside the configured hue range reads `absent` (test)
- [ ] Every existing renderer, Detect and evaluate test passes unchanged; frames rendered without `details` are byte-identical to before for the same seed (test)
- [ ] The unknown-cause checks are shared with `detect/regions.py`, not duplicated (a test imports the one helper from both call sites, or arch check shows no duplicate)
- [ ] **E2E Conformance:** a clip written by `write_synth_station_clip` with details, opened through the real `capture.source.FrameSource` file path, reads each frame's detail states matching the renderer's ground truth

**Closes on Kevin's clips (not an engage AC):** `min_fill` and the stripe hue range calibrated on v1 under the real lamp; whether a ferrule is resolvable from the camera's height at the chosen resolution (if not, the README says so and the ferrule check is dropped from the demo rather than faked).

**Depends on:** HF3.1
**Model:** claude-opus-4-8

### HF3.16 — Slot details through Detect, Judge and evaluate | Cx: 4 | P1

**Description:** Wire the HF3.15 readers into the live path. Config: an optional `detect.slot_details: {position_id: {kind: quad}}`. Observation targets are `<position_id>.<kind>` (for example `rail_pos_1.torque_stripe`), and a `required_slots` entry of that form makes the detail required; config validation fails loud naming the dotted key when a required detail has no `detect.slot_details` region or its position has no `detect.rail_positions` entry (K9). A `DetailTracker` with the tracker protocol (`update(frame, frame_id, ts, corners)`, `unknown_all(...)`) applies the same N-frame persistence and `emit_interval_s` re-emit as `PositionTracker`, reusing its persistence machinery (refactor, do not copy), and emits `part_present`, `part_absent` or `part_unknown` with `method` naming the detail method. A detail is judged only while its parent position's confirmed state is `present`; otherwise it emits `part_unknown` with cause `parent_not_present`. The Judge raises no `missing_part` for a detail whose parent position has an active `missing_part` fault, so a missing component yields exactly one fault. `build_detector` adds the tracker when details are configured. Evaluate scores detail targets with the existing rail-position confusion matrix (manifest `positions` entries with target `rail_pos_1.label`), and the synthetic evaluation set gains one clip with a missing torque stripe; regenerate `measurements/synthetic/` with `evaluate --synthetic` and keep README claims passing (the existing claim values must not change; add no new README numbers in this task).

**Interface:**
- Consumes HF3.15: `read_detail(...) -> DetailReading` (`target`, `state`, `cause`, `confidence_ceiling`, `scores`), `DETAIL_KINDS`, `DEFAULT_DETAIL_REGIONS`, renderer spec key `details` and truth `details`.
- Consumes HF3.9's evaluate changes (normal-clip step times, required `--out` for real runs) and HF2's `Detector` tracker protocol, `PositionTracker`, `judge_rules.missing_part_faults`, `evaluate/metrics.rail_confusion`.
- Observation records stay the HF1 `Observation` format; no new `ObservationKind` or `FaultKind`.

**AC:**
- [ ] **E2E Conformance:** a real `station-watch run` subprocess on a synthetic clip where the torque stripe on `rail_pos_1` is missing, with `rail_pos_1.torque_stripe` required, raises exactly one `missing_part` fault with target `rail_pos_1.torque_stripe` citing frames that exist; restoring the stripe recovers after the configured healthy verdicts
- [ ] A clip with the whole component missing raises exactly one `missing_part` fault (the position), none for its details, and details read `part_unknown` with cause `parent_not_present` (test)
- [ ] A required detail with no `detect.slot_details` region fails config load naming the dotted key (test)
- [ ] Details never read `part_present` while occluded (test with the hand blob)
- [ ] Regenerated `measurements/synthetic/detect.json` includes the detail targets in the rail confusion matrix; README claims test passes unchanged

**Closes on Kevin's clips (not an engage AC):** label, ferrule and torque-stripe flags checked against labels on v1 and scored on held-out v2; the demo's "pull a label" moment rehearsed on the bench.

**Depends on:** HF3.15, HF3.9
**Model:** claude-opus-4-8

### HF3.17 — Demo day run-of-show and the cold-start measurement | Cx: 3 | P1

**Description:** Add `station-watch preflight --cold-start --config <station.yaml> --source <s> --log <path> --out <file>`: it launches `station-watch run` as a child, measures seconds from launch to the first `FrameRecord`, to the first verdict, and to the first `healthy` verdict in the Log (polled read-only, bounded by a timeout that reports `status: no_healthy_verdict` with no `metrics` key when crossed), stops the child, and writes a measurement file (`dataset_kind` synthetic for a generated clip, real for a camera). Commit a synthetic cold-start file under `measurements/synthetic/cold_start.json`. Write `docs/DEMO.md`, the demo-day run-of-show, following PLAN 8.1: setup checklist (bench, lamp, camera, edge box, Board screen, power strip), the preflight command and what each FAIL means, cold start time cited from the measurement file with a claim marker in the README's format (synthetic until the bench run), then per demo beat what to trigger and what to say (normal cycle with frame age, a part left off, a pulled label or missing torque stripe, work stopped until the stall threshold, a hand in the keep-out zone, the lens covered with the whole station going UNKNOWN), and how each recovers (K11: recovery is the next good cycles on camera, not a button). Backup plans: power (restart order and the cold-start time), wifi (nothing in the run path uses the network; `fetch-model` must have been run beforehand), camera (the spare UVC webcam by index, then a recorded clip from `data/local/` via `--source <file>`, then a synthetic drill clearly announced as synthetic). Extend the claims test so every number in `docs/DEMO.md` carries a claim marker, as in the README section.

**Interface:**
- Consumes HF3.12: `station-watch preflight` options and `CheckResult` statuses; HF3.13: `--list-cameras`, `camera.gimbal`, `docs/CAMERAS.md`; HF3.6: the `audit` subcommands named in the post-demo review step; HF3A's `station-watch drill` (synthetic and live) and the fault names in `faults.py` (`lens_covered`, `frozen`, `bumped`, `cable_pulled`, `lights_off`).
- Uses the claim marker syntax from `tests/test_claims.py` (`<!-- claim: <path>#<dotted key> round=N -->value`).

**AC:**
- [ ] **E2E Conformance:** a real `station-watch preflight --cold-start` subprocess, which itself launches a real `station-watch run`, on a generated clip writes a file with launch-to-first-frame, first-verdict and first-healthy-verdict seconds in increasing order; with a clip that never becomes healthy it writes `status: no_healthy_verdict` and no `metrics` key (subprocess tests)
- [ ] Every `station-watch` command in `docs/DEMO.md` parses with the real argparse parser, and every fault name it uses exists in `faults.py` (test)
- [ ] The claims test checks `docs/DEMO.md`: every number has a claim marker and matches its file (test, plus a test of the test with an unmarked number)
- [ ] `docs/DEMO.md` covers each PLAN 8.1 beat, the recovery for each, and the power, wifi and camera backup plans (test asserts the section headings exist)

**Closes on Kevin's clips (not an engage AC):** real cold start on the edge box of record under `measurements/v1/cold_start.json`; a full rehearsal with fault injection (PLAN 6, Tue 10/27) following `docs/DEMO.md`, with anything that did not match fixed in the doc.

**Depends on:** HF3.12, HF3.13, HF3.6
**Model:** claude-opus-4-8

## Phase 8: Wire it

### HF3.18 — Wiring, README "Run it", CI | Cx: 2 | P0

**Description:** Update the HF1 orphan-module test so every module under `src/station_watch/` must be reached from the run, watchdog, drill, measure, board, evaluate, audit, qa, soak or preflight entry points, and fix any orphan. Add to README's "Run it" section the commands for `station-watch audit build|review|mark|score|rate`, `station-watch qa`, `station-watch soak`, `station-watch preflight` (including `--list-cameras` and `--cold-start`), the `--evidence-dir` / `--no-evidence` run options, and `evaluate --split held_out`, each with one line on what it writes and what its honest no-data status means; link `docs/DEMO.md` and `docs/CAMERAS.md`. Change nothing else in README: no new numbers in "Measured performance" in this lane.

**AC:**
- [ ] The orphan-module test covers the new entry points and passes
- [ ] README "Run it" commands for audit, qa, soak, preflight and held-out evaluate parse with the real argparse parser (test); README Status, principles and "Measured performance" sections unchanged; the claims test passes
- [ ] No footage, images, binary video, weights, evidence frames, audit sheets or verdict files are tracked (`git ls-files` check in a test); `.gitignore` unchanged
- [ ] CI green: ruff check, ruff format --check, pytest; `bpsai-pair arch check --strict src` and `bpsai-pair arch check --strict tests` both pass

**Depends on:** HF3.7, HF3.9, HF3.11, HF3.14, HF3.16, HF3.17
**Model:** claude-opus-4-8

---

## Delivery Summary

| ID | Title | Cx | Priority | Model | Depends on |
|---|---|---|---|---|---|
| HF3.1 | Pipeline carry-overs: ArUco once per frame, stall window default, measure errors, motion mask, per-clip backends | 4 | P1 | claude-opus-4-8 | None |
| HF3.2 | Board hardening carry-overs: UNKNOWN on decode failure, headers, threading, indexed blind reasons | 3 | P0 | claude-opus-4-8 | None |
| HF3.3 | Session QA: a per-session unknown budget a mostly blind session cannot pass | 3 | P0 | claude-opus-4-8 | HF3.2 |
| HF3.4 | Evidence frames: keep a thumbnail of every frame a flag can cite | 4 | P0 | claude-opus-4-8 | HF3.1 |
| HF3.5 | `station-watch audit build`: a proof sheet for every flag | 4 | P0 | claude-opus-4-8 | HF3.4, HF3.2 |
| HF3.6 | `station-watch audit review` and `audit score`: reviewer verdicts and reviewed precision | 4 | P0 | claude-opus-4-8 | HF3.5, HF3.3 |
| HF3.7 | `station-watch audit rate`: false-alarm rate per hour on normal operation | 3 | P0 | claude-opus-4-8 | HF3.6 |
| HF3.8 | Manifest split and the calibration provenance check | 4 | P0 | claude-opus-4-8 | None |
| HF3.9 | Calibration hygiene and the held-out claims rule | 3 | P0 | claude-opus-4-8 | HF3.8, HF3.1 |
| HF3.10 | Soak sampler and the growth verdict | 3 | P1 | claude-opus-4-8 | HF3.2 |
| HF3.11 | `station-watch soak`: run, watchdog and Board for hours on a looping source | 4 | P1 | claude-opus-4-8 | HF3.10, HF3.3 |
| HF3.12 | `station-watch preflight`: PASS or FAIL per item before going live | 4 | P0 | claude-opus-4-8 | HF3.1 |
| HF3.13 | Webcam sources: a generic UVC webcam and the DJI Pocket 3, with a gimbal warning | 2 | P1 | claude-opus-4-8 | HF3.12 |
| HF3.14 | Board browser QA under load: never OK on stale or unknown | 3 | P1 | claude-opus-4-8 | HF3.2 |
| HF3.15 | Slot detail readers and the renderer: label, ferrule and torque stripe | 4 | P1 | claude-opus-4-8 | HF3.1 |
| HF3.16 | Slot details through Detect, Judge and evaluate | 4 | P1 | claude-opus-4-8 | HF3.15, HF3.9 |
| HF3.17 | Demo day run-of-show and the cold-start measurement | 3 | P1 | claude-opus-4-8 | HF3.12, HF3.13, HF3.6 |
| HF3.18 | Wiring, README "Run it", CI | 2 | P0 | claude-opus-4-8 | HF3.7, HF3.9, HF3.11, HF3.14, HF3.16, HF3.17 |

Total Cx: 61.

## Priority Order

1. HF3.1 and HF3.2 (carry-over fixes; everything downstream builds on the single ArUco pass and the hardened reader)
2. HF3.3 (session QA; gates reviewed precision, false-alarm rate and soak)
3. HF3.8 (manifest split and the leak refusal; independent of the audit chain)
4. HF3.4, HF3.5, HF3.6, HF3.7 (evidence, proof sheet, reviewer verdicts, false-alarm rate)
5. HF3.9 (calibration hygiene and the held-out claims rule)
6. HF3.12 (preflight; the demo-day gate)
7. HF3.10, HF3.11 (soak)
8. HF3.14 (Board QA under load)
9. HF3.13 (webcam sources)
10. HF3.15, HF3.16 (slot detail checks)
11. HF3.17 (run-of-show and cold start)
12. HF3.18 (wiring, README, CI)

If the lane runs long, cut in this order (PLAN 6 cut list spirit: never cut the measured numbers or the camera-health path): HF3.16 then HF3.15 (slot details move to HF4), HF3.14, HF3.13. Never cut HF3.3, HF3.5 to HF3.9 or HF3.12.
