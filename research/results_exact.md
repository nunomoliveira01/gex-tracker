# Entries and exits at the exact gamma level vs at the close (JP + Put/Call rule, 2022-01-03 → 2026-11-17, no costs)

Exact level: entry at the SPY price where gamma = −10bn that day (or the open on a gap), exit at the flip price (gamma = 0) or the open on a gap. NQ fills = NQ close moved by beta 1.28 × SPY's move from the fill to the close.

| Version | Instrument | Trades | Avg per trade | Won | Return/yr | Volatility | Sharpe | Worst fall | Beta | Alpha/yr | Alpha t |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Close (current backtest) | SPY | 32 | 1.7% | 94% | 9.94% | 11.34% | 0.54 | -16.44% | 0.455 | 2.62% | 0.71 |
| Close (current backtest) | NQ futures | 32 | 2.43% | 91% | 13.35% | 14.49% | 0.66 | -17.49% | 0.559 | 5.28% | 1.08 |
| Exact level | SPY | 43 | 1.62% | 91% | 11.54% | 10.42% | 0.72 | -16.44% | 0.385 | 4.52% | 1.24 |
| Exact level | NQ futures | 43 | 2.28% | 93% | 16.96% | 12.88% | 0.97 | -17.49% | 0.46 | 8.96% | 1.95 |