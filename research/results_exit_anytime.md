# Test 14 — exit at the flip at any hour vs New York session only (NQ hourly bars 2024-07-07 → 2026-11-25, NQ beta 1.28)

| Strategy | Trades in window | Changed by overnight exit | Avg per trade: NY only | Avg: anytime | Won: NY only | Won: anytime | Worst: NY only | Worst: anytime | Days held: NY only | Days held: anytime | Changed trades better with anytime |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Put wall long | 120 | 104 | 0.725% | 0.608% | 73% | 83% | -7.04% | -5.73% | 3.76 | 2.03 | 38% |
| Call wall short | 162 | 140 | 0.344% | 0.306% | 65% | 70% | -4.29% | -4.29% | 2.21 | 0.94 | 47% |
| −10bn strategy (my strategy) | 33 | 23 | 2.045% | 1.708% | 97% | 94% | -6.18% | -4.86% | 8.48 | 6.39 | 26% |

- Put wall long: changed trades avg 0.661% (NY only) vs 0.526% (anytime); anytime better: False
- Call wall short: changed trades avg 0.384% (NY only) vs 0.34% (anytime); anytime better: False
- −10bn strategy (my strategy): changed trades avg 2.15% (NY only) vs 1.666% (anytime); anytime better: False