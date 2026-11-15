# Simple search LLM latency

Main benchmark last trace: 2026-11-15T03:17:23.076805+00:00; this run started 2026-11-15T03:17:36+00:00. Twelve selected searches are not the full section 9 SLA sample.

Model attempted: 12/12; HTTP success: 12; valid answers: 10; transport failures: 0; invalid results: 2.
Full response p50/p95 for valid answers: 5.859521 / 7.923683 s.
Full response p50/p95 for HTTP successes: 5.69245 / 7.923683 s.
Full response p50/p95 for all cases: 5.69245 / 7.923683 s.

| Case | Status | LLM calls | Full latency, s |
|---|---|---:|---:|
| S01 | ok | 1 | 2.778 |
| S02 | ok | 1 | 5.997 |
| S03 | clarification | 1 | 5.138 |
| S04 | ok | 1 | 7.390 |
| S05 | ok | 1 | 5.932 |
| S06 | clarification | 1 | 4.665 |
| S07 | ok | 1 | 5.787 |
| S08 | ok | 1 | 5.598 |
| S09 | ok | 1 | 4.709 |
| S10 | ok | 1 | 7.924 |
| S11 | ok | 1 | 4.054 |
| S12 | ok | 1 | 7.788 |
