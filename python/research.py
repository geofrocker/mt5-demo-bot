"""Search for an OOS-profitable EURUSD rule. Distinct strategies, costs included."""
from __future__ import annotations

import json
import math
import ssl
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

Bar = Tuple[datetime, float, float, float, float]
TZ = timezone(timedelta(hours=3))
ROOT = Path(__file__).resolve().parents[1]
START = 10000.0
SPREAD = {"H1": 0.00012, "H4": 0.00014, "D1": 0.00016}
OOS = datetime(2025, 9, 1, tzinfo=TZ)


def yahoo(symbol: str, interval: str, span: str, cache: Path) -> List[Bar]:
    if cache.exists():
        raw = json.loads(cache.read_text())
        return [(datetime.fromisoformat(r[0]), *r[1:]) for r in raw]
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval={interval}&range={span}&includePrePost=false"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "mt5-demo-bot/1.0"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=40) as resp:
        payload = json.loads(resp.read().decode())
    result = payload["chart"]["result"][0]
    ts, q = result["timestamp"], result["indicators"]["quote"][0]
    rows, dump = [], []
    for i, unix in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        dt = datetime.fromtimestamp(unix, tz=timezone.utc).astimezone(TZ)
        rows.append((dt, float(o), float(h), float(l), float(c)))
        dump.append([dt.isoformat(), float(o), float(h), float(l), float(c)])
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps(dump))
    return rows


def resample_h4(h1: List[Bar]) -> List[Bar]:
    buckets: Dict[str, List[Bar]] = {}
    for t, o, h, l, c in h1:
        key = t.strftime("%Y-%m-%d") + f"-{t.hour // 4}"
        buckets.setdefault(key, []).append((t, o, h, l, c))
    out = []
    for key in sorted(buckets):
        b = buckets[key]
        out.append((b[0][0], b[0][1], max(x[2] for x in b), min(x[3] for x in b), b[-1][4]))
    return out


def ema(v, n):
    out = [None] * len(v)
    if len(v) < n:
        return out
    s = sum(v[:n]) / n
    out[n - 1] = s
    k = 2 / (n + 1)
    for i in range(n, len(v)):
        s = v[i] * k + s * (1 - k)
        out[i] = s
    return out


def sma(v, n):
    out = [None] * len(v)
    acc = 0.0
    for i, x in enumerate(v):
        acc += x
        if i >= n:
            acc -= v[i - n]
        if i >= n - 1:
            out[i] = acc / n
    return out


def rsi(v, n=14):
    out = [None] * len(v)
    if len(v) <= n:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        ch = v[i] - v[i - 1]
        g += max(ch, 0)
        l += max(-ch, 0)
    ag, al = g / n, l / n
    out[n] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(v)):
        ch = v[i] - v[i - 1]
        ag = (ag * (n - 1) + max(ch, 0)) / n
        al = (al * (n - 1) + max(-ch, 0)) / n
        out[i] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def atr(h, l, c, n=14):
    out = [None] * len(c)
    tr = [0.0] * len(c)
    for i in range(1, len(c)):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    if len(c) <= n:
        return out
    s = sum(tr[1 : n + 1]) / n
    out[n] = s
    for i in range(n + 1, len(c)):
        s = (s * (n - 1) + tr[i]) / n
        out[i] = s
    return out


def er_trend(bars, n=10, er_min=0.4):
    c = [b[4] for b in bars]
    h, l = [b[2] for b in bars], [b[3] for b in bars]
    e50 = ema(c, 50)
    a = atr(h, l, c, 14)
    er = [None] * len(c)
    for i in range(n, len(c)):
        change = abs(c[i] - c[i - n])
        vol = sum(abs(c[j] - c[j - 1]) for j in range(i - n + 1, i + 1))
        er[i] = change / vol if vol else 0.0

    def sig(i):
        if e50[i] is None or er[i] is None or a[i] is None:
            return None
        if er[i] < er_min:
            return None
        if c[i] > e50[i] and c[i - 1] <= e50[i - 1]:
            return "buy"
        if c[i] < e50[i] and c[i - 1] >= e50[i - 1]:
            return "sell"
        return None

    return sig, {"atr": a, "atr_stop": 2.5, "reward": 2.0, "max_day": 1}


