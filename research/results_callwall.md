# Test 10 — SHORT NQ at SPY's call wall vs long at the put wall (2023-11-17 → 2026-11-17, daily, no costs)

Call wall found for 100.0% of days. NQ fills = NQ close moved by 1.28 × SPY's move from the fill to the close.

| Version | Trades | Avg per trade | Won | Avg days | Worst trade | Return/yr | Sharpe | Worst fall | Beta | Alpha/yr | Alpha t | Random p |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S — short at the call wall | 207 | 0.41% | 66% | 1.5 | -4.15% | 17.86% | 1.89 | -5.78% | -0.151 | 14.52% | 4.1 | 0.0 |
| S-pos — call wall, only if gamma > 0 | 141 | 0.3% | 66% | 1.8 | -4.15% | 9.81% | 0.94 | -5.78% | -0.123 | 6.98% | 2.33 | 0.002 |
| S-OI — call wall by open interest | 59 | 0.16% | 56% | 2.3 | -3.47% | 4.36% | 0.01 | -3.65% | -0.047 | 0.73% | 0.3 | 0.0874 |
| S-5d — rule S, exit after 5 days at most | 211 | 0.4% | 65% | 1.4 | -3.75% | 18.57% | 2.12 | -3.78% | -0.139 | 14.93% | 4.51 | 0.0 |
| P — long at the put wall (Test 9) | 161 | 0.75% | 79% | 2.4 | -5.73% | 28.2% | 1.54 | -17.2% | 0.692 | 11.53% | 2.23 | 0.0 |

Rule S: PASS (alpha t ≥ 2 and random p < 0.10). Add-ons used: 17. Open now: False.