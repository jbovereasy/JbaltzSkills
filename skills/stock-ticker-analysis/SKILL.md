---
name: stock-ticker-analysis
description: Analyzes a stock ticker with technical analysis (moving averages, MACD, RSI, Bollinger, ADX, OBV, support/resistance), fundamental analysis from SEC EDGAR filings (valuation, margins, growth, balance-sheet health, Piotroski F-score, Altman Z), comparative benchmarking against the market, a sector ETF and peers (returns, beta, alpha, Sharpe, capture ratios, peer valuation percentiles), and anomaly detection (return/volume outliers and gaps matched to SEC filings, volatility and correlation regime shifts, accounting red flags). Activates when someone asks to analyze, evaluate, review or compare a stock or ticker symbol, asks how a stock is doing versus the market or its peers, or asks whether a stock has had unusual moves. Informational only, never investment advice.
version: 1.0.0
tags: [stocks, finance, technical-analysis, fundamental-analysis, benchmarking, anomaly-detection]
dependencies:
  cli:
    - python3
---

# Stock Ticker Analysis

## Overview

One script, [scripts/analyze_ticker.py](scripts/analyze_ticker.py), pulls about 2 years of daily prices from Yahoo Finance and the company's filings data from SEC EDGAR. It then writes a Markdown (or JSON) report with four sections: technical, fundamental, comparative benchmarking and anomalies. You run the script, check the numbers, and write a short narrative on top.

The script is deterministic and uses only the standard library. Its formulas, thresholds and data sources are documented in [references/README.md](references/README.md). Don't recompute indicators by hand. If a number looks wrong, check the data notes and known limits before trusting or discarding it.

## Usage

Use this skill when someone:
- Asks to analyze, evaluate or review a stock or ticker (e.g. "analyze NVDA", "what does AAPL look like?")
- Wants a stock compared with the market, its sector or named competitors
- Asks whether a stock had unusual price or volume moves, or what drove a recent drop or spike
- Wants valuation, profitability or balance-sheet health for a US-listed company

Don't use it to place trades, give buy/sell/hold recommendations, set price targets, or size positions.

## Prerequisites

- `python3` 3.9 or later. Always run the script with `-I`.
- Outbound HTTPS to `query1.finance.yahoo.com` / `query2.finance.yahoo.com`, `www.sec.gov` and `data.sec.gov`. The script only reads. It sends the ticker symbols and a User-Agent string, nothing else.
- `SEC_USER_AGENT` (optional): SEC asks for a contact in the User-Agent, e.g. `export SEC_USER_AGENT="Jane Doe jane@example.com"`. Without it the script sends `JbaltzSkills stock-ticker-analysis placeholder@email.com`.

## Instructions

### 1. Resolve the ticker and benchmarks

1. Get the ticker from the user. If they give a company name, use the ticker you're confident of. If you're unsure (several share classes, a foreign listing), ask. Use Yahoo symbol format: `BRK-B`, `7203.T`, `SHOP.TO`.
2. **Peers:** use any the user names. Otherwise choose 3–5 listed competitors with a similar business and, ideally, similar size, and tell the user which you picked. Use US-listed peers when you want peer fundamentals, since those come from EDGAR.
3. **Sector ETF:** the script maps the SEC SIC code to a SPDR sector ETF. Override it with `--sector-etf` when SIC misclassifies the business, e.g. GOOGL and META → `XLC`, and payment networks such as V or MA → `XLF`.
4. **Market:** keep `SPY` for US stocks. For non-US listings, pass the local index, e.g. `--market ^N225`, `^FTSE`, `^GSPTSE`, `^STOXX50E`.

### 2. Run the script

```bash
python3 -I scripts/analyze_ticker.py TICKER --peers P1,P2,P3 [--sector-etf XLC] [--market SPY] \
  [--range 2y] [--anomaly-days 63] --out /tmp/TICKER-analysis.md
```

| Option | Default | Notes |
|--------|---------|-------|
| `--peers` | none | Comma-separated. Adds peer rows to the returns/risk tables and a peer fundamentals table |
| `--sector-etf` | mapped from SIC | Overrides the sector benchmark |
| `--market` | `SPY` | Broad-market benchmark; also used for beta and the stock-specific-move test |
| `--range` | `2y` | `1y`, `2y`, `5y` or `10y`. Use at least `2y` so SMA200 and 1-year stats are complete |
| `--anomaly-days` | `63` | Sessions scanned for price/volume anomalies (about one quarter) |
| `--no-fundamentals` | off | Skip the SEC company-facts calls |
| `--format` | `md` | `json` gives every computed value for follow-up questions |

Exit code 0 means the report was written. Exit code 1 means no price data for the ticker; ask the user to check the symbol or exchange suffix. The run takes about 10–30 seconds with peers.

Read the whole report, including **Data notes**.

### 3. Write the analysis

Lead with the conclusion: a 3–5 sentence summary of trend, valuation versus peers, relative performance and any notable anomaly. Then cover each section briefly:

1. **Technical:**
   - The trend: price versus SMA50/SMA200 and the last cross.
   - Momentum: RSI, MACD and stochastic.
   - Trend strength (ADX) and volume confirmation (OBV).
   - The nearest support and resistance levels.
   - Use the signal tally as context, not a verdict. Point out where signals conflict, e.g. a long-term uptrend with a fresh bearish MACD cross.
