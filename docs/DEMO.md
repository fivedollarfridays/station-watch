# Demo day run-of-show

This is the script for showing Station Watch live: what to set up, the go/no-go check,
each demo beat with what to trigger, what to say and how it recovers, and what to do
when the power, the network or the camera lets you down.

Two rules hold for every beat:

- **Recovery is the next good cycles on camera, not a button (K11).** Put the part back,
  take your hand out, uncover the lens, and the station clears itself once enough good
  frames and healthy verdicts have arrived (`recover_good_frames` and
  `recover_healthy_verdicts` in the config). Nobody touches the keyboard to clear a flag.
- **Unobservable is never healthy (K1).** Whenever the camera cannot see, the Board says
  UNKNOWN or STALE, never OK. That is a feature to show, not a failure to hide.

The bench config is written once the physical bench exists. Below it is called
`config/station.yaml`; the Log and every other runtime file live under `data/local/`,
which is gitignored.

## Setup checklist

Do this the evening before and again on arrival.

1. **Bench.** The DIN rail panel on a stable table, the printed fiducial marker fixed in
   place, every rail part seated, the torque stripes painted, the labels on.
2. **Lamp.** The bench lamp on and aimed so the marker and the rail are evenly lit, with
   no glare spot on the rail parts. Room lights can change; the lamp should dominate.
3. **Camera.** On a rigid mount over the bench, focus and exposure locked if the camera
   allows it. Find its index with `station-watch preflight --list-cameras` (see
   [CAMERAS.md](CAMERAS.md)). A gimbal camera (the DJI Pocket in CAMERAS.md) runs with its gimbal locked and
   `camera.gimbal: true` in the config.
4. **Edge box.** The machine that runs Station Watch, with the package installed, the
   keep-out model already fetched (`station-watch fetch-model`, done at home: the venue
   network is not needed or trusted), and the clock set.
5. **Board screen.** A second display or a browser window the audience can see, showing
   the Board on loopback.
