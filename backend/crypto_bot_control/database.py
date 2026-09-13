from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker, Session

from . import config as app_config
from .config import ensure_dirs


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Bot(Base):
    __tablename__ = "bots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, default="")
    managed_path: Mapped[str] = mapped_column(Text, nullable=False)
    entry_point: Mapped[str] = mapped_column(String(260), default="")
    python_executable: Mapped[str] = mapped_column(String(500), default="")
    venv_path: Mapped[str] = mapped_column(Text, default="")
    trading_mode: Mapped[str] = mapped_column(String(20), default="PAPER")
    status: Mapped[str] = mapped_column(String(20), default="STOPPED")
    pause_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    keep_running_on_app_close: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_start: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_restart: Mapped[bool] = mapped_column(Boolean, default=False)
    restart_count: Mapped[int] = mapped_column(Integer, default=0)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    binance_status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    telegram_status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    env_status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    deps_status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    analysis_json: Mapped[str] = mapped_column(Text, default="{}")
    active_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    versions: Mapped[list["BotVersion"]] = relationship(back_populates="bot", cascade="all, delete-orphan")
    env_vars: Mapped[list["EnvVar"]] = relationship(back_populates="bot", cascade="all, delete-orphan")


class BotVersion(Base):
    __tablename__ = "bot_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), nullable=False)
    version_label: Mapped[str] = mapped_column(String(40), nullable=False)
    snapshot_path: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    change_summary: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    is_known_good: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    bot: Mapped[Bot] = relationship(back_populates="versions")


class EnvVar(Base):
    __tablename__ = "env_vars"
    __table_args__ = (UniqueConstraint("bot_id", "key", name="uq_bot_env_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    encrypted_value: Mapped[str] = mapped_column(Text, default="")
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    bot: Mapped[Bot] = relationship(back_populates="env_vars")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    category: Mapped[str] = mapped_column(String(20), default="SYSTEM")
    source: Mapped[str] = mapped_column(String(40), default="bot")
    message: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), nullable=False, index=True)
    signal_id: Mapped[str] = mapped_column(String(80), default="")
    symbol: Mapped[str] = mapped_column(String(40), default="")
    side: Mapped[str] = mapped_column(String(10), default="")
    entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategy: Mapped[str] = mapped_column(String(80), default="")
    mode: Mapped[str] = mapped_column(String(20), default="")
    execution_status: Mapped[str] = mapped_column(String(20), default="GENERATED")
    telegram_status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    binance_status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    tps_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), nullable=False, index=True)
    display_number: Mapped[int] = mapped_column(Integer, nullable=False)
    external_id: Mapped[str] = mapped_column(String(80), default="")
    symbol: Mapped[str] = mapped_column(String(40), default="")
    side: Mapped[str] = mapped_column(String(10), default="")
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    tps_json: Mapped[str] = mapped_column(Text, default="[]")
    unrealized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategy: Mapped[str] = mapped_column(String(80), default="")
    mode: Mapped[str] = mapped_column(String(20), default="")
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    close_reason: Mapped[str] = mapped_column(String(40), default="")
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), nullable=False, index=True)
    position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    symbol: Mapped[str] = mapped_column(String(40), default="")
    side: Mapped[str] = mapped_column(String(10), default="")
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_reason: Mapped[str] = mapped_column(String(40), default="")
    is_win: Mapped[bool] = mapped_column(Boolean, default=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class LogLine(Base):
    __tablename__ = "log_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), nullable=False, index=True)
    stream: Mapped[str] = mapped_column(String(10), default="stdout")
    line: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


_engine = None
_SessionLocal = None


def get_engine(db_path=None):
    global _engine, _SessionLocal
    ensure_dirs()
    path = db_path or app_config.DB_PATH
    url = f"sqlite:///{path}"
    if _engine is None or str(_engine.url) != url:
        _engine = create_engine(url, connect_args={"check_same_thread": False}, echo=False)

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        _SessionLocal = sessionmaker(bind=_engine, autoflush=True, autocommit=False, expire_on_commit=False)
        try:
            Base.metadata.create_all(_engine, checkfirst=True)
        except Exception:
            pass
        _ensure_extra_columns(_engine)
    return _engine


def _ensure_extra_columns(engine) -> None:
    extras = (
        ("signals", "tps_json", "TEXT"),
        ("positions", "tps_json", "TEXT"),
    )
    with engine.begin() as conn:
        for table, column, coltype in extras:
            info = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            names = {row[1] for row in info}
            if column not in names:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def get_session() -> Session:
    if _SessionLocal is None:
        get_engine()
    return _SessionLocal()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine():
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
