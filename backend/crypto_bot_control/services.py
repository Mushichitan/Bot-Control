from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from . import config as app_config
from .config import (
    APP_VERSION,
    AUTO_RESTART_WINDOW_SECONDS,
    LOG_BUFFER_MAX_LINES,
    MAX_AUTO_RESTARTS,
    ensure_dirs,
)
from .database import (
    AuditLog,
    Bot,
    BotVersion,
    EnvVar,
    Event,
    LogLine,
    Position,
    Signal,
    Trade,
    session_scope,
    utcnow,
)
from .events import CATEGORY_MAP, format_activity_message, parse_line
from .process_controller import BotProcess, ProcessRegistry, process_alive
from .project_analyzer import analyze_project
from .project_manager import (
    allocate_slug,
    bot_dirs,
    copy_project,
    create_venv,
    diff_file_maps,
    install_dependencies,
    list_relative_files,
    parse_env_file,
    project_fingerprint,
    restore_snapshot,
    snapshot_version,
    venv_python,
)
from .security import SecretVault, is_safe_relative_path, is_secret_name, mask_secret, redact_text

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


registry = ProcessRegistry()
_vault = SecretVault()
_restart_window: dict[int, list[float]] = {}
_file_maps: dict[int, dict[str, str]] = {}
_lock = threading.Lock()
_watch_started = False


def _audit(session: Session, action: str, bot_id: int | None = None, details: str = "") -> None:
    session.add(AuditLog(bot_id=bot_id, action=action, details=redact_text(details)))


def _bot_or_404(session: Session, bot_id: int) -> Bot:
    bot = session.get(Bot, bot_id)
    if not bot:
        raise ValueError("Bot not found")
    return bot


def list_bots(session: Session) -> list[dict]:
    bots = session.query(Bot).order_by(Bot.id.asc()).all()
    return [bot_to_dict(b, session) for b in bots]


def bot_to_dict(bot: Bot, session: Session | None = None) -> dict:
    running = registry.is_running(bot.id) or process_alive(bot.pid)
    status = bot.status
    if running and status in {"STOPPED", "CRASHED", "ERROR"}:
        status = "RUNNING"
    if not running and status in {"RUNNING", "STARTING", "PAUSED"}:
        status = "STOPPED"
    version_label = ""
    if bot.active_version_id and session:
        ver = session.get(BotVersion, bot.active_version_id)
        if ver:
            version_label = ver.version_label
    return {
        "id": bot.id,
        "name": bot.name,
        "slug": bot.slug,
        "source_path": bot.source_path,
        "managed_path": bot.managed_path,
        "entry_point": bot.entry_point,
        "python_executable": bot.python_executable,
        "trading_mode": bot.trading_mode,
        "status": status,
        "pause_supported": bot.pause_supported,
        "keep_running_on_app_close": bot.keep_running_on_app_close,
        "auto_start": bot.auto_start,
        "auto_restart": bot.auto_restart,
        "pid": registry.pid(bot.id) or bot.pid,
        "binance_status": bot.binance_status,
        "telegram_status": bot.telegram_status,
        "env_status": bot.env_status,
        "deps_status": bot.deps_status,
        "last_heartbeat": bot.last_heartbeat.isoformat() if bot.last_heartbeat else None,
        "last_error": bot.last_error,
        "active_version_id": bot.active_version_id,
        "version_label": version_label,
        "app_version": APP_VERSION,
        "analysis": json.loads(bot.analysis_json or "{}"),
        "created_at": bot.created_at.isoformat() if bot.created_at else None,
        "updated_at": bot.updated_at.isoformat() if bot.updated_at else None,
    }


