"""Mock trading bot used for Control Center development.

Never connects to Binance. Never sends real Telegram messages.
Emits structured CBC_EVENT lines on stdout for the desktop app.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time

from config import get_mode, interval
from strategy import SYMBOLS
from utils.formatters import fmt_side

RUNNING = True
PAUSED = False

TELEGRAM_COMMANDS = ["/start", "/status", "/positions", "/stop", "/pause", "/resume"]


def emit(**payload) -> None:
    print("CBC_EVENT " + json.dumps(payload), flush=True)


def handle_signal(signum, frame) -> None:
    global RUNNING
    RUNNING = False


def main() -> None:
    global RUNNING, PAUSED
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    for required in ("BINANCE_API_KEY", "BINANCE_SECRET", "TELEGRAM_BOT_TOKEN"):
        if not os.getenv(required):
            emit(type="BOT_ERROR", message=f"Missing environment variable: {required}")
            sys.exit(2)

    if os.getenv("MOCK_CRASH") == "1":
        emit(type="BOT_ERROR", message="Simulated crash")
        sys.exit(1)

    mode = get_mode()
    wait = interval()
    print(f"Mock bot starting in {mode} mode", flush=True)
    emit(type="BOT_STARTED", mode=mode, message="Mock bot started")
    emit(type="EXCHANGE_CONNECTED", message="Binance connected (mock)")
    emit(type="TELEGRAM_SENT", message="Telegram connected (mock) /start /status /positions /stop /pause /resume")

    steps = [
        {
            "type": "SIGNAL_GENERATED",
            "signal_id": "S-001",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry": 67245,
            "tp": 68000,
            "sl": 66500,
            "confidence": 0.82,
            "strategy": "mock-trend",
            "mode": mode,
            "execution_status": "GENERATED",
            "telegram_status": "PUBLISHED",
            "binance_status": "QUEUED",
        },
        {
            "type": "POSITION_OPENED",
            "position_id": "1",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry": 67245,
            "current_price": 67280,
            "quantity": 0.01,
            "tp": 68000,
            "sl": 66500,
            "unrealized_pnl": 0.35,
            "pnl_pct": 0.05,
            "strategy": "mock-trend",
            "mode": mode,
        },
        {
            "type": "TELEGRAM_SENT",
            "message": "TELEGRAM Notification sent: BTCUSDT LONG opened",
        },
        {
            "type": "POSITION_UPDATED",
            "position_id": "1",
            "symbol": "BTCUSDT",
            "current_price": 67620,
            "unrealized_pnl": 3.75,
            "pnl_pct": 0.56,
        },
        {
            "type": "SIGNAL_GENERATED",
            "signal_id": "S-002",
            "symbol": "ETHUSDT",
            "side": "SHORT",
            "entry": 3520.5,
            "tp": 3480,
            "sl": 3560,
            "confidence": 0.71,
            "strategy": "mock-mean",
            "mode": mode,
            "execution_status": "GENERATED",
        },
        {
            "type": "POSITION_OPENED",
            "position_id": "2",
            "symbol": "ETHUSDT",
            "side": "SHORT",
            "entry": 3520.5,
            "current_price": 3518,
            "quantity": 0.2,
            "tp": 3480,
            "sl": 3560,
            "unrealized_pnl": 0.5,
            "strategy": "mock-mean",
            "mode": mode,
        },
        {
            "type": "TP_HIT",
            "position_id": "1",
            "symbol": "BTCUSDT",
            "exit": 68000,
            "realized_pnl": 18.42,
            "pnl_pct": 1.12,
            "close_reason": "TP",
        },
        {
            "type": "TELEGRAM_SENT",
            "message": "TELEGRAM Notification sent: TP HIT BTCUSDT +18.42",
        },
        {
            "type": "SL_HIT",
            "position_id": "2",
            "symbol": "ETHUSDT",
            "exit": 3560,
            "realized_pnl": -7.9,
            "pnl_pct": -1.12,
            "close_reason": "SL",
        },
        {
            "type": "SIGNAL_GENERATED",
            "signal_id": "S-003",
            "symbol": "SOLUSDT",
            "side": "LONG",
            "entry": 178.4,
            "tp": 185,
            "sl": 174,
            "strategy": "mock-trend",
            "mode": mode,
        },
        {
            "type": "POSITION_OPENED",
            "position_id": "3",
            "symbol": "SOLUSDT",
            "side": "LONG",
            "entry": 178.4,
            "current_price": 179.1,
            "quantity": 2,
            "tp": 185,
            "sl": 174,
            "unrealized_pnl": 1.4,
            "strategy": "mock-trend",
            "mode": mode,
        },
        {
            "type": "POSITION_CLOSED",
            "position_id": "3",
            "symbol": "SOLUSDT",
            "exit": 181.2,
            "realized_pnl": 5.6,
            "pnl_pct": 1.57,
            "close_reason": "MANUAL",
        },
        {
            "type": "HEARTBEAT",
            "message": "heartbeat",
        },
    ]

    idx = 0
    while RUNNING:
        if not PAUSED and idx < len(steps):
            step = steps[idx]
            emit(**step)
            extra = ""
            if step["type"] == "SIGNAL_GENERATED":
                extra = f"SIGNAL {step['symbol']} {fmt_side(step['side'])} Entry {step.get('entry')} TP {step.get('tp')} SL {step.get('sl')}"
                print(extra, flush=True)
            idx += 1
        elif not PAUSED:
            emit(type="HEARTBEAT", message="idle heartbeat")
            print("Mock bot idle heartbeat", flush=True)
        time.sleep(wait)

    emit(type="BOT_STOPPED", message="Mock bot stopped")
    print("Mock bot stopped", flush=True)


if __name__ == "__main__":
    main()
