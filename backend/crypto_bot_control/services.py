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
    Report,
    Signal,
    Trade,
    session_scope,
    utcnow,
)
from .events import CATEGORY_MAP, format_activity_message, parse_line
from .process_controller import BotProcess, ProcessRegistry, process_alive
from .project_analyzer import SKIP_DIRS, analyze_project
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
from .universe import (
    fetch_binance_futures_universe,
    hardcoded_tradfi_symbols,
    normalize_scan_mode,
    symbols_for_mode,
)

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
    else:
        entry_path = Path(bot.managed_path) / bot.entry_point
        try:
            src = entry_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            src = ""
        if ("from strategy import" in src or "import strategy" in src) and not (Path(bot.managed_path) / "strategy.py").is_file():
            issues.append("strategy.py is missing. Upload or restore it in Settings before Start.")
    rows = session.query(EnvVar).filter(EnvVar.bot_id == bot.id).all()
    missing = [r.key for r in rows if r.is_required and not r.encrypted_value]
    if missing:
        issues.append("Missing required environment variables: " + ", ".join(missing))
    req = Path(bot.managed_path) / "requirements.txt"
    empty_reqs = req.is_file() and not any(
        line.strip() and not line.strip().startswith("#")
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines()
    )
    if empty_reqs and bot.deps_status == "ERROR":
        bot.deps_status = "READY"
        bot.last_error = ""
    elif bot.deps_status == "ERROR":
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


def _recent_stderr(session: Session, bot_id: int) -> str:
    rows = (
        session.query(LogLine)
        .filter(LogLine.bot_id == bot_id, LogLine.stream == "stderr")
        .order_by(LogLine.id.desc())
        .limit(30)
        .all()
    )
    lines = [r.line.strip() for r in reversed(rows) if r.line and r.line.strip()]
    for line in reversed(lines):
        if "Error" in line or "Exception" in line:
            return line
    return " | ".join(lines[-3:]) if lines else ""


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
        detail = _recent_stderr(session, bot_id)
        reason = f"process exited with code {code}"
        if detail:
            reason = f"{reason}: {detail}"
        bot.status = "CRASHED"
        bot.last_error = reason
        ingest_event(
            session,
            bot_id,
            {"type": "BOT_ERROR", "message": f"BOT CRASHED Reason: {reason}"},
            source="controller",
        )
        _audit(session, "BOT_CRASH", bot_id, f"exit={code}")
        if bot.auto_restart and "No module named" not in (detail or ""):
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
    env["PYTHONPATH"] = str(Path(bot.managed_path)) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
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
        time.sleep(0.2)
        detail = _recent_stderr(session, bot.id)
        bot.status = "ERROR"
        bot.last_error = detail or "Process exited immediately after start"
        registry.unregister(bot.id)
        raise ValueError(bot.last_error)
    bot.pid = pid
    bot.status = "RUNNING"
    bot.last_error = ""
    bot.last_heartbeat = utcnow()
    reconcile_stale_positions(session, bot.id)
    ingest_event(session, bot.id, {"type": "BOT_STARTED", "message": "Bot started"}, source="controller")
    _audit(session, "BOT_STARTED", bot.id, f"pid={pid} mode={bot.trading_mode}")
    return bot_to_dict(bot, session)


