from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from .config import APP_NAME, APP_VERSION, ensure_dirs
from .database import get_engine, session_scope
from .api import app, mount_frontend
from . import services as bot_services


def _frontend_dir() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "frontend" / "dist",
        here.parents[1] / "frontend" / "dist",
        Path(__file__).resolve().parents[2] / "web" / "dist",
    ]
    for c in candidates:
        if (c / "index.html").exists():
            return c
    return candidates[0]


def create_app():
    ensure_dirs()
    get_engine()
    try:
        with session_scope() as session:
            bot_services.ensure_demo_bot(session)
    except BaseException:
        pass
    static = _frontend_dir()
    if (static / "index.html").exists():
        mount_frontend(static)
    return app


def run(host: str = "127.0.0.1", port: int = 8787, open_browser: bool = True) -> None:
    create_app()
    if open_browser:
        def _open():
            time.sleep(1.2)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=_open, daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="info")


def cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} v{APP_VERSION}")
    parser.add_argument("--host", default=os.environ.get("CBC_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CBC_PORT", "8787")))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    run(host=args.host, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    cli()
