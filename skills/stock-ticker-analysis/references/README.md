# Stock Ticker Analysis — References

Supplementary reference material for the **stock-ticker-analysis** skill.

## Files

| File | Purpose |
|------|---------|
| `README.md` | This file: data sources, metric definitions, anomaly thresholds and known limits |

The executable lives in `../scripts/analyze_ticker.py`. It uses only the Python standard library; run it with `python3 -I`.

## Data sources

| Source | Endpoint | Used for | Auth |
|--------|----------|----------|------|
| Yahoo Finance chart API | `https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>` (falls back to `query2`) | Daily OHLCV, adjusted close, dividends, exchange/currency/instrument type, `^IRX` risk-free rate | None |
| SEC ticker map | `https://www.sec.gov/files/company_tickers.json` | Ticker → CIK | `User-Agent` with contact |
| SEC submissions | `https://data.sec.gov/submissions/CIK##########.json` | SIC code (sector ETF), filing dates and 8-K item codes | `User-Agent` with contact |
| SEC company facts | `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` | XBRL financial statements (us-gaap and ifrs-full) | `User-Agent` with contact |

- The script only reads. It sends nothing but ticker symbols and the User-Agent string.
- SEC requires a contact in the `User-Agent`. Set `SEC_USER_AGENT="<name> <email>"`; the default is `JbaltzSkills stock-ticker-analysis placeholder@email.com`.
- SEC's fair-access limit is 10 requests/second. The script sleeps 0.12 s between SEC calls and makes 2–3 per ticker.
- Yahoo's endpoint is unofficial and can change or rate-limit without notice. HTTP 429/5xx are retried 3 times with backoff.

## Price handling

- OHLC are scaled by `adjclose / close`, so splits and dividends don't show up as gaps or false returns.
- The snapshot price is the raw (unadjusted) last close; every computed indicator uses adjusted prices.
- During market hours the last bar is intraday and partial.

## Technical indicators

| Indicator | Definition | Reading used in the signal table |
|-----------|------------|-----------------------------------|
| SMA 20/50/200 | Simple moving average of adjusted close | Price above SMA = bullish; SMA50 vs SMA200 (golden/death cross, with last cross date) |
| MACD (12,26,9) | EMA12 − EMA26; signal = EMA9 of MACD; histogram = MACD − signal | Histogram > 0 bullish; crossover in the last 10 sessions is reported |
| RSI(14) | Wilder-smoothed average gain / loss | ≥ 70 overbought (bearish), ≤ 30 oversold (bullish), otherwise above/below 50 |
| Stochastic (14,3) | %K = position of close in the 14-day high/low range; %D = 3-day SMA of %K | %K vs %D, with > 80 / < 20 zones |
| Bollinger (20,2) | SMA20 ± 2 population σ; %B and bandwidth | %B > 1 or < 0 = stretched; bandwidth at a 6-month low = squeeze |
| ATR(14) | Wilder-smoothed true range | Scale for gap and range anomalies |
| ADX(14) | Wilder ADX with +DI / −DI | ≥ 25 trending (direction from DI), < 25 no trend |
| OBV | On-balance volume; 20-session change | Same sign as the 20-day price move = confirmation, opposite = divergence |
| 52-week position | Distance from the 52-week high and low | Within 5 % of the high bullish, more than 20 % below bearish |
| Support / resistance | Swing pivots (± 5 bars) over the last 126 sessions, clustered within 1.5 % | Nearest 3 below / above price, with touch counts |

The signal tally (bullish / bearish / neutral) is a count, not a score. Signals overlap (e.g. both SMA rows measure trend).

## Fundamentals

Values come from SEC XBRL facts. Each metric checks a list of alternative tags (see `TAGS` in the script) because companies tag the same line item differently.

- **TTM (trailing twelve months)** for income and cash-flow items:
  1. The latest fiscal-year value if the latest period is a fiscal year;
  2. otherwise last FY + current YTD − prior-year YTD;
  3. otherwise the sum of 4 consecutive quarters.
- **Balance-sheet items** use the latest point-in-time value; YoY comparisons use the value closest to one year earlier (± 45 days).
- **Shares** = `dei:EntityCommonStockSharesOutstanding`. Multi-class filers often report only one class, so if that's under 70 % of weighted diluted shares the script uses diluted shares instead.
- **Market cap** = raw price × shares. Price multiples are omitted when the reporting currency differs from the price currency.

| Group | Metrics |
|-------|---------|
| Valuation | P/E (TTM diluted EPS), P/S, P/B, EV/EBIT, EV/Sales, FCF yield. EV = market cap + total debt − cash |
| Profitability | Gross, operating, net and FCF margin; ROE and ROA on average equity / assets |
| Growth | Revenue, net income and EPS, TTM vs the TTM one year earlier |
| Health | Debt/equity, net debt, current ratio. Total debt = `LongTermDebt` (or noncurrent + current portions) + short-term borrowings and commercial paper |
| Quality | **Piotroski F-score** (9 tests on the last two fiscal years; untestable items are excluded from the denominator). **Altman Z** (public-manufacturer form: > 2.99 safe, 1.81–2.99 grey, < 1.81 distress; skipped for SIC 6000–6799 financials) |
| History | Last 5 fiscal years: revenue, net income, EPS, FCF, gross and operating margin |

