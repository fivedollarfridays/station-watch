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

The example config watches the camera's own health: frames arriving, not dark, not frozen, and the fiducial marker in place. Its marker position matches the synthetic test clip, so for a real camera put the printed marker in view and set `fiducial.expected_center_px` to where it appears; until then the "fiducial missing" alarm stays up, which is the canary doing its job. Parts slots stay empty until Detect lands in HF2. A recorded file ends the run when it has been read to the end; a live camera never does, so an unplugged camera raises the "disconnected" alarm and the run keeps retrying it until you stop it (Ctrl-C or SIGTERM).

Detect runs automatically when the config lists rail positions or zones under `detect`: Capture reads those targets from the same frames and appends the observations to the Log for the Judge, so a missing or unseated part becomes a fault. The example config lists none, so it stays camera-health only. `--observations` is fixture input for drills and is refused alongside a `detect` config with targets — one source of observations per run.

Start the Watchdog in a second terminal, on its own clock and its own alarm rail (K7, K8), so a stalled or dead run is caught even though it shares the Log:

```bash
station-watch watchdog --config config/station-example.yaml --log station.db
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

## Inspiration

Patterns from [deadman](https://github.com/fivedollarfridays/deadman): catch the failure that reports success, and alarm when the thing being watched goes quiet. Liveness proven by fresh captures, never a bare heartbeat, written after a 24-day silent outage that hid behind a green heartbeat.

## License

MIT. Every model and dataset used is checked for a license compatible with a public MIT repo before it is added.