def reconcile_stale_positions(session: Session, bot_id: int, reason: str = "BOT_RESTART") -> int:
    stale = (
        session.query(Position)
        .filter(Position.bot_id == bot_id, Position.status == "OPEN")
        .all()
    )
    for pos in stale:
        ingest_event(
            session,
            bot_id,
            {
                "type": "POSITION_CLOSED",
                "internal_id": pos.id,
                "position_id": pos.external_id or str(pos.display_number),
                "id": pos.external_id or str(pos.id),
                "symbol": pos.symbol,
                "side": pos.side,
                "exit": pos.current_price or pos.entry_price,
                "exit_price": pos.current_price or pos.entry_price,
                "realized_pnl": 0.0,
                "close_reason": reason,
                "message": f"Stale position {pos.symbol} #{pos.display_number} closed on start ({reason})",
            },
            source="controller",
        )
    if stale:
        _audit(session, "POSITIONS_RECONCILED", bot_id, f"closed {len(stale)} stale position(s): {reason}")
    return len(stale)


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
        tps = _extract_tps(payload)
        session.add(
            Signal(
                bot_id=bot_id,
                signal_id=str(payload.get("signal_id") or payload.get("id") or ""),
                symbol=str(payload.get("symbol") or ""),
                side=str(payload.get("side") or "").upper(),
                entry=_num(payload.get("entry")),
                tp=_num(payload.get("tp")) if _num(payload.get("tp")) is not None else (tps[-1] if tps else None),
                sl=_num(payload.get("sl")),
                confidence=_num(payload.get("confidence")),
                strategy=str(payload.get("strategy") or ""),
                mode=str(payload.get("mode") or ""),
                execution_status=str(payload.get("execution_status") or "GENERATED"),
                telegram_status=str(payload.get("telegram_status") or "UNKNOWN"),
                binance_status=str(payload.get("binance_status") or "UNKNOWN"),
                tps_json=json.dumps(tps),
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
        tps = _extract_tps(payload)
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
                tp=_num(payload.get("tp")) if _num(payload.get("tp")) is not None else (tps[-1] if tps else None),
                sl=_num(payload.get("sl")),
                tps_json=json.dumps(tps),
                hit_tps_json="[]",
                tp_hits=0,
                tps_total=len(tps),
                booked_pnl=0.0,
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
        extra_tps = _extract_tps(payload)
        if extra_tps:
            pos.tps_json = json.dumps(extra_tps)
            pos.tp = extra_tps[-1]
        _apply_tp_tracking(pos, payload)
        return
    if t in {"TP_HIT", "SL_HIT", "POSITION_CLOSED"} and pos:
        remaining = _num(payload.get("remaining_quantity") or payload.get("quantity_remaining"))
        partial = bool(payload.get("partial")) or (t == "TP_HIT" and remaining is not None and remaining > 0)
        if payload.get("current_price") is not None:
            pos.current_price = _num(payload.get("current_price"))
        if payload.get("quantity") is not None and partial:
            pos.quantity = _num(payload.get("quantity"))
        if remaining is not None:
            pos.quantity = remaining
        if partial and t == "TP_HIT":
            if payload.get("unrealized_pnl") is not None:
                pos.unrealized_pnl = _num(payload.get("unrealized_pnl"))
            extra_tps = _extract_tps(payload)
            if extra_tps:
                pos.tps_json = json.dumps(extra_tps)
            _apply_tp_tracking(pos, payload, hit=payload.get("tp"))
            return
        reason = "TP" if t == "TP_HIT" else "SL" if t == "SL_HIT" else str(payload.get("close_reason") or "MANUAL")
        _apply_tp_tracking(pos, payload, hit=payload.get("tp"))
        if payload.get("tps_total") is None and not pos.tps_total:
            pos.tps_total = len(_parse_tps_json(pos.tps_json))
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
                booked_pnl=pos.booked_pnl or 0.0,
                tp_hits=pos.tp_hits or 0,
                tps_total=pos.tps_total or 0,
                pnl_pct=pos.pnl_pct,
                close_reason=reason,
                is_win=(pos.realized_pnl or 0.0) > 0,
                opened_at=pos.opened_at,
                closed_at=pos.closed_at,
            )
        )


