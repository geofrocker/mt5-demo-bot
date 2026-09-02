# MT5 demo bot

**PythonBridgeEA** trades a **MetaTrader 5 demo** account on its own. It is not a human fund manager, not financial advice, and it will not print money.

Python is optional (status, halt, install, scan). Do **not** run the old Python manager alongside the EA.

Keep MT5 open with **Algo Trading** enabled. If the terminal is closed, nothing trades.

```
PythonBridgeEA on EURUSD, USDCAD, EURJPY H4
    -> demo account
optional: hook.cmd status / halt
```

## Strategy

Entry: **Kaufman efficiency ratio + EMA50 close cross on H4**. One entry per symbol per day.

Exit (set at fill, then left alone):

- Initial stop **2.5×ATR**
- Take profit **2R**
- Risk **1.0%** of equity per trade (0.10 lot cap still applies)
- No trailing stop, no breakeven move, no scale-out

`config/enabled.json` is the research enable-list. Attach the EA only on those symbols. Re-run `hook scan` after strategy changes.

## Safety rails (enforced in the EA)

- Demo accounts only (`RequireDemo=true`)
- Max **0.10** lots per order
- Max **1** position per symbol, **3** magic positions on the account
- Max **2** same-way USD positions
- Halt after **2%** daily equity drawdown
- `AutoTrade=true` by default
- Shared token `demo-local-hook` (localhost only, optional hook)
- No martingale, no grid, no averaging down

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

Then in MT5: Navigator → Experts → Refresh, **reattach** **PythonBridgeEA** to **each enabled symbol** (H4 chart) so `RiskPercent=1.0` and the v1.11 HUD meters load, enable **Algo Trading**. Leave **EnableTcp=false** when more than one chart is attached.

The on-chart panel has two meters. **Last H4 ER** is the closed candle the entry uses (white tick at 0.40; green = armed, amber = chop). **To signal** is a live forming-H4 countdown: fill is the weaker of ER-to-gate and distance-to-EMA50-cross. 100% green means both conditions are true on the current H4 and it will evaluate when that bar closes (unless the label says blocked).

## 24/7

The EA is the manager. No Python daemon. For always-on trading, keep MT5 running (this PC awake, or MQL5 VPS / a Windows VPS later).

Remove a leftover Python task:

```bat
hook.cmd daemon-uninstall
```

Python is still useful for:

```bat
hook.cmd status --symbol EURUSD
hook.cmd halt
hook.cmd scan
```

## Commands

Windows uses `hook.cmd`; macOS uses `./hook`. Same subcommands:

```
paths
install
scan
status
status --symbol EURUSD
signal --symbol EURUSD
buy EURUSD --risk 1.0
halt
resume
close-all
```
