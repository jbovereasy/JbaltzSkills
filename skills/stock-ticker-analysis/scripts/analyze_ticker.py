#!/usr/bin/env python3
"""Technical, fundamental, comparative-benchmark and anomaly analysis for one stock ticker.

Prices come from Yahoo Finance's public chart endpoint. Fundamentals, the SIC code
(used to pick a sector ETF) and filing dates come from SEC EDGAR. Read-only, stdlib only.

Usage:
  python3 -I analyze_ticker.py TICKER [--peers A,B,C] [--sector-etf XLK] [--market SPY]
                               [--range 2y] [--anomaly-days 63] [--format md|json] [--out FILE]

SEC asks every client to send a contact in the User-Agent. Set SEC_USER_AGENT to
"<name> <email>"; the default is a placeholder.

Exit codes: 0 report written, 1 price data missing for the ticker, 2 bad arguments.
"""
import argparse
import datetime as dt
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SEC_UA = os.environ.get("SEC_USER_AGENT") or "JbaltzSkills stock-ticker-analysis placeholder@email.com"
YAHOO_UA = "Mozilla/5.0 (X11; Linux x86_64) JbaltzSkills/1.0"
TRADING_DAYS = 252
DEFAULT_RF = 0.04
FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "20-F", "20-F/A", "40-F", "40-F/A", "6-K"}
WINDOWS = [("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126), ("1Y", 252)]

# 8-K item codes worth naming next to an anomaly. Full list: references/README.md (Anomaly detection)
EIGHT_K_ITEMS = {
    "1.01": "material agreement", "1.02": "agreement terminated", "1.03": "bankruptcy",
    "2.01": "acquisition/disposition", "2.02": "earnings release", "2.05": "restructuring costs",
    "2.06": "impairment", "3.01": "delisting notice", "4.02": "non-reliance on prior financials",
    "5.02": "officer/director change", "7.01": "Reg FD disclosure", "8.01": "other event",
}


class DataError(Exception):
    pass


# --------------------------------------------------------------------------- fetch

def fetch_json(url, ua, retries=3, timeout=25):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise DataError(f"HTTP {e.code} for {url}") from e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise DataError(f"{type(e).__name__} for {url}: {e}") from e


def yahoo_chart(symbol, range_):
    """-> {symbol, meta, bars[{date,open,high,low,close,raw_close,volume}], dividends[(date, amt)]}.

    OHLC are scaled by adjclose/close so splits and dividends don't show up as gaps."""
    q = urllib.parse.urlencode({"range": range_, "interval": "1d", "events": "div,split",
                                "includeAdjustedClose": "true"})
    last_err = None
    for host in ("query1", "query2"):
        try:
            data = fetch_json(f"https://{host}.finance.yahoo.com/v8/finance/chart/"
                              f"{urllib.parse.quote(symbol)}?{q}", YAHOO_UA)
            break
        except DataError as e:
            last_err = e
    else:
        raise last_err
    chart = data.get("chart") or {}
    if not chart.get("result"):
        raise DataError(f"no chart data for {symbol}: {chart.get('error')}")
    r = chart["result"][0]
    meta = r.get("meta", {})
    off = meta.get("gmtoffset", 0)
    quote = r["indicators"]["quote"][0]
    adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose") or quote["close"]
    bars = []
    for i, t in enumerate(r.get("timestamp") or []):
        o, h, l, c, v, a = (quote["open"][i], quote["high"][i], quote["low"][i],
                            quote["close"][i], quote["volume"][i], adj[i])
        if None in (o, h, l, c, a) or c <= 0:
            continue
        f = a / c
        day = dt.datetime.fromtimestamp(t + off, dt.timezone.utc).date().isoformat()
        bars.append({"date": day, "open": o * f, "high": h * f, "low": l * f, "close": a,
                     "raw_close": c, "volume": v or 0})
    divs = sorted((dt.datetime.fromtimestamp(int(k) + off, dt.timezone.utc).date().isoformat(), d["amount"])
                  for k, d in ((r.get("events") or {}).get("dividends") or {}).items())
    return {"symbol": symbol, "meta": meta, "bars": bars, "dividends": divs}


def risk_free_rate():
    """13-week T-bill yield (^IRX) as a decimal, or DEFAULT_RF if unavailable."""
    try:
        bars = yahoo_chart("^IRX", "1mo")["bars"]
        if bars:
            return bars[-1]["raw_close"] / 100.0, "^IRX (13-week T-bill)"
    except DataError:
        pass
    return DEFAULT_RF, f"default {DEFAULT_RF:.0%} (^IRX unavailable)"


_SEC_TICKERS = None


def sec_lookup(ticker):
    """-> (cik10, title) or None."""
    global _SEC_TICKERS
    if _SEC_TICKERS is None:
        data = fetch_json("https://www.sec.gov/files/company_tickers.json", SEC_UA)
        _SEC_TICKERS = {row["ticker"].upper(): (f"{row['cik_str']:010d}", row["title"]) for row in data.values()}
    t = ticker.upper()
    return _SEC_TICKERS.get(t) or _SEC_TICKERS.get(t.replace(".", "-")) or _SEC_TICKERS.get(t.replace("-", "."))


def sec_submissions(cik):
    time.sleep(0.12)  # SEC fair-access limit is 10 requests/second
    return fetch_json(f"https://data.sec.gov/submissions/CIK{cik}.json", SEC_UA)


def sec_companyfacts(cik):
    time.sleep(0.12)
    return fetch_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", SEC_UA)


# --------------------------------------------------------------------------- math helpers

def d(s):
    return dt.date.fromisoformat(s)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def stdev(xs):
    xs = [x for x in xs if x is not None]
    return statistics.stdev(xs) if len(xs) >= 2 else None


def safe_div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def pct_change(new, old):
    if new is None or old is None or old == 0:
        return None
    return new / abs(old) - 1 if old < 0 else new / old - 1


def sma(xs, n):
    out = [None] * len(xs)
    s = 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(xs, n):
    out = [None] * len(xs)
    if len(xs) < n:
        return out
    k = 2 / (n + 1)
    out[n - 1] = sum(xs[:n]) / n
    for i in range(n, len(xs)):
        out[i] = xs[i] * k + out[i - 1] * (1 - k)
    return out


def ema_sparse(xs, n):
    """EMA over a list that starts with Nones."""
    start = next((i for i, x in enumerate(xs) if x is not None), None)
    out = [None] * len(xs)
    if start is None:
        return out
    tail = ema(xs[start:], n)
    out[start:] = tail
    return out


def wilder(xs, n, start=0):
    """Wilder smoothing (RMA) of xs[start:]; returns list aligned to xs."""
    out = [None] * len(xs)
    if len(xs) - start < n:
        return out
    out[start + n - 1] = sum(xs[start:start + n]) / n
    for i in range(start + n, len(xs)):
        out[i] = (out[i - 1] * (n - 1) + xs[i]) / n
    return out


def rolling_stdev(xs, n):
    out = [None] * len(xs)
    for i in range(n - 1, len(xs)):
        out[i] = statistics.pstdev(xs[i - n + 1:i + 1])
    return out


def max_drawdown(closes):
    peak, mdd = -math.inf, 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)
    return mdd


def ann_vol(rets):
    s = stdev(rets)
    return s * math.sqrt(TRADING_DAYS) if s is not None else None


def returns(closes):
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]


# --------------------------------------------------------------------------- technicals

