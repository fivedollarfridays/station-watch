# Station Watch fault drill — synthetic

- dataset_kind: **synthetic**
- git commit: `657aaa45cc3d12b8feaf89d57b280c2da80e22bd`
- detector: `station_watch.detect:real-pipeline:v1`

Reproduce:

```
station-watch drill --config measurements/synthetic/drill-config.yaml --log data/local/drill.db --out measurements/synthetic/drill.json --schedule measurements/synthetic/drill-schedule.yaml
```

| Fault | Blind reason | Time to alarm (s) | Time to recovery (s) | One alarm | One recovery |
| --- | --- | --- | --- | --- | --- |
| lens_covered | dark | 0.300 | 0.200 | yes | yes |
| frozen | frozen | 0.200 | 0.150 | yes | yes |
| bumped | view_shifted | 0.200 | 0.200 | yes | yes |
| cable_pulled | disconnected | 0.034 | 0.750 | yes | yes |
| lights_off | dark | 0.300 | 0.150 | yes | yes |
