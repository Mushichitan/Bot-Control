# Crypto Bot Control Center

Windows-friendly desktop control center for **user-supplied Python crypto trading bots**.

The app is the controller, monitor, and secure settings layer. Your Python project remains the source of truth for trading logic. The app never invents signals, orders, TP/SL, or P&L.

## What it does

- Import a complete multi-file Python bot project
- Isolated per-bot environment
- Encrypted environment/secret vault
- Start / stop / restart with verified process state
- Dashboard for bot, Binance, Telegram, P&L, signals, positions, activity, logs
- Hourly and 4-hour reports
- Project change detection, version activation, rollback
- LIVE confirmation and no-withdrawal safety

## Launch (Windows)

1. Install Python 3.10+.
2. Run `start.bat`.
3. Use the Control Center in the browser window that opens.

## Launch (development)

```bash
python3 -m pip install --break-system-packages -r backend/requirements.txt
cd frontend && npm install && npm run build && cd ..
python3 run.py --host 127.0.0.1 --port 8787
```

Open http://127.0.0.1:8787

## Import your real bot

In the UI: **Add Bot -> project folder path -> Analyze -> configure environment -> Start**.

Use PAPER or TESTNET first. LIVE requires an explicit confirmation.

A safe mock bot is included in `mock_bot/` for a first run. It never talks to Binance or Telegram.

Optional structured events from any bot (stdout):

```text
CBC_EVENT {"type":"SIGNAL_GENERATED","symbol":"BTCUSDT","side":"LONG","entry":67245,"tp":68000,"sl":66500}
```

## Tests

```bash
python3 -m pytest backend/tests
```

## Docs

See `docs/USER_GUIDE.md` for setup, security, Telegram, versions, backups, and troubleshooting.