def technicals(bars):
    c = [b["close"] for b in bars]
    h = [b["high"] for b in bars]
    lo = [b["low"] for b in bars]
    v = [b["volume"] for b in bars]
    n = len(c)
    last = c[-1]

    sma20, sma50, sma200 = sma(c, 20), sma(c, 50), sma(c, 200)
    ema12, ema26 = ema(c, 12), ema(c, 26)
    macd = [a - b if a is not None and b is not None else None for a, b in zip(ema12, ema26)]
    macd_sig = ema_sparse(macd, 9)
    hist = [a - b if a is not None and b is not None else None for a, b in zip(macd, macd_sig)]

    diffs = [0.0] + [c[i] - c[i - 1] for i in range(1, n)]
    gains = wilder([max(x, 0) for x in diffs], 14, 1)
    losses = wilder([max(-x, 0) for x in diffs], 14, 1)
    rsi = [None if g is None else (100.0 if l_ == 0 else 100 - 100 / (1 + g / l_)) for g, l_ in zip(gains, losses)]

    sd20 = rolling_stdev(c, 20)
    bb_up = [m + 2 * s if m is not None and s is not None else None for m, s in zip(sma20, sd20)]
    bb_lo = [m - 2 * s if m is not None and s is not None else None for m, s in zip(sma20, sd20)]
    bb_bw = [safe_div(u - l_, m) if u is not None else None for u, l_, m in zip(bb_up, bb_lo, sma20)]
    pct_b = safe_div(last - bb_lo[-1], bb_up[-1] - bb_lo[-1]) if bb_up[-1] is not None else None

    tr = [h[0] - lo[0]] + [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, n)]
    atr = wilder(tr, 14)

    k = [None] * n
    for i in range(13, n):
        hh, ll = max(h[i - 13:i + 1]), min(lo[i - 13:i + 1])
        k[i] = 100 * (c[i] - ll) / (hh - ll) if hh > ll else 50.0
    dline = [None] * n
    for i in range(15, n):
        dline[i] = sum(k[i - 2:i + 1]) / 3

    pdm = [0.0] + [max(h[i] - h[i - 1], 0) if h[i] - h[i - 1] > lo[i - 1] - lo[i] else 0.0 for i in range(1, n)]
    ndm = [0.0] + [max(lo[i - 1] - lo[i], 0) if lo[i - 1] - lo[i] > h[i] - h[i - 1] else 0.0 for i in range(1, n)]
    str_, spdm, sndm = wilder(tr, 14, 1), wilder(pdm, 14, 1), wilder(ndm, 14, 1)
    pdi = [safe_div(100 * p, t) if t else None for p, t in zip(spdm, str_)]
    ndi = [safe_div(100 * q, t) if t else None for q, t in zip(sndm, str_)]
    dx = [100 * abs(p - q) / (p + q) if p is not None and q is not None and p + q else (0.0 if p is not None else None)
          for p, q in zip(pdi, ndi)]
    first_dx = next((i for i, x in enumerate(dx) if x is not None), n)
    adx = wilder([x if x is not None else 0.0 for x in dx], 14, first_dx)

    obv = [0.0]
    for i in range(1, n):
        obv.append(obv[-1] + (v[i] if c[i] > c[i - 1] else -v[i] if c[i] < c[i - 1] else 0))

    # last 50/200 cross
    cross = None
    for i in range(n - 1, 0, -1):
        if None in (sma50[i], sma200[i], sma50[i - 1], sma200[i - 1]):
            break
        if (sma50[i] - sma200[i]) * (sma50[i - 1] - sma200[i - 1]) < 0:
            cross = {"type": "golden" if sma50[i] > sma200[i] else "death", "date": bars[i]["date"],
                     "days_ago": n - 1 - i}
            break

    macd_cross = None
    for i in range(n - 1, max(n - 11, 0), -1):
        if hist[i] is not None and hist[i - 1] is not None and hist[i] * hist[i - 1] < 0:
            macd_cross = {"type": "bullish" if hist[i] > 0 else "bearish", "date": bars[i]["date"], "days_ago": n - 1 - i}
            break

    hi52, lo52 = max(h[-TRADING_DAYS:]), min(lo[-TRADING_DAYS:])
    rets = returns(c)
    levels = support_resistance(bars[-126:], last)
    bw_hist = [x for x in bb_bw[-126:] if x is not None]

    vals = {
        "price": last, "sma20": sma20[-1], "sma50": sma50[-1], "sma200": sma200[-1],
        "ema12": ema12[-1], "ema26": ema26[-1], "macd": macd[-1], "macd_signal": macd_sig[-1], "macd_hist": hist[-1],
        "rsi14": rsi[-1], "stoch_k": k[-1], "stoch_d": dline[-1], "bb_upper": bb_up[-1], "bb_lower": bb_lo[-1],
        "bb_pct_b": pct_b, "bb_bandwidth": bb_bw[-1],
        "bb_squeeze": bool(bw_hist) and bb_bw[-1] is not None and bb_bw[-1] <= min(bw_hist) * 1.05,
        "atr14": atr[-1], "atr_pct": safe_div(atr[-1], last), "adx14": adx[-1], "plus_di": pdi[-1], "minus_di": ndi[-1],
        "obv_20d_change": obv[-1] - obv[-21] if n > 21 else None,
        "high_52w": hi52, "low_52w": lo52, "pct_from_52w_high": last / hi52 - 1, "pct_from_52w_low": last / lo52 - 1,
        "vol_20d_ann": ann_vol(rets[-20:]), "vol_60d_ann": ann_vol(rets[-60:]), "vol_1y_ann": ann_vol(rets[-TRADING_DAYS:]),
        "avg_volume_20d": mean(v[-20:]), "sma50_200_cross": cross, "macd_recent_cross": macd_cross,
    }
    return {"values": vals, "signals": tech_signals(vals, c), "levels": levels,
            "series": {"atr": atr, "bb_bw": bb_bw}}


def support_resistance(bars, price, w=5, tol=0.015):
    """Swing pivots (local extremes over +/- w bars), clustered within tol."""
    pivots = []
    for i in range(w, len(bars) - w):
        win = bars[i - w:i + w + 1]
        if bars[i]["high"] == max(b["high"] for b in win):
            pivots.append(bars[i]["high"])
        if bars[i]["low"] == min(b["low"] for b in win):
            pivots.append(bars[i]["low"])
    clusters = []
    for p in sorted(pivots):
        if clusters and p <= clusters[-1][-1] * (1 + tol):
            clusters[-1].append(p)
        else:
            clusters.append([p])
    lv = [{"level": sum(cl) / len(cl), "touches": len(cl)} for cl in clusters]
    support = sorted([x for x in lv if x["level"] < price], key=lambda x: -x["level"])[:3]
    resistance = sorted([x for x in lv if x["level"] > price], key=lambda x: x["level"])[:3]
    return {"support": support, "resistance": resistance}


def tech_signals(v, closes):
    s = []

    def add(ind, reading, detail):
        s.append({"indicator": ind, "reading": reading, "detail": detail})

    p = v["price"]
    if v["sma200"] is not None:
        add("Price vs SMA200", "bullish" if p > v["sma200"] else "bearish",
            f"price {pct_change(p, v['sma200']):+.1%} vs 200-day average (long-term trend)")
    if v["sma50"] is not None:
        add("Price vs SMA50", "bullish" if p > v["sma50"] else "bearish",
            f"price {pct_change(p, v['sma50']):+.1%} vs 50-day average (intermediate trend)")
    if v["sma50"] is not None and v["sma200"] is not None:
        cr = v["sma50_200_cross"]
        detail = "50-day above 200-day" if v["sma50"] > v["sma200"] else "50-day below 200-day"
        if cr:
            detail += f"; last {cr['type']} cross {cr['date']} ({cr['days_ago']} sessions ago)"
        add("SMA50/SMA200", "bullish" if v["sma50"] > v["sma200"] else "bearish", detail)
    if v["macd_hist"] is not None:
        detail = f"MACD {v['macd']:.2f} vs signal {v['macd_signal']:.2f}"
        mc = v["macd_recent_cross"]
        if mc:
            detail += f"; {mc['type']} crossover {mc['date']}"
        add("MACD (12,26,9)", "bullish" if v["macd_hist"] > 0 else "bearish", detail)
    r = v["rsi14"]
    if r is not None:
        if r >= 70:
            add("RSI(14)", "bearish", f"{r:.1f} overbought (>70); pullback risk")
        elif r <= 30:
            add("RSI(14)", "bullish", f"{r:.1f} oversold (<30); bounce potential")
        else:
            add("RSI(14)", "bullish" if r >= 50 else "bearish", f"{r:.1f} ({'positive' if r >= 50 else 'negative'} momentum)")
    if v["stoch_k"] is not None and v["stoch_d"] is not None:
        kk, dd = v["stoch_k"], v["stoch_d"]
        zone = "overbought" if kk > 80 else "oversold" if kk < 20 else "mid-range"
        reading = "bearish" if kk > 80 and kk < dd else "bullish" if kk < 20 and kk > dd else (
            "bullish" if kk > dd else "bearish")
        add("Stochastic(14,3)", reading, f"%K {kk:.1f} / %D {dd:.1f} ({zone})")
    if v["bb_pct_b"] is not None:
        b = v["bb_pct_b"]
        if b > 1:
            reading, note = "bearish", "closed above upper band (stretched)"
        elif b < 0:
            reading, note = "bullish", "closed below lower band (stretched)"
        else:
            reading, note = "neutral", "inside bands"
        if v["bb_squeeze"]:
            note += "; bandwidth at 6-month low (squeeze, breakout likely)"
        add("Bollinger %B (20,2)", reading, f"%B {b:.2f}, {note}")
    if v["adx14"] is not None:
        a = v["adx14"]
        up = (v["plus_di"] or 0) > (v["minus_di"] or 0)
        if a >= 25:
            add("ADX(14)", "bullish" if up else "bearish", f"{a:.1f} strong trend, {'+DI' if up else '-DI'} leading")
        else:
            add("ADX(14)", "neutral", f"{a:.1f} weak/no trend (<25)")
    if v["obv_20d_change"] is not None and len(closes) > 21:
        pr = closes[-1] / closes[-21] - 1
        o = v["obv_20d_change"]
        if (o > 0) == (pr > 0):
            add("OBV (20d)", "bullish" if o > 0 else "bearish", f"volume confirms {pr:+.1%} 20-day move")
        else:
            add("OBV (20d)", "bullish" if o > 0 else "bearish",
                f"divergence: price {pr:+.1%} but OBV {'rising' if o > 0 else 'falling'}")
    fh = v["pct_from_52w_high"]
    add("52-week position", "bullish" if fh > -0.05 else "bearish" if fh < -0.20 else "neutral",
        f"{fh:+.1%} from 52w high, {v['pct_from_52w_low']:+.1%} from 52w low")
    return s


