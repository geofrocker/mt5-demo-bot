# MT5 demo bot

Python control of a **MetaTrader 5 demo** account from this Mac. It is not a human fund manager, not financial advice, and it will not print money.

The official `MetaTrader5` Python package is Windows-only. You do **not** need to switch to Windows. A local hook talks to an Expert Advisor inside MT5.

When the bridge is up, I can read the account and send trades from this project.

## How control works

```
me (this chat)
    -> python/mt5_hook  (CLI)
        -> per-symbol Common Files drop
            -> PythonBridgeEA on each enabled chart
                -> demo account
```

MT5 must stay open with **Algo Trading** enabled. If the terminal is closed, nothing trades.

Do **not** attach `ConservativeTrendEA` at the same time as `PythonBridgeEA`. One boss only.

## Strategy

One engine: **Kaufman efficiency ratio + EMA50 close cross on H4**. Stops are **2.5×ATR** (vol-normalized), target **2R**, one entry per symbol per day.

`config/enabled.json` lists which majors passed the walk-forward gates. The manager only trades those. Pairs that fail (GBPUSD on this rule) stay off. Re-run:

```bash
./hook scan
```

## Safety rails (enforced in the EA)

- Demo accounts only (`RequireDemo=true`)
- Max **0.10** lots per order
- Max **1** position per symbol, **3** hook-managed positions on the account
- Halt after **2%** daily equity drawdown
- Shared token `demo-local-hook` (localhost only)
- No martingale, no grid, no averaging down

The manager also caps **two same-way USD** positions (so you do not stack EURUSD+GBPUSD+AUDUSD shorts as one USD bet).

## Setup (Wine on this Mac)

MT5 for macOS is a Wine app, so Finder paste into the Windows “Data Folder” often fails. Copy from the Mac side instead:

```bash
./install_to_mt5.sh
```

That writes `PythonBridgeEA.mq5` into:

`~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/`

Then in MT5: Navigator → Experts → Refresh, open **PythonBridgeEA**, F7 to compile, **reattach** it to **each enabled symbol** (H4 chart), enable **Algo Trading**.

Leave **EnableTcp=false** when more than one chart is attached.

## 24/7 manager

A LaunchAgent on this Mac ticks every 20 seconds:

- Manage open trades (breakeven at 1R, trail at 1.5R)
- Ask each attached EA for the H4 signal and enter only if the enable-list, session, daily cap, portfolio slot, and USD cap allow it
- Never martingale or stack losers
- Restart itself if it crashes (`KeepAlive`)
- `caffeinate -i` so idle sleep is less likely while it runs

This is **not** a cloud robot. It needs:

1. This Mac **awake** (lid sleep still pauses it)
2. **MetaTrader 5 running**
3. **PythonBridgeEA** attached on each enabled chart with Algo Trading on

```bash
./hook daemon-install      # start at login, keep alive
./hook daemon-uninstall    # stop
./hook manage --interval 20   # foreground, useful to watch logs
```

Logs: `logs/manager.log`

True 24/7 away from this laptop means a small always-on machine later. Until then, plug the Mac in and turn off sleep.

## Commands

```bash
./hook scan
./hook status
./hook status --symbol EURUSD
./hook signal --symbol EURUSD
./hook buy EURUSD --risk 0.5
./hook halt
./hook resume
./hook close-all
```
