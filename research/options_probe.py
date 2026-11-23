"""Which ThetaData endpoints answer for the options test (Test 13). Prints row counts and column names only."""
import time
import theta_fetch as tf

ds = "20251015"
tests = [
    ("SPY stock 1m ohlc", "stock/history/ohlc", dict(symbol="SPY", date=ds, interval="1m")),
    ("SPY stock 1m quote", "stock/history/quote", dict(symbol="SPY", date=ds, interval="1m")),
    ("SPY option 1m quote (0DTE put 660)", "option/history/quote", dict(symbol="SPY", expiration=ds, strike="660.000", right="put", date=ds, interval="1m")),
    ("SPY option 1m quote all strikes 0DTE", "option/history/quote", dict(symbol="SPY", expiration=ds, date=ds, interval="1m")),
    ("SPY greeks 1m (underlying price)", "option/history/greeks/first_order", dict(symbol="SPY", expiration=ds, strike="660.000", right="put", date=ds, interval="1m")),
    ("SPY option 1m quote 2019", "option/history/quote", dict(symbol="SPY", expiration="20190118", strike="260.000", right="put", date="20190115", interval="1m")),
    ("SPY list expirations", "option/list/expirations", dict(symbol="SPY")),
]
for name, path, kw in tests:
    t0 = time.time()
    try:
        df = tf.get(path, **kw)
        print(f"{name}: {len(df)} rows, {time.time() - t0:.1f}s, columns={list(df.columns)[:14]}")
    except Exception as e:
        print(f"{name}: FAILED {str(e)[:200]}")
