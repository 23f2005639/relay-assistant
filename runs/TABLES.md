### Table 2a
| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |
|---|---|---:|---:|---:|---|---|
| overload-short | stand-in | 17 | 112 | 9.1 | 1560 / 15634 / 15634 | 1575 / 15648 / 15648 |
| overload-noshare | stand-in | 77 | 462 | 10.0 | 8000 / 19222 / 22171 | 8015 / 19238 / 22182 |
| overload | stand-in | 77 | 462 | 10.0 | 4425 / 19652 / 24040 | 4442 / 19670 / 24054 |
| halved | stand-in | 17 | 468 | 2.2 | 968 / 6278 / 6278 | 981 / 6292 / 6292 |
| retry-naive | stand-in | 34 | 341 | 6.0 | 830 / 11003 / 11155 | 842 / 11018 / 11167 |
| retry-none | stand-in | 34 | 341 | 6.0 | 1103 / 15337 / 15906 | 1116 / 15337 / 15920 |
| retry-budgeted | stand-in | 34 | 341 | 6.0 | 1520 / 22347 / none | 1534 / 22362 / none |
| harness-freeze-check | stand-in | 22 | 104 | 12.7 | 1310 / 10701 / 12780 | 1321 / 10714 / 12795 |
| steady-short | stand-in | 9 | 155 | 3.5 | 913 / 2155 / 2155 | 927 / 2167 / 2167 |
| steady | stand-in | 31 | 567 | 3.3 | 1062 / 3206 / 5069 | 1076 / 3223 / 5083 |
| daily | stand-in | 46 | 571 | 4.8 | 10 / 1748 / 3641 | 10 / 1756 / 3655 |
| burst-short | stand-in | 40 | 19 | 125.0 | 4 / 16556 / 23187 | 4 / 16569 / 23200 |
| burst | stand-in | 45 | 288 | 9.4 | 4 / 16366 / 22379 | 4 / 16380 / 22393 |
| steady-short | real | 9 | 155 | 3.5 | 599 / 6386 / 6386 | 640 / 6472 / 6472 |
| burst-short | real | 40 | 19 | 125.0 | 5 / 10552 / 20686 | 5 / 10624 / 20686 |
| overload-short | real | 17 | 112 | 9.1 | 753 / 16135 / 16135 | 830 / 16232 / 16232 |
| daily-exhausted | real | 10 | 52 | 11.4 | 7 / 137 / 137 | 7 / 137 / 137 |

### Table 2b
| Scenario | Succeeded (%) | Rate-limited (%) | Timed out or given up (%) | Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 answers |
|---|---:|---:|---:|---:|---:|---:|---:|
| overload-short (stand-in) | 88.2 | 11.8 | 0.0 | 0.00 | 2 | 1605 | $0.16 |
| overload-noshare (stand-in) | 63.6 | 36.4 | 0.0 | 0.00 | 3 | 1446 | $0.15 |
| overload (stand-in) | 77.9 | 22.1 | 0.0 | 0.00 | 3 | 1471 | $0.15 |
| halved (stand-in) | 94.1 | 5.9 | 0.0 | 0.00 | 1 | 2358 | $0.22 |
| retry-naive (stand-in) | 61.8 | 38.2 | 0.0 | 1.59 | 1 | 2161 | $0.20 |
| retry-none (stand-in) | 79.4 | 2.9 | 0.0 | 0.00 | 2 | 1917 | $0.19 |
| retry-budgeted (stand-in) | 79.4 | 17.6 | 2.9 | 0.15 | 2 | 1702 | $0.17 |
| harness-freeze-check (stand-in) | 100.0 | 0.0 | 0.0 | 0.00 | 1 | 2091 | $0.20 |
| steady-short (stand-in) | 100.0 | 0.0 | 0.0 | 0.00 | 1 | 2322 | $0.22 |
| steady (stand-in) | 100.0 | 0.0 | 0.0 | 0.00 | 1 | 2268 | $0.21 |
| daily (stand-in) | 43.5 | 56.5 | 0.0 | 0.00 | 1 | 2140 | $0.20 |
| burst-short (stand-in) | 27.5 | 72.5 | 0.0 | 0.00 | 3 | 1627 | $0.16 |
| burst (stand-in) | 35.6 | 64.4 | 0.0 | 0.00 | 3 | 1838 | $0.18 |
| steady-short (real) | 100.0 | 0.0 | 0.0 | 0.11 | 1 | 2341 | $0.21 |
| burst-short (real) | 25.0 | 75.0 | 0.0 | 0.00 | 3 | 1531 | $0.14 |
| overload-short (real) | 88.2 | 11.8 | 0.0 | 0.00 | 2 | 1610 | $0.15 |
| daily-exhausted (real) | 0.0 | 100.0 | 0.0 | 0.00 | 1 | 0 | $0.00 |

