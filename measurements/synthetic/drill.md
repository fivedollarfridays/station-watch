# Station Watch fault drill — synthetic

- dataset_kind: **synthetic**
- git commit: `c382d7e8b78148830d4eb93757475e0903af255e`
- detector: `station_watch.detect:real-pipeline:v1`

Reproduce:

```
station-watch drill --config measurements/synthetic/drill-config.yaml --log /tmp/station-watch-drill.db --out measurements/synthetic/drill.json --schedule measurements/synthetic/drill-schedule.yaml
```

| Fault | Blind reason | Time to alarm (s) | Time to recovery (s) | One alarm | One recovery |
| --- | --- | --- | --- | --- | --- |
| lens_covered | dark | 0.300 | 0.200 | yes | yes |
| frozen | frozen | 0.150 | 0.150 | yes | yes |
| bumped | view_shifted | 0.200 | 0.150 | yes | yes |
| cable_pulled | disconnected | 0.038 | 0.850 | yes | yes |
| lights_off | dark | 0.300 | 0.150 | yes | yes |
