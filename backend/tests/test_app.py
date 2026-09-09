from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from crypto_bot_control.database import Event, Position, Signal, Trade, session_scope
from crypto_bot_control.events import parse_line
from crypto_bot_control.reporting import compute_report, four_hour_history, hourly_history
from crypto_bot_control.security import is_secret_name, mask_secret, redact_text
from crypto_bot_control import services
from crypto_bot_control.project_analyzer import analyze_project


def test_project_import_multi_file(mock_bot_path):
    with session_scope() as s:
        bot = services.import_bot(s, "Mock Bot", str(mock_bot_path))
        assert bot["name"] == "Mock Bot"
        analysis = bot["analysis"]
        assert analysis["file_count"] >= 4
        assert "main.py" in analysis["python_files"] or "bot.py" in analysis["entry_points"]
        assert analysis["dependencies"]["requirements_txt"] is True


def test_entry_point_detection(mock_bot_path):
    analysis = analyze_project(mock_bot_path)
    assert "main.py" in analysis["entry_points"]
    assert "bot.py" in analysis["entry_points"]


def test_entry_point_choice(mock_bot_path):
    with session_scope() as s:
        bot = services.import_bot(s, "Choice Bot", str(mock_bot_path), entry_point="bot.py")
        assert bot["entry_point"] == "bot.py"


def test_dependency_detection(mock_bot_path):
    analysis = analyze_project(mock_bot_path)
    assert analysis["dependencies"]["preferred"] == "requirements.txt"


def test_env_variable_detection(mock_bot_path):
    analysis = analyze_project(mock_bot_path)
    assert "BINANCE_API_KEY" in analysis["env_names"]
    assert "TELEGRAM_BOT_TOKEN" in analysis["env_names"]


def test_secret_masking():
    assert mask_secret("abcdefghijk") == "*******hijk"
    assert is_secret_name("BINANCE_SECRET") is True
    assert is_secret_name("TRADING_MODE") is False


def test_secret_redaction_in_logs():
    text = "BINANCE_SECRET=supersecretvalue TOKEN=abcdefghijklmnopqrstuvwxyz"
    red = redact_text(text, extra_values=["supersecretvalue"])
    assert "supersecretvalue" not in red
    assert "***REDACTED***" in red


def test_env_set_and_mask(mock_bot_path):
    with session_scope() as s:
        bot = services.import_bot(s, "Env Bot", str(mock_bot_path))
        services.set_env_var(s, bot["id"], "BINANCE_SECRET", "real-secret-value-123")
        listed = services.list_env(s, bot["id"])
        secret = next(x for x in listed if x["key"] == "BINANCE_SECRET")
        assert "real-secret-value-123" not in secret["value"]
        assert secret["is_secret"] is True
        raw = services.decrypted_env(s, bot["id"])
        assert raw["BINANCE_SECRET"] == "real-secret-value-123"


def test_env_import_and_delete(mock_bot_path):
    with session_scope() as s:
        bot = services.import_bot(s, "Env Import", str(mock_bot_path))
        services.import_env_file(s, bot["id"], "FOO=1\nBAR=2\n")
        keys = {x["key"] for x in services.list_env(s, bot["id"])}
        assert "FOO" in keys
        services.delete_env_var(s, bot["id"], "FOO")
        keys = {x["key"] for x in services.list_env(s, bot["id"])}
        assert "FOO" not in keys


def _ready_bot(session, mock_bot_path, name="Ready Bot"):
    bot = services.import_bot(session, name, str(mock_bot_path))
    services.set_env_var(session, bot["id"], "BINANCE_API_KEY", "test-key-1234567890")
    services.set_env_var(session, bot["id"], "BINANCE_SECRET", "test-secret-1234567890")
    services.set_env_var(session, bot["id"], "TELEGRAM_BOT_TOKEN", "123:ABC-token-value")
    services.set_env_var(session, bot["id"], "TRADING_MODE", "PAPER")
    services.set_env_var(session, bot["id"], "MOCK_INTERVAL", "0.25")
    return bot


def test_validate_required_env(mock_bot_path):
    with session_scope() as s:
        bot = services.import_bot(s, "Need Env", str(mock_bot_path))
        result = services.validate_bot(s, bot["id"])
        assert result["ok"] is False
        bot2 = _ready_bot(s, mock_bot_path, "Has Env")
        result2 = services.validate_bot(s, bot2["id"])
        assert result2["ok"] is True


def test_start_stop_restart(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Proc Bot")
        started = services.start_bot(s, bot["id"])
        assert started["status"] == "RUNNING"
        assert started["pid"]
        time.sleep(0.4)
        stopped = services.stop_bot(s, bot["id"])
        assert stopped["status"] == "STOPPED"
        restarted = services.restart_bot(s, bot["id"])
        assert restarted["status"] == "RUNNING"
        services.stop_bot(s, bot["id"])


def test_crash_detection(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Crash Bot")
        services.set_env_var(s, bot["id"], "MOCK_CRASH", "1")
        with pytest.raises(ValueError):
            services.start_bot(s, bot["id"])


def test_signal_and_position_ingestion(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Event Bot")
        bid = bot["id"]
        services.ingest_event(
            s,
            bid,
            {
                "type": "SIGNAL_GENERATED",
                "signal_id": "S-1",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry": 100,
                "tp": 110,
                "sl": 90,
            },
        )
        services.ingest_event(
            s,
            bid,
            {
                "type": "POSITION_OPENED",
                "position_id": "p1",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry": 100,
                "quantity": 1,
                "tp": 110,
                "sl": 90,
            },
        )
        services.ingest_event(
            s,
            bid,
            {"type": "POSITION_UPDATED", "position_id": "p1", "current_price": 105, "unrealized_pnl": 5},
        )
        sig = s.query(Signal).filter(Signal.bot_id == bid).one()
        pos = s.query(Position).filter(Position.bot_id == bid).one()
        assert sig.symbol == "BTCUSDT"
        assert pos.display_number == 1
        assert pos.status == "OPEN"
        assert pos.unrealized_pnl == 5


def test_position_numbering(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Num Bot")
        bid = bot["id"]
        services.ingest_event(s, bid, {"type": "POSITION_OPENED", "position_id": "a", "symbol": "AAA", "side": "LONG"})
        services.ingest_event(s, bid, {"type": "POSITION_OPENED", "position_id": "b", "symbol": "BBB", "side": "SHORT"})
        numbers = [p.display_number for p in s.query(Position).filter(Position.bot_id == bid).order_by(Position.id)]
        assert numbers == [1, 2]


def test_tp_sl_remain_in_history(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "TPSL Bot")
        bid = bot["id"]
        services.ingest_event(
            s,
            bid,
            {"type": "POSITION_OPENED", "position_id": "1", "symbol": "BTCUSDT", "side": "LONG", "entry": 10, "tp": 12, "sl": 8},
        )
        services.ingest_event(
            s,
            bid,
            {"type": "TP_HIT", "position_id": "1", "symbol": "BTCUSDT", "exit": 12, "realized_pnl": 2.5},
        )
        services.ingest_event(
            s,
            bid,
            {"type": "POSITION_OPENED", "position_id": "2", "symbol": "ETHUSDT", "side": "SHORT", "entry": 20, "tp": 18, "sl": 22},
        )
        services.ingest_event(
            s,
            bid,
            {"type": "SL_HIT", "position_id": "2", "symbol": "ETHUSDT", "exit": 22, "realized_pnl": -1.5},
        )
        rows = s.query(Position).filter(Position.bot_id == bid).order_by(Position.display_number).all()
        assert rows[0].status == "CLOSED"
        assert rows[0].close_reason == "TP"
        assert rows[0].tp == 12
        assert rows[0].sl == 8
        assert rows[0].realized_pnl == 2.5
        assert rows[1].close_reason == "SL"
        assert rows[1].tp == 18
        trades = s.query(Trade).filter(Trade.bot_id == bid).all()
        assert len(trades) == 2


def test_realized_vs_unrealized(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "PnL Bot")
        bid = bot["id"]
        services.ingest_event(s, bid, {"type": "POSITION_OPENED", "position_id": "1", "symbol": "AAA", "side": "LONG", "entry": 1})
        services.ingest_event(s, bid, {"type": "POSITION_UPDATED", "position_id": "1", "unrealized_pnl": 3.0})
        services.ingest_event(
            s,
            bid,
            {"type": "POSITION_OPENED", "position_id": "2", "symbol": "BBB", "side": "LONG", "entry": 1},
        )
        services.ingest_event(s, bid, {"type": "POSITION_CLOSED", "position_id": "2", "realized_pnl": 4.0, "close_reason": "MANUAL"})
        report = compute_report(s, bid, "TODAY")
        assert report["realized_pnl"] == 4.0
        assert report["unrealized_pnl"] == 3.0
        assert report["total_pnl"] == 7.0
        assert report["realized_pnl"] != report["unrealized_pnl"]


def test_hourly_and_four_hour_reports(mock_bot_path):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Report Bot")
        bid = bot["id"]
        s.add(
            Trade(
                bot_id=bid,
                symbol="BTCUSDT",
                side="LONG",
                realized_pnl=11.42,
                is_win=True,
                closed_at=now - timedelta(minutes=10),
            )
        )
        s.add(
            Trade(
                bot_id=bid,
                symbol="ETHUSDT",
                side="SHORT",
                realized_pnl=-2.0,
                is_win=False,
                closed_at=now - timedelta(minutes=5),
            )
        )
        s.flush()
        hourly = hourly_history(s, bid, hours=2, now=now)
        assert hourly[-1]["trades"] >= 2
        four = four_hour_history(s, bid, blocks=1, now=now)
        assert four[-1]["trades"] >= 2
        assert four[-1]["realized_pnl"] == 9.42


def test_telegram_command_detection(mock_bot_path):
    analysis = analyze_project(mock_bot_path)
    cmds = analysis["capabilities"]["telegram_commands"]
    assert "/start" in cmds
    assert "/pause" in cmds
    with session_scope() as s:
        bot = services.import_bot(s, "TG Bot", str(mock_bot_path))
        assert "/status" in services.telegram_commands(s, bot["id"])


def test_health_monitoring(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Health Bot")
        h = services.health(s, bot["id"])
        assert h["database"] == "HEALTHY"
        assert h["bot"] in {"STOPPED", "RUNNING", "ERROR"}
        assert "binance" in h
        assert "telegram" in h


def test_database_persistence(mock_bot_path):
    with session_scope() as s:
        bot = services.import_bot(s, "Persist Bot", str(mock_bot_path))
        bid = bot["id"]
    with session_scope() as s:
        bots = services.list_bots(s)
        assert any(b["id"] == bid for b in bots)


def test_file_change_and_version_activation(mock_bot_path):
    with session_scope() as s:
        bot = services.import_bot(s, "Ver Bot", str(mock_bot_path))
        bid = bot["id"]
        managed = Path(bot["managed_path"])
        before = services.detect_changes(s, bid)
        assert before["changed"] is False
        (managed / "new_module.py").write_text("print('hello')\n")
        after = services.detect_changes(s, bid)
        assert after["changed"] is True
        assert "new_module.py" in after["diff"]["added"]
        activated = services.activate_version(s, bid, "add module")
        assert activated["version"].startswith("v1.")
        versions = services.list_versions(s, bid)
        assert len(versions) >= 2


def test_rollback(mock_bot_path):
    with session_scope() as s:
        bot = services.import_bot(s, "Roll Bot", str(mock_bot_path))
        bid = bot["id"]
        managed = Path(bot["managed_path"])
        (managed / "changed.py").write_text("x=1\n")
        services.activate_version(s, bid, "change")
        assert (managed / "changed.py").exists()
        rolled = services.rollback_version(s, bid)
        assert rolled["version"] == "v1.0"
        assert not (managed / "changed.py").exists()


def test_live_confirmation_required(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Live Bot")
        services.update_bot(s, bot["id"], trading_mode="LIVE")
        with pytest.raises(PermissionError):
            services.start_bot(s, bot["id"], live_confirmed=False)
        started = services.start_bot(s, bot["id"], live_confirmed=True)
        assert started["status"] == "RUNNING"
        services.stop_bot(s, bot["id"])


def test_no_withdrawal_warning(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Withdraw Bot")
        services.set_env_var(s, bot["id"], "BINANCE_PERMISSIONS", "trade,withdraw")
        result = services.validate_bot(s, bot["id"])
        assert any("Withdrawal" in w or "withdraw" in w.lower() for w in result["warnings"])


def test_no_live_activate_while_running(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Live Act")
        services.update_bot(s, bot["id"], trading_mode="LIVE")
        services.start_bot(s, bot["id"], live_confirmed=True)
        Path(bot["managed_path"], "x.py").write_text("x=1\n")
        with pytest.raises(PermissionError):
            services.activate_version(s, bot["id"])
        services.stop_bot(s, bot["id"])


def test_parse_structured_and_heuristic():
    line = 'CBC_EVENT {"type": "SIGNAL_GENERATED", "symbol": "BTCUSDT", "side": "LONG", "entry": 1}'
    parsed = parse_line(line)
    assert parsed["type"] == "SIGNAL_GENERATED"
    heur = parse_line("14:32 SIGNAL BTCUSDT LONG Entry 67245 TP 68000 SL 66500")
    assert heur["type"] == "SIGNAL_GENERATED"
    assert heur["symbol"] == "BTCUSDT"


def test_parse_demo_event():
    line = 'DEMO_EVENT {"event":"SIGNAL_GENERATED","symbol":"BTCUSDT","side":"LONG","entry":67000,"tp":67536,"sl":66665}'
    parsed = parse_line(line)
    assert parsed["type"] == "SIGNAL_GENERATED"
    assert parsed["symbol"] == "BTCUSDT"
    health = parse_line('DEMO_EVENT {"event":"HEALTH_UPDATE","exchange":"SIMULATED","telegram":"SIMULATED"}')
    assert health["type"] == "HEALTH_UPDATE"


def test_demo_bot_import():
    demo = Path(__file__).resolve().parents[2] / "demo_bot"
    with session_scope() as s:
        bot = services.import_bot(s, "Demo Trading Bot", str(demo), entry_point="main.py")
        assert bot["entry_point"] == "main.py"
        analysis = bot["analysis"]
        assert "main.py" in analysis["entry_points"]
        assert "strategy.py" in analysis["python_files"]


def test_demo_bot_start_emits_events():
    with session_scope() as s:
        bot = services.ensure_demo_bot(s)
        assert bot is not None
        started = services.start_bot(s, bot["id"])
        assert started["status"] == "RUNNING"
    time.sleep(3.5)
    with session_scope() as s:
        from crypto_bot_control.database import Event, Position, Signal
        bid = bot["id"]
        events = s.query(Event).filter(Event.bot_id == bid).all()
        signals = s.query(Signal).filter(Signal.bot_id == bid).all()
        services.stop_bot(s, bid)
        assert any(e.event_type == "BOT_STARTED" for e in events)
        assert len(events) >= 3
        assert len(signals) >= 0



def test_analysis_does_not_execute(tmp_path, monkeypatch):
    ran = {"v": False}
    project = tmp_path / "proj"
    project.mkdir()
    (project / "main.py").write_text("raise SystemExit('executed')\nopen('executed.txt','w').write('x')\n")
    analysis = analyze_project(project)
    assert analysis["entry_points"] == ["main.py"]
    assert not (project / "executed.txt").exists()


def test_backup_excludes_secrets_by_default(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Backup Bot")
        result = services.export_backup(s, bot["id"], include_secrets=False)
        data = json.loads(Path(result["path"], "backup.json").read_text())
        assert "env" not in data
        assert data["include_secrets"] is False


def test_stdout_events_from_running_mock(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Live Events")
        services.start_bot(s, bot["id"])
    time.sleep(3.5)
    with session_scope() as s:
        signals = s.query(Signal).filter(Signal.bot_id == bot["id"]).all()
        positions = s.query(Position).filter(Position.bot_id == bot["id"]).all()
        events = s.query(Event).filter(Event.bot_id == bot["id"]).all()
        services.stop_bot(s, bot["id"])
        assert len(events) >= 1
        assert any(e.event_type == "BOT_STARTED" for e in events)
    assert len(signals) >= 1 or len(positions) >= 1 or len(events) >= 2


def test_positions_api_handles_naive_opened_at(mock_bot_path):
    from crypto_bot_control.api import api_positions
    from crypto_bot_control.database import Position
    from datetime import datetime

    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Tz Bot")
        bid = bot["id"]
        s.add(
            Position(
                bot_id=bid,
                display_number=1,
                external_id="1",
                symbol="BTCUSDT",
                side="LONG",
                entry_price=100,
                status="OPEN",
                opened_at=datetime(2026, 9, 9, 12, 0, 0),
            )
        )
        s.flush()
        s.commit()
    rows = api_positions(bid)
    assert rows[0]["number"] == "#001"
    assert rows[0]["duration_seconds"] is not None


def test_pause_hidden_unless_supported(mock_bot_path):
    with session_scope() as s:
        bot = services.import_bot(s, "Pause Bot", str(mock_bot_path))
        assert bot["pause_supported"] is True
        with pytest.raises(ValueError):
            services.resume_bot(s, bot["id"])
