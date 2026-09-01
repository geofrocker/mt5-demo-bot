from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from .client import HookClient, HookError


def _dump(payload: Dict[str, Any]) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    if payload.get("ok") is False:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mt5_hook",
        description="Control a demo MT5 account through PythonBridgeEA.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="Run the local TCP/HTTP control plane")

    def with_symbol(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--symbol", default=None, help="chart symbol (required when several EAs are attached)")
        return p

    with_symbol(sub.add_parser("status", help="Show account snapshot"))
    with_symbol(sub.add_parser("ping", help="Ping the EA"))
    with_symbol(sub.add_parser("signal", help="Ask the EA for the current H4 setup"))
    with_symbol(sub.add_parser("halt", help="Block new entries"))
    with_symbol(sub.add_parser("resume", help="Allow entries again"))
    with_symbol(sub.add_parser("close-all", help="Close hook-managed positions on that chart"))
    sub.add_parser("scan", help="Walk-forward majors and rewrite config/enabled.json")
    manage = sub.add_parser("manage", help="Run the 24/7 manager in the foreground")
    manage.add_argument("--interval", type=int, default=20, help="seconds between ticks")
    manage.add_argument("--caffeinate", action="store_true", help="prevent idle sleep (macOS)")
    sub.add_parser("daemon-install", help="Install a LaunchAgent that keeps the manager running")
    sub.add_parser("daemon-uninstall", help="Remove the LaunchAgent")

    buy = sub.add_parser("buy", help="Open a buy")
    sell = sub.add_parser("sell", help="Open a sell")
    for p in (buy, sell):
        p.add_argument("symbol")
        p.add_argument("--volume", type=float, default=0.0, help="0 = size from risk")
        p.add_argument("--sl", type=float, default=0.0)
        p.add_argument("--tp", type=float, default=0.0)
        p.add_argument("--risk", type=float, default=0.5, help="percent of equity")
        p.add_argument("--comment", default="python-hook")

    close = sub.add_parser("close", help="Close one ticket")
    close.add_argument("ticket", type=int)
    close.add_argument("--symbol", default=None)

    modify = sub.add_parser("modify", help="Change SL/TP")
    modify.add_argument("ticket", type=int)
    modify.add_argument("--sl", type=float, required=True)
    modify.add_argument("--tp", type=float, required=True)
    modify.add_argument("--symbol", default=None)

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        from .server import serve

        serve()
        return 0
    if args.cmd == "scan":
        from majors import scan

        scan()
        return 0
    if args.cmd == "manage":
        from .manager import run

        run(interval=args.interval, caffeinate=args.caffeinate)
        return 0
    if args.cmd == "daemon-install":
        from . import launchd

        launchd.install()
        return 0
    if args.cmd == "daemon-uninstall":
        from . import launchd

        launchd.uninstall()
        return 0

    client = HookClient()
    try:
        if args.cmd == "status":
            return _dump(client.status(symbol=args.symbol))
        if args.cmd == "ping":
            return _dump(client.ping(symbol=args.symbol))
        if args.cmd == "signal":
            return _dump(client.signal(symbol=args.symbol))
        if args.cmd == "halt":
            return _dump(client.halt(symbol=args.symbol))
        if args.cmd == "resume":
            return _dump(client.resume(symbol=args.symbol))
        if args.cmd == "close-all":
            return _dump(client.close_all(symbol=args.symbol))
        if args.cmd == "buy":
            return _dump(
                client.buy(args.symbol, args.volume, args.sl, args.tp, args.risk, args.comment)
            )
        if args.cmd == "sell":
            return _dump(
                client.sell(args.symbol, args.volume, args.sl, args.tp, args.risk, args.comment)
            )
        if args.cmd == "close":
            return _dump(client.close(args.ticket, symbol=args.symbol))
        if args.cmd == "modify":
            return _dump(client.modify(args.ticket, args.sl, args.tp, symbol=args.symbol))
    except HookError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
