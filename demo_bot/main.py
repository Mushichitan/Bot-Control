import time, json
from datetime import datetime, timezone
from config import Config
from simulator import TradingSimulator
from strategy import DemoStrategy

def emit(event_type, **data):
    event={"event":event_type,"timestamp":datetime.now(timezone.utc).isoformat(),**data}
    print("DEMO_EVENT "+json.dumps(event), flush=True)

def main():
    cfg=Config(); strategy=DemoStrategy(); simulator=TradingSimulator(cfg,emit)
    emit("BOT_STARTED",bot_name=cfg.bot_name,mode=cfg.mode)
    emit("HEALTH_UPDATE",bot="OK",exchange="SIMULATED",telegram="SIMULATED",database="OK",dependencies="OK")
    cycle=0
    try:
        while True:
            cycle+=1; price=simulator.next_price()
            emit("MARKET_UPDATE",symbol=cfg.symbol,price=price,cycle=cycle)
            simulator.update_position(price)
            signal=strategy.generate(price,cycle)
            if signal: emit("SIGNAL_GENERATED",**signal); simulator.open_position(signal)
            simulator.emit_report_if_due()
            emit("HEARTBEAT",cycle=cycle,open_positions=simulator.open_count())
            time.sleep(cfg.cycle_seconds)
    except KeyboardInterrupt: emit("BOT_STOPPED",reason="USER_INTERRUPT")
    except Exception as exc: emit("BOT_ERROR",error=str(exc),error_type=type(exc).__name__); raise

if __name__=="__main__": main()
