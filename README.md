# Station Watch

A camera over one assembly station that tells the supervisor when work has stalled, when a kitted part is missing, or when a hand enters a keep-out zone, and that alarms loudly when the camera itself goes blind instead of reporting all clear.

Entry for the HackFW 2026 MADE track (AI for Manufacturing), Fort Worth.

## Status

Building. Work began **Monday, September 28, 2026**, before the Thursday, October 1 kickoff. The HackFW rules we could find say nothing about when work may start, and the Devpost schedule opens submissions before the kickoff, so we read pre-kickoff work as allowed. We are asking the organizers at kickoff. The commit history is the record: it is never backdated or squashed to hide when work happened, and if the organizers rule otherwise, the history shows exactly what to set aside.

Disclosure: Kevin Masterson, who is building this entry, is also the Fort Worth DAO's HackFW campaign lead.

## What it does

One overhead or angled camera watches one station and its kitted parts tray. It produces three kinds of flags, each with the frames that prove it, and one alarm about itself:

1. **Stalled work.** A unit sits in the station zone with no assembly motion for longer than a threshold taken from measured normal step times.
2. **Missing part at kit check.** Each tray slot is classified present, absent, or **unknown** (occluded, too dark, too blurry). Unknown is never silently treated as present or absent.
3. **Unsafe motion.** A hand or arm enters a marked keep-out zone while that zone is active. The system only alerts; it never controls anything. Hardware interlocks and operator approval stay outside the AI's control path.
4. **Camera health alarm.** If frames stop arriving, the image goes dark, the image freezes, or the view shifts, the station becomes UNKNOWN and a loud alarm fires. A silent camera must never read as "no problems found."

Everything runs on one local edge box. Footage does not leave the building.

## Pipeline

Capture, Detect, Judge, Log, Alarm, plus an optional local Explain step, a read-only Board for the supervisor, and a Watchdog on its own clock. Each component is built after the one that feeds it and ships with its own proving test.

## Run it

Install the package (Python 3.12+) into a virtualenv:

```bash
pip install -e .
```

Watch a live webcam (device index `0`), raising alarms on screen and through the buzzer named in the config:

```bash
station-watch run --config config/station-example.yaml --source 0 --log station.db
```

Watch a recorded file instead of a camera (any path OpenCV can open):

```bash
station-watch run --config config/station-example.yaml --source clip.mkv --log station.db
```

The example config watches the camera's own health: frames arriving, not dark, not frozen, and the fiducial marker in place. Its marker position matches the synthetic test clip, so for a real camera put the printed marker in view and set `fiducial.expected_center_px` to where it appears; until then the "fiducial missing" alarm stays up, which is the canary doing its job. It lists no Detect targets, so it reads no parts (see below). A recorded file ends the run when it has been read to the end; a live camera never does, so an unplugged camera raises the "disconnected" alarm and the run keeps retrying it until you stop it (Ctrl-C or SIGTERM).

Detect runs automatically when the config lists rail positions or zones under `detect`: Capture reads those targets from the same frames and appends the observations to the Log for the Judge, so a missing or unseated part becomes a fault. The example config lists none, so it stays camera-health only. `--observations` is fixture input for drills and is refused alongside a `detect` config with targets — one source of observations per run.

