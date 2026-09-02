"""Search new entries without touching the live enable-list.

Protocol (anti-curve-fit):
1. Freeze the exit that already won: 2.5xATR stop, 2R target, no trail.
2. Freeze the walk-forward cut (OOS from 2025-09-01) and the majors gates.
3. Rank on OOS, not full-sample PnL. A candidate must also be profitable in-sample
   so we are not mining one lucky year.
4. Do not write config/enabled.json. Promote only after a second look and a demo run.
5. Gold / indices / stocks use real contract specs (see OTHER), not the FX $10/pip shortcut.

Live baseline to beat: EURUSD / USDCAD / EURJPY on H4, ER(10, 0.40) + EMA50.
"""
from __future__ import annotations

import json
import sys
from typing import Callable, Dict, List, Tuple

from majors import GATES, PAIRS, passes
from research import ROOT, er_trend, eval_strat, resample_h4, yahoo

EXTRA = [
    ("EURGBP", "EURGBP=X"),
    ("GBPJPY", "GBPJPY=X"),
    ("AUDJPY", "AUDJPY=X"),
]

def search_pass(row: dict) -> tuple:
    ok, reason = passes(row)
    if not ok:
        return ok, reason
    if row["is"]["n"] < 10:
        return False, f"IS n={row['is']['n']} < 10"
    return True, "pass"


ER_GRID = [(10, 0.40), (8, 0.40), (12, 0.40), (10, 0.35), (10, 0.45)]
TFS = (
    ("H1", 200, False),
    ("H4", 80, True),
    ("D1", 220, False),
)

# 0.5% risk, 1R = that dollar amount. Not the FX $10/pip formula.
OTHER = [
    {
        "symbol": "XAUUSD",
        "yahoo": "GC=F",
        "kind": "gold 100oz ($1 per 0.01 per 1.00 lot)",
        "spec": {"pip": 0.01, "pip_value": 1.0, "min_lot": 0.01, "max_lot": 0.50, "lot_step": 0.01, "spread": 0.40},
    },
    {
        "symbol": "US500",
        "yahoo": "ES=F",
        "kind": "S&P CFD $1 per point per 1.00 lot",
        "spec": {"pip": 0.25, "pip_value": 0.25, "min_lot": 0.01, "max_lot": 5.0, "lot_step": 0.01, "spread": 0.50},
    },
    {
        "symbol": "NAS100",
        "yahoo": "NQ=F",
        "kind": "Nasdaq CFD $1 per point per 1.00 lot",
        "spec": {"pip": 0.25, "pip_value": 0.25, "min_lot": 0.01, "max_lot": 5.0, "lot_step": 0.01, "spread": 1.00},
    },
    {
        "symbol": "US30",
        "yahoo": "YM=F",
        "kind": "Dow CFD $1 per point per 1.00 lot",
        "spec": {"pip": 1.0, "pip_value": 1.0, "min_lot": 0.01, "max_lot": 5.0, "lot_step": 0.01, "spread": 2.00},
    },
    {
        "symbol": "NVDA",
        "yahoo": "NVDA",
        "kind": "shares ($1 per $1 per share)",
        "spec": {"pip": 0.01, "pip_value": 0.01, "min_lot": 1.0, "max_lot": 200.0, "lot_step": 1.0, "spread": 0.05},
    },
]


def _load(symbol: str, yahoo_sym: str, tf: str, from_h1: bool):
    if tf == "D1":
        cache = ROOT / "logs" / f"{symbol.lower()}_d1.json"
        return yahoo(yahoo_sym, "1d", "10y", cache)
    h1_cache = ROOT / "logs" / f"{symbol.lower()}_h1.json"
    h1 = yahoo(yahoo_sym, "1h", "2y", h1_cache)
    if tf == "H1":
        return h1
    if from_h1 and tf == "H4":
        return resample_h4(h1)
    raise ValueError(tf)


def main() -> None:
    universe: List[Tuple[str, str]] = list(PAIRS) + list(EXTRA)
    rows = []
    errors = []
    print("=== entry search (exit frozen at 2.5 ATR / 2R) ===")
    print(f"gates IS>0 IS n>=10 OOS>0 OOS PF>={GATES['oos_pf']} OOS n>={GATES['oos_n']} full n>={GATES['full_n']} OOS DD<={GATES['oos_dd']}%")
    for symbol, ysym in universe:
        for tf, warm, from_h1 in TFS:
            try:
                bars = _load(symbol, ysym, tf, from_h1)
            except Exception as exc:  # noqa: BLE001
                errors.append({"symbol": symbol, "tf": tf, "error": str(exc)})
                print(f"{symbol:<8} {tf} FAIL {exc}")
                continue
            if len(bars) < warm + 40:
                print(f"{symbol:<8} {tf} skip short n={len(bars)}")
                continue
            for n, emin in ER_GRID:
                name = f"{symbol}_{tf}_er{n}_{emin}"
                factory: Callable = lambda b, nn=n, ee=emin: er_trend(b, nn, ee)
                try:
                    row = eval_strat(name, bars, tf, warm, factory)
                except Exception as exc:  # noqa: BLE001
                    print(f"{name:<28} FAIL {exc}")
                    continue
                ok, reason = search_pass(row)
                pack = {
                    "name": name,
                    "symbol": symbol,
                    "tf": tf,
                    "er_n": n,
                    "er_min": emin,
                    "live_rule": tf == "H4" and n == 10 and abs(emin - 0.40) < 1e-9,
                    "pass": ok,
                    "reason": reason,
                    "is": row["is"],
                    "oos": row["oos"],
                    "full": row["full"],
                }
                rows.append(pack)
                flag = "PASS" if ok else "----"
                o, i = row["oos"], row["is"]
                print(
                    f"{flag} {name:<28} IS {i['net_pnl']:8.1f} PF {str(i['pf']):>5} n={i['n']:3d}  "
                    f"OOS {o['net_pnl']:8.1f} PF {str(o['pf']):>5} n={o['n']:3d} DD {o['max_dd']:4.1f}  {reason}"
                )

    winners = [r for r in rows if r["pass"]]
    winners.sort(key=lambda r: r["oos"]["net_pnl"], reverse=True)
    print("\n=== OOS winners (same gates as live enable-list) ===")
    if not winners:
        print("none")
    for r in winners:
        live = "  LIVE-RULE" if r["live_rule"] else ""
        o = r["oos"]
        print(
            f"{r['name']:<28} OOS {o['net_pnl']:8.1f} PF {o['pf']} n={o['n']} WR {o['wr']} DD {o['max_dd']}%{live}"
        )

    live = [r for r in rows if r["live_rule"] and r["symbol"] in ("EURUSD", "USDCAD", "EURJPY")]
    print("\n=== live H4 ER10/0.40 baseline ===")
    for r in live:
        print(f"{r['symbol']:<8} pass={r['pass']} OOS {r['oos']['net_pnl']} PF {r['oos']['pf']} n={r['oos']['n']}")

    payload = {
        "protocol": {
            "exit": "2.5 ATR stop, 2R TP, no trail",
            "oos_from": "2025-09-01",
            "gates": GATES,
            "writes_enabled_json": False,
        },
        "winners": [
            {
                "name": r["name"],
                "symbol": r["symbol"],
                "tf": r["tf"],
                "er_n": r["er_n"],
                "er_min": r["er_min"],
                "live_rule": r["live_rule"],
                "is": r["is"],
                "oos": r["oos"],
                "full": r["full"],
            }
            for r in winners
        ],
        "all": [
            {
                "name": r["name"],
                "symbol": r["symbol"],
                "tf": r["tf"],
                "er_n": r["er_n"],
                "er_min": r["er_min"],
                "live_rule": r["live_rule"],
                "pass": r["pass"],
                "reason": r["reason"],
                "is_pnl": r["is"]["net_pnl"],
                "oos_pnl": r["oos"]["net_pnl"],
                "oos_pf": r["oos"]["pf"],
                "oos_n": r["oos"]["n"],
                "oos_dd": r["oos"]["max_dd"],
            }
            for r in rows
        ],
        "errors": errors,
    }
    out = ROOT / "logs" / "entry_search.json"
    out.write_text(json.dumps(payload, indent=2))
    print("wrote", out, "candidates", len(winners), "tested", len(rows))


def _factory(n, emin, spec: Dict):
    def factory(b, nn=n, ee=emin, extra=spec):
        sig, cfg = er_trend(b, nn, ee)
        cfg = dict(cfg)
        cfg.update(extra)
        return sig, cfg

    return factory


def search_other() -> None:
    rows = []
    errors = []
    print("=== gold / indices / NVDA (exit frozen at 2.5 ATR / 2R, 0.5% risk) ===")
    print("sizing uses contract specs, not FX $10/pip")
    print(f"gates IS>0 IS n>=10 OOS>0 OOS PF>={GATES['oos_pf']} OOS n>={GATES['oos_n']} full n>={GATES['full_n']} OOS DD<={GATES['oos_dd']}%")
    for mkt in OTHER:
        symbol, ysym, spec = mkt["symbol"], mkt["yahoo"], mkt["spec"]
        print(f"-- {symbol}  {mkt['kind']}")
        for tf, warm, from_h1 in TFS:
            try:
                bars = _load(symbol, ysym, tf, from_h1)
            except Exception as exc:  # noqa: BLE001
                errors.append({"symbol": symbol, "tf": tf, "error": str(exc)})
                print(f"{symbol:<8} {tf} FAIL {exc}")
                continue
            if len(bars) < warm + 40:
                print(f"{symbol:<8} {tf} skip short n={len(bars)}")
                continue
            for n, emin in ER_GRID:
                name = f"{symbol}_{tf}_er{n}_{emin}"
                try:
                    row = eval_strat(name, bars, tf, warm, _factory(n, emin, spec))
                except Exception as exc:  # noqa: BLE001
                    print(f"{name:<28} FAIL {exc}")
                    continue
                ok, reason = search_pass(row)
                pack = {
                    "name": name,
                    "symbol": symbol,
                    "tf": tf,
                    "er_n": n,
                    "er_min": emin,
                    "kind": mkt["kind"],
                    "live_rule": tf == "H4" and n == 10 and abs(emin - 0.40) < 1e-9,
                    "pass": ok,
                    "reason": reason,
                    "is": row["is"],
                    "oos": row["oos"],
                    "full": row["full"],
                }
                rows.append(pack)
                flag = "PASS" if ok else "----"
                o, i = row["oos"], row["is"]
                print(
                    f"{flag} {name:<28} IS {i['net_pnl']:8.1f} PF {str(i['pf']):>5} n={i['n']:3d}  "
                    f"OOS {o['net_pnl']:8.1f} PF {str(o['pf']):>5} n={o['n']:3d} DD {o['max_dd']:4.1f}  {reason}"
                )

    winners = [r for r in rows if r["pass"]]
    winners.sort(key=lambda r: r["oos"]["net_pnl"], reverse=True)
    print("\n=== OOS winners ===")
    if not winners:
        print("none")
    for r in winners:
        live = "  LIVE-KNOB" if r["live_rule"] else ""
        o = r["oos"]
        print(
            f"{r['name']:<28} OOS {o['net_pnl']:8.1f} PF {o['pf']} n={o['n']} WR {o['wr']} DD {o['max_dd']}%{live}"
        )
    print("\n=== same knob as the FX EA (H4 ER10/0.40) ===")
    for r in rows:
        if r["live_rule"]:
            print(
                f"{r['symbol']:<8} pass={r['pass']}  IS {r['is']['net_pnl']} n={r['is']['n']}  "
                f"OOS {r['oos']['net_pnl']} PF {r['oos']['pf']} n={r['oos']['n']}  {r['reason']}"
            )

    payload = {
        "protocol": {
            "exit": "2.5 ATR stop, 2R TP, no trail",
            "oos_from": "2025-09-01",
            "gates": GATES,
            "writes_enabled_json": False,
            "sizing": [ {"symbol": m["symbol"], "kind": m["kind"], "spec": m["spec"]} for m in OTHER ],
        },
        "winners": [
            {
                "name": r["name"],
                "symbol": r["symbol"],
                "tf": r["tf"],
                "er_n": r["er_n"],
                "er_min": r["er_min"],
                "kind": r["kind"],
                "live_rule": r["live_rule"],
                "is": r["is"],
                "oos": r["oos"],
                "full": r["full"],
            }
            for r in winners
        ],
        "all": [
            {
                "name": r["name"],
                "symbol": r["symbol"],
                "tf": r["tf"],
                "er_n": r["er_n"],
                "er_min": r["er_min"],
                "live_rule": r["live_rule"],
                "pass": r["pass"],
                "reason": r["reason"],
                "is_pnl": r["is"]["net_pnl"],
                "oos_pnl": r["oos"]["net_pnl"],
                "oos_pf": r["oos"]["pf"],
                "oos_n": r["oos"]["n"],
                "oos_dd": r["oos"]["max_dd"],
            }
            for r in rows
        ],
        "errors": errors,
    }
    out = ROOT / "logs" / "entry_search_other.json"
    out.write_text(json.dumps(payload, indent=2))
    print("wrote", out, "candidates", len(winners), "tested", len(rows))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("other", "gold", "markets"):
        search_other()
    else:
        main()
