# FX test 2 — all six currencies together (GBP, JPY, CHF, AUD, CAD, MXN), equal weight — Rule C: FAIL

Currencies with a positive average per trade under Rule C: 3 of 6.

| Currency | C trades | C avg | C Sharpe | C alpha t | C random p | Crowd no pullback avg | Trend + pullback avg | Carry source |
|---|---|---|---|---|---|---|---|---|
| GBP | 34 | 0.92% | 0.24 | 1.41 | 0.1191 | 0.17% | 0.05% | UK rate: Bank of England Bank Rate. |
| JPY | 32 | 0.67% | 0.13 | 0.56 | 0.152 | 0.18% | 0.66% | JPY rate: IMF M.JP.FIMM_PA (DBnomics). |
| CHF | 34 | 0.2% | 0.03 | 0.12 | 0.408 | -0.14% | -0.03% | CHF rate: IMF M.CH.FPOLM_PA (DBnomics). |
| AUD | 26 | -0.14% | -0.01 | -0.06 | 0.6115 | -0.02% | 0.49% | AUD rate: IMF M.AU.FIMM_PA (DBnomics). |
| CAD | 28 | -0.83% | -0.28 | -1.37 | 0.9278 | -0.68% | 0.3% | CAD rate: IMF M.CA.FIMM_PA (DBnomics). |
| MXN | 28 | -0.32% | -0.04 | -0.18 | 0.6469 | -0.7% | 0.02% | MXN rate: IMF M.MX.FIMM_PA (DBnomics). |

| Pooled rule | Trades | Avg per trade | Won | Return/yr | Volatility | Sharpe | Worst fall | Alpha/yr | Alpha t | Random p |
|---|---|---|---|---|---|---|---|---|---|---|
| C — crowd + 1 std pullback | 182 | 0.13% | 46% | 0.18% | 2.49% | 0.08 | -12.27% | 0.33% | 0.72 | 0.4186 |
| D — C + add-on | 182 | 0.37% | 50% | 0.12% | 1.98% | 0.07 | -11.0% | 0.24% | 0.64 | – |
| Crowd, no pullback | 233 | -0.19% | 45% | -0.33% | 3.19% | -0.09 | -18.44% | -0.06% | -0.11 | – |
| Trend + pullback, no CFTC | 772 | 0.23% | 45% | 1.13% | 4.54% | 0.27 | -14.89% | 1.39% | 2.58 | – |

| Period | C pooled avg (trades) | Crowd no pullback | Trend + pullback |
|---|---|---|---|
| 2004–2010 | 0.89% (56) | 0.05% (80) | 0.4% (189) |
| 2011–2018 | -0.07% (64) | -0.1% (78) | 0.13% (291) |
| 2019–2026 | -0.35% (62) | -0.53% (75) | 0.22% (292) |