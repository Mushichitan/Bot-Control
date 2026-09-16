# Crypto Bot Control Center User Guide

## Installation

### Windows
1. Install Python 3.10+ from python.org and enable "Add python.exe to PATH".
2. Double-click `start.bat` in the application folder.
3. The Control Center opens in your browser at http://127.0.0.1:8787.

### From source
```bash
python3 -m pip install --break-system-packages -r backend/requirements.txt
cd frontend && npm install && npm run build && cd ..
python3 run.py
```

The desktop app and the bot project stay separate. Updating the app never rewrites your trading code.

## First-time setup

1. Open the app.
2. Click **Add Bot**.
3. Enter a name.
4. Paste the full path of your Python bot **project folder** (not a single file).
   In this cloud workspace use a server path such as `/workspace/demo_bot`.
5. Click **Analyze**. Review detected entry points.
6. Save the bot.
7. Open **Settings -> Environment** and add secrets (or import a `.env` file into the vault).
8. Choose PAPER, TESTNET, or LIVE.
9. Click **Start**.

## Adding a bot / importing a project

The app copies the folder into a managed directory and keeps the original structure.

Supported contents include multiple `.py` files, `requirements.txt`, `pyproject.toml`, strategy modules, and runtime assets.

Analysis is static. The project is not executed until you press Start.

If several entry points are found, you choose one.

## Configuring .env / secrets

Secrets live in an encrypted vault, not in the bot folder.

- Add variables manually
- Import a `.env` file
- Values are masked in the UI
- Required variables are validated before start
- Logs, exports, and diagnostics redact secret values

Do not paste API keys into chat or support tickets.

A leftover plaintext `.env` inside the imported project produces a warning.

## Paper / Testnet / Live

The app does not silently change the bot's internal mode. Map a variable such as `TRADING_MODE` if the bot uses one.

LIVE always shows a confirmation:

> This bot may place real orders using configured exchange credentials.

The app never asks for Binance withdrawal permission.

## Starting and stopping

- **Start / Stop / Restart** always verify process state
- **Pause / Resume** appear only if the imported bot actually exposes those commands
- Default policy: the bot stops when the app closes
- Optional: keep the bot running when the app closes
- Optional bounded auto-restart after crash (max 3 attempts / 5 minutes)

Statuses: STOPPED, STARTING, RUNNING, PAUSED, STOPPING, CRASHED, ERROR, UPDATING

Any position still marked OPEN from a previous process is closed on Start with reason `BOT_RESTART`, so the dashboard never shows phantom open positions a fresh process cannot manage.

## Telegram

The Control Center does not replace Telegram. If the bot already sends messages, it keeps doing so.

Detected commands (for example `/status`) are listed in Settings. Buttons are shown only for detected commands.

## Bot version management

When project files change, the app shows:

- Current version
- Added / modified / removed files
- Activate new version
- Rollback to a known-good snapshot

Changed code is never auto-activated while LIVE.

Settings also lets you edit strategy files (save, clean comments, delete) with a local backup. Saving does not auto-activate a LIVE running bot.

## Scan, delay, and take profits

Settings -> Scan / Delay / Take profits:

- Scan mode: ALL SUPPORTED loads live Binance USD-M perpetual symbols from `fapi.binance.com/fapi/v1/exchangeInfo` plus researched US TradFi contracts. Prices come from `fapi.binance.com/fapi/v1/ticker/price` and klines; no fake prices and no coin-select list. Binance Futures and US TradFi can be chosen as the scan universe. If Binance is unreachable, scanning stops instead of inventing prices.
- Open position limit: 1-20 concurrent positions (default 3). Change it on the Dashboard (3 / 5 / 10 / 20) or in Settings (1 / 2 / 3 / 5 / 10 or custom). New signals are rejected with reason `POSITION_LIMIT` after the limit until a position closes. The Dashboard shows `Open positions X / limit N`. Restart the bot to apply.
- Startup delay: 3 / 5 / 10 minutes or custom seconds (default 3 minutes). Existing open positions stay manageable during the wait.
- Trade gap: wait after a close before the next new trade (3 / 5 / 10 minutes or custom)
- Take-profit count: 1 to 5 levels. Live signals use Hermis ATR stops and take-profits from Binance 15m klines, not dummy percents.
- Reset all data: clears signals, positions, trades, events, reports, and logs for that bot. Stop the bot first. Strategy files and secrets are kept.

Restart the bot after changing scan, limit, delay, gap, or TP values.

## Trade timing, TP hits, and booked profit

Positions and closed trades show:

- Opened / Closed: local date-time for each trade
- Dur: how long the position was open
- TPs: how many take-profit levels were hit out of the total (for example `2/4`)
- Booked: profit already realized from partial TPs while the position is still open
- P&L: unrealized while open, final realized after close

Partial TPs close only a slice, so the position stays open with the remaining quantity. Each hit is recorded, and the booked amount carries into the closed trade.

## Backups

Settings -> Backup exports configuration, versions, and events. Secrets are excluded by default.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Bot failed to start | Settings -> Validate. Missing env vars are named, not shown. |
| Dependencies error | Install dependencies from Settings and read the error text |
| No signals / positions | The bot must emit events (stdout `CBC_EVENT {json}` or supported log lines). The app never invents trades. |
| LIVE blocked | Confirm the LIVE dialog |
| Version activate blocked | Stop a LIVE bot before activating new code |
| Crash loop | Auto-restart stops after 3 failures and shows ERROR |

## Security notes

- Secrets are encrypted locally; the vault key is stored separately from the database
- No withdrawal workflow exists
- Imported Python is untrusted executable code
- Dependency installs are explicit
- Log export redacts secrets

## Updating the app

App version and bot version are shown separately (for example App v1.0.0, Bot v1.0). App updates do not modify bot files.
