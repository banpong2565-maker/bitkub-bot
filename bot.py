import sys
import numpy as np
import pandas as pd
import time
import traceback

# Cache for BTC market crash detection (valid for 5 minutes)
_btc_market_crash_cache = None
_btc_market_crash_timestamp = 0
from datetime import datetime, timezone, timedelta
import json
import requests
import os

# Helper to safely format floats in logs – returns 'N/A' for None values
def fmt(value, precision=2):
    """Safe formatting handling None values."""
    try:
        return f"{value:.{precision}f}" if value is not None else "N/A"
    except Exception:
        return "N/A"

# Helper for unified BUY rejection logging
def log_buy_rejection(symbol, reason, spread=None, volume_ratio=None, adx=None, net_edge=None, score=None, expected=None, fee=None):
    parts = [f"[{symbol.upper()}] REJECT BUY: {reason}"]
    if spread is not None:
        parts.append(f"Spread={fmt(spread*100,2)}%")
    if volume_ratio is not None:
        parts.append(f"VolRatio={fmt(volume_ratio,2)}")
    if adx is not None:
        parts.append(f"ADX={fmt(adx,1)}")
    if net_edge is not None:
        parts.append(f"NetEdge={fmt(net_edge*100,2)}%")
    if score is not None:
        parts.append(f"Score={score}")
    if expected is not None:
        parts.append(f"ExpMove={fmt(expected*100,2)}%")
    if fee is not None:
        parts.append(f"Fee={fmt(fee*100,2)}%")
    print(" | ".join(parts))

import logging
import config
import bot_utils
from bitkub_client import BitkubClient
from strategy import (
    AdvancedStrategy,
    RSIStrategy,
    SMAStrategy,
    parse_candles_to_dataframe,
    calculate_indicators,
    calculate_score,
)
from telegram_notifier import TelegramNotifier
import ai_last_chance_scanner
import trade_logger

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


paper_thb = 10000.0
paper_balances = {}
position_state = {}
last_trade_at = {}
daily_start_value = 0.0
last_daily_reset_day = None
consecutive_losses = 0
last_buy_date = None
last_chance_scan_minute = None

STATE_FILE = "state.json"


def save_state():
    """บันทึกข้อมูลสถานะ position_state, consecutive_losses, last_buy_date, paper_thb และ paper_balances ลงไฟล์ state.json"""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "positions": position_state,
                "consecutive_losses": consecutive_losses,
                "last_buy_date": last_buy_date,
                "paper_thb": paper_thb,
                "paper_balances": paper_balances
            }, f, indent=4)
    except Exception as e:
        print(f"[⚠️ State] บันทึกสถานะพอร์ตล้มเหลว: {e}")


def load_state():
    """โหลดข้อมูลสถานะกลับคืนจากไฟล์ state.json"""
    global position_state, consecutive_losses, last_buy_date, paper_thb, paper_balances
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                position_state = data.get("positions", {})
                consecutive_losses = data.get("consecutive_losses", 0)
                last_buy_date = data.get("last_buy_date", None)
                paper_thb = data.get("paper_thb", 10000.0)
                paper_balances = data.get("paper_balances", {})
            else:
                position_state = {}
                consecutive_losses = 0
                last_buy_date = None
                paper_thb = 10000.0
                paper_balances = {}
            print(f"[💾 State] โหลดประวัติพอร์ตสำหรับ {len(position_state)} เหรียญสำเร็จ (แพ้ติดกัน: {consecutive_losses} ครั้ง, ซื้อล่าสุด: {last_buy_date}, เงินสดจำลอง: {paper_thb:,.2f} THB)")
        except Exception as e:
            print(f"[⚠️ State] โหลดสถานะพอร์ตล้มเหลว: {e}. เริ่มต้นด้วยค่าว่างเปล่า.")
            position_state = {}
            consecutive_losses = 0
            last_buy_date = None
            paper_thb = 10000.0
            paper_balances = {}
    else:
        position_state = {}
        consecutive_losses = 0
        last_buy_date = None
        paper_thb = 10000.0
        paper_balances = {}


def get_server_day_and_time(client):
    try:
        server_ts = client.get_server_time()
        server_dt = datetime.fromtimestamp(server_ts / 1000.0, tz=timezone(timedelta(hours=7)))
        return server_dt.date().isoformat(), server_dt
    except Exception:
        now = datetime.now()
        return now.date().isoformat(), now


def make_strategy():
    if config.STRATEGY in ("sma_crossover", "ema_macd_adx"):
        return AdvancedStrategy()
    if config.STRATEGY == "rsi":
        return RSIStrategy(period=14, overbought=70, oversold=30)
    raise ValueError(f"Unknown strategy: {config.STRATEGY}")


def ticker_key_to_symbol(ticker_key: str) -> str:
    parts = ticker_key.upper().split("_")
    if len(parts) == 2 and parts[0] == "THB":
        return f"{parts[1]}_THB".lower()
    return ticker_key.lower()


def symbol_to_ticker_key(symbol: str) -> str:
    parts = symbol.upper().split("_")
    if len(parts) == 2:
        return f"{parts[1]}_{parts[0]}"
    return symbol.upper()


def base_coin(symbol: str) -> str:
    return symbol.split("_")[0].upper()


def safe_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_ticker_volume_thb(ticker: dict) -> float:
    return max(
        safe_float(ticker.get("quoteVolume")),
        safe_float(ticker.get("baseVolume")),
        safe_float(ticker.get("volume")),
    )


def get_spread_rate(ticker: dict) -> float:
    bid = safe_float(
        ticker.get("highestBid"),
        safe_float(ticker.get("bid"), safe_float(ticker.get("buy"))),
    )
    ask = safe_float(
        ticker.get("lowestAsk"),
        safe_float(ticker.get("ask"), safe_float(ticker.get("sell"))),
    )
    mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
    if mid <= 0:
        return 0.0
    return max((ask - bid) / mid, 0.0)


def is_trade_cooling_down(symbol: str) -> bool:
    traded_at = last_trade_at.get(symbol)
    if traded_at is None:
        return False
    return (time.time() - traded_at) < config.TRADE_COOLDOWN_SECONDS


def mark_traded(symbol: str):
    last_trade_at[symbol] = time.time()


def get_scan_symbols(client: BitkubClient, tickers: dict) -> list:
    if config.SCAN_SYMBOLS:
        return config.SCAN_SYMBOLS[: config.MAX_SCAN_SYMBOLS]

    if not tickers:
        tickers = client.get_all_tickers()

    thb_pairs = []
    for ticker_key, ticker in tickers.items():
        if not ticker_key.upper().startswith("THB_") or not isinstance(ticker, dict):
            continue
        volume = get_ticker_volume_thb(ticker)
        thb_pairs.append((volume, ticker_key_to_symbol(ticker_key)))

    thb_pairs.sort(reverse=True)
    symbols = [symbol for _, symbol in thb_pairs]
    return symbols[: config.MAX_SCAN_SYMBOLS] or [config.SYMBOL]


def estimate_trade_edge(df, signal: str, spread_rate: float = 0.0) -> dict:
    lookback = min(config.PROFIT_LOOKBACK_CANDLES, len(df) - 1)
    if lookback <= 0:
        return {"expected_rate": 0.0, "required_rate": 0.0, "net_rate": -1.0}

    close_now = safe_float(df["close"].iloc[-1])
    close_prev = safe_float(df["close"].iloc[-lookback - 1])
    if close_now <= 0 or close_prev <= 0:
        return {"expected_rate": 0.0, "required_rate": 0.0, "net_rate": -1.0}

    recent_return = (close_now / close_prev) - 1
    avg_range = ((df["high"] - df["low"]) / df["close"]).tail(lookback).mean()
    avg_range = safe_float(avg_range)

    directional_move = recent_return if signal == "BUY" else -recent_return
    expected_rate = max(directional_move, avg_range * 0.5)
    required_rate = (config.TRADING_FEE_RATE * 2) + spread_rate + config.MIN_EXPECTED_PROFIT_RATE
    return {
        "expected_rate": expected_rate,
        "required_rate": required_rate,
        "net_rate": expected_rate - required_rate,
    }


def calculate_position_size(thb_balance: float, total_value: float) -> float:
    # Calculate 80% of available THB balance with safety limits
    calculated = thb_balance * 0.80
    trade_amount = max(100.0, min(calculated, 5000.0))
    # Ensure we do not exceed the actual balance
    trade_amount = min(trade_amount, thb_balance)
    return trade_amount


def format_rate(rate: float) -> str:
    return f"{rate * 100:.2f}%"


def get_balances(client: BitkubClient, current_prices: dict) -> tuple:
    if config.DRY_RUN:
        total_value = paper_thb
        for coin, amount in paper_balances.items():
            total_value += amount * current_prices.get(coin, 0.0)
        return paper_thb, dict(paper_balances), total_value

    balances_res = client.get_balances()
    if balances_res.get("error") != 0:
        raise RuntimeError(balances_res.get("message") or balances_res.get("error"))

    result = balances_res.get("result", {})
    thb_balance = safe_float(result.get("THB", {}).get("available"))
    asset_balances = {}
    total_value = thb_balance

    for coin, balance in result.items():
        coin = coin.upper()
        if coin == "THB":
            continue
        amount = safe_float(balance.get("available") if isinstance(balance, dict) else balance)
        if amount <= 0:
            continue
        asset_balances[coin] = amount
        total_value += amount * current_prices.get(coin, 0.0)

    return thb_balance, asset_balances, total_value


