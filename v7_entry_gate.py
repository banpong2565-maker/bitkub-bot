"""V7 entry gate adapted from the supplied Google Apps Script scanner.

This module is intentionally isolated from order execution. It only decides whether
an existing BUY candidate passes the additional V7 Ultimate criteria.
"""
from __future__ import annotations

from typing import Dict, Tuple
import pandas as pd


def _ema(series: pd.Series, period: int) -> float:
    if series is None or len(series) == 0:
        return 0.0
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])


def exhaustion_filter(df5: pd.DataFrame) -> bool:
    if len(df5) < 20:
        return False
    ema20 = _ema(df5["close"], 20)
    if ema20 <= 0:
        return False
    last = float(df5["close"].iloc[-1])
    distance = ((last - ema20) / ema20) * 100.0
    return distance < 5.5


def trend_strength(df15: pd.DataFrame) -> bool:
    if len(df15) < 50:
        return False
    closes = df15["close"]
    return _ema(closes, 9) > _ema(closes, 21) > _ema(closes, 50)


def breakout_confirmation(df5: pd.DataFrame) -> bool:
    if len(df5) < 3:
        return False
    last = df5.iloc[-1]
    prev = df5.iloc[-2]
    body = abs(float(last["close"]) - float(last["open"]))
    candle_range = float(last["high"]) - float(last["low"])
    if candle_range <= 0:
        return False
    close_near_high = (float(last["high"]) - float(last["close"])) / candle_range < 0.2
    return (
        float(last["close"]) > float(prev["high"])
        and float(last["close"]) > float(last["open"])
        and body / candle_range > 0.58
        and close_near_high
        and float(last["volume"]) >= float(prev["volume"]) * 1.1
    )


def detect_whale_v3(df5: pd.DataFrame) -> bool:
    if len(df5) < 20:
        return False
    part = df5.iloc[-20:]
    avg_vol = float(part.iloc[:-1]["volume"].mean())
    last = part.iloc[-1]
    body = abs(float(last["close"]) - float(last["open"]))
    candle_range = float(last["high"]) - float(last["low"])
    if candle_range <= 0 or avg_vol <= 0:
        return False
    ema20 = _ema(part["close"], 20)
    return (
        float(last["volume"]) > avg_vol * 2.2
        and float(last["close"]) > float(last["open"])
        and body / candle_range > 0.55
        and float(last["close"]) > ema20
    )


def volume_expansion(df5: pd.DataFrame) -> bool:
    if len(df5) < 15:
        return False
    recent = float(df5["volume"].iloc[-5:].mean())
    old = float(df5["volume"].iloc[-15:-5].mean())
    return old > 0 and recent > old * 1.2


def price_efficiency(df5: pd.DataFrame) -> float:
    if len(df5) < 5:
        return 1.0
    recent = df5.iloc[-5:]
    body_sum = float((recent["close"] - recent["open"]).abs().sum())
    range_sum = float((recent["high"] - recent["low"]).sum()) + 0.0001
    return body_sum / range_sum


def _macd_values(df5: pd.DataFrame) -> Tuple[float, float, float, float, float, float]:
    close = df5["close"]
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    return (
        float(macd.iloc[-1]),
        float(signal.iloc[-1]),
        float(hist.iloc[-1]),
        float(macd.iloc[-2]),
        float(signal.iloc[-2]),
        float(hist.iloc[-2]),
    )


def calculate_v7_score(
    df5: pd.DataFrame,
    df15: pd.DataFrame,
    market_status: str = "CHOPPY / SIDEWAY",
) -> Dict:
    """Mirror the supplied Apps Script calculateUltimateScore criteria."""
    total = 0
    reasons = []

    if not exhaustion_filter(df5):
        return {"passed": False, "score": 0, "reasons": ["Exhaustion Filter failed"]}

    if trend_strength(df15):
        total += 10
        reasons.append("Trend Alignment")

    if breakout_confirmation(df5):
        total += 12
        reasons.append("Breakout Confirmed")

    if detect_whale_v3(df5):
        total += 18
        reasons.append("Smart Whale Accumulation")

    if volume_expansion(df5):
        total += 8
        reasons.append("Volume Expansion")

    rsi5 = float(df5["rsi"].iloc[-1]) if "rsi" in df5 else 50.0
    rsi15 = float(df15["rsi"].iloc[-1]) if "rsi" in df15 else 50.0
    if 45 <= rsi5 <= 68:
        total += 10
        if 50 < rsi5 < 58 and rsi15 > 50:
            total += 10
            reasons.append("RSI Origin Zone")
        else:
            reasons.append("Healthy RSI")

    macd, signal, hist, prev_macd, prev_signal, prev_hist = _macd_values(df5)
    if hist > prev_hist and macd > signal * 0.95:
        total += 18
        reasons.append("MACD Acceleration")

    if market_status == "ALT BULLISH":
        total += 8
        reasons.append("Altseason Env")
    elif market_status == "BEARISH / DUMP":
        total -= 30

    efficiency = price_efficiency(df5)
    if efficiency < 0.3:
        total -= 15

    total = max(0, min(100, total))
    return {
        "passed": total >= 76,
        "score": int(round(total)),
        "reasons": reasons,
        "price_efficiency": efficiency,
        "rsi5": rsi5,
        "rsi15": rsi15,
        "macd_hist": hist,
        "prev_macd_hist": prev_hist,
        "market_status": market_status,
    }
