# Pre-registered test: earnings-surprise drift and analyst revisions

Written 2026-11-09, committed and pushed **before** either signal was computed or compared
with returns. Only the data format was inspected (column names, timestamp time zones, how far
back the records go). Nothing below changes after results are seen.

## Why
Price/volume indicators showed no 5-day edge (PREREG_2026-11-09_short_factors.md). These two
use information that is not in past prices: what companies reported versus forecasts, and what
analysts did afterwards. Both are documented effects, usually weaker in large caps.

## Data
- Universes: HK `THEMES` and US `US_THEMES` in `hkstock/universe.py` at commit time (today's
  names; survivorship bias flatters results).
- Prices: Yahoo daily, adjusted, full history, used from 2013-06-01.
- Earnings: Yahoo `get_earnings_dates(limit=60)` (EPS estimate, reported EPS, Surprise %).
- Analyst actions (US only): Yahoo `upgrades_downgrades`.
- **Development period:** 2014-01-01 to **2021-10-31** (never used by any earlier test).
- **Locked period:** **2021-11-01** to the last date with a 20-day forward return.

## Event timing (no look-ahead)
An earnings report time-stamped on local exchange date D (US: New York time; HK: Hong Kong
time) is usable from the **close of the first trading day after D**. An analyst action dated D
is usable from the close of the first trading day after D.

## Signals (higher = buy), fixed here
| id | market | definition |
|---|---|---|
| E1 | HK, US | Surprise % of the latest report usable within the last 40 trading days, clipped to [-50, 50]. Only stocks with such a report are ranked on that date. |
| E2 | US | Over the last 30 calendar days of analyst actions: each action scores +1 if Action = up, -1 if Action = down, otherwise +1 if the price target was raised, -1 if lowered, else 0. E2 = sum / number of actions; 0 if no actions. All stocks ranked. |

## Test
- Horizon: next **20 trading days**. Sample every 5th trading day; t-stats are divided by 2 for
  the 4x overlap.
- Spearman IC between the signal and the 20-day return among the ranked stocks (at least 8 on
  a date, otherwise the date is skipped); t-stat of the mean IC.
- Top quintile of the ranked stocks: average 20-day return minus the whole universe's average,
  minus one round trip (HK 0.3%, US 0.1%).

## Decision rules
1. **Development pass:** adjusted IC t >= 2.0 AND top-quintile net excess > 0.
2. Every development pass (at most 3: E1-HK, E1-US, E2-US) goes to the locked period, **run once**,
   same pass rule.
3. A locked pass is shown on the page as its own list with its own live log, not merged into
   the existing scores. Anything else is reported as failed and not used.
4. If nothing passes development, the locked period is not opened.