def sync_position_state(client: BitkubClient, asset_balances: dict, current_prices: dict):
    updated = False
    for coin, amount in asset_balances.items():
        if amount <= 0:
            has_open_orders = False
            if not config.DRY_RUN:
                try:
                    symbol = f"{coin}_thb"
                    if get_open_order_count(client, symbol) > 0:
                        has_open_orders = True
                except Exception as e:
                    print(f"[Warning] Failed to check open orders for {coin} in sync_position_state: {e}")
            if not has_open_orders:
                if coin in position_state:
                    position_state.pop(coin)
                    updated = True
                continue

        price = current_prices.get(coin, 0.0)
        if coin not in position_state:
            # ดักกรณีรีบัสหรือมีการซื้อเหรียญโดยบอทอื่น/นอกระบบ
            position_state[coin] = {
                "entry_price": price,
                "highest_price": price,
                "stop_loss_price": price * (1 - config.STOP_LOSS_RATE),
                "take_profit_price": price * (1 + config.TAKE_PROFIT_RATE),
                "atr_at_entry": 0.0,
                "initial_amount": amount,
                "tp1_sold": False,
                "tp2_sold": False,
                "mode": "normal",
                "cooldown_until": 0,
                "opened_at": datetime.now().isoformat(timespec="seconds"),
                "daily_fee_total": 0.0,
                "daily_profit_total": 0.0,
            }
            updated = True
        else:
            state = position_state[coin]
            if price > state.get("highest_price", 0.0):
                state["highest_price"] = price
                updated = True

    for coin in list(position_state):
        if coin not in asset_balances or asset_balances.get(coin, 0.0) <= 0:
            has_open_orders = False
            if not config.DRY_RUN:
                try:
                    symbol = f"{coin}_thb"
                    if get_open_order_count(client, symbol) > 0:
                        has_open_orders = True
                except Exception as e:
                    print(f"[Warning] Failed to check open orders for {coin} in sync_position_state: {e}")
            if not has_open_orders:
                position_state.pop(coin, None)
                updated = True

    if updated:
        save_state()


def build_exit_candidates(client: BitkubClient, asset_balances: dict, current_prices: dict, df_15m_by_coin: dict) -> list:
    exit_candidates = []
    for coin, amount in asset_balances.items():
        price = current_prices.get(coin, 0.0)
        if amount <= 0 or price <= 0:
            continue

        state = position_state.get(coin, {})
        entry_price = state.get("entry_price") or price
        highest_price = max(state.get("highest_price", price), price)
        pnl_rate = (price / entry_price) - 1 if entry_price > 0 else 0.0
        drawdown_from_high = (price / highest_price) - 1 if highest_price > 0 else 0.0
        # กำไรสุทธิจริงหลังหักค่าธรรมเนียมซื้อ+ขาย
        net_pnl_rate = pnl_rate - (config.TRADING_FEE_RATE * 2)

        atr_at_entry = state.get("atr_at_entry", 0.0)
        tp1_sold = state.get("tp1_sold", False)
        tp2_sold = state.get("tp2_sold", False)
        initial_amount = state.get("initial_amount") or amount

        # Stop Loss using ATR multiplier (primary) and hard‑stop safety
        sl_price = state.get("stop_loss_price")
        if sl_price is None:
            # Calculate ATR‑based stop‑loss if ATR available
            if atr_at_entry > 0:
                atr_sl_price = entry_price - (atr_at_entry * config.ATR_STOP_MULTIPLIER)
                pct_sl_price = entry_price * (1 - config.STOP_LOSS_RATE)
                # [FIX] ป้องกัน SL แคบเกินไป เลือกจุดที่ห่างจากราคาซื้อมากกว่า
                sl_price = min(atr_sl_price, pct_sl_price)
            else:
                sl_price = entry_price * (1 - config.STOP_LOSS_RATE)
        # Hard‑stop safety price (flash crash protection)
        hard_stop_price = entry_price * (1 - config.HARD_STOP_EXTRA_DROP_RATE)

        reason = None
        sell_reasons = []  # [NEW] รายการเหตุผลสำหรับ logging
        amount_to_sell = amount
        is_partial = False

        # ======================================================
        # 1. PANIC SELL — ขายทันที: Volume spike + Price crash
        # ======================================================
        if config.PANIC_SELL_ENABLED and coin in df_15m_by_coin:
            df_panic = df_15m_by_coin[coin]
            if len(df_panic) >= 2:
                row_now = df_panic.iloc[-1]
                volume = safe_float(row_now.get("volume", 0))
                volume_ma = safe_float(row_now.get("volume_ma", 0))
                candle_drop = 0.0
                if safe_float(row_now.get("open", 0)) > 0:
                    candle_drop = (safe_float(row_now.get("close", 0)) / safe_float(row_now.get("open", 0))) - 1

                volume_spike = (volume_ma > 0 and volume > volume_ma * config.PANIC_SELL_VOLUME_SPIKE)
                price_crash = (candle_drop <= -config.PANIC_SELL_PRICE_DROP)

                if volume_spike and price_crash:
                    reason = "panic sell (volume spike + price crash)"
                    sell_reasons = ["🚨 Volume spike", f"📉 Candle drop {candle_drop*100:.2f}%"]
                    amount_to_sell = amount
                    is_partial = False

        # ======================================================
        # 2. STOP LOSS (Full Exit) — ขายทันที ไม่มีเงื่อนไขอื่น
        # ======================================================
        if not reason and (price <= sl_price or price <= hard_stop_price):
            # Determine which stop triggered for logging
            if price <= sl_price:
                reason = "stop loss"
                sell_reasons = [f"🛑 StopLoss @ {sl_price:.4f} (ปัจจุบัน {price:.4f})"]
            else:
                reason = "hard stop"
                sell_reasons = [f"🛑 HardStop @ {hard_stop_price:.4f} (ปัจจุบัน {price:.4f})"]
            amount_to_sell = amount
            is_partial = False

        # ======================================================
        # 3. TRAILING STOP (Full Exit)
        # [FIX] เปิดใช้งานเฉพาะเมื่อ net_pnl_rate (หลังหักค่าธรรมเนียม) >= TRAILING_MIN_PROFIT_RATE
        # หากขาดทุนอยู่ ห้ามใช้ Trailing Stop — ใช้ Stop Loss เพียงอย่างเดียว
        # ======================================================
        if not reason and net_pnl_rate >= config.TRAILING_MIN_PROFIT_RATE:
            if drawdown_from_high <= -config.TRAILING_STOP_RATE:
                reason = "trailing stop"
                sell_reasons = [
                    f"📉 Trailing: drawdown {drawdown_from_high*100:.2f}% จาก high {highest_price:.4f}",
                    f"✅ Net profit ณ เวลานั้น: {net_pnl_rate*100:.2f}%",
                ]
                amount_to_sell = amount
                is_partial = False

        # ======================================================
        # 4. DYNAMIC TAKE PROFITS — ATR-based (TP1 30%, TP2 30%, TP3 Full)
        # ======================================================
        if not reason and atr_at_entry > 0:
            tp1_target = entry_price + (atr_at_entry * 1.0)
            tp2_target = entry_price + (atr_at_entry * 2.0)
            tp3_target = entry_price + (atr_at_entry * 3.0)

            if price >= tp3_target:
                reason = "take profit (TP3)"
                sell_reasons = [f"🎯 TP3 @ {tp3_target:.4f} (ATR x3)"]
                amount_to_sell = amount
                is_partial = False
            elif price >= tp2_target and not tp2_sold:
                reason = "partial take profit (TP2)"
                sell_reasons = [f"🎯 TP2 @ {tp2_target:.4f} (ATR x2)"]
                amount_to_sell = bot_utils.get_capped_sell_amount(initial_amount, amount, 0.3)
                is_partial = True
            elif price >= tp1_target and not tp1_sold:
                reason = "partial take profit (TP1)"
                sell_reasons = [f"🎯 TP1 @ {tp1_target:.4f} (ATR x1)"]
                amount_to_sell = bot_utils.get_capped_sell_amount(initial_amount, amount, 0.3)
                is_partial = True

        # ======================================================
        # 5. FALLBACK PERCENTAGE-BASED TAKE PROFIT (ไม่มี ATR)
        # ======================================================
        if not reason and atr_at_entry <= 0:
            if pnl_rate >= config.TAKE_PROFIT_RATE:
                reason = "take profit (percentage)"
                sell_reasons = [f"🎯 TakeProfit {pnl_rate*100:.2f}% >= {config.TAKE_PROFIT_RATE*100:.1f}%"]
                amount_to_sell = amount
                is_partial = False

        # ======================================================
        # 6. TECHNICAL EXIT — ต้องมีอย่างน้อย SELL_CONFIRM_MIN_CONDITIONS ใน 3
        # [FIX] เปลี่ยนจาก OR → ต้องการ >= N สัญญาณพร้อมกัน
        # ======================================================
        if not reason:
            held_minutes = _position_held_minutes(coin)
            # [FIX] บังคับ MIN_HOLD_MINUTES ก่อนขายด้วย technical signal
            if held_minutes is None or held_minutes >= config.MIN_HOLD_MINUTES:
                if coin not in df_15m_by_coin:
                    try:
                        symbol = f"{coin}_thb"
                        candles_15m = client.get_candles(symbol, resolution="15", limit=100)
                        df_15m = parse_candles_to_dataframe(candles_15m)
                        if not df_15m.empty and len(df_15m) >= 30:
                            df_15m = calculate_indicators(df_15m)
                            df_15m_by_coin[coin] = df_15m
                    except Exception as e:
                        print(f"[⚠️ Warning] Could not fetch/calculate indicators for held coin {coin}: {e}")

                if coin in df_15m_by_coin:
                    df_15m = df_15m_by_coin[coin]
                    if len(df_15m) >= 2:
                        row_now = df_15m.iloc[-1]
                        row_prev = df_15m.iloc[-2]

                        # คำนวณสัญญาณทั้ง 3 ตัว
                        ema_exit = (row_prev["ema_fast"] >= row_prev["ema_slow"]) and (row_now["ema_fast"] < row_now["ema_slow"])
                        macd_exit = (row_prev["macd"] >= row_prev["macd_signal"]) and (row_now["macd"] < row_now["macd_signal"])
                        rsi_exit = row_now["rsi"] < config.SELL_RSI_THRESHOLD

                        # [NEW] Sideway Filter — ถ้า ADX ต่ำ ห้ามขายด้วย technical signal
                        adx_val = safe_float(row_now.get("adx", 25))
                        sideway_market = config.SIDEWAY_FILTER_ENABLED and adx_val < config.ADX_SIDEWAY_THRESHOLD

                        # [NEW] Volume Filter — ถ้า volume ต่ำกว่าค่าเฉลี่ย ลด confidence ของสัญญาณ
                        volume = safe_float(row_now.get("volume", 0))
                        volume_ma = safe_float(row_now.get("volume_ma", 1))
                        low_volume = config.VOLUME_FILTER_ENABLED and volume_ma > 0 and volume < volume_ma * config.MIN_VOLUME_RATIO

                        # นับจำนวนสัญญาณที่เป็นจริง
                        sell_signal_count = sum([ema_exit, macd_exit, rsi_exit])
                        active_reasons = []
                        if ema_exit:
                            active_reasons.append("📉 EMA Cross Down")
                        if macd_exit:
                            active_reasons.append("📉 MACD Cross Down")
                        if rsi_exit:
                            active_reasons.append(f"📉 RSI={row_now['rsi']:.1f} < {config.SELL_RSI_THRESHOLD}")

                        if sideway_market:
                            print(f"[{coin}] Skip technical sell: ADX={adx_val:.1f} < {config.ADX_SIDEWAY_THRESHOLD} (sideways market)")
                        elif low_volume:
                            print(f"[{coin}] Skip technical sell: Volume ต่ำ (Volume={volume:.1f}, MA={volume_ma:.1f}, ratio={volume/volume_ma:.2f})")
                        elif sell_signal_count >= config.SELL_CONFIRM_MIN_CONDITIONS:
                            # [FIX] ต้องมีสัญญาณ >= N ใน 3 ถึงจะขาย
                            reason = f"technical exit ({', '.join(active_reasons)})"
                            sell_reasons = active_reasons
                            amount_to_sell = amount
                            is_partial = False
                        else:
                            print(f"[{coin}] HOLD: สัญญาณ technical ไม่พอ ({sell_signal_count}/{config.SELL_CONFIRM_MIN_CONDITIONS} required): {active_reasons}")

        # ======================================================
        # 6. TIME-BASED EXIT — หากถือครองเกิน TIME_EXIT_MAX_HOLD_HOURS และมีกำไรตามเงื่อนไข
        if not reason and check_time_exit(coin, price):
            reason = "time exit"
            sell_reasons = ["⏰ Time-based exit"]
            amount_to_sell = amount
            is_partial = False
        # 7. LOG เหตุผลการขายอย่างละเอียด
        # ======================================================
        if reason:
            symbol = f"{coin}_thb"
            amount_to_sell = min(amount_to_sell, amount)
            value_thb = amount_to_sell * price
            expected_rate = max(pnl_rate, 0.0)
            required_rate = config.TRADING_FEE_RATE * 2
            net_rate = pnl_rate - required_rate

            # บังคับ Full Exit หากขนาด Partial ต่ำกว่าขั้นต่ำ 50 บาท
            if value_thb < config.MIN_TRADE_VALUE_THB and is_partial:
                reason = "take profit (upgrade from partial due to size)"
                sell_reasons.append("⚠️ Partial size < minimum → Full exit")
                amount_to_sell = amount
                value_thb = amount * price
                is_partial = False

            # [FIX] ห้ามขายถ้ากำไรสุทธิติดลบ ยกเว้น Stop Loss, Trailing Stop, Panic Sell
            emergency_exits = ("stop loss", "trailing stop", "panic sell")
            is_emergency = any(e in reason for e in emergency_exits)
            if not is_emergency and net_rate < config.MIN_NET_PROFIT_RATE:
                print(
                    f"[{coin}] Skip exit [{reason}]: net_rate {net_rate*100:.2f}% < min net profit {config.MIN_NET_PROFIT_RATE*100:.2f}%"
                    f" | HOLD เพื่อรอกำไรมากขึ้น"
                )
                continue

            # [NEW] พิมพ์เหตุผลการขายอย่างละเอียด
            print(f"[{coin}] 🔔 SELL SIGNAL detected:")
            print(f"  ➡ Reason : {reason}")
            for r in sell_reasons:
                print(f"  ➡ Detail : {r}")
            print(f"  ➡ PnL    : {pnl_rate*100:.2f}% (net: {net_rate*100:.2f}%)")
            print(f"  ➡ Entry  : {entry_price:.4f} | Current: {price:.4f}")
            print(f"  ➡ Amount : {amount_to_sell:.8f} {coin} (value: {value_thb:,.2f} THB)")

            exit_candidates.append(
                {
                    "symbol": symbol,
                    "coin": coin,
                    "price": price,
                    "signal": "SELL",
                    "amount": amount_to_sell,
                    "value_thb": value_thb,
                    "expected_rate": expected_rate,
                    "required_rate": required_rate,
                    "net_rate": net_rate,
                    "reason": reason,
                    "sell_reasons": sell_reasons,
                    "is_partial": is_partial,
                }
            )

    exit_candidates.sort(key=lambda item: item["net_rate"])
    return exit_candidates


