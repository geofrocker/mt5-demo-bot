"""Walk-forward the ER H4 rule on majors and size a vol-normalized basket."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from research import ROOT, START, er_trend, eval_strat, lots, resample_h4, yahoo

PAIRS = [
    ("EURUSD", "EURUSD=X"),
    ("GBPUSD", "GBPUSD=X"),
    ("USDJPY", "USDJPY=X"),
    ("AUDUSD", "AUDUSD=X"),
    ("USDCHF", "USDCHF=X"),
    ("USDCAD", "USDCAD=X"),
    ("NZDUSD", "NZDUSD=X"),
    ("EURJPY", "EURJPY=X"),
]

GATES = {
    "is_pnl": 0.0,
    "oos_pnl": 0.0,
    "oos_pf": 1.2,
    "oos_n": 10,
    "full_n": 20,
    "oos_dd": 15.0,
}

MAX_POSITIONS = 3
MAX_USD_DIR = 2
RISK_PERCENT = 0.5
WARMUP = 80


def _spread(closes: List[float]) -> float:
    return 0.014 if max(closes) > 20 else 0.00014


def _pip(closes: List[float]) -> float:
    return 0.01 if max(closes) > 20 else 0.0001


def usd_dir(symbol: str, side: str) -> int:
    s = symbol.replace(".", "").replace("_", "").upper()
    if len(s) < 6:
        return 0
    base, quote = s[:3], s[3:6]
    if quote == "USD":
        return -1 if side == "buy" else 1
    if base == "USD":
        return 1 if side == "buy" else -1
    return 0


def passes(row: dict) -> Tuple[bool, str]:
    ins, oos, full = row["is"], row["oos"], row["full"]
    if ins["net_pnl"] <= GATES["is_pnl"]:
        return False, f"IS PnL {ins['net_pnl']}"
    if oos["net_pnl"] <= GATES["oos_pnl"]:
        return False, f"OOS PnL {oos['net_pnl']}"
    pf = oos["pf"]
    if pf is None or pf < GATES["oos_pf"]:
        return False, f"OOS PF {pf}"
    if oos["n"] < GATES["oos_n"]:
        return False, f"OOS n={oos['n']} < {GATES['oos_n']}"
    if full["n"] < GATES["full_n"]:
        return False, f"full n={full['n']} < {GATES['full_n']}"
    if oos["max_dd"] > GATES["oos_dd"]:
        return False, f"OOS DD {oos['max_dd']}%"
    return True, "pass"


def _usd_ok(open_pos: Dict[str, dict], symbol: str, side: str) -> bool:
    dnew = usd_dir(symbol, side)
    if dnew == 0:
        return True
    same = sum(1 for p in open_pos.values() if usd_dir(p["symbol"], p["side"]) == dnew)
    return same < MAX_USD_DIR


def simulate_portfolio(books: Dict[str, dict]) -> dict:
    """Shared equity, 0.5% risk, max 3 books, max 2 same-way USD."""
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
            dist = max(a * 2.5, book["pip"] * 8)
            vol = lots(equity, dist, book["pip"])
            if vol <= 0:
                continue
            spread = book["spread"]
            o = book["o"][i]
            entry = o + spread / 2 if side == "buy" else o - spread / 2
            sl = entry - dist if side == "buy" else entry + dist
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

    curve = [(month, eq) for month, eq in sorted(month_last.items())]

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gw, gl = sum(t["pnl"] for t in wins), abs(sum(t["pnl"] for t in losses))
    by_symbol: Dict[str, float] = defaultdict(float)
    n_by: Dict[str, int] = defaultdict(int)
    for t in trades:
        by_symbol[t["symbol"]] += t["pnl"]
        n_by[t["symbol"]] += 1

    seen = []
    prev = START
    for month, eq in curve:
        seen.append((month, round(eq - prev, 2)))
        prev = eq

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
        "pnl_by_symbol": {k: round(v, 2) for k, v in sorted(by_symbol.items())},
        "n_by_symbol": dict(n_by),
        "equity_curve": curve,
        "monthly": seen,
        "trades": trades[-12:],
    }


def scan() -> dict:
    rows = []
    books: Dict[str, dict] = {}
    for symbol, yahoo_sym in PAIRS:
        cache = ROOT / "logs" / f"{symbol.lower()}_h1.json"
        try:
            h1 = yahoo(yahoo_sym, "1h", "2y", cache)
            h4 = resample_h4(h1)
        except Exception as exc:  # noqa: BLE001
            rows.append({"symbol": symbol, "error": str(exc), "enabled": False})
            print(f"{symbol:<8} FAIL {exc}")
            continue
        name = f"{symbol}_H4_er"
        row = eval_strat(name, h4, "H4", WARMUP, er_trend)
        ok, reason = passes(row)
        pack = {
            "symbol": symbol,
            "enabled": ok,
            "reason": reason,
            "is": row["is"],
            "oos": row["oos"],
            "full": row["full"],
        }
        rows.append(pack)
        i, o, f = row["is"], row["oos"], row["full"]
        flag = "ON " if ok else "off"
        print(
            f"{symbol:<8} {flag}  IS {i['net_pnl']:8.1f} PF {str(i['pf']):>5} n={i['n']:3d}  "
            f"OOS {o['net_pnl']:8.1f} PF {str(o['pf']):>5} n={o['n']:3d} DD {o['max_dd']:4.1f}  "
            f"FULL {f['net_pnl']:8.1f} n={f['n']:3d}  {reason}"
        )
        if ok:
            sig, cfg = er_trend(h4)
            closes = [b[4] for b in h4]
            books[symbol] = {
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

    enabled = [r["symbol"] for r in rows if r.get("enabled")]
    portfolio = simulate_portfolio(books) if books else None
    if portfolio:
        print(
            f"\nPORTFOLIO  {', '.join(enabled) or '(none)'}  "
            f"PnL {portfolio['net_pnl']}  PF {portfolio['pf']}  n={portfolio['n']}  "
            f"DD {portfolio['max_dd']}%  blocked_usd={portfolio['blocked_usd']}"
        )

    cfg = {
        "strategy": "er_ema50_h4",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gates": GATES,
        "risk_percent": RISK_PERCENT,
        "max_positions": MAX_POSITIONS,
        "max_usd_dir": MAX_USD_DIR,
        "enabled": enabled,
        "rejected": [
            {"symbol": r["symbol"], "reason": r.get("reason") or r.get("error", "")}
            for r in rows
            if not r.get("enabled")
        ],
    }
    cfg_path = ROOT / "config" / "enabled.json"
    cfg_path.parent.mkdir(exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")

    payload = {"pairs": rows, "portfolio": portfolio, "config": cfg}
    (ROOT / "logs" / "majors.json").write_text(json.dumps(payload, indent=2))
    print("wrote", cfg_path, "and logs/majors.json")
    return payload


if __name__ == "__main__":
    scan()