# --------------------------------------------------------------------------- comparative

def window_returns(bars):
    c = [b["close"] for b in bars]
    out = {}
    for name, n in WINDOWS:
        out[name] = c[-1] / c[-1 - n] - 1 if len(c) > n else None
    year = bars[-1]["date"][:4]
    prior = [b["close"] for b in bars if b["date"] < f"{year}-01-01"]
    out["YTD"] = c[-1] / prior[-1] - 1 if prior else None
    return out


def risk_stats(bars, rf):
    c = [b["close"] for b in bars[-(TRADING_DAYS + 1):]]
    r = returns(c)
    rf_d = rf / TRADING_DAYS
    s = stdev(r)
    downside = [min(x - rf_d, 0) for x in r]
    dd = math.sqrt(sum(x * x for x in downside) / len(downside)) if downside else None
    m = mean(r)
    return {
        "vol_1y": ann_vol(r),
        "sharpe_1y": (m - rf_d) / s * math.sqrt(TRADING_DAYS) if s else None,
        "sortino_1y": (m - rf_d) / dd * math.sqrt(TRADING_DAYS) if dd else None,
        "max_drawdown_1y": max_drawdown(c),
    }


def aligned_returns(a_bars, b_bars, last_n=TRADING_DAYS):
    bmap = {b["date"]: b["close"] for b in b_bars}
    common = [(x["date"], x["close"], bmap[x["date"]]) for x in a_bars if x["date"] in bmap]
    common = common[-(last_n + 1):]
    ra = [common[i][1] / common[i - 1][1] - 1 for i in range(1, len(common))]
    rb = [common[i][2] / common[i - 1][2] - 1 for i in range(1, len(common))]
    return ra, rb


def beta_corr(ra, rb):
    if len(ra) < 20:
        return None, None
    ma, mb = mean(ra), mean(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / (len(ra) - 1)
    vb, va = statistics.variance(rb), statistics.variance(ra)
    beta = cov / vb if vb else None
    corr = cov / math.sqrt(va * vb) if va and vb else None
    return beta, corr


def relative_stats(t_bars, b_bars, rf):
    ra, rb = aligned_returns(t_bars, b_bars)
    if len(ra) < 20:
        return None
    beta, corr = beta_corr(ra, rb)
    rf_d = rf / TRADING_DAYS
    alpha = None
    if beta is not None:
        alpha = mean([(x - rf_d) - beta * (y - rf_d) for x, y in zip(ra, rb)]) * TRADING_DAYS
    active = [x - y for x, y in zip(ra, rb)]
    te = ann_vol(active)
    up = [(x, y) for x, y in zip(ra, rb) if y > 0]
    dn = [(x, y) for x, y in zip(ra, rb) if y < 0]
    ra60, rb60 = aligned_returns(t_bars, b_bars, 60)
    beta60, corr60 = beta_corr(ra60, rb60)
    return {
        "beta_1y": beta, "corr_1y": corr, "alpha_1y_ann": alpha, "beta_60d": beta60, "corr_60d": corr60,
        "tracking_error_1y": te, "info_ratio_1y": safe_div(mean(active) * TRADING_DAYS, te),
        "up_capture": safe_div(mean([x for x, _ in up]), mean([y for _, y in up])) if up else None,
        "down_capture": safe_div(mean([x for x, _ in dn]), mean([y for _, y in dn])) if dn else None,
        "n_days": len(ra),
    }


# --------------------------------------------------------------------------- fundamentals

TAGS = {
    "revenue": ["RevenuesNetOfInterestExpense", "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet",
                "Revenue"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfSales"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"],
    "net_income": ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
                   "ProfitLossAttributableToOwnersOfParent", "ProfitLoss"],
    "eps_diluted": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "DilutedEarningsLossPerShare"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations", "CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
               "EquityAttributableToOwnersOfParent", "Equity"],
    "current_assets": ["AssetsCurrent", "CurrentAssets"],
    "current_liabilities": ["LiabilitiesCurrent", "CurrentLiabilities"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndCashEquivalents"],
    "ltd_total": ["LongTermDebt"],
    "ltd_noncurrent": ["LongTermDebtNoncurrent", "NoncurrentBorrowings"],
    "ltd_current": ["LongTermDebtCurrent", "DebtCurrent", "CurrentBorrowings"],
    "short_debt": ["ShortTermBorrowings", "CommercialPaper"],
    "receivables": ["AccountsReceivableNetCurrent", "TradeAndOtherCurrentReceivables"],
    "inventory": ["InventoryNet", "Inventories"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit", "RetainedEarnings"],
    "diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding",
                       "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
                       "AdjustedWeightedAverageShares"],
    "shares_outstanding": ["EntityCommonStockSharesOutstanding"],
}
PER_SHARE = {"eps_diluted"}
SHARE_COUNTS = {"diluted_shares", "shares_outstanding"}


def dur(e):
    return (d(e["end"]) - d(e["start"])).days if e.get("start") else None


def collect(facts, key):
    """Merge every alternative tag's facts into one list keyed by (start, end); earlier tags win."""
    kind = "per_share" if key in PER_SHARE else "shares" if key in SHARE_COUNTS else "money"
    merged, currency = {}, None
    for prio, tag in enumerate(TAGS[key]):
        for tax in ("us-gaap", "ifrs-full", "dei"):
            node = (facts.get(tax) or {}).get(tag)
            if not node:
                continue
            units = node.get("units", {})
            if kind == "shares":
                unit = "shares" if "shares" in units else None
            elif kind == "per_share":
                cands = [u for u in units if u.endswith("/shares")]
                unit = "USD/shares" if "USD/shares" in cands else (cands[0] if cands else None)
            else:
                cands = [u for u in units if "/" not in u and u not in ("shares", "pure")]
                unit = "USD" if "USD" in cands else (cands[0] if cands else None)
            if not unit:
                continue
            for e in units[unit]:
                if e.get("form") not in FORMS or e.get("val") is None:
                    continue
                k = (e.get("start"), e["end"])
                old = merged.get(k)
                if old is None or prio < old["_prio"] or (prio == old["_prio"] and e.get("filed", "") > old["filed"]):
                    merged[k] = {"start": e.get("start"), "end": e["end"], "val": e["val"],
                                 "filed": e.get("filed", ""), "form": e.get("form"), "_prio": prio}
            if currency is None and kind != "shares":
                currency = unit.split("/")[0]
    return sorted(merged.values(), key=lambda e: (e["end"], e.get("start") or "")), currency


def near(e_end, target, tol):
    return abs((d(e_end) - target).days) <= tol


def flow_ttm(entries, as_of=None):
    """Trailing-twelve-month value of a flow (income/cash-flow) item.

    Uses the annual figure if the latest period is a fiscal year, otherwise
    last FY + current YTD - prior-year YTD, otherwise the sum of 4 quarters."""
    fl = [e for e in entries if e.get("start") and dur(e) <= 380 and (as_of is None or e["end"] <= as_of)]
    if not fl:
        return None
    latest = max(e["end"] for e in fl)
    at_end = [e for e in fl if e["end"] == latest]
    annual = [e for e in at_end if 350 <= dur(e) <= 380]
    if annual:
        return {"value": annual[0]["val"], "end": latest, "method": "fiscal year"}
    cur = max(at_end, key=dur)
    dd = dur(cur)
    cur_end, cur_start = d(cur["end"]), d(cur["start"])
    prior = next((e for e in fl if near(e["end"], cur_end - dt.timedelta(days=365), 15)
                  and abs(dur(e) - dd) <= 15), None)
    fy = next((e for e in fl if 350 <= dur(e) <= 380 and near(e["end"], cur_start - dt.timedelta(days=1), 15)), None)
    if prior and fy:
        return {"value": fy["val"] + cur["val"] - prior["val"], "end": latest, "method": "FY + YTD - prior YTD"}
    quarters = sorted([e for e in fl if 80 <= dur(e) <= 100], key=lambda e: e["end"], reverse=True)
    chain, expect = [], cur_end
    for q in quarters:
        if near(q["end"], expect, 10):
            chain.append(q)
            expect = d(q["start"]) - dt.timedelta(days=1)
            if len(chain) == 4:
                return {"value": sum(x["val"] for x in chain), "end": latest, "method": "sum of 4 quarters"}
    return None


