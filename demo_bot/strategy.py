"""
Hermis / Bot03 - Dynamic Binance Futures Multi-Symbol Strategy
No Binance symbols are hardcoded. The existing scanner supplies the symbols.
"""

from typing import Any, Dict, List, Optional
import math


class StrategyConfig:
    def __init__(self):
        self.timeframe = "15m"
        self.tp_count = 3
        self.confidence_threshold = 7
        self.sl_atr = 1.5
        self.tp_atr = [1.0, 1.8, 2.6, 3.5, 4.5]
        self.ema_fast = 9
        self.ema_slow = 21
        self.ema_trend = 50
        self.rsi_period = 14
        self.atr_period = 14
        self.volume_period = 20
        self.max_extension_atr = 1.8
        self.min_candles = 60


class BinanceMultiSymbolMomentumStrategy:
    name = "Hermis Dynamic Momentum"
    version = "1.0.0"

    def __init__(self, config=None):
        self.config = config or StrategyConfig()
        self.config.tp_count = max(1, min(int(self.config.tp_count), 5))
        self.config.confidence_threshold = max(
            0, min(int(self.config.confidence_threshold), 10)
        )

    def generate(self, symbol, candles=None, cycle=0, price=None, **kwargs):
        """
        Preferred call:
            generate(symbol, candles, cycle)

        candles must contain at least 60 OHLCV rows. Each row may be a dict
        or an object with open/high/low/close/volume attributes.
        Returns a signal dict or None.
        """
        symbol = str(symbol or "").upper().strip()
        if not symbol:
            return None

        rows = self._normalize(candles)
        if len(rows) < self.config.min_candles:
            return None

        closes = [x["close"] for x in rows]
        volumes = [x["volume"] for x in rows]
        last = closes[-1]
        try:
            live = float(price) if price is not None else 0.0
        except (TypeError, ValueError):
            live = 0.0
        if live > 0:
            last = live

        ema_fast = self._ema(closes, self.config.ema_fast)
        ema_slow = self._ema(closes, self.config.ema_slow)
        ema_trend = self._ema(closes, self.config.ema_trend)
        rsi = self._rsi(closes, self.config.rsi_period)
        atr = self._atr(rows, self.config.atr_period)
        avg_volume = self._sma(volumes, self.config.volume_period)

        if not all(math.isfinite(x) for x in (ema_fast, ema_slow, ema_trend, rsi, atr)):
            return None
        if last <= 0 or atr <= 0 or avg_volume <= 0:
            return None

        volume_ratio = volumes[-1] / avg_volume
        lookback = min(5, len(closes) - 1)
        momentum = (last / closes[-1 - lookback]) - 1.0
        extension = abs(last - ema_fast) / atr

        # Do not chase an already over-extended move.
        if extension > self.config.max_extension_atr:
            return None

        long_checks = {
            "trend": ema_fast > ema_slow and last > ema_trend,
            "momentum": momentum > 0,
            "rsi": 52 <= rsi <= 72,
            "volume": volume_ratio >= 1.05,
        }
        short_checks = {
            "trend": ema_fast < ema_slow and last < ema_trend,
            "momentum": momentum < 0,
            "rsi": 28 <= rsi <= 48,
            "volume": volume_ratio >= 1.05,
        }

        long_score = self._score(long_checks)
        short_score = self._score(short_checks)

        if long_score == short_score:
            return None

        side = "LONG" if long_score > short_score else "SHORT"
        score = max(long_score, short_score)
        checks = long_checks if side == "LONG" else short_checks

        # Trend + momentum are mandatory; confidence is configurable.
        if not checks["trend"] or not checks["momentum"]:
            return None
        if score < self.config.confidence_threshold:
            return None

        if side == "LONG":
            sl = last - atr * self.config.sl_atr
            tps = [last + atr * x for x in self.config.tp_atr]
        else:
            sl = last + atr * self.config.sl_atr
            tps = [last - atr * x for x in self.config.tp_atr]

        tps = [self._price(x) for x in tps[:self.config.tp_count]]
        sl = self._price(sl)
        entry = self._price(last)

        if side == "LONG" and not (sl < entry < tps[0]):
            return None
        if side == "SHORT" and not (sl > entry > tps[0]):
            return None

        passed = [k for k, v in checks.items() if v]
        failed = [k for k, v in checks.items() if not v]

        signal = {
            "signal_id": f"HERMIS-{symbol}-{cycle}-{int(last * 1000000) % 1000000:06d}",
            "symbol": symbol,
            "side": side,
            "entry": entry,
            "sl": sl,
            "tp": tps[-1] if tps else entry,
            "tps": tps,
            "confidence": int(score),
            "confidence_display": f"{int(score)} / 10",
            "strategy": self.name,
            "strategy_version": self.version,
            "timeframe": self.config.timeframe,
            "tp_count": self.config.tp_count,
            "score": int(score),
            "conditions": {
                "passed": passed,
                "failed": failed,
                "rsi": round(rsi, 2),
                "volume_ratio": round(volume_ratio, 2),
                "momentum_pct": round(momentum * 100, 3),
                "ema_fast": self._price(ema_fast),
                "ema_slow": self._price(ema_slow),
                "ema_trend": self._price(ema_trend),
                "atr": self._price(atr),
            },
            "mode": "PAPER",
            "execution_status": "PENDING",
            "telegram_status": "PENDING",
            "candidate_status": "QUALIFIED",
        }

        for i, tp in enumerate(tps, 1):
            signal[f"tp{i}"] = self._price(tp)

        return signal

    def update_config(self, **values):
        for key, value in values.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        self.config.tp_count = max(1, min(int(self.config.tp_count), 5))
        self.config.confidence_threshold = max(
            0, min(int(self.config.confidence_threshold), 10)
        )

    @staticmethod
    def _normalize(candles):
        if candles is None or isinstance(candles, (str, bytes, int, float)):
            return []
        out = []
        for c in candles:
            try:
                if isinstance(c, dict):
                    get = c.get
                else:
                    get = lambda k, d=0.0: getattr(c, k, d)
                row = {
                    "open": float(get("open", 0)),
                    "high": float(get("high", 0)),
                    "low": float(get("low", 0)),
                    "close": float(get("close", 0)),
                    "volume": float(get("volume", 0)),
                }
                if row["close"] > 0 and row["high"] >= row["low"] and row["volume"] >= 0:
                    out.append(row)
            except (TypeError, ValueError):
                pass
        return out

    @staticmethod
    def _sma(values, period):
        period = min(period, len(values))
        return sum(values[-period:]) / period if period else float("nan")

    @staticmethod
    def _ema(values, period):
        if not values:
            return float("nan")
        alpha = 2.0 / (period + 1.0)
        ema = values[0]
        for value in values[1:]:
            ema = alpha * value + (1 - alpha) * ema
        return ema

    @staticmethod
    def _rsi(values, period=14):
        if len(values) < period + 1:
            return float("nan")
        gains, losses = [], []
        for i in range(1, len(values)):
            d = values[i] - values[i - 1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        ag = sum(gains[:period]) / period
        al = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            ag = ((ag * (period - 1)) + gains[i]) / period
            al = ((al * (period - 1)) + losses[i]) / period
        if al == 0:
            return 100.0
        rs = ag / al
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _atr(rows, period=14):
        if len(rows) < 2:
            return float("nan")
        trs = []
        for i, row in enumerate(rows):
            if i == 0:
                tr = row["high"] - row["low"]
            else:
                prev = rows[i - 1]["close"]
                tr = max(
                    row["high"] - row["low"],
                    abs(row["high"] - prev),
                    abs(row["low"] - prev),
                )
            trs.append(max(tr, 0))
        period = min(period, len(trs))
        return sum(trs[-period:]) / period

    @staticmethod
    def _score(checks):
        # Trend=3, momentum=2, RSI=2, volume=2.
        # Correlated evidence is grouped rather than blindly double-counted.
        weights = {"trend": 3, "momentum": 2, "rsi": 2, "volume": 2}
        raw = sum(weights[k] for k, ok in checks.items() if ok)
        return min(10, round(raw / 9.0 * 10))

    @staticmethod
    def _price(value):
        if value >= 1000:
            return round(value, 2)
        if value >= 1:
            return round(value, 4)
        if value >= 0.01:
            return round(value, 6)
        return round(value, 8)


# Compatibility aliases for existing strategy loaders.
Strategy = BinanceMultiSymbolMomentumStrategy
DemoStrategy = BinanceMultiSymbolMomentumStrategy


def create_strategy(config=None):
    cfg = StrategyConfig()
    if isinstance(config, dict):
        for key, value in config.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
    return BinanceMultiSymbolMomentumStrategy(cfg)
