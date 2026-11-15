# Simple search LLM latency

Simple search scope; factual claims and other agent metrics need separate gold review.

Model attempted: 8/8; HTTP success: 8; valid answers: 2; transport failures: 0; invalid results: 6.
Full response p50/p95 for valid answers: 4.6480205 / 4.992644 s.
Full response p50/p95 for HTTP successes: 2.6936005 / 12.34604 s.
Full response p50/p95 for all cases: 2.6936005 / 12.34604 s.

| Case | Status | LLM calls | Full latency, s |
|---|---|---:|---:|
| ambiguous-original | clarification | 3 | 12.346 |
| ambiguous-short | clarification | 1 | 2.129 |
| status-original | ok | 2 | 4.303 |
| status-short | ok | 2 | 4.993 |
| empty-original | clarification | 1 | 1.863 |
| empty-short | clarification | 1 | 2.301 |
| empty-future-1 | clarification | 1 | 2.191 |
| empty-future-2 | clarification | 1 | 3.086 |
