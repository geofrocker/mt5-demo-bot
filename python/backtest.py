"""Replay manager rules on Yahoo EURUSD H1. Supports a small, named variant sweep."""
from __future__ import annotations

import json
import math
import ssl
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

Bar = Tuple[datetime, float, float, float, float]  # t, o, h, l, c
TZ = timezone(timedelta(hours=3))
ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "logs" / "eurusd_h1.json"
START_EQUITY = 10000.0
SPREAD = 0.00012
OOS_START = datetime(2025, 9, 1, tzinfo=TZ)


@dataclass
class Cfg:
    name: str
    atr_stop: float = 1.5
    reward: float = 2.0
    rsi_buy: float = 45.0
    rsi_sell: float = 55.0
    max_day: int = 3
    session_start: int = 7
    session_end: int = 20
    stack3: bool = True  # 20>50>200 vs 50>200
    bounce20: bool = False
    adx_min: float = 0.0
    confirm_candle: bool = False
    notes: str = ""


@dataclass
class Trade:
    side: str
    entry_time: str
    exit_time: str
    entry: float
    sl: float
    tp: float
    lots: float
    exit: float
    pnl: float
    reason: str


VARIANTS = [
    Cfg("v0_baseline", notes="Live rules that lost money"),
    Cfg("v1_wider_stop", atr_stop=2.0, notes="Give trades room"),
    Cfg("v2_wider_easier_tp", atr_stop=2.0, reward=1.5, notes="Wider stop, 1.5R"),
    Cfg("v3_strict_rsi", atr_stop=2.0, reward=1.5, rsi_buy=40.0, rsi_sell=60.0, max_day=1),
    Cfg("v4_ema50_200", atr_stop=2.0, reward=1.5, rsi_buy=40.0, rsi_sell=60.0, max_day=1, stack3=False),
    Cfg("v5_bounce_ema20", atr_stop=2.0, reward=1.5, rsi_buy=40.0, rsi_sell=60.0, max_day=1, stack3=False, bounce20=True),
    Cfg("v6_adx20", atr_stop=2.0, reward=1.5, rsi_buy=40.0, rsi_sell=60.0, max_day=1, stack3=False, bounce20=True, adx_min=20.0),
    Cfg("v7_london_ny", atr_stop=2.0, reward=1.5, rsi_buy=40.0, rsi_sell=60.0, max_day=1, stack3=False, bounce20=True, adx_min=20.0, session_start=8, session_end=17, confirm_candle=True),
    Cfg("v8_adx25_2r", atr_stop=2.0, reward=2.0, rsi_buy=38.0, rsi_sell=62.0, max_day=1, stack3=False, bounce20=True, adx_min=25.0, session_start=8, session_end=17, confirm_candle=True),
]


def fetch_yahoo_h1() -> List[Bar]:
    if CACHE.exists():
        raw = json.loads(CACHE.read_text(encoding="utf-8"))
        return [
            (datetime.fromisoformat(r[0]), r[1], r[2], r[3], r[4])
            for r in raw
        ]
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X"
        "?interval=1h&range=2y&includePrePost=false"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "mt5-demo-bot/1.0"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    result = payload["chart"]["result"][0]
    ts = result["timestamp"]
    q = result["indicators"]["quote"][0]
    rows: List[Bar] = []
    dump = []
    for i, unix in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        dt = datetime.fromtimestamp(unix, tz=timezone.utc).astimezone(TZ)
        rows.append((dt, float(o), float(h), float(l), float(c)))
        dump.append([dt.isoformat(), float(o), float(h), float(l), float(c)])
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(dump), encoding="utf-8")
    return rows