current_key_index = 0
gemini_key_available_at = {}


def call_gemini_api(prompt: str) -> str:
    global current_key_index
    keys = config.GEMINI_API_KEYS
    if not keys:
        print("[⚠️ Warning] No Gemini API keys found in config")
        return ""

    num_keys = len(keys)
    now = time.time()
    start_key_index = current_key_index
    for offset in range(num_keys):
        key_index = (start_key_index + offset) % num_keys
        if gemini_key_available_at.get(key_index, 0) > now:
            continue

        api_key = keys[key_index]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generation_config": {"response_mime_type": "application/json"},
        }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            if response.status_code == 200:
                res_data = response.json()
                try:
                    text = res_data["candidates"][0]["content"]["parts"][0]["text"]
                    current_key_index = (key_index + 1) % num_keys
                    return text
                except (KeyError, IndexError) as parse_err:
                    print(f"[⚠️ Gemini Parse Error] {parse_err}. Response data: {res_data}")
            if response.status_code in (429, 403):
                print(f"[🔄 Gemini] Key index {key_index} hit quota limit or error (HTTP {response.status_code}). Rotating...")
                gemini_key_available_at[key_index] = time.time() + 300  # Cooldown 5 mins
            else:
                print(f"[⚠️ Gemini Error] HTTP {response.status_code}: {response.text}")
        except Exception as e:
            print(f"[⚠️ Gemini Exception] {e}")
            gemini_key_available_at[key_index] = time.time() + 60

    return ""


def get_gemini_decision(df, standard_signal: str, symbol: str) -> tuple:
    if df.empty:
        return "HOLD", "Empty dataframe"

    recent_df = df.tail(15)
    candles_summary = []
    for idx, row in recent_df.iterrows():
        candles_summary.append(
            f"Time: {row['datetime']}, Close: {row['close']:.2f}, High: {row['high']:.2f}, Low: {row['low']:.2f}"
        )
    candles_text = "\n".join(candles_summary)

    latest_row = df.iloc[-1]
    rsi_val = latest_row.get("rsi", 50.0)
    ema_fast = latest_row.get("ema_fast", 0.0)
    ema_slow = latest_row.get("ema_slow", 0.0)

    prompt = f"""
You are a professional crypto trading assistant.
Analyze the following market data for {symbol.upper()}:

Price history (last 15 candles):
{candles_text}

Latest indicators:
- RSI (14): {rsi_val:.2f}
- EMA Fast (9): {ema_fast:.2f}
- EMA Slow (21): {ema_slow:.2f}
- Traditional Indicator Signal: {standard_signal}

Important Constraint:
- Every single trade incurs a 0.25% trading fee (0.50% total fee for a complete buy and sell roundtrip).
- You must factor in this 0.25% fee per transaction. Only confirm a "BUY" or "SELL" signal if the expected price movement has enough momentum/room to comfortably cover the 0.50% roundtrip fees and yield a net profit.

Based on this, make a trading decision. Choose exactly one of the following: "BUY", "SELL", or "HOLD".
- Only output "BUY" if the market is showing strong recovery or oversold conditions starting to reverse upward and has enough momentum to cover the transaction fees.
- Only output "SELL" if the market is showing overbought conditions or strong downward trend.
- Output "HOLD" if the trend is flat, uncertain, or indicators are neutral.

You MUST respond ONLY with a JSON object in this exact format:
{{
  "decision": "BUY" | "SELL" | "HOLD",
  "reason": "Brief explanation of the decision in Thai language"
}}
"""
    response_text = call_gemini_api(prompt)
    if not response_text:
        if config.GEMINI_SKIP_ON_FAIL:
            print("[🤖 Gemini] Gemini API ล้มเหลวและตั้งค่า GEMINI_SKIP_ON_FAIL=True. ข้ามสัญญาณการซื้อขายนี้เพื่อความปลอดภัย.")
            return "HOLD", "Gemini API failed and GEMINI_SKIP_ON_FAIL is enabled"
        return standard_signal, "Gemini API failed, using technical indicators fallback"

    try:
        data = json.loads(response_text)
        decision = data.get("decision", "HOLD").upper()
        reason = data.get("reason", "No reason provided")
        if decision in ("BUY", "SELL", "HOLD"):
            return decision, reason
    except Exception as e:
        print(f"[⚠️ Gemini] Error parsing JSON response: {e}")

    return standard_signal, "Error parsing Gemini response, using technical indicators fallback"


