# Test 9 — NQ entered at SPY's put wall vs at −10bn (2023-11-17 → 2026-11-17, daily, no costs)

Put wall found for 100.0% of days. NQ fills = NQ close moved by 1.28 × SPY's move from the fill to the close.

| Version | Trades | Avg per trade | Won | Avg days | Return/yr | Sharpe | Worst fall | Beta | Alpha/yr | Alpha t | Random p |
|---|---|---|---|---|---|---|---|---|---|---|---|
| P — enter at the put wall | 161 | 0.75% | 79% | 2.4 | 28.2% | 1.54 | -17.2% | 0.692 | 11.53% | 2.23 | 0.0002 |
| P-neg — put wall, only if gamma < 0 | 128 | 0.85% | 79% | 2.6 | 25.1% | 1.37 | -17.2% | 0.686 | 9.15% | 1.78 | 0.0004 |
| P-OI — put wall by open interest | 14 | 1.9% | 79% | 8.0 | 7.73% | 0.32 | -19.55% | 0.527 | -3.71% | -0.7 | 0.0919 |
| Current rule — enter at −10bn | 34 | 2.01% | 97% | 6.2 | 18.66% | 1.01 | -17.49% | 0.633 | 4.58% | 0.85 | 0.0023 |

Rule P: PASS (alpha t ≥ 2 and random p < 0.10). Better than the current rule: yes.