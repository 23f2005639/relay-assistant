### Table 2a
| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |
|---|---|---:|---:|---:|---|---|
| steady | stand-in | 36 | 580 | 3.7 | 1132 / 4003 / 7264 | 1145 / 4018 / 7279 |
| burst | stand-in | 53 | 277 | 11.5 | 5 / 12670 / 22083 | 5 / 12683 / 22098 |
| overload | stand-in | 73 | 462 | 9.5 | 1102 / 18370 / 25297 | 1115 / 18397 / 25313 |
| halved | stand-in | 27 | 451 | 3.6 | 1127 / 7273 / 12577 | 1142 / 7286 / 12591 |
| daily | stand-in | 42 | 562 | 4.5 | 15 / 2343 / 17128 | 15 / 2360 / 17156 |
| overload-noshare | stand-in | 73 | 462 | 9.5 | 3262 / 19193 / 24027 | 3276 / 19207 / 24042 |
| retry-none | stand-in | 42 | 337 | 7.5 | 2198 / none / none | 2211 / none / none |
| retry-budgeted | stand-in | 42 | 337 | 7.5 | 1763 / 18520 / none | 1779 / 18533 / none |
| retry-naive | stand-in | 42 | 337 | 7.5 | 232 / 10256 / 11217 | 232 / 10256 / 11230 |
| harness-freeze-check | stand-in | 32 | 115 | 16.7 | 1149 / 12348 / 12394 | 1163 / 12361 / 12404 |
| steady-short | stand-in | 16 | 162 | 5.9 | 904 / 7253 / 7253 | 923 / 7267 / 7267 |
| steady-short | real | 16 | 162 | 5.9 | 494 / 6272 / 6272 | 558 / 6305 / 6305 |
| burst-short | stand-in | 44 | 77 | 34.1 | 4 / 11422 / 21302 | 4 / 11422 / 21316 |
| burst-short | real | 44 | 77 | 34.1 | 5 / 15525 / 20123 | 5 / 15525 / 20169 |
| overload-short | stand-in | 20 | 90 | 13.3 | 1785 / 18406 / 21471 | 1800 / 18419 / 21489 |
| overload-short | real | 20 | 90 | 13.3 | 514 / 8217 / 15774 | 514 / 8299 / 15774 |
| burst-provider-unreachable | real | 44 | 77 | 34.1 | 5 / 648 / 658 | 5 / 648 / 658 |
| overload-provider-unreachable | real | 20 | 90 | 13.3 | 12 / 651 / 725 | 12 / 651 / 725 |

### Table 2b
| Scenario | Succeeded (%) | Rate-limited (%) | Timed out or given up (%) | Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 answers |
|---|---:|---:|---:|---:|---:|---:|---:|
| steady (stand-in) | 100.0 | 0.0 | 0.0 | 0.00 | 1 | 2259 | $0.21 |
| burst (stand-in) | 49.1 | 50.9 | 0.0 | 0.00 | 3 | 2074 | $0.20 |
| overload (stand-in) | 79.5 | 20.5 | 0.0 | 0.00 | 3 | 1588 | $0.16 |
| halved (stand-in) | 92.6 | 7.4 | 0.0 | 0.00 | 1 | 2258 | $0.21 |
| daily (stand-in) | 47.6 | 52.4 | 0.0 | 0.00 | 2 | 2249 | $0.21 |
| overload-noshare (stand-in) | 64.4 | 35.6 | 0.0 | 0.00 | 3 | 1491 | $0.15 |
| retry-none (stand-in) | 69.0 | 16.7 | 9.5 | 0.00 | 2 | 1859 | $0.18 |
| retry-budgeted (stand-in) | 71.4 | 23.8 | 2.4 | 0.12 | 2 | 1832 | $0.18 |
| retry-naive (stand-in) | 54.8 | 45.2 | 0.0 | 1.86 | 1 | 2313 | $0.22 |
| harness-freeze-check (stand-in) | 100.0 | 0.0 | 0.0 | 0.00 | 1 | 2109 | $0.20 |
| steady-short (stand-in) | 100.0 | 0.0 | 0.0 | 0.00 | 1 | 2328 | $0.22 |
| steady-short (real) | 100.0 | 0.0 | 0.0 | 0.06 | 1 | 2229 | $0.20 |
| burst-short (stand-in) | 38.6 | 61.4 | 0.0 | 0.00 | 2 | 1869 | $0.18 |
| burst-short (real) | 38.6 | 61.4 | 0.0 | 0.00 | 3 | 1858 | $0.17 |
| overload-short (stand-in) | 85.0 | 15.0 | 0.0 | 0.00 | 2 | 1728 | $0.17 |
| overload-short (real) | 80.0 | 20.0 | 0.0 | 0.05 | 2 | 1734 | $0.16 |
| burst-provider-unreachable (real) | 0.0 | 77.3 | 0.0 | 0.16 | 1 | 0 | $0.00 |
| overload-provider-unreachable (real) | 0.0 | 60.0 | 0.0 | 0.35 | 1 | 0 | $0.00 |