def prior_ttm(entries, cur):
    if not cur:
        return None
    target = d(cur["end"]) - dt.timedelta(days=365)
    p = flow_ttm(entries, as_of=(target + dt.timedelta(days=20)).isoformat())
    return p if p and near(p["end"], target, 25) else None


def instant(entries, target=None, tol=45):
    """Balance-sheet (point-in-time) value: latest, or nearest to target within tol days."""
    inst = [e for e in entries if not e.get("start")]
    if not inst:
        return None
    if target is None:
        e = inst[-1]
        return {"value": e["val"], "end": e["end"]}
    best = min(inst, key=lambda e: abs((d(e["end"]) - target).days))
    return {"value": best["val"], "end": best["end"]} if near(best["end"], target, tol) else None


def annual_map(entries):
    return {e["end"]: e["val"] for e in entries if e.get("start") and 350 <= dur(e) <= 380}


def val(x):
    return x["value"] if x else None


def fundamentals(ticker, price, price_currency, sic):
    hit = sec_lookup(ticker)
    if not hit:
        return {"available": False, "reason": "ticker not in SEC EDGAR (non-US listing, ETF, fund, or index)"}
    cik, title = hit
    try:
        facts = sec_companyfacts(cik).get("facts", {})
    except DataError as e:
        if "HTTP 404" not in str(e):
            raise
        facts = {}
    if not facts:
        return {"available": False, "reason": "no XBRL financial data on EDGAR for this filer", "cik": cik}
    S, cur_by = {}, {}
    for k in TAGS:
        S[k], cur_by[k] = collect(facts, k)
    currency = cur_by["revenue"] or cur_by["net_income"] or "USD"

    t = {k: flow_ttm(S[k]) for k in ("revenue", "cost_of_revenue", "gross_profit", "operating_income",
                                       "net_income", "eps_diluted", "cfo", "capex")}
    p = {k: prior_ttm(S[k], t[k]) for k in t}
    gp, gp_prior = val(t["gross_profit"]), val(p["gross_profit"])
    if gp is None and val(t["revenue"]) is not None and val(t["cost_of_revenue"]) is not None:
        gp = val(t["revenue"]) - val(t["cost_of_revenue"])
    if gp_prior is None and val(p["revenue"]) is not None and val(p["cost_of_revenue"]) is not None:
        gp_prior = val(p["revenue"]) - val(p["cost_of_revenue"])

    latest_bs = instant(S["assets"]) or instant(S["equity"])
    bs_end = d(latest_bs["end"]) if latest_bs else None
    yr_ago = bs_end - dt.timedelta(days=365) if bs_end else None

    def bs(k, when=bs_end, tol=45):
        return val(instant(S[k], when, tol)) if when else None

    def debt(when, tol=45):
        total = bs("ltd_total", when, tol)
        if total is None:
            parts = [bs("ltd_noncurrent", when, tol), bs("ltd_current", when, tol)]
            total = sum(x for x in parts if x is not None) if any(x is not None for x in parts) else None
        short = bs("short_debt", when, tol)
        if total is None and short is None:
            return None
        return (total or 0) + (short or 0)

    rev, ni, cfo, capex, oi = (val(t[k]) for k in ("revenue", "net_income", "cfo", "capex", "operating_income"))
    eps = val(t["eps_diluted"])
    assets, equity, liab = bs("assets"), bs("equity"), bs("liabilities")
    if liab is None and assets is not None and equity is not None:
        liab = assets - equity
    cash, total_debt = bs("cash"), debt(bs_end)
    ca, cl = bs("current_assets"), bs("current_liabilities")

    so = instant(S["shares_outstanding"])
    shares = val(so)
    dil_v = S["diluted_shares"][-1]["val"] if S["diluted_shares"] else None
    shares_note = "dei:EntityCommonStockSharesOutstanding"
    if shares is None or (dil_v and shares < 0.7 * dil_v):  # multi-class filers report one class only
        shares, shares_note = dil_v, "weighted diluted shares (latest period)"
    if eps is None and ni is not None and shares:
        eps = ni / shares

    same_ccy = (currency == (price_currency or "USD"))
    # 20-F/40-F filers usually trade as ADRs; EDGAR counts ordinary shares, so price x shares is off by the ADR ratio
    is_fpi = any((e.get("form") or "").startswith(("20-F", "40-F")) for e in S["revenue"] + S["net_income"])
    price_ok = same_ccy and not is_fpi
    mcap = price * shares if shares and price_ok else None
    fcf = cfo - capex if cfo is not None and capex is not None else (cfo if cfo is not None and capex is None else None)
    ev = mcap + (total_debt or 0) - (cash or 0) if mcap is not None else None
    assets_prev, equity_prev = bs("assets", yr_ago), bs("equity", yr_ago)
    avg_eq = mean([equity, equity_prev]) if equity is not None else None
    avg_assets = mean([assets, assets_prev]) if assets is not None else None
    eps_prior = val(p["eps_diluted"])

    ratios = {
        "market_cap": mcap, "enterprise_value": ev,
        "pe_ttm": safe_div(price, eps) if eps and eps > 0 and price_ok else None,
        "ps_ttm": safe_div(mcap, rev), "pb": safe_div(mcap, equity) if equity and equity > 0 else None,
        "ev_ebit": safe_div(ev, oi) if oi and oi > 0 else None, "ev_sales": safe_div(ev, rev),
        "fcf_yield": safe_div(fcf, mcap),
        "gross_margin": safe_div(gp, rev), "operating_margin": safe_div(oi, rev), "net_margin": safe_div(ni, rev),
        "fcf_margin": safe_div(fcf, rev), "roe": safe_div(ni, avg_eq) if avg_eq and avg_eq > 0 else None,
        "roa": safe_div(ni, avg_assets),
        "debt_to_equity": safe_div(total_debt, equity) if equity and equity > 0 else None,
        "net_debt": (total_debt or 0) - cash if cash is not None else None,
        "current_ratio": safe_div(ca, cl),
        "revenue_growth_yoy": pct_change(rev, val(p["revenue"])),
        "net_income_growth_yoy": pct_change(ni, val(p["net_income"])),
        "eps_growth_yoy": pct_change(eps, eps_prior) if val(t["eps_diluted"]) is not None else None,
        "gross_margin_prior": safe_div(gp_prior, val(p["revenue"])),
    }
    if sic is not None and 6000 <= sic <= 6799:  # deposits/loans swamp operating cash flow and current items
        for k in ("fcf_yield", "fcf_margin", "current_ratio"):
            ratios[k] = None
    ttm = {"revenue": rev, "gross_profit": gp, "operating_income": oi, "net_income": ni, "eps_diluted": eps,
           "cfo": cfo, "capex": capex, "fcf": fcf}
    balance = {"as_of": latest_bs["end"] if latest_bs else None, "assets": assets, "liabilities": liab,
               "equity": equity, "cash": cash, "total_debt": total_debt, "current_assets": ca,
               "current_liabilities": cl, "shares": shares, "shares_source": shares_note}

    # 5-year annual history
    amaps = {k: annual_map(S[k]) for k in ("revenue", "net_income", "eps_diluted", "cfo", "capex",
                                            "gross_profit", "cost_of_revenue", "operating_income")}
    fy_ends = sorted(set(amaps["revenue"]) | set(amaps["net_income"]))[-6:]
    history = []
    for e in fy_ends[-5:]:
        r_ = amaps["revenue"].get(e)
        g_ = amaps["gross_profit"].get(e)
        if g_ is None and r_ is not None and amaps["cost_of_revenue"].get(e) is not None:
            g_ = r_ - amaps["cost_of_revenue"][e]
        c_, x_ = amaps["cfo"].get(e), amaps["capex"].get(e)
        history.append({"fy_end": e, "revenue": r_, "net_income": amaps["net_income"].get(e),
                        "eps_diluted": amaps["eps_diluted"].get(e), "cfo": c_,
                        "fcf": c_ - x_ if c_ is not None and x_ is not None else None,
                        "gross_margin": safe_div(g_, r_), "operating_margin": safe_div(amaps["operating_income"].get(e), r_)})

    piotroski = piotroski_score(S, amaps, fy_ends, bs, debt)
    is_financial = sic is not None and 6000 <= sic <= 6799
    altman = None
    if not is_financial and all(x is not None for x in (assets, liab, rev)) and assets and mcap and liab:
        wc = (ca - cl) if ca is not None and cl is not None else None
        re_ = bs("retained_earnings")
        if wc is not None and re_ is not None and oi is not None:
            z = 1.2 * wc / assets + 1.4 * re_ / assets + 3.3 * oi / assets + 0.6 * mcap / liab + 1.0 * rev / assets
            altman = {"z": z, "zone": "safe" if z > 2.99 else "grey" if z >= 1.81 else "distress"}

    latest_filed = max((e["filed"] for e in S["revenue"] + S["net_income"]), default=None)
    rec_now, rec_prev = bs("receivables"), bs("receivables", yr_ago)
    inv_now, inv_prev = bs("inventory"), bs("inventory", yr_ago)
    sh_prev = val(instant(S["shares_outstanding"], d(so["end"]) - dt.timedelta(days=365), 60)) if so else None
    return {
        "available": True, "cik": cik, "name": title, "currency": currency, "price_currency_matches": same_ccy,
        "foreign_private_issuer": is_fpi,
        "ttm_period_end": t["revenue"]["end"] if t["revenue"] else (t["net_income"] or {}).get("end"),
        "ttm_method": (t["revenue"] or t["net_income"] or {}).get("method"), "latest_filed": latest_filed,
        "ttm": ttm, "balance": balance, "ratios": ratios, "history": history, "piotroski": piotroski,
        "altman": altman, "is_financial": is_financial,
        "_anomaly_inputs": {
            "ni": ni, "cfo": cfo, "fcf": fcf, "avg_assets": avg_assets, "equity": equity,
            "rev_growth": ratios["revenue_growth_yoy"], "rec_growth": pct_change(rec_now, rec_prev),
            "inv_growth": pct_change(inv_now, inv_prev), "share_growth": pct_change(shares if so else None, sh_prev),
            "gm": ratios["gross_margin"], "gm_prior": ratios["gross_margin_prior"],
            "ttm_end": (t["revenue"] or t["net_income"] or {}).get("end"), "bs_end": balance["as_of"],
        },
    }


