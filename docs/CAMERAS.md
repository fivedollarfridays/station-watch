# Cameras

`station-watch` watches one assembly station through one camera. The `run` (and
`preflight`) commands take `--source`, which is **either a device index** (a bare
integer, e.g. `0`) **or a path** to a recorded clip or image sequence. This page is
about live cameras: how to find the right device index, how to set up a generic UVC
webcam, and how to use a DJI Pocket 3 as a USB webcam with its gimbal locked.

## Finding the right device index

OpenCV's device-index order is **not stable across machines** — index `0` is not
guaranteed to be your webcam, and the numbering can change when you plug or unplug
other USB video devices. So there is no index to hard-code; you discover it:

```
station-watch preflight --list-cameras
```

This tries device indexes `0` through `5` and, for each one that opens, prints the
index, the resolution and fps it reports, and whether a frame actually read. An index
that does not open is simply skipped — that is expected, because most indexes on any
machine are empty. Widen the search with `--max-index`:

```
station-watch preflight --list-cameras --max-index 8
```

Pick the index whose reported resolution and "frame: read" match your camera, then use
it as `--source`:

```
station-watch run --config config/station.yaml --source 0 --log data/station.db
```

Before going live, run the full preflight on that index — it opens the camera, confirms
live frames, finds the fiducial marker, and checks that the marker is not drifting:

```
station-watch preflight --config config/station.yaml --source 0 --log data/station.db
```

The `camera_stability` check samples the fiducial center for a few seconds (tune the
window with `--drift-s`) and WARNs if the center moves more than half of the configured
`fiducial.tolerance_px`:

```
station-watch preflight --config config/station.yaml --source 2 --log data/station.db --drift-s 10
```

## A generic UVC webcam

Most USB webcams are UVC (USB Video Class) devices and need no driver on Linux or
macOS. To set one up:

1. Plug the webcam into a USB port directly (avoid unpowered hubs for 1080p streams).
2. Mount it so the station and the fiducial marker are fully in frame and the marker is
   near its configured `expected_center_px`.
3. Find its device index with `station-watch preflight --list-cameras` (above).
4. Lock focus and exposure if the camera's own utility allows it — autofocus hunting
   and auto-exposure swings look like scene changes to the blind-condition watch.
5. Run `station-watch preflight --config config/station.yaml --source <index> --log data/station.db`
   and confirm every check is PASS (or a WARN you understand) before going live.

A fixed webcam on a rigid mount does not drift, so `camera_stability` should PASS. If it
WARNs, the mount is moving — re-seat it.

## The DJI Pocket 3 as a USB webcam

The DJI Pocket 3 can act as a plain UVC webcam over USB-C, which lets `station-watch`
use it through the same `--source <index>` path as any other webcam. The catch is the
**gimbal**: the Pocket 3 actively stabilizes, which means the framing can drift slowly
even on a static mount, and that drift raises `view_shifted`. For a fixed station camera
you want the gimbal **locked**, not stabilizing.

Set the optional config key so preflight reminds you:

```yaml
camera:
  gimbal: true
```

With `camera.gimbal: true`, the `camera_stability` check always reports **WARN** —
"a gimbal can drift and raise view_shifted; lock it before going live" — as a standing
reminder to lock it before a production run.

The exact on-device menu names for webcam mode and gimbal lock are **not yet confirmed
on the hardware**, so the device-level steps below are marked `unverified`. Do not
follow invented menu paths; confirm each against the current firmware and DJI's own
documentation before relying on it.

### On the Pocket 3 (device steps — unverified)

1. Connect the Pocket 3 with a USB-C data cable and select its USB / webcam mode so it enumerates as a UVC device — exact menu name **unverified**.
2. Lock the gimbal so it holds a fixed orientation instead of re-centering on movement — exact control / menu name **unverified**.
3. Confirm the host sees it with `preflight --list-cameras`, which should list a new index matching the Pocket 3's webcam output — step **unverified** until checked on hardware.
4. Mount it rigidly, set `camera.gimbal: true` in the config, and run the full `preflight` on that index — step **unverified** until checked on hardware.

## Troubleshooting

- **`--list-cameras` shows nothing.** No index in the range opened. Widen `--max-index`,
  check the cable and port, and make sure no other application holds the camera.
- **An index opens but `frame: no frame`.** The device enumerated but did not deliver a
  frame — often a camera still held by another process, or a mode it has not started.
- **`camera_stability` WARNs on a fixed webcam.** The mount is moving; re-seat it. On a
  Pocket 3, lock the gimbal and expect the standing gimbal WARN when `camera.gimbal` is
  true.
