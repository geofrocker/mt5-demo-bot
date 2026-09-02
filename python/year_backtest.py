"""Calendar-year slices of the live H4 ER/EMA50 rule on the three enabled pairs."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Tuple

from majors import (
    ATR_STOP_MULT,
    MAX_POSITIONS,
    MAX_USD_DIR,
    RISK_PERCENT,
    WARMUP,
    _pip,
    _spread,
    _usd_ok,
    simulate_portfolio,
)
from research import ROOT, START, TZ, er_trend, lots, resample_h4, simulate, yahoo

PAIRS = [
    ("EURUSD", "EURUSD=X"),
    ("USDCAD", "USDCAD=X"),
    ("EURJPY", "EURJPY=X"),
]


def _slice(bars, year: int):
    t0 = datetime(year, 1, 1, tzinfo=TZ)
    t1 = datetime(year + 1, 1, 1, tzinfo=TZ)
    idx0 = 0
    for i, b in enumerate(bars):
        if b[0] >= t0:
            idx0 = max(0, i - WARMUP)
            break
    idx1 = len(bars)
    for i, b in enumerate(bars):
        if b[0] >= t1:
            idx1 = i
            break
    warm = 0
    slice_bars = bars[idx0:idx1]
    for i, b in enumerate(slice_bars):
        if b[0] >= t0:
            warm = i
            break
    return slice_bars, t0, t1, max(warm, 30)


def _book(h4):
    sig, cfg = er_trend(h4)
    closes = [b[4] for b in h4]
    return {
        "bars": h4,
        "o": [b[1] for b in h4],
        "h": [b[2] for b in h4],
        "l": [b[3] for b in h4],
        "c": closes,
        "sig": sig,
        "atr": cfg["atr"],
        "spread": _spread(closes),
        "pip": _pip(closes),
    }


def _pack(summary, trades) -> dict:
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    by_reason: Dict[str, int] = defaultdict(int)
    for t in trades:
        by_reason[t.reason] += 1
    out = dict(summary)
    out["tp"] = by_reason.get("tp", 0)
    out["sl"] = by_reason.get("sl", 0)
    out["risk_percent"] = RISK_PERCENT
    out["wins"] = len(wins)
    out["losses"] = len(losses)
    return out


def pair_year(symbol: str, h4, year: int) -> dict:
    slice_bars, t0, t1, warm = _slice(h4, year)
    sig, cfg = er_trend(slice_bars)
    cfg = dict(cfg)
    cfg["name"] = f"{symbol}_{year}"
    cfg["risk_percent"] = RISK_PERCENT

    def gated(i):
        ts = slice_bars[i][0]
        if ts < t0 or ts >= t1:
            return None
        return sig(i)

    summary, trades = simulate(slice_bars, "H4", warm, gated, cfg)
    return _pack(summary, trades)


def portfolio_year(full_h4: Dict[str, list], year: int) -> dict:
    books = {}
    for symbol, h4 in full_h4.items():
        slice_bars, _, _, _ = _slice(h4, year)
        if len(slice_bars) <= WARMUP:
            continue
        books[symbol] = _book(slice_bars)
    if not books:
        return {}
    port = simulate_portfolio(books, risk_percent=RISK_PERCENT)
    trades = port.pop("trades", [])
    t0 = datetime(year, 1, 1, tzinfo=TZ)
    t1 = datetime(year + 1, 1, 1, tzinfo=TZ)
    year_trades = []
    for t in trades:
        et = datetime.strptime(t["entry_time"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        if t0 <= et < t1:
            year_trades.append(t)
    # simulate_portfolio currently returns only the last 12 trades; recompute from monthly if needed
    return port


def _simulate_portfolio_all(books: Dict[str, dict], risk_percent: float = RISK_PERCENT) -> dict:
    """Same as majors.simulate_portfolio but keeps every trade and tags the year."""
    groups: Dict[datetime, List[Tuple[str, int]]] = defaultdict(list)
    for symbol, book in books.items():
        for i, bar in enumerate(book["bars"]):
            if i < WARMUP:
                continue
            groups[bar[0]].append((symbol, i))

    equity, peak, dd = START, START, 0.0
    open_pos: Dict[str, dict] = {}
    day_count: Dict[str, Tuple[str, int]] = {}
    trades: List[dict] = []
    month_last: Dict[str, float] = {}
    blocked_usd = blocked_slots = 0

    for ts in sorted(groups):
        day = ts.strftime("%Y-%m-%d")
        for symbol, i in groups[ts]:
            book = books[symbol]
            pos = open_pos.get(symbol)
            if not pos:
                continue
            h, l = book["h"][i], book["l"][i]
            side, entry, sl, tp, vol = pos["side"], pos["entry"], pos["sl"], pos["tp"], pos["lots"]
            exit_px = reason = None
            if side == "buy":
                if l <= sl:
                    exit_px, reason = sl, "sl"
                elif tp is not None and h >= tp:
                    exit_px, reason = tp, "tp"
            else:
                if h >= sl:
                    exit_px, reason = sl, "sl"
                elif tp is not None and l <= tp:
                    exit_px, reason = tp, "tp"
            if exit_px is None:
                continue
            pip = book["pip"]
            signed = (exit_px - entry) if side == "buy" else (entry - exit_px)
            pnl = (signed / pip) * vol * 10.0
            equity += pnl
            trades.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "entry_time": pos["tm"],
                    "exit_time": ts.strftime("%Y-%m-%d %H:%M"),
                    "pnl": round(pnl, 2),
                    "reason": reason,
                    "year": int(pos["tm"][:4]),
                }
            )
            del open_pos[symbol]
            peak = max(peak, equity)
            dd = max(dd, (peak - equity) / peak * 100)

        for symbol, i in groups[ts]:
            if symbol in open_pos:
                continue
            book = books[symbol]
            prev = day_count.get(symbol)
            count = 0 if not prev or prev[0] != day else prev[1]
            if count >= 1:
                continue
            side = book["sig"](i - 1) if i else None
            if side not in ("buy", "sell"):
                continue
            if len(open_pos) >= MAX_POSITIONS:
                blocked_slots += 1
                continue
            if not _usd_ok(open_pos, symbol, side):
                blocked_usd += 1
                continue
            atr_v = book["atr"]
            a = atr_v[i - 1] if atr_v and atr_v[i - 1] else (book["h"][i] - book["l"][i])
            dist = max(a * ATR_STOP_MULT, book["pip"] * 8)
            vol = lots(equity, dist, book["pip"], risk_percent=risk_percent)
            if vol <= 0:
                continue
            spread = book["spread"]
            o = book["o"][i]
            entry = o + spread / 2 if side == "buy" else o - spread / 2
            sl = entry - dist if side == "buy" else entry + dist
            tp = None
            if 2.0 > 0:
                tp = entry + dist * 2.0 if side == "buy" else entry - dist * 2.0
            open_pos[symbol] = {
                "symbol": symbol,
                "side": side,
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "lots": vol,
                "tm": ts.strftime("%Y-%m-%d %H:%M"),
            }
            day_count[symbol] = (day, count + 1)

        month_last[ts.strftime("%Y-%m")] = round(equity, 2)

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gw, gl = sum(t["pnl"] for t in wins), abs(sum(t["pnl"] for t in losses))
    by_year: Dict[int, List[dict]] = defaultdict(list)
    for t in trades:
        by_year[t["year"]].append(t)

    year_rows = []
    for year in sorted(by_year):
        yt = by_year[year]
        yw = [t for t in yt if t["pnl"] > 0]
        yl = [t for t in yt if t["pnl"] < 0]
        ygw, ygl = sum(t["pnl"] for t in yw), abs(sum(t["pnl"] for t in yl))
        y_by = defaultdict(float)
        y_n = defaultdict(int)
        y_tp = y_sl = 0
        for t in yt:
            y_by[t["symbol"]] += t["pnl"]
            y_n[t["symbol"]] += 1
            if t["reason"] == "tp":
                y_tp += 1
            elif t["reason"] == "sl":
                y_sl += 1
        year_rows.append(
            {
                "year": year,
                "n": len(yt),
                "wins": len(yw),
                "wr": round(100 * len(yw) / len(yt), 1) if yt else 0.0,
                "pf": round(ygw / ygl, 2) if ygl else None,
                "net_pnl": round(sum(t["pnl"] for t in yt), 2),
                "tp": y_tp,
                "sl": y_sl,
                "pnl_by_symbol": {k: round(v, 2) for k, v in sorted(y_by.items())},
                "n_by_symbol": dict(y_n),
            }
        )

    return {
        "end_equity": round(equity, 2),
        "net_pnl": round(equity - START, 2),
        "return_pct": round((equity / START - 1) * 100, 2),
        "max_dd": round(dd, 2),
        "n": len(trades),
        "wins": len(wins),
        "wr": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
        "pf": round(gw / gl, 2) if gl else None,
        "blocked_usd": blocked_usd,
        "blocked_slots": blocked_slots,
        "equity_curve": [(m, eq) for m, eq in sorted(month_last.items())],
        "by_year": year_rows,
    }


def main() -> dict:
    full_h4 = {}
    span = {}
    for symbol, ys in PAIRS:
        cache = ROOT / "logs" / f"{symbol.lower()}_h1.json"
        h1 = yahoo(ys, "1h", "2y", cache)
        h4 = resample_h4(h1)
        full_h4[symbol] = h4
        span[symbol] = {"first": h4[0][0].isoformat(), "last": h4[-1][0].isoformat(), "h4": len(h4)}

    years = sorted({b[0].year for h4 in full_h4.values() for b in h4})
    pairs = []
    for symbol, h4 in full_h4.items():
        rows = []
        for year in years:
            row = pair_year(symbol, h4, year)
            row["year"] = year
            row["symbol"] = symbol
            rows.append(row)
            print(
                f"{symbol} {year}  n={row['n']:3d}  WR {row['wr']:5.1f}%  PF {str(row['pf']):>5}  "
                f"PnL {row['net_pnl']:8.1f}  DD {row['max_dd']:4.1f}%  TP/SL {row['tp']}/{row['sl']}"
            )
        pairs.append({"symbol": symbol, "years": rows, "span": span[symbol]})

    isolated_port = []
    for year in years:
        books = {}
        for symbol, h4 in full_h4.items():
            slice_bars, t0, t1, _ = _slice(h4, year)
            if len(slice_bars) <= WARMUP:
                continue
            book = _book(slice_bars)
            # zero signals before the year so warmup does not trade
            raw_sig = book["sig"]

            def gated(i, _sig=raw_sig, _bars=slice_bars, _t0=t0, _t1=t1):
                ts = _bars[i][0]
                if ts < _t0 or ts >= _t1:
                    return None
                return _sig(i)

            book["sig"] = gated
            books[symbol] = book
        port = simulate_portfolio(books, risk_percent=RISK_PERCENT) if books else {}
        port.pop("trades", None)
        port.pop("monthly", None)
        port["year"] = year
        isolated_port.append(port)
        print(
            f"BOOK {year}  n={port.get('n')}  WR {port.get('wr')}  PF {port.get('pf')}  "
            f"PnL {port.get('net_pnl')}  DD {port.get('max_dd')}%"
        )

    books_full = {s: _book(h4) for s, h4 in full_h4.items()}
    continuous = _simulate_portfolio_all(books_full)
    print(
        f"CONTINUOUS  n={continuous['n']}  PnL {continuous['net_pnl']}  PF {continuous['pf']}  "
        f"DD {continuous['max_dd']}%"
    )
    for row in continuous["by_year"]:
        print(f"  attrib {row['year']}  n={row['n']}  PnL {row['net_pnl']}  PF {row['pf']}")

    payload = {
        "rule": "H4 Kaufman ER10>=0.40 + EMA50 close cross; 2.5 ATR stop; 2R TP; 1% risk; 0.10 lot cap",
        "source": "Yahoo Finance H1 resampled to H4",
        "start_equity": START,
        "span": span,
        "years": years,
        "pairs": pairs,
        "isolated_book": isolated_port,
        "continuous_book": continuous,
    }
    out = ROOT / "logs" / "year_backtest.json"
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print("wrote", out)
    return payload


if __name__ == "__main__":
    main()
