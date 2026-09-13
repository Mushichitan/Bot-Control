import json
import time
from datetime import datetime, timezone
from pathlib import Path
from config import Config
from market import BinanceMarket
from simulator import TradingSimulator, _signal_tps

try:
    from strategy import DemoStrategy
except ImportError:
    class DemoStrategy:
        def generate(self, symbol, candles=None, cycle=0, price=None, cfg=None, **kwargs):
            return None

COMMAND_FILE = Path(__file__).resolve().parent / ".cbc" / "commands.jsonl"


def emit(event_type, **data):
    event = {"event": event_type, "timestamp": datetime.now(timezone.utc).isoformat(), **data}
    print("DEMO_EVENT " + json.dumps(event), flush=True)


def _call_strategy(strategy, symbol, candles, cycle, price, cfg):
    try:
        signal = strategy.generate(symbol, candles=candles, cycle=cycle, price=price, cfg=cfg)
    except TypeError:
        try:
            signal = strategy.generate(symbol, candles, cycle)
        except TypeError:
            try:
                signal = strategy.generate(symbol, price, cycle, cfg)
            except TypeError:
                return None
    if not signal:
        return None
    tps = _signal_tps(signal)
    if tps:
        signal["tps"] = tps
        signal["tp"] = tps[-1]
    return signal


def main():
    cfg = Config()
    strategy = DemoStrategy()
    if hasattr(strategy, "update_config"):
        strategy.update_config(tp_count=cfg.tp_count)
    market = BinanceMarket(interval="15m", kline_limit=80)
    emit("BOT_STARTED", bot_name=cfg.bot_name, mode=cfg.mode, scan_mode=cfg.scan_mode, symbols=cfg.symbols[:40], symbol_count=len(cfg.symbols))
    cfg.load_live_universe()
    simulator = TradingSimulator(cfg, emit)
    if cfg.universe_error and not cfg.symbols:
        emit("SCAN_UNAVAILABLE", message=cfg.universe_error, scan_mode=cfg.scan_mode)
        emit("HEALTH_UPDATE", bot="OK", exchange="DISCONNECTED", telegram="SIMULATED", database="OK", dependencies="OK")
    else:
        emit(
            "SCAN_UNIVERSE",
            count=len(cfg.symbols),
            scan_mode=cfg.scan_mode,
            crypto_count=len(cfg.crypto_symbols),
            tradfi_count=len(cfg.tradfi_symbols),
            sample=cfg.symbols[:12],
            universe=_universe_label(cfg.scan_mode),
        )
    if cfg.startup_delay_seconds:
        emit(
            "STARTUP_DELAY",
            seconds=cfg.startup_delay_seconds,
            message=f"Startup delay {cfg.startup_delay_seconds}s before first scan",
        )
    cycle = 0
    last_delay_emit = 0
    last_gap_emit = 0
    command_offset = 0
    batch_size = max(8, min(40, int(getattr(cfg, "scan_batch", 20) or 20)))
    try:
        while True:
            cycle += 1
            command_offset = consume_commands(simulator, command_offset)
            prices = {}
            try:
                prices = market.fetch_prices(cfg.symbols)
                simulator.set_prices(prices)
                emit("HEALTH_UPDATE", bot="OK", exchange="CONNECTED", telegram="SIMULATED", database="OK", dependencies="OK")
            except RuntimeError as exc:
                emit("HEALTH_UPDATE", bot="OK", exchange="DISCONNECTED", telegram="SIMULATED", database="OK", dependencies="OK")
                emit("SCAN_UNAVAILABLE", message=str(exc), scan_mode=cfg.scan_mode)
            watch = []
            if cfg.symbols and prices:
                start = ((cycle - 1) * batch_size) % len(cfg.symbols)
                batch = cfg.symbols[start:start + batch_size] or cfg.symbols[:batch_size]
                watch = [s for s in batch if s in prices]
                for symbol in watch:
                    emit(
                        "MARKET_UPDATE",
                        symbol=symbol,
                        price=prices[symbol],
                        cycle=cycle,
                        scan_mode=cfg.scan_mode,
                        market_type=cfg.market_type(symbol),
                        source="BINANCE_FAPI",
                    )
            open_needed = {p["symbol"] for p in simulator.positions.values()}
            monitor_prices = {s: prices[s] for s in open_needed if s in prices}
            monitor_prices.update({s: prices[s] for s in watch})
            simulator.update_positions(monitor_prices)
            delay_left = simulator.remaining_delay()
            gap_left = simulator.remaining_gap()
            now = time.monotonic()
            if delay_left > 0 and now - last_delay_emit >= 1:
                emit("STARTUP_DELAY", remaining=round(delay_left, 1), seconds=cfg.startup_delay_seconds)
                last_delay_emit = now
            elif delay_left <= 0 and gap_left > 0 and now - last_gap_emit >= 1:
                emit("TRADE_COOLDOWN", remaining=round(gap_left, 1), seconds=cfg.trade_gap_seconds)
                last_gap_emit = now
            elif watch:
                for symbol in watch:
                    candles = market.fetch_klines(symbol)
                    if len(candles) < 60:
                        continue
                    signal = _call_strategy(strategy, symbol, candles, cycle, prices[symbol], cfg)
                    if signal:
                        signal["market_type"] = cfg.market_type(symbol)
                        emit("SIGNAL_GENERATED", **signal)
                        simulator.open_position(signal)
            simulator.emit_report_if_due()
            emit("HEARTBEAT", cycle=cycle, open_positions=simulator.open_count(), symbols=len(cfg.symbols), live_prices=len(prices), source="BINANCE_FAPI")
            time.sleep(cfg.cycle_seconds)
    except KeyboardInterrupt:
        emit("BOT_STOPPED", reason="USER_INTERRUPT")
    except Exception as exc:
        emit("BOT_ERROR", error=str(exc), error_type=type(exc).__name__)
        raise


def _universe_label(scan_mode):
    return {
        "ALL": "Binance Futures + US TradFi",
        "BINANCE": "Binance Futures",
        "TRADFI": "US TradFi",
        "SELECTED": "Selected symbols",
    }.get(scan_mode, scan_mode)


def consume_commands(simulator, offset):
    if not COMMAND_FILE.exists():
        return offset
    raw = COMMAND_FILE.read_text(encoding="utf-8")
    lines = raw.splitlines()
    for line in lines[offset:]:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(cmd.get("action") or "").upper() != "CLOSE_POSITION":
            continue
        pid = str(cmd.get("position_id") or "")
        number = cmd.get("display_number")
        pos = None
        if pid and pid in simulator.positions:
            pos = simulator.positions[pid]
        if pos is None and number is not None:
            try:
                number = int(number)
            except (TypeError, ValueError):
                number = None
            if number is not None:
                for item in simulator.positions.values():
                    if int(item.get("number") or 0) == number:
                        pos = item
                        break
        if pos is None and pid:
            for item in simulator.positions.values():
                if str(item.get("number")) == pid or str(item.get("position_id")) == pid:
                    pos = item
                    break
        if pos:
            price = simulator.prices.get(pos["symbol"], pos.get("current_price") or pos.get("entry"))
            simulator.close_position(pos, "MANUAL", price)
    return len(lines)


if __name__ == "__main__":
    main()