def _position_held_minutes(coin: str) -> float:
    state = position_state.get(coin)
    if not state or not state.get("opened_at"):
        return None
    try:
        opened_at = datetime.fromisoformat(state["opened_at"])
        delta = datetime.now() - opened_at
        return delta.total_seconds() / 60.0
    except Exception as e:
        print(f"[Warning] Failed to parse opened_at for {coin}: {e}")
        return None

def check_time_exit(coin: str, price: float) -> bool:
    """Return True if position held longer than TIME_EXIT_MAX_HOLD_HOURS and net profit >= TIME_EXIT_MIN_PROFIT_RATE.
    Feature enabled only when config.TIME_EXIT_ENABLED is True.
    """
    if not config.TIME_EXIT_ENABLED:
        return False
    held_minutes = _position_held_minutes(coin)
    if held_minutes is None:
        return False
    held_hours = held_minutes / 60.0
    if held_hours < config.TIME_EXIT_MAX_HOLD_HOURS:
        return False
    state = position_state.get(coin, {})
    entry_price = state.get("entry_price")
    if not entry_price or entry_price <= 0:
        return False
    pnl_rate = (price / entry_price) - 1
    net_pnl_rate = pnl_rate - (config.TRADING_FEE_RATE * 2)
    return net_pnl_rate >= config.TIME_EXIT_MIN_PROFIT_RATE

# Helper to compute median ATR of last N candles
def median_atr(df, n=20):
    if "atr" not in df.columns:
        return 0.0
    recent = df["atr"].tail(n)
    if recent.empty:
        return 0.0
    return float(np.median(recent.values))

# Determine market condition
def is_trending(df):
    if "ema_fast" in df.columns and "ema_slow" in df.columns and "atr" in df.columns:
        ema_dist = abs(df["ema_fast"].iloc[-1] - df["ema_slow"].iloc[-1])
        atr = df["atr"].iloc[-1]
        adx = df.get("adx", pd.Series()).iloc[-1] if "adx" in df.columns else 0
        return ema_dist > atr and adx >= 25
    return False

def is_sideway(df):
    return df.get("adx", pd.Series()).iloc[-1] < config.ADX_SIDEWAY_THRESHOLD if "adx" in df.columns else False

# Reversal detection over consecutive bars
def check_reversal_confirmation(df, rsi_drop=config.REVERSAL_RSI_DROP, rsi_max=config.REVERSAL_RSI_MAX, bars=config.REVERSAL_CONFIRM_CONSECUTIVE_BARS):
    if len(df) < bars:
        return False
    for i in range(-bars, 0):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        ema_cross = prev["ema_fast"] >= prev["ema_slow"] and row["ema_fast"] < row["ema_slow"]
        macd_cross = prev["macd"] >= prev["macd_signal"] and row["macd"] < row["macd_signal"]
        rsi_drop_cond = (prev["rsi"] - row["rsi"]) >= rsi_drop and row["rsi"] <= rsi_max
        if sum([ema_cross, macd_cross, rsi_drop_cond]) >= 2:
            return True
    return False

# Apply recovery mode if loss but EMA200 rising
def apply_recovery_mode(state, df):
    if not df.empty and "ema_slow" in df.columns:
        if state.get("mode") != "recovery":
            # loss check
            entry = state.get("entry_price", 0)
            price = df["close"].iloc[-1]
            if price < entry:
                ema_now = df["ema_slow"].iloc[-1]
                ema_prev = df["ema_slow"].iloc[-2] if len(df) >= 2 else ema_now
                if ema_now > ema_prev:
                    state["mode"] = "recovery"
                    print(f"[{state.get('coin','')}] Entering recovery mode")

# Check and enforce cooldowns
def is_on_cooldown(state):
    cd = state.get("cooldown_until", 0)
    return cd and time.time() < cd


def is_btc_market_crashing(client) -> bool:
    """Detect if the BTC market is crashing.

    Conditions:
    1. ATR spike – current ATR > average of last 20 ATRs * config.BTC_REGIME_ATR_SPIKE_MULTIPLIER
    2. 24‑h price drop – absolute percent change > config.BTC_REGIME_PRICE_DROP_24H

    Result is cached for 5 minutes to avoid excessive API calls.
    """
    global _btc_market_crash_cache, _btc_market_crash_timestamp
    now = time.time()
    if _btc_market_crash_cache is not None and (now - _btc_market_crash_timestamp) < 5 * 60:
        return _btc_market_crash_cache
    try:
        candles = client.get_candles("BTC_THB", resolution="15", limit=100)
        df = parse_candles_to_dataframe(candles)
        if df.empty:
            result = False
        else:
            df = calculate_indicators(df)
            current_atr = df["atr"].iloc[-1]
            avg_atr_20 = df["atr"].iloc[-20:].mean()
            atr_spike = current_atr > avg_atr_20 * config.BTC_REGIME_ATR_SPIKE_MULTIPLIER
            ticker = client.get_ticker("BTC_THB")
            pc = ticker.get("percentChange") or ticker.get("percent_change") or 0
            try:
                price_drop = abs(float(pc)) / 100.0
            except Exception:
                price_drop = 0.0
            price_drop_cond = price_drop > config.BTC_REGIME_PRICE_DROP_24H
            result = atr_spike or price_drop_cond
    except Exception as e:
        print(f"[⚠️] Error checking BTC market crash: {e}")
        result = False
    _btc_market_crash_cache = result
    _btc_market_crash_timestamp = now
    return result



def scan_market(client: BitkubClient, strategy, tickers: dict, asset_balances: dict) -> tuple:
    symbols = get_scan_symbols(client, tickers)
    buy_candidates = []
    sell_candidates = []
    prices_by_coin = {}
    df_15m_by_coin = {}

    print(f"Scanning {len(symbols)} THB pairs...")
    for symbol in symbols:
        adx = 0.0  # กันปัญหา UnboundLocalError: ให้ adx มีค่าเริ่มต้นเสมอในทุก branch ของลูป
        ticker = tickers.get(symbol_to_ticker_key(symbol), {})
        if is_trade_cooling_down(symbol):
            continue

        volume_thb = get_ticker_volume_thb(ticker)
        spread_rate = get_spread_rate(ticker)  # Ensure spread_rate defined early
        if volume_thb and volume_thb < config.MIN_24H_VOLUME_THB:
            log_buy_rejection(symbol, "24h volume too low", spread=spread_rate, volume_ratio=None, adx=None, net_edge=None, score=None, expected=None, fee=None)
            continue

