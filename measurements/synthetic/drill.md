# Station Watch fault drill — synthetic

- dataset_kind: **synthetic**
- git commit: `ce4682ee1576d92578590352138a6558ce992b60`
- detector: `station_watch.detect:real-pipeline:v1`

Reproduce:

```
station-watch drill --config measurements/synthetic/drill-config.yaml --log /tmp/station-watch-drill.db --out measurements/synthetic/drill.json --schedule measurements/synthetic/drill-schedule.yaml
```

| Fault | Blind reason | Time to alarm (s) | Time to recovery (s) | One alarm | One recovery |
| --- | --- | --- | --- | --- | --- |
| lens_covered | dark | 0.465 | 0.274 | yes | yes |
| frozen | frozen | 0.101 | 0.109 | yes | yes |
| bumped | view_shifted | 0.273 | 0.251 | yes | yes |
| cable_pulled | disconnected | 0.058 | 0.835 | yes | yes |
| lights_off | dark | 0.488 | 0.300 | yes | yes |