def close_position(session: Session, bot_id: int, position_id: int, reason: str = "MANUAL") -> dict:
    bot = _bot_or_404(session, bot_id)
    pos = session.get(Position, position_id)
    if not pos or pos.bot_id != bot.id:
        pos = (
            session.query(Position)
            .filter(Position.bot_id == bot.id, Position.display_number == position_id, Position.status == "OPEN")
            .first()
        )
    if not pos or pos.bot_id != bot.id:
        raise ValueError("Position not found")
    if pos.status != "OPEN":
        raise ValueError("Position is not open")
    exit_price = pos.current_price if pos.current_price is not None else pos.entry_price
    pnl = round((pos.booked_pnl or 0.0) + (pos.unrealized_pnl or 0.0), 2)
    ingest_event(
        session,
        bot.id,
        {
            "type": "POSITION_CLOSED",
            "internal_id": pos.id,
            "position_id": pos.external_id or str(pos.display_number),
            "id": pos.external_id or str(pos.id),
            "symbol": pos.symbol,
            "side": pos.side,
            "exit": exit_price,
            "exit_price": exit_price,
            "realized_pnl": pnl,
            "booked_pnl": pos.booked_pnl or 0.0,
            "tp_hits": pos.tp_hits or 0,
            "hit_tps": _parse_tps_json(pos.hit_tps_json),
            "close_reason": reason or "MANUAL",
            "partial": False,
        },
        source="controller",
    )
    _write_bot_command(
        bot,
        {
            "action": "CLOSE_POSITION",
            "position_id": pos.external_id or str(pos.display_number),
            "display_number": pos.display_number,
            "symbol": pos.symbol,
            "reason": reason or "MANUAL",
        },
    )
    _audit(session, "POSITION_CLOSED_MANUAL", bot.id, f"{pos.symbol} #{pos.display_number}")
    session.refresh(pos)
    return {
        "ok": True,
        "id": pos.id,
        "status": pos.status,
        "close_reason": pos.close_reason,
        "exit": pos.exit_price,
        "realized_pnl": pos.realized_pnl,
    }


def _apply_tp_tracking(pos: Position, payload: dict, hit: Any = None) -> None:
    if payload.get("booked_pnl") is not None:
        pos.booked_pnl = _num(payload.get("booked_pnl")) or 0.0
    if payload.get("tp_hits") is not None:
        pos.tp_hits = int(_num(payload.get("tp_hits")) or 0)
    total = _num(payload.get("tps_total"))
    if total is not None and total > 0:
        pos.tps_total = max(pos.tps_total or 0, int(total))
    hits = None
    if isinstance(payload.get("hit_tps"), list):
        hits = [_num(x) for x in payload.get("hit_tps") if _num(x) is not None]
    elif hit is not None:
        existing = _parse_tps_json(pos.hit_tps_json)
        value = _num(hit)
        if value is not None and value not in existing:
            hits = existing + [value]
    if hits is not None:
        pos.hit_tps_json = json.dumps(hits)
        if payload.get("tp_hits") is None:
            pos.tp_hits = len(hits)


def _write_bot_command(bot: Bot, command: dict) -> None:
    dest = Path(bot.managed_path) / ".cbc"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "commands.jsonl"
    payload = dict(command)
    payload["ts"] = utcnow().isoformat()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


def _find_position(session: Session, bot_id: int, payload: dict) -> Position | None:
    internal = payload.get("internal_id") or payload.get("db_id")
    if internal not in (None, ""):
        try:
            found = session.get(Position, int(internal))
        except (TypeError, ValueError):
            found = None
        if found and found.bot_id == bot_id and found.status == "OPEN":
            return found
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


def _parse_tps_json(raw: str | None) -> list[float]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    out: list[float] = []
    if isinstance(data, list):
        for item in data:
            n = _num(item)
            if n is not None:
                out.append(n)
    return out[:5]


def _extract_tps(payload: dict) -> list[float]:
    raw = payload.get("tps")
    if raw is None:
        raw = payload.get("take_profits")
    out: list[float] = []
    if isinstance(raw, list):
        for item in raw:
            n = _num(item)
            if n is not None:
                out.append(n)
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                for item in parsed:
                    n = _num(item)
                    if n is not None:
                        out.append(n)
        except json.JSONDecodeError:
            for part in raw.replace(";", ",").split(","):
                n = _num(part.strip())
                if n is not None:
                    out.append(n)
    if not out:
        for key in ("tp1", "tp2", "tp3", "tp4", "tp5"):
            n = _num(payload.get(key))
            if n is not None:
                out.append(n)
    if not out:
        n = _num(payload.get("tp"))
        if n is not None:
            out.append(n)
    return out[:5]


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