# spread_rate already obtained earlier
        if spread_rate > 0.02:
            log_buy_rejection(symbol, "spread > 2% hard limit", spread=spread_rate)
            continue
        if spread_rate > config.MAX_SPREAD_RATE:
            log_buy_rejection(symbol, "spread > MAX_SPREAD_RATE (1.5%)", spread=spread_rate)
            continue

        price = safe_float(ticker.get("last"))
        if price <= 0:
            ticker = client.get_ticker(symbol)
            price = safe_float(ticker.get("last"))
        if price <= 0:
            continue

        coin = base_coin(symbol)
        prices_by_coin[coin] = price

        # 1. โหลดข้อมูลหลายไทม์เฟรม (5m, 15m, 1h)
        candles_5m = client.get_candles(symbol, resolution="5", limit=100)
        candles_15m = client.get_candles(symbol, resolution="15", limit=100)
        candles_1h = client.get_candles(symbol, resolution="60", limit=100)

        df_5m = parse_candles_to_dataframe(candles_5m)
        df_15m = parse_candles_to_dataframe(candles_15m)
        df_1h = parse_candles_to_dataframe(candles_1h)

        if df_5m.empty or df_15m.empty or df_1h.empty:
            continue

        df_5m = calculate_indicators(df_5m)
        df_15m = calculate_indicators(df_15m)
        df_1h = calculate_indicators(df_1h)

        if len(df_5m) < 30 or len(df_15m) < 30 or len(df_1h) < 30:
            continue

        df_15m_by_coin[coin] = df_15m

        # เช็คสัญญาณขายสำหรับเหรียญที่ถืออยู่แล้ว
        if asset_balances.get(coin, 0.0) > 0:
            row_now = df_15m.iloc[-1]
            row_prev = df_15m.iloc[-2]
            ema_cross_down = (row_prev["ema_fast"] >= row_prev["ema_slow"]) and (row_now["ema_fast"] < row_now["ema_slow"])
            macd_cross_down = (row_prev["macd"] >= row_prev["macd_signal"]) and (row_now["macd"] < row_now["macd_signal"])
            # [FIX] เปลี่ยนเงื่อนไข RSI: อิง SELL_RSI_THRESHOLD (45) แทนเดิม overbought_turn
            rsi_cross_down = row_now["rsi"] < config.SELL_RSI_THRESHOLD

            # [NEW] Sideway Filter
            adx_val = safe_float(row_now.get("adx", 25))
            sideway_market = config.SIDEWAY_FILTER_ENABLED and adx_val < config.ADX_SIDEWAY_THRESHOLD

            # [NEW] Volume Filter
            vol_now = safe_float(row_now.get("volume", 0))
            vol_ma = safe_float(row_now.get("volume_ma", 1))
            low_volume = config.VOLUME_FILTER_ENABLED and vol_ma > 0 and vol_now < vol_ma * config.MIN_VOLUME_RATIO

            # [FIX] ต้องมี >= SELL_CONFIRM_MIN_CONDITIONS ใน 3 ถึงจะขาย (เดิม: OR condition สัญญาณใดสัญญาณหนึ่งก็ขาย)
            sell_signals = [ema_cross_down, macd_cross_down, rsi_cross_down]
            sell_signal_count = sum(sell_signals)
            active_reasons = []
            if ema_cross_down:
                active_reasons.append("📉 EMA Cross Down")
            if macd_cross_down:
                active_reasons.append("📉 MACD Cross Down")
            if rsi_cross_down:
                active_reasons.append(f"📉 RSI={row_now['rsi']:.1f}<{config.SELL_RSI_THRESHOLD}")

            if sell_signal_count >= config.SELL_CONFIRM_MIN_CONDITIONS:
                if sideway_market:
                    print(f"[{coin}] Skip scan_market sell: ADX={adx_val:.1f} < {config.ADX_SIDEWAY_THRESHOLD} (sideways)")
                elif low_volume:
                    print(f"[{coin}] Skip scan_market sell: Volume ต่ำ ({vol_now:.0f}/{vol_ma:.0f})")
                else:
                    # [FIX] ตรวจเวลาถือครองขั้นต่ำ
                    held_minutes = _position_held_minutes(coin)
                    if held_minutes is not None and held_minutes < config.MIN_HOLD_MINUTES:
                        print(f"[{coin}] Skip scan_market sell: held {held_minutes:.1f}m < min {config.MIN_HOLD_MINUTES}m")
                    else:
                        reason = f"technical sell ({', '.join(active_reasons)})"
                        amount = asset_balances[coin]
                        value_thb = amount * price

                        entry_price = position_state.get(coin, {}).get("entry_price") or price
                        pnl_rate = (price / entry_price) - 1 if entry_price > 0 else 0.0
                        required_rate = config.TRADING_FEE_RATE * 2
                        net_rate = pnl_rate - required_rate

                        if net_rate < config.MIN_NET_PROFIT_RATE:
                            print(f"[{coin}] Skip scan_market sell: net_rate {net_rate*100:.2f}% < min {config.MIN_NET_PROFIT_RATE*100:.2f}%")
                        else:
                            print(f"[{coin}] 🔔 SELL ({sell_signal_count}/3 signals): {active_reasons}")
                            sell_candidates.append({
                                "symbol": symbol, "coin": coin, "price": price, "signal": "SELL",
                                "amount": amount, "value_thb": value_thb,
                                "expected_rate": pnl_rate, "required_rate": required_rate,
                                "net_rate": net_rate, "reason": reason,
                                "sell_reasons": active_reasons, "is_partial": False,
                            })
            elif sell_signal_count > 0:
                print(f"[{coin}] HOLD in scan_market: สัญญาณไม่ครบ ({sell_signal_count}/{config.SELL_CONFIRM_MIN_CONDITIONS}): {active_reasons}")

        # 2. คัดกรองกรอบเวลาใหญ่ (EMA 50 เป็นตัวกรองแนวโน้มหลัก 1H และ 5m)
        ema_fast_1h = df_1h["ema_fast"].iloc[-1]
        ema_slow_1h = df_1h["ema_slow"].iloc[-1]
        ema_50_1h = df_1h["ema_50"].iloc[-1]
        close_1h = df_1h["close"].iloc[-1]

        is_downtrend_1h = (config.REQUIRE_1H_UPTREND and ema_fast_1h < ema_slow_1h) or (config.REQUIRE_PRICE_ABOVE_EMA50_1H and close_1h < ema_50_1h)
        if is_downtrend_1h:
            continue

        # [NEW] Sideway Filter — งดซื้อเมื่อ ADX < ADX_SIDEWAY_THRESHOLD (ตลาดอยู่ใน sideways)
        row_15m_latest = df_15m.iloc[-1]
        # Ensure ADX is available; if missing, skip symbol
        if "adx" not in row_15m_latest:
            log_buy_rejection(symbol, "ADX missing", spread=spread_rate, adx=None, net_edge=None, score=None, expected=None, fee=None)
            continue
        adx_15m = safe_float(row_15m_latest.get("adx", 25))
        adx = adx_15m  # Define adx for later use
        if config.SIDEWAY_FILTER_ENABLED and adx_15m < config.ADX_SIDEWAY_THRESHOLD:
            log_buy_rejection(symbol, "sideways market", spread=spread_rate, adx=adx_15m, net_edge=None, score=None, expected=None, fee=None)
            continue

        # [NEW] Volume Filter — Reject when volume ratio < config.MIN_VOLUME_RATIO
        vol_15m = safe_float(row_15m_latest.get("volume", 0))
        vol_ma_15m = safe_float(row_15m_latest.get("volume_ma", 1))
        volume_ratio = vol_15m / vol_ma_15m if vol_ma_15m > 0 else 0
        if config.VOLUME_FILTER_ENABLED and vol_ma_15m > 0 and volume_ratio < config.MIN_VOLUME_RATIO:
            log_buy_rejection(symbol, "low volume", spread=spread_rate, volume_ratio=volume_ratio, adx=adx_15m, net_edge=None, score=None, expected=None, fee=None)
            continue

        # 4. New entry scoring system (Score >= config.MIN_BUY_SCORE required)
        # Compute edge early to avoid UnboundLocalError
        edge = estimate_trade_edge(df_15m, "BUY", spread_rate)
        # Compute composite score based on defined criteria
        new_score = 0
        row_now = df_15m.iloc[-1]
        rsi = row_now["rsi"]
        volume = row_now["volume"]
        volume_ma = row_now["volume_ma"]
        atr = row_now["atr"]

        # EMA50 > EMA200 (+2)
        if row_now.get("ema_50", 0) > row_now.get("ema_200", 0):
            new_score += 2
        # ADX >= 18 (+2)
        if adx >= 18.0:
            new_score += 2
        # Volume ratio >= threshold (+1)
        if volume_ma > 0 and (volume / volume_ma) >= config.MIN_VOLUME_RATIO:
            new_score += 1
        # RSI within 45-70 (+1)
        if 45 <= rsi <= 70:
            new_score += 1
        # Net Edge >= MIN_NET_EDGE (+3)
        edge = estimate_trade_edge(df_15m, "BUY", spread_rate)
        if edge["net_rate"] >= config.MIN_NET_EDGE:
            new_score += 3
        print(f"[{symbol.upper()}] DEBUG: Pair:{symbol.upper()}, Price:{fmt(price,4)}, EMA50:{fmt(row_now.get('ema_50'))}, EMA200:{fmt(row_now.get('ema_200'))}, ADX:{fmt(adx)}, RSI:{fmt(rsi)}, Volume Ratio:{fmt((volume/volume_ma) if volume_ma>0 else 0)}, Expected Move:{fmt(edge['expected_rate']*100)}, Trading Fee:{fmt(config.TRADING_FEE_RATE*200)}, Net Edge:{fmt(edge['net_rate']*100) if edge.get('net_rate') is not None else 'N/A'}, Score:{new_score}, Decision: BUY")
        if new_score >= 7:
            if not config.ALLOW_ADD_TO_POSITION and asset_balances.get(coin, 0.0) > 0:
                log_buy_rejection(
                    symbol,
                    "existing position and add‑to‑position disabled",
                    spread=spread_rate,
                    net_edge=edge.get('net_rate'),
                    score=new_score,
                    expected=edge.get('expected_rate'),
                    fee=config.TRADING_FEE_RATE*2,
                )
                continue
            opportunity = {
                "symbol": symbol,
                "coin": coin,
                "price": price,
                "signal": "BUY",
                "spread_rate": spread_rate,
                "volume_thb": volume_thb,
                "atr": atr,
                "score": new_score,
                **edge,
            }



            # Re-estimate edge after initial checks
            edge = estimate_trade_edge(df_15m, "BUY", spread_rate)
            # Preserve the previously calculated score and update opportunity with new edge data
            opportunity.update(edge)

            if edge["net_rate"] <= 0:
                log_buy_rejection(
                    symbol,
                    "net_rate <= 0",
                    spread=spread_rate,
                    net_edge=edge.get('net_rate'),
                    score=new_score,
                    expected=edge.get('expected_rate'),
                    fee=config.TRADING_FEE_RATE * 2,
                )
                continue

            if config.GEMINI_ENABLED:
                print(f"[🤖 AI Filter] Requesting Gemini validation for {symbol.upper()}...")
                gemini_signal, reason = get_gemini_decision(df_15m, "BUY", symbol)
                print(f"[🤖 AI Response] Gemini decision: {gemini_signal}. Reason: {reason}")
                if gemini_signal != "BUY":
                    print(f"Skip {symbol.upper()} BUY: Not confirmed by Gemini")
                    continue
                opportunity["reason"] = f"Gemini: {reason}"

            buy_candidates.append(opportunity)

    buy_candidates.sort(key=lambda item: item["net_rate"], reverse=True)
    sell_candidates.sort(key=lambda item: item["net_rate"])
    return buy_candidates, sell_candidates, prices_by_coin, df_15m_by_coin