What people received, and how long the first token took for each kind (not a required table):
| Scenario | Model answer: n, first token p50 / p95 (ms) | Shared answer: n | Quoted passages: n, p50 (ms) | Refused: n | Nothing shown: n |
|---|---|---:|---|---:|---:|
| overload-short (stand-in) | 15, 1633 / 15634 | 0 | 2, 5 | 0 | 0 |
| overload-noshare (stand-in) | 49, 13600 / 19410 | 0 | 28, 4 | 0 | 0 |
| overload (stand-in) | 48, 11589 / 20270 | 12 | 17, 4 | 0 | 0 |
| halved (stand-in) | 14, 997 / 6278 | 2 | 1, 5 | 0 | 0 |
| retry-naive (stand-in) | 19, 1302 / 11155 | 2 | 13, 232 | 0 | 0 |
| retry-none (stand-in) | 22, 1103 / 10912 | 5 | 7, 12529 | 0 | 0 |
| retry-budgeted (stand-in) | 23, 1795 / 18010 | 4 | 6, 4 | 0 | 1 |
| harness-freeze-check (stand-in) | 22, 1310 / 10701 | 0 | 0, not measured | 0 | 0 |
| steady-short (stand-in) | 9, 913 / 2155 | 0 | 0, not measured | 0 | 0 |
| steady (stand-in) | 26, 1275 / 3206 | 5 | 0, not measured | 0 | 0 |
| daily (stand-in) | 13, 1186 / 3641 | 7 | 26, 9 | 0 | 0 |
| burst-short (stand-in) | 8, 2603 / 23187 | 3 | 29, 4 | 0 | 0 |
| burst (stand-in) | 12, 1429 / 22379 | 4 | 29, 3 | 0 | 0 |
| steady-short (real) | 8, 599 / 6386 | 0 | 1, 438 | 0 | 0 |
| burst-short (real) | 5, 688 / 10552 | 3 | 30, 4 | 2 | 0 |
| overload-short (real) | 11, 748 / 16135 | 0 | 2, 5 | 4 | 0 |
| daily-exhausted (real) | 0, not measured / not measured | 0 | 10, 7 | 0 | 0 |

### Table 3
| Setting | Questions asked | Correct and cited (%) | Wrong (%) | Correctly refused (%) | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |
|---|---:|---:|---:|---:|---:|---|---|
| medium-300w-k4-rerank | 15 | 60.0 | 6.7 | 26.7 | 2223 | 638 / 4918 | 720 / 5011 |
| small-150w-k8-norerank | 15 | 60.0 | 6.7 | 26.7 | 2982 | 784 / 1050 | 868 / 1073 |
| large-600w-k2-norerank | 15 | 46.7 | 13.3 | 26.7 | 1777 | 682 / 1253 | 750 / 1289 |

Times exclude time queued for token budget and time spent retrying (first_token_ms - wait_ms - retry_ms). The share not shown in a row was refused although the papers do answer (grade refused_wrongly).
