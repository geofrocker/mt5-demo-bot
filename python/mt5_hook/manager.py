from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .client import HookClient, HookError
from . import paths

DEFAULTS = {
    "enabled": ["EURUSD"],
    "max_positions": 3,
    "max_usd_dir": 2,
    "risk_percent": 0.5,
}

ATR_STOP_MULT = 2.5
REWARD_RATIO = 2.0
MAX_ENTRIES_PER_DAY = 1


def load_enabled() -> Dict[str, Any]:
    path = paths.enabled_config_path()
    cfg = dict(DEFAULTS)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg.update(raw)
        except (OSError, json.JSONDecodeError):
            pass
    enabled = cfg.get("enabled") or ["EURUSD"]
    cfg["enabled"] = [str(s).upper() for s in enabled]
    cfg["max_positions"] = int(cfg.get("max_positions") or 3)
    cfg["max_usd_dir"] = int(cfg.get("max_usd_dir") or 2)
    cfg["risk_percent"] = float(cfg.get("risk_percent") or 0.5)
    return cfg


def usd_dir(symbol: str, side: str) -> int:
    s = str(symbol or "").replace(".", "").replace("_", "").upper()
    if len(s) < 6:
        return 0
    base, quote = s[:3], s[3:6]
    if quote == "USD":
        return -1 if side == "buy" else 1
    if base == "USD":
        return 1 if side == "buy" else -1
    return 0


def _snap(payload: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(payload.get("snapshot"), dict):
        return payload["snapshot"]
    return payload


def _log(event: str, **fields: Any) -> None:
    row = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event,
        **fields,
    }
    line = json.dumps(row, separators=(",", ":"))
    print(line, flush=True)


def _load_state() -> Dict[str, Any]:
    path = paths.manager_state_path()
    if not path.exists():
        return {"day": "", "symbols": {}, "tickets": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"day": "", "symbols": {}, "tickets": {}}


def _save_state(state: Dict[str, Any]) -> None:
    paths.manager_state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")


def _today(snap: Dict[str, Any]) -> str:
    t = str(snap.get("time") or "")
    return t[:10] if len(t) >= 10 else datetime.now(timezone.utc).strftime("%Y.%m.%d")