def ema(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: List[float], period: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        ch = values[i] - values[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_g, avg_l = gains / period, losses / period
    out[period] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    for i in range(period + 1, len(values)):
        ch = values[i] - values[i - 1]
        g = ch if ch > 0 else 0.0
        l = -ch if ch < 0 else 0.0
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
        out[i] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    return out


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(closes)
    trs = [0.0] * len(closes)
    for i in range(1, len(closes)):
        trs[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    if len(closes) <= period:
        return out
    prev = sum(trs[1 : period + 1]) / period
    out[period] = prev
    for i in range(period + 1, len(closes)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[Optional[float]]:
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if n <= period * 2:
        return out
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    tr = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    sm_tr = sm_p = sm_m = 0.0
    for i in range(1, period + 1):
        sm_tr += tr[i]
        sm_p += plus_dm[i]
        sm_m += minus_dm[i]
    dxs: List[Optional[float]] = [None] * n
    def dx_at(tr_s: float, p_s: float, m_s: float) -> float:
        if tr_s <= 0:
            return 0.0
        pdi = 100.0 * p_s / tr_s
        mdi = 100.0 * m_s / tr_s
        denom = pdi + mdi
        return 0.0 if denom == 0 else 100.0 * abs(pdi - mdi) / denom
    dxs[period] = dx_at(sm_tr, sm_p, sm_m)
    for i in range(period + 1, n):
        sm_tr = sm_tr - sm_tr / period + tr[i]
        sm_p = sm_p - sm_p / period + plus_dm[i]
        sm_m = sm_m - sm_m / period + minus_dm[i]
        dxs[i] = dx_at(sm_tr, sm_p, sm_m)
    first = period * 2 - 1
    if first >= n:
        return out
    seed = sum(dxs[period : first + 1]) / period  # type: ignore
    out[first] = seed
    prev = seed
    for i in range(first + 1, n):
        prev = (prev * (period - 1) + (dxs[i] or 0.0)) / period
        out[i] = prev
    return out


def lots_for_risk(equity: float, sl_dist: float) -> float:
    risk = equity * 0.005
    sl_pips = sl_dist / 0.0001
    if sl_pips <= 0:
        return 0.0
    raw = risk / (sl_pips * 10.0)
    lots = math.floor(raw / 0.01) * 0.01
    lots = max(0.01, min(0.10, lots))
    if 0.01 * sl_pips * 10.0 > risk * 1.5:
        return 0.0
    return lots


def signal(cfg: Cfg, i: int, opens, highs, lows, closes, e20, e50, e200, rs, at, adx_v) -> Optional[str]:
    if None in (e20[i], e50[i], e200[i], rs[i], rs[i - 1], at[i]):
        return None
    if cfg.adx_min > 0 and (adx_v[i] is None or adx_v[i] < cfg.adx_min):
        return None
    ema20, ema50, ema200 = e20[i], e50[i], e200[i]
    rsi_now, rsi_prev = rs[i], rs[i - 1]
    close, high, low, opn = closes[i], highs[i], lows[i], opens[i]
    if cfg.stack3:
        up = ema20 > ema50 > ema200
        down = ema20 < ema50 < ema200
    else:
        up = ema50 > ema200 and close > ema50
        down = ema50 < ema200 and close < ema50
    buy_rsi = rsi_prev < cfg.rsi_buy and rsi_now > rsi_prev and rsi_now < 55.0
    sell_rsi = rsi_prev > cfg.rsi_sell and rsi_now < rsi_prev and rsi_now > 45.0
    if cfg.bounce20:
        buy_rsi = buy_rsi and low <= ema20 <= close
        sell_rsi = sell_rsi and high >= ema20 >= close
    if cfg.confirm_candle:
        buy_rsi = buy_rsi and close > opn
        sell_rsi = sell_rsi and close < opn
    if up and buy_rsi:
        return "buy"
    if down and sell_rsi:
        return "sell"
    return None


def summarize(trades: List[Trade], equity: float, max_dd: float, cfg: Cfg, start: str, end: str) -> dict:
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    flats = [t for t in trades if t.pnl == 0]
    gw = sum(t.pnl for t in wins)
    gl = abs(sum(t.pnl for t in losses))
    pf = (gw / gl) if gl else None
    reasons: Dict[str, int] = {}
    for t in trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1
    monthly: Dict[str, float] = {}
    for t in trades:
        m = t.exit_time[:7]
        monthly[m] = round(monthly.get(m, 0.0) + t.pnl, 2)
    return {
        "name": cfg.name,
        "notes": cfg.notes,
        "start": start,
        "end": end,
        "start_equity": START_EQUITY,
        "end_equity": round(equity, 2),
        "net_pnl": round(equity - START_EQUITY, 2),
        "return_pct": round((equity / START_EQUITY - 1) * 100.0, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(flats),
        "win_rate_pct": round(100.0 * len(wins) / len(trades), 1) if trades else 0.0,
        "avg_win": round(gw / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(t.pnl for t in losses) / len(losses), 2) if losses else 0.0,
        "profit_factor": round(pf, 2) if pf is not None else None,
        "expectancy": round(sum(t.pnl for t in trades) / len(trades), 2) if trades else 0.0,
        "reasons": reasons,
        "monthly": monthly,
        "recent": [asdict(t) for t in trades[-12:]],
        "cfg": asdict(cfg),
    }


def run(bars: List[Bar], cfg: Cfg, t0: Optional[datetime] = None, t1: Optional[datetime] = None) -> dict:
    closes = [r[4] for r in bars]
    highs = [r[2] for r in bars]
    lows = [r[3] for r in bars]
    opens = [r[1] for r in bars]
    times = [r[0] for r in bars]
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)
    rs = rsi(closes, 14)
    at = atr(highs, lows, closes, 14)
    adx_v = adx(highs, lows, closes, 14)
    equity = START_EQUITY
    peak = equity
    max_dd = 0.0
    trades: List[Trade] = []
    pos = None
    entries_today = 0
    day = ""
    last_entry_bar = ""
    start_i = 200
    first_t = last_t = None
    curve: List[Tuple[str, float]] = []

    for i in range(start_i, len(bars)):
        t = times[i]
        if t0 and t < t0:
            continue
        if t1 and t >= t1:
            break
        if first_t is None:
            first_t = t
        last_t = t
        day_key = t.strftime("%Y-%m-%d")
        if day_key != day:
            day = day_key
            entries_today = 0
        if pos is not None:
            side = pos["side"]
            hi, lo = highs[i], lows[i]
            sl0, entry, sl, tp, lots = pos["sl0"], pos["entry"], pos["sl"], pos["tp"], pos["lots"]
            r = abs(entry - sl0)
            exit_px = reason = None
            if side == "buy":
                if lo <= sl and hi >= tp:
                    exit_px, reason = sl, "sl_before_tp_same_bar"
                elif lo <= sl:
                    exit_px, reason = sl, "stop"
                elif hi >= tp:
                    exit_px, reason = tp, "target"
                else:
                    if hi >= entry + 1.5 * r:
                        pos["sl"] = entry + 0.5 * r
                        pos["trail"] = True
                    elif hi >= entry + r:
                        pos["sl"] = entry
                    if lo <= pos["sl"]:
                        exit_px, reason = pos["sl"], "trail_stop" if pos.get("trail") else "be_stop"
            else:
                if hi >= sl and lo <= tp:
                    exit_px, reason = sl, "sl_before_tp_same_bar"
                elif hi >= sl:
                    exit_px, reason = sl, "stop"
                elif lo <= tp:
                    exit_px, reason = tp, "target"
                else:
                    if lo <= entry - 1.5 * r:
                        pos["sl"] = entry - 0.5 * r
                        pos["trail"] = True
                    elif lo <= entry - r:
                        pos["sl"] = entry
                    if hi >= pos["sl"]:
                        exit_px, reason = pos["sl"], "trail_stop" if pos.get("trail") else "be_stop"
            if exit_px is not None:
                signed = (exit_px - entry) if side == "buy" else (entry - exit_px)
                pnl = (signed / 0.0001) * lots * 10.0
                equity += pnl
                trades.append(
                    Trade(side, pos["time"], t.strftime("%Y-%m-%d %H:%M"), round(entry, 5), round(sl0, 5), round(tp, 5), lots, round(exit_px, 5), round(pnl, 2), reason)
                )
                pos = None
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak * 100.0)

        if i % 48 == 0:
            curve.append((t.strftime("%Y-%m-%d"), round(equity, 2)))
        if pos is not None:
            continue
        sig_i = i - 1
        if sig_i < start_i:
            continue
        if not (cfg.session_start <= times[sig_i].hour < cfg.session_end) or times[sig_i].weekday() >= 5:
            continue
        if entries_today >= cfg.max_day:
            continue
        bar_id = times[sig_i].strftime("%Y-%m-%d %H:%M")
        if bar_id == last_entry_bar:
            continue
        side = signal(cfg, sig_i, opens, highs, lows, closes, e20, e50, e200, rs, at, adx_v)
        atr_now = at[sig_i]
        if side is None or not atr_now:
            continue
        entry = opens[i] + SPREAD / 2 if side == "buy" else opens[i] - SPREAD / 2
        sl_dist = atr_now * cfg.atr_stop
        lots = lots_for_risk(equity, sl_dist)
        if lots <= 0:
            continue
        if side == "buy":
            sl, tp = entry - sl_dist, entry + sl_dist * cfg.reward
        else:
            sl, tp = entry + sl_dist, entry - sl_dist * cfg.reward
        pos = {"side": side, "entry": entry, "sl": sl, "sl0": sl, "tp": tp, "lots": lots, "time": t.strftime("%Y-%m-%d %H:%M")}
        entries_today += 1
        last_entry_bar = bar_id

    if last_t and (not curve or curve[-1][0] != last_t.strftime("%Y-%m-%d")):
        curve.append((last_t.strftime("%Y-%m-%d"), round(equity, 2)))
    summary = summarize(
        trades,
        equity,
        max_dd,
        cfg,
        first_t.strftime("%Y-%m-%d") if first_t else "",
        last_t.strftime("%Y-%m-%d") if last_t else "",
    )
    summary["curve"] = curve
    summary["trades_full"] = [asdict(t) for t in trades]
    return summary


def score(row: dict) -> tuple:
    pf = row["profit_factor"] if row["profit_factor"] is not None else 0.0
    return (row["net_pnl"] > 0, pf, row["net_pnl"], -row["max_drawdown_pct"])


def main() -> None:
    bars = fetch_yahoo_h1()
    table = []
    full_by_name = {}
    print(f"{'name':<22} {'IS pnl':>9} {'IS PF':>6} {'IS n':>5} {'OOS pnl':>9} {'OOS PF':>7} {'OOS n':>6} {'full pnl':>9} {'full PF':>7}")
    for cfg in VARIANTS:
        ins = run(bars, cfg, t1=OOS_START)
        oos = run(bars, cfg, t0=OOS_START)
        full = run(bars, cfg)
        full_by_name[cfg.name] = full
        table.append({"cfg": cfg.name, "notes": cfg.notes, "is": ins, "oos": oos, "full": full})
        print(
            f"{cfg.name:<22} {ins['net_pnl']:9.1f} {str(ins['profit_factor']):>6} {ins['trades']:5d} "
            f"{oos['net_pnl']:9.1f} {str(oos['profit_factor']):>7} {oos['trades']:6d} "
            f"{full['net_pnl']:9.1f} {str(full['profit_factor']):>7}"
        )

    profitable_oos = [r for r in table if r["oos"]["net_pnl"] > 0 and (r["oos"]["profit_factor"] or 0) >= 1.0]
    if profitable_oos:
        winner = max(profitable_oos, key=lambda r: score(r["oos"]))
        selection = "Best out-of-sample variant with OOS P&L>0 and PF>=1."
    else:
        traded = [r for r in table if r["full"]["trades"] >= 30]
        winner = max(traded, key=lambda r: r["full"]["net_pnl"])
        selection = (
            "No variant was profitable out of sample. Chose the least-damaging "
            "full-sample rule set with at least 30 trades (not a claimed edge)."
        )
    chosen = full_by_name[winner["cfg"]]
    chosen["variants"] = [
        {
            "name": r["cfg"],
            "notes": r["notes"],
            "is_pnl": r["is"]["net_pnl"],
            "is_pf": r["is"]["profit_factor"],
            "is_n": r["is"]["trades"],
            "oos_pnl": r["oos"]["net_pnl"],
            "oos_pf": r["oos"]["profit_factor"],
            "oos_n": r["oos"]["trades"],
            "full_pnl": r["full"]["net_pnl"],
            "full_pf": r["full"]["profit_factor"],
            "full_n": r["full"]["trades"],
            "full_dd": r["full"]["max_drawdown_pct"],
            "full_wr": r["full"]["win_rate_pct"],
        }
        for r in table
    ]
    chosen["winner"] = winner["cfg"]
    chosen["selection"] = selection
    chosen["assumptions"] = [
        "Yahoo EURUSD=X H1, 1.2 pip spread, UTC+3 session, stop wins if SL and TP in the same hour.",
        "In-sample through Aug 2025, out-of-sample from Sep 2025.",
        f"Selected {winner['cfg']}: {winner['notes']}",
    ]
    chosen.pop("trades_full", None)
    out = ROOT / "logs" / "backtest.json"
    out.write_text(json.dumps(chosen, indent=2), encoding="utf-8")
    print("winner", winner["cfg"], "oos_pnl", winner["oos"]["net_pnl"])
    print("wrote", out)


if __name__ == "__main__":
    main()