def notify_trade(notifier: TelegramNotifier, title: str, opportunity: dict, detail: str):
    message = (
        f"{title}\n"
        f"Pair: {opportunity['symbol'].upper()}\n"
        f"Signal: {opportunity['signal']}\n"
        f"Price: {opportunity['price']:,.8f} THB\n"
        f"Expected move: {format_rate(opportunity['expected_rate'])}\n"
        f"Required after fees: {format_rate(opportunity['required_rate'])}\n"
        f"Net edge: {format_rate(opportunity['net_rate'])}\n"
        f"Reason: {opportunity.get('reason', 'strategy signal')}\n"
        f"{detail}"
    )
    notifier.send(message)


def get_open_order_count(client, symbol: str) -> int:
    """Return the count of open orders for *symbol*.
    Any API error (including error 61) is treated as 0 open orders.
    The full API response is logged for debugging.
    """
    try:
        open_orders = client.get_open_orders(symbol)
        print(f"[Debug] Open orders response for {symbol}: {open_orders}")
        error = open_orders.get("error")
        if error not in (None, 0):
            # Bitkub returns error 61 for broker‑source coins – treat as no orders
            if error == 61:
                print(f"[Info] Symbol {symbol} is a broker-source coin — open order check skipped (error 61). Assuming 0 open orders.")
                return 0
            print(f"[Warning] Could not check open orders: {open_orders.get('message') or error}")
            return 0
        result = open_orders.get("result", [])
        if isinstance(result, list):
            return len(result)
        if isinstance(result, dict):
            orders = (
                result.get("orders")
                or result.get(symbol.lower())
                or result.get(symbol.upper())
                or []
            )
            if isinstance(orders, list):
                return len(orders)
            return len(orders) if orders else 0
        return 0
    except Exception:
        print("[Error] Exception while checking open orders:")
        traceback.print_exc()
        return 0


def execute_sell(client, notifier, opportunity):
    global paper_thb, paper_balances, consecutive_losses

    amount = opportunity["amount"]
    value_thb = opportunity["value_thb"]
    is_partial = opportunity.get("is_partial", False)
    coin = opportunity["coin"]
    reason = opportunity.get("reason", "unknown")

    print(
        f"SELL {opportunity['symbol'].upper()} amount {amount:.8f}, "
        f"value about {value_thb:,.2f} THB, PnL {format_rate(opportunity['expected_rate'])}, "
        f"is_partial={is_partial}, reason={reason}"
    )

    if config.DRY_RUN:
        fee = value_thb * config.TRADING_FEE_RATE
        net_received = value_thb - fee
        paper_thb += net_received

        # คำนวณ PnL เพื่อประเมินสถิติการแพ้ติดกัน
        state = position_state.get(coin, {})
        entry_price = state.get("entry_price") or opportunity["price"]
        net_profit = net_received - (amount * entry_price)

        if net_profit < 0 and not is_partial:
            consecutive_losses += 1
        elif net_profit > 0 and not is_partial:
            consecutive_losses = 0

        if is_partial:
            paper_balances[coin] = paper_balances.get(coin, 0.0) - amount
            if coin in position_state:
                if "TP1" in reason:
                    position_state[coin]["tp1_sold"] = True
                    position_state[coin]["stop_loss_price"] = position_state[coin]["entry_price"]
                    print(f"[🛡️ Break-even] กำไรแตะ TP1 แล้ว: เลื่อน Stop Loss ไปที่ทุน {position_state[coin]['entry_price']:.2f} THB")
                elif "TP2" in reason:
                    position_state[coin]["tp2_sold"] = True
                    position_state[coin]["tp1_sold"] = True
                    position_state[coin]["stop_loss_price"] = position_state[coin]["entry_price"]
                    print(f"[🛡️ Break-even] กำไรแตะ TP2 แล้ว: ยืนยันเลื่อน Stop Loss ไปที่ทุน {position_state[coin]['entry_price']:.2f} THB")
        else:
            paper_balances[coin] = 0.0
            position_state.pop(coin, None)

        save_state()
        mark_traded(opportunity["symbol"])
        detail = f"Dry run sold. PnL: {net_profit:,.2f} THB. Reason: {reason}"
        print(detail)
        notify_trade(notifier, f"[DRY RUN] Exit: {reason}", opportunity, detail)
        return

    # เทรดจริง (Live)
    current_order_type = config.ORDER_TYPE_SELL
    current_post_only = config.POST_ONLY

    # บังคับใช้ Market Order สำหรับ Stop Loss เพื่อความปลอดภัยสูงสุดในการออกสถานะ
    if reason == "stop loss":
        current_order_type = "market"
        current_post_only = False

    is_ioc = False
    if current_order_type == "limit_ioc":
        current_order_type = "limit"
        is_ioc = True

    order_res = client.place_ask(
        symbol=opportunity["symbol"],
        amount=amount,
        rate=opportunity["price"] if current_order_type == "limit" else 0.0,
        order_type=current_order_type,
        post_only=current_post_only,
    )
    if order_res.get("error") == 0:
        # หากใช้จำลอง Limit IOC ให้รอจับคู่ 3 วินาทีแล้วยกเลิกส่วนที่เหลือ
        if is_ioc:
            time.sleep(3)
            open_orders_res = client.get_open_orders(opportunity["symbol"])
            if open_orders_res.get("error") == 0:
                orders = open_orders_res.get("result", [])
                if isinstance(orders, list) and orders:
                    print(f"[⚡ IOC] ยกเลิกออเดอร์ขาย IOC ที่ไม่แมตช์จำนวน {len(orders)} รายการ...")
                    for o in orders:
                        order_id = o.get("id")
                        if order_id:
                            cancel_res = client.cancel_order(opportunity["symbol"], order_id, "sell")
                            print(f"[⚡ IOC] ยกเลิกออเดอร์ {order_id} ผลลัพธ์: {cancel_res}")

        # คำนวณประมาณการ PnL
        state = position_state.get(coin, {})
        entry_price = state.get("entry_price") or opportunity["price"]
        net_profit = (value_thb * (1 - config.TRADING_FEE_RATE)) - (amount * entry_price)

        if net_profit < 0 and not is_partial:
            consecutive_losses += 1
        elif net_profit > 0 and not is_partial:
            consecutive_losses = 0

        if is_partial:
            if coin in position_state:
                if "TP1" in reason:
                    position_state[coin]["tp1_sold"] = True
                    position_state[coin]["stop_loss_price"] = position_state[coin]["entry_price"]
                    print(f"[🛡️ Break-even] กำไรแตะ TP1 แล้ว: เลื่อน Stop Loss ไปที่ทุน {position_state[coin]['entry_price']:.2f} THB")
                elif "TP2" in reason:
                    position_state[coin]["tp2_sold"] = True
                    position_state[coin]["tp1_sold"] = True
                    position_state[coin]["stop_loss_price"] = position_state[coin]["entry_price"]
                    print(f"[🛡️ Break-even] กำไรแตะ TP2 แล้ว: ยืนยันเลื่อน Stop Loss ไปที่ทุน {position_state[coin]['entry_price']:.2f} THB")
        else:
            position_state.pop(coin, None)

        save_state()
        mark_traded(opportunity["symbol"])
        detail = f"Live sell success: {order_res.get('result')}\nReason: {reason}"
        print(detail)
        notify_trade(notifier, f"[LIVE] Exit: {reason}", opportunity, detail)
        try:
            trade_logger.log_trade({
                "timestamp": datetime.now().isoformat(),
                "symbol": opportunity["symbol"],
                "side": "SELL",
                "reason": reason,
                "entry_price": entry_price,
                "exit_price": opportunity["price"],
                "amount": amount,
                "value_thb": value_thb,
                "net_pnl_thb": net_profit,
                "is_partial": is_partial,
                "mode": "LIVE",
            })
        except Exception as e:
            print(f"[⚠️ Trade log error] {e}")
    else:
        mark_traded(opportunity["symbol"])
        detail = f"Live sell failed: {order_res.get('message') or order_res.get('error')}"
        print(detail)
        notify_trade(notifier, "[LIVE] Sell failed", opportunity, detail)
        try:
            trade_logger.log_trade({
                "timestamp": datetime.now().isoformat(),
                "symbol": opportunity["symbol"],
                "side": "SELL",
                "reason": "live_sell_failed",
                "entry_price": opportunity["price"],
                "exit_price": None,
                "amount": 0,
                "value_thb": 0,
                "net_pnl_thb": 0,
                "is_partial": False,
                "mode": "LIVE",
            })
        except Exception as e:
            print(f"[⚠️ Trade log error] {e}")


