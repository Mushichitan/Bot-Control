class DemoStrategy:
    def generate(self, price, cycle):
        if cycle < 2 or cycle % 5 != 0: return None
        side="LONG" if (cycle//5)%2 else "SHORT"
        tp=price*1.008 if side=="LONG" else price*0.992
        sl=price*0.995 if side=="LONG" else price*1.005
        return {"signal_id":f"SIG-{cycle:06d}","symbol":"BTCUSDT","side":side,"entry":round(price,2),"tp":round(tp,2),"sl":round(sl,2),"confidence":round(.72+(cycle%4)*.05,2),"strategy":"Demo Momentum","mode":"PAPER","execution_status":"PENDING","telegram_status":"SIMULATED"}
