"""Closed-bar indicators for the research lab.

Rule (no repaint, no lookahead):
- At index i the bar is treated as finished.
- Values at i may use bars[0]..bars[i] only.
- Past values must not change when a later bar arrives.
- Zigzags / arrows that move after the fact are not allowed here.

Repaint: a past candle's indicator value later changes. That cheats a backtest.
"""
from __future__ import annotations

from typing import List, Optional

from research import ema, rsi

Series = List[Optional[float]]


def efficiency(closes: List[float], n: int = 10) -> Series:
    """Kaufman efficiency ratio: net move / total wiggling over n closed bars."""
    out: Series = [None] * len(closes)
    for i in range(n, len(closes)):
        change = abs(closes[i] - closes[i - n])
        vol = sum(abs(closes[j] - closes[j - 1]) for j in range(i - n + 1, i + 1))
        out[i] = change / vol if vol else 0.0
    return out


def donchian_prior(high: List[float], low: List[float], n: int = 20) -> tuple:
    """Channel from the previous n bars, not including bar i (breakout of a finished range)."""
    up: Series = [None] * len(high)
    dn: Series = [None] * len(low)
    for i in range(n, len(high)):
        up[i] = max(high[i - n : i])
        dn[i] = min(low[i - n : i])
    return up, dn


def macd(closes: List[float], fast: int = 12, slow: int = 26, signal_n: int = 9):
    """MACD line, signal line, histogram. All aligned to closed bars."""
    fast_e = ema(closes, fast)
    slow_e = ema(closes, slow)
    line: Series = [None] * len(closes)
    for i in range(len(closes)):
        if fast_e[i] is not None and slow_e[i] is not None:
            line[i] = fast_e[i] - slow_e[i]
    sig: Series = [None] * len(closes)
    hist: Series = [None] * len(closes)
    start = next((i for i, x in enumerate(line) if x is not None), None)
    if start is None or start + signal_n > len(closes):
        return line, sig, hist
    seed = sum(line[start : start + signal_n]) / signal_n
    sig[start + signal_n - 1] = seed
    k = 2 / (signal_n + 1)
    s = seed
    for i in range(start + signal_n, len(closes)):
        s = line[i] * k + s * (1 - k)
        sig[i] = s
    for i in range(len(closes)):
        if line[i] is not None and sig[i] is not None:
            hist[i] = line[i] - sig[i]
    return line, sig, hist


def rsi_closed(closes: List[float], n: int = 14) -> Series:
    return rsi(closes, n)
