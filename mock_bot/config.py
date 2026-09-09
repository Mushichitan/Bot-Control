import os


def get_mode() -> str:
    return os.getenv("TRADING_MODE") or os.getenv("BOT_MODE") or os.getenv("MODE") or "PAPER"


def interval() -> float:
    try:
        return max(0.2, float(os.getenv("MOCK_INTERVAL", "1.5")))
    except ValueError:
        return 1.5
