# Dealer gamma tracker (free)

Tracks dealer gamma exposure for **SPY, QQQ and SPX** every 5 minutes using Cboe's free, delayed
option chains (about 15 minutes behind). Each snapshot adds a row to a history file and redraws:

- **Timeline chart**: price, dealer gamma line, and a put/call "fear" bar per day (like the Marmus chart)
- **Gamma by strike chart**: where gamma sits right now, with gamma flip, call wall and put wall
- **Gamma map**: today's price line drawn over horizontal gamma bars per strike (from open interest and from today's volume), with dots showing where each bar was 5, 15 and 30 minutes earlier
- **TradingView levels**: a ready-made indicator (`docs/<TICKER>_levels.pine`, also on the web page) that draws the flip, walls and biggest gamma strikes on your TradingView chart
- **A web page** (`docs/index.html`) showing both charts plus today's key levels

The history starts empty. After about 2 weeks the bottom panel switches on. After about 150 snapshots
(roughly 6 trading days) an automatic "deep negative" entry level is drawn.

---

## Option A: runs by itself in the cloud, free (recommended)

You need a free GitHub account. No coding needed after setup.

1. **Create a repository.** Go to github.com → **New repository** → name it `gex-tracker`, choose
   **Public**, and click **Create**. Public repos get unlimited free run minutes. Private
   repos also work but have a monthly limit (about 2,000 minutes, which this should fit inside).
2. **Upload the files.** On the new repo page, click **uploading an existing file**, drag in
   *everything inside this folder* (including the hidden `.github` folder: on Mac press
   `Cmd+Shift+.` in Finder to see it), and click **Commit changes**.
   - If the `.github` folder won't upload: click **Add file → Create new file**, type the name
     `.github/workflows/gex.yml`, paste in the contents of that file, and commit.
3. **Let it save results.** Go to **Settings → Actions → General → Workflow permissions**, pick
   **Read and write permissions**, and click **Save**.
4. **Turn on the web page.** Go to **Settings → Pages**. Under *Build and deployment*, choose
   **Deploy from a branch**, branch `main`, folder **/docs**, then **Save**. Your page will be at
   `https://<your-username>.github.io/gex-tracker/`. Bookmark it on your phone.
5. **Test it now.** Go to **Actions → Dealer gamma snapshot → Run workflow**. After about a minute
   you should see a green tick, and the charts appear on your page a minute or two later.

From then on it runs every 5 minutes during US market hours (14:30–21:00 Lisbon time in winter).

Notes:
- GitHub's timer is sometimes a few minutes late, or skips a run when busy. That's normal.
- A red X in the Actions tab means a run failed. The most likely cause is Cboe being down or
  changing its data format. Click the red X to see the message.

## Option B: run on your own computer

Needs Python 3.10 or newer.

```bash
cd gex-tracker
pip install -r requirements.txt
python gex.py --force        # one snapshot now, even if the market is closed
python gex.py --loop 15      # keep it running: a snapshot every 15 minutes
```

Then open `docs/index.html` in your browser. History only builds while the computer is on and the
loop is running.

---

## JP's rule and the retail panic gauge

The tracker follows JP's (financascomjp) SPY rule automatically:

- **Entry**: SPY dealer gamma reaches **-10bn** (`entry_level_bn` in `config.json`).
- **Add** (one, `max_adds`): at a day's close, if gamma is still at or below -10bn, the retail panic gauge is
  at least `add_min_z` (1.0) **and** the close is **below the first entry** (JP added this last condition after 13 Nov).
- **Exit**: gamma back to **0**.

**Retail panic gauge (free stand-in).** JP measures the dollar premium of 1-contract XSP puts bought at the ask,
against the previous 60 days. Free data has no trade sizes or buy/sell side, so the tracker adds up the dollar
premium of *all* XSP puts with 5+ days to expiry traded that day (XSP is the mini-SPX, mostly traded by small
accounts) and compares it with the previous 60 days. It needs at least 10 trading days before it shows a value.

Entries, adds, exits and P&L appear on the timeline chart and in the **"Regra do JP"** panel on `live.html`.

## Seeing the levels on TradingView

TradingView doesn't let outside data flow into charts automatically, so each run writes a small indicator with
that moment's levels:

1. On the web page, open **"TradingView levels for SPY"** and copy the code (or open `docs/SPY_levels.pine` in the repo).
2. In TradingView, open a **SPY** chart (use an **SPX** chart for the SPX levels), click **Pine Editor** at the
   bottom, delete what's there, paste, then **Save** and **Add to chart**.
3. You'll see dashed lines: orange = gamma flip, thick red = put wall, thick green = call wall, thin red/green =
   the other big negative/positive strikes.

The levels are a snapshot, so paste a fresh copy each morning (or when the page shows the walls have moved).

## Settings (`config.json`)

| Setting | What it does |
|---|---|
| `tickers` | Which tickers to track. `["SPY","QQQ","SPX"]` by default. You can add `"NDX"`. |
| `entry_level_bn` | Your "deep negative gamma" line in $bn. Leave it `null` to set it automatically to the lowest 15% of your own history, or type a number such as `-6` once you've watched it for a while. |
| `auto_level_min_rows` | How many snapshots to wait before drawing the automatic line. |

## How the numbers are worked out

- **Net gamma ($bn per 1% move)**: for every option, gamma × open interest × 100 × price² × 1%.
  Calls count as positive and puts as negative (the usual assumption that dealers own the calls
  and are short the puts). It covers all expiries. Roughly: how many dollars of the ETF dealers
  must buy or sell for each 1% move. **Negative** means they trade *with* the move, making it
  bigger; **positive** means they trade *against* it, calming it down.
- **Gamma flip**: the price at which net gamma would cross zero. It's found by re-pricing every
  option's gamma at prices ±15% from today.
- **Call wall / put wall**: the strikes with the most call gamma and the most put gamma.
- **Bottom panel**: a free stand-in for the paid "retail put buying" signal. It shows the
  day's put volume ÷ call volume, as a z-score against the previous 60 days (needs at least 10).
  Gold means the reading is at least 1.
- **Triangles and circles on the price chart**: the simple rule "enter when gamma falls below
  the entry line, exit when it gets back to 0". This is for looking only, not a tested strategy.
  When the entry line is automatic, it's based on your whole history, so older signals use
  hindsight.

## Limits to keep in mind

- Open interest is only updated once a day (overnight), so moves within the day come from price
  and volatility changes, not from new positions.
- The "dealers long calls, short puts" assumption is a simplification and can be wrong at any
  single strike.
- Your levels will not match paid services (SpotGamma, Marmus, etc.). They use different
  products, expiries and assumptions. Judge levels against your own history.
- Cboe's free data is for personal use. It can change format or be switched off without notice.

Not investment advice.
