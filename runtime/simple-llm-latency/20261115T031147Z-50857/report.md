# Simple search LLM latency

Exploratory concurrent run; not an independent SLA measurement.

Classification recomputed from saved traces; no inference replay.

| Outcome | Cases |
|---|---:|
| LLM attempts | 12 |
| HTTP 200 | 7 |
| Valid answers | 5 |
| Transport failures | 5 |
| Invalid results | 2 |

| Group | Full response p50, s | Full response p95, s |
|---|---:|---:|
| Valid answers | 11.751239 | 17.412772 |
| HTTP 200 | 15.241055 | 23.141954 |
| All 12 | 20.277363 | 25.096425 |

| Case | Status | Fallback | LLM calls | Full latency, s |
|---|---|---|---:|---:|
| S01 | clarification | model_error | 1 | 25.096 |
| S02 | ok | — | 1 | 15.241 |
| S03 | clarification | invalid_json | 1 | 17.145 |
| S04 | clarification | model_error | 1 | 25.062 |
| S05 | clarification | model_error | 1 | 25.054 |
| S06 | clarification | invalid_json | 1 | 23.142 |
| S07 | ok | — | 1 | 11.751 |
| S08 | ok | — | 1 | 17.413 |
| S09 | ok | — | 1 | 11.308 |
| S10 | ok | — | 1 | 9.927 |
| S11 | clarification | model_error | 1 | 25.061 |
| S12 | clarification | model_error | 1 | 25.056 |
