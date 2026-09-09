import random,time
class TradingSimulator:
    def __init__(self,cfg,emit): self.cfg=cfg; self.emit=emit; self.price=100000.; self.balance=cfg.initial_balance; self.positions={}; self.trade_counter=0; self.last_report=time.monotonic()
    def next_price(self): self.price=max(50000,self.price+random.uniform(-260,260)); return round(self.price,2)
    def open_position(self,s):
        if self.positions: self.emit("ORDER_REJECTED",signal_id=s["signal_id"],reason="DEMO_MAX_ONE_OPEN_POSITION"); return
        self.trade_counter+=1; pid=f"POS-{self.trade_counter:06d}"; q=self.cfg.position_size_usdt/s["entry"]
        p={"position_id":pid,"number":self.trade_counter,"signal_id":s["signal_id"],"symbol":s["symbol"],"side":s["side"],"entry":s["entry"],"current_price":s["entry"],"quantity":round(q,6),"tp":s["tp"],"sl":s["sl"],"unrealized_pnl":0.0,"opened_at":time.time(),"status":"OPEN"}; self.positions[pid]=p
        self.emit("ORDER_SUBMITTED",order_id=f"ORD-{self.trade_counter:06d}",position_id=pid,signal_id=s["signal_id"],side=s["side"],price=s["entry"],quantity=round(q,6)); self.emit("ORDER_FILLED",order_id=f"ORD-{self.trade_counter:06d}",position_id=pid,fill_price=s["entry"]); self.emit("POSITION_OPENED",**p)
        self.emit("TELEGRAM_SENT",message_type="SIGNAL",text=f"[DEMO] {s['side']} {s['symbol']} Entry {s['entry']} TP {s['tp']} SL {s['sl']}")
    def update_position(self,price):
        if not self.positions:return
        pid,p=next(iter(self.positions.items())); p["current_price"]=round(price,2)
        pnl=(price-p["entry"])*p["quantity"] if p["side"]=="LONG" else (p["entry"]-price)*p["quantity"]; p["unrealized_pnl"]=round(pnl,2)
        self.emit("POSITION_UPDATED",position_id=pid,number=p["number"],symbol=p["symbol"],side=p["side"],entry=p["entry"],current_price=p["current_price"],tp=p["tp"],sl=p["sl"],unrealized_pnl=p["unrealized_pnl"])
        if (p["side"]=="LONG" and price>=p["tp"]) or (p["side"]=="SHORT" and price<=p["tp"]): self.close_position(p,"TP_HIT",price)
        elif (p["side"]=="LONG" and price<=p["sl"]) or (p["side"]=="SHORT" and price>=p["sl"]): self.close_position(p,"SL_HIT",price)
    def close_position(self,p,reason,exit_price):
        pnl=p["unrealized_pnl"]; self.balance+=pnl; self.positions.pop(p["position_id"],None); trade={"position_id":p["position_id"],"number":p["number"],"symbol":p["symbol"],"side":p["side"],"entry":p["entry"],"exit":round(exit_price,2),"tp":p["tp"],"sl":p["sl"],"realized_pnl":round(pnl,2),"close_reason":reason,"status":"CLOSED"}; self.emit(reason,**trade); self.emit("POSITION_CLOSED",**trade); self.emit("TELEGRAM_SENT",message_type="TRADE_CLOSED",text=f"[DEMO] #{p['number']:03d} {reason} {p['symbol']} P&L {pnl:+.2f}")
    def open_count(self): return len(self.positions)
    def emit_report_if_due(self):
        if time.monotonic()-self.last_report<self.cfg.report_interval_seconds:return
        self.last_report=time.monotonic(); u=sum(p["unrealized_pnl"] for p in self.positions.values()); r=self.balance-self.cfg.initial_balance; d={"balance":round(self.balance,2),"realized_pnl":round(r,2),"unrealized_pnl":round(u,2),"total_pnl":round(r+u,2),"open_positions":len(self.positions)}; self.emit("REPORT_HOURLY",period="HOURLY_DEMO",**d); self.emit("REPORT_4H",period="4H_DEMO",**d)
