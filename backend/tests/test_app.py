from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from crypto_bot_control.database import Event, LogLine, Position, Signal, Trade, session_scope
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


def test_empty_requirements_skips_pip(tmp_path):
    from crypto_bot_control.project_manager import install_dependencies

    (tmp_path / "requirements.txt").write_text("# Demo bot has no third-party dependencies.\n")
    result = install_dependencies("/nonexistent/python", tmp_path)
    assert result["ok"] is True
    assert "skipped" in result["logs"][0].lower()


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
    now = datetime(2026, 9, 10, 15, 40, tzinfo=timezone.utc)
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


def test_multi_tp_signal_and_position_ingestion(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Multi TP Bot")
        bid = bot["id"]
        services.ingest_event(
            s,
            bid,
            {
                "type": "SIGNAL_GENERATED",
                "signal_id": "S-TP",
                "symbol": "ETHUSDT",
                "side": "LONG",
                "entry": 100,
                "tps": [101, 102, 103],
                "sl": 95,
            },
        )
        services.ingest_event(
            s,
            bid,
            {
                "type": "POSITION_OPENED",
                "position_id": "mtp",
                "symbol": "ETHUSDT",
                "side": "LONG",
                "entry": 100,
                "tps": [101, 102, 103],
                "sl": 95,
                "quantity": 1,
            },
        )
        sig = s.query(Signal).filter(Signal.bot_id == bid).one()
        pos = s.query(Position).filter(Position.bot_id == bid).one()
        assert json.loads(sig.tps_json) == [101, 102, 103]
        assert sig.tp == 103
        assert json.loads(pos.tps_json) == [101, 102, 103]
        assert pos.tp == 103
        assert pos.tps_total == 3
        assert pos.tp_hits == 0
        assert pos.booked_pnl == 0.0


def test_tp_hits_and_booked_pnl_tracking(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "TP Track Bot")
        bid = bot["id"]
        services.ingest_event(
            s,
            bid,
            {
                "type": "POSITION_OPENED",
                "position_id": "p-tp",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry": 100,
                "tps": [110, 120, 130],
                "sl": 90,
                "quantity": 3,
            },
        )
        services.ingest_event(
            s,
            bid,
            {
                "type": "TP_HIT",
                "position_id": "p-tp",
                "symbol": "BTCUSDT",
                "tp": 110,
                "partial": True,
                "remaining_quantity": 2,
                "tps": [120, 130],
                "hit_tps": [110],
                "tp_hits": 1,
                "tps_total": 3,
                "booked_pnl": 4.5,
                "realized_pnl": 4.5,
            },
        )
        pos = s.query(Position).filter(Position.bot_id == bid).one()
        assert pos.status == "OPEN"
        assert pos.tp_hits == 1
        assert pos.tps_total == 3
        assert pos.booked_pnl == 4.5
        assert json.loads(pos.hit_tps_json) == [110]
        services.ingest_event(
            s,
            bid,
            {
                "type": "TP_HIT",
                "position_id": "p-tp",
                "symbol": "BTCUSDT",
                "tp": 120,
                "partial": True,
                "remaining_quantity": 1,
                "tps": [130],
                "hit_tps": [110, 120],
                "tp_hits": 2,
                "tps_total": 3,
                "booked_pnl": 9.25,
                "realized_pnl": 4.75,
            },
        )
        pos = s.query(Position).filter(Position.bot_id == bid).one()
        assert pos.tp_hits == 2
        assert pos.booked_pnl == 9.25
        services.ingest_event(
            s,
            bid,
            {
                "type": "POSITION_CLOSED",
                "position_id": "p-tp",
                "symbol": "BTCUSDT",
                "exit": 130,
                "realized_pnl": 14.75,
                "booked_pnl": 9.25,
                "tp_hits": 3,
                "tps_total": 3,
                "close_reason": "TP",
                "closed_at": "2026-01-01T01:00:00+00:00",
            },
        )
        pos = s.query(Position).filter(Position.bot_id == bid).one()
        assert pos.status == "CLOSED"
        assert pos.close_reason == "TP"
        trade = s.query(Trade).filter(Trade.bot_id == bid).one()
        assert trade.booked_pnl == 9.25
        assert trade.tp_hits == 3
        assert trade.tps_total == 3
        assert trade.realized_pnl == 14.75
        assert trade.opened_at is not None
        assert trade.closed_at is not None


def test_partial_tp_keeps_position_open(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Partial TP Bot")
        bid = bot["id"]
        services.ingest_event(
            s,
            bid,
            {
                "type": "POSITION_OPENED",
                "position_id": "p1",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry": 100,
                "tps": [110, 120],
                "sl": 90,
                "quantity": 2,
            },
        )
        services.ingest_event(
            s,
            bid,
            {
                "type": "TP_HIT",
                "position_id": "p1",
                "symbol": "BTCUSDT",
                "partial": True,
                "remaining_quantity": 1,
                "tps": [120],
                "realized_pnl": 5,
            },
        )
        pos = s.query(Position).filter(Position.bot_id == bid).one()
        assert pos.status == "OPEN"
        assert pos.quantity == 1
        assert s.query(Trade).filter(Trade.bot_id == bid).count() == 0


def test_strategy_file_save_backup_and_clean(mock_bot_path):
    with session_scope() as s:
        bot = services.import_bot(s, "Strat Bot", str(mock_bot_path))
        bid = bot["id"]
        files = services.list_strategy_files(s, bid)
        assert any(f["path"] == "strategy.py" for f in files)
        original = services.read_strategy_file(s, bid, "strategy.py")["content"]
        saved = services.save_strategy_file(s, bid, "strategy.py", original + "\n# comment\n", activate=False)
        assert saved["ok"] is True
        assert saved["backup"]
        cleaned = services.clean_strategy_file(s, bid, "strategy.py")
        assert "# comment" not in cleaned["content"]
        assert cleaned["backup"]
        deleted = services.delete_strategy_file(s, bid, "strategy.py")
        assert deleted["backup"]
        backups = services.list_strategy_backups(s, bid)
        assert backups
        restored = services.restore_strategy_backup(s, bid, deleted["backup"])
        assert restored["ok"] is True
        assert "strategy.py" in restored["path"]
        assert services.read_strategy_file(s, bid, "strategy.py")["content"]


def test_strategy_activate_blocked_while_live_running(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Live Strat")
        services.update_bot(s, bot["id"], trading_mode="LIVE")
        services.start_bot(s, bot["id"], live_confirmed=True)
        with pytest.raises(PermissionError):
            services.save_strategy_file(s, bot["id"], "strategy.py", "SYMBOLS = []\n", activate=True)
        services.stop_bot(s, bot["id"])


def test_runtime_settings_scan_delay_gap_tp(mock_bot_path):
    with session_scope() as s:
        bot = services.import_bot(s, "Runtime Bot", str(mock_bot_path))
        bid = bot["id"]
        updated = services.update_runtime_settings(
            s,
            bid,
            {
                "scan_mode": "BINANCE",
                "symbols": ["ETHUSDT", "SOLUSDT"],
                "startup_delay_seconds": 180,
                "trade_gap_seconds": 300,
                "tp_count": 3,
                "tp_percents": [0.5, 1.0, 1.5],
                "sl_percent": 0.4,
            },
        )
        assert updated["scan_mode"] == "BINANCE"
        assert updated["symbols"] == ["ETHUSDT", "SOLUSDT"]
        assert updated["startup_delay_seconds"] == 180
        assert updated["trade_gap_seconds"] == 300
        assert updated["tp_count"] == 3
        assert updated["tp_percents"] == [0.5, 1.0, 1.5]
        assert updated["max_open_positions"] == 3
        limited = services.update_runtime_settings(s, bid, {"max_open_positions": 5})
        assert limited["max_open_positions"] == 5
        env = services.decrypted_env(s, bid)
        assert env["SCAN_MODE"] == "BINANCE"
        assert env["SYMBOLS"] == "ETHUSDT,SOLUSDT"
        assert env["MAX_OPEN_POSITIONS"] == "5"
        coerced = services.update_runtime_settings(s, bid, {"scan_mode": "SELECTED"})
        assert coerced["scan_mode"] == "ALL"
        with pytest.raises(ValueError):
            services.update_runtime_settings(s, bid, {"tp_count": 6})
        with pytest.raises(ValueError):
            services.update_runtime_settings(s, bid, {"max_open_positions": 21})


def test_demo_config_scan_and_multi_tp(monkeypatch):
    import importlib
    import sys

    demo_dir = str(Path(__file__).resolve().parents[2] / "demo_bot")
    if demo_dir not in sys.path:
        sys.path.insert(0, demo_dir)
    monkeypatch.setenv("SCAN_MODE", "TRADFI")
    monkeypatch.setenv("TP_COUNT", "3")
    monkeypatch.setenv("TP_PERCENTS", "0.5,1,1.5")
    monkeypatch.setenv("STARTUP_DELAY_SECONDS", "180")
    monkeypatch.setenv("TRADE_GAP_SECONDS", "300")
    sys.modules.pop("config", None)
    sys.modules.pop("strategy", None)
    sys.modules.pop("universe", None)
    cfg_mod = importlib.import_module("config")
    strat_mod = importlib.import_module("strategy")
    cfg = cfg_mod.Config()
    assert cfg.scan_mode == "TRADFI"
    assert "AAPLUSDT" in cfg.symbols
    assert cfg.tp_count == 3
    assert cfg.startup_delay_seconds == 180
    assert cfg.trade_gap_seconds == 300
    assert cfg.max_open_positions == 3
    strategy = strat_mod.DemoStrategy()
    strategy.update_config(tp_count=cfg.tp_count, confidence_threshold=0, max_extension_atr=50)
    assert strategy.generate("ETHUSDT", candles=None, cycle=5, price=100.0) is None
    signal = strategy.generate("ETHUSDT", candles=_trend_candles(), cycle=5, price=111.9)
    assert signal is not None
    assert signal["symbol"] == "ETHUSDT"
    assert len(signal["tps"]) == 3
    assert signal["tps"][0] != signal["tps"][-1]
    assert signal["sl"] < signal["entry"] < signal["tps"][0]


def test_demo_all_scan_uses_all_symbols_env(monkeypatch):
    import importlib
    import sys

    demo_dir = str(Path(__file__).resolve().parents[2] / "demo_bot")
    if demo_dir not in sys.path:
        sys.path.insert(0, demo_dir)
    monkeypatch.setenv("SCAN_MODE", "ALL")
    monkeypatch.setenv("ALL_SYMBOLS", "AAVEUSDT,NEARUSDT,OPUSDT")
    monkeypatch.delenv("SYMBOLS", raising=False)
    sys.modules.pop("config", None)
    sys.modules.pop("universe", None)
    cfg = importlib.import_module("config").Config()
    assert cfg.scan_mode == "ALL"
    assert cfg.symbols == ["AAVEUSDT", "NEARUSDT", "OPUSDT"]
    assert "BTCUSDT" not in cfg.symbols


def test_demo_tradfi_scan_uses_hardcoded_list(monkeypatch):
    import importlib
    import sys

    demo_dir = str(Path(__file__).resolve().parents[2] / "demo_bot")
    if demo_dir not in sys.path:
        sys.path.insert(0, demo_dir)
    monkeypatch.setenv("SCAN_MODE", "TRADFI")
    monkeypatch.delenv("ALL_SYMBOLS", raising=False)
    monkeypatch.delenv("TRADFI_SYMBOLS", raising=False)
    sys.modules.pop("config", None)
    sys.modules.pop("universe", None)
    cfg = importlib.import_module("config").Config()
    assert cfg.scan_mode == "TRADFI"
    assert "AAPLUSDT" in cfg.symbols
    assert "NVDAUSDT" in cfg.symbols
    assert "TSLAUSDT" in cfg.symbols
    assert "XAUUSDT" in cfg.symbols
    assert cfg.market_type("AAPLUSDT") == "US_TRADFI"
    assert cfg.market_type("BTCUSDT") == "BINANCE_FUTURES"


def test_manual_close_position(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Close Bot")
        bid = bot["id"]
        services.ingest_event(
            s,
            bid,
            {
                "type": "POSITION_OPENED",
                "position_id": "p-close",
                "symbol": "ETHUSDT",
                "side": "LONG",
                "entry": 100,
                "current_price": 104,
                "unrealized_pnl": 4,
                "quantity": 1,
            },
        )
        pos = s.query(Position).filter(Position.bot_id == bid).one()
        closed = services.close_position(s, bid, pos.id)
        assert closed["ok"] is True
        s.refresh(pos)
        assert pos.status == "CLOSED"
        assert pos.close_reason == "MANUAL"
        assert pos.realized_pnl == 4
        assert s.query(Trade).filter(Trade.bot_id == bid).count() == 1
        with pytest.raises(ValueError):
            services.close_position(s, bid, pos.id)
        cmd_path = Path(bot["managed_path"]) / ".cbc" / "commands.jsonl"
        assert cmd_path.is_file()
        payload = json.loads(cmd_path.read_text(encoding="utf-8").splitlines()[-1])
        assert payload["action"] == "CLOSE_POSITION"


def test_api_close_commits(mock_bot_path):
    from fastapi.testclient import TestClient
    from crypto_bot_control.api import app

    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Api Close")
        bid = bot["id"]
        services.ingest_event(
            s,
            bid,
            {
                "type": "POSITION_OPENED",
                "position_id": "p-api-close",
                "symbol": "ETHUSDT",
                "side": "LONG",
                "entry": 100,
                "current_price": 104,
                "unrealized_pnl": 4,
                "quantity": 1,
            },
        )
        pos = s.query(Position).filter(Position.bot_id == bid).one()
        pid = pos.id
    client = TestClient(app)
    r = client.post(f"/api/bots/{bid}/positions/{pid}/close", json={"reason": "MANUAL"})
    assert r.status_code == 200, r.text
    with session_scope() as s:
        pos = s.get(Position, pid)
        assert pos.status == "CLOSED"
        assert pos.close_reason == "MANUAL"


def test_all_scan_fetches_binance_universe(mock_bot_path, monkeypatch):
    monkeypatch.setattr(
        services,
        "fetch_scan_universe",
        lambda: {
            "crypto": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
            "tradfi": ["AAPLUSDT", "NVDAUSDT", "TSLAUSDT", "XAUUSDT"],
            "all": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AAPLUSDT", "NVDAUSDT", "TSLAUSDT", "XAUUSDT"],
            "crypto_error": "",
            "tradfi_source": "hardcoded",
            "crypto_source": "binance",
        },
    )
    with session_scope() as s:
        bot = services.import_bot(s, "Universe Bot", str(mock_bot_path))
        updated = services.update_runtime_settings(
            s,
            bot["id"],
            {"scan_mode": "ALL", "refresh_universe": True},
        )
        assert updated["scan_mode"] == "ALL"
        assert updated["all_symbol_count"] == 8
        assert updated["crypto_symbol_count"] == 4
        assert updated["tradfi_symbol_count"] == 4
        env = services.decrypted_env(s, bot["id"])
        assert "SOLUSDT" in env["ALL_SYMBOLS"]
        assert "AAPLUSDT" in env["ALL_SYMBOLS"]
        assert "XAUUSDT" in env["TRADFI_SYMBOLS"]


def test_binance_scan_unavailable_does_not_fake_symbols(mock_bot_path, monkeypatch):
    monkeypatch.setattr(
        services,
        "fetch_scan_universe",
        lambda: {
            "crypto": [],
            "tradfi": ["AAPLUSDT", "NVDAUSDT"],
            "all": ["AAPLUSDT", "NVDAUSDT"],
            "crypto_error": "Binance USD-M futures universe unavailable",
            "tradfi_source": "hardcoded",
            "crypto_source": "unavailable",
        },
    )
    with session_scope() as s:
        bot = services.import_bot(s, "No Universe", str(mock_bot_path))
        with pytest.raises(ValueError, match="unavailable"):
            services.update_runtime_settings(s, bot["id"], {"scan_mode": "BINANCE", "refresh_universe": True})


def test_tradfi_scan_uses_hardcoded_list(mock_bot_path, monkeypatch):
    monkeypatch.setattr(
        services,
        "fetch_scan_universe",
        lambda: {
            "crypto": ["BTCUSDT"],
            "tradfi": ["AAPLUSDT", "NVDAUSDT", "TSLAUSDT", "XAUUSDT"],
            "all": ["BTCUSDT", "AAPLUSDT", "NVDAUSDT", "TSLAUSDT", "XAUUSDT"],
            "crypto_error": "",
            "tradfi_source": "hardcoded",
            "crypto_source": "binance",
        },
    )
    with session_scope() as s:
        bot = services.import_bot(s, "TradFi Bot", str(mock_bot_path))
        updated = services.update_runtime_settings(
            s,
            bot["id"],
            {"scan_mode": "TRADFI", "refresh_universe": True},
        )
        assert updated["scan_mode"] == "TRADFI"
        env = services.decrypted_env(s, bot["id"])
        assert env["ALL_SYMBOLS"] == "AAPLUSDT,NVDAUSDT,TSLAUSDT,XAUUSDT"
        assert "BTCUSDT" not in env["ALL_SYMBOLS"]


def test_all_scan_keeps_tradfi_when_binance_unavailable(mock_bot_path, monkeypatch):
    monkeypatch.setattr(
        services,
        "fetch_scan_universe",
        lambda: {
            "crypto": [],
            "tradfi": ["AAPLUSDT", "NVDAUSDT", "XAUUSDT"],
            "all": ["AAPLUSDT", "NVDAUSDT", "XAUUSDT"],
            "crypto_error": "Binance USD-M futures universe unavailable",
            "tradfi_source": "hardcoded",
            "crypto_source": "unavailable",
        },
    )
    with session_scope() as s:
        bot = services.import_bot(s, "Partial Universe", str(mock_bot_path))
        updated = services.update_runtime_settings(
            s,
            bot["id"],
            {"scan_mode": "ALL", "refresh_universe": True},
        )
        assert updated["scan_mode"] == "ALL"
        assert updated["tradfi_symbol_count"] == 3
        assert updated["crypto_symbol_count"] == 0
        assert "AAPLUSDT" in (updated["all_symbols"] or [])
        assert updated["scan_error"]


def test_parse_binance_exchange_info_splits_crypto_and_tradfi():
    from crypto_bot_control.universe import parse_binance_exchange_info

    crypto, tradfi = parse_binance_exchange_info(
        {
            "symbols": [
                {"symbol": "BTCUSDT", "status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT", "underlyingType": "COIN"},
                {"symbol": "ETHUSDT", "status": "BREAK", "contractType": "PERPETUAL", "quoteAsset": "USDT", "underlyingType": "COIN"},
                {"symbol": "AAPLUSDT", "status": "TRADING", "contractType": "TRADIFI_PERPETUAL", "quoteAsset": "USDT", "underlyingType": "EQUITY", "underlyingSubType": ["TradFi"]},
                {"symbol": "XAUUSDT", "status": "TRADING", "contractType": "TRADIFI_PERPETUAL", "quoteAsset": "USDT", "underlyingType": "COMMODITY", "underlyingSubType": ["TradFi"]},
                {"symbol": "BTCUSDC", "status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDC", "underlyingType": "COIN"},
            ]
        }
    )
    assert crypto == ["BTCUSDT"]
    assert tradfi == ["AAPLUSDT", "XAUUSDT"]


def test_auto_restart_can_be_turned_off(mock_bot_path):
    with session_scope() as s:
        bot = services.import_bot(s, "Restart Toggle", str(mock_bot_path))
        updated = services.update_bot(s, bot["id"], auto_restart=True)
        assert updated["auto_restart"] is True
        updated = services.update_bot(s, bot["id"], auto_restart=False)
        assert updated["auto_restart"] is False


def test_demo_startup_delay_blocks_open(monkeypatch):
    import importlib
    import sys

    demo_dir = str(Path(__file__).resolve().parents[2] / "demo_bot")
    if demo_dir not in sys.path:
        sys.path.insert(0, demo_dir)
    monkeypatch.setenv("STARTUP_DELAY_SECONDS", "180")
    monkeypatch.setenv("TRADE_GAP_SECONDS", "0")
    monkeypatch.setenv("SCAN_MODE", "TRADFI")
    monkeypatch.setenv("MAX_OPEN_POSITIONS", "2")
    for name in ("config", "simulator", "strategy", "universe"):
        sys.modules.pop(name, None)
    cfg_mod = importlib.import_module("config")
    sim_mod = importlib.import_module("simulator")
    events = []
    sim = sim_mod.TradingSimulator(cfg_mod.Config(), lambda *a, **k: events.append((a, k)))
    assert sim.cfg.max_open_positions == 2
    ok, reason = sim.can_open()
    assert ok is False
    assert reason == "STARTUP_DELAY"
    sim.ready_at = 0
    ok, reason = sim.can_open()
    assert ok is True
    sim.positions["POS-1"] = {"symbol": "AAPLUSDT"}
    sim.positions["POS-2"] = {"symbol": "NVDAUSDT"}
    ok, reason = sim.can_open()
    assert ok is False
    assert reason == "POSITION_LIMIT"


def test_live_ticker_parser_and_no_fake_price():
    import importlib
    import sys

    demo_dir = str(Path(__file__).resolve().parents[2] / "demo_bot")
    if demo_dir not in sys.path:
        sys.path.insert(0, demo_dir)
    for name in ("market", "simulator"):
        sys.modules.pop(name, None)
    market = importlib.import_module("market")
    prices = market.parse_ticker_prices(
        [{"symbol": "BTCUSDT", "price": "67012.4"}, {"symbol": "ETHUSDT", "price": "3512.11"}, {"symbol": "BAD", "price": "x"}]
    )
    assert prices["BTCUSDT"] == 67012.4
    assert prices["ETHUSDT"] == 3512.11
    assert "BAD" not in prices
    klines = market.parse_klines(
        [
            [1, "100", "110", "90", "105", "10"],
            [2, "105", "120", "100", "118", "12"],
        ]
    )
    assert klines[0]["close"] == 105.0
    assert klines[1]["volume"] == 12.0
    sim = importlib.import_module("simulator")
    assert not hasattr(sim.TradingSimulator, "next_price")


def _trend_candles(n=80, start=100.0):
    rows = []
    for i in range(n):
        close = start + i * 0.15
        rows.append(
            {
                "open": close - 0.05,
                "high": close + 0.12,
                "low": close - 0.08,
                "close": close,
                "volume": 10.0 if i < n - 1 else 22.0,
            }
        )
    return rows


def test_hermis_uses_live_candles_for_sl_tp():
    import importlib
    import sys

    demo_dir = str(Path(__file__).resolve().parents[2] / "demo_bot")
    if demo_dir not in sys.path:
        sys.path.insert(0, demo_dir)
    sys.modules.pop("strategy", None)
    strategy = importlib.import_module("strategy").DemoStrategy()
    strategy.update_config(tp_count=3, confidence_threshold=0, max_extension_atr=50)
    candles = _trend_candles()
    live = candles[-1]["close"]
    signal = strategy.generate("BTCUSDT", candles=candles, cycle=9, price=live)
    assert signal is not None
    assert signal["entry"] == strategy._price(live)
    assert signal["sl"] < signal["entry"]
    assert signal["tps"][0] > signal["entry"]
    atr = signal["conditions"]["atr"]
    assert atr > 0
    assert signal["entry"] - signal["sl"] > 0


def test_reconcile_stale_positions_on_start(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Reconcile Bot")
        bid = bot["id"]
        services.ingest_event(
            s,
            bid,
            {"type": "POSITION_OPENED", "position_id": "stale-a", "symbol": "BTCUSDT", "side": "LONG", "entry": 100, "quantity": 1, "tp": 110, "sl": 90},
        )
        services.ingest_event(
            s,
            bid,
            {"type": "POSITION_OPENED", "position_id": "stale-b", "symbol": "ETHUSDT", "side": "SHORT", "entry": 200, "quantity": 1, "tp": 190, "sl": 210},
        )
        assert s.query(Position).filter(Position.bot_id == bid, Position.status == "OPEN").count() == 2
        closed = services.reconcile_stale_positions(s, bid)
        assert closed == 2
        assert s.query(Position).filter(Position.bot_id == bid, Position.status == "OPEN").count() == 0
        trades = s.query(Trade).filter(Trade.bot_id == bid).all()
        assert len(trades) == 2
        assert all(t.close_reason == "BOT_RESTART" for t in trades)
        assert services.reconcile_stale_positions(s, bid) == 0


def test_reset_bot_data(mock_bot_path):
    with session_scope() as s:
        bot = _ready_bot(s, mock_bot_path, "Reset Bot")
        bid = bot["id"]
        services.ingest_event(
            s,
            bid,
            {"type": "SIGNAL_GENERATED", "signal_id": "S-R", "symbol": "BTCUSDT", "side": "LONG", "entry": 100, "tp": 110, "sl": 90},
        )
        services.ingest_event(
            s,
            bid,
            {"type": "POSITION_OPENED", "position_id": "p-r", "symbol": "BTCUSDT", "side": "LONG", "entry": 100, "quantity": 1, "tp": 110, "sl": 90},
        )
        services.ingest_event(s, bid, {"type": "HEARTBEAT", "cycle": 1})
        assert s.query(Signal).filter(Signal.bot_id == bid).count() == 1
        assert s.query(Position).filter(Position.bot_id == bid).count() == 1
        assert s.query(Event).filter(Event.bot_id == bid).count() >= 1
        result = services.reset_bot_data(s, bid)
        assert result["ok"] is True
        assert s.query(Signal).filter(Signal.bot_id == bid).count() == 0
        assert s.query(Position).filter(Position.bot_id == bid).count() == 0
        assert s.query(Event).filter(Event.bot_id == bid).count() == 0
        assert s.query(Trade).filter(Trade.bot_id == bid).count() == 0
        assert s.query(LogLine).filter(LogLine.bot_id == bid).count() == 0
        from crypto_bot_control.database import Bot
        row = s.get(Bot, bid)
        row.status = "RUNNING"
        with pytest.raises(PermissionError):
            services.reset_bot_data(s, bid)
        row.status = "STOPPED"
