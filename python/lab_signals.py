"""Signal factories for the research lab. Exit is frozen: 2.5 ATR stop, 2R target, no trail.

Each factory(bars) -> (sig, cfg). sig(i) uses only closed bar i.
Add a new idea here as one function, then list it in CATALOG.
"""
from __future__ import annotations

from typing import Callable, List

from lab_indicators import donchian_prior, efficiency, macd, rsi_closed
from research import atr, ema, er_trend

EXIT = {"atr_stop": 2.5, "reward": 2.0, "trail_atr": 0.0, "max_day": 1}


def _ohlc(bars):
    c = [b[4] for b in bars]
    h = [b[2] for b in bars]
    l = [b[3] for b in bars]
    return c, h, l, atr(h, l, c, 14)


def _cfg(a):
    cfg = dict(EXIT)
    cfg["atr"] = a
    return cfg


def live_er_ema(bars, n=10, er_min=0.4):
    """Live baseline. Must stay in the catalog as the thing to beat."""
    return er_trend(bars, n, er_min)


def donchian_er(bars, channel=20, er_n=10, er_min=0.4):
    """Close breaks the prior N-bar high/low, and ER is high enough."""
    c, h, l, a = _ohlc(bars)
    up, dn = donchian_prior(h, l, channel)
    er = efficiency(c, er_n)

    def sig(i):
        if i < 1:
            return None
        if None in (up[i], dn[i], er[i], a[i]):
            return None
        if er[i] < er_min:
            return None
        if c[i] > up[i]:
            return "buy"
        if c[i] < dn[i]:
            return "sell"
        return None

    return sig, _cfg(a)


def donchian_plain(bars, channel=20):
    """Same breakout without the ER gate (usually noisier)."""
    c, h, l, a = _ohlc(bars)
    up, dn = donchian_prior(h, l, channel)

    def sig(i):
        if i < 1:
            return None
        if up[i] is None or dn[i] is None or a[i] is None:
            return None
        if c[i] > up[i]:
            return "buy"
        if c[i] < dn[i]:
            return "sell"
        return None

    return sig, _cfg(a)


def macd_ema_side(bars, trend=50):
    """MACD histogram flips while close is on that side of EMA."""
    c, h, l, a = _ohlc(bars)
    e50 = ema(c, trend)
    _line, _sig, hist = macd(c)

    def sig(i):
        if i < 1:
            return None
        if None in (hist[i], hist[i - 1], e50[i], a[i]):
            return None
        if hist[i] > 0 and hist[i - 1] <= 0 and c[i] > e50[i]:
            return "buy"
        if hist[i] < 0 and hist[i - 1] >= 0 and c[i] < e50[i]:
            return "sell"
        return None

    return sig, _cfg(a)


def rsi_leave_ema(bars, rsi_n=14, low=30.0, high=70.0, trend=50):
    """RSI leaves oversold/overbought while close is on that side of EMA."""
    c, h, l, a = _ohlc(bars)
    e50 = ema(c, trend)
    r = rsi_closed(c, rsi_n)

    def sig(i):
        if i < 1:
            return None
        if None in (r[i], r[i - 1], e50[i], a[i]):
            return None
        if r[i - 1] < low and r[i] >= low and c[i] > e50[i]:
            return "buy"
        if r[i - 1] > high and r[i] <= high and c[i] < e50[i]:
            return "sell"
        return None

    return sig, _cfg(a)


# id, short label, factory. live_baseline=True is the enabled EA rule.
CATALOG: List[Tuple[str, str, Callable, bool]] = [
    ("live_er_ema", "ER10/0.40 + EMA50 close cross (live)", live_er_ema, True),
    ("donchian_er", "Donchian20 break + ER 0.40", donchian_er, False),
    ("donchian_plain", "Donchian20 break, no ER", donchian_plain, False),
    ("macd_ema", "MACD hist flip + EMA50 side", macd_ema_side, False),
    ("rsi_ema", "RSI leave 30/70 + EMA50 side", rsi_leave_ema, False),
]
