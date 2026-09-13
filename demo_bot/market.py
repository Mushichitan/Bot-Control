from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BINANCE_FAPI_TICKER = "https://fapi.binance.com/fapi/v1/ticker/price"
BINANCE_FAPI_KLINES = "https://fapi.binance.com/fapi/v1/klines"


def _get_json(url: str, timeout: float = 15.0):
    req = Request(url, headers={"User-Agent": "crypto-bot-control/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_ticker_prices(payload) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(payload, list):
        return out
    for item in payload:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        try:
            price = float(item.get("price"))
        except (TypeError, ValueError):
            continue
        if symbol and price > 0:
            out[symbol] = price
    return out


def parse_klines(payload) -> list[dict]:
    rows = []
    if not isinstance(payload, list):
        return rows
    for item in payload:
        if not isinstance(item, (list, tuple)) or len(item) < 6:
            continue
        try:
            row = {
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
            }
        except (TypeError, ValueError):
            continue
        if row["close"] > 0:
            rows.append(row)
    return rows


class BinanceMarket:
    def __init__(self, interval: str = "15m", kline_limit: int = 80):
        self.interval = interval
        self.kline_limit = kline_limit
        self.prices: dict[str, float] = {}
        self.klines: dict[str, list[dict]] = {}
        self._prices_at = 0.0
        self._kline_at: dict[str, float] = {}
        self.last_error = ""
        self.connected = False

    def fetch_prices(self, wanted: list[str] | None = None, ttl: float = 2.0) -> dict[str, float]:
        now = time.monotonic()
        if self.prices and now - self._prices_at < ttl:
            return self._filter(wanted)
        try:
            payload = _get_json(BINANCE_FAPI_TICKER, timeout=15.0)
            parsed = parse_ticker_prices(payload)
            if not parsed:
                raise RuntimeError("Binance ticker returned no prices")
            self.prices = parsed
            self._prices_at = now
            self.connected = True
            self.last_error = ""
        except (URLError, HTTPError, TimeoutError, json.JSONDecodeError, OSError, ValueError, RuntimeError) as exc:
            self.last_error = f"Binance live prices unavailable: {exc}"
            self.connected = False
            if not self.prices:
                raise RuntimeError(self.last_error) from exc
        return self._filter(wanted)

    def fetch_klines(self, symbol: str, ttl: float = 20.0) -> list[dict]:
        symbol = str(symbol or "").upper()
        if not symbol:
            return []
        now = time.monotonic()
        cached = self.klines.get(symbol) or []
        if cached and now - self._kline_at.get(symbol, 0) < ttl:
            return cached
        query = urlencode({"symbol": symbol, "interval": self.interval, "limit": str(self.kline_limit)})
        try:
            payload = _get_json(f"{BINANCE_FAPI_KLINES}?{query}", timeout=15.0)
            rows = parse_klines(payload)
            if rows:
                self.klines[symbol] = rows
                self._kline_at[symbol] = now
                self.last_error = ""
                return rows
        except (URLError, HTTPError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
            return cached
        return cached

    def _filter(self, wanted: list[str] | None) -> dict[str, float]:
        if not wanted:
            return dict(self.prices)
        out = {}
        for symbol in wanted:
            price = self.prices.get(str(symbol).upper())
            if price is not None:
                out[str(symbol).upper()] = price
        return out
