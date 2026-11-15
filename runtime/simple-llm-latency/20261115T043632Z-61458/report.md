# Simple search LLM latency

Simple search scope; factual claims and other agent metrics need separate gold review.

Model attempted: 8/8; HTTP success: 8; valid answers: 2; transport failures: 0; invalid results: 6.
Full response p50/p95 for valid answers: 5.9193765 / 6.345742 s.
Full response p50/p95 for HTTP successes: 3.6408715000000003 / 13.008447 s.
Full response p50/p95 for all cases: 3.6408715000000003 / 13.008447 s.

| Case | Status | LLM calls | Full latency, s |
|---|---|---:|---:|
| ambiguous-original | clarification | 3 | 13.008 |
| ambiguous-short | clarification | 1 | 5.000 |
| status-original | ok | 2 | 5.493 |
| status-short | ok | 2 | 6.346 |
| empty-original | clarification | 1 | 2.281 |
| empty-short | clarification | 1 | 2.280 |
| empty-future-1 | no_data | 1 | 2.272 |
| empty-future-2 | no_data | 1 | 2.020 |