What people received, and how long the first token took for each kind (not a required table):
| Scenario | Model answer: n, first token p50 / p95 (ms) | Shared answer: n | Quoted passages: n, p50 (ms) | Refused: n | Nothing shown: n |
|---|---|---:|---|---:|---:|
| steady (stand-in) | 29, 1408 / 4003 | 7 | 0, not measured | 0 | 0 |
| burst (stand-in) | 18, 1716 / 22083 | 8 | 27, 4 | 0 | 0 |
| overload (stand-in) | 44, 6170 / 18402 | 14 | 15, 4 | 0 | 0 |
| halved (stand-in) | 19, 1465 / 12577 | 6 | 2, 3 | 0 | 0 |
| daily (stand-in) | 13, 1327 / 17128 | 7 | 22, 9 | 0 | 0 |
| overload-noshare (stand-in) | 47, 10153 / 19378 | 0 | 26, 4 | 0 | 0 |
| retry-none (stand-in) | 24, 2950 / 17440 | 5 | 9, 4 | 0 | 4 |
| retry-budgeted (stand-in) | 23, 2958 / 18520 | 7 | 11, 4 | 0 | 1 |
| retry-naive (stand-in) | 18, 1222 / 11217 | 5 | 19, 230 | 0 | 0 |
| harness-freeze-check (stand-in) | 32, 1149 / 12348 | 0 | 0, not measured | 0 | 0 |
| steady-short (stand-in) | 12, 1215 / 7253 | 4 | 0, not measured | 0 | 0 |
| steady-short (real) | 9, 682 / 6272 | 4 | 0, not measured | 3 | 0 |
| burst-short (stand-in) | 10, 2200 / 21302 | 7 | 27, 4 | 0 | 0 |
| burst-short (real) | 8, 794 / 20123 | 7 | 27, 4 | 2 | 0 |
| overload-short (stand-in) | 12, 8117 / 21471 | 5 | 3, 4 | 0 | 0 |
| overload-short (real) | 8, 747 / 8217 | 5 | 4, 3 | 3 | 0 |
| burst-provider-unreachable (real) | 0, not measured / not measured | 3 | 41, 5 | 0 | 0 |
| overload-provider-unreachable (real) | 0, not measured / not measured | 1 | 19, 12 | 0 | 0 |

### Table 3
| Setting | Questions asked | Correct and cited (%) | Wrong (%) | Correctly refused (%) | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |
|---|---:|---:|---:|---:|---:|---|---|
| medium-300w-k4-rerank | 15 | 60.0 | 6.7 | 26.7 | 2214 | 1027 / 4789 | 1234 / 5007 |
| small-150w-k8-norerank | 15 | 60.0 | 13.3 | 26.7 | 2995 | 1655 / 3566 | 1788 / 3566 |
| large-600w-k2-norerank | 15 | 53.3 | 6.7 | 26.7 | 1773 | 809 / 1549 | 812 / 1929 |

Times exclude time queued for token budget and time spent retrying (first_token_ms - wait_ms - retry_ms). The share not shown in a row was refused although the papers do answer (grade refused_wrongly).

Chosen setting on every question written (not a required table):
| Setting | Questions asked | Correct and cited (%) | Wrong (%) | Correctly refused (%) | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |
|---|---:|---:|---:|---:|---:|---|---|
| medium-300w-k4-rerank | 25 | 64.0 | 12.0 | 20.0 | 2224 | 793 / 3210 | 916 / 3212 |
