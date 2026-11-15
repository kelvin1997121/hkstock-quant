# Pre-registered verdict for the simulated account

Written 2026-11-15, committed and pushed before the code that computes the verdict existed.
The rule below does not change after results are seen.

## The account when this was written
Started 2026-11-10 15:02 HKT with HK$100,000 of pretend money, traded by the Claude cloud routine.
On 2026-11-15: equity HK$100,829 (+0.83%), benchmark -1.15%, five open positions, no closed
trade. Six days and five holdings say nothing yet, and this file is written so that the eventual
answer cannot be bent towards whatever the numbers turn out to be.

## When the verdict is taken
At the first update after whichever comes first:
- the **60th Hong Kong trading session** after the start has finished. By the calendar in
  `hkstock/market_days.py` on the day of writing that session is **2027-02-04** (an unscheduled
  closure such as a typhoon does not move the date); or
- the **40th closed trade**.

## What is measured at that moment
- Account return: equity / 100,000 - 1, after every fee, with dividends credited and open
  positions valued at their last close.
- Benchmark return: half Hang Seng, half S&P 500, each from the open of its first session after
  the start, plus the dividends an index fund paid (2800.HK and SPY; US less 30%).
- Closed trades in finished sessions: how many, the average R (net profit or loss divided by the
  risk to the first stop), and the t of R.

## The verdict
- Fewer than **10** closed trades: **樣本唔夠**. No conclusion, and that fact is what is reported.
- **合格** if the account return is above the benchmark return **and** the average R of the closed
  trades is above zero. It is then also stated whether t >= 2.0 (unlikely to be luck) or not (luck
  cannot be ruled out on this sample).
- Otherwise **不合格**, shown on the page as: over this period, trading the way Claude did was not
  better than holding index funds.

The result is frozen in `data/paper_state.json` when taken and stays on the page. Trading may go
on afterwards, but a second period needs its own file.

## Honest notes
- The trading rules were tightened before this file was written (12 Nov: limits must trade
  through, dividends credited, exposure limits; 15 Nov: benchmark with dividends, no finalising a
  day with a missing price, no target exit on the day of an intraday fill, a floor on reward
  against risk after costs). The verdict uses the engine from this commit on.
- A later fix to a calculation error in the engine is allowed, and will be listed below with its
  date and effect. The rule above is not a calculation and is never amended.

## Amendments
(none)
