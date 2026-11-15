# Pre-registered record: do the system's buy zone, stop and target make money?

Written 2026-11-15 (a Sunday), committed and pushed **before** the code that records or scores
any plan was run. The first plans are recorded after the close of 2026-11-16. Nothing below
changes after results are seen; a change needs a new file and a count that starts again from zero.

## Why
The page gives every short-term candidate a buy zone, a stop and a target. Whether following
them earns money after costs is not known: the earlier backtest of these levels passed for US
stocks only, on a universe with survivorship bias, and failed for Hong Kong. The simulated
account closes two or three trades a week, which is too few to answer the question this year.
This record follows every candidate's plan forward in time, without buying anything.

## What is recorded
Each trading day, when the after-close analysis of a market is published (session `收市後`, data
as of that day), every stock in that market's short-term candidate lists (`short`, 10 names, and
`tech`, up to 6; a stock in both counts once) is written to `data/plan_log.json` with its plan:
top and bottom of the buy zone (`entry_high`, `entry_low`), `stop`, first target `t1`, the close,
the score and the theme. The first record for a date stands and is never rewritten. A plan with a
missing level, or without stop < entry_high < t1, is recorded as invalid and not traded. GitHub
Actions commits the file the same evening, so the git history shows each plan existed before the
next session opened.

## The hypothetical trade (fixed)
- **Order:** limit buy at `entry_high`, good for the first 3 sessions on which the stock trades
  after the signal date.
- **Fill:** a session that opens at or below `entry_high` fills at the open; otherwise the low
  has to go below `entry_high` and the fill is at `entry_high` (a touch is not a fill). A session
  that opens at or below the stop before any fill voids the plan (no trade).
- **Exit**, checked from the fill day: low at or below the stop sells at min(open, stop) less
  slippage (0.1% HK, 0.05% US). Otherwise the target: an open at or above `t1` sells at the open,
  a high above `t1` sells at `t1`. On the fill day the target counts only if the fill was at the
  open, because otherwise the day's high may have come before the fill. Stop and target in one day
  count as the stop. With neither, the trade is sold at the close of the 10th session, counting
  the fill day as the first, less slippage.
- **Size and costs:** HK$20,000 a trade in Hong Kong, US$2,500 in the US, fractional shares, board
  lots ignored; fees from `hkstock.paper.fee` (Futu's schedule) on both sides. Dividends with an
  ex-date after the fill day up to the exit day are credited (US less 30% withholding). A split
  inside the window drops the record.
- **Net return** = (exit - entry) x shares - fees + dividends, divided by entry x shares, in the
  stock's own currency.

## Comparisons, per trade
1. **Bought without the levels:** the same stock bought at the open of the first session after
   the signal (plus slippage) and sold at the close of the 10th session (less slippage), same size
   and fees.
2. **The index:** Hang Seng or S&P 500 from the open of the fill day to the close of the exit day
   (price index, so it slightly understates the index by a few days of dividends).

## Statistics
Trades from one signal day rise and fall together, so a signal day counts once: its value is the
average over that day's completed trades. Mean and t (mean divided by its standard error) are
taken across signal days.

## Verdict, per market, taken once
At the first update when a market has at least **30 signal days** with a completed trade and at
least **150 completed trades**:
- **合格** only if both hold: the day-average net return is above zero with t >= 2.0, and the
  day-average of (net return - index return) is above zero with t >= 2.0.
- otherwise **不合格**.
The figures at that moment are frozen in the log. Before then the page shows the running numbers
marked 樣本唔夠; they are for information and decide nothing.

Also shown, not part of the verdict: share of plans filled, win rate, results by type of exit,
and the day-average of (plan trade - bought without the levels).

## Limits known in advance
- Daily bars cannot show the order of events inside a day; the rules above take the unfavourable
  reading where it matters (stop before target, no target on an intraday fill day).
- The candidates come from scores that failed their own backtests. This measures the levels as
  used on those candidates, not the scores.
- Thirty signal days is a small sample. A pass is evidence, not proof, and would need a second
  registered period to confirm. A fail on thirty days does not prove the levels are useless either.
- Today's large caps only; Yahoo data can be wrong.
- Nothing here changes the system's scores or levels. A pass or fail is reported, and any change
  to the system would need its own registered test.
