# Pre-registered test: new short-term (5-day) indicators

Written 2026-11-09, committed and pushed **before** any of these indicators was computed or
tested. The git history of this file is the proof. Nothing below is changed after results
are seen; a changed idea is a new test with a new file and new dates.

## Why
The current short score failed its own test (HK IC t 0.57, US t 0.47). It is a trend/momentum
score, so it avoids sectors that are falling (US chips in Nov 2026). These candidates test
different ideas, not more of the same momentum.

## Data
- Universes: `hkstock/universe.py` as of commit time (HK THEMES; US US_THEMES). ETFs and
  extras excluded. Today's names only, so survivorship bias flatters every result.
- Yahoo daily prices, 5 years to the run date, same cleaning as the live system.
- **Development period:** first usable date to **2024-10-31**.
- **Locked period:** **2024-11-01** to the last date with a 5-day forward return.
  None of the candidates below has been computed on any date in either period.

## Candidates (higher value = buy), parameters fixed here
| id | name | definition |
|---|---|---|
| F1 | reversal_5d | minus the 5-day return |
| F2 | pullback_uptrend | if close > 200-day MA: 100 - RSI(14); otherwise ranked lowest |
| F3 | near_52w_high | close / 250-day high - 1 |
| F4 | sector_momentum | equal-weight 20-day return of the stock's theme |
| F5 | high_volume_drift | sum of daily returns over the last 20 days on days whose turnover was >= 2.5x the prior 20-day average (news/earnings drift proxy) |
| F6 | macd_hist | (EMA12 - EMA26 - 9-day EMA of that) / close |
| F7 | bollinger_reversion | minus %B of a 20-day, 2-standard-deviation band |

Reference only (already tested, not a candidate): the current short score.

## Test (same for every candidate, each market separately)
- Every 5th trading day (non-overlapping), rank the universe by the candidate.
- Spearman IC with the next 5-day return; t-stat of the mean IC over dates.
- Top 10 average 5-day return minus the universe average, minus round-trip cost
  (HK 0.3%, US 0.1%).

## Decision rules
1. **Development pass:** IC t >= 2.0 AND top-10 net excess > 0.
2. **Finalists:** at most 2 per market, the development passers with the highest IC t.
3. **Locked period, run once, finalists only:** pass if IC t >= 2.0 AND top-10 net excess > 0.
4. A locked-period pass is shown on the page as its own list ("已驗證"), with its own live
   log; it is **not** merged into the existing short score. Everything else is reported as
   failed and not used.
5. If no candidate passes development, the locked period is not opened at all.

With 7 candidates x 2 markets, about 0.3 false development passes are expected by chance
(one-sided 2.3% each); the single locked-period check is what guards against that.

## Result (appended 2026-11-09 after the development run; rules above unchanged)
No candidate passed development in either market (best: HK F3 near_52w_high IC t 1.23,
US F2 pullback_uptrend t 0.64). Per rule 5 the locked period (2024-11-01 onward) was **not opened**, so it
is still unused for any new indicator. Full numbers: `research/results_2026-11-09.json`.
