# ThetaData tests — results (2018-11-19 to 2026-11-17, 2008 trading days)

Rules fixed before seeing the data: see claude/thetadata-teste-pre-registo.md. Returns are SPY points in %, no costs.

## Test 1 — fixed −10bn: FAIL

| Rule | Trades | Mean | Median | Wins | Worst during trade | Days held |
|---|---|---|---|---|---|---|
| JP rule | 33 | 1.11% | 1.39% | 85.0% | -16.87% | 12.3 |
| VIX twin (VIX ≥ 31.0) | 33 | 0.82% | 1.3% | 70.0% | -25.06% | 4.7 |
| Drop twin (4.5% below 20d high) | 35 | 0.45% | 1.24% | 74.0% | -30.85% | 10.1 |

Random entries doing as well: 0.2896 · JP entries within 3 days of a VIX-twin entry: 3.0%, of a drop-twin entry: 12.0%

| Period | JP mean (n) | VIX twin (n) | Drop twin (n) | JP best? |
|---|---|---|---|---|
| 2018–21 | – (0) | 0.17% (24) | 0.43% (12) | – |
| 2022–23 | 1.66% (8) | 1.89% (6) | 0.34% (15) | False |
| 2024–26 | 0.94% (25) | 3.89% (3) | 0.66% (8) | False |

## Test 3 — gamma below its own 5th percentile of the last 12 months: FAIL

| Rule | Trades | Mean | Median | Wins | Worst during trade | Days held |
|---|---|---|---|---|---|---|
| JP rule | 53 | 0.68% | 1.08% | 75.0% | -16.45% | 10.0 |
| VIX twin (VIX ≥ 28.0) | 56 | 0.45% | 0.74% | 70.0% | -25.06% | 3.9 |
| Drop twin (3.5% below 20d high) | 53 | 0.63% | 1.22% | 74.0% | -30.85% | 8.6 |

Random entries doing as well: 0.4493 · JP entries within 3 days of a VIX-twin entry: 8.0%, of a drop-twin entry: 32.0%

| Period | JP mean (n) | VIX twin (n) | Drop twin (n) | JP best? |
|---|---|---|---|---|
| 2018–21 | 0.92% (25) | 0.19% (36) | 0.76% (20) | True |
| 2022–23 | 0.09% (13) | 0.51% (14) | 0.03% (19) | False |
| 2024–26 | 0.78% (15) | 1.85% (6) | 1.25% (14) | False |

## Test 2 — retail panic add-on: FAIL

| Filter | Add-ons | Add-on mean | Days firing | SPY +1d | +3d | +5d |
|---|---|---|---|---|---|---|
| JP exact (1-lot XSP puts at ask) | 17 | 1.46% | 329 | 0.049% | 0.229% | 0.18% |
| Free proxy (all XSP put premium) | 14 | 1.14% | 267 | 0.198% | 0.238% | 0.403% |
| VIX z ≥ 1 | 14 | 1.32% | 372 | 0.075% | 0.143% | 0.295% |
| SPY put/call z ≥ 1 | 15 | 1.93% | 284 | 0.123% | 0.446% | 0.573% |
| No filter | 22 | 1.68% | 2008 | 0.057% | 0.173% | 0.29% |
| Any day (baseline) | – | – | all | 0.057% | 0.173% | 0.29% |

JP exact best overall: False · best by period: {'2018–21': False, '2022–23': False, '2024–26': False}