def _managed_file(bot: Bot, rel: str) -> Path:
    if not is_safe_relative_path(rel):
        raise ValueError("Invalid file path")
    root = Path(bot.managed_path).resolve()
    path = (root / rel).resolve()
    if root not in path.parents and path != root:
        raise ValueError("Invalid file path")
    if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
        raise ValueError("That path is not editable")
    return path


def list_strategy_files(session: Session, bot_id: int) -> list[dict]:
    bot = _bot_or_404(session, bot_id)
    root = Path(bot.managed_path)
    files = []
    for rel, digest in sorted(list_relative_files(root).items()):
        suffix = Path(rel).suffix.lower()
        if suffix not in STRATEGY_EDITABLE_SUFFIXES:
            continue
        path = root / rel
        files.append(
            {
                "path": rel,
                "size": path.stat().st_size if path.exists() else 0,
                "fingerprint": digest,
            }
        )
    return files


def read_strategy_file(session: Session, bot_id: int, rel: str) -> dict:
    bot = _bot_or_404(session, bot_id)
    path = _managed_file(bot, rel)
    if not path.is_file():
        raise ValueError("File not found")
    if path.stat().st_size > STRATEGY_MAX_BYTES:
        raise ValueError("File is too large to edit in the app")
    return {"path": rel, "content": path.read_text(encoding="utf-8", errors="replace")}


def _backup_strategy_file(bot: Bot, rel: str, path: Path) -> str | None:
    if not path.is_file():
        return None
    stamp = utcnow().strftime("%Y%m%d%H%M%S")
    dest_dir = Path(bot.managed_path) / ".strategy_backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = rel.replace("/", "__").replace("\\", "__")
    dest = dest_dir / f"{safe}.{stamp}.bak"
    dest.write_bytes(path.read_bytes())
    return str(dest.relative_to(bot.managed_path)).replace("\\", "/")


def save_strategy_file(session: Session, bot_id: int, rel: str, content: str, activate: bool = False) -> dict:
    bot = _bot_or_404(session, bot_id)
    if bot.trading_mode == "LIVE" and registry.is_running(bot.id) and activate:
        raise PermissionError("Cannot silently activate changed trading code while LIVE. Stop the bot first.")
    path = _managed_file(bot, rel)
    if path.suffix.lower() not in STRATEGY_EDITABLE_SUFFIXES:
        raise ValueError("That file type is not editable")
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup_strategy_file(bot, rel, path) if path.exists() else None
    path.write_text(content if content is not None else "", encoding="utf-8")
    _audit(session, "STRATEGY_SAVED", bot.id, rel)
    activated = None
    if activate:
        if bot.trading_mode == "LIVE" and registry.is_running(bot.id):
            raise PermissionError("Cannot silently activate changed trading code while LIVE. Stop the bot first.")
        activated = activate_version(session, bot.id, note=f"saved {rel}")
    return {
        "ok": True,
        "path": rel,
        "backup": backup,
        "content": path.read_text(encoding="utf-8", errors="replace"),
        "activated": bool(activated),
        "live_blocked": bot.trading_mode == "LIVE" and registry.is_running(bot.id) and not activate,
        "needs_activation": detect_changes(session, bot.id)["changed"],
    }


def delete_strategy_file(session: Session, bot_id: int, rel: str) -> dict:
    bot = _bot_or_404(session, bot_id)
    path = _managed_file(bot, rel)
    if not path.exists():
        raise ValueError("File not found")
    if path.suffix.lower() not in STRATEGY_EDITABLE_SUFFIXES:
        raise ValueError("That file type is not editable")
    backup = _backup_strategy_file(bot, rel, path)
    path.unlink()
    _audit(session, "STRATEGY_DELETED", bot.id, rel)
    return {"ok": True, "path": rel, "backup": backup, "needs_activation": True}


