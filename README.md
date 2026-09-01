# MT5 demo bot

Python control of a **MetaTrader 5 demo** account. It is not a human fund manager, not financial advice, and it will not print money.

The same Python hook auto-detects the OS:

- **Windows:** native MT5, Common Files under `%APPDATA%`, Task Scheduler daemon
- **macOS:** Wine-wrapped MT5, Common Files under `~/Library/Application Support`, LaunchAgent daemon

Keep MT5 open with **Algo Trading** enabled. If the terminal is closed, nothing trades.

```
this chat / hook
    -> python/mt5_hook
        -> MT5 Common Files drop
            -> PythonBridgeEA on each enabled chart
                -> demo account
```

Do **not** attach `ConservativeTrendEA` at the same time as `PythonBridgeEA`. One boss only.

## Strategy

One engine: **Kaufman efficiency ratio + EMA50 close cross on H4**. Stops are **2.5×ATR** (vol-normalized), target **2R**, one entry per symbol per day.

`config/enabled.json` lists which majors passed the walk-forward gates. The manager only trades those. Pairs that fail (GBPUSD on this rule) stay off. Re-run `hook scan` (see commands below).

## Safety rails (enforced in the EA)

- Demo accounts only (`RequireDemo=true`)
- Max **0.10** lots per order
- Max **1** position per symbol, **3** hook-managed positions on the account
- Halt after **2%** daily equity drawdown
- Shared token `demo-local-hook` (localhost only)
- No martingale, no grid, no averaging down

The manager also caps **two same-way USD** positions (so you do not stack EURUSD+GBPUSD+AUDUSD shorts as one USD bet).

## Setup

Needs Python 3.9+ and MetaTrader 5 logged into a **demo** account. Then:

**Windows** (cmd / PowerShell):

```bat
install_to_mt5.cmd
hook.cmd paths
```

**macOS** (Wine MT5; Finder paste into the Windows data folder often fails):

```bash
./install_to_mt5.sh
./hook paths
```

Both call the same installer. It copies `PythonBridgeEA.mq5` into the local Experts folder and compiles with MetaEditor (via `wine64` on a Mac). If compile fails, open the file in MetaEditor and press F7.

Typical Experts paths:

- Windows: `%APPDATA%\MetaQuotes\Terminal\<id>\MQL5\Experts\`
- macOS: `~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/`

Then in MT5: Navigator → Experts → Refresh, attach **PythonBridgeEA** to **each enabled symbol** (H4 chart), enable **Algo Trading**. Leave **EnableTcp=false** when more than one chart is attached.

## 24/7 manager

`daemon-install` picks the host OS:

- **Windows:** Task Scheduler job `mt5-demo-manager` (restarts on crash, `SetThreadExecutionState` against idle sleep)
- **macOS:** LaunchAgent `com.mt5-demo-bot.manager` wrapped in `caffeinate -i`

Every 20 seconds it:

- Manages open trades (breakeven at 1R, trail at 1.5R)
- Asks each attached EA for the H4 signal and enters only if the enable-list, session, daily cap, portfolio slot, and USD cap allow it
- Never martingales or stacks losers

This is **not** a cloud robot. It needs the machine **awake**, **MT5 running**, and **PythonBridgeEA** attached with Algo Trading on.

**Windows:**

```bat
hook.cmd daemon-install
hook.cmd daemon-uninstall
hook.cmd manage --interval 20 --no-sleep
```

**macOS:**

```bash
./hook daemon-install
./hook daemon-uninstall
./hook manage --interval 20 --no-sleep
```

Logs: `logs/manager.log`

Lid/sleep still pauses trading. Plug in and turn off sleep for overnight runs.

## Commands

Windows uses `hook.cmd`; macOS uses `./hook`. Same subcommands:

```
paths
install
scan
status
status --symbol EURUSD
signal --symbol EURUSD
buy EURUSD --risk 0.5
halt
resume
close-all
```