2. **Fundamental:**
   - Valuation multiples, preferably against the peer median and the target's percentile.
   - Margins and growth trend from the 5-year history.
   - Balance-sheet health.
   - Piotroski and Altman scores, with what they mean.
   - Always state the TTM period end, so stale data is visible.
3. **Benchmarking:**
   - Returns across windows against the market, the sector and peers.
   - Risk-adjusted performance (Sharpe, Sortino, max drawdown).
   - Beta and correlation. Note when the 60-day value differs from the 1-year value.
   - Alpha and capture ratios.
4. **Anomalies:**
   - For each high or medium event, say what happened and whether it was stock-specific or market-wide.
   - Name the nearby filing when there is one (e.g. "−12.6% gap down the day after the 8-K 2.02 earnings release").
   - For events with no nearby filing, say the cause is unknown from this data. Don't guess at news.
   - Then cover regime anomalies and fundamental red flags.

Format and wording:
- Quote numbers from the report as they are.
- Use tables where they help. You can paste the script's tables or a trimmed version.
- Present signals with neutral wording ("trades above its 200-day average", "screens expensive relative to peers"). Don't say "buy", "sell" or "will".
- End with one line: *Informational analysis based on public data; not investment advice.*

### 4. Follow-ups

- For a different horizon, rerun with `--range` or `--anomaly-days`.
- For a single number or an extra calculation, rerun with `--format json` and read the value. Don't recompute it from memory.
- To dig into a flagged filing, link the user to `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=<cik>` (the CIK is in the JSON under `fundamentals.cik`).

## Troubleshooting

**`error: HTTP 404 ... finance.yahoo.com`**: the symbol doesn't exist on Yahoo. Check the share-class dash (`BRK-B`) and the exchange suffix (`.T`, `.L`, `.TO`, `.HK`).

**`HTTP 429` from Yahoo:** rate limited. Wait a minute and retry with fewer peers.

**`HTTP 403` from SEC:** SEC rejected the User-Agent. Set `SEC_USER_AGENT` to a real name and email.

**"Fundamentals not available":**
- *Ticker not in SEC EDGAR*: a non-US listing, ETF or index. Report technicals, benchmarking and price anomalies only, and say why fundamentals are missing.
- *Instrument type is ETF / INDEX / MUTUALFUND*: expected; fundamentals only apply to company stock.

**Multiples are n/a:**
- *Foreign private issuer*: EDGAR counts ordinary shares while the price is per ADR, so the script omits price multiples on purpose. Margins and growth are still valid.
- *Currency mismatch*: the company reports in one currency and trades in another.
- *Negative earnings*: P/E and EV/EBIT are blank when EPS or EBIT is ≤ 0.

**TTM period is older than expected:** check the fundamental flags for "XBRL lag" (EDGAR hasn't parsed the latest filing yet) or "TTM lag" (inconsistent tagging between the 10-K and 10-Q). Tell the user how old the figures are.

**Bank or insurer looks odd:** FCF, current ratio and Altman Z are blanked or skipped for financials. Focus on P/B, ROE, net margin and growth.

## Supporting Files

### scripts/analyze_ticker.py
Fetches the data, computes everything, and writes Markdown or JSON. It never writes anywhere except `--out` (or stdout).

**Usage:** `python3 -I scripts/analyze_ticker.py TICKER [--peers ...] [--out FILE]`

### references/README.md
Covers the data sources, price adjustment, indicator formulas and how each signal is read, how TTM and the ratios are built, the SIC → sector ETF table, the anomaly rules and severity, and the known limits.

**When to use:** whenever you need to explain how a number or flag was derived, or decide whether to trust it.

## Core Concepts

**Stock-specific vs market-wide:** a large move is tested twice: as a raw return z-score, and as the residual after removing beta × market return. A high raw z with a small residual means the whole market moved. A high residual means something specific to this company.

**Peer percentile:** the share of peers with a lower value. For P/E, P/S, EV/EBIT and D/E a high percentile means more expensive or more leveraged than peers. For margins, ROE and growth a high percentile is better.

**Signal tally:** a count of bullish, bearish and neutral readings. Several indicators measure the same thing (trend), so a lopsided tally is weaker evidence than it looks.

## Quick Reference

| Task | Command |
|------|---------|
| Full analysis with peers | `python3 -I scripts/analyze_ticker.py MSFT --peers AAPL,GOOGL,ORCL --out /tmp/msft.md` |
| Override sector benchmark | `... META --sector-etf XLC` |
| Non-US listing | `... 7203.T --market ^N225` |
| Price-only (fast) | `... TSLA --no-fundamentals` |
| Longer anomaly window | `... NVDA --anomaly-days 126` |
| Machine-readable output | `... AMZN --format json --out /tmp/amzn.json` |

## Common Mistakes

**Mistake:** Presenting the signal tally or a peer percentile as a recommendation.
**Fix:** Describe what the data shows and what would change it; leave the decision to the user.

**Mistake:** Attributing an anomaly to news you didn't verify.
**Fix:** Only cite the filings the report lists. Otherwise say the cause isn't identified from this data.

**Mistake:** Quoting stale fundamentals as current.
**Fix:** Always state the TTM period end, and mention any "XBRL lag" or "TTM lag" flag.

**Mistake:** Comparing a stock against the wrong sector ETF.
**Fix:** Check the SIC description in the snapshot; use `--sector-etf` when it doesn't match the business.
