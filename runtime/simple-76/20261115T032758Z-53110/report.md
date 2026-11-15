# Simple search LLM latency

Simple search scope; factual claims and other agent metrics need separate gold review.

Model attempted: 76/76; HTTP success: 76; valid answers: 71; transport failures: 0; invalid results: 5.
Full response p50/p95 for valid answers: 8.426175 / 24.715398 s.
Full response p50/p95 for HTTP successes: 8.3468745 / 24.715398 s.
Full response p50/p95 for all cases: 8.3468745 / 24.715398 s.

| Case | Status | LLM calls | Full latency, s |
|---|---|---:|---:|
| sp01 | ok | 1 | 3.699 |
| sp02 | ok | 1 | 3.371 |
| sp03 | ok | 1 | 1.982 |
| sp04 | clarification | 1 | 2.228 |
| sp05 | clarification | 1 | 2.670 |
| sp06 | ok | 1 | 1.701 |
| sp07 | ok | 1 | 1.682 |
| sp08 | ok | 1 | 3.144 |
| sp09 | ok | 1 | 1.949 |
| sp10 | ok | 1 | 2.686 |
| sp11 | ok | 1 | 2.959 |
| sp12 | clarification | 3 | 19.748 |
| sp13 | ok | 1 | 3.608 |
| sp14 | ok | 1 | 5.052 |
| sp15 | ok | 1 | 2.646 |
| sp16 | ok | 1 | 3.705 |
| sp17 | ok | 1 | 4.177 |
| sp18 | ok | 1 | 3.756 |
| sp19 | ok | 3 | 16.264 |
| sp20 | ok | 2 | 8.268 |
| sp21 | ok | 2 | 9.994 |
| sp22 | ok | 2 | 8.462 |
| sp23 | ok | 3 | 16.341 |
| sp24 | ok | 2 | 12.485 |
| sp25 | ok | 3 | 16.912 |
| sp26 | ok | 3 | 29.924 |
| sp27 | ok | 2 | 7.523 |
| sp28 | ok | 2 | 10.231 |
| sp29 | ok | 3 | 18.344 |
| sp30 | ok | 3 | 48.096 |
| sp31 | ok | 3 | 15.482 |
| sp32 | ok | 3 | 24.715 |
| sp33 | ok | 2 | 12.988 |
| sp34 | ok | 3 | 19.497 |
| sp35 | ok | 3 | 17.786 |
| sp36 | ok | 3 | 24.056 |
| sp37 | ok | 1 | 7.301 |
| sp38 | clarification | 1 | 7.725 |
| sp39 | ok | 1 | 9.469 |
| sp40 | ok | 1 | 9.923 |
| sp41 | ok | 1 | 9.632 |
| sp42 | ok | 1 | 7.940 |
| sp43 | ok | 1 | 8.702 |
| sp44 | ok | 1 | 9.554 |
| sp45 | ok | 1 | 7.527 |
| sp46 | ok | 1 | 9.054 |
| sp47 | ok | 1 | 8.190 |
| sp48 | ok | 3 | 66.585 |
| sp49 | ok | 1 | 8.951 |
| sp50 | ok | 1 | 11.388 |
| sp51 | ok | 1 | 9.058 |
| sp52 | ok | 1 | 7.758 |
| sp53 | ok | 1 | 9.391 |
| sp54 | ok | 1 | 7.406 |
| sp55 | clarification | 1 | 15.722 |
| sp56 | ok | 1 | 10.242 |
| sp57 | ok | 1 | 4.807 |
| sp58 | ok | 1 | 7.389 |
| sp59 | ok | 1 | 8.233 |
| sp60 | ok | 1 | 10.503 |
| sp61 | ok | 1 | 13.700 |
| sp62 | ok | 1 | 15.746 |
| sp63 | ok | 1 | 9.108 |
| sp64 | ok | 1 | 8.460 |
| sp65 | ok | 1 | 8.426 |
| sp66 | ok | 1 | 6.237 |
| sh01 | ok | 1 | 6.980 |
| sh02 | ok | 1 | 7.428 |
| sh03 | ok | 1 | 9.015 |
| sh04 | ok | 1 | 6.604 |
| sh05 | ok | 1 | 12.797 |
| sh06 | ok | 1 | 6.659 |
| sh07 | ok | 1 | 5.571 |
| sh08 | ok | 1 | 7.757 |
| sh09 | ok | 1 | 6.346 |
| sh10 | ok | 1 | 5.374 |

Separate phrasing family generated from the same seed catalog; no manual external authorship.

## Primary and holdout

| Set | n | Valid technical responses | Tool accuracy | Constraint satisfaction | Full p50, s | Full p95, s |
|---|---:|---:|---:|---:|---:|---:|
| primary | 66 | 61/66 | 89.4% | 83.3% | 8.582 | 24.715 |
| holdout | 10 | 10/10 | 100.0% | 100.0% | 6.819 | 12.797 |
| worst | — | 92.4% | 89.4% | 83.3% | — | 24.715 |

## Level 3, simple-search scope

- latency_p95_le_8s: FAIL
- tool_accuracy_ge_85pct: PASS
- constraint_satisfaction_ge_80pct: PASS
- Other level 3 metrics: UNVERIFIED