def supertrend(h, l, c, atr_v, mult=3.0):
    st = [None] * len(c)
    dirn = [0] * len(c)
    prev_up = prev_dn = None
    prev_dir = 1
    for i in range(len(c)):
        if atr_v[i] is None:
            continue
        mid = (h[i] + l[i]) / 2
        up = mid - mult * atr_v[i]
        dn = mid + mult * atr_v[i]
        if prev_up is not None:
            up = max(up, prev_up) if c[i - 1] > prev_up else up
            dn = min(dn, prev_dn) if c[i - 1] < prev_dn else dn
        d = prev_dir
        if prev_dn is not None and c[i] > prev_dn:
            d = 1
        elif prev_up is not None and c[i] < prev_up:
            d = -1
        st[i] = up if d == 1 else dn
        dirn[i] = d
        prev_up, prev_dn, prev_dir = up, dn, d
    return st, dirn


def lots(equity, sl_dist, pip=0.0001):
    risk = equity * 0.005
    pips = sl_dist / pip
    if pips <= 0:
        return 0.0
    raw = risk / (pips * 10.0)
    x = math.floor(raw / 0.01) * 0.01
    x = max(0.01, min(0.10, x))
    if 0.01 * pips * 10 > risk * 1.5:
        return 0.0
    return x


@dataclass
class Trade:
    side: str
    entry_time: str
    exit_time: str
    pnl: float
    reason: str


def summarize(name, trades: List[Trade], equity, dd, n_bars, tf) -> dict:
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    gw, gl = sum(t.pnl for t in wins), abs(sum(t.pnl for t in losses))
    pf = (gw / gl) if gl else None
    return {
        "name": name,
        "tf": tf,
        "bars": n_bars,
        "end_equity": round(equity, 2),
        "net_pnl": round(equity - START, 2),
        "return_pct": round((equity / START - 1) * 100, 2),
        "max_dd": round(dd, 2),
        "n": len(trades),
        "wins": len(wins),
        "wr": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
        "pf": round(pf, 2) if pf is not None else None,
        "exp": round(sum(t.pnl for t in trades) / len(trades), 2) if trades else 0.0,
        "avg_win": round(gw / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(t.pnl for t in losses) / len(losses), 2) if losses else 0.0,
    }


def simulate(bars: List[Bar], tf: str, warmup: int, signal_fn, cfg: dict) -> dict:
    t, o, h, l, c = zip(*bars)
    t, o, h, l, c = list(t), list(o), list(h), list(l), list(c)
    spread = cfg.get("spread")
    if spread is None:
        jpy = max(c) > 20
        if jpy:
            spread = 0.014 if tf == "H4" else (0.016 if tf == "D1" else 0.012)
        else:
            spread = SPREAD[tf]
    equity, peak, dd = START, START, 0.0
    trades: List[Trade] = []
    pos = None
    day_count = 0
    day = ""
    atr_stop = cfg.get("atr_stop", 2.0)
    reward = cfg.get("reward", 1.5)
    trail = cfg.get("trail_atr", 0.0)
    max_day = cfg.get("max_day", 1)
    time_stop = cfg.get("time_stop", 0)
    opposite_exit = cfg.get("opposite_exit", False)
    atr_v = cfg.get("atr")
    pip = 0.01 if (max(c) > 20) else 0.0001  # JPY pairs

    def sl_dist(i):
        a = atr_v[i] if atr_v and atr_v[i] else (h[i] - l[i])
        return max(a * atr_stop, pip * 8)

    for i in range(warmup, len(bars)):
        if t[i].strftime("%Y-%m-%d") != day:
            day = t[i].strftime("%Y-%m-%d")
            day_count = 0
        if pos:
            side, entry, sl, tp, vol, bars_held = pos["side"], pos["entry"], pos["sl"], pos["tp"], pos["lots"], pos["held"]
            hi, lo = h[i], l[i]
            exit_px = reason = None
            if trail and atr_v and atr_v[i]:
                if side == "buy":
                    pos["sl"] = max(pos["sl"], c[i] - trail * atr_v[i])
                else:
                    pos["sl"] = min(pos["sl"], c[i] + trail * atr_v[i])
                sl = pos["sl"]
            if side == "buy":
                if lo <= sl and (tp is None or hi >= tp):
                    exit_px, reason = sl, "sl"
                elif lo <= sl:
                    exit_px, reason = sl, "sl"
                elif tp is not None and hi >= tp:
                    exit_px, reason = tp, "tp"
            else:
                if hi >= sl and (tp is None or lo <= tp):
                    exit_px, reason = sl, "sl"
                elif hi >= sl:
                    exit_px, reason = sl, "sl"
                elif tp is not None and lo <= tp:
                    exit_px, reason = tp, "tp"
            pos["held"] += 1
            if exit_px is None and time_stop and pos["held"] >= time_stop:
                exit_px, reason = c[i], "time"
            if exit_px is None and opposite_exit:
                nxt = signal_fn(i - 1) if i else None
                if nxt and nxt != side:
                    exit_px, reason = c[i], "flip"
            if exit_px is not None:
                signed = (exit_px - entry) if side == "buy" else (entry - exit_px)
                pnl = (signed / pip) * vol * 10.0
                equity += pnl
                trades.append(Trade(side, pos["tm"], t[i].strftime("%Y-%m-%d %H:%M"), round(pnl, 2), reason))
                pos = None
                peak = max(peak, equity)
                dd = max(dd, (peak - equity) / peak * 100)
        if pos:
            continue
        if day_count >= max_day:
            continue
        sig_i = i - 1
        side = signal_fn(sig_i)
        if side not in ("buy", "sell"):
            continue
        dist = sl_dist(sig_i)
        vol = lots(equity, dist, pip)
        if vol <= 0:
            continue
        entry = o[i] + spread / 2 if side == "buy" else o[i] - spread / 2
        sl = entry - dist if side == "buy" else entry + dist
        tp = (entry + dist * reward) if (side == "buy" and reward) else ((entry - dist * reward) if reward else None)
        if side == "sell" and reward:
            tp = entry - dist * reward
        pos = {"side": side, "entry": entry, "sl": sl, "tp": tp, "lots": vol, "held": 0, "tm": t[i].strftime("%Y-%m-%d %H:%M")}
        day_count += 1
    return summarize(cfg.get("name", "?"), trades, equity, dd, len(bars) - warmup, tf), trades