6. **Power strip.** Edge box, lamp, Board screen and camera hub on one strip you can
   reach, so a restart follows the order under [Backup: power](#backup-power).
7. **Spare camera.** A second UVC webcam in the bag, already tried with
   `--list-cameras` on this edge box.

## Preflight

Run the go/no-go check on the real camera, with the Board already running:

```
station-watch preflight --config config/station.yaml --source 0 --log data/local/demo.db --board-port 8765
```

Go only on `PREFLIGHT PASS`. Each FAIL means:

- **config**: the config did not load; the line names the missing or bad dotted key.
- **camera**: the source did not open; the line names the device index. Re-run
  `--list-cameras`, re-seat the cable, close any other app holding the camera.
- **frames_live**: frames are not arriving, or they are identical (a frozen or dead
  sensor). Replug the camera.
- **fiducial**: the marker was not found, or sits off its configured position. Clear the
  view or re-seat the marker.
- **camera_stability**: a WARN, not a FAIL, when the marker drifts or a gimbal is
  configured; lock the mount or the gimbal. It FAILs only when the camera never opened.
- **model_weights**: keep-out zones are configured but the weights are missing or fail
  their hash. Run `station-watch fetch-model` (needs the network, so do it at home).
- **log_writable**: the Log's directory is missing or not writable. Create it or fix
  its permissions.
- **disk_space**: too little free space at the Log or evidence directory.
- **clock**: the wall clock is behind the Log's newest row, or the year is implausible.
  Set the clock. (NTP sync is not checked.)
- **alarm_sinks**: an alarm sink did not build, or the alarm sound file is missing.
- **board**: nothing answered on the Board port. Start the Board first.

## Cold start

From launching `run` to the first healthy verdict on the Board takes
<!-- claim: measurements/synthetic/cold_start.json#metrics.launch_to_first_healthy_s round=1 -->1.1
seconds. **This is a synthetic number** (a generated clip on a development machine), not
the edge box on the bench; the real figure replaces it once measured on the bench with:

```
station-watch preflight --cold-start --config config/station.yaml --source 0 --log data/local/cold.db --out measurements/v1/cold_start.json --dataset-kind real
```

Start the watch and the Board in two terminals:

```
station-watch run --config config/station.yaml --source 0 --log data/local/demo.db
```

```
station-watch board --config config/station.yaml --log data/local/demo.db
```

## Demo beats

Each beat names what to trigger, what to say, and how it recovers.

### Beat: normal cycle

**Trigger:** Nothing. Work the station normally: pick, place, move on.

**Say:** "This is the station watching itself. The Board shows the station state and the
frame age, how old the newest camera frame is. If the frames stop, that age climbs and
the station goes STALE. It never shows a stale picture as OK."

**Recovers:** Nothing to recover. Point at the frame age ticking over each cycle.

### Beat: a part left off

**Trigger:** Pull one rail part (for example the one at `rail_pos_1`) and leave the slot
empty.

**Say:** "A required part is missing. Once the camera has seen the empty slot for a few
frames in a row, the Board raises `missing_part` on that position and cites the exact
frames it saw."

**Recovers:** Put the part back. After the configured run of healthy verdicts the flag
clears by itself.

### Beat: a missing torque stripe

**Trigger:** Swap in a part with no torque stripe painted, with
`rail_pos_1.torque_stripe` listed in `required_slots`.

**Say:** "The part is there, but the paint mark that proves it was torqued is not. That is
its own flag: `missing_part` on `rail_pos_1.torque_stripe`, and only that one, because
the part itself is present."

**Recovers:** Swap the striped part back in; the flag clears after the healthy verdicts.

### Beat: a pulled label

**Trigger:** Peel the heat-shrink label off the wire at `rail_pos_1`, with
`rail_pos_1.label` listed in `required_slots`.

**Say:** "Same idea for the label at the termination: a missing label is a flag on
`rail_pos_1.label`."

**Recovers:** Put a labeled wire back; the flag clears after the healthy verdicts.

### Beat: work stopped

**Trigger:** Stop working and step back, hands off the bench, until the stall threshold
passes. `run` prints the threshold at startup ("stall threshold ... s").

**Say:** "Nobody is working the station. Once nothing has moved for longer than the stall
threshold, which comes from the measured step times or from takt plus grace, the Board
raises `stalled`."

**Recovers:** Start working again; motion returns and the stall clears after the healthy
verdicts.

### Beat: a hand in the keep-out zone

**Trigger:** Reach a hand into the marked keep-out zone and hold it there.

**Say:** "That zone is off limits while the station runs. The person detector runs
locally on this box, nothing goes to the network, and a hand held in the zone raises
`keepout_entry`."

**Recovers:** Take the hand out; the zone reads clear and the flag clears after the
healthy verdicts.

### Beat: the lens covered

**Trigger:** Cover the camera lens with a hand or a card. This is the `lens_covered`
fault from the drill, done for real.

**Say:** "Now the camera is blind. The whole station goes UNKNOWN, with the blind reason
on the Board, and the alarm fires. A blind camera is never read as a healthy station:
we cannot see, so we say so."

**Recovers:** Uncover the lens. After enough good frames the blind reason clears, then
the healthy verdicts bring the station back.

## Backup plans

### Backup: power

If the power drops, restore it in this order: power strip, edge box, lamp, camera, Board
screen. Then start `run`, then the Board, then re-run the preflight. The
station is back to healthy about the cold-start time after `run` launches (see
[Cold start](#cold-start)). The Log is append-only, so nothing recorded before the drop
is lost.

### Backup: wifi

Nothing in the run path uses the network: `run`, the Board, the preflight and the audit
all work offline, and the Board is served on loopback only. The one command that needs
the network is `station-watch fetch-model`, which must have been run before leaving
home. If the venue wifi is down, carry on.

### Backup: camera

In order:

1. **The spare UVC webcam.** Plug it in, find its index, and restart `run` on it:
   `station-watch preflight --list-cameras`, then
   `station-watch run --config config/station.yaml --source 1 --log data/local/demo.db`
   with the index it reported.
2. **A recorded clip.** Play a bench recording from `data/local/` as the source:
   `station-watch run --config config/station.yaml --source data/local/bench-clip.mkv --log data/local/demo-clip.db`.
   Say plainly that this is a recording.
3. **A synthetic drill, announced as synthetic.** Run the fault drill on rendered frames,
   and tell the audience these are generated frames, not the bench:
   `station-watch drill --config config/station.yaml --log data/local/drill.db --out data/local/drill.json --schedule measurements/synthetic/drill-schedule.yaml`.
   Its schedule injects `fault: lens_covered`, `fault: frozen`, `fault: bumped`,
   `fault: cable_pulled` and `fault: lights_off` in turn.

With a live camera, the drill can also take faults typed by hand
(`station-watch drill --config config/station.yaml --log data/local/drill.db --out data/local/drill.json --live --source 0`),
for example `start lens_covered`, then `clear lens_covered` once the lens is uncovered.

## After the demo

Review every flag the demo raised, so each one is marked right or wrong by a person:

```
station-watch audit build --config config/station.yaml --log data/local/demo.db
```

```
station-watch audit review --audit data/local/audit/demo
```

Then score the reviewed flags:

```
station-watch audit score --audit data/local/audit/demo --config config/station.yaml --log data/local/demo.db --dataset-kind real --out measurements/v1/demo-precision.json
```
