from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

from . import paths


class HookError(RuntimeError):
    pass


class HookClient:
    """Talk to PythonBridgeEA via Common Files (per-symbol) or local HTTP."""

    def __init__(self, http_url: str = "http://127.0.0.1:18790", timeout: float = 8.0):
        self.http_url = http_url.rstrip("/")
        self.timeout = timeout
        self.token = paths.token()

    def status(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        if symbol is None:
            http = self._http_get("/snapshot")
            if http is not None:
                return http
            snaps = self.status_all()
            if len(snaps) == 1:
                return {"ok": True, "via": "file", "snapshot": snaps[0]}
            if snaps:
                return {"ok": True, "via": "file", "snapshots": snaps, "snapshot": snaps[0]}
        snap = self._read_snapshot_file(symbol)
        if snap is not None:
            return {"ok": True, "via": "file", "snapshot": snap}
        common = paths.common_files_dir()
        if not common.exists():
            raise HookError(
                f"MetaTrader 5 Common Files not found at {common}. "
                f"Install MT5, log into a demo account, then run {paths.hook_command()} install."
            )
        hint = f" on {symbol}" if symbol else ""
        raise HookError(
            f"No snapshot yet{hint}. Attach PythonBridgeEA to the chart, enable "
            "Algo Trading, and wait one second."
        )

    def status_all(self) -> List[Dict[str, Any]]:
        symbols = paths.list_snapshot_symbols()
        snaps: List[Dict[str, Any]] = []
        seen = set()
        for sym in symbols:
            data = self._read_snapshot_file(sym)
            if data is None:
                continue
            key = str(data.get("symbol") or sym)
            if key in seen:
                continue
            seen.add(key)
            snaps.append(data)
        if not snaps:
            legacy = self._read_snapshot_file(None)
            if legacy is not None:
                snaps.append(legacy)
        return snaps

    def ping(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        return self.send({"cmd": "ping"}, symbol=symbol)

    def buy(
        self,
        symbol: str,
        volume: float = 0.0,
        sl: float = 0.0,
        tp: float = 0.0,
        risk_percent: float = 1.0,
        comment: str = "python-hook",
        atr_stop_mult: float = 2.5,
        reward_ratio: float = 2.0,
    ) -> Dict[str, Any]:
        return self.send(
            {
                "cmd": "buy",
                "symbol": symbol,
                "volume": volume,
                "sl": sl,
                "tp": tp,
                "risk_percent": risk_percent,
                "atr_stop_mult": atr_stop_mult,
                "reward_ratio": reward_ratio,
                "comment": comment,
            },
            symbol=symbol,
        )

    def sell(
        self,
        symbol: str,
        volume: float = 0.0,
        sl: float = 0.0,
        tp: float = 0.0,
        risk_percent: float = 1.0,
        comment: str = "python-hook",
        atr_stop_mult: float = 2.5,
        reward_ratio: float = 2.0,
    ) -> Dict[str, Any]:
        return self.send(
            {
                "cmd": "sell",
                "symbol": symbol,
                "volume": volume,
                "sl": sl,
                "tp": tp,
                "risk_percent": risk_percent,
                "atr_stop_mult": atr_stop_mult,
                "reward_ratio": reward_ratio,
                "comment": comment,
            },
            symbol=symbol,
        )

    def close(self, ticket: int, symbol: Optional[str] = None) -> Dict[str, Any]:
        return self.send({"cmd": "close", "ticket": int(ticket)}, symbol=symbol)

    def close_all(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        if symbol:
            return self.send({"cmd": "close_all"}, symbol=symbol)
        results = []
        for snap in self.status_all():
            sym = str(snap.get("symbol") or "")
            if not sym:
                continue
            results.append(self.send({"cmd": "close_all"}, symbol=sym))
        if not results:
            return self.send({"cmd": "close_all"})
        ok = all(r.get("ok") for r in results)
        return {"ok": ok, "cmd": "close_all", "results": results}

    def modify(self, ticket: int, sl: float, tp: float, symbol: Optional[str] = None) -> Dict[str, Any]:
        return self.send({"cmd": "modify", "ticket": int(ticket), "sl": sl, "tp": tp}, symbol=symbol)

    def halt(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        return self._broadcast("halt", symbol)

    def resume(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        return self._broadcast("resume", symbol)

    def signal(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        return self.send({"cmd": "signal"}, symbol=symbol)

    def _broadcast(self, cmd: str, symbol: Optional[str]) -> Dict[str, Any]:
        if symbol:
            return self.send({"cmd": cmd}, symbol=symbol)
        snaps = self.status_all()
        if not snaps:
            return self.send({"cmd": cmd})
        results = [self.send({"cmd": cmd}, symbol=str(s.get("symbol"))) for s in snaps if s.get("symbol")]
        ok = all(r.get("ok") for r in results)
        return {"ok": ok, "cmd": cmd, "results": results}

    def send(self, cmd: Dict[str, Any], symbol: Optional[str] = None) -> Dict[str, Any]:
        payload = dict(cmd)
        payload.setdefault("id", str(uuid.uuid4()))
        payload.setdefault("token", self.token)
        target = symbol or payload.get("symbol")
        if target:
            payload.setdefault("symbol", target)
            return self._send_file(payload, str(target))
        http = self._http_post("/cmd", payload)
        if http is not None:
            return http
        return self._send_file(payload, None)

    def _http_get(self, route: str) -> Optional[Dict[str, Any]]:
        req = urllib.request.Request(
            self.http_url + route,
            headers={"X-Hook-Token": self.token},
            method="GET",
        )
        return self._http(req)

    def _http_post(self, route: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.http_url + route,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-Hook-Token": self.token,
            },
            method="POST",
        )
        return self._http(req)

    def _http(self, req: urllib.request.Request) -> Optional[Dict[str, Any]]:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                if isinstance(body, dict):
                    body.setdefault("via", "http")
                return body
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ConnectionError):
            return None

    def _read_snapshot_file(self, symbol: Optional[str]) -> Optional[Dict[str, Any]]:
        path = paths.snapshot_path(symbol)
        data = self._read_json_file(path)
        if data is None and symbol:
            legacy = self._read_json_file(paths.snapshot_path(None))
            if legacy is not None and str(legacy.get("symbol") or "").upper() in (
                symbol.upper(),
                symbol.upper().replace("_", "."),
            ):
                return legacy
        return data

    def _read_json_file(self, path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        for _ in range(5):
            try:
                age = time.time() - path.stat().st_mtime
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                time.sleep(0.05)
                continue
            except OSError:
                return None
            if age > 15:
                data["_stale_seconds"] = round(age, 1)
            return data
        return None

    def _send_file(self, payload: Dict[str, Any], symbol: Optional[str]) -> Dict[str, Any]:
        common = paths.common_files_dir()
        if not common.exists():
            raise HookError(
                f"MT5 Common Files folder not found at {common}. "
                f"Install MetaTrader 5, log into a demo account, then run {paths.hook_command()} install."
            )

        cmd_file = paths.cmd_path(symbol)
        result_file = paths.result_path(symbol)
        deadline_wait = time.time() + self.timeout
        while cmd_file.exists() and time.time() < deadline_wait:
            time.sleep(0.05)
        if cmd_file.exists():
            raise HookError(f"Previous command is still pending for {symbol or 'default'}.")

        if result_file.exists():
            try:
                result_file.unlink()
            except OSError:
                pass

        tmp = cmd_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        tmp.replace(cmd_file)

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if result_file.exists():
                try:
                    raw = result_file.read_text(encoding="utf-8")
                    data = json.loads(raw)
                except (OSError, json.JSONDecodeError):
                    time.sleep(0.05)
                    continue
                if data.get("id") in (None, "", payload["id"]) or data.get("id") == payload["id"]:
                    data["via"] = "file"
                    return data
            time.sleep(0.05)

        raise HookError(
            f"EA did not answer{' on ' + symbol if symbol else ''}. "
            "Attach PythonBridgeEA to that chart, enable Algo Trading, and keep MT5 running."
        )