## Comparative benchmarking

- **Benchmarks:** the market (`--market`, default `SPY`), a sector ETF mapped from the SIC code (`SIC_ETF` in the script, overridable with `--sector-etf`), and any `--peers`. A benchmark equal to the target is dropped.
- **Per-symbol stats:** returns over 1W/1M/3M/6M/1Y/YTD, plus 1-year volatility, Sharpe, Sortino and max drawdown. The risk-free rate is the `^IRX` 13-week T-bill yield (4 % if unavailable).
- **Target vs each benchmark:** computed on aligned daily returns.
  - Beta and correlation, over 1 year and 60 days.
  - Jensen's alpha (annualized), tracking error and information ratio.
  - Up capture and down capture.
- **Peer fundamentals table:** each peer's ratios, the peer median, and the target's percentile (the share of peers with a lower value).

### SIC → sector ETF (summary)

| SIC range | ETF | | SIC range | ETF |
|-----------|-----|-|-----------|-----|
| 1310–1389, 2910–2999 | XLE | | 4800–4899, 2700–2799, 7800–7899 | XLC |
| 2830–2836, 3840–3851, 8000–8099 | XLV | | 4900–4999 | XLU |
| 3570–3579, 3600–3699, 7370–7379 | XLK | | 6000–6799 (6500–6553, 6798 → XLRE) | XLF |
| 2000–2199, 2840–2844, 5400–5499 | XLP | | 1000–1499, 2400–2699, 2800–2899, 3000–3399 | XLB |
| 2200–2399, 3711, 3714–3716, 3900–3999, 5200–5999, 7000–7099, 7900–7999 | XLY | | 1500–1799, 3400–3569, 3700–3839, 4000–4799, 8700–8799 | XLI |

SIC is coarse. Override it when the mapping is wrong for the business. For example, GOOGL and META are SIC 7370 (→ XLK) but sit in XLC; AMZN is SIC 5961 (→ XLY), which is right.

## Anomaly detection

Price/volume checks run on each of the last `--anomaly-days` sessions (default 63). Each day is compared with the 60 sessions before it.

| Flag | Rule |
|------|------|
| Return outlier | \|z\| ≥ 3 for the daily return vs the prior 60 days |
| Stock-specific move | \|z\| ≥ 3 for the market-model residual (return − β × market return) |
| Mostly market-wide | Return outlier with residual \|z\| < 2 |
| Volume spike | Volume ≥ 3 × median of the prior 20 sessions |
| Gap up / down | \|open / prior close − 1\| ≥ max(3 %, 2 × ATR %) |
| Range expansion | (high − low) / prior close ≥ 3 × ATR % |

Severity is **high** for \|z\| ≥ 4, volume ≥ 5×, or 3 or more flags; **medium** for 2 flags or \|z\| ≥ 3; otherwise **low**. Each event lists SEC filings dated 0–3 days before it. 8-K items are named, e.g. 2.02 earnings release, 5.02 officer change, 4.02 non-reliance.

**Regime / data checks:**
- 20-day volatility ≥ 1.75× its 1-year level (≤ 0.5× = compression).
- 60-day vs 1-year correlation to the market differs by ≥ 0.3.
- 60-day beta differs from 1-year beta by ≥ 50 %.
- Price 20 % or more below its 52-week high (35 % or more = high severity).
- RSI ≥ 80 or ≤ 20.
- Last bar older than 5 days.
- More than 3 zero-volume sessions in the last 60.

**Fundamental red flags:**
- Accruals: (net income − operating cash flow) / average assets above 10 %.
- Positive net income with negative FCF.
- Receivables or inventory growing more than 15 percentage points faster than revenue.
- Shares outstanding up more than 5 % YoY.
- Gross margin moved 5 points or more YoY.
- Negative equity.
- Altman Z in the distress zone.
- Piotroski 2 or below (with at least 7 tests scored).
- Late filer: the latest 10-K/10-Q is over 200 days old, or the latest 20-F/40-F over 400.
- XBRL lag: EDGAR's financial data is older than the latest periodic filing.

## Known limits

- **Non-SEC listings, ETFs and indexes** get technicals, benchmarking and price anomalies only; fundamentals are reported as unavailable.
- **ADRs (20-F/40-F filers):** EDGAR reports ordinary shares, not ADRs (e.g. 1 TSM ADR = 5 shares), so the script omits market cap and every price multiple for these filers. Margins, growth and balance-sheet ratios are still reported; EPS is per ordinary share.
- **Financials (SIC 6000–6799):** FCF yield, FCF margin and current ratio are blanked, Altman Z is skipped, and the accrual, profit-without-cash and weak-Piotroski flags are suppressed. Use P/B, ROE and net margin. Bank revenue prefers `RevenuesNetOfInterestExpense`, which banks tag in both 10-K and 10-Q.
- **TTM lag:** if a company tags revenue differently between its 10-K and its 10-Q, TTM income figures can end before the balance-sheet date. The report flags this as "TTM lag".
- **XBRL coverage** sometimes lags a filing that has already been made (the report flags this as "XBRL lag").
- **Not investment advice.** The output describes data; it doesn't recommend trades.

## Conventions

- Reference files use Markdown, lowercase-hyphenated names.
- Thresholds in this file mirror the constants in `analyze_ticker.py`; change both together.
