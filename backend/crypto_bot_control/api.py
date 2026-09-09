from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .config import APP_NAME, APP_VERSION, LOGS_DIR
from .database import Bot, Event, LogLine, Position, Signal, get_session, utcnow
from .reporting import compute_all_pnl, compute_report, four_hour_history, hourly_history
from .security import redact_text
from . import services

app = FastAPI(title=APP_NAME, version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def db() -> Session:
    return get_session()


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class ImportBody(BaseModel):
    name: str
    source_path: str
    entry_point: str | None = None


class AnalyzeBody(BaseModel):
    source_path: str


class EnvBody(BaseModel):
    key: str
    value: str = ""
    is_secret: bool | None = None
    is_required: bool | None = None


class EnvImportBody(BaseModel):
    content: str


class BotUpdateBody(BaseModel):
    name: str | None = None
    entry_point: str | None = None
    trading_mode: str | None = None
    keep_running_on_app_close: bool | None = None
    auto_start: bool | None = None
    auto_restart: bool | None = None
    pause_supported: bool | None = None


class StartBody(BaseModel):
    live_confirmed: bool = False


class EventBody(BaseModel):
    type: str
    payload: dict = Field(default_factory=dict)


class ActivateBody(BaseModel):
    note: str = ""


class RollbackBody(BaseModel):
    version_id: int | None = None


def _ok_or_err(fn):
    try:
        return fn()
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/health")
def api_health(bot_id: int | None = None):
    session = db()
    try:
        return services.health(session, bot_id)
    finally:
        session.close()


@app.get("/api/meta")
def api_meta():
    return {"name": APP_NAME, "app_version": APP_VERSION}


@app.post("/api/projects/analyze")
def api_analyze(body: AnalyzeBody):
    return _ok_or_err(lambda: services.analyze_path(body.source_path))


@app.get("/api/bots")
def api_list_bots():
    session = db()
    try:
        return services.list_bots(session)
    finally:
        session.close()


@app.post("/api/bots")
def api_import_bot(body: ImportBody):
    session = db()
    try:
        return _ok_or_err(lambda: services.import_bot(session, body.name, body.source_path, body.entry_point))
    finally:
        session.close()


@app.get("/api/bots/{bot_id}")
def api_get_bot(bot_id: int):
    session = db()
    try:
        bot = session.get(Bot, bot_id)
        if not bot:
            raise HTTPException(404, "Bot not found")
        return services.bot_to_dict(bot, session)
    finally:
        session.close()


@app.patch("/api/bots/{bot_id}")
def api_update_bot(bot_id: int, body: BotUpdateBody):
    session = db()
    try:
        return _ok_or_err(lambda: services.update_bot(session, bot_id, **body.model_dump()))
    finally:
        session.close()


@app.post("/api/bots/{bot_id}/dependencies")
def api_install_deps(bot_id: int):
    session = db()
    try:
        return _ok_or_err(lambda: services.install_bot_deps(session, bot_id))
    finally:
        session.close()


@app.get("/api/bots/{bot_id}/validate")
def api_validate(bot_id: int):
    session = db()
    try:
        return _ok_or_err(lambda: services.validate_bot(session, bot_id))
    finally:
        session.close()


@app.get("/api/bots/{bot_id}/env")
def api_list_env(bot_id: int):
    session = db()
    try:
        return _ok_or_err(lambda: services.list_env(session, bot_id))
    finally:
        session.close()


@app.post("/api/bots/{bot_id}/env")
def api_set_env(bot_id: int, body: EnvBody):
    session = db()
    try:
        return _ok_or_err(lambda: services.set_env_var(session, bot_id, body.key, body.value, body.is_secret, body.is_required))
    finally:
        session.close()


@app.delete("/api/bots/{bot_id}/env/{key}")
def api_del_env(bot_id: int, key: str):
    session = db()
    try:
        return _ok_or_err(lambda: services.delete_env_var(session, bot_id, key))
    finally:
        session.close()


@app.post("/api/bots/{bot_id}/env/import")
def api_import_env(bot_id: int, body: EnvImportBody):
    session = db()
    try:
        return _ok_or_err(lambda: services.import_env_file(session, bot_id, body.content))
    finally:
        session.close()


@app.post("/api/bots/{bot_id}/start")
def api_start(bot_id: int, body: StartBody | None = None):
    session = db()
    try:
        confirmed = body.live_confirmed if body else False
        return _ok_or_err(lambda: services.start_bot(session, bot_id, live_confirmed=confirmed))
    finally:
        session.close()


@app.post("/api/bots/{bot_id}/stop")
def api_stop(bot_id: int):
    session = db()
    try:
        return _ok_or_err(lambda: services.stop_bot(session, bot_id))
    finally:
        session.close()


@app.post("/api/bots/{bot_id}/restart")
def api_restart(bot_id: int, body: StartBody | None = None):
    session = db()
    try:
        confirmed = body.live_confirmed if body else False
        return _ok_or_err(lambda: services.restart_bot(session, bot_id, live_confirmed=confirmed))
    finally:
        session.close()


@app.post("/api/bots/{bot_id}/pause")
def api_pause(bot_id: int):
    session = db()
    try:
        return _ok_or_err(lambda: services.pause_bot(session, bot_id))
    finally:
        session.close()


@app.post("/api/bots/{bot_id}/resume")
def api_resume(bot_id: int):
    session = db()
    try:
        return _ok_or_err(lambda: services.resume_bot(session, bot_id))
    finally:
        session.close()


@app.get("/api/bots/{bot_id}/events")
def api_events(bot_id: int, category: str | None = None, limit: int = 200):
    session = db()
    try:
        q = session.query(Event).filter(Event.bot_id == bot_id)
        if category:
            q = q.filter(Event.category == category.upper())
        rows = q.order_by(Event.id.desc()).limit(min(limit, 500)).all()
        return [
            {
                "id": r.id,
                "type": r.event_type,
                "category": r.category,
                "source": r.source,
                "message": r.message,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reversed(rows)
        ]
    finally:
        session.close()


@app.post("/api/bots/{bot_id}/events")
def api_push_event(bot_id: int, body: EventBody):
    session = db()
    try:
        payload = dict(body.payload)
        payload["type"] = body.type
        event = services.ingest_event(session, bot_id, payload, source="adapter")
        return {"id": event.id, "type": event.event_type}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        session.close()


@app.get("/api/bots/{bot_id}/signals")
def api_signals(bot_id: int, limit: int = 200):
    session = db()
    try:
        rows = (
            session.query(Signal)
            .filter(Signal.bot_id == bot_id)
            .order_by(Signal.id.desc())
            .limit(min(limit, 500))
            .all()
        )
        return [
            {
                "id": r.id,
                "signal_id": r.signal_id or f"SIG-{r.id}",
                "time": r.created_at.isoformat() if r.created_at else None,
                "symbol": r.symbol,
                "side": r.side,
                "entry": r.entry,
                "tp": r.tp,
                "sl": r.sl,
                "confidence": r.confidence,
                "strategy": r.strategy,
                "mode": r.mode,
                "execution_status": r.execution_status,
                "telegram_status": r.telegram_status,
                "binance_status": r.binance_status,
            }
            for r in rows
        ]
    finally:
        session.close()


@app.get("/api/bots/{bot_id}/positions")
def api_positions(bot_id: int, status: str | None = None):
    session = db()
    try:
        q = session.query(Position).filter(Position.bot_id == bot_id)
        if status:
            q = q.filter(Position.status == status.upper())
        rows = q.order_by(Position.display_number.desc()).all()
        out = []
        for r in rows:
            opened = _aware(r.opened_at)
            closed = _aware(r.closed_at)
            duration = None
            if opened:
                end = closed or utcnow()
                duration = int((end - opened).total_seconds())
            out.append(
                {
                    "id": r.id,
                    "number": f"#{r.display_number:03d}",
                    "display_number": r.display_number,
                    "symbol": r.symbol,
                    "side": r.side,
                    "entry": r.entry_price,
                    "current_price": r.current_price,
                    "exit": r.exit_price,
                    "quantity": r.quantity,
                    "tp": r.tp,
                    "sl": r.sl,
                    "unrealized_pnl": r.unrealized_pnl,
                    "realized_pnl": r.realized_pnl,
                    "pnl_pct": r.pnl_pct,
                    "strategy": r.strategy,
                    "mode": r.mode,
                    "status": r.status,
                    "close_reason": r.close_reason,
                    "opened_at": opened.isoformat() if opened else None,
                    "closed_at": closed.isoformat() if closed else None,
                    "duration_seconds": duration,
                }
            )
        return out
    finally:
        session.close()


@app.get("/api/bots/{bot_id}/reports")
def api_reports(bot_id: int, period: str = "TODAY"):
    session = db()
    try:
        return compute_report(session, bot_id, period.upper())
    finally:
        session.close()


@app.get("/api/bots/{bot_id}/pnl")
def api_pnl(bot_id: int):
    session = db()
    try:
        return compute_all_pnl(session, bot_id)
    finally:
        session.close()


@app.get("/api/bots/{bot_id}/reports/hourly")
def api_hourly(bot_id: int):
    session = db()
    try:
        return hourly_history(session, bot_id)
    finally:
        session.close()


@app.get("/api/bots/{bot_id}/reports/four-hour")
def api_four_hour(bot_id: int):
    session = db()
    try:
        return four_hour_history(session, bot_id)
    finally:
        session.close()


@app.get("/api/bots/{bot_id}/logs")
def api_logs(bot_id: int, q: str | None = None, limit: int = 300):
    session = db()
    try:
        rows = (
            session.query(LogLine)
            .filter(LogLine.bot_id == bot_id)
            .order_by(LogLine.id.desc())
            .limit(min(limit, 1000))
            .all()
        )
        items = list(reversed(rows))
        out = []
        for r in items:
            line = r.line
            if q and q.lower() not in line.lower():
                continue
            out.append(
                {
                    "id": r.id,
                    "stream": r.stream,
                    "line": line,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
            )
        return out
    finally:
        session.close()


@app.get("/api/bots/{bot_id}/logs/export")
def api_export_logs(bot_id: int):
    session = db()
    try:
        rows = session.query(LogLine).filter(LogLine.bot_id == bot_id).order_by(LogLine.id.asc()).all()
        text = "\n".join(f"{r.created_at.isoformat() if r.created_at else ''} [{r.stream}] {r.line}" for r in rows)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        path = LOGS_DIR / f"bot-{bot_id}-logs.txt"
        path.write_text(redact_text(text), encoding="utf-8")
        return FileResponse(path, filename=path.name, media_type="text/plain")
    finally:
        session.close()


@app.get("/api/bots/{bot_id}/versions")
def api_versions(bot_id: int):
    session = db()
    try:
        return _ok_or_err(lambda: services.list_versions(session, bot_id))
    finally:
        session.close()


@app.get("/api/bots/{bot_id}/changes")
def api_changes(bot_id: int):
    session = db()
    try:
        return _ok_or_err(lambda: services.detect_changes(session, bot_id))
    finally:
        session.close()


@app.post("/api/bots/{bot_id}/versions/activate")
def api_activate(bot_id: int, body: ActivateBody | None = None):
    session = db()
    try:
        note = body.note if body else ""
        return _ok_or_err(lambda: services.activate_version(session, bot_id, note))
    finally:
        session.close()


@app.post("/api/bots/{bot_id}/versions/rollback")
def api_rollback(bot_id: int, body: RollbackBody | None = None):
    session = db()
    try:
        vid = body.version_id if body else None
        return _ok_or_err(lambda: services.rollback_version(session, bot_id, vid))
    finally:
        session.close()


@app.get("/api/bots/{bot_id}/telegram/commands")
def api_tg_commands(bot_id: int):
    session = db()
    try:
        return _ok_or_err(lambda: services.telegram_commands(session, bot_id))
    finally:
        session.close()


@app.post("/api/bots/{bot_id}/backup")
def api_backup(bot_id: int, include_secrets: bool = False):
    session = db()
    try:
        return _ok_or_err(lambda: services.export_backup(session, bot_id, include_secrets=include_secrets))
    finally:
        session.close()


def mount_frontend(static_dir: Path) -> None:
    index = static_dir / "index.html"
    if not index.exists():
        return
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets") if (static_dir / "assets").exists() else None

    @app.get("/")
    def _index():
        return FileResponse(index)

    @app.get("/{full_path:path}")
    def _spa(full_path: str):
        candidate = static_dir / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)
