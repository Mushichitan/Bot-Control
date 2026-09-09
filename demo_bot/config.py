import os


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


class Config:
    def __init__(self):
        self.bot_name = os.getenv("BOT_NAME", "Demo Trading Bot")
        self.mode = (os.getenv("TRADING_MODE") or os.getenv("BOT_MODE") or os.getenv("MODE") or "PAPER").upper()
        self.symbol = os.getenv("SYMBOL", "BTCUSDT")
        self.cycle_seconds = max(0.2, _float("CYCLE_SECONDS", 1))
        self.initial_balance = _float("INITIAL_BALANCE", 10000)
        self.position_size_usdt = _float("POSITION_SIZE_USDT", 500)
        self.tp_percent = _float("TP_PERCENT", 0.8)
        self.sl_percent = _float("SL_PERCENT", 0.5)
        self.report_interval_seconds = _float("REPORT_INTERVAL_SECONDS", 30)
        self.start_price = _float("START_PRICE", 67000)