def piotroski_score(S, amaps, fy_ends, bs, debt):
    if len(fy_ends) < 2:
        return None
    t1, t0 = fy_ends[-1], fy_ends[-2]
    t1d, t0d = d(t1), d(t0)
    tm1 = t0d - dt.timedelta(days=365)

    def a(k, e):
        return amaps[k].get(e)

    ni1, ni0 = a("net_income", t1), a("net_income", t0)
    cfo1 = a("cfo", t1)
    A1, A0, Am1 = bs("assets", t1d, 15), bs("assets", t0d, 15), bs("assets", tm1, 20)
    rev1, rev0 = a("revenue", t1), a("revenue", t0)
    g1 = a("gross_profit", t1) if a("gross_profit", t1) is not None else (
        rev1 - a("cost_of_revenue", t1) if rev1 is not None and a("cost_of_revenue", t1) is not None else None)
    g0 = a("gross_profit", t0) if a("gross_profit", t0) is not None else (
        rev0 - a("cost_of_revenue", t0) if rev0 is not None and a("cost_of_revenue", t0) is not None else None)
    cr1 = safe_div(bs("current_assets", t1d, 15), bs("current_liabilities", t1d, 15))
    cr0 = safe_div(bs("current_assets", t0d, 15), bs("current_liabilities", t0d, 15))
    lev1, lev0 = safe_div(debt(t1d, 15), A1), safe_div(debt(t0d, 15), A0)
    sh = {e["end"]: e["val"] for e in S["diluted_shares"] if e.get("start") and 350 <= dur(e) <= 380}
    roa1, roa0 = safe_div(ni1, A0), safe_div(ni0, Am1)
    tests = [
        ("ROA > 0", None if roa1 is None else roa1 > 0),
        ("Operating cash flow > 0", None if cfo1 is None else cfo1 > 0),
        ("ROA improved", None if roa1 is None or roa0 is None else roa1 > roa0),
        ("Cash flow > net income (accrual quality)", None if cfo1 is None or ni1 is None else cfo1 > ni1),
        ("Leverage fell", None if lev1 is None or lev0 is None else lev1 < lev0),
        ("Current ratio rose", None if cr1 is None or cr0 is None else cr1 > cr0),
        ("No dilution", None if sh.get(t1) is None or sh.get(t0) is None else sh[t1] <= sh[t0]),
        ("Gross margin rose", None if not (g1 and g0 and rev1 and rev0) else g1 / rev1 > g0 / rev0),
        ("Asset turnover rose", None if not (rev1 and rev0 and A0 and Am1) else rev1 / A0 > rev0 / Am1),
    ]
    scored = [x for x in tests if x[1] is not None]
    return {"score": sum(1 for _, ok in scored if ok), "out_of": len(scored), "fy": t1,
            "tests": [{"test": n, "pass": ok} for n, ok in tests]}


# --------------------------------------------------------------------------- anomalies

def filing_events(subs, since):
    rec = (subs or {}).get("filings", {}).get("recent", {})
    out = []
    for form, fd, items in zip(rec.get("form", []), rec.get("filingDate", []), rec.get("items", [""] * 10 ** 6)):
        if fd < since or form not in ("8-K", "8-K/A", "10-Q", "10-K", "20-F", "6-K", "40-F"):
            continue
        names = [EIGHT_K_ITEMS[i] for i in (items or "").split(",") if i in EIGHT_K_ITEMS]
        out.append({"date": fd, "form": form, "items": names})
    return out


def nearby_filings(day, filings):
    """Filings dated from 3 calendar days before through the bar date (releases often land after the close)."""
    dd = d(day)
    hits = [f for f in filings if 0 <= (dd - d(f["date"])).days <= 3]
    return [f"{f['form']} {f['date']}" + (f" ({', '.join(f['items'])})" if f["items"] else "") for f in hits]


def price_anomalies(bars, mkt_bars, beta, atr, filings, days):
    c = [b["close"] for b in bars]
    v = [b["volume"] for b in bars]
    n = len(c)
    rets = [None] + returns(c)
    mret = {}
    if mkt_bars:
        mc = {b["date"]: b["close"] for b in mkt_bars}
        for i in range(1, n):
            a, b_ = mc.get(bars[i]["date"]), mc.get(bars[i - 1]["date"])
            if a and b_:
                mret[bars[i]["date"]] = a / b_ - 1
    resid = [None] * n
    if beta is not None:
        for i in range(1, n):
            m = mret.get(bars[i]["date"])
            if m is not None:
                resid[i] = rets[i] - beta * m
    events = []
    for i in range(max(61, n - days), n):
        base = rets[i - 60:i]
        mu, sd = mean(base), stdev(base)
        z = (rets[i] - mu) / sd if sd else None
        rb = [x for x in resid[i - 60:i] if x is not None]
        zr = (resid[i] - mean(rb)) / stdev(rb) if resid[i] is not None and stdev(rb) else None
        med_v = statistics.median(v[i - 20:i]) if any(v[i - 20:i]) else 0
        vr = v[i] / med_v if med_v else None
        atr_pct = safe_div(atr[i - 1], c[i - 1]) if atr[i - 1] is not None else None
        gap = bars[i]["open"] / c[i - 1] - 1
        rng = (bars[i]["high"] - bars[i]["low"]) / c[i - 1]
        flags = []
        if z is not None and abs(z) >= 3:
            flags.append("return outlier")
        if zr is not None and abs(zr) >= 3:
            flags.append("stock-specific move")
        if vr is not None and vr >= 3:
            flags.append("volume spike")
        if atr_pct is not None and abs(gap) >= max(0.03, 2 * atr_pct):
            flags.append("gap up" if gap > 0 else "gap down")
        if atr_pct is not None and rng >= 3 * atr_pct:
            flags.append("range expansion")
        if not flags:
            continue
        notes = []
        if z is not None and abs(z) >= 3 and zr is not None and abs(zr) < 2:
            notes.append("mostly market-wide (residual vs market is small)")
        m = mret.get(bars[i]["date"])
        sev = "high" if (z and abs(z) >= 4) or (vr and vr >= 5) or len(flags) >= 3 else (
            "medium" if len(flags) >= 2 or (z and abs(z) >= 3) else "low")
        events.append({"date": bars[i]["date"], "close": c[i], "return": rets[i], "market_return": m,
                       "return_z": z, "residual_z": zr, "volume_ratio": vr, "gap": gap, "flags": flags,
                       "severity": sev, "filings": nearby_filings(bars[i]["date"], filings), "notes": notes})
    return events


