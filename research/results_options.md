# Test 13 — call-wall short with SPY puts vs shorting NQ (real bid/ask, 1-minute quotes)

Signal days with option data: 415 (SPY reached the wall in the minute data on 395); random days: 195.

| Version | Days | Trades | Avg return on premium | Median | Won | Worst | Avg P&L per $ of exposure | NQ short same days | 1% premium per trade: return/yr | Worst run | Positive years 2019–24 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A — 0DTE (or ≤2-day), out at 15:55 — signal | 0.1 | 415 | 40.6% | -36.4% | 39% | -99.4% | 0.218% | 0.346% | 21.45% | -13.3% | 6 of 6 |
| A — 0DTE (or ≤2-day), out at 15:55 — random | 0.2 | 195 | -15.1% | -53.2% | 32% | -99.6% | -0.084% | – | -5.45% | -27.8% | 2 of 6 |
| B1 — 2–3 day, out at 15:55 — signal | 2.2 | 415 | 18.7% | -0.6% | 49% | -83.5% | 0.224% | 0.346% | 10.02% | -4.2% | 6 of 6 |
| B1 — 2–3 day, out at 15:55 — random | 2.2 | 195 | -2.7% | -9.5% | 40% | -78.8% | -0.046% | – | -0.99% | -6.2% | 3 of 6 |
| B2 — 2–3 day, out 9:45 next day — signal | 2.2 | 414 | 18.7% | -14.6% | 43% | -95.6% | 0.248% | 0.342% | 9.87% | -7.8% | 5 of 6 |
| B2 — 2–3 day, out 9:45 next day — random | 2.2 | 194 | -9.8% | -28.5% | 29% | -94.9% | -0.15% | – | -3.52% | -18.1% | 1 of 6 |

By year, average return on premium (signal days):

- A — 0DTE (or ≤2-day), out at 15:55: 2018: +48.0%, 2019: +45.1%, 2020: +9.6%, 2021: +25.5%, 2022: +40.8%, 2023: +10.1%, 2024: +8.5%, 2025: +120.9%, 2026: +37.2%
- B1 — 2–3 day, out at 15:55: 2018: +26.6%, 2019: +18.3%, 2020: +3.0%, 2021: +9.5%, 2022: +28.0%, 2023: +8.1%, 2024: +9.6%, 2025: +40.7%, 2026: +16.2%
- B2 — 2–3 day, out 9:45 next day: 2018: +27.5%, 2019: +0.2%, 2020: +7.1%, 2021: +14.9%, 2022: +40.8%, 2023: -5.5%, 2024: +9.6%, 2025: +38.1%, 2026: +18.3%

Pass (an option version beats the NQ short per $ of exposure after spreads, positive and above random days, ≥4 of 6 positive years): {"A \u2014 0DTE (or \u22642-day), out at 15:55": false, "B1 \u2014 2\u20133 day, out at 15:55": false, "B2 \u2014 2\u20133 day, out 9:45 next day": false}