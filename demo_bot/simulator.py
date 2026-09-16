import time


def _signal_tps(s):
    tps = list(s.get("tps") or [])
    if tps:
        return tps
    numbered = []
    for i in range(1, 8):
        val = s.get(f"tp{i}")
        if val is None:
            break
        numbered.append(val)
    if numbered:
        return numbered
    if s.get("tp") is not None:
        return [s["tp"]]
    return []


class TradingSimulator:
    def __init__(self, cfg, emit):
        self.cfg = cfg
        self.emit = emit
        self.prices = {}
        self.balance = cfg.initial_balance
        self.positions = {}
        self.trade_counter = 0
        self.last_report = time.monotonic()
        self.last_close_at = 0.0
        self.ready_at = time.monotonic() + max(0, cfg.startup_delay_seconds)

    def set_prices(self, prices):
        for symbol, price in (prices or {}).items():
            try:
                value = float(price)
            except (TypeError, ValueError):
                continue
            if value > 0:
                self.prices[str(symbol).upper()] = value

    def remaining_delay(self):
        return max(0.0, self.ready_at - time.monotonic())

    def remaining_gap(self):
        if not self.last_close_at:
            return 0.0
        return max(0.0, self.cfg.trade_gap_seconds - (time.monotonic() - self.last_close_at))

    def can_open(self):
        limit = max(1, int(getattr(self.cfg, "max_open_positions", 1) or 1))
        if len(self.positions) >= limit:
            return False, "POSITION_LIMIT"
        if self.remaining_delay() > 0:
            return False, "STARTUP_DELAY"
        if self.remaining_gap() > 0:
            return False, "TRADE_GAP"
        return True, ""

    def open_position(self, s):
        ok, reason = self.can_open()
        if not ok:
            self.emit("ORDER_REJECTED", signal_id=s.get("signal_id"), reason=reason, symbol=s.get("symbol"))
            return
        self.trade_counter += 1
        pid = f"POS-{self.trade_counter:06d}"
        qty = self.cfg.position_size_usdt / s["entry"]
        tps = _signal_tps(s)
        remaining = list(tps)
        p = {
            "position_id": pid,
            "number": self.trade_counter,
            "signal_id": s["signal_id"],
            "symbol": s["symbol"],
            "side": s["side"],
            "entry": s["entry"],
            "current_price": s["entry"],
            "quantity": round(qty, 6),
            "initial_quantity": round(qty, 6),
            "tp": tps[-1] if tps else s.get("tp"),
            "tps": tps,
            "remaining_tps": remaining,
            "hit_tps": [],
            "tp_hits": 0,
            "booked_pnl": 0.0,
            "sl": s["sl"],
            "unrealized_pnl": 0.0,
            "realized_pnl": 0.0,
            "opened_at": time.time(),
            "status": "OPEN",
        }
        self.positions[pid] = p
        self.emit(
            "ORDER_SUBMITTED",
            order_id=f"ORD-{self.trade_counter:06d}",
            position_id=pid,
            signal_id=s["signal_id"],
            side=s["side"],
            price=s["entry"],
            quantity=round(qty, 6),
        )
        self.emit("ORDER_FILLED", order_id=f"ORD-{self.trade_counter:06d}", position_id=pid, fill_price=s["entry"])
        self.emit("POSITION_OPENED", **{k: v for k, v in p.items() if k != "remaining_tps"})
        tp_text = "/".join(str(x) for x in tps) if tps else str(s.get("tp"))
        self.emit(
            "TELEGRAM_SENT",
            message_type="SIGNAL",
            text=f"[DEMO] {s['side']} {s['symbol']} Entry {s['entry']} TP {tp_text} SL {s['sl']}",
        )

    def _pnl(self, p, price, qty=None):
        qty = p["quantity"] if qty is None else qty
        if p["side"] == "LONG":
            return (price - p["entry"]) * qty
        return (p["entry"] - price) * qty

    def update_positions(self, prices):
        for pid, p in list(self.positions.items()):
            price = prices.get(p["symbol"])
            if price is None:
                continue
            p["current_price"] = round(price, 2)
            p["unrealized_pnl"] = round(self._pnl(p, price), 2)
            self.emit(
                "POSITION_UPDATED",
                position_id=pid,
                number=p["number"],
                symbol=p["symbol"],
                side=p["side"],
                entry=p["entry"],
                current_price=p["current_price"],
                tp=p["tp"],
                tps=p.get("remaining_tps") or p.get("tps") or [],
                hit_tps=list(p.get("hit_tps") or []),
                tp_hits=p.get("tp_hits", 0),
                tps_total=len(p.get("tps") or []),
                booked_pnl=p.get("booked_pnl", 0.0),
                sl=p["sl"],
                quantity=p["quantity"],
                unrealized_pnl=p["unrealized_pnl"],
            )
            hit = None
            remaining = list(p.get("remaining_tps") or [])
            if remaining:
                nxt = remaining[0]
                if (p["side"] == "LONG" and price >= nxt) or (p["side"] == "SHORT" and price <= nxt):
                    hit = nxt
            if hit is not None:
                self._take_profit(p, price, hit)
            elif (p["side"] == "LONG" and price <= p["sl"]) or (p["side"] == "SHORT" and price >= p["sl"]):
                self.close_position(p, "SL_HIT", price)

    def _take_profit(self, p, price, hit):
        remaining = list(p.get("remaining_tps") or [])
        remaining.pop(0)
        p["remaining_tps"] = remaining
        p["hit_tps"].append(hit)
        p["tp_hits"] = len(p["hit_tps"])
        slices = max(1, len(p.get("tps") or [hit]))
        slice_qty = p["initial_quantity"] / slices
        close_qty = min(p["quantity"], slice_qty)
        realized = round(self._pnl(p, price, close_qty), 2)
        p["realized_pnl"] = round(p.get("realized_pnl", 0.0) + realized, 2)
        p["booked_pnl"] = round(p.get("booked_pnl", 0.0) + realized, 2)
        p["quantity"] = round(max(0.0, p["quantity"] - close_qty), 6)
        last = not remaining or p["quantity"] <= 1e-9
        payload = {
            "position_id": p["position_id"],
            "number": p["number"],
            "symbol": p["symbol"],
            "side": p["side"],
            "entry": p["entry"],
            "exit": round(price, 2),
            "tp": hit,
            "tps": remaining,
            "hit_tps": list(p["hit_tps"]),
            "tp_hits": p["tp_hits"],
            "tps_total": len(p.get("tps") or []),
            "booked_pnl": p["booked_pnl"],
            "sl": p["sl"],
            "realized_pnl": realized,
            "partial": not last,
            "remaining_quantity": p["quantity"],
            "close_reason": "TP",
        }
        self.emit("TP_HIT", **payload)
        if last:
            p["unrealized_pnl"] = 0.0
            p["quantity"] = 0.0
            self.close_position(p, "TP_HIT", price, already_emitted=True)
        else:
            p["unrealized_pnl"] = round(self._pnl(p, price), 2)
            self.emit(
                "TELEGRAM_SENT",
                message_type="TP_PARTIAL",
                text=f"[DEMO] #{p['number']:03d} TP {hit} {p['symbol']} partial P&L {realized:+.2f}",
            )

    def close_position(self, p, reason, exit_price, already_emitted=False):
        pnl = round(p.get("realized_pnl", 0.0) + (p.get("unrealized_pnl") or 0.0), 2)
        self.balance += pnl
        self.positions.pop(p["position_id"], None)
        self.last_close_at = time.monotonic()
        trade = {
            "position_id": p["position_id"],
            "number": p["number"],
            "symbol": p["symbol"],
            "side": p["side"],
            "entry": p["entry"],
            "exit": round(exit_price, 2),
            "tp": p.get("tp"),
            "tps": p.get("tps") or [],
            "hit_tps": list(p.get("hit_tps") or []),
            "tp_hits": p.get("tp_hits", 0),
            "tps_total": len(p.get("tps") or []),
            "booked_pnl": p.get("booked_pnl", 0.0),
            "sl": p["sl"],
            "realized_pnl": round(pnl, 2),
            "close_reason": reason,
            "opened_at": p.get("opened_at"),
            "closed_at": time.time(),
            "status": "CLOSED",
        }
        if not already_emitted:
            self.emit(reason, **trade)
        self.emit("POSITION_CLOSED", **trade)
        self.emit(
            "TELEGRAM_SENT",
            message_type="TRADE_CLOSED",
            text=f"[DEMO] #{p['number']:03d} {reason} {p['symbol']} P&L {pnl:+.2f}",
        )

    def open_count(self):
        return len(self.positions)

    def emit_report_if_due(self):
        if time.monotonic() - self.last_report < self.cfg.report_interval_seconds:
            return
        self.last_report = time.monotonic()
        u = sum(p["unrealized_pnl"] for p in self.positions.values())
        r = self.balance - self.cfg.initial_balance
        d = {
            "balance": round(self.balance, 2),
            "realized_pnl": round(r, 2),
            "unrealized_pnl": round(u, 2),
            "total_pnl": round(r + u, 2),
            "open_positions": len(self.positions),
        }
        self.emit("REPORT_HOURLY", period="HOURLY_DEMO", **d)
        self.emit("REPORT_4H", period="4H_DEMO", **d)