def regime_anomalies(tech, rel_mkt, bars):
    out = []
    v = tech["values"]
    if v["vol_20d_ann"] and v["vol_1y_ann"]:
        ratio = v["vol_20d_ann"] / v["vol_1y_ann"]
        if ratio >= 1.75:
            out.append({"type": "volatility regime", "severity": "medium",
                        "detail": f"20d volatility {v['vol_20d_ann']:.1%} is {ratio:.1f}x its 1y level {v['vol_1y_ann']:.1%}"})
        elif ratio <= 0.5:
            out.append({"type": "volatility compression", "severity": "low",
                        "detail": f"20d volatility {v['vol_20d_ann']:.1%} is {ratio:.1f}x its 1y level; often precedes a large move"})
    if rel_mkt:
        c60, c1 = rel_mkt.get("corr_60d"), rel_mkt.get("corr_1y")
        if c60 is not None and c1 is not None and abs(c60 - c1) >= 0.3:
            out.append({"type": "correlation break", "severity": "medium",
                        "detail": f"60d correlation to market {c60:.2f} vs 1y {c1:.2f}"})
        b60, b1 = rel_mkt.get("beta_60d"), rel_mkt.get("beta_1y")
        if b60 is not None and b1 is not None and abs(b1) > 0.1 and abs(b60 / b1 - 1) >= 0.5:
            out.append({"type": "beta drift", "severity": "low", "detail": f"60d beta {b60:.2f} vs 1y {b1:.2f}"})
    if v["pct_from_52w_high"] <= -0.20:
        out.append({"type": "drawdown", "severity": "high" if v["pct_from_52w_high"] <= -0.35 else "medium",
                    "detail": f"{v['pct_from_52w_high']:.1%} below 52-week high"})
    if v["rsi14"] is not None and (v["rsi14"] >= 80 or v["rsi14"] <= 20):
        out.append({"type": "momentum extreme", "severity": "medium", "detail": f"RSI(14) {v['rsi14']:.1f}"})
    stale = (dt.date.today() - d(bars[-1]["date"])).days
    if stale > 5:
        out.append({"type": "stale data", "severity": "high", "detail": f"last price bar is {stale} days old"})
    zero_vol = sum(1 for b in bars[-60:] if not b["volume"])
    if zero_vol > 3:
        out.append({"type": "data gaps", "severity": "low", "detail": f"{zero_vol} zero-volume sessions in last 60"})
    return out


def fundamental_anomalies(f):
    if not f or not f.get("available"):
        return []
    a, out = f["_anomaly_inputs"], []

    def add(kind, sev, detail):
        out.append({"type": kind, "severity": sev, "detail": detail})

    fin = f.get("is_financial")
    if a["ttm_end"] and a["bs_end"] and (d(a["bs_end"]) - d(a["ttm_end"])).days > 45:
        add("TTM lag", "low", f"TTM income figures end {a['ttm_end']} but the balance sheet is {a['bs_end']}; "
            "the latest quarters aren't tagged consistently, so growth and margins are older than the balance sheet")
    if not fin and a["ni"] is not None and a["cfo"] is not None and a["avg_assets"]:
        acc = (a["ni"] - a["cfo"]) / a["avg_assets"]
        if acc > 0.10:
            add("high accruals", "medium", f"(net income - operating cash flow)/assets = {acc:.1%}; earnings not backed by cash")
    if not fin and a["ni"] is not None and a["fcf"] is not None and a["ni"] > 0 and a["fcf"] < 0:
        add("profit without cash", "medium", "TTM net income positive but free cash flow negative")
    if a["rec_growth"] is not None and a["rev_growth"] is not None and a["rec_growth"] - a["rev_growth"] > 0.15:
        add("receivables outpacing sales", "medium",
            f"receivables {a['rec_growth']:+.1%} YoY vs revenue {a['rev_growth']:+.1%}")
    if a["inv_growth"] is not None and a["rev_growth"] is not None and a["inv_growth"] - a["rev_growth"] > 0.15:
        add("inventory build", "medium", f"inventory {a['inv_growth']:+.1%} YoY vs revenue {a['rev_growth']:+.1%}")
    if a["share_growth"] is not None and a["share_growth"] > 0.05:
        add("dilution", "medium", f"shares outstanding {a['share_growth']:+.1%} YoY")
    if a["gm"] is not None and a["gm_prior"] is not None and abs(a["gm"] - a["gm_prior"]) >= 0.05:
        add("gross margin shift", "low", f"gross margin {a['gm']:.1%} vs {a['gm_prior']:.1%} a year earlier")
    if a["equity"] is not None and a["equity"] < 0:
        add("negative equity", "medium", "stockholders' equity is negative (check buybacks vs distress)")
    if f.get("altman") and f["altman"]["zone"] == "distress":
        add("Altman Z distress", "high", f"Z = {f['altman']['z']:.2f} (< 1.81)")
    p = f.get("piotroski")
    if not fin and p and p["out_of"] >= 7 and p["score"] <= 2:
        add("weak Piotroski", "medium", f"F-score {p['score']}/{p['out_of']}")
    lp = f.get("latest_periodic_filing")
    if lp:
        limit = 400 if lp["form"].startswith(("20-F", "40-F")) else 200  # annual-only foreign filers
        age = (dt.date.today() - d(lp["date"])).days
        if age > limit:
            add("late filer", "high", f"latest periodic report {lp['form']} filed {lp['date']} ({age} days ago)")
        elif f.get("latest_filed") and f["latest_filed"] < lp["date"]:
            add("XBRL lag", "low", f"EDGAR financial data runs through the filing of {f['latest_filed']}, but a "
                f"{lp['form']} was filed {lp['date']}; fundamentals are one period behind")
    elif f.get("latest_filed") and (dt.date.today() - d(f["latest_filed"])).days > 200:
        add("stale filings", "medium", f"latest XBRL financial data filed {f['latest_filed']} (> 200 days old)")
    return out


# --------------------------------------------------------------------------- sector ETF

SIC_ETF = [  # (lo, hi, etf) first match wins; see references/benchmarks.md
    (1310, 1389, "XLE"), (2910, 2999, "XLE"), (2830, 2836, "XLV"), (3840, 3851, "XLV"), (5120, 5122, "XLV"),
    (8000, 8099, "XLV"), (2840, 2844, "XLP"), (2000, 2199, "XLP"), (5400, 5499, "XLP"), (5912, 5912, "XLP"),
    (5140, 5199, "XLP"), (3630, 3639, "XLY"), (3570, 3579, "XLK"), (3600, 3699, "XLK"), (7370, 7379, "XLK"),
    (3711, 3711, "XLY"), (3714, 3716, "XLY"), (2200, 2399, "XLY"), (3900, 3999, "XLY"),
    (5200, 5999, "XLY"), (7000, 7099, "XLY"), (7900, 7999, "XLY"), (2700, 2799, "XLC"), (4800, 4899, "XLC"),
    (7800, 7899, "XLC"), (4900, 4999, "XLU"), (6500, 6553, "XLRE"), (6798, 6798, "XLRE"), (6000, 6799, "XLF"),
    (1000, 1499, "XLB"), (2400, 2699, "XLB"), (2800, 2899, "XLB"), (3000, 3399, "XLB"), (1500, 1799, "XLI"),
    (3400, 3569, "XLI"), (3580, 3599, "XLI"), (3700, 3799, "XLI"), (3800, 3839, "XLI"), (4000, 4799, "XLI"),
    (5000, 5119, "XLI"), (8700, 8799, "XLI"), (100, 999, "XLP"),
]


def sector_etf(sic):
    if sic is None:
        return None
    return next((etf for lo, hi, etf in SIC_ETF if lo <= sic <= hi), None)


# --------------------------------------------------------------------------- report

def fp(x, digits=1):
    return "n/a" if x is None else f"{x * 100:+.{digits}f}%"


def fpu(x, digits=1):
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def fn(x, digits=2):
    return "n/a" if x is None else f"{x:,.{digits}f}"


def fx(x):
    return "n/a" if x is None else f"{x:.1f}x"


def fbig(x, ccy=""):
    if x is None:
        return "n/a"
    sign, a = ("-" if x < 0 else ""), abs(x)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{sign}{ccy}{a / div:.2f}{suf}"
    return f"{sign}{ccy}{a:.0f}"