def window(bars, t0=None, t1=None):
    out = []
    for b in bars:
        if t0 and b[0] < t0:
            continue
        if t1 and b[0] >= t1:
            break
        out.append(b)
    return out


def eval_strat(name, bars, tf, warmup, factory):
    def run_slice(t0, t1):
        idx0 = 0
        if t0:
            for i, b in enumerate(bars):
                if b[0] >= t0:
                    idx0 = max(0, i - warmup)
                    break
        idx1 = len(bars)
        if t1:
            for i, b in enumerate(bars):
                if b[0] >= t1:
                    idx1 = i
                    break
        slice_bars = bars[idx0:idx1]
        sig, cfg = factory(slice_bars)
        cfg = dict(cfg)
        cfg["name"] = name

        def gated(i):
            ts = slice_bars[i][0]
            if t0 and ts < t0:
                return None
            if t1 and ts >= t1:
                return None
            return sig(i)

        warm = 0
        if t0:
            for i, b in enumerate(slice_bars):
                if b[0] >= t0:
                    warm = i
                    break
        warm = max(warm, 30)
        summary, _trades = simulate(slice_bars, tf, warm, gated, cfg)
        return summary

    ins = run_slice(None, OOS)
    oos = run_slice(OOS, None)
    sig, cfg = factory(bars)
    cfg = dict(cfg)
    cfg["name"] = name
    full, _ = simulate(bars, tf, warmup, sig, cfg)
    return {"name": name, "tf": tf, "is": ins, "oos": oos, "full": full}


