from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .database import Position, Trade


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def period_bounds(period: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    now = _aware(now) or datetime.now(timezone.utc)
    mapping = {
        "1H": timedelta(hours=1),
        "4H": timedelta(hours=4),
        "6H": timedelta(hours=6),
        "24H": timedelta(hours=24),
        "7D": timedelta(days=7),
    }
    if period == "TODAY":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now
    if period == "HOURLY":
        start = now.replace(minute=0, second=0, microsecond=0)
        return start, start + timedelta(hours=1)
    if period == "4HOUR":
        hour_block = (now.hour // 4) * 4
        start = now.replace(hour=hour_block, minute=0, second=0, microsecond=0)
        return start, start + timedelta(hours=4)
    delta = mapping.get(period, timedelta(hours=1))
    return now - delta, now


def compute_report(session: Session, bot_id: int, period: str, now: datetime | None = None) -> dict:
    start, end = period_bounds(period, now)
    trades = (
        session.query(Trade)
        .filter(Trade.bot_id == bot_id, Trade.closed_at >= start, Trade.closed_at < end)
        .all()
    )
    wins = sum(1 for t in trades if t.is_win)
    losses = sum(1 for t in trades if not t.is_win)
    realized = sum((t.realized_pnl or 0.0) for t in trades)
    open_positions = (
        session.query(Position)
        .filter(Position.bot_id == bot_id, Position.status == "OPEN")
        .all()
    )
    unrealized = sum((p.unrealized_pnl or 0.0) for p in open_positions)
    count = len(trades)
    win_rate = (wins / count * 100.0) if count else 0.0
    return {
        "period": period,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "trades": count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "realized_pnl": round(realized, 4),
        "unrealized_pnl": round(unrealized, 4),
        "total_pnl": round(realized + unrealized, 4),
        "open_positions": len(open_positions),
    }


def compute_all_pnl(session: Session, bot_id: int, now: datetime | None = None) -> dict:
    periods = ["TODAY", "1H", "4H", "6H", "24H", "7D"]
    return {p: compute_report(session, bot_id, p, now) for p in periods}


def hourly_history(session: Session, bot_id: int, hours: int = 12, now: datetime | None = None) -> list[dict]:
    now = _aware(now) or datetime.now(timezone.utc)
    start_hour = now.replace(minute=0, second=0, microsecond=0)
    rows = []
    for i in range(hours - 1, -1, -1):
        start = start_hour - timedelta(hours=i)
        end = start + timedelta(hours=1)
        trades = (
            session.query(Trade)
            .filter(Trade.bot_id == bot_id, Trade.closed_at >= start, Trade.closed_at < end)
            .all()
        )
        wins = sum(1 for t in trades if t.is_win)
        losses = sum(1 for t in trades if not t.is_win)
        realized = sum((t.realized_pnl or 0.0) for t in trades)
        count = len(trades)
        rows.append(
            {
                "period": "HOURLY",
                "label": f"{start.strftime('%H:%M')} - {end.strftime('%H:%M')}",
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "trades": count,
                "wins": wins,
                "losses": losses,
                "win_rate": round((wins / count * 100.0) if count else 0.0, 1),
                "realized_pnl": round(realized, 4),
            }
        )
    return rows


def four_hour_history(session: Session, bot_id: int, blocks: int = 6, now: datetime | None = None) -> list[dict]:
    now = _aware(now) or datetime.now(timezone.utc)
    hour_block = (now.hour // 4) * 4
    start_block = now.replace(hour=hour_block, minute=0, second=0, microsecond=0)
    rows = []
    for i in range(blocks - 1, -1, -1):
        start = start_block - timedelta(hours=4 * i)
        end = start + timedelta(hours=4)
        trades = (
            session.query(Trade)
            .filter(Trade.bot_id == bot_id, Trade.closed_at >= start, Trade.closed_at < end)
            .all()
        )
        wins = sum(1 for t in trades if t.is_win)
        losses = sum(1 for t in trades if not t.is_win)
        realized = sum((t.realized_pnl or 0.0) for t in trades)
        open_positions = (
            session.query(Position)
            .filter(Position.bot_id == bot_id, Position.status == "OPEN")
            .all()
        )
        unrealized = sum((p.unrealized_pnl or 0.0) for p in open_positions) if i == 0 else 0.0
        count = len(trades)
        rows.append(
            {
                "period": "4HOUR",
                "label": f"{start.strftime('%H:%M')} - {end.strftime('%H:%M')}",
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "trades": count,
                "wins": wins,
                "losses": losses,
                "win_rate": round((wins / count * 100.0) if count else 0.0, 1),
                "realized_pnl": round(realized, 4),
                "unrealized_pnl": round(unrealized, 4),
            }
        )
    return rows