def render_md(r):
    L = []
    s = r["snapshot"]
    L.append(f"# {r['ticker']} — {s['name']}")
    L.append(f"_As of {s['as_of']} · {s['exchange']} · {s['currency']} · generated {r['generated']}_\n")
    L.append("## Snapshot\n")
    L.append("| Price | Day | 52w range | Market cap | Sector (SIC) | Benchmarks |")
    L.append("|---|---|---|---|---|---|")
    mc = r["fundamentals"].get("ratios", {}).get("market_cap") if r["fundamentals"].get("available") else None
    L.append(f"| {fn(s['price'])} | {fp(s['day_change'], 2)} | {fn(s['low_52w'])} – {fn(s['high_52w'])} | "
             f"{fbig(mc)} | {s['sic_description'] or 'n/a'} ({s['sic'] or '—'}) | "
             f"{', '.join(r['benchmarks'])} |\n")

    t = r["technical"]
    v = t["values"]
    tally = {"bullish": 0, "bearish": 0, "neutral": 0}
    for sg in t["signals"]:
        tally[sg["reading"]] += 1
    L.append("## Technical analysis\n")
    L.append(f"Signal tally: **{tally['bullish']} bullish / {tally['bearish']} bearish / {tally['neutral']} neutral**\n")
    L.append("| Indicator | Reading | Detail |\n|---|---|---|")
    for sg in t["signals"]:
        L.append(f"| {sg['indicator']} | {sg['reading']} | {sg['detail']} |")
    L.append("\n| SMA20 | SMA50 | SMA200 | RSI14 | MACD hist | ATR14 (% px) | ADX14 | Vol 20d / 1y |")
    L.append("|---|---|---|---|---|---|---|---|")
    L.append(f"| {fn(v['sma20'])} | {fn(v['sma50'])} | {fn(v['sma200'])} | {fn(v['rsi14'], 1)} | {fn(v['macd_hist'], 3)} | "
             f"{fn(v['atr14'])} ({fpu(v['atr_pct'])}) | {fn(v['adx14'], 1)} | {fpu(v['vol_20d_ann'])} / {fpu(v['vol_1y_ann'])} |\n")
    lv = t["levels"]
    L.append("Support: " + (", ".join(f"{fn(x['level'])} ({x['touches']}x)" for x in lv["support"]) or "none found") +
             " · Resistance: " + (", ".join(f"{fn(x['level'])} ({x['touches']}x)" for x in lv["resistance"]) or "none found (at highs)") + "\n")

    f = r["fundamentals"]
    L.append("## Fundamental analysis\n")
    if not f.get("available"):
        L.append(f"Not available: {f.get('reason')}.\n")
    else:
        ra, tt, bsh = f["ratios"], f["ttm"], f["balance"]
        cc = f["currency"]
        L.append(f"TTM through {f['ttm_period_end']} ({f['ttm_method']}); balance sheet {bsh['as_of']}; "
                 f"latest filing {f['latest_filed']}; reported in {cc}.")
        if not f["price_currency_matches"]:
            L.append(f"**Price currency differs from reporting currency ({cc}); price multiples omitted.**")
        elif f["foreign_private_issuer"]:
            L.append("**Foreign private issuer (20-F/40-F): EDGAR counts ordinary shares, not ADRs, so market cap "
                     "and price multiples are omitted.**")
        if f["is_financial"]:
            L.append("_Financial company: FCF, current ratio, gross margin, Altman Z and the cash-flow red flags don't "
                     "apply; weigh P/B, ROE and net margin instead._")
        L.append("\n| Valuation | | Profitability | | Growth (YoY TTM) | | Health | |")
        L.append("|---|---|---|---|---|---|---|---|")
        rows = [
            ("P/E", fx(ra["pe_ttm"]), "Gross margin", fpu(ra["gross_margin"]), "Revenue", fp(ra["revenue_growth_yoy"]),
             "Debt/Equity", fx(ra["debt_to_equity"])),
            ("P/S", fx(ra["ps_ttm"]), "Operating margin", fpu(ra["operating_margin"]), "Net income",
             fp(ra["net_income_growth_yoy"]), "Current ratio", fx(ra["current_ratio"])),
            ("P/B", fx(ra["pb"]), "Net margin", fpu(ra["net_margin"]), "EPS", fp(ra["eps_growth_yoy"]),
             "Net debt", fbig(ra["net_debt"])),
            ("EV/EBIT", fx(ra["ev_ebit"]), "ROE", fpu(ra["roe"]), "", "", "Cash", fbig(bsh["cash"])),
            ("FCF yield", fpu(ra["fcf_yield"]), "ROA", fpu(ra["roa"]), "", "", "Total debt", fbig(bsh["total_debt"])),
        ]
        for row in rows:
            L.append("| " + " | ".join(row) + " |")
        L.append(f"\nTTM: revenue {fbig(tt['revenue'])}, operating income {fbig(tt['operating_income'])}, "
                 f"net income {fbig(tt['net_income'])}, diluted EPS {fn(tt['eps_diluted'])}, FCF {fbig(tt['fcf'])}. "
                 f"EV {fbig(ra['enterprise_value'])}. Shares: {fbig(bsh['shares'])} ({bsh['shares_source']}).")
        p = f["piotroski"]
        if p:
            L.append(f"\nPiotroski F-score (FY {p['fy']}): **{p['score']}/{p['out_of']}** — " +
                     "; ".join(f"{x['test']} {'✓' if x['pass'] else '✗' if x['pass'] is False else '?'}" for x in p["tests"]))
        if f["altman"]:
            L.append(f"\nAltman Z: **{f['altman']['z']:.2f}** ({f['altman']['zone']} zone)")
        elif f["is_financial"]:
            L.append("\nAltman Z: not meaningful for financials.")
        if f["history"]:
            L.append("\n| FY end | Revenue | Net income | EPS | FCF | Gross margin | Op margin |")
            L.append("|---|---|---|---|---|---|---|")
            for h in f["history"]:
                L.append(f"| {h['fy_end']} | {fbig(h['revenue'])} | {fbig(h['net_income'])} | {fn(h['eps_diluted'])} | "
                         f"{fbig(h['fcf'])} | {fpu(h['gross_margin'])} | {fpu(h['operating_margin'])} |")
        L.append("")

    c = r["comparative"]
    L.append("## Comparative benchmarking\n")
    L.append(f"Risk-free rate: {c['risk_free']:.2%} ({c['risk_free_source']}).\n")
    cols = [w for w, _ in WINDOWS] + ["YTD"]
    L.append("| Symbol | Role | " + " | ".join(cols) + " | Vol 1y | Sharpe | Sortino | Max DD 1y |")
    L.append("|---|---|" + "---|" * (len(cols) + 4))
    for sym, row in c["symbols"].items():
        rs = row["risk"]
        L.append(f"| {sym} | {row['role']} | " + " | ".join(fp(row["returns"].get(w)) for w in cols) +
                 f" | {fpu(rs['vol_1y'])} | {fn(rs['sharpe_1y'])} | {fn(rs['sortino_1y'])} | {fp(rs['max_drawdown_1y'])} |")
    L.append(f"\n| {r['ticker']} vs | Beta 1y | Beta 60d | Corr 1y | Corr 60d | Alpha 1y | Tracking err | Info ratio | Up capture | Down capture |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for sym, rel in c["relative"].items():
        if not rel:
            continue
        L.append(f"| {sym} | {fn(rel['beta_1y'])} | {fn(rel['beta_60d'])} | {fn(rel['corr_1y'])} | {fn(rel['corr_60d'])} | "
                 f"{fp(rel['alpha_1y_ann'])} | {fpu(rel['tracking_error_1y'])} | {fn(rel['info_ratio_1y'])} | "
                 f"{fx(rel['up_capture'])} | {fx(rel['down_capture'])} |")
    pf = c.get("peer_fundamentals")
    if pf:
        keys = [("pe_ttm", "P/E", fx), ("ps_ttm", "P/S", fx), ("ev_ebit", "EV/EBIT", fx), ("gross_margin", "Gross m.", fpu),
                ("operating_margin", "Op m.", fpu), ("roe", "ROE", fpu), ("revenue_growth_yoy", "Rev growth", fp),
                ("debt_to_equity", "D/E", fx), ("fcf_yield", "FCF yield", fpu)]
        L.append("\n| Company | Mkt cap | " + " | ".join(k[1] for k in keys) + " |")
        L.append("|---|---|" + "---|" * len(keys))
        for sym, ratios in pf["rows"].items():
            if ratios is None:
                L.append(f"| {sym} | n/a | " + " | ".join("n/a" for _ in keys) + " |")
                continue
            L.append(f"| {sym} | {fbig(ratios.get('market_cap'))} | " + " | ".join(fmt(ratios.get(k)) for k, _, fmt in keys) + " |")
        L.append(f"| **Peer median** | | " + " | ".join(fmt(pf["median"].get(k)) for k, _, fmt in keys) + " |")
        L.append(f"| **{r['ticker']} percentile** | | " + " | ".join(
            "n/a" if pf["percentile"].get(k) is None else f"{pf['percentile'][k]:.0f}" for k, _, _ in keys) + " |")
        L.append("\n_Percentile = share of peers with a lower value (for P/E, P/S, EV/EBIT, D/E lower is cheaper/safer)._")
    L.append("")

    a = r["anomalies"]
    L.append(f"## Anomaly detection (last {a['window_days']} sessions)\n")
    if a["price_volume"]:
        L.append("| Date | Severity | Return | Market | Return z | Residual z | Vol x median | Gap | Flags | Nearby filings |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for e in sorted(a["price_volume"], key=lambda e: e["date"], reverse=True):
            L.append(f"| {e['date']} | {e['severity']} | {fp(e['return'], 2)} | {fp(e['market_return'], 2)} | "
                     f"{fn(e['return_z'], 1)} | {fn(e['residual_z'], 1)} | {fx(e['volume_ratio'])} | {fp(e['gap'], 2)} | "
                     f"{', '.join(e['flags'] + e['notes'])} | {'; '.join(e['filings']) or '—'} |")
    else:
        L.append("No price/volume anomalies in the window.")
    L.append("")
    for title, key in (("Regime / data", "regime"), ("Fundamental", "fundamental")):
        items = a[key]
        L.append(f"**{title}:** " + ("none" if not items else ""))
        for x in items:
            L.append(f"- [{x['severity']}] {x['type']}: {x['detail']}")
        L.append("")
    L.append("## Data notes\n")
    for n_ in r["notes"]:
        L.append(f"- {n_}")
    return "\n".join(L) + "\n"


def percentile_rank(x, xs):
    xs = [y for y in xs if y is not None]
    if x is None or not xs:
        return None
    return 100 * sum(1 for y in xs if y < x) / len(xs)


def analyze(args):
    ticker = args.ticker.upper()
    notes = []
    tgt = yahoo_chart(ticker, args.range)
    if len(tgt["bars"]) < 60:
        raise DataError(f"only {len(tgt['bars'])} daily bars for {ticker}; need at least 60")
    meta, bars = tgt["meta"], tgt["bars"]
    if len(bars) < 220:
        notes.append(f"Only {len(bars)} daily bars; SMA200 and 1-year stats may be missing or partial.")

    sic = sic_desc = subs = None
    hit = None
    try:
        hit = sec_lookup(ticker)
        if hit:
            subs = sec_submissions(hit[0])
            sic = int(subs["sic"]) if subs.get("sic") else None
            sic_desc = subs.get("sicDescription")
    except DataError as e:
        notes.append(f"SEC lookup failed: {e}")

    sect = (args.sector_etf or sector_etf(sic) or "").upper() or None
    market = args.market.upper()
    benches = [b for b in dict.fromkeys([market, sect]) if b and b != ticker]  # never benchmark against itself
    peers = [p.strip().upper() for p in (args.peers or "").split(",") if p.strip() and p.strip().upper() != ticker]
    rf, rf_src = risk_free_rate()

    series, currencies = {}, {}
    for sym in benches + peers:
        try:
            ch = yahoo_chart(sym, args.range)
            series[sym], currencies[sym] = ch["bars"], ch["meta"].get("currency")
        except DataError as e:
            notes.append(f"Price data unavailable for {sym}: {e}")

    tech = technicals(bars)
    mkt = series.get(market)
    rel = {sym: relative_stats(bars, series[sym], rf) for sym in benches + peers if sym in series}

    comp_syms = {ticker: {"role": "target", "returns": window_returns(bars), "risk": risk_stats(bars, rf)}}
    for sym in benches + peers:
        if sym in series and len(series[sym]) > 30:
            role = "market" if sym == market else "sector" if sym == sect else "peer"
            comp_syms[sym] = {"role": role, "returns": window_returns(series[sym]), "risk": risk_stats(series[sym], rf)}

    if args.no_fundamentals:
        fund = {"available": False, "reason": "skipped (--no-fundamentals)"}
    elif meta.get("instrumentType") not in (None, "EQUITY"):
        fund = {"available": False, "reason": f"instrument type is {meta.get('instrumentType')}, not a company stock"}
    else:
        try:
            fund = fundamentals(ticker, bars[-1]["raw_close"], meta.get("currency"), sic)
        except DataError as e:
            fund = {"available": False, "reason": f"SEC data fetch failed: {e}"}
    if fund.get("available") and subs:
        rec = subs.get("filings", {}).get("recent", {})
        fund["latest_periodic_filing"] = next(
            ({"form": fm, "date": fd} for fm, fd in zip(rec.get("form", []), rec.get("filingDate", []))
             if fm in ("10-K", "10-Q", "20-F", "40-F", "10-K/A", "20-F/A")), None)

    peer_fund = None
    if peers and fund.get("available"):
        rows = {}
        for p in peers:
            pb = series.get(p)
            try:
                psub = sec_lookup(p)
                psic = int(sec_submissions(psub[0]).get("sic") or 0) or None if psub else None
                pf = fundamentals(p, pb[-1]["raw_close"], currencies.get(p), psic) if pb else {"available": False}
            except DataError as e:
                notes.append(f"Fundamentals unavailable for peer {p}: {e}")
                pf = {"available": False}
            rows[p] = pf["ratios"] if pf.get("available") else None
        keys = list(fund["ratios"].keys())
        med = {}
        for k in keys:
            xs = [r_[k] for r_ in rows.values() if r_ and r_.get(k) is not None]
            med[k] = statistics.median(xs) if xs else None
        pct = {k: percentile_rank(fund["ratios"].get(k), [r_.get(k) for r_ in rows.values() if r_]) for k in keys}
        peer_fund = {"rows": {ticker: fund["ratios"], **rows}, "median": med, "percentile": pct}
    elif not peers:
        notes.append("No peers given (--peers); peer valuation table omitted.")

    since = bars[max(0, len(bars) - args.anomaly_days - 5)]["date"]
    filings = filing_events(subs, since) if subs else []
    beta = (rel.get(market) or {}).get("beta_1y")
    anomalies = {
        "window_days": args.anomaly_days,
        "price_volume": price_anomalies(bars, mkt, beta, tech["series"]["atr"], filings, args.anomaly_days),
        "regime": regime_anomalies(tech, rel.get(market), bars),
        "fundamental": fundamental_anomalies(fund),
    }
    if not filings:
        notes.append("No SEC filing dates available; anomalies are not matched to earnings or 8-K events.")

    last = bars[-1]
    prev = bars[-2]["close"]
    notes += [
        "Prices: Yahoo Finance chart API, split/dividend-adjusted daily bars; the latest bar may be intraday.",
        "Fundamentals: SEC EDGAR XBRL company facts (as filed; tags vary by company, so some fields may be n/a).",
        f"Sector ETF: {sect or 'none'} ({'override' if args.sector_etf else 'mapped from SIC ' + str(sic) if sic else 'no SIC'}).",
        "Informational analysis only, not investment advice.",
    ]
    tech.pop("series")
    if fund.get("available"):
        fund.pop("_anomaly_inputs", None)
    return {
        "ticker": ticker, "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "snapshot": {"name": meta.get("longName") or meta.get("shortName") or (hit[1] if hit else ticker),
                     "as_of": last["date"], "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
                     "currency": meta.get("currency"), "instrument_type": meta.get("instrumentType"),
                     "price": last["raw_close"], "day_change": last["close"] / prev - 1,
                     "high_52w": meta.get("fiftyTwoWeekHigh") or tech["values"]["high_52w"],
                     "low_52w": meta.get("fiftyTwoWeekLow") or tech["values"]["low_52w"],
                     "sic": sic, "sic_description": sic_desc,
                     "dividend_ttm": sum(a for dd_, a in tgt["dividends"] if d(dd_) >= d(last["date"]) - dt.timedelta(days=365))},
        "benchmarks": benches, "peers": peers, "technical": tech, "fundamentals": fund,
        "comparative": {"risk_free": rf, "risk_free_source": rf_src, "symbols": comp_syms, "relative": rel,
                        "peer_fundamentals": peer_fund},
        "anomalies": anomalies, "notes": notes,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ticker")
    ap.add_argument("--peers", help="comma-separated peer tickers for side-by-side benchmarking")
    ap.add_argument("--market", default="SPY", help="broad-market benchmark (default SPY)")
    ap.add_argument("--sector-etf", help="override the SIC-mapped sector ETF (e.g. XLC for GOOGL/META)")
    ap.add_argument("--range", default="2y", choices=["1y", "2y", "5y", "10y"], help="price history (default 2y)")
    ap.add_argument("--anomaly-days", type=int, default=63, help="sessions to scan for anomalies (default 63)")
    ap.add_argument("--no-fundamentals", action="store_true")
    ap.add_argument("--format", default="md", choices=["md", "json"])
    ap.add_argument("--out", help="write the report here instead of stdout")
    args = ap.parse_args()
    if not 5 <= args.anomaly_days <= 500:
        ap.error("--anomaly-days must be 5..500")
    try:
        report = analyze(args)
    except DataError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    text = json.dumps(report, indent=2, default=str) if args.format == "json" else render_md(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
