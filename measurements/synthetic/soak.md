# Station Watch soak — synthetic

- dataset_kind: **synthetic**
- status: **pass**
- git commit: `397dede548b5d679a33a3997e49993c0f273c3ea`
- detector: `station_watch.detect:real-pipeline:v1`

A pass covers memory and Log growth, Board latency, verdict cadence and false flags over the run; it does not measure detection accuracy (that is `station-watch evaluate`).

Reproduce:

```
station-watch soak --config measurements/synthetic/soak-config.yaml --log /tmp/sw-soak-final.db --out measurements/synthetic/soak.json --dataset-kind synthetic --synthetic-loop --minutes 10.0 --sample-s 20.0 --board-port 8795
```

| Metric | Value |
| --- | --- |
| RSS growth board (MB/h) | 6.71 |
| RSS growth run (MB/h) | 44.28 |
| RSS growth watchdog (MB/h) | 1.27 |
| Log growth (MB/h) | 16.29 |
| Board render p95 (s) | 0.026 |
| Board render failures | 0 |
| Max verdict gap (s) | 20.14 |
| Verdicts / minute | 59.41 |
| False flags | 0 |
| Duration (s) | 600.0 |