Keep-out zones need a person detector. Station Watch uses [YOLOX](https://github.com/Megvii-BaseDetection/YOLOX)-Nano (Apache-2.0) in ONNX form, run locally through OpenCV's `cv2.dnn` — no extra Python package. The weights are never committed; fetch them once into `data/local/models/` (gitignored), which verifies the SHA-256:

```bash
station-watch fetch-model
```

This is the only command that touches the network — `run` never does. A config with `keepout_zones` set but the weights missing or failing the hash refuses to start, naming the file (K9). A box overlapping an active zone for `detect.keepout.persistence_frames` frames raises a `keepout_entry` fault; an inactive zone never faults, and a zone the detector cannot read (marker missing, model error) reads `zone_unknown`, which keeps the station unobservable rather than healthy.

Start the Watchdog in a second terminal, on its own clock and its own alarm rail (K7, K8), so a stalled or dead run is caught even though it shares the Log:

```bash
station-watch watchdog --config config/station-example.yaml --log station.db
```

Run a fault drill to measure time-to-alarm against the real `run` pipeline. Synthetic mode injects camera faults into rendered frames on a schedule and writes a measurement file plus a markdown time-to-alarm table beside it:

```bash
station-watch drill --config config/station-example.yaml --log drill.db --out drill.json --schedule schedule.yaml
```

Live mode runs a real camera while you inject faults physically and type `start`/`clear <fault>` on stdin (for example `start lens_covered`, then `clear lens_covered` once the lens is uncovered). Press Ctrl-D when the drill is over: the run stops at its next cycle and writes the measurement and table. Ctrl-C also stops it and writes what was measured, but exits non-zero because the drill was cut short:

```bash
station-watch drill --config config/station-example.yaml --log drill.db --out drill.json --live --source 0
```

Measure a physics script (for example the fps sweep) over the labeled clip set. With no `--clips`, an absent manifest, or no clips tagged for the script, `measure` writes a `no_input` file — status `no_input`, no metrics key — and exits zero, so a physics table that does not exist yet says so in the repo rather than inventing numbers; real results write the measurement format under `measurements/v1/`:

```bash
station-watch measure fps_sweep --clips clips.yaml --config config/station-example.yaml
```

Open the read-only operator Board, built from the Log. It never writes, and a missing or stale Log reads as UNKNOWN rather than OK. Serve the auto-refreshing screen on loopback, or print the plain-text view once and exit:

```bash
station-watch board --config config/station-example.yaml --log station.db
```

```bash
station-watch board --config config/station-example.yaml --log station.db --once
```

Every `run` keeps a small thumbnail of each frame a flag can cite, under `data/local/evidence` (gitignored) by default. Point it elsewhere with `--evidence-dir`, or turn it off with `--no-evidence`; evidence only observes, so an evidence dir that cannot be written never stops the watch:

```bash
station-watch run --config config/station-example.yaml --source 0 --log station.db --evidence-dir data/local/evidence
```

```bash
station-watch run --config config/station-example.yaml --source clip.mkv --log station.db --no-evidence
```

Before going live, run the preflight. It prints PASS, FAIL, WARN or SKIP per check (config, camera, live frames, fiducial, camera stability, keep-out weights, Log, disk, clock, alarm sinks, Board) and exits 0 only when nothing FAILs. It reads the camera and the Log and writes nothing but a probe file it removes; a camera that will not open is a `camera FAIL` naming the index, never a crash:

```bash
station-watch preflight --config config/station-example.yaml --source 0 --log station.db --board-port 8765
```

Find which device index is your webcam (OpenCV's order differs between machines). It lists each index that opens with its resolution and fps, prints "no cameras opened" when none do, and always exits 0. See [docs/CAMERAS.md](docs/CAMERAS.md) for webcam and DJI Pocket setup:

```bash
station-watch preflight --list-cameras --max-index 5
```

Measure the cold start: launch `run`, time the first frame, first verdict and first healthy verdict, and write a measurement file under the dataset kind's tree. With no healthy verdict before `--timeout-s` it writes `status: no_healthy_verdict` and no numbers:

```bash
station-watch preflight --cold-start --config config/station-example.yaml --source 0 --log cold.db --out measurements/v1/cold_start.json --dataset-kind real
```

The demo-day run-of-show (setup, preflight, each demo beat and how it recovers, backup plans) is [docs/DEMO.md](docs/DEMO.md).

Check one session's quality with QA. It reads the Log read-only, time-weights how much of the session was unobservable, and passes only within the config's `qa:` thresholds (exit 0 pass, 1 fail); a config with no `qa.max_unknown_fraction` refuses to start, naming the key:

```bash
station-watch qa --config config/station-example.yaml --log station.db
```

Audit every flag a session raised. `build` writes `flags.json` and a self-contained proof sheet (`index.html`) under `data/local/audit/<log name>/`, showing the exact frames each flag cites ("no flags in this session" when there are none). `review` serves that sheet on loopback with a correct/incorrect control per flag, and `mark` records one verdict without a browser. The Log is never written:

```bash
station-watch audit build --config config/station-example.yaml --log station.db
```

```bash
station-watch audit review --audit data/local/audit/station
```

```bash
station-watch audit mark --audit data/local/audit/station run-1:missing_part:rail_pos_1:7 correct
```

Score the reviewed flags into reviewed precision, and turn the reviewed false flags into a false-alarm rate per hour of normal operation. A session that fails QA writes `status: qa_failed`; no reviewed flags writes `status: no_reviewed_flags`; too little observed time for a rate writes `status: insufficient_duration`. None of them write numbers:

```bash
station-watch audit score --audit data/local/audit/station --config config/station-example.yaml --log station.db --dataset-kind real --out measurements/v1/precision.json
```

```bash
station-watch audit rate --audit data/local/audit/station --config config/station-example.yaml --log station.db --dataset-kind real --out measurements/v1/false_alarm_rate.json
```

Soak the pipeline for hours. `soak` starts the real `run`, `watchdog` and `board` children against one Log, samples memory, Log growth, Board latency and verdict cadence, and judges growth against the config's `soak:` thresholds plus session QA (exit 0 only on a clean soak). `--synthetic-loop` feeds a looping normal-work source, so every fault it raises is a false flag; too few samples reports `insufficient_samples`, never a pass:

```bash
station-watch soak --config measurements/synthetic/soak-config.yaml --log soak.db --synthetic-loop --hours 2 --dataset-kind synthetic --out measurements/synthetic/soak.json
```

Score the held-out clip set. `--split held_out` first checks that the thresholds were not calibrated on any held-out clip and refuses a leaked manifest; with no manifest it reports "no labeled set present", writes nothing and exits non-zero:

```bash
station-watch evaluate --config config/station-example.yaml --manifest clips/manifest.yaml --split held_out --out measurements/v2
```

## Design principles

These are ideas carried over from patterns run in production, rewritten fresh for a plant floor. No code is imported.

| Id | Principle | On the station |
|---|---|---|
| K1 | Absence is a state, not a null | Three outcomes per station: healthy, fault, unobservable. Blind stations are counted separately and never fold into "passed" |
| K2 | Liveness is proved by the output, read from inside the record | A camera is live when a new frame with a new capture timestamp and different content arrived within N seconds; a connected stream is only a heartbeat |
| K3 | How you learned something caps what you learned | A call from a blurred or occluded frame carries a lower confidence ceiling than one from a fresh, in-focus frame |
| K4 | Infer freely, invent never | Every flag and any explanation cites frame ids and detector outputs; one bad citation drops the explanation; the deterministic alarm still fires |
| K5 | The alarm answers the fault, never the model | Alarm first, explain second; the explainer can never delay or suppress the alarm |
| K7 | Liveness of the reporter, on a clock it does not own | A watchdog on a separate process or hardware timer judges the pipeline's own "cycle completed" record |
| K8 | The alarm may not travel over a watched rail | The stack light or buzzer does not share the camera's network or process |
| K9 | Fail loud at startup | No camera config, no station expectations, or no alarm rail: refuse to start and name the missing piece |
| K10 | Probes never raise, but a crash is not a verdict | "Could not get a frame" is unobservable; "saw a fault, then crashed" still alarms as a fault |
| K11 | Verify a fix only by re-observing | An operator's "cleared" press does not close an incident; the next good cycles on camera do |
| K12 | Rate, not level; active canary | Cycle time creeping up alarms on slope before a stall; a fixed fiducial marker in view is the camera canary |
| K13 | Claims checked against committed measurements | Every number in this README and the demo video lives in a committed measurement file, and a test fails if prose and numbers drift |
| K14 | Tests exercise the shape production produces | At least one test drives real recorded frames through the real capture path, and one drives the real alarm end to end |

**Why the Watchdog exists (finding D1).** An audit of an earlier monitoring system found that its own alarm path was not watched by an independent clock: if the alarm stopped while capture and detection kept running, nothing would notice. Here the Watchdog runs on its own clock with its own alarm rail, and its proving test is exactly that case: stop Alarm while Capture and Detect keep running, and the Watchdog must fire within its window.

## Measured performance

**No real measured numbers exist yet.** The labeled clip set **v1** has not been recorded, so `measurements/v1/` does not exist and nothing below is cited from it. Every number in this section is **synthetic** — produced by the evaluation harness (`station-watch evaluate --synthetic`) over the committed synthetic proving set in `measurements/synthetic/`, not from real footage. These prove the measurement path end to end (K13); they do not describe real-world accuracy. When clip set v1 is recorded, `measurements/v1/*.json` lands and its real numbers replace these, each still carrying its claim marker.

Synthetic proving set (`measurements/synthetic/`, dataset_kind `synthetic`), real Detect pipeline — all numbers below are **synthetic**:

| Flag | Precision | Recall |
|---|---|---|
| Missing part | <!-- claim: measurements/synthetic/detect.json#metrics.missing_part.precision round=2 -->1.00 | <!-- claim: measurements/synthetic/detect.json#metrics.missing_part.recall round=2 -->1.00 |
| Stalled | <!-- claim: measurements/synthetic/detect.json#metrics.stalled.precision round=2 -->1.00 | <!-- claim: measurements/synthetic/detect.json#metrics.stalled.recall round=2 -->1.00 |
| Keep-out entry | <!-- claim: measurements/synthetic/detect.json#metrics.keepout_entry.precision round=2 -->1.00 | <!-- claim: measurements/synthetic/detect.json#metrics.keepout_entry.recall round=2 -->1.00 |

Detection latency over <!-- claim: measurements/synthetic/detect.json#metrics.latency.count -->36 synthetic verdicts has a median of <!-- claim: measurements/synthetic/detect.json#metrics.latency.median_s round=2 -->0.42 s and a p95 of <!-- claim: measurements/synthetic/detect.json#metrics.latency.p95_s round=2 -->0.83 s. The synthetic set is <!-- claim: measurements/synthetic/detect.json#provenance.clips -->7 clips across two sessions.

## Inspiration

Patterns from [deadman](https://github.com/fivedollarfridays/deadman): catch the failure that reports success, and alarm when the thing being watched goes quiet. Liveness proven by fresh captures, never a bare heartbeat, written after a 24-day silent outage that hid behind a green heartbeat.

## License

MIT. Every model and dataset used is checked for a license compatible with a public MIT repo before it is added.