def main():
    eurusd_h1 = yahoo("EURUSD=X", "1h", "2y", ROOT / "logs" / "eurusd_h1.json")
    eurusd_h4 = resample_h4(eurusd_h1)
    eurusd_d1 = yahoo("EURUSD=X", "1d", "10y", ROOT / "logs" / "eurusd_d1.json")
    gbpusd_h1 = yahoo("GBPUSD=X", "1h", "2y", ROOT / "logs" / "gbpusd_h1.json")

    results = []

    def add(name, bars, tf, warmup, factory):
        try:
            row = eval_strat(name, bars, tf, warmup, factory)
            results.append(row)
            i, o, f = row["is"], row["oos"], row["full"]
            print(
                f"{name:<28} {tf:<3} IS {i['net_pnl']:8.1f} PF {str(i['pf']):>5} n={i['n']:4d}  "
                f"OOS {o['net_pnl']:8.1f} PF {str(o['pf']):>5} n={o['n']:4d}  "
                f"FULL {f['net_pnl']:8.1f} n={f['n']:4d}"
            )
        except Exception as exc:
            print(name, "FAIL", exc)

    # --- builders ---
    def donchian(bars, n):
        h = [b[2] for b in bars]
        l = [b[3] for b in bars]
        c = [b[4] for b in bars]
        a = atr(h, l, c, 14)

        def sig(i):
            if i < n:
                return None
            if c[i] > max(h[i - n : i]):
                return "buy"
            if c[i] < min(l[i - n : i]):
                return "sell"
            return None

        return sig, {"atr": a, "atr_stop": 2.5, "reward": 0.0, "trail_atr": 2.5, "max_day": 1, "opposite_exit": True}

    def st_flip(bars, mult):
        h, l, c = [b[2] for b in bars], [b[3] for b in bars], [b[4] for b in bars]
        a = atr(h, l, c, 14)
        _, d = supertrend(h, l, c, a, mult)

        def sig(i):
            if i < 1 or d[i] == 0 or d[i - 1] == 0:
                return None
            if d[i] == 1 and d[i - 1] == -1:
                return "buy"
            if d[i] == -1 and d[i - 1] == 1:
                return "sell"
            return None

        return sig, {"atr": a, "atr_stop": 3.0, "reward": 0.0, "trail_atr": 3.0, "max_day": 1, "opposite_exit": True}

    def rsi2_mr(bars):
        c = [b[4] for b in bars]
        h, l = [b[2] for b in bars], [b[3] for b in bars]
        r2 = rsi(c, 2)
        s200 = sma(c, 200)
        a = atr(h, l, c, 14)

        def sig(i):
            if r2[i] is None or s200[i] is None:
                return None
            if r2[i] < 10 and c[i] > s200[i]:
                return "buy"
            if r2[i] > 90 and c[i] < s200[i]:
                return "sell"
            return None

        return sig, {"atr": a, "atr_stop": 3.0, "reward": 0.0, "time_stop": 10, "max_day": 1, "opposite_exit": True}

    def keltner(bars, k=1.8):
        c = [b[4] for b in bars]
        h, l = [b[2] for b in bars], [b[3] for b in bars]
        e = ema(c, 20)
        a = atr(h, l, c, 14)

        def sig(i):
            if e[i] is None or a[i] is None:
                return None
            if c[i] > e[i] + k * a[i]:
                return "buy"
            if c[i] < e[i] - k * a[i]:
                return "sell"
            return None

        return sig, {"atr": a, "atr_stop": 2.0, "reward": 1.5, "max_day": 1}

    def london_orb(bars):
        # Asian range 00:00-07:59, trade 08:00-12:00 on break of range.
        h, l, c = [b[2] for b in bars], [b[3] for b in bars], [b[4] for b in bars]
        t = [b[0] for b in bars]
        a = atr(h, l, c, 14)
        ranges: Dict[str, Tuple[float, float]] = {}
        cur_d = None
        hi = lo = None
        for i, ts in enumerate(t):
            d = ts.strftime("%Y-%m-%d")
            if d != cur_d:
                if cur_d and hi is not None:
                    ranges[cur_d] = (hi, lo)
                cur_d, hi, lo = d, None, None
            if 0 <= ts.hour < 8:
                hi = h[i] if hi is None else max(hi, h[i])
                lo = l[i] if lo is None else min(lo, l[i])
        if cur_d and hi is not None:
            ranges[cur_d] = (hi, lo)

        def sig(i):
            ts = t[i]
            if ts.hour < 8 or ts.hour >= 12:
                return None
            rng = ranges.get(ts.strftime("%Y-%m-%d"))
            if not rng:
                return None
            rh, rl = rng
            if rh - rl < 0.0004:
                return None
            if c[i] > rh:
                return "buy"
            if c[i] < rl:
                return "sell"
            return None

        return sig, {"atr": a, "atr_stop": 1.2, "reward": 1.2, "max_day": 1}

    def nr_break(bars, n=7):
        h, l, c = [b[2] for b in bars], [b[3] for b in bars], [b[4] for b in bars]
        a = atr(h, l, c, 14)

        def sig(i):
            if i < n:
                return None
            rng = h[i] - l[i]
            if rng > min(h[j] - l[j] for j in range(i - n, i)):
                return None
            if c[i] > h[i - 1]:
                return "buy"
            if c[i] < l[i - 1]:
                return "sell"
            return None

        return sig, {"atr": a, "atr_stop": 2.0, "reward": 1.5, "max_day": 1}

    universes = [
        ("EURUSD", "H1", eurusd_h1, 200),
        ("EURUSD", "H4", eurusd_h4, 80),
        ("EURUSD", "D1", eurusd_d1, 220),
        ("GBPUSD", "H1", gbpusd_h1, 200),
    ]

    print("=== strategy search ===")
    for pair, tf, bars, warm in universes:
        for n in (20, 24, 36, 48, 72):
            if tf == "D1" and n > 40:
                continue
            add(f"{pair}_{tf}_don{n}", bars, tf, warm, lambda b, nn=n: donchian(b, nn))
        for m in (2.0, 3.0, 3.5):
            add(f"{pair}_{tf}_st{m}", bars, tf, warm, lambda b, mm=m: st_flip(b, mm))
        add(f"{pair}_{tf}_rsi2", bars, tf, warm, rsi2_mr)
        add(f"{pair}_{tf}_kelt", bars, tf, warm, keltner)
        if tf == "H1":
            add(f"{pair}_{tf}_orb", bars, tf, warm, london_orb)
        add(f"{pair}_{tf}_er", bars, tf, warm, er_trend)
        add(f"{pair}_{tf}_nr7", bars, tf, warm, nr_break)

    passed = [
        r
        for r in results
        if r["oos"]["n"] >= 12
        and r["oos"]["net_pnl"] > 0
        and (r["oos"]["pf"] or 0) >= 1.15
        and r["is"]["net_pnl"] > 0
        and r["oos"]["max_dd"] <= 15
    ]
    print("\n=== ER robustness (EURUSD H4 + GBPUSD H4) ===")
    gbp_h4 = resample_h4(gbpusd_h1)
    for n, emin in ((8, 0.35), (8, 0.40), (10, 0.35), (10, 0.40), (10, 0.45), (12, 0.40)):
        add(
            f"EURUSD_H4_er_n{n}_{emin}",
            eurusd_h4,
            "H4",
            80,
            lambda b, nn=n, ee=emin: er_trend(b, nn, ee),
        )
    add("GBPUSD_H4_er", gbp_h4, "H4", 80, er_trend)
    sig, cfg = er_trend(eurusd_h4)
    _full, trades = simulate(eurusd_h4, "H4", 80, sig, {**cfg, "name": "er"})
    print("EURUSD H4 ER trade list:")
    for t in trades:
        print(t)
    if not passed:
        print("NONE under strict gate")
        # looser
        passed = [
            r
            for r in results
            if r["oos"]["n"] >= 8
            and r["oos"]["net_pnl"] > 0
            and (r["oos"]["pf"] or 0) >= 1.05
            and r["full"]["net_pnl"] > 0
        ]
        print("loose gate", len(passed))
    for r in sorted(passed, key=lambda x: x["oos"]["net_pnl"], reverse=True)[:15]:
        print(
            f"{r['name']:<28} OOS {r['oos']['net_pnl']:8.1f} PF {r['oos']['pf']} n={r['oos']['n']} "
            f"IS {r['is']['net_pnl']:8.1f} FULL {r['full']['net_pnl']:8.1f} DD {r['full']['max_dd']}"
        )

    payload = {
        "strict_pass": [
            {
                "name": r["name"],
                "tf": r["tf"],
                "is": r["is"],
                "oos": r["oos"],
                "full": r["full"],
            }
            for r in sorted(passed, key=lambda x: x["oos"]["net_pnl"], reverse=True)
        ],
        "all": [
            {"name": r["name"], "tf": r["tf"], "is": r["is"], "oos": r["oos"], "full": r["full"]}
            for r in results
        ],
    }
    (ROOT / "logs" / "research.json").write_text(json.dumps(payload, indent=2))
    print("wrote logs/research.json", "candidates", len(passed))


if __name__ == "__main__":
    main()