def analyze_path(path: str) -> dict:
    root = Path(path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("Project folder does not exist")
    analysis = analyze_project(root)
    analysis["untrusted_code_warning"] = (
        "Imported Python code can execute with your user account permissions. "
        "The app analyzes files statically and will not run the project until you start it."
    )
    return analysis


def import_bot(session: Session, name: str, source_path: str, entry_point: str | None = None) -> dict:
    ensure_dirs()
    source = Path(source_path).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise ValueError("Project folder does not exist")
    existing = {b.slug for b in session.query(Bot).all()}
    if session.query(Bot).filter(Bot.name == name).first():
        raise ValueError("A bot with this name already exists")
    slug = allocate_slug(name, existing)
    dirs = bot_dirs(slug)
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    copy_project(source, dirs["current"])
    analysis = analyze_project(dirs["current"])
    entries = analysis.get("entry_points") or []
    chosen = entry_point or (entries[0] if entries else "")
    if chosen and not is_safe_relative_path(chosen):
        raise ValueError("Invalid entry point")
    if entry_point and entry_point not in entries and not (dirs["current"] / entry_point).exists():
        raise ValueError("Selected entry point was not found in the project")
    py = ""
    deps_status = "UNKNOWN"
    try:
        py = create_venv(dirs["venv"])
        deps_status = "READY"
    except Exception:
        deps_status = "ERROR"
        py = sys.executable
    bot = Bot(
        name=name,
        slug=slug,
        source_path=str(source),
        managed_path=str(dirs["current"]),
        entry_point=chosen,
        python_executable=py,
        venv_path=str(dirs["venv"]),
        trading_mode="PAPER",
        status="STOPPED",
        pause_supported=bool(analysis.get("capabilities", {}).get("pause_supported")),
        env_status="INVALID" if analysis.get("env_names") else "UNKNOWN",
        deps_status=deps_status,
        analysis_json=json.dumps(analysis),
        binance_status="UNKNOWN",
        telegram_status="UNKNOWN",
    )
    session.add(bot)
    session.flush()
    for key in analysis.get("env_names") or []:
        session.add(
            EnvVar(
                bot_id=bot.id,
                key=key,
                encrypted_value="",
                is_secret=is_secret_name(key),
                is_required=is_secret_name(key),
            )
        )
    fp = project_fingerprint(dirs["current"])
    snap = snapshot_version(dirs["current"], dirs["versions"], "v1.0")
    version = BotVersion(
        bot_id=bot.id,
        version_label="v1.0",
        snapshot_path=str(snap),
        fingerprint=fp,
        change_summary="Initial import",
        is_active=True,
        is_known_good=True,
    )
    session.add(version)
    session.flush()
    bot.active_version_id = version.id
    _file_maps[bot.id] = list_relative_files(dirs["current"])
    _audit(session, "BOT_CREATED", bot.id, f"Imported from {source}")
    _audit(session, "PROJECT_IMPORTED", bot.id, f"files={analysis.get('file_count')}")
    return bot_to_dict(bot, session)


def install_bot_deps(session: Session, bot_id: int) -> dict:
    bot = _bot_or_404(session, bot_id)
    py = bot.python_executable or create_venv(Path(bot.venv_path))
    bot.python_executable = py
    result = install_dependencies(py, Path(bot.managed_path))
    bot.deps_status = "READY" if result["ok"] else "ERROR"
    if not result["ok"]:
        bot.last_error = redact_text(result.get("error") or "Dependency install failed")
    _audit(session, "DEPENDENCIES_INSTALLED", bot.id, "ok" if result["ok"] else "failed")
    return {
        "ok": result["ok"],
        "deps_status": bot.deps_status,
        "error": result.get("error") or "",
        "logs": [redact_text(x)[-4000:] for x in (result.get("logs") or [])],
    }


def list_env(session: Session, bot_id: int) -> list[dict]:
    bot = _bot_or_404(session, bot_id)
    rows = session.query(EnvVar).filter(EnvVar.bot_id == bot.id).order_by(EnvVar.key.asc()).all()
    out = []
    for row in rows:
        value = ""
        if row.encrypted_value:
            try:
                value = _vault.decrypt(row.encrypted_value)
            except Exception:
                value = ""
        out.append(
            {
                "id": row.id,
                "key": row.key,
                "value": mask_secret(value) if row.is_secret else value,
                "has_value": bool(row.encrypted_value),
                "is_secret": row.is_secret,
                "is_required": row.is_required,
            }
        )
    return out


def set_env_var(session: Session, bot_id: int, key: str, value: str, is_secret: bool | None = None, is_required: bool | None = None) -> dict:
    bot = _bot_or_404(session, bot_id)
    key = key.strip()
    if not key or not _ENV_KEY_RE.match(key):
        raise ValueError("Invalid environment variable name")
    row = session.query(EnvVar).filter(EnvVar.bot_id == bot.id, EnvVar.key == key).first()
    secret = is_secret_name(key) if is_secret is None else is_secret
    if row is None:
        row = EnvVar(bot_id=bot.id, key=key, is_secret=secret)
        session.add(row)
    row.encrypted_value = _vault.encrypt(value) if value is not None else ""
    row.is_secret = secret
    if is_required is not None:
        row.is_required = is_required
    session.flush()
    _refresh_env_status(session, bot)
    _audit(session, "ENVIRONMENT_CHANGED", bot.id, f"set {key}")
    return {"ok": True, "key": key, "is_secret": row.is_secret}


def delete_env_var(session: Session, bot_id: int, key: str) -> dict:
    bot = _bot_or_404(session, bot_id)
    row = session.query(EnvVar).filter(EnvVar.bot_id == bot.id, EnvVar.key == key).first()
    if row:
        session.delete(row)
        _audit(session, "ENVIRONMENT_CHANGED", bot.id, f"deleted {key}")
    _refresh_env_status(session, bot)
    return {"ok": True}


def import_env_file(session: Session, bot_id: int, text: str) -> dict:
    bot = _bot_or_404(session, bot_id)
    parsed = parse_env_file(text)
    count = 0
    for key, value in parsed.items():
        set_env_var(session, bot_id, key, value)
        count += 1
    _audit(session, "SECRET_IMPORTED", bot.id, f"imported {count} variables")
    return {"ok": True, "imported": count, "keys": list(parsed.keys())}


def decrypted_env(session: Session, bot_id: int) -> dict[str, str]:
    rows = session.query(EnvVar).filter(EnvVar.bot_id == bot_id).all()
    env = {}
    for row in rows:
        if not row.encrypted_value:
            continue
        try:
            env[row.key] = _vault.decrypt(row.encrypted_value)
        except Exception:
            continue
    return env


def secret_values_for(session: Session, bot_id: int) -> list[str]:
    rows = session.query(EnvVar).filter(EnvVar.bot_id == bot_id, EnvVar.is_secret.is_(True)).all()
    values = []
    for row in rows:
        if not row.encrypted_value:
            continue
        try:
            val = _vault.decrypt(row.encrypted_value)
        except Exception:
            continue
        if val and len(val) >= 8:
            values.append(val)
    return values


def _refresh_env_status(session: Session, bot: Bot) -> None:
    rows = session.query(EnvVar).filter(EnvVar.bot_id == bot.id).all()
    missing = [r.key for r in rows if r.is_required and not r.encrypted_value]
    bot.env_status = "INVALID" if missing else "VALID"


def validate_bot(session: Session, bot_id: int) -> dict:
    bot = _bot_or_404(session, bot_id)
    issues = []
    warnings = []
    if not bot.entry_point:
        issues.append("No entry point configured")
    elif not (Path(bot.managed_path) / bot.entry_point).exists():
        issues.append(f"Entry point not found: {bot.entry_point}")
    rows = session.query(EnvVar).filter(EnvVar.bot_id == bot.id).all()
    missing = [r.key for r in rows if r.is_required and not r.encrypted_value]
    if missing:
        issues.append("Missing required environment variables: " + ", ".join(missing))
    if bot.deps_status == "ERROR":
        issues.append("Dependencies are not installed")
    analysis = json.loads(bot.analysis_json or "{}")
    if analysis.get("plaintext_env_present"):
        warnings.append("A plaintext .env file remains inside the imported project. Prefer the app vault.")
    if bot.trading_mode == "LIVE":
        warnings.append("LIVE mode is selected. Real funds may be affected.")
    env = decrypted_env(session, bot_id)
    perm = (env.get("BINANCE_PERMISSIONS") or env.get("API_PERMISSIONS") or "").lower()
    if "withdraw" in perm:
        warnings.append("HIGH SEVERITY: Withdrawal permission detected. The app never requires withdrawal.")
    _refresh_env_status(session, bot)
    return {
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "env_status": bot.env_status,
        "deps_status": bot.deps_status,
        "trading_mode": bot.trading_mode,
        "entry_point": bot.entry_point,
        "pause_supported": bot.pause_supported,
    }


def update_bot(session: Session, bot_id: int, **fields) -> dict:
    bot = _bot_or_404(session, bot_id)
    allowed = {
        "name",
        "entry_point",
        "trading_mode",
        "keep_running_on_app_close",
        "auto_start",
        "auto_restart",
        "pause_supported",
    }
    mode_changed = False
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "trading_mode":
            v = str(v).upper()
            if v not in {"PAPER", "TESTNET", "LIVE"}:
                raise ValueError("Invalid trading mode")
            if v != bot.trading_mode:
                mode_changed = True
        if k == "entry_point" and v and not is_safe_relative_path(str(v)):
            raise ValueError("Invalid entry point")
        setattr(bot, k, v)
    if mode_changed:
        _audit(session, "MODE_CHANGED", bot.id, bot.trading_mode)
    _audit(session, "CONFIGURATION_CHANGED", bot.id)
    return bot_to_dict(bot, session)


def _on_line(bot_id: int, stream: str, line: str) -> None:
    with session_scope() as session:
        session.add(LogLine(bot_id=bot_id, stream=stream, line=line))
        extra = session.query(LogLine).filter(LogLine.bot_id == bot_id).order_by(LogLine.id.desc()).offset(LOG_BUFFER_MAX_LINES).all()
        for row in extra:
            session.delete(row)
        parsed = parse_line(line)
        if parsed:
            ingest_event(session, bot_id, parsed, source="stdout")


def _on_exit(bot_id: int, code: int | None) -> None:
    with session_scope() as session:
        bot = session.get(Bot, bot_id)
        if not bot:
            return
        running = registry.is_running(bot_id)
        if running:
            return
        bot.pid = None
        if bot.status in {"STOPPING", "UPDATING"}:
            bot.status = "STOPPED"
            ingest_event(session, bot_id, {"type": "BOT_STOPPED", "message": "Bot stopped"}, source="controller")
            return
        if code in (-15, -2, 15, 130, 143):
            bot.status = "STOPPED"
            bot.last_error = ""
            ingest_event(session, bot_id, {"type": "BOT_STOPPED", "message": "Bot process ended"}, source="controller")
            return
        bot.status = "CRASHED"
        bot.last_error = f"process exited with code {code}"
        ingest_event(
            session,
            bot_id,
            {"type": "BOT_ERROR", "message": f"BOT CRASHED Reason: process exited with code {code}"},
            source="controller",
        )
        _audit(session, "BOT_CRASH", bot_id, f"exit={code}")
        if bot.auto_restart:
            _maybe_autorestart(bot_id)


def _maybe_autorestart(bot_id: int) -> None:
    now = time.time()
    stamps = [t for t in _restart_window.get(bot_id, []) if now - t < AUTO_RESTART_WINDOW_SECONDS]
    if len(stamps) >= MAX_AUTO_RESTARTS:
        with session_scope() as session:
            bot = session.get(Bot, bot_id)
            if bot:
                bot.status = "ERROR"
                bot.last_error = "Auto-restart limit reached"
                _audit(session, "AUTO_RESTART", bot_id, "blocked: retry limit")
        return
    stamps.append(now)
    _restart_window[bot_id] = stamps
    try:
        with session_scope() as session:
            start_bot(session, bot_id, live_confirmed=False, from_autorestart=True)
            _audit(session, "AUTO_RESTART", bot_id, "attempt")
    except Exception:
        pass


def start_bot(session: Session, bot_id: int, live_confirmed: bool = False, from_autorestart: bool = False) -> dict:
    bot = _bot_or_404(session, bot_id)
    if registry.is_running(bot.id):
        bot.status = "RUNNING"
        return bot_to_dict(bot, session)
    validation = validate_bot(session, bot_id)
    if not validation["ok"]:
        bot.status = "ERROR"
        bot.last_error = "; ".join(validation["issues"])
        raise ValueError(bot.last_error)
    if bot.trading_mode == "LIVE" and not live_confirmed and not from_autorestart:
        raise PermissionError("LIVE_CONFIRMATION_REQUIRED")
    if bot.trading_mode == "LIVE" and live_confirmed:
        _audit(session, "LIVE_START_CONFIRMATION", bot.id, "user confirmed LIVE start")
    py = bot.python_executable or sys.executable
    entry = Path(bot.managed_path) / bot.entry_point
    if not entry.exists():
        raise ValueError("Entry point not found")
    env = os.environ.copy()
    for k in list(env.keys()):
        if is_secret_name(k):
            env.pop(k, None)
    env.update(decrypted_env(session, bot.id))
    env["CBC_BOT_ID"] = str(bot.id)
    env["CBC_TRADING_MODE"] = bot.trading_mode
    env["PYTHONUNBUFFERED"] = "1"
    secrets = secret_values_for(session, bot.id)
    proc = BotProcess(
        bot_id=bot.id,
        command=[py, str(entry)],
        cwd=bot.managed_path,
        env=env,
        on_line=_on_line,
        on_exit=_on_exit,
        secret_values=secrets,
    )
    bot.status = "STARTING"
    session.flush()
    pid = proc.start()
    registry.register(proc)
    time.sleep(0.45)
    if not proc.is_running():
        bot.status = "ERROR"
        bot.last_error = "Process exited immediately after start"
        registry.unregister(bot.id)
        raise ValueError(bot.last_error)
    bot.pid = pid
    bot.status = "RUNNING"
    bot.last_error = ""
    bot.last_heartbeat = utcnow()
    ingest_event(session, bot.id, {"type": "BOT_STARTED", "message": "Bot started"}, source="controller")
    _audit(session, "BOT_STARTED", bot.id, f"pid={pid} mode={bot.trading_mode}")
    return bot_to_dict(bot, session)


def stop_bot(session: Session, bot_id: int) -> dict:
    bot = _bot_or_404(session, bot_id)
    bot.status = "STOPPING"
    session.flush()
    registry.stop(bot_id)
    bot.pid = None
    bot.status = "STOPPED"
    ingest_event(session, bot.id, {"type": "BOT_STOPPED", "message": "Bot stopped"}, source="controller")
    _audit(session, "BOT_STOPPED", bot.id)
    return bot_to_dict(bot, session)


def restart_bot(session: Session, bot_id: int, live_confirmed: bool = False) -> dict:
    stop_bot(session, bot_id)
    return start_bot(session, bot_id, live_confirmed=live_confirmed)


def pause_bot(session: Session, bot_id: int) -> dict:
    bot = _bot_or_404(session, bot_id)
    if not bot.pause_supported:
        raise ValueError("Pause is not supported by this bot")
    ingest_event(session, bot.id, {"type": "LOG", "message": "Pause requested"}, source="controller")
    bot.status = "PAUSED"
    _audit(session, "BOT_PAUSED", bot.id)
    return bot_to_dict(bot, session)


def resume_bot(session: Session, bot_id: int) -> dict:
    bot = _bot_or_404(session, bot_id)
    if bot.status != "PAUSED":
        raise ValueError("Bot is not paused")
    bot.status = "RUNNING"
    _audit(session, "BOT_RESUMED", bot.id)
    return bot_to_dict(bot, session)


def ingest_event(session: Session, bot_id: int, payload: dict, source: str = "bot") -> Event:
    event_type = str(payload.get("type") or "").upper()
    payload["category"] = payload.get("category") or CATEGORY_MAP.get(event_type, "SYSTEM")
    message = redact_text(payload.get("message") or format_activity_message(payload))
    clean = {k: v for k, v in payload.items() if k != "secrets"}
    event = Event(
        bot_id=bot_id,
        event_type=event_type,
        category=payload.get("category") or "SYSTEM",
        source=source,
        message=message,
        payload_json=json.dumps(clean, default=str),
    )
    session.add(event)
    bot = session.get(Bot, bot_id)
    if bot:
        bot.last_heartbeat = utcnow()
        if event_type == "EXCHANGE_CONNECTED":
            bot.binance_status = "CONNECTED"
        elif event_type == "EXCHANGE_DISCONNECTED":
            bot.binance_status = "DISCONNECTED"
        elif event_type == "TELEGRAM_SENT":
            bot.telegram_status = "CONNECTED"
        elif event_type == "HEALTH_UPDATE":
            bot.binance_status = _health_status(payload.get("exchange"))
            bot.telegram_status = _health_status(payload.get("telegram"))
            bot.env_status = bot.env_status or "VALID"
            if payload.get("dependencies"):
                bot.deps_status = _health_status(payload.get("dependencies"))
        elif event_type == "BOT_ERROR":
            bot.last_error = message
    _apply_trading_event(session, bot_id, payload)
    session.flush()
    return event


def _apply_trading_event(session: Session, bot_id: int, payload: dict) -> None:
    t = str(payload.get("type") or "").upper()
    if t == "SIGNAL_GENERATED":
        session.add(
            Signal(
                bot_id=bot_id,
                signal_id=str(payload.get("signal_id") or payload.get("id") or ""),
                symbol=str(payload.get("symbol") or ""),
                side=str(payload.get("side") or "").upper(),
                entry=_num(payload.get("entry")),
                tp=_num(payload.get("tp")),
                sl=_num(payload.get("sl")),
                confidence=_num(payload.get("confidence")),
                strategy=str(payload.get("strategy") or ""),
                mode=str(payload.get("mode") or ""),
                execution_status=str(payload.get("execution_status") or "GENERATED"),
                telegram_status=str(payload.get("telegram_status") or "UNKNOWN"),
                binance_status=str(payload.get("binance_status") or "UNKNOWN"),
            )
        )
        return
    if t == "POSITION_OPENED":
        last = (
            session.query(Position)
            .filter(Position.bot_id == bot_id)
            .order_by(Position.display_number.desc())
            .first()
        )
        number = (last.display_number + 1) if last else 1
        session.add(
            Position(
                bot_id=bot_id,
                display_number=number,
                external_id=str(payload.get("position_id") or payload.get("id") or number),
                symbol=str(payload.get("symbol") or ""),
                side=str(payload.get("side") or "").upper(),
                entry_price=_num(payload.get("entry") or payload.get("entry_price")),
                current_price=_num(payload.get("current_price") or payload.get("entry") or payload.get("entry_price")),
                quantity=_num(payload.get("quantity")),
                tp=_num(payload.get("tp")),
                sl=_num(payload.get("sl")),
                unrealized_pnl=_num(payload.get("unrealized_pnl")) or 0.0,
                pnl_pct=_num(payload.get("pnl_pct")),
                strategy=str(payload.get("strategy") or ""),
                mode=str(payload.get("mode") or ""),
                status="OPEN",
                opened_at=_ts(payload.get("opened_at")) or utcnow(),
            )
        )
        return
    pos = _find_position(session, bot_id, payload)
    if t == "POSITION_UPDATED" and pos:
        if payload.get("current_price") is not None:
            pos.current_price = _num(payload.get("current_price"))
        if payload.get("unrealized_pnl") is not None:
            pos.unrealized_pnl = _num(payload.get("unrealized_pnl"))
        if payload.get("pnl_pct") is not None:
            pos.pnl_pct = _num(payload.get("pnl_pct"))
        if payload.get("quantity") is not None:
            pos.quantity = _num(payload.get("quantity"))
        return
    if t in {"TP_HIT", "SL_HIT", "POSITION_CLOSED"} and pos:
        reason = "TP" if t == "TP_HIT" else "SL" if t == "SL_HIT" else str(payload.get("close_reason") or "MANUAL")
        pos.status = "CLOSED"
        pos.close_reason = reason
        pos.exit_price = _num(payload.get("exit") or payload.get("exit_price") or payload.get("current_price"))
        pos.realized_pnl = _num(payload.get("realized_pnl")) or 0.0
        pos.pnl_pct = _num(payload.get("pnl_pct"))
        pos.unrealized_pnl = 0.0
        pos.closed_at = _ts(payload.get("closed_at")) or utcnow()
        session.add(
            Trade(
                bot_id=bot_id,
                position_id=pos.id,
                symbol=pos.symbol,
                side=pos.side,
                realized_pnl=pos.realized_pnl or 0.0,
                pnl_pct=pos.pnl_pct,
                close_reason=reason,
                is_win=(pos.realized_pnl or 0.0) > 0,
                opened_at=pos.opened_at,
                closed_at=pos.closed_at,
            )
        )


def _find_position(session: Session, bot_id: int, payload: dict) -> Position | None:
    ext = str(payload.get("position_id") or payload.get("id") or "")
    if ext:
        pos = (
            session.query(Position)
            .filter(Position.bot_id == bot_id, Position.external_id == ext, Position.status == "OPEN")
            .first()
        )
        if pos:
            return pos
    symbol = str(payload.get("symbol") or "")
    if symbol:
        return (
            session.query(Position)
            .filter(Position.bot_id == bot_id, Position.symbol == symbol, Position.status == "OPEN")
            .order_by(Position.id.desc())
            .first()
        )
    return (
        session.query(Position)
        .filter(Position.bot_id == bot_id, Position.status == "OPEN")
        .order_by(Position.id.desc())
        .first()
    )


def _health_status(val: Any) -> str:
    text = str(val or "").upper()
    if text in {"OK", "CONNECTED", "SIMULATED", "READY", "HEALTHY", "VALID"}:
        return "CONNECTED" if text in {"OK", "CONNECTED", "SIMULATED", "HEALTHY"} else text
    if text in {"ERROR", "INVALID", "DISCONNECTED", "FAILED"}:
        return "DISCONNECTED" if text in {"ERROR", "FAILED", "DISCONNECTED"} else text
    return "UNKNOWN"


def _num(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _ts(val: Any) -> datetime | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, (int, float)):
        if val > 1e12:
            val = val / 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromtimestamp(float(val), tz=timezone.utc)
        except (TypeError, ValueError):
            return None


def detect_changes(session: Session, bot_id: int) -> dict:
    bot = _bot_or_404(session, bot_id)
    current = list_relative_files(Path(bot.managed_path))
    previous = _file_maps.get(bot.id)
    if previous is None:
        active = session.get(BotVersion, bot.active_version_id) if bot.active_version_id else None
        if active and Path(active.snapshot_path).exists():
            previous = list_relative_files(Path(active.snapshot_path))
        else:
            previous = current
            _file_maps[bot.id] = current
    diff = diff_file_maps(previous, current)
    changed = bool(diff["added"] or diff["removed"] or diff["modified"])
    return {
        "changed": changed,
        "diff": diff,
        "current_version": session.get(BotVersion, bot.active_version_id).version_label if bot.active_version_id else "",
        "fingerprint": project_fingerprint(Path(bot.managed_path)),
    }


def activate_version(session: Session, bot_id: int, note: str = "") -> dict:
    bot = _bot_or_404(session, bot_id)
    if bot.trading_mode == "LIVE" and registry.is_running(bot.id):
        raise PermissionError("Cannot silently activate changed trading code while LIVE. Stop the bot first.")
    was_running = registry.is_running(bot.id)
    if was_running:
        bot.status = "UPDATING"
        registry.stop(bot.id)
        bot.pid = None
    dirs = bot_dirs(bot.slug)
    versions = session.query(BotVersion).filter(BotVersion.bot_id == bot.id).all()
    next_label = f"v1.{len(versions)}"
    snap = snapshot_version(Path(bot.managed_path), dirs["versions"], next_label)
    fp = project_fingerprint(Path(bot.managed_path))
    for v in versions:
        v.is_active = False
    version = BotVersion(
        bot_id=bot.id,
        version_label=next_label,
        snapshot_path=str(snap),
        fingerprint=fp,
        change_summary=note or "Activated new project files",
        is_active=True,
        is_known_good=False,
    )
    session.add(version)
    session.flush()
    bot.active_version_id = version.id
    _file_maps[bot.id] = list_relative_files(Path(bot.managed_path))
    analysis = analyze_project(Path(bot.managed_path))
    bot.analysis_json = json.dumps(analysis)
    bot.pause_supported = bool(analysis.get("capabilities", {}).get("pause_supported"))
    _audit(session, "VERSION_ACTIVATED", bot.id, next_label)
    if was_running:
        if bot.trading_mode == "LIVE":
            bot.status = "STOPPED"
        else:
            start_bot(session, bot.id, live_confirmed=False)
    else:
        bot.status = "STOPPED"
    version.is_known_good = bot.status == "RUNNING"
    return {"version": next_label, "bot": bot_to_dict(bot, session)}


def rollback_version(session: Session, bot_id: int, version_id: int | None = None) -> dict:
    bot = _bot_or_404(session, bot_id)
    if version_id:
        target = session.get(BotVersion, version_id)
    else:
        target = (
            session.query(BotVersion)
            .filter(BotVersion.bot_id == bot.id, BotVersion.is_known_good.is_(True), BotVersion.id != bot.active_version_id)
            .order_by(BotVersion.id.desc())
            .first()
        )
    if not target or target.bot_id != bot.id:
        raise ValueError("No known-good version available to restore")
    was_running = registry.is_running(bot.id)
    if was_running:
        registry.stop(bot.id)
        bot.pid = None
        bot.status = "UPDATING"
    restore_snapshot(Path(target.snapshot_path), Path(bot.managed_path))
    for v in session.query(BotVersion).filter(BotVersion.bot_id == bot.id):
        v.is_active = False
    target.is_active = True
    bot.active_version_id = target.id
    _file_maps[bot.id] = list_relative_files(Path(bot.managed_path))
    bot.analysis_json = json.dumps(analyze_project(Path(bot.managed_path)))
    _audit(session, "ROLLBACK", bot.id, target.version_label)
    if was_running and bot.trading_mode != "LIVE":
        start_bot(session, bot.id, live_confirmed=False)
    else:
        bot.status = "STOPPED"
    return {"version": target.version_label, "bot": bot_to_dict(bot, session)}


def list_versions(session: Session, bot_id: int) -> list[dict]:
    _bot_or_404(session, bot_id)
    rows = session.query(BotVersion).filter(BotVersion.bot_id == bot_id).order_by(BotVersion.id.desc()).all()
    return [
        {
            "id": v.id,
            "version_label": v.version_label,
            "is_active": v.is_active,
            "is_known_good": v.is_known_good,
            "change_summary": v.change_summary,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in rows
    ]


def health(session: Session, bot_id: int | None = None) -> dict:
    db_ok = True
    try:
        session.query(Bot).count()
    except Exception:
        db_ok = False
    payload = {
        "app": "RUNNING",
        "app_version": APP_VERSION,
        "database": "HEALTHY" if db_ok else "ERROR",
    }
    if bot_id:
        bot = _bot_or_404(session, bot_id)
        running = registry.is_running(bot.id)
        payload.update(
            {
                "bot": bot.status if running or bot.status not in {"RUNNING", "STARTING"} else "STOPPED",
                "binance": bot.binance_status,
                "telegram": bot.telegram_status,
                "environment": bot.env_status,
                "dependencies": bot.deps_status,
                "last_heartbeat": bot.last_heartbeat.isoformat() if bot.last_heartbeat else None,
                "pid": registry.pid(bot.id),
            }
        )
    return payload


def export_backup(session: Session, bot_id: int, include_secrets: bool = False) -> dict:
    bot = _bot_or_404(session, bot_id)
    ensure_dirs()
    stamp = utcnow().strftime("%Y%m%d%H%M%S")
    dest = app_config.BACKUPS_DIR / f"{bot.slug}-{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    data = {
        "bot": bot_to_dict(bot, session),
        "versions": list_versions(session, bot.id),
        "include_secrets": include_secrets,
        "exported_at": utcnow().isoformat(),
        "warning": "Secrets are excluded by default.",
    }
    if include_secrets:
        data["env"] = decrypted_env(session, bot.id)
        data["warning"] = "This backup includes secrets. Store it securely."
    else:
        data["env_keys"] = [r.key for r in session.query(EnvVar).filter(EnvVar.bot_id == bot.id)]
    (dest / "backup.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"path": str(dest), "include_secrets": include_secrets, "warning": data["warning"]}


def telegram_commands(session: Session, bot_id: int) -> list[str]:
    bot = _bot_or_404(session, bot_id)
    analysis = json.loads(bot.analysis_json or "{}")
    return analysis.get("capabilities", {}).get("telegram_commands") or []


DEMO_ENV_DEFAULTS = {
    "BOT_NAME": "Demo Trading Bot",
    "TRADING_MODE": "PAPER",
    "SYMBOL": "BTCUSDT",
    "CYCLE_SECONDS": "1",
    "INITIAL_BALANCE": "10000",
    "POSITION_SIZE_USDT": "500",
    "TP_PERCENT": "0.8",
    "SL_PERCENT": "0.5",
    "REPORT_INTERVAL_SECONDS": "20",
}


def ensure_demo_bot(session: Session) -> dict | None:
    existing = session.query(Bot).filter(Bot.name == "Demo Trading Bot").first()
    if existing:
        return bot_to_dict(existing, session)
    demo_path = Path(__file__).resolve().parents[2] / "demo_bot"
    if not demo_path.exists():
        return None
    bot = import_bot(session, "Demo Trading Bot", str(demo_path), entry_point="main.py")
    for key, value in DEMO_ENV_DEFAULTS.items():
        set_env_var(session, bot["id"], key, value, is_secret=False, is_required=False)
    return bot_to_dict(session.get(Bot, bot["id"]), session)
