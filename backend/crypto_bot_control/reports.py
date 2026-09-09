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


def period_bounds(now: datetime, period: str) -> tuple[datetime, datetime]:
    now = _aware(now) or datetime.now(timezone.utc)
    period = period.upper()
    if period == "1H":
        start = now.replace(minute=0, second=0, microsecond=0)
        return start, start + timedelta(hours=1)
    if period == "4H":
        hour = (now.hour // 4) * 4
        start = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        return start, start + timedelta(hours=4)
    if period == "6H":
        hour = (now.hour // 6) * 6
        start = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        return start, start + timedelta(hours=6)
    if period == "24H":
        return now - timedelta(hours=24), now
    if period == "7D":
        return now - timedelta(days=7), now
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def summarize_trades(trades: list[Trade], open_positions: list[Position] | None = None) -> dict:
    wins = sum(1 for t in trades if t.is_win)
    losses = sum(1 for t in trades if not t.is_win)
    realized = sum(float(t.realized_pnl or 0) for t in trades)
    unrealized = 0.0
    if open_positions:
        unrealized = sum(float(p.unrealized_pnl or 0) for p in open_positions if p.status == "OPEN")
    total = len(trades)
    win_rate = (wins / total * 100.0) if total else 0.0
    return {
        "trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "realized_pnl": round(realized, 4),
        "unrealized_pnl": round(unrealized, 4),
        "total_pnl": round(realized + unrealized, 4),
    }


def report_for_bot(session: Session, bot_id: int, period: str, now: datetime | None = None) -> dict:
    now = _aware(now) or datetime.now(timezone.utc)
    start, end = period_bounds(now, period)
    trades = (
        session.query(Trade)
        .filter(Trade.bot_id == bot_id, Trade.closed_at >= start, Trade.closed_at < end)
        .all()
    )
    open_positions = (
        session.query(Position).filter(Position.bot_id == bot_id, Position.status == "OPEN").all()
    )
    summary = summarize_trades(trades, open_positions)
    summary.update(
        {
            "period": period.upper(),
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "open_positions": len(open_positions),
        }
    )
    return summary


def all_period_reports(session: Session, bot_id: int, now: datetime | None = None) -> dict:
    now = _aware(now) or datetime.now(timezone.utc)
    out = {}
    for period in ("TODAY", "1H", "4H", "6H", "24H", "7D"):
        out[period] = report_for_bot(session, bot_id, period, now)
    return out


def duration_seconds(opened_at: datetime | None, closed_at: datetime | None = None) -> int | None:
    opened_at = _aware(opened_at)
    if opened_at is None:
        return None
    end = _aware(closed_at) or datetime.now(timezone.utc)
    return int((end - opened_at).total_seconds())


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return ""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"
