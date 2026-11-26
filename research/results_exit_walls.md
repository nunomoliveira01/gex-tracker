# Test 12 — exit at the opposite wall vs the gamma flip (NQ, no add-on, daily, no costs)

## Out of sample: 2018-11-19 → 2023-07-31

| Strategy | Exit | Trades | EV per trade | Won | Avg days | Longest | Worst trade | Return/yr | Sharpe | Worst fall | Beta | Alpha/yr | Alpha t | Random p |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Put wall long | Flip (current) | 204 | 0.62% | 72% | 2.6 | 48 | -15.19% | 31.83% | 1.32 | -22.95% | 0.768 | 17.72% | 2.86 | 0.0082 |
| Put wall long | Opposite wall | 191 | 0.92% | 73% | 2.9 | 18 | -7.52% | 45.98% | 1.96 | -20.94% | 0.606 | 29.76% | 4.51 | 0.0 |
| Put wall long | Wall or flip, first | 252 | 0.66% | 71% | 1.6 | 10 | -7.04% | 43.17% | 2.06 | -14.88% | 0.502 | 28.88% | 4.6 | 0.0 |
| Put wall long | Wall, 10-day limit | 194 | 0.93% | 73% | 2.9 | 10 | -8.55% | 47.15% | 2.01 | -19.71% | 0.61 | 30.5% | 4.66 | 0.0 |
| Call wall short | Flip (current) | 210 | 0.35% | 61% | 2.7 | 30 | -7.01% | 17.42% | 1.05 | -16.11% | -0.362 | 20.58% | 3.55 | 0.0022 |
| Call wall short | Opposite wall | 190 | 0.42% | 58% | 3.2 | 13 | -10.17% | 17.64% | 0.91 | -13.3% | -0.505 | 23.23% | 3.6 | 0.0017 |
| Call wall short | Wall or flip, first | 248 | 0.36% | 60% | 1.8 | 11 | -6.43% | 21.66% | 1.38 | -12.38% | -0.325 | 23.46% | 4.31 | 0.0003 |
| Call wall short | Wall, 10-day limit | 193 | 0.4% | 58% | 3.1 | 10 | -10.17% | 17.05% | 0.89 | -15.36% | -0.505 | 22.73% | 3.52 | 0.0022 |

## Original period: 2023-11-17 → 2026-11-17

| Strategy | Exit | Trades | EV per trade | Won | Avg days | Longest | Worst trade | Return/yr | Sharpe | Worst fall | Beta | Alpha/yr | Alpha t | Random p |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Put wall long | Flip (current) | 161 | 0.71% | 77% | 2.4 | 44 | -7.04% | 50.58% | 2.24 | -18.39% | 0.846 | 25.89% | 4.13 | 0.0006 |
| Put wall long | Opposite wall | 171 | 0.96% | 77% | 2.1 | 12 | -4.96% | 76.86% | 3.74 | -11.2% | 0.536 | 46.09% | 6.75 | 0.0 |
| Put wall long | Wall or flip, first | 202 | 0.77% | 75% | 1.4 | 7 | -4.96% | 72.01% | 3.75 | -11.2% | 0.477 | 44.04% | 6.66 | 0.0 |
| Put wall long | Wall, 10-day limit | 172 | 0.95% | 76% | 2.1 | 10 | -4.96% | 76.52% | 3.73 | -11.2% | 0.536 | 45.9% | 6.73 | 0.0 |
| Call wall short | Flip (current) | 207 | 0.35% | 65% | 1.5 | 17 | -4.29% | 32.64% | 2.12 | -6.99% | -0.268 | 28.49% | 4.53 | 0.0 |
| Call wall short | Opposite wall | 172 | 0.58% | 65% | 2.2 | 11 | -5.19% | 43.88% | 2.01 | -11.84% | -0.748 | 44.29% | 6.39 | 0.0 |
| Call wall short | Wall or flip, first | 229 | 0.37% | 64% | 1.2 | 7 | -3.89% | 37.95% | 2.48 | -5.5% | -0.277 | 32.54% | 5.25 | 0.0 |
| Call wall short | Wall, 10-day limit | 172 | 0.58% | 65% | 2.2 | 10 | -5.19% | 43.26% | 1.99 | -11.84% | -0.746 | 43.83% | 6.32 | 0.0 |

## Verdicts (must beat the flip on Sharpe AND alpha t in both periods)

- P|wall: beats the flip
- P|first: beats the flip
- P|wall10: beats the flip
- S|wall: worse: keep the flip
- S|first: beats the flip
- S|wall10: worse: keep the flip