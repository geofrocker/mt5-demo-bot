"""Walk-forward the lab catalog. Does not write config/enabled.json. Does not touch the live EA.

Protocol (same as entry_search):
1. Exit frozen: 2.5xATR stop, 2R target, no trail.
2. OOS from 2025-09-01. Rank on OOS, not full-sample PnL.
3. Same majors gates. Promote nothing automatically.
"""
from __future__ import annotations

import json
from typing import List, Tuple

from entry_search import EXTRA, _load, search_pass
from lab_signals import CATALOG
from majors import GATES
from research import ROOT, eval_strat

LIVE = ("EURUSD", "USDCAD", "EURJPY")
# H4 only: that is the live timeframe. Add H1/D1 later if a candidate deserves it.
TF = ("H4", 80, True)


def _universe() -> List[Tuple[str, str]]:
    from majors import PAIRS

    wanted = set(LIVE)
    out = [(s, y) for s, y in PAIRS if s in wanted]
    out.extend(EXTRA)
    return out


def main() -> None:
    rows = []
    errors = []
    tf, warm, from_h1 = TF
    print("=== lab search (exit frozen at 2.5 ATR / 2R, live EA not touched) ===")
    print("writes_enabled_json=false")
    print(
        f"gates IS>0 IS n>=10 OOS>0 OOS PF>={GATES['oos_pf']} OOS n>={GATES['oos_n']} "
        f"full n>={GATES['full_n']} OOS DD<={GATES['oos_dd']}%"
    )
    for symbol, ysym in _universe():
        try:
            bars = _load(symbol, ysym, tf, from_h1)
        except Exception as exc:  # noqa: BLE001
            errors.append({"symbol": symbol, "tf": tf, "error": str(exc)})
            print(f"{symbol:<8} {tf} FAIL {exc}")
            continue
        if len(bars) < warm + 40:
            print(f"{symbol:<8} {tf} skip short n={len(bars)}")
            continue
        for key, label, factory, live_base in CATALOG:
            name = f"{symbol}_{tf}_{key}"
            try:
                row = eval_strat(name, bars, tf, warm, factory)
            except Exception as exc:  # noqa: BLE001
                print(f"{name:<36} FAIL {exc}")
                continue
            ok, reason = search_pass(row)
            pack = {
                "name": name,
                "symbol": symbol,
                "tf": tf,
                "key": key,
                "label": label,
                "live_baseline": live_base and symbol in LIVE,
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
                f"{flag} {name:<36} IS {i['net_pnl']:8.1f} PF {str(i['pf']):>5} n={i['n']:3d}  "
                f"OOS {o['net_pnl']:8.1f} PF {str(o['pf']):>5} n={o['n']:3d} DD {o['max_dd']:4.1f}  {reason}"
            )

    winners = [r for r in rows if r["pass"]]
    winners.sort(key=lambda r: r["oos"]["net_pnl"], reverse=True)
    print("\n=== OOS winners ===")
    if not winners:
        print("none")
    for r in winners:
        tag = "  LIVE-BASELINE" if r["live_baseline"] else ""
        o = r["oos"]
        print(
            f"{r['name']:<36} OOS {o['net_pnl']:8.1f} PF {o['pf']} n={o['n']} "
            f"WR {o['wr']} DD {o['max_dd']}%{tag}"
        )

    print("\n=== live baseline (ER10/0.40 + EMA50 on the three enabled pairs) ===")
    for r in rows:
        if r["live_baseline"]:
            print(
                f"{r['symbol']:<8} pass={r['pass']} OOS {r['oos']['net_pnl']} "
                f"PF {r['oos']['pf']} n={r['oos']['n']}  {r['reason']}"
            )

    payload = {
        "protocol": {
            "exit": "2.5 ATR stop, 2R TP, no trail",
            "oos_from": "2025-09-01",
            "gates": GATES,
            "writes_enabled_json": False,
            "touches_live_ea": False,
        },
        "catalog": [{"key": k, "label": lab, "live_baseline": live} for k, lab, _, live in CATALOG],
        "winners": [
            {
                "name": r["name"],
                "symbol": r["symbol"],
                "key": r["key"],
                "live_baseline": r["live_baseline"],
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
                "key": r["key"],
                "live_baseline": r["live_baseline"],
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
    out = ROOT / "logs" / "lab_search.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print("wrote", out, "candidates", len(winners), "tested", len(rows))


if __name__ == "__main__":
    main()
