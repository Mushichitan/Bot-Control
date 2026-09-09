from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

EVENT_TYPES = {
    "BOT_STARTED",
    "BOT_STOPPED",
    "BOT_ERROR",
    "SIGNAL_GENERATED",
    "ORDER_SUBMITTED",
    "ORDER_FILLED",
    "POSITION_OPENED",
    "POSITION_UPDATED",
    "POSITION_CLOSED",
    "TP_HIT",
    "SL_HIT",
    "CANCELLED",
    "TELEGRAM_SENT",
    "TELEGRAM_RECEIVED",
    "EXCHANGE_CONNECTED",
    "EXCHANGE_DISCONNECTED",
    "HEARTBEAT",
    "LOG",
}

CATEGORY_MAP = {
    "SIGNAL_GENERATED": "SIGNAL",
    "ORDER_SUBMITTED": "BINANCE",
    "ORDER_FILLED": "BINANCE",
    "POSITION_OPENED": "POSITION",
    "POSITION_UPDATED": "POSITION",
    "POSITION_CLOSED": "POSITION",
    "TP_HIT": "TP",
    "SL_HIT": "SL",
    "TELEGRAM_SENT": "TELEGRAM",
    "TELEGRAM_RECEIVED": "TELEGRAM",
    "EXCHANGE_CONNECTED": "BINANCE",
    "EXCHANGE_DISCONNECTED": "BINANCE",
    "BOT_ERROR": "ERROR",
    "BOT_STARTED": "SYSTEM",
    "BOT_STOPPED": "SYSTEM",
    "CANCELLED": "SYSTEM",
    "HEARTBEAT": "SYSTEM",
    "LOG": "SYSTEM",
}

CBC_PREFIX = "CBC_EVENT "


def parse_structured_line(line: str) -> dict | None:
    text = (line or "").strip()
    if not text:
        return None
    payload = None
    if text.startswith(CBC_PREFIX):
        raw = text[len(CBC_PREFIX) :].strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
    elif text.startswith("{") and '"type"' in text:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    event_type = str(payload.get("type") or payload.get("event_type") or "").upper()
    if event_type not in EVENT_TYPES:
        return None
    payload["type"] = event_type
    payload["category"] = CATEGORY_MAP.get(event_type, "SYSTEM")
    return payload


_SIGNAL_RE = re.compile(
    r"(?i)signal\s+(\w+)\s+(LONG|SHORT)\b(?:.*entry\s+([\d.]+))?(?:.*tp\s+([\d.]+))?(?:.*sl\s+([\d.]+))?"
)
_POS_OPEN_RE = re.compile(r"(?i)position opened\s+(\w+)\s+(LONG|SHORT)")
_TP_RE = re.compile(r"(?i)tp hit\s+(\w+)")
_SL_RE = re.compile(r"(?i)sl hit\s+(\w+)")


def parse_heuristic_line(line: str) -> dict | None:
    text = (line or "").strip()
    if not text:
        return None
    m = _SIGNAL_RE.search(text)
    if m:
        return {
            "type": "SIGNAL_GENERATED",
            "category": "SIGNAL",
            "symbol": m.group(1),
            "side": m.group(2).upper(),
            "entry": _f(m.group(3)),
            "tp": _f(m.group(4)),
            "sl": _f(m.group(5)),
            "message": text,
        }
    m = _POS_OPEN_RE.search(text)
    if m:
        return {
            "type": "POSITION_OPENED",
            "category": "POSITION",
            "symbol": m.group(1),
            "side": m.group(2).upper(),
            "message": text,
        }
    m = _TP_RE.search(text)
    if m:
        return {"type": "TP_HIT", "category": "TP", "symbol": m.group(1), "message": text}
    m = _SL_RE.search(text)
    if m:
        return {"type": "SL_HIT", "category": "SL", "symbol": m.group(1), "message": text}
    lower = text.lower()
    if "exchange connected" in lower or "binance connected" in lower:
        return {"type": "EXCHANGE_CONNECTED", "category": "BINANCE", "message": text}
    if "telegram connected" in lower:
        return {"type": "TELEGRAM_SENT", "category": "TELEGRAM", "message": text}
    return None


def _f(val: str | None) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except ValueError:
        return None


def parse_line(line: str) -> dict | None:
    return parse_structured_line(line) or parse_heuristic_line(line)


def format_activity_message(event: dict) -> str:
    t = event.get("type", "")
    symbol = event.get("symbol", "")
    side = event.get("side", "")
    if t == "SIGNAL_GENERATED":
        parts = [f"SIGNAL {symbol} {side}".strip()]
        if event.get("entry") is not None:
            parts.append(f"Entry {event['entry']}")
        if event.get("tp") is not None:
            parts.append(f"TP {event['tp']}")
        if event.get("sl") is not None:
            parts.append(f"SL {event['sl']}")
        return " ".join(parts)
    if t == "POSITION_OPENED":
        return f"ORDER {symbol} position opened".strip()
    if t == "TP_HIT":
        pnl = event.get("realized_pnl")
        extra = f" P&L {pnl:+.2f} USDT" if isinstance(pnl, (int, float)) else ""
        return f"TP HIT {symbol}{extra}".strip()
    if t == "SL_HIT":
        pnl = event.get("realized_pnl")
        extra = f" P&L {pnl:+.2f} USDT" if isinstance(pnl, (int, float)) else ""
        return f"SL HIT {symbol}{extra}".strip()
    if t == "TELEGRAM_SENT":
        return event.get("message") or "TELEGRAM Notification sent"
    return event.get("message") or t
