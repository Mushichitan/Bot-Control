import os

from universe import (
    fetch_binance_futures_universe,
    hardcoded_tradfi_symbols,
    market_type_for,
    normalize_scan_mode,
    symbols_for_mode,
)


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _csv(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default) or ""
    return [p.strip().upper() for p in raw.replace(";", ",").split(",") if p.strip()]


def _floats(name: str, default: str) -> list[float]:
    out = []
    for part in (os.getenv(name, default) or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(float(part))
        except ValueError:
            continue
    return out


def fetch_binance_usdm_symbols() -> list[str]:
    universe = fetch_binance_futures_universe()
    crypto = list(universe.get("crypto") or [])
    if not crypto:
        raise RuntimeError(universe.get("crypto_error") or "Binance USD-M futures universe unavailable")
    return crypto


class Config:
    def __init__(self):
        self.bot_name = os.getenv("BOT_NAME", "Demo Trading Bot")
        self.mode = (os.getenv("TRADING_MODE") or os.getenv("BOT_MODE") or os.getenv("MODE") or "PAPER").upper()
        self.scan_mode = normalize_scan_mode(os.getenv("SCAN_MODE") or "ALL")
        self.universe_error = ""
        self.crypto_symbols = _csv("CRYPTO_SYMBOLS", "")
        self.tradfi_symbols = _csv("TRADFI_SYMBOLS", "")
        if not self.tradfi_symbols:
            self.tradfi_symbols = hardcoded_tradfi_symbols()
        self.tradfi_set = set(self.tradfi_symbols)
        if self.scan_mode == "BINANCE":
            self.symbols = self.crypto_symbols
        elif self.scan_mode == "TRADFI":
            self.symbols = self.tradfi_symbols
        else:
            self.symbols = _csv("ALL_SYMBOLS", "")
            if not self.symbols:
                self.symbols = self.crypto_symbols + [s for s in self.tradfi_symbols if s not in set(self.crypto_symbols)]
        self.symbol = self.symbols[0] if self.symbols else ""
        self.cycle_seconds = max(0.2, _float("CYCLE_SECONDS", 1))
        self.initial_balance = _float("INITIAL_BALANCE", 10000)
        self.position_size_usdt = _float("POSITION_SIZE_USDT", 500)
        self.tp_count = min(5, max(1, _int("TP_COUNT", 2)))
        percents = _floats("TP_PERCENTS", "")
        if not percents:
            base = _float("TP_PERCENT", 0.8)
            percents = [round(base * (i + 1), 4) for i in range(self.tp_count)]
        while len(percents) < self.tp_count:
            percents.append(round(percents[-1] + percents[0], 4))
        self.tp_percents = percents[: self.tp_count]
        self.tp_percent = self.tp_percents[0]
        self.sl_percent = _float("SL_PERCENT", 0.5)
        self.startup_delay_seconds = max(0, _int("STARTUP_DELAY_SECONDS", 180))
        self.trade_gap_seconds = max(0, _int("TRADE_GAP_SECONDS", 180))
        self.max_open_positions = min(20, max(1, _int("MAX_OPEN_POSITIONS", 3)))
        self.report_interval_seconds = _float("REPORT_INTERVAL_SECONDS", 30)
        self.start_price = _float("START_PRICE", 67000)

    def market_type(self, symbol: str) -> str:
        return market_type_for(symbol, self.tradfi_set)

    def load_live_universe(self) -> dict:
        universe = fetch_binance_futures_universe()
        self.crypto_symbols = list(universe.get("crypto") or [])
        self.tradfi_symbols = list(universe.get("tradfi") or hardcoded_tradfi_symbols())
        self.tradfi_set = set(self.tradfi_symbols)
        self.symbols = symbols_for_mode(universe, self.scan_mode)
        self.symbol = self.symbols[0] if self.symbols else ""
        if self.scan_mode in {"ALL", "BINANCE"} and not self.crypto_symbols:
            self.universe_error = universe.get("crypto_error") or "Binance USD-M futures universe unavailable"
        elif not self.symbols:
            self.universe_error = universe.get("crypto_error") or "Scan universe unavailable"
        else:
            self.universe_error = ""
        return universe