def list_strategy_backups(session: Session, bot_id: int, rel: str | None = None) -> list[dict]:
    bot = _bot_or_404(session, bot_id)
    root = Path(bot.managed_path)
    dest_dir = root / ".strategy_backups"
    if not dest_dir.is_dir():
        return []
    wanted = None
    if rel:
        wanted = rel.replace("/", "__").replace("\\", "__")
    out = []
    for path in sorted(dest_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_file() or not path.name.endswith(".bak"):
            continue
        stem = path.name[: -len(".bak")]
        if "." not in stem:
            continue
        safe, stamp = stem.rsplit(".", 1)
        original = safe.replace("__", "/")
        if wanted and safe != wanted:
            continue
        out.append(
            {
                "backup": str(path.relative_to(root)).replace("\\", "/"),
                "path": original,
                "stamp": stamp,
                "size": path.stat().st_size,
            }
        )
    return out


def restore_strategy_backup(
    session: Session, bot_id: int, backup: str, rel: str | None = None, activate: bool = False
) -> dict:
    bot = _bot_or_404(session, bot_id)
    if bot.trading_mode == "LIVE" and registry.is_running(bot.id) and activate:
        raise PermissionError("Cannot silently activate changed trading code while LIVE. Stop the bot first.")
    root = Path(bot.managed_path).resolve()
    src = (root / backup).resolve()
    if root not in src.parents or ".strategy_backups" not in src.parts:
        raise ValueError("Invalid backup path")
    if not src.is_file():
        raise ValueError("Backup not found")
    target_rel = rel
    if not target_rel:
        name = src.name[: -len(".bak")] if src.name.endswith(".bak") else src.name
        if "." in name:
            target_rel = name.rsplit(".", 1)[0].replace("__", "/")
        else:
            target_rel = name.replace("__", "/")
    dest = _managed_file(bot, target_rel)
    if dest.suffix.lower() not in STRATEGY_EDITABLE_SUFFIXES:
        raise ValueError("That file type is not editable")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        _backup_strategy_file(bot, target_rel, dest)
    dest.write_bytes(src.read_bytes())
    _audit(session, "STRATEGY_RESTORED", bot.id, f"{backup} -> {target_rel}")
    activated = None
    if activate:
        activated = activate_version(session, bot.id, note=f"restored {target_rel}")
    return {
        "ok": True,
        "path": target_rel,
        "backup": backup,
        "activated": bool(activated),
        "content": dest.read_text(encoding="utf-8", errors="replace"),
        "needs_activation": detect_changes(session, bot.id)["changed"],
    }


def clean_strategy_file(session: Session, bot_id: int, rel: str) -> dict:
    bot = _bot_or_404(session, bot_id)
    path = _managed_file(bot, rel)
    if not path.is_file():
        raise ValueError("File not found")
    original = path.read_text(encoding="utf-8", errors="replace")
    backup = _backup_strategy_file(bot, rel, path)
    lines = original.splitlines()
    cleaned: list[str] = []
    blank_run = 0
    for line in lines:
        stripped = line.rstrip()
        if stripped.strip().startswith("#") and not stripped.strip().startswith("#!"):
            continue
        if stripped.strip() == "":
            blank_run += 1
            if blank_run > 1:
                continue
            cleaned.append("")
            continue
        blank_run = 0
        cleaned.append(stripped)
    text = "\n".join(cleaned).strip() + ("\n" if cleaned else "")
    path.write_text(text, encoding="utf-8")
    _audit(session, "STRATEGY_CLEANED", bot.id, rel)
    return {"ok": True, "path": rel, "backup": backup, "content": text, "needs_activation": True}


def _safe_int(val: Any, default: int) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def get_runtime_settings(session: Session, bot_id: int) -> dict:
    bot = _bot_or_404(session, bot_id)
    env = decrypted_env(session, bot_id)
    scan_mode = normalize_scan_mode(env.get("SCAN_MODE") or "ALL")
    symbols_raw = env.get("SYMBOLS") or env.get("SYMBOL") or ""
    symbols = [s.strip().upper() for s in symbols_raw.replace(";", ",").split(",") if s.strip()]
    delay = max(0, _safe_int(env.get("STARTUP_DELAY_SECONDS"), 180))
    gap = max(0, _safe_int(env.get("TRADE_GAP_SECONDS"), 180))
    tp_count = min(5, max(1, _safe_int(env.get("TP_COUNT"), 1)))
    percents = []
    for part in (env.get("TP_PERCENTS") or env.get("TP_PERCENT") or "0.8").replace(";", ",").split(","):
        n = _num(part.strip())
        if n is not None:
            percents.append(n)
    if not percents:
        percents = [0.8]
    while len(percents) < tp_count:
        percents.append(round(percents[-1] + percents[0], 4))
    percents = percents[:tp_count]
    all_raw = env.get("ALL_SYMBOLS") or ""
    all_symbols = [s.strip().upper() for s in all_raw.replace(";", ",").split(",") if s.strip()]
    crypto_raw = env.get("CRYPTO_SYMBOLS") or ""
    crypto_symbols = [s.strip().upper() for s in crypto_raw.replace(";", ",").split(",") if s.strip()]
    tradfi_raw = env.get("TRADFI_SYMBOLS") or ""
    tradfi_symbols = [s.strip().upper() for s in tradfi_raw.replace(";", ",").split(",") if s.strip()]
    if not tradfi_symbols:
        tradfi_symbols = hardcoded_tradfi_symbols()
    if not all_symbols:
        if scan_mode == "TRADFI":
            all_symbols = list(tradfi_symbols)
        elif scan_mode == "BINANCE":
            all_symbols = list(crypto_symbols)
        elif scan_mode == "ALL":
            crypto_set = set(crypto_symbols)
            all_symbols = crypto_symbols + [s for s in tradfi_symbols if s not in crypto_set]
    return {
        "scan_mode": scan_mode,
        "symbols": symbols,
        "symbol": env.get("SYMBOL") or (symbols[0] if symbols else ""),
        "all_symbols": all_symbols[:40],
        "all_symbol_count": len(all_symbols),
        "crypto_symbols": crypto_symbols[:40],
        "crypto_symbol_count": len(crypto_symbols),
        "tradfi_symbols": tradfi_symbols[:40],
        "tradfi_symbol_count": len(tradfi_symbols),
        "universe_label": _universe_label(scan_mode),
        "startup_delay_seconds": max(0, delay),
        "trade_gap_seconds": max(0, gap),
        "tp_count": tp_count,
        "tp_percents": percents,
        "sl_percent": _num(env.get("SL_PERCENT")) or 0.5,
        "max_open_positions": min(20, max(1, _safe_int(env.get("MAX_OPEN_POSITIONS"), 3))),
        "trading_mode": bot.trading_mode,
        "presets": {
            "delay": [180, 300, 600],
            "gap": [180, 300, 600],
            "tp_count": [1, 2, 3, 4, 5],
            "scan_mode": ["ALL", "BINANCE", "TRADFI"],
            "max_open_positions": [1, 2, 3, 5, 10],
        },
    }


def update_runtime_settings(session: Session, bot_id: int, payload: dict) -> dict:
    current = get_runtime_settings(session, bot_id)
    scan_mode = normalize_scan_mode(payload.get("scan_mode") or current["scan_mode"] or "ALL")
    if scan_mode not in {"ALL", "BINANCE", "TRADFI"}:
        raise ValueError("scan_mode must be ALL, BINANCE, or TRADFI")
    symbols = payload.get("symbols")
    if symbols is None:
        symbols = current["symbols"]
    if isinstance(symbols, str):
        symbols = [s.strip().upper() for s in symbols.replace(";", ",").split(",") if s.strip()]
    elif isinstance(symbols, list):
        symbols = [str(s).strip().upper() for s in symbols if str(s).strip()]
    else:
        symbols = []
    delay = payload.get("startup_delay_seconds")
    if delay is None:
        delay = current["startup_delay_seconds"]
    delay = int(float(delay))
    if delay < 0:
        raise ValueError("startup_delay_seconds must be >= 0")
    gap = payload.get("trade_gap_seconds")
    if gap is None:
        gap = current["trade_gap_seconds"]
    gap = int(float(gap))
    if gap < 0:
        raise ValueError("trade_gap_seconds must be >= 0")
    tp_count = payload.get("tp_count")
    if tp_count is None:
        tp_count = current["tp_count"]
    tp_count = int(float(tp_count))
    if tp_count < 1 or tp_count > 5:
        raise ValueError("tp_count must be 1-5")
    percents = payload.get("tp_percents")
    if isinstance(percents, str):
        percents = [_num(p.strip()) for p in percents.replace(";", ",").split(",") if p.strip()]
    elif isinstance(percents, list):
        percents = [_num(p) for p in percents]
    else:
        percents = []
    percents = [p for p in percents if p is not None]
    if not percents:
        percents = [0.8 * (i + 1) for i in range(tp_count)]
    percents = percents[:tp_count]
    while len(percents) < tp_count:
        percents.append(round(percents[-1] + percents[0], 4))
    sl = payload.get("sl_percent")
    sl_n = current["sl_percent"] if sl is None else _num(sl)
    if sl_n is None or sl_n < 0:
        raise ValueError("sl_percent must be >= 0")
    max_open = payload.get("max_open_positions")
    if max_open is None:
        max_open = current.get("max_open_positions", 3)
    max_open = int(float(max_open))
    if max_open < 1 or max_open > 20:
        raise ValueError("max_open_positions must be 1-20")
    set_env_var(session, bot_id, "SCAN_MODE", scan_mode, is_secret=False)
    set_env_var(session, bot_id, "SYMBOLS", ",".join(symbols), is_secret=False)
    if symbols:
        set_env_var(session, bot_id, "SYMBOL", symbols[0], is_secret=False)
    all_symbols: list[str] = []
    crypto_symbols: list[str] = []
    tradfi_symbols: list[str] = hardcoded_tradfi_symbols()
    scan_error = ""
    if payload.get("refresh_universe"):
        universe = fetch_scan_universe()
        crypto_symbols = list(universe.get("crypto") or [])
        tradfi_symbols = list(universe.get("tradfi") or hardcoded_tradfi_symbols())
        all_symbols = symbols_for_mode(universe, scan_mode)
        if scan_mode in {"ALL", "BINANCE"} and not crypto_symbols:
            scan_error = universe.get("crypto_error") or "Binance USD-M futures universe unavailable"
            if scan_mode == "BINANCE":
                raise ValueError(scan_error)
        set_env_var(session, bot_id, "ALL_SYMBOLS", ",".join(all_symbols), is_secret=False)
        set_env_var(session, bot_id, "CRYPTO_SYMBOLS", ",".join(crypto_symbols), is_secret=False)
        set_env_var(session, bot_id, "TRADFI_SYMBOLS", ",".join(tradfi_symbols), is_secret=False)
    set_env_var(session, bot_id, "STARTUP_DELAY_SECONDS", str(delay), is_secret=False)
    set_env_var(session, bot_id, "TRADE_GAP_SECONDS", str(gap), is_secret=False)
    set_env_var(session, bot_id, "TP_COUNT", str(tp_count), is_secret=False)
    set_env_var(session, bot_id, "TP_PERCENTS", ",".join(str(p) for p in percents), is_secret=False)
    set_env_var(session, bot_id, "TP_PERCENT", str(percents[0]), is_secret=False)
    set_env_var(session, bot_id, "SL_PERCENT", str(sl_n), is_secret=False)
    set_env_var(session, bot_id, "MAX_OPEN_POSITIONS", str(max_open), is_secret=False)
    _audit(session, "RUNTIME_SETTINGS_CHANGED", bot_id, f"scan={scan_mode} delay={delay} gap={gap} tp={tp_count} max_open={max_open}")
    result = get_runtime_settings(session, bot_id)
    if all_symbols:
        result["all_symbols"] = all_symbols[:40]
        result["all_symbol_count"] = len(all_symbols)
    if crypto_symbols:
        result["crypto_symbols"] = crypto_symbols[:40]
        result["crypto_symbol_count"] = len(crypto_symbols)
    result["tradfi_symbols"] = tradfi_symbols[:40]
    result["tradfi_symbol_count"] = len(tradfi_symbols)
    result["universe_label"] = _universe_label(scan_mode)
    result["scan_error"] = scan_error
    return result


def _universe_label(scan_mode: str) -> str:
    mode = normalize_scan_mode(scan_mode)
    return {
        "ALL": "Binance Futures + US TradFi",
        "BINANCE": "Binance Futures",
        "TRADFI": "US TradFi",
        "SELECTED": "Binance Futures + US TradFi",
    }.get(mode, mode)


def fetch_scan_universe() -> dict:
    return fetch_binance_futures_universe()


def fetch_binance_usdm_symbols() -> list[str]:
    universe = fetch_scan_universe()
    crypto = list(universe.get("crypto") or [])
    if not crypto:
        raise ValueError(universe.get("crypto_error") or "Binance USD-M futures universe unavailable")
    return crypto


DEMO_ENV_DEFAULTS = {
    "BOT_NAME": "Demo Trading Bot",
    "TRADING_MODE": "PAPER",
    "SCAN_MODE": "ALL",
    "SYMBOL": "BTCUSDT",
    "SYMBOLS": "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT",
    "CYCLE_SECONDS": "1",
    "INITIAL_BALANCE": "10000",
    "POSITION_SIZE_USDT": "500",
    "TP_COUNT": "2",
    "TP_PERCENTS": "0.8,1.6",
    "TP_PERCENT": "0.8",
    "SL_PERCENT": "0.5",
    "STARTUP_DELAY_SECONDS": "180",
    "TRADE_GAP_SECONDS": "180",
    "MAX_OPEN_POSITIONS": "3",
    "REPORT_INTERVAL_SECONDS": "20",
}


def reset_bot_data(session: Session, bot_id: int) -> dict:
    bot = _bot_or_404(session, bot_id)
    if bot.status in {"RUNNING", "STARTING"}:
        raise PermissionError("Stop the bot before resetting trading data")
    counts = {
        "signals": session.query(Signal).filter(Signal.bot_id == bot_id).delete(synchronize_session=False),
        "positions": session.query(Position).filter(Position.bot_id == bot_id).delete(synchronize_session=False),
        "trades": session.query(Trade).filter(Trade.bot_id == bot_id).delete(synchronize_session=False),
        "events": session.query(Event).filter(Event.bot_id == bot_id).delete(synchronize_session=False),
        "logs": session.query(LogLine).filter(LogLine.bot_id == bot_id).delete(synchronize_session=False),
        "reports": session.query(Report).filter(Report.bot_id == bot_id).delete(synchronize_session=False),
    }
    bot.last_error = ""
    bot.last_heartbeat = None
    _audit(session, "BOT_DATA_RESET", bot_id, json.dumps(counts))
    return {"ok": True, "cleared": counts}

STRATEGY_EDITABLE_SUFFIXES = {".py", ".json", ".yml", ".yaml", ".toml", ".txt", ".cfg", ".ini", ".md"}
STRATEGY_MAX_BYTES = 512_000


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