def execute_buy(client, notifier, opportunity, thb_balance, total_value):
    global paper_thb, paper_balances, last_buy_date

    amount_thb = calculate_position_size(thb_balance, total_value)
    estimated_coin = amount_thb / opportunity["price"]
    entry_price = opportunity["price"]
    atr = opportunity.get("atr", 0.0)

    # คำนวณขีดจำกัด SL และ TP ขาเข้าอ้างอิง ATR
    if atr > 0:
        atr_sl_price = entry_price - (atr * 1.5)
        pct_sl_price = entry_price * (1 - config.STOP_LOSS_RATE)
        # [FIX] ป้องกัน SL แคบเกินไปในเหรียญที่ผันผวนต่ำ (ATR เล็ก) จนราคาลงนิดเดียวก็โดน Stop Loss
        # เลือกจุดตัดขาดทุนที่ห่างจากราคาซื้อมากกว่า (ปลอดภัยกว่า) ระหว่าง ATR-based กับ Percentage-based
        if atr_sl_price < pct_sl_price:
            sl_price = atr_sl_price
            sl_desc = f"ATR-based (entry - 1.5*ATR: {sl_price:,.4f})"
        else:
            sl_price = pct_sl_price
            sl_desc = f"Percentage-based floor ({config.STOP_LOSS_RATE*100:.1f}%: {sl_price:,.4f}) – ATR แคบเกินไป"
        tp_price = entry_price + (atr * 3.0)
        tp_desc = f"ATR-based (entry + 3.0*ATR: {tp_price:,.4f})"
    else:
        sl_price = entry_price * (1 - config.STOP_LOSS_RATE)
        tp_price = entry_price * (1 + config.TAKE_PROFIT_RATE)
        sl_desc = f"Percentage-based ({config.STOP_LOSS_RATE*100:.1f}%: {sl_price:,.4f})"
        tp_desc = f"Percentage-based ({config.TAKE_PROFIT_RATE*100:.1f}%: {tp_price:,.4f})"

    print(
        f"BUY {opportunity['symbol'].upper()} using {amount_thb:,.2f} THB, "
        f"estimated coin {estimated_coin:.8f}, net edge {format_rate(opportunity['net_rate'])}\n"
        f"  Stop Loss target: {sl_desc}\n"
        f"  Take Profit target: {tp_desc}"
    )

    if amount_thb < config.MIN_TRADE_VALUE_THB:
        print("Skip buy: THB balance is below minimum trade value")
        return

    if config.DRY_RUN:
        fee = amount_thb * config.TRADING_FEE_RATE
        net_buy_thb = amount_thb - fee
        paper_thb -= amount_thb
        coin_bought = net_buy_thb / entry_price
        paper_balances[opportunity["coin"]] = paper_balances.get(opportunity["coin"], 0.0) + coin_bought
        
        # Handle position state for dry run
        if config.ALLOW_ADD_TO_POSITION and opportunity["coin"] in position_state:
            # Existing position: average entry price and increase amount
            prev = position_state[opportunity["coin"]]
            prev_amount = prev["initial_amount"]
            new_amount = coin_bought
            total_amount = prev_amount + new_amount
            weighted_entry = (prev["entry_price"] * prev_amount + entry_price * new_amount) / total_amount
            prev["entry_price"] = weighted_entry
            prev["highest_price"] = max(prev.get("highest_price", weighted_entry), entry_price)
            prev["initial_amount"] = total_amount
        else:
            # New position
            position_state[opportunity["coin"]] = {
                "entry_price": entry_price,
                "highest_price": entry_price,
                "stop_loss_price": sl_price,
                "take_profit_price": tp_price,
                "atr_at_entry": atr,
                "initial_amount": coin_bought,
                "tp1_sold": False,
                "tp2_sold": False,
                "opened_at": datetime.now().isoformat(timespec="seconds"),
            }

        server_day, _ = get_server_day_and_time(client)
        last_buy_date = server_day
        save_state()
        mark_traded(opportunity["symbol"])
        detail = f"Dry run bought. SL: {sl_desc}, TP: {tp_desc}. Score: {opportunity.get('score', 0)}"
        print(detail)
        notify_trade(notifier, "[DRY RUN] Buy executed", opportunity, detail)
        try:
            trade_logger.log_trade({
                "timestamp": datetime.now().isoformat(),
                "symbol": opportunity["symbol"],
                "side": "BUY",
                "reason": "dry_run_success",
                "entry_price": entry_price,
                "exit_price": None,
                "amount": coin_bought,
                "value_thb": amount_thb,
                "net_pnl_thb": 0.0,
                "is_partial": False,
                "mode": "DRY_RUN",
            })
        except Exception as e:
            print(f"[⚠️ Trade log error] {e}")
        return

    open_order_count = get_open_order_count(client, opportunity["symbol"])
    print("[Attempting BUY...]")
    print(f"[Debug] Attempting BUY for {opportunity['symbol']} – open orders: {open_order_count}, THB balance: {thb_balance}, order size (THB): {amount_thb}")
    # Validate entry price; refresh if zero or negative
    if entry_price <= 0:
        ticker = client.get_ticker(opportunity["symbol"])
        refreshed = safe_float(ticker.get("last"))
        if refreshed > 0:
            entry_price = refreshed
            print(f"[Info] Refreshed entry price for {opportunity['symbol']}: {entry_price:,.4f} THB")
    if open_order_count > 0:
        mark_traded(opportunity["symbol"])
        detail = f"Skipped live buy because {open_order_count} open order(s) already exist"
        print(detail)
        # [FIX] ไม่ส่ง Telegram เมื่อซื้อไม่สำเร็จ/ถูกข้าม – แจ้งเตือนเฉพาะซื้อสำเร็จเท่านั้น
        return
    current_order_type = config.ORDER_TYPE_BUY
    current_post_only = config.POST_ONLY

    is_ioc = False
    if current_order_type == "limit_ioc":
        current_order_type = "limit"
        is_ioc = True

    order_res = client.place_bid(
        symbol=opportunity["symbol"],
        amount=amount_thb,
        rate=entry_price if current_order_type == "limit" else 0.0,
        order_type=current_order_type,
        post_only=current_post_only,
    )
    if order_res.get("error") == 0:
        if is_ioc:
            time.sleep(3)
            open_orders_res = client.get_open_orders(opportunity["symbol"])
            if open_orders_res.get("error") == 0:
                orders = open_orders_res.get("result", [])
                if isinstance(orders, list) and orders:
                    print(f"[⚡ IOC] ยกเลิกออเดอร์ซื้อ IOC ที่ไม่แมตช์จำนวน {len(orders)} รายการ...")
                    for o in orders:
                        order_id = o.get("id")
                        if order_id:
                            cancel_res = client.cancel_order(opportunity["symbol"], order_id, "buy")
                            print(f"[⚡ IOC] ยกเลิกออเดอร์ {order_id} ผลลัพธ์: {cancel_res}")

        result = order_res.get("result", {})
        order_id = result.get("id")
        if not order_id:
            print(f"[⚠️ Warning] Order placed but no ID returned for {opportunity['symbol']}")
            # Treat as failure
            mark_traded(opportunity["symbol"])
            detail = f"Buy FAILED {opportunity.get('symbol')} – Missing order ID"
            print(detail)
            # [FIX] ไม่ส่ง Telegram เมื่อซื้อไม่สำเร็จ – แจ้งเตือนเฉพาะซื้อสำเร็จเท่านั้น
            try:
                trade_logger.log_trade({
                    "timestamp": datetime.now().isoformat(),
                    "symbol": opportunity["symbol"],
                    "side": "BUY",
                    "reason": "live_buy_failed_missing_id",
                    "entry_price": opportunity["price"],
                    "exit_price": None,
                    "amount": 0,
                    "value_thb": 0,
                    "net_pnl_thb": 0,
                    "is_partial": False,
                    "mode": "LIVE",
                })
            except Exception as e:
                print(f"[⚠️ Trade log error] {e}")
            return
        actual_price = safe_float(
            result.get("rate") or 
            result.get("rat") or 
            result.get("avg_price") or 
            result.get("avg")
        )
        if actual_price > 0:
            entry_price = actual_price
            print(f"[🔥 Filled Price] ดึงราคา fill จริงจาก order_res ได้สำเร็จ: {entry_price:,.4f} THB")
            # Recalculate SL and TP based on actual entry price
            if atr > 0:
                atr_sl_price = entry_price - (atr * 1.5)
                pct_sl_price = entry_price * (1 - config.STOP_LOSS_RATE)
                # [FIX] ใช้จุดตัดขาดทุนที่ห่างกว่าเสมอ ป้องกัน SL แคบเกินไป
                if atr_sl_price < pct_sl_price:
                    sl_price = atr_sl_price
                    sl_desc = f"ATR-based (entry - 1.5*ATR: {sl_price:,.4f})"
                else:
                    sl_price = pct_sl_price
                    sl_desc = f"Percentage-based floor ({config.STOP_LOSS_RATE*100:.1f}%: {sl_price:,.4f}) – ATR แคบเกินไป"
                tp_price = entry_price + (atr * 3.0)
                tp_desc = f"ATR-based (entry + 3.0*ATR: {tp_price:,.4f})"
            else:
                sl_price = entry_price * (1 - config.STOP_LOSS_RATE)
                tp_price = entry_price * (1 + config.TAKE_PROFIT_RATE)
                sl_desc = f"Percentage-based ({config.STOP_LOSS_RATE*100:.1f}%: {sl_price:,.4f})"
                tp_desc = f"Percentage-based ({config.TAKE_PROFIT_RATE*100:.1f}%: {tp_price:,.4f})"
        else:
            print(f"[⚠️ Warning] ไม่สามารถดึงราคา fill จริงจาก order_res ได้ ใช้ราคา ticker: {entry_price:,.4f} THB")

        server_day, _ = get_server_day_and_time(client)
        new_amount = (amount_thb / entry_price) * (1 - config.TRADING_FEE_RATE)
        
        if config.ALLOW_ADD_TO_POSITION and opportunity["coin"] in position_state:
            prev = position_state[opportunity["coin"]]
            prev_amount = prev["initial_amount"]
            total_amount = prev_amount + new_amount
            weighted_entry = (prev["entry_price"] * prev_amount + entry_price * new_amount) / total_amount
            prev["entry_price"] = weighted_entry
            prev["highest_price"] = max(prev.get("highest_price", weighted_entry), entry_price)
            prev["initial_amount"] = total_amount
        else:
            position_state[opportunity["coin"]] = {
                "entry_price": entry_price,
                "highest_price": entry_price,
                "stop_loss_price": sl_price,
                "take_profit_price": tp_price,
                "atr_at_entry": atr,
                "initial_amount": new_amount,
                "tp1_sold": False,
                "tp2_sold": False,
                "opened_at": datetime.now().isoformat(timespec="seconds"),
            }
        last_buy_date = server_day
        save_state()
        mark_traded(opportunity["symbol"])
        detail = f"Live buy success: {order_res.get('result')}\nSL: {sl_desc}\nTP: {tp_desc}"
        print(detail)
        notify_trade(notifier, "[LIVE] Buy success", opportunity, detail)
        try:
            trade_logger.log_trade({
                "timestamp": datetime.now().isoformat(),
                "symbol": opportunity["symbol"],
                "side": "BUY",
                "reason": "live_buy_success",
                "entry_price": entry_price,
                "exit_price": None,
                "amount": new_amount,
                "value_thb": amount_thb,
                "net_pnl_thb": 0.0,
                "is_partial": False,
                "mode": "LIVE",
            })
        except Exception as e:
            print(f"[⚠️ Trade log error] {e}")
    else:
        mark_traded(opportunity["symbol"])
        error_code = order_res.get('error')
        detail = f"Buy FAILED {opportunity.get('symbol')} – Error {error_code}: {order_res.get('message') or 'No message'}"
        print(detail)
        # [FIX] ไม่ส่ง Telegram เมื่อซื้อไม่สำเร็จ – แจ้งเตือนเฉพาะซื้อสำเร็จเท่านั้น
        try:
            trade_logger.log_trade({
                "timestamp": datetime.now().isoformat(),
                "symbol": opportunity["symbol"],
                "side": "BUY",
                "reason": "live_buy_failed",
                "entry_price": opportunity["price"],
                "exit_price": None,
                "amount": 0,
                "value_thb": 0,
                "net_pnl_thb": 0,
                "is_partial": False,
                "mode": "LIVE",
            })
        except Exception as e:
            print(f"[⚠️ Trade log error] {e}")


