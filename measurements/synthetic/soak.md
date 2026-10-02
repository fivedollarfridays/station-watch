# Station Watch soak — synthetic

- dataset_kind: **synthetic**
- status: **pass**
- git commit: `657aaa45cc3d12b8feaf89d57b280c2da80e22bd`
- detector: `station_watch.detect:real-pipeline:v1`

A pass covers memory and Log growth, Board latency, verdict cadence and false flags over the run; it does not measure detection accuracy (that is `station-watch evaluate`).

Reproduce:

```
station-watch soak --config measurements/synthetic/soak-config.yaml --log data/local/soak.db --out measurements/synthetic/soak.json --dataset-kind synthetic --synthetic-loop --minutes 10.0 --sample-s 20.0 --board-port 8795
```

| Metric | Value |
| --- | --- |
| RSS growth board (MB/h) | 6.78 |
| RSS growth run (MB/h) | 51.17 |
| RSS growth watchdog (MB/h) | 1.32 |
| Log growth (MB/h) | 16.47 |
| Board render p95 (s) | 0.026 |
| Board render failures | 0 |
| Max verdict gap (s) | 20.25 |
| Verdicts / minute | 59.39 |
| False flags | 0 |
| Duration (s) | 600.0 |
