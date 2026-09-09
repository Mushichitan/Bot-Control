from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="cbc-test-"))
os.environ["CBC_DATA_DIR"] = str(ROOT / "data")
os.environ["CBC_CONFIG_DIR"] = str(ROOT / "config")

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest

from crypto_bot_control.config import ensure_dirs
from crypto_bot_control.database import get_engine, reset_engine, session_scope
from crypto_bot_control import services


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    data = tmp_path / "data"
    cfg = tmp_path / "config"
    data.mkdir()
    cfg.mkdir()
    monkeypatch.setenv("CBC_DATA_DIR", str(data))
    monkeypatch.setenv("CBC_CONFIG_DIR", str(cfg))
    import crypto_bot_control.config as config

    config.DATA_DIR = data
    config.CONFIG_DIR = cfg
    config.DB_PATH = data / "app.db"
    config.VAULT_KEY_PATH = cfg / "vault.key"
    config.BOTS_DIR = data / "bots"
    config.BACKUPS_DIR = data / "backups"
    config.LOGS_DIR = data / "logs"
    import crypto_bot_control.project_manager as pm

    pm.BOTS_DIR = config.BOTS_DIR
    services._vault = services.SecretVault(key_path=config.VAULT_KEY_PATH)
    services._file_maps.clear()
    services._restart_window.clear()

    def fake_venv(path: Path) -> str:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        (path / "pyvenv.cfg").write_text("home = test")
        return sys.executable

    monkeypatch.setattr(services, "create_venv", fake_venv)
    monkeypatch.setattr(pm, "create_venv", fake_venv)

    def fake_install(py, root):
        return {"ok": True, "logs": ["skipped in tests"], "error": ""}

    monkeypatch.setattr(services, "install_dependencies", fake_install)
    reset_engine()
    ensure_dirs()
    get_engine(config.DB_PATH)
    yield
    for bid in list(services.registry.running_ids()):
        services.registry.stop(bid)
    reset_engine()


@pytest.fixture
def mock_bot_path() -> Path:
    return Path(__file__).resolve().parents[2] / "mock_bot"


@pytest.fixture
def session():
    with session_scope() as s:
        yield s
