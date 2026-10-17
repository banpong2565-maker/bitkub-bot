import pandas as pd
import numpy as np


def parse_candles_to_dataframe(candles_data: dict) -> pd.DataFrame:
    """แปลงข้อมูลดิบของแท่งเทียนที่ได้จาก TradingView History API เป็น Pandas DataFrame"""
    if not candles_data or candles_data.get("s") != "ok":
        return pd.DataFrame()

    try:
        df = pd.DataFrame(
            {
                "timestamp": candles_data["t"],
                "open": candles_data["open"] if "open" in candles_data else candles_data["o"],
                "high": candles_data["high"] if "high" in candles_data else candles_data["h"],
                "low": candles_data["low"] if "low" in candles_data else candles_data["l"],
                "close": candles_data["close"] if "close" in candles_data else candles_data["c"],
                "volume": candles_data["volume"] if "volume" in candles_data else candles_data["v"],
            }
        )

        # แปลงเวลาเป็น Datetime และตัวเลขทั้งหมดเป็น Float
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        return df
    except Exception as e:
        print(f"[⚠️ ข้อผิดพลาด] แปลงแท่งเทียนเป็น DataFrame ล้มเหลว: {e}")
        return pd.DataFrame()


def calculate_supertrend(df, period=10, multiplier=3):
    """คำนวณ SuperTrend ดั้งเดิม (10 periods, 3 multiplier)"""
    if len(df) < period + 2:
        return pd.Series(0.0, index=df.index), pd.Series(1, index=df.index)

    high = df["high"]
    low = df["low"]
    close = df["close"]

    # คำนวณ ATR 10
    atr = df["tr"].rolling(window=period).mean().fillna(0.0)

    hl2 = (high + low) / 2
    basic_ub = hl2 + multiplier * atr
    basic_lb = hl2 - multiplier * atr

    final_ub = basic_ub.copy()
    final_lb = basic_lb.copy()

    # ใช้ลูปคำนวณหาแบนด์สุดท้ายแบบไดนามิก
    for i in range(1, len(df)):
        # Upper Band
        if basic_ub.iloc[i] < final_ub.iloc[i - 1] or close.iloc[i - 1] > final_ub.iloc[i - 1]:
            final_ub.iloc[i] = basic_ub.iloc[i]
        else:
            final_ub.iloc[i] = final_ub.iloc[i - 1]

        # Lower Band
        if basic_lb.iloc[i] > final_lb.iloc[i - 1] or close.iloc[i - 1] < final_lb.iloc[i - 1]:
            final_lb.iloc[i] = basic_lb.iloc[i]
        else:
            final_lb.iloc[i] = final_lb.iloc[i - 1]

    # คำนวณทิศทางแนวโน้มและเส้น SuperTrend
    direction = pd.Series(1, index=df.index)
    supertrend = pd.Series(0.0, index=df.index)

    for i in range(1, len(df)):
        if direction.iloc[i - 1] == 1:
            if close.iloc[i] < final_lb.iloc[i]:
                direction.iloc[i] = -1
                supertrend.iloc[i] = final_ub.iloc[i]
            else:
                direction.iloc[i] = 1
                supertrend.iloc[i] = final_lb.iloc[i]
        else:
            if close.iloc[i] > final_ub.iloc[i]:
                direction.iloc[i] = 1
                supertrend.iloc[i] = final_lb.iloc[i]
            else:
                direction.iloc[i] = -1
                supertrend.iloc[i] = final_ub.iloc[i]

    return supertrend, direction


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """คำนวณตัวชี้วัดทางเทคนิคทั้งหมด รวม EMA200 สำหรับกรองแนวโน้มใหญ่"""
    df = df.copy()
    if len(df) < 30:
        return df

    # 1. EMA 9, EMA 21 และ EMA 50 (กรองแนวโน้มหลัก)
    df["ema_fast"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=21, adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()

    # 2. RSI 14 (Wilder's RSI)
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    df["rsi"] = df["rsi"].fillna(50.0)

    # 3. MACD (12, 26, 9)
    df["ema12"] = df["close"].ewm(span=12, adjust=False).mean()
    df["ema26"] = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = df["ema12"] - df["ema26"]
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # 4. True Range (TR) & Average True Range (ATR 14)
    high_low = df["high"] - df["low"]
    high_cp = np.abs(df["high"] - df["close"].shift(1))
    low_cp = np.abs(df["low"] - df["close"].shift(1))
    df["tr"] = np.maximum(high_low, np.maximum(high_cp, low_cp))
    df["atr"] = df["tr"].ewm(com=13, adjust=False).mean()

    # 5. ADX 14, +DI, -DI
    up_move = df["high"] - df["high"].shift(1)
    down_move = df["low"].shift(1) - df["low"]

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    smoothed_tr = df["tr"].ewm(com=13, adjust=False).mean()
    smoothed_plus_dm = pd.Series(plus_dm, index=df.index).ewm(com=13, adjust=False).mean()
    smoothed_minus_dm = pd.Series(minus_dm, index=df.index).ewm(com=13, adjust=False).mean()

    plus_di = 100 * (smoothed_plus_dm / smoothed_tr.replace(0, np.nan))
    minus_di = 100 * (smoothed_minus_dm / smoothed_tr.replace(0, np.nan))
    df["plus_di"] = plus_di.fillna(0.0)
    df["minus_di"] = minus_di.fillna(0.0)

    di_sum = plus_di + minus_di
    dx = 100 * np.abs(plus_di - minus_di) / di_sum.replace(0, np.nan)
    df["adx"] = dx.ewm(com=13, adjust=False).mean()
    df["adx"] = df["adx"].fillna(0.0)

    # 6. Volume Average (20)
    df["volume_ma"] = df["volume"].rolling(window=20).mean()

    # 7. Bollinger Bands (20 periods, 2 standard deviations)
    df["bb_middle"] = df["close"].rolling(window=20).mean()
    df["bb_std"] = df["close"].rolling(window=20).std()
    df["bb_upper"] = df["bb_middle"] + (df["bb_std"] * 2)
    df["bb_lower"] = df["bb_middle"] - (df["bb_std"] * 2)
    df["bb_bandwidth"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_middle"]

    # 8. VWAP (Volume Weighted Average Price - rolling 20)
    df["vwap"] = (df["volume"] * df["close"]).rolling(window=20).sum() / df["volume"].rolling(window=20).sum()
    df["vwap"] = df["vwap"].fillna(df["close"])

    # 9. Donchian Channel (20 periods)
    df["donchian_high"] = df["high"].rolling(window=20).max()
    df["donchian_low"] = df["low"].rolling(window=20).min()

    # 10. SuperTrend (10 periods, 3 multiplier)
    df["supertrend"], df["supertrend_dir"] = calculate_supertrend(df, period=10, multiplier=3)

    return df


# ======================= ⭐️ NEW V7 ADVANCED FILTERS ======================= #

def calculate_ema(prices, period):
    """Simple EMA calculation"""
    if len(prices) < period:
        return prices[-1] if prices else 0
    k = 2 / (period + 1)
    ema = prices[0]
    for price in prices[1:]:
        ema = price * k + ema * (1 - k)
    return ema


def exhaustion_filter(candles_df):
    """🎯 Exhaustion Filter: ป้องกันการเข้าเมื่อราคาห่างจาก EMA20 มาก (V7)
    
    ยอมให้ระยะห่างสูงสุด < 5.5% เพื่อป้องกันการติดในจุดสูง
    """
    if len(candles_df) < 20:
        return False
    
    close_prices = candles_df["close"].values
    ema20 = calculate_ema(list(close_prices), 20)
    last_price = close_prices[-1]
    
    if ema20 == 0:
        return False
    
    distance = ((last_price - ema20) / ema20) * 100
    return distance < 5.5


def breakout_confirmation(candles_df):
    """⭐️ Breakout Confirmation (V7): ยืนยันการแตกทะลุแบบแม่นยำ (0.58 body ratio)
    
    ตรวจสอบ:
    - ปิดเหนือแท่งก่อนหน้า
    - ปิดเหนือเปิด (bullish candle)
    - Body % >= 0.58 ของ range
    - ปิดใกล้ high (< 0.2 candle range)
    - Volume spike >= 1.1x
    """
    if len(candles_df) < 2:
        return False
    
    last = candles_df.iloc[-1]
    prev = candles_df.iloc[-2]
    
    body = abs(last["close"] - last["open"])
    candle_range = last["high"] - last["low"]
    
    if candle_range == 0:
        return False
    
    close_near_high = (last["high"] - last["close"]) / candle_range < 0.2
    
    return (
        last["close"] > prev["high"] and
        last["close"] > last["open"] and
        body / candle_range > 0.58 and
        close_near_high and
        last["volume"] >= prev["volume"] * 1.1
    )


def detect_whale_v3(candles_df):
    """🐋 Smart Whale Accumulation Detection (V7)
    
    ตรวจจับสัญญาณการสะสมของ smart money:
    - Volume spike > 2.2x average (last 20 candles)
    - Bullish candle (close > open)
    - Body ratio >= 0.55
    - Close > EMA20
    """
    if len(candles_df) < 20:
        return False
    
    candles_slice = candles_df.iloc[-20:]
    
    # Average volume from first 19 candles
    avg_vol = candles_slice.iloc[:-1]["volume"].mean()
    last = candles_slice.iloc[-1]
    
    body = abs(last["close"] - last["open"])
    candle_range = last["high"] - last["low"]
    
    if candle_range == 0 or avg_vol == 0:
        return False
    
    close_prices = candles_slice["close"].values
    ema20 = calculate_ema(list(close_prices), 20)
    
    return (
        last["volume"] > avg_vol * 2.2 and
        last["close"] > last["open"] and
        body / candle_range > 0.55 and
        last["close"] > ema20
    )


def volume_trend_expansion(candles_df):
    """📊 Volume Trend Expansion Check (V7)
    
    ตรวจสอบว่าปริมาณ 5 แท่งล่าสุดสูงกว่า 10 แท่งก่อนหน้า >= 1.2x
    """
    if len(candles_df) < 15:
        return False
    
    volumes = candles_df["volume"].values
    recent_vol = np.mean(volumes[-5:])
    old_vol = np.mean(volumes[-15:-5])
    
    return recent_vol > old_vol * 1.2


def price_efficiency_check(candles_df):
    """💯 Price Efficiency Ratio (V7)
    
    ตรวจสอบประสิทธิภาพของราคา = Body Sum / Range Sum
    ค่าต่ำ (< 0.3) = fake pump/wick candles
    """
    if len(candles_df) < 5:
        return 1.0
    
    recent = candles_df.iloc[-5:]
    body_sum = 0
    range_sum = 0.0001  # Avoid division by zero
    
    for _, candle in recent.iterrows():
        body_sum += abs(candle["close"] - candle["open"])
        range_sum += (candle["high"] - candle["low"])
    
    return body_sum / range_sum


def trend_strength_check(candles_df):
    """📈 Trend Strength Filter (V7)
    
    ตรวจสอบ EMA Alignment: EMA9 > EMA21 > EMA50
    """
    if len(candles_df) < 50:
        return False
    
    close_prices = candles_df["close"].values
    
    ema9 = calculate_ema(list(close_prices), 9)
    ema21 = calculate_ema(list(close_prices), 21)
    ema50 = calculate_ema(list(close_prices), 50)
    
    return ema9 > ema21 and ema21 > ema50


# ========================================================================== #

class TradingStrategy:
    """คลาสหลักของกลยุทธ์การเทรด"""

    def generate_signal(self, df: pd.DataFrame) -> str:
        """ส่งสัญญาณกลับมาเป็น 'BUY', 'SELL' หรือ 'HOLD'"""
        raise NotImplementedError("ต้องทำการสืบทอดและเขียนเมทอดนี้ในคลาสลูก")


class AdvancedStrategy(TradingStrategy):
    """กลยุทธ์ขั้นสูงรวม EMA Trend, RSI Filter, MACD, Volume Filter และ ADX"""

    def generate_signal(self, df: pd.DataFrame) -> str:
        """ประเมินเพื่อหาทิศทางเบื้องต้น (ใช้ EMA Crossover / Trend เป็นตัววัดหลัก)

        หมายเหตุ: ระบบเทรดจริงจะนำ indicators ไปคิดคะแนนในระบบ AI Scoring (ระดับ 4) อีกครั้ง
        """
        if len(df) < 30:
            return "HOLD"

        df = calculate_indicators(df)

        row_now = df.iloc[-1]
        row_prev = df.iloc[-2]

        ema_fast_now = row_now["ema_fast"]
        ema_slow_now = row_now["ema_slow"]
        ema_fast_prev = row_prev["ema_fast"]
        ema_slow_prev = row_prev["ema_slow"]

        # คืนสัญญาณซื้อขายตามพื้นฐาน EMA Trend/Cross
        if ema_fast_prev <= ema_slow_prev and ema_fast_now > ema_slow_now:
            return "BUY"
        elif ema_fast_prev >= ema_slow_prev and ema_fast_now < ema_slow_now:
            return "SELL"

        return "HOLD"


class SMAStrategy(TradingStrategy):
    """กลยุทธ์เส้นค่าเฉลี่ยเคลื่อนที่ตัดกัน (SMA Crossover) - เก็บไว้สำรอง"""

    def __init__(self, fast_period: int = 5, slow_period: int = 20):
        self.fast_period = fast_period
        self.slow_period = slow_period

    def generate_signal(self, df: pd.DataFrame) -> str:
        if len(df) < self.slow_period + 2:
            return "HOLD"

        df = df.copy()
        df["sma_fast"] = df["close"].rolling(window=self.fast_period).mean()
        df["sma_slow"] = df["close"].rolling(window=self.slow_period).mean()

        fast_now = df["sma_fast"].iloc[-1]
        slow_now = df["sma_slow"].iloc[-1]
        fast_prev = df["sma_fast"].iloc[-2]
        slow_prev = df["sma_slow"].iloc[-2]

        if fast_prev <= slow_prev and fast_now > slow_now:
            return "BUY"
        elif fast_prev >= slow_prev and fast_now < slow_now:
            return "SELL"

        return "HOLD"


class RSIStrategy(TradingStrategy):
    """กลยุทธ์ RSI เกินขอบเขต (Oversold / Overbought) - เก็บไว้สำรอง"""

    def __init__(self, period: int = 14, overbought: float = 70.0, oversold: float = 30.0):
        self.period = period
        self.overbought = overbought
        self.oversold = oversold

    def _calculate_rsi(self, df: pd.DataFrame) -> pd.Series:
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=self.period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=self.period - 1, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50.0)

    def generate_signal(self, df: pd.DataFrame) -> str:
        if len(df) < self.period + 2:
            return "HOLD"

        df = df.copy()
        df["rsi"] = self._calculate_rsi(df)

        rsi_now = df["rsi"].iloc[-1]

        if rsi_now <= self.oversold:
            return "BUY"
        elif rsi_now >= self.overbought:
            return "SELL"

        return "HOLD"


def calculate_score(client, symbol: str, df_15m: pd.DataFrame) -> tuple:
    """🎯 Enhanced AI Scoring with V7 Advanced Filters (V7 + Original)
    
    System computes score out of 100 using multiple technical indicators:
    - EMA Trend Strength (25 pts)
    - MACD Acceleration (20 pts)
    - RSI Filter (15 pts)
    - ADX Trend Confirmation (15 pts)
    - Volume Expansion (15 pts)
    - Order Book Depth (10 pts)
    - ATR Volatility Filter (5 pts)
    - Breakout Confirmation (V7) [12 pts bonus]
    - Whale Accumulation (V7) [18 pts bonus]
    - Volume Trend Expansion (V7) [8 pts bonus]
    """
    score = 0
    if len(df_15m) < 2:
        return 0, 0.0, 0.0

    # Apply V7 Pre-filters (return early if failed)
    if not exhaustion_filter(df_15m):
        return 0, 0.0, 0.0  # Over-extended from EMA20
    
    if not trend_strength_check(df_15m):
        return 0, 0.0, 0.0  # No uptrend alignment

    row_now = df_15m.iloc[-1]
    row_prev = df_15m.iloc[-2]

    close_val = row_now["close"]
    ema_fast = row_now["ema_fast"]
    ema_slow = row_now["ema_slow"]
    ema_50 = row_now["ema_50"]
    rsi = row_now["rsi"]
    rsi_prev = row_prev["rsi"]
    macd = row_now["macd"]
    macd_signal = row_now["macd_signal"]
    macd_hist = row_now["macd_hist"]
    adx = row_now["adx"]
    plus_di = row_now["plus_di"]
    minus_di = row_now["minus_di"]
    volume = row_now["volume"]
    volume_ma = row_now["volume_ma"]
    atr = row_now["atr"]

    # คำนวณ rolling mean ของ ATR
    atr_ma = df_15m["atr"].rolling(20).mean().iloc[-1]

    # A. สัญญาณ EMA Trend (EMA9 > EMA21) [25 คะแนน]
    if ema_fast > ema_slow and close_val > ema_50:
        score += 25
    elif ema_fast > ema_slow:
        score += 15

    # B. สัญญาณ MACD (MACD > Signal และ Histogram > 0) [20 คะแนน]
    if macd > macd_signal and macd_hist > 0:
        score += 20
    elif macd > macd_signal:
        score += 10

    # C. สัญญาณ RSI Filter (RSI > 50 และต่ำกว่า 65 + กำลังเป็นขาขึ้น) [15 คะแนน]
    if rsi > 50.0 and rsi < 65.0 and rsi > rsi_prev:
        score += 15
    elif rsi > 50.0 and rsi < 65.0:
        score += 10

    # D. สัญญาณ ADX Filter (ADX > 18 และไม่ใช่แนวโน้มขาลงที่ชัดเจน) [15 คะแนน]
    downtrend_strong = (adx > 22.0) and (minus_di > plus_di)
    if not downtrend_strong and adx > 18.0:
        score += 15

    # E. สัญญาณ Volume Filter (Volume แท่งล่าสุด > Volume MA 20 แท่ง * 1.2 หรือ 1.1) [15 คะแนน]
    if volume > volume_ma * 1.2:
        score += 15
    elif volume > volume_ma * 1.1:
        score += 10

    # F. สัญญาณตรวจสอบระดับความแน่นของ Order Book (Bid Volume > Ask Volume) [10 คะแนน]
    depth_score = 0
    bid_vol, ask_vol = 0.0, 0.0
    try:
        depth = client.get_depth(symbol, limit=10)
        bids = depth.get("bids", [])
        asks = depth.get("asks", [])
        if bids and asks:
            def _sf(v):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return 0.0
            bid_vol = sum(_sf(b[1]) for b in bids)
            ask_vol = sum(_sf(a[1]) for a in asks)
            if bid_vol > ask_vol:
                ratio = bid_vol / ask_vol
                if ratio > 1.5:
                    depth_score = 10
                else:
                    depth_score = 7
    except Exception as e:
        print(f"[Warning] Failed to fetch depth in calculate_score for {symbol}: {e}")
    score += depth_score

    # G. สัญญาณความผันผวน ATR Volatility Filter (ATR > ATR MA 20 แท่ง) [5 คะแนน]
    if atr > atr_ma:
        score += 5
    else:
        score += 2

    # ⭐️ V7 BONUS FILTERS (Add to score, not replace)
    
    # V7: Breakout Confirmation Bonus [+12 pts]
    if breakout_confirmation(df_15m):
        score += 12

    # V7: Whale Accumulation Detection [+18 pts]
    if detect_whale_v3(df_15m):
        score += 18

    # V7: Volume Expansion Trend [+8 pts]
    if volume_trend_expansion(df_15m):
        score += 8

    # V7: Fake Pump Prevention (Price Efficiency)
    if price_efficiency_check(df_15m) < 0.3:
        score -= 15

    # Apply scaling factor to ensure total max score is 100 (adjusted for V7 bonuses)
    # Original max before bonuses was ~105, now with V7 can go to ~161, so scale down
    scaling_factor = 100 / 161.0  # ≈0.62
    score = int(score * scaling_factor)
    
    return score, bid_vol, ask_vol