def _sym_state(state: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    bag = state.setdefault("symbols", {})
    slot = bag.get(symbol)
    if not isinstance(slot, dict):
        slot = {"entries_today": 0, "last_entry_bar": ""}
        bag[symbol] = slot
    return slot


def _remember_ticket(state: Dict[str, Any], pos: Dict[str, Any]) -> None:
    tickets: Dict[str, Any] = state.setdefault("tickets", {})
    key = str(pos.get("ticket"))
    if key not in tickets:
        tickets[key] = {
            "entry": pos.get("price_open"),
            "sl0": pos.get("sl"),
            "tp": pos.get("tp"),
            "type": pos.get("type"),
            "symbol": pos.get("symbol"),
            "breakeven": False,
            "trail": False,
        }


def _usd_ok(positions: List[Dict[str, Any]], symbol: str, side: str, max_same: int) -> bool:
    dnew = usd_dir(symbol, side)
    if dnew == 0:
        return True
    same = sum(1 for p in positions if usd_dir(str(p.get("symbol") or ""), str(p.get("type") or "")) == dnew)
    return same < max_same


def _manage_positions(client: HookClient, snap: Dict[str, Any], state: Dict[str, Any]) -> None:
    positions: List[Dict[str, Any]] = list(snap.get("positions") or [])
    symbol = str(snap.get("symbol") or "")
    tickets: Dict[str, Any] = state.setdefault("tickets", {})

    bid = float(snap.get("bid") or 0)
    ask = float(snap.get("ask") or 0)
    for pos in positions:
        _remember_ticket(state, pos)
        key = str(pos.get("ticket"))
        meta = tickets.get(key) or {}
        entry = float(pos.get("price_open") or 0)
        sl = float(pos.get("sl") or 0)
        sl0 = float(meta.get("sl0") or sl)
        side = pos.get("type")
        if entry <= 0 or sl0 <= 0:
            continue
        r = abs(entry - sl0)
        if r <= 0:
            continue
        price = bid if side == "buy" else ask
        volume = float(pos.get("volume") or 0)
        profit = float(pos.get("profit") or 0)
        if price > 0:
            favorable = (price - entry) if side == "buy" else (entry - price)
        elif volume > 0:
            pip_value = volume * 10.0
            favorable = (profit / pip_value) * 0.0001
        else:
            continue
        ticket = int(pos["ticket"])
        tp = float(pos.get("tp") or meta.get("tp") or 0)
        new_sl: Optional[float] = None
        action = ""
        if favorable >= 1.5 * r and not meta.get("trail"):
            new_sl = entry + 0.5 * r if side == "buy" else entry - 0.5 * r
            action = "trail"
        elif favorable >= r and not meta.get("breakeven"):
            new_sl = entry
            action = "breakeven"
        if new_sl is not None:
            res = client.modify(ticket, new_sl, tp, symbol=symbol or None)
            _log(action, ticket=ticket, symbol=symbol, sl=new_sl, ok=res.get("ok"), message=res.get("message"))
            if action == "trail":
                meta["trail"] = True
                meta["breakeven"] = True
            else:
                meta["breakeven"] = True
            tickets[key] = meta


def _maybe_enter(
    client: HookClient,
    snap: Dict[str, Any],
    state: Dict[str, Any],
    all_positions: List[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> None:
    if snap.get("halted_daily") or snap.get("halted_python"):
        return
    if not snap.get("algo_allowed"):
        return
    if snap.get("session_ok") is False:
        return
    symbol = str(snap.get("symbol") or "")
    if not symbol:
        return
    if any(str(p.get("symbol") or "") == symbol for p in all_positions):
        return
    account = max(int(snap.get("positions_account") or 0), len(all_positions))
    if account >= int(cfg["max_positions"]):
        return
    day = _today(snap)
    if state.get("day") != day:
        state["day"] = day
        for slot in state.setdefault("symbols", {}).values():
            if isinstance(slot, dict):
                slot["entries_today"] = 0
                slot["last_entry_bar"] = ""
    slot = _sym_state(state, symbol)
    if int(slot.get("entries_today") or 0) >= MAX_ENTRIES_PER_DAY:
        return
    bar = str(snap.get("bar_time") or "")
    if bar and bar == slot.get("last_entry_bar"):
        return
    try:
        sig = client.signal(symbol=symbol)
    except HookError as exc:
        _log("signal_error", symbol=symbol, error=str(exc))
        return
    side = sig.get("side") or "flat"
    reason = sig.get("reason") or ""
    if side not in ("buy", "sell"):
        prev = slot.get("last_flat_reason")
        now = time.time()
        if reason != prev or now - float(slot.get("last_flat_ts") or 0) > 900:
            _log("flat", symbol=symbol, reason=reason, bar=bar)
            slot["last_flat_reason"] = reason
            slot["last_flat_ts"] = now
        return
    if not _usd_ok(all_positions, symbol, side, int(cfg["max_usd_dir"])):
        _log("skip_usd_cap", symbol=symbol, side=side)
        return
    risk = float(cfg["risk_percent"])
    if side == "buy":
        res = client.buy(
            symbol,
            risk_percent=risk,
            atr_stop_mult=ATR_STOP_MULT,
            reward_ratio=REWARD_RATIO,
            comment="manager-er-h4",
        )
    else:
        res = client.sell(
            symbol,
            risk_percent=risk,
            atr_stop_mult=ATR_STOP_MULT,
            reward_ratio=REWARD_RATIO,
            comment="manager-er-h4",
        )
    ok = bool(res.get("ok"))
    _log("entry", symbol=symbol, side=side, reason=reason, ok=ok, message=res.get("message"), ticket=res.get("ticket"))
    if ok:
        slot["entries_today"] = int(slot.get("entries_today") or 0) + 1
        slot["last_entry_bar"] = bar
        fresh = _snap(res)
        for pos in fresh.get("positions") or []:
            _remember_ticket(state, pos)
            all_positions.append(pos)


def tick(client: HookClient, state: Dict[str, Any]) -> List[Dict[str, Any]]:
    cfg = load_enabled()
    snaps: List[Dict[str, Any]] = []
    for symbol in cfg["enabled"]:
        try:
            snap = _snap(client.status(symbol=symbol))
        except HookError as exc:
            miss = state.setdefault("missing", {})
            now = time.time()
            if now - float(miss.get(symbol) or 0) > 300:
                _log("missing_chart", symbol=symbol, error=str(exc))
                miss[symbol] = now
            continue
        if snap.get("_stale_seconds"):
            _log("stale_snapshot", symbol=symbol, seconds=snap.get("_stale_seconds"))
            continue
        snaps.append(snap)

    all_positions: List[Dict[str, Any]] = []
    seen_tickets = set()
    for snap in snaps:
        for pos in snap.get("positions") or []:
            key = str(pos.get("ticket"))
            if key in seen_tickets:
                continue
            seen_tickets.add(key)
            all_positions.append(pos)

    live = {str(p.get("ticket")) for p in all_positions}
    tickets: Dict[str, Any] = state.setdefault("tickets", {})
    for gone in [k for k in list(tickets) if k not in live]:
        tickets.pop(gone, None)

    for snap in snaps:
        _manage_positions(client, snap, state)
        _maybe_enter(client, snap, state, all_positions, cfg)

    _save_state(state)
    ticks = int(state.get("_ticks") or 0) + 1
    state["_ticks"] = ticks
    if ticks == 1 or ticks % 15 == 0:
        eq = snaps[0].get("equity") if snaps else None
        _log(
            "heartbeat",
            equity=eq,
            charts=len(snaps),
            enabled=cfg["enabled"],
            positions=len(all_positions),
        )
    return snaps


def run(interval: int = 20, caffeinate: bool = False) -> None:
    if caffeinate and sys.platform == "darwin" and os.environ.get("MT5_MANAGER_CAFFEINATED") != "1":
        os.environ["MT5_MANAGER_CAFFEINATED"] = "1"
        os.execvp("caffeinate", ["caffeinate", "-i", sys.executable, "-m", "mt5_hook", "manage", "--interval", str(interval)])

    client = HookClient()
    state = _load_state()
    cfg = load_enabled()
    _log("manager_start", interval=interval, pid=os.getpid(), enabled=cfg["enabled"])
    while True:
        try:
            tick(client, state)
        except HookError as exc:
            _log("hook_error", error=str(exc))
        except Exception as exc:  # noqa: BLE001
            _log("crash", error=str(exc), trace=traceback.format_exc()[-500:])
        time.sleep(max(5, interval))
