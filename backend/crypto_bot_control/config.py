from __future__ import annotations

import os
import sys
from pathlib import Path


APP_VERSION = "1.0.0"
APP_NAME = "Crypto Bot Control Center"


def _default_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "CryptoBotControl"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "crypto-bot-control"
    return Path.home() / ".local" / "share" / "crypto-bot-control"


def _default_config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "CryptoBotControl"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "crypto-bot-control"
    return Path.home() / ".config" / "crypto-bot-control"


DATA_DIR = Path(os.environ.get("CBC_DATA_DIR", str(_default_data_dir()))).resolve()
CONFIG_DIR = Path(os.environ.get("CBC_CONFIG_DIR", str(_default_config_dir()))).resolve()
DB_PATH = DATA_DIR / "app.db"
VAULT_KEY_PATH = CONFIG_DIR / "vault.key"
BOTS_DIR = DATA_DIR / "bots"
BACKUPS_DIR = DATA_DIR / "backups"
LOGS_DIR = DATA_DIR / "logs"

SECRET_NAME_HINTS = {
    "SECRET",
    "TOKEN",
    "KEY",
    "PASSWORD",
    "PASS",
    "CREDENTIAL",
    "PRIVATE",
    "APIKEY",
    "API_KEY",
}

COMMON_ENV_HINTS = [
    "BINANCE_API_KEY",
    "BINANCE_SECRET",
    "BINANCE_API_SECRET",
    "BINANCE_SECRET_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TRADING_MODE",
    "BOT_MODE",
    "MODE",
    "EXCHANGE",
]

ENTRY_POINT_CANDIDATES = [
    "main.py",
    "bot.py",
    "run.py",
    "run_bot.py",
    "app.py",
    "start.py",
    "__main__.py",
]

TELEGRAM_COMMAND_PATTERNS = [
    "/start",
    "/status",
    "/positions",
    "/stop",
    "/pause",
    "/resume",
    "/help",
    "/balance",
    "/pnl",
    "/close",
]

MAX_AUTO_RESTARTS = 3
AUTO_RESTART_WINDOW_SECONDS = 300
PROCESS_STOP_TIMEOUT = 10
FILE_WATCH_INTERVAL = 5.0
LOG_BUFFER_MAX_LINES = 2000


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    BOTS_DIR.mkdir(parents=True, exist_ok=True)
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