def run_bot():
    global daily_start_value, last_daily_reset_day

    if not config.validate_config():
        sys.exit(1)

    load_state()

    client = BitkubClient(
        config.API_KEY,
        config.API_SECRET,
        config.BASE_URL,
        request_timeout=config.REQUEST_TIMEOUT_SECONDS,
        order_timeout=config.ORDER_TIMEOUT_SECONDS,
    )
    notifier = TelegramNotifier(
        config.TELEGRAM_BOT_TOKEN,
        config.TELEGRAM_CHAT_ID,
        config.TELEGRAM_ENABLED,
    )
    strategy = make_strategy()

    print("=" * 60)
    print("Bitkub multi-coin trading bot started")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mode: {'DRY RUN' if config.DRY_RUN else 'LIVE'}")
    print(f"Strategy: {config.STRATEGY}")
    print("Dynamic trade sizing based on portfolio balance and risk")
    print(f"Fee rate: {format_rate(config.TRADING_FEE_RATE)} per side")
    print(f"Minimum expected profit buffer: {format_rate(config.MIN_EXPECTED_PROFIT_RATE)}")
    print("=" * 60)

    while True:
        try:
            print("\n" + "-" * 40)
            print(f"Bot update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            tickers = client.get_all_tickers()
            current_prices = {}
            for ticker_key, ticker in tickers.items():
                if ticker_key.upper().startswith("THB_") and isinstance(ticker, dict):
                    symbol = ticker_key_to_symbol(ticker_key)
                    current_prices[base_coin(symbol)] = safe_float(ticker.get("last"))

            thb_balance, asset_balances, total_value = get_balances(client, current_prices)
            sync_position_state(client, asset_balances, current_prices)
            print(f"THB balance: {thb_balance:,.2f} | Portfolio value: {total_value:,.2f} THB")

            # ตรวจสอบขีดจำกัดการขาดทุนรายวัน (Daily Loss Limit 3%)
            server_day, _ = get_server_day_and_time(client)
            if last_daily_reset_day != server_day:
                daily_start_value = total_value
                last_daily_reset_day = server_day
                print(f"[📅 Daily Reset] Initial daily portfolio value set to {daily_start_value:,.2f} THB")

            daily_loss_rate = (total_value / daily_start_value) - 1 if daily_start_value > 0 else 0.0
            disable_buys = False
            if daily_loss_rate <= -config.DAILY_RISK_LIMIT_RATE:
                print(f"[🛑 Daily Loss Limit] Portfolio ยุบเกิน {format_rate(daily_loss_rate)} (จำกัด: {format_rate(-config.DAILY_RISK_LIMIT_RATE)}). ระงับการเปิด Position เพิ่ม in วันนี้!")
                disable_buys = True

            # ตรวจระบบระงับเทรดอัตโนมัติเมื่อแพ้ติดต่อกันเกิน 3 ครั้ง
            if consecutive_losses >= 3:
                print(f"[🛑 Auto Pause] แพ้ติดต่อกัน {consecutive_losses} ครั้ง. ระงับการซื้อขายอัตโนมัติเพื่อป้องกันพอร์ตเสียหาย!")
                disable_buys = True
                # ==== BTC Market Crash Filter ====
                if config.BTC_REGIME_FILTER_ENABLED and is_btc_market_crashing(client):
                    print("[🛑 BTC Crash] BTC market appears to be crashing – disabling buys for this cycle.")
                    disable_buys = True

            buy_candidates, sell_candidates, prices_by_coin, df_15m_by_coin = scan_market(
                client,
                strategy,
                tickers,
                asset_balances,
            )
            current_prices.update(prices_by_coin)

            risk_exits = build_exit_candidates(client, asset_balances, current_prices, df_15m_by_coin)
            open_positions = sum(1 for amount in asset_balances.values() if amount > 0)

            if risk_exits:
                execute_sell(client, notifier, risk_exits[0])
            elif sell_candidates:
                execute_sell(client, notifier, sell_candidates[0])
            elif buy_candidates:
                if disable_buys:
                    print("[🛑 Blocked] ข้ามคำสั่งซื้อ: ถูกระงับชั่วคราวเนื่องจากความปลอดภัย (ลิมิตพอร์ตรายวัน หรือ แพ้ติดต่อกัน)")
                elif thb_balance < config.MIN_TRADE_VALUE_THB:
                    print("No trade: ยอดเงิน THB ในพอร์ตไม่เพียงพอสำหรับการเทรด")
                elif open_positions >= config.MAX_OPEN_POSITIONS:
                    print(f"No trade: เปิด position ครบโควต้าแล้ว ({open_positions}/{config.MAX_OPEN_POSITIONS})")
                else:
                    execute_buy(client, notifier, buy_candidates[0], thb_balance, total_value)
            else:
                print("No trade: no signal passed score or fee filter")

                # AI Last Chance Scanner (Non-Intrusive Mode)
                server_day, server_dt = get_server_day_and_time(client)
                current_hour = server_dt.hour
                current_minute = server_dt.minute

                # Check activation conditions
                in_time_window = (22 <= current_hour < 24)
                no_buy_executed_today = (last_buy_date != server_day)
                no_open_positions = (open_positions == 0)
                no_normal_buy_signal = (not buy_candidates)

                if in_time_window and no_buy_executed_today and no_open_positions and no_normal_buy_signal:
                    global last_chance_scan_minute
                    current_minute_key = (server_day, current_hour, current_minute // 5)

                    if last_chance_scan_minute != current_minute_key:
                        print(f"[🔍 AI Last Chance Scanner] Activating scanner at {server_dt.strftime('%H:%M:%S')}...")
                        last_chance_scan_minute = current_minute_key

                        scanner_opt = ai_last_chance_scanner.run_last_chance_scan_v2(client)
                        if scanner_opt:
                            if disable_buys:
                                print("[🛑 Blocked] AI Last Chance Scanner BUY skipped: safety limit (daily loss or consecutive losses)")
                            elif thb_balance < config.MIN_TRADE_VALUE_THB:
                                print("No trade: ยอดเงิน THB ไม่เพียงพอสำหรับ AI Last Chance Scanner")
                            else:
                                print(f"[🚀 AI Last Chance Scanner] Executing BUY for {scanner_opt['symbol'].upper()}...")
                                execute_buy(client, notifier, scanner_opt, thb_balance, total_value)

        except KeyboardInterrupt:
            print("\nBot stopped by user")
            break
        except Exception as e:
            print(f"[Error] Main loop failed: {e}")

        time.sleep(config.LOOP_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_bot()
