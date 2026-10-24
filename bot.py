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
import config

# Helper to safely format floats in logs – returns 'N/A' for None values

# ======================= PROFIT-ONLY SELL PROTECTION =======================
# SELL is allowed only when estimated NET proceeds after BUY/SELL fees
# and execution buffer are greater than the actual cost basis.
PROFIT_ONLY_SELL_ENABLED = True
PROFIT_ONLY_MIN_NET_RATE = config.SELL_MIN_NET_PROFIT_RATE if hasattr(config, "SELL_MIN_NET_PROFIT_RATE") else 0.0075
PROFIT_ONLY_SLIPPAGE_BUFFER_RATE = config.SELL_EXECUTION_BUFFER_RATE if hasattr(config, "SELL_EXECUTION_BUFFER_RATE") else 0.0020
PROFIT_ONLY_USE_BID_PRICE = True

# ================= HOLD UNTIL DOWNTURN EXIT =================
# When enabled, a position is never sold just because it reached a fixed
# take-profit percentage.  After the conservative break-even threshold
# (fees + execution buffer), the bot keeps holding until a bearish signal
# appears.  This lets strong trends run instead of taking small profits early.
HOLD_UNTIL_SELL_SIGNAL = True
HOLD_UNTIL_SELL_SIGNAL_MIN_NET_RATE = 0.0

def profit_only_sell_check(entry_cost, sell_price, quantity, buy_fee=0.0, sell_fee_rate=0.0):
    if not PROFIT_ONLY_SELL_ENABLED or entry_cost <= 0 or sell_price <= 0 or quantity <= 0:
        return False if PROFIT_ONLY_SELL_ENABLED else True, 0.0, -1.0, 0.0
    total_cost=float(entry_cost)+float(buy_fee)
    protected_price=float(sell_price)*(1.0-PROFIT_ONLY_SLIPPAGE_BUFFER_RATE)
    gross=protected_price*float(quantity)
    net=gross-gross*float(sell_fee_rate)
    profit=net-total_cost
    rate=profit/total_cost if total_cost else -1.0
    required_rate = HOLD_UNTIL_SELL_SIGNAL_MIN_NET_RATE if HOLD_UNTIL_SELL_SIGNAL else PROFIT_ONLY_MIN_NET_RATE
    required=total_cost*(1.0+required_rate)
    allowed=net>=required
    denom=float(quantity)*max(1e-12,1.0-float(sell_fee_rate))
    min_price=(required/denom)/max(1e-12,1.0-PROFIT_ONLY_SLIPPAGE_BUFFER_RATE)
    return allowed,profit,rate,min_price

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
    # Every explicit BUY rejection is a HOLD/BLOCK decision and is persisted.
    # Failures here are non-fatal so one symbol can never crash the scanner.
    try:
        if config.LEARNING_ENABLED:
            stats = learning.get_symbol_learning(symbol)
            if stats is not None:
                learning.decision(
                    symbol, "HOLD", price=0, score=score,
                    min_score=getattr(config, "FINAL_MIN_BUY_SCORE", None),
                    expected=expected, required=None, net_edge=net_edge,
                    reason=f"BLOCK/HOLD: {reason}", stats=stats
                )
    except Exception as exc:
        print(f"[DB-AWARENESS][WARN] rejection decision log failed {symbol}: {exc}")

import logging
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
import v7_entry_gate
import trade_logger
from supabase_learning import learning
from profit_vault import profit_vault

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
                # Legacy state cannot prove whether cost_thb/entry_price came from
                # an actual exchange fill or a balance-only restart estimate.
                for _coin, _state in position_state.items():
                    if isinstance(_state, dict) and "cost_basis_verified" not in _state:
                        _state["cost_basis_verified"] = False
                        _state["cost_basis_source"] = "legacy_state_unverified"
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


def extract_buy_fill_details(order_result, order_info, fallback_price, requested_thb, fee_rate):
    """Reconcile a BUY from Bitkub's acknowledgement and order-info history.

    Bitkub order-info history records quote spend per fill and its execution
    rate. Prefer a weighted average from those fills; use the acknowledgement's
    receive quantity when available, otherwise estimate received quantity from
    fill history after the configured trading fee.
    """
    order_result = order_result if isinstance(order_result, dict) else {}
    order_info = order_info if isinstance(order_info, dict) else {}
    history = order_info.get("history", [])
    quote_spent = 0.0
    gross_coin = 0.0
    weighted_price = 0.0
    if isinstance(history, list):
        for fill in history:
            if not isinstance(fill, dict):
                continue
            rate = safe_float(fill.get("rate"))
            quote_amount = safe_float(fill.get("amount"))
            if rate > 0 and quote_amount > 0:
                quote_spent += quote_amount
                gross_coin += quote_amount / rate
    if quote_spent > 0 and gross_coin > 0:
        weighted_price = quote_spent / gross_coin

    entry_price = weighted_price or safe_float(
        order_result.get("rate")
        or order_result.get("rat")
        or order_result.get("avg_price")
        or order_result.get("avg")
        or order_info.get("rate")
        or order_info.get("rat")
        or order_info.get("avg_price")
        or order_info.get("avg")
    ) or safe_float(fallback_price)

    received = safe_float(order_result.get("rec"))
    if received <= 0 and gross_coin > 0:
        received = gross_coin * (1.0 - max(0.0, min(float(fee_rate), 1.0)))

    actual_cost = safe_float(order_info.get("filled"))
    if actual_cost <= 0:
        actual_cost = quote_spent if quote_spent > 0 else safe_float(requested_thb)

    return entry_price, received, actual_cost, bool(weighted_price)


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


def anti_chase_check(df_5m, df_15m, price: float):
    """Return (allowed, reason) for late/spike BUY entries.

    This is deliberately a conservative safety gate. It blocks entries when
    price is stretched from EMA20, has risen too quickly over the last 3
    closed/current bars, RSI is overheated, or the latest 5m candle is an
    unusually large green candle.
    """
    if not getattr(config, "ANTI_CHASE_ENABLED", True):
        return True, "disabled"
    try:
        if price <= 0 or len(df_5m) < 25 or len(df_15m) < 25:
            return False, "insufficient candle data"

        close5 = df_5m["close"].astype(float)
        ema20_5 = float(close5.ewm(span=20, adjust=False).mean().iloc[-1])
        if ema20_5 > 0:
            dist5 = (price / ema20_5) - 1.0
            if dist5 > config.ANTI_CHASE_MAX_5M_EMA20_DISTANCE:
                return False, f"5m price {dist5*100:.2f}% above EMA20"

        close15 = df_15m["close"].astype(float)
        ema20_15 = float(close15.ewm(span=20, adjust=False).mean().iloc[-1])
        if ema20_15 > 0:
            dist15 = (price / ema20_15) - 1.0
            if dist15 > config.ANTI_CHASE_MAX_15M_EMA20_DISTANCE:
                return False, f"15m price {dist15*100:.2f}% above EMA20"

        if len(close5) >= 4:
            rise3 = (price / float(close5.iloc[-4])) - 1.0
            if rise3 > config.ANTI_CHASE_MAX_3BAR_RISE_5M:
                return False, f"5m 3-bar rise {rise3*100:.2f}%"

        rsi5 = safe_float(df_5m.iloc[-1].get("rsi"), 0.0)
        if rsi5 >= config.ANTI_CHASE_MAX_RSI_5M:
            return False, f"5m RSI {rsi5:.1f} >= {config.ANTI_CHASE_MAX_RSI_5M:.1f}"

        row5 = df_5m.iloc[-1]
        o = safe_float(row5.get("open"), 0.0)
        c = safe_float(row5.get("close"), 0.0)
        if o > 0 and c > o:
            green = (c / o) - 1.0
            if green >= config.ANTI_CHASE_LARGE_GREEN_BAR_5M:
                return False, f"5m large green candle {green*100:.2f}%"

        return True, "entry not stretched"
    except Exception as exc:
        # Fail closed for live trading: if the protection cannot be evaluated,
        # do not chase an entry with stale/incomplete data.
        return False, f"anti-chase check error: {exc}"


def mark_traded(symbol: str):
    last_trade_at[symbol] = time.time()


_broker_symbols_cache = set()
_broker_symbols_cache_timestamp = 0.0


def get_broker_symbols(client: BitkubClient, cache_seconds: int = 300) -> set:
    """Return symbols that Bitkub marks as source=broker.

    Bitkub documents error 61 as a hard limitation of place-bid/place-ask:
    broker-source coins cannot be traded through those API endpoints.
    Cache the market metadata so every scan does not make another symbols call.
    """
    global _broker_symbols_cache, _broker_symbols_cache_timestamp

    now = time.time()
    if now - _broker_symbols_cache_timestamp < cache_seconds:
        return _broker_symbols_cache

    try:
        response = client.get_symbols()
        if response.get("error") not in (None, 0):
            return _broker_symbols_cache

        rows = response.get("result", [])
        if isinstance(rows, dict):
            rows = rows.get("symbols", rows.get("data", []))

        broker = set()
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if str(row.get("source", "")).lower() == "broker":
                    sym = str(row.get("symbol") or row.get("sym") or "").lower()
                    if sym:
                        broker.add(sym)

        _broker_symbols_cache = broker
        _broker_symbols_cache_timestamp = now
        return broker
    except Exception as e:
        print(f"[Warning] Could not refresh broker-source symbols: {e}")
        return _broker_symbols_cache


def get_scan_symbols(client: BitkubClient, tickers: dict) -> list:
    broker_symbols = get_broker_symbols(client)

    if config.SCAN_SYMBOLS:
        configured = config.SCAN_SYMBOLS[: config.MAX_SCAN_SYMBOLS]
        filtered = [s for s in configured if s.lower() not in broker_symbols]
        skipped = [s for s in configured if s.lower() in broker_symbols]
        if skipped:
            print(f"[Broker filter] Excluded unsupported broker pairs: {', '.join(s.upper() for s in skipped)}")
        return filtered

    if not tickers:
        tickers = client.get_all_tickers()

    thb_pairs = []
    for ticker_key, ticker in tickers.items():
        if not ticker_key.upper().startswith("THB_") or not isinstance(ticker, dict):
            continue
        symbol = ticker_key_to_symbol(ticker_key)
        if symbol.lower() in broker_symbols:
            continue
        volume = get_ticker_volume_thb(ticker)
        thb_pairs.append((volume, symbol))

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
    """Use available THB for each buy, while always keeping a minimum cash reserve.

    The strategy is "near all-in", but it must always leave at least
    ``config.MIN_CASH_RESERVE_RATE`` (default 10%) of the total portfolio
    value (THB + coins) sitting in THB, untouched. Only the THB above that
    reserve is available to spend on a buy order.
    """
    thb_balance = max(float(thb_balance), 0.0)
    total_value = max(float(total_value), 0.0)

    if thb_balance <= 0:
        return 0.0

    reserve_rate = max(min(float(config.MIN_CASH_RESERVE_RATE), 1.0), 0.0)
    reserve_amount = total_value * reserve_rate

    usable_thb = thb_balance - reserve_amount
    return max(usable_thb, 0.0)


def position_cost_for_amount(coin: str, amount: float) -> float:
    """Return the estimated THB cost basis for ``amount`` coins.

    New positions persist the exact THB amount spent in ``cost_thb``.
    Older state files are supported by deriving the cost from entry price and
    the buy-side fee that was deducted from the received coin.
    """
    if amount <= 0:
        return 0.0

    state = position_state.get(coin, {})
    total_amount = safe_float(state.get("initial_amount", 0.0))
    total_cost = safe_float(state.get("cost_thb", 0.0))

    if total_amount > 0 and total_cost > 0:
        return total_cost * min(amount / total_amount, 1.0)

    entry_price = safe_float(state.get("entry_price", 0.0))
    if entry_price <= 0:
        return 0.0

    # On a buy, the fee is deducted from the received coin. Therefore the
    # original THB cost per received coin is entry_price / (1 - fee).
    fee_factor = max(1.0 - config.TRADING_FEE_RATE, 1e-12)
    return amount * entry_price / fee_factor


def calculate_net_sell_profit(coin: str, amount: float, price: float) -> tuple[float, float]:
    """Return (net_profit_thb, break_even_price) after both trading fees."""
    if amount <= 0 or price <= 0:
        return 0.0, 0.0

    cost_thb = position_cost_for_amount(coin, amount)
    protected_price = price * (1.0 - PROFIT_ONLY_SLIPPAGE_BUFFER_RATE)
    net_received = amount * protected_price * (1.0 - config.TRADING_FEE_RATE)
    net_profit = net_received - cost_thb

    # Break-even is the *market* price required after the execution buffer
    # and sell fee. This keeps the reported break-even consistent with the
    # same protected-price model used by net_profit.
    break_even_price = (
        cost_thb
        / (
            amount
            * max(1.0 - config.TRADING_FEE_RATE, 1e-12)
            * max(1.0 - PROFIT_ONLY_SLIPPAGE_BUFFER_RATE, 1e-12)
        )
        if amount > 0 else 0.0
    )
    return net_profit, break_even_price


def is_profitable_sell(coin: str, amount: float, price: float) -> bool:
    """Never submit a sell unless net proceeds exceed cost after fees."""
    net_profit, _ = calculate_net_sell_profit(coin, amount, price)
    return net_profit > 0.0


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

    result = balances_res.get("result", {}) or {}
    thb_entry = result.get("THB", 0.0)
    if isinstance(thb_entry, dict):
        thb_available = thb_entry.get("available", thb_entry.get("free", thb_entry.get("balance", 0.0)))
    else:
        thb_available = thb_entry
    thb_balance = max(safe_float(thb_available), 0.0)
    asset_balances = {}
    total_value = thb_balance

    if thb_balance <= 0:
        print(f"[⚠️ Portfolio] Bitkub reports THB available={thb_balance:,.8f}. Assets returned={len(result)}. Keys={list(result.keys())[:12]}")

    for coin, balance in result.items():
        coin = coin.upper()
        if coin == "THB":
            continue
        if isinstance(balance, dict):
            available = balance.get("available", balance.get("free", balance.get("balance", 0.0)))
        else:
            available = balance
        amount = safe_float(available)
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
                "atr_at_entry": 0.0,
                "cost_thb": amount * price / max(1.0 - config.TRADING_FEE_RATE, 1e-12),
                "initial_amount": amount,
                # Balance sync cannot prove historical entry price/cost. Block live
                # sells until real cost basis is reconciled rather than inventing one.
                "cost_basis_verified": False,
                "cost_basis_source": "balance_sync_estimate",
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
    """Build sell candidates for held positions.

    In HOLD_UNTIL_SELL_SIGNAL mode the bot:
    1. calculates a conservative break-even price including fees/buffer;
    2. refuses to sell below that threshold;
    3. ignores fixed TP/ATR/time exits;
    4. sells only after a confirmed bearish technical signal.
    """

    exit_candidates = []

    for coin, amount in asset_balances.items():
        price = current_prices.get(coin, 0.0)
        if amount <= 0 or price <= 0:
            continue

        state = position_state.get(coin, {})
        entry_price = safe_float(state.get("entry_price", price)) or price
        _update_learning_position_metrics(coin, price, state)
        highest_price = max(safe_float(state.get("highest_price", price)), price)
        pnl_rate = (price / entry_price) - 1 if entry_price > 0 else 0.0
        drawdown_from_high = (price / highest_price) - 1 if highest_price > 0 else 0.0
        net_profit, break_even_price = calculate_net_sell_profit(coin, amount, price)

        # If the current full position cannot be sold for a net profit,
        # do not create any exit candidate, regardless of technical signals.
        required_rate = HOLD_UNTIL_SELL_SIGNAL_MIN_NET_RATE if HOLD_UNTIL_SELL_SIGNAL else PROFIT_ONLY_MIN_NET_RATE
        required_profit = position_cost_for_amount(coin, amount) * required_rate
        if net_profit < required_profit:
            print(
                f"[{coin}] HOLD: profit target not reached "
                f"| Current {price:.8f} | Break-even {break_even_price:.8f} "
                f"| Net P/L {net_profit:,.4f} THB < target {required_profit:,.4f} THB"
            )
            learning.decision(f"{coin}_THB", "HOLD", price=price, reason="profit target not reached", net_edge=(net_profit/position_cost_for_amount(coin,amount) if position_cost_for_amount(coin,amount)>0 else 0))
            continue

        atr_at_entry = safe_float(state.get("atr_at_entry", 0.0))
        reason = None
        sell_reasons = []

        # Learned trailing is an emergency profit-protection exit, not the normal exit.
        learned_exit = None
        try:
            learned_exit = learning.get_exit_learning(f"{coin}_THB") if config.LEARNING_ENABLED else None
        except Exception:
            learned_exit = None
        max_profit_pct_state = safe_float(state.get("max_profit_pct"), 0.0) or 0.0
        trailing_stop = safe_float(state.get("trailing_stop"), 0.0) or 0.0
        if (learned_exit and learned_exit.get("trade_count", 0) >= 2
                and max_profit_pct_state >= config.TRAILING_MIN_PROFIT_RATE
                and trailing_stop > 0 and price <= trailing_stop and pnl_rate > 0):
            reason = f"learned trailing protection ({trailing_stop:.8f})"
            sell_reasons = ["LEARNED_TRAILING"]

        # 1) Technical exit only.  Fixed TP/ATR exits are intentionally
        # disabled in HOLD_UNTIL_SELL_SIGNAL mode so profitable trends can run.
        if not reason:
            held_minutes = _position_held_minutes(coin)
            if held_minutes is None or held_minutes >= config.MIN_HOLD_MINUTES:
                if coin not in df_15m_by_coin:
                    try:
                        symbol = f"{coin}_thb"
                        candles_15m = client.get_candles(symbol, resolution="15", limit=300)
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

                        ema_exit = (row_prev["ema_fast"] >= row_prev["ema_slow"]) and (row_now["ema_fast"] < row_now["ema_slow"])
                        macd_exit = (row_prev["macd"] >= row_prev["macd_signal"]) and (row_now["macd"] < row_now["macd_signal"])
                        rsi_exit = row_now["rsi"] < config.SELL_RSI_THRESHOLD

                        sell_signal_count = sum([ema_exit, macd_exit, rsi_exit])
                        active_reasons = []
                        if ema_exit:
                            active_reasons.append("📉 EMA Cross Down")
                        if macd_exit:
                            active_reasons.append("📉 MACD Cross Down")
                        if rsi_exit:
                            active_reasons.append(f"📉 RSI={row_now['rsi']:.1f} < {config.SELL_RSI_THRESHOLD}")

                        if sell_signal_count >= config.SELL_CONFIRM_MIN_CONDITIONS:
                            reason = f"technical profit exit ({', '.join(active_reasons)})"
                            sell_reasons = active_reasons

        # Time exits are intentionally disabled in HOLD_UNTIL_SELL_SIGNAL mode.
        if not reason and not HOLD_UNTIL_SELL_SIGNAL and check_time_exit(coin, price):
            reason = "time profit exit"
            sell_reasons = ["⏰ Time-based profit exit"]

        if not reason:
            continue

        amount_to_sell = amount
        value_thb = amount_to_sell * price

        # Final exact gate immediately before creating the candidate.
        final_profit, final_break_even = calculate_net_sell_profit(coin, amount_to_sell, price)
        final_required_profit = position_cost_for_amount(coin, amount_to_sell) * (
            HOLD_UNTIL_SELL_SIGNAL_MIN_NET_RATE if HOLD_UNTIL_SELL_SIGNAL else PROFIT_ONLY_MIN_NET_RATE
        )
        if final_profit < final_required_profit:
            print(
                f"[{coin}] HOLD: exit signal '{reason}' แต่กำไรสุทธิไม่พอ "
                f"| Net P/L {final_profit:,.4f} THB | Break-even {final_break_even:.8f}"
            )
            continue

        print(f"[{coin}] 🔔 SELL SIGNAL detected:")
        print(f"  ➡ Reason : {reason}")
        for r in sell_reasons:
            print(f"  ➡ Detail : {r}")
        print(f"  ➡ Entry  : {entry_price:.8f} | Current: {price:.8f}")
        print(f"  ➡ Break-even after fees: {final_break_even:.8f}")
        print(f"  ➡ Net P/L after fees: {final_profit:,.2f} THB")
        print(f"  ➡ Amount : {amount_to_sell:.8f} {coin} (value: {value_thb:,.2f} THB)")

        exit_candidates.append({
            "symbol": f"{coin}_thb",
            "coin": coin,
            "price": price,
            "signal": "SELL",
            "amount": amount_to_sell,
            "value_thb": value_thb,
            "expected_rate": pnl_rate,
            "required_rate": config.TRADING_FEE_RATE * 2,
            "net_rate": final_profit / max(position_cost_for_amount(coin, amount_to_sell), 1e-12),
            "net_profit_thb": final_profit,
            "break_even_price": final_break_even,
            "reason": reason,
            "sell_reasons": sell_reasons,
            "is_partial": False,
        })

    exit_candidates.sort(key=lambda item: item["net_profit_thb"], reverse=True)
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



_v7_market_regime_cache = {"timestamp": 0.0, "status": "CHOPPY / SIDEWAY"}


def get_v7_market_regime(client: BitkubClient) -> str:
    """Get the supplied V7 market regime using Bitkub BTC/THB 15m data.

    The original supplied script uses Binance BTCUSDT. This bot is Bitkub-based,
    so the same EMA20/EMA50 rule is applied to BTC_THB without changing the
    order/execution architecture.
    """
    global _v7_market_regime_cache
    now = time.time()
    if now - _v7_market_regime_cache["timestamp"] < 120:
        return _v7_market_regime_cache["status"]

    status = "CHOPPY / SIDEWAY"
    try:
        candles = client.get_candles("BTC_THB", resolution="15", limit=100)
        df = parse_candles_to_dataframe(candles)
        if len(df) >= 50:
            close = df["close"]
            ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
            ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
            last = close.iloc[-1]
            if last > ema20 and ema20 > ema50:
                status = "ALT BULLISH"
            elif last < ema20 and ema20 < ema50:
                status = "BEARISH / DUMP"
    except Exception as e:
        print(f"[⚠️ V7 Market Regime] {e}")

    _v7_market_regime_cache = {"timestamp": now, "status": status}
    return status



def get_orderbook_metrics(client, symbol):
    """Return best bid/ask depth and imbalance. Fails open only when the API is unavailable."""
    if not config.ORDERBOOK_FILTER_ENABLED:
        return {"bid_depth_thb": 0.0, "ask_depth_thb": 0.0, "imbalance": 0.0, "available": False}
    try:
        data = client.get_depth(symbol, limit=10)
        result = data.get("result", data) if isinstance(data, dict) else {}
        bids = result.get("bids", []) if isinstance(result, dict) else []
        asks = result.get("asks", []) if isinstance(result, dict) else []
        def depth(rows):
            total = 0.0
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict):
                    price = safe_float(row.get("price") or row.get("rate"))
                    amount = safe_float(row.get("amount") or row.get("qty") or row.get("volume"))
                elif isinstance(row, (list, tuple)) and len(row) >= 2:
                    price, amount = safe_float(row[0]), safe_float(row[1])
                else:
                    continue
                total += price * amount
            return total
        bid = depth(bids); ask = depth(asks)
        total = bid + ask
        return {
            "bid_depth_thb": bid,
            "ask_depth_thb": ask,
            "imbalance": ((bid-ask)/total) if total > 0 else 0.0,
            "available": total > 0,
        }
    except Exception as e:
        print(f"[OrderBook] {symbol}: {e}")
        return {"bid_depth_thb": 0.0, "ask_depth_thb": 0.0, "imbalance": 0.0, "available": False}


def market_regime(row):
    price = safe_float(row.get("close"))
    ema50 = safe_float(row.get("ema_50"))
    ema200 = safe_float(row.get("ema_200"))
    adx = safe_float(row.get("adx"))
    atr = safe_float(row.get("atr"))
    vol = safe_float(row.get("volume"))
    vma = safe_float(row.get("volume_ma"))
    if ema200 <= 0:
        return "UNKNOWN"
    if price > ema50 > ema200 and adx >= 25:
        return "TREND_UP"
    if price < ema50 < ema200 and adx >= 25:
        return "TREND_DOWN"
    if atr > 0 and price > 0 and atr/price > 0.03:
        return "HIGH_VOLATILITY"
    if vma > 0 and vol/vma > 2.0 and price > ema50:
        return "BREAKOUT"
    return "SIDEWAY"


def scan_market(client: BitkubClient, strategy, tickers: dict, asset_balances: dict) -> tuple:
    # DB-awareness is learning/telemetry only. A temporary Supabase outage must
    # not stop the core market scanner or live trading.
    if config.LEARNING_ENABLED:
        try:
            if not learning.db.refresh(force=True):
                print("[DB-AWARENESS][WARN] PostgreSQL schema unavailable; continuing without DB learning")
        except Exception as exc:
            print(f"[DB-AWARENESS][WARN] Schema check failed: {exc}; continuing without DB learning")

    symbols = get_scan_symbols(client, tickers)
    buy_candidates = []
    sell_candidates = []
    prices_by_coin = {}
    df_15m_by_coin = {}

    print(f"Scanning {len(symbols)} THB pairs...")
    for symbol in symbols:
        adx = 0.0  # กันปัญหา UnboundLocalError: ให้ adx มีค่าเริ่มต้นเสมอในทุก branch ของลูป
        ticker = tickers.get(symbol_to_ticker_key(symbol), {})
        held_position = asset_balances.get(base_coin(symbol), 0.0) > 0
        if is_trade_cooling_down(symbol) and not held_position:
            continue

        volume_thb = get_ticker_volume_thb(ticker)
        spread_rate = get_spread_rate(ticker)  # Ensure spread_rate defined early
        if volume_thb and volume_thb < config.MIN_24H_VOLUME_THB and not held_position:
            log_buy_rejection(symbol, "24h volume too low", spread=spread_rate, volume_ratio=None, adx=None, net_edge=None, score=None, expected=None, fee=None)
            continue

# spread_rate already obtained earlier
        if spread_rate > 0.02 and not held_position:
            log_buy_rejection(symbol, "spread > 2% hard limit", spread=spread_rate)
            continue
        if spread_rate > config.MAX_SPREAD_RATE and not held_position:
            log_buy_rejection(symbol, f"spread {spread_rate*100:.2f}% > MAX_SPREAD_RATE {config.MAX_SPREAD_RATE*100:.2f}%", spread=spread_rate)
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
        candles_5m = client.get_candles(symbol, resolution="5", limit=300)
        candles_15m = client.get_candles(symbol, resolution="15", limit=300)
        candles_1h = client.get_candles(symbol, resolution="60", limit=300)

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

        # DB is the bot's long-term memory. Every symbol follows the same lookup.
        learning_stats = (
            learning.get_symbol_learning(symbol)
            if config.LEARNING_ENABLED
            else {"symbol": symbol.upper(), "trade_count": 0, "win_rate": None, "avg_pnl_rate": None}
        )
        if learning_stats is None:
            print(f"[DB-AWARENESS][WARN] Learning query failed for {symbol}; using neutral learning data")
            learning_stats = {"symbol": symbol.upper(), "trade_count": 0, "win_rate": None, "avg_pnl_rate": None}
        learning_score = learning.calculate_learning_score(learning_stats)
        # Shadow-only use of settled historical outcomes: informative telemetry,
        # deliberately excluded from final_score and all order decisions.
        outcome_stats = (
            learning.get_prediction_outcome_stats(symbol)
            if config.LEARNING_ENABLED else None
        )
        if outcome_stats and outcome_stats.get("sample_count", 0) >= 5:
            avg15 = outcome_stats.get("avg_return_15m")
            avg60 = outcome_stats.get("avg_return_60m")
            win60 = outcome_stats.get("win_rate_60m")
            exec_avg60 = outcome_stats.get("executed_avg_return_60m")
            fmt15 = f"{avg15 * 100:.2f}%" if avg15 is not None else "N/A"
            fmt60 = f"{avg60 * 100:.2f}%" if avg60 is not None else "N/A"
            fmt_win60 = f"{win60 * 100:.1f}%" if win60 is not None else "N/A"
            fmt_exec60 = f"{exec_avg60 * 100:.2f}%" if exec_avg60 is not None else "N/A"
            print(
                f"[SHADOW-LEARNING] {symbol.upper()} "
                f"n={outcome_stats['sample_count']} "
                f"15m_avg={fmt15} 60m_avg={fmt60} 60m_win={fmt_win60} "
                f"executed={outcome_stats['executed_count']} "
                f"not_executed={outcome_stats['not_executed_count']} "
                f"executed_60m_avg={fmt_exec60}"
            )
        try:
            row = df_15m.iloc[-1].to_dict()
            row.update({"close": price, "spread_rate": spread_rate,
                        "bid": safe_float(ticker.get("highestBid")),
                        "ask": safe_float(ticker.get("lowestAsk")),
                        "regime": market_regime(df_15m.iloc[-1])})
            if config.LEARNING_ENABLED and not learning.snapshot(symbol, row):
                print(f"[DB-AWARENESS][WARN] Market snapshot write failed for {symbol}; continuing")
            if config.LEARNING_ENABLED:
                db_snapshot = learning.latest_market_snapshot(symbol)
                if db_snapshot is None:
                    print(f"[DB-AWARENESS][WARN] Latest market snapshot unavailable for {symbol}; continuing")
        except Exception as e:
            print(f"[DB-AWARENESS][WARN] snapshot validation failed {symbol}: {e}; continuing")

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
                # Exit filters are intentionally softer than BUY filters.
                # A confirmed bearish signal can exit a profitable position even
                # in sideways/low-volume conditions; profit-only protection remains mandatory.
                held_minutes = _position_held_minutes(coin)
                if held_minutes is not None and held_minutes < config.MIN_HOLD_MINUTES:
                    print(f"[{coin}] Skip scan_market sell: held {held_minutes:.1f}m < min {config.MIN_HOLD_MINUTES}m")
                else:
                        reason = f"technical sell ({', '.join(active_reasons)})"
                        amount = asset_balances[coin]
                        value_thb = amount * price

                        entry_price = position_state.get(coin, {}).get("entry_price") or price
                        pnl_rate = (price / entry_price) - 1 if entry_price > 0 else 0.0
                        net_profit, break_even_price = calculate_net_sell_profit(coin, amount, price)
                        required_rate = HOLD_UNTIL_SELL_SIGNAL_MIN_NET_RATE if HOLD_UNTIL_SELL_SIGNAL else config.SELL_MIN_NET_PROFIT_RATE
                        cost_basis = position_cost_for_amount(coin, amount)
                        allowed_profit, protected_profit, net_rate, min_required_price = profit_only_sell_check(
                            cost_basis, price, amount, buy_fee=0.0, sell_fee_rate=config.TRADING_FEE_RATE
                        )

                        if not allowed_profit:
                            print(
                                f"[{coin}] HOLD: net profit target not reached | "
                                f"Net={net_rate*100:.2f}% | Required={required_rate*100:.2f}% | "
                                f"MinSell={min_required_price:.8f} | BreakEven={break_even_price:.8f}"
                            )
                        else:
                            print(f"[{coin}] 🔔 SELL ({sell_signal_count}/3 signals): {active_reasons} | Net={net_rate*100:.2f}%")
                            learning.decision(
                                symbol, "SELL", price=price, score=learning_score,
                                min_score=config.FINAL_MIN_BUY_SCORE,
                                expected=pnl_rate, required=required_rate, net_edge=net_rate,
                                reason=f"SELL: technical exit confirmed; learning={learning_score}",
                                stats=learning_stats,
                            )
                            sell_candidates.append({
                                "symbol": symbol, "coin": coin, "price": price, "signal": "SELL",
                                "amount": amount, "value_thb": value_thb,
                                "expected_rate": pnl_rate, "required_rate": required_rate,
                                "net_rate": net_rate, "reason": reason,
                                "learning_score": learning_score,
                                "learning_trade_count": learning_stats.get("trade_count", 0),
                                "learning_win_rate": learning_stats.get("win_rate"),
                                "learning_avg_pnl_rate": learning_stats.get("avg_pnl_rate"),
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

        # V10: large-timeframe trend + order-book quality
        ema200_1h = safe_float(df_1h["ema_200"].iloc[-1])
        if config.REQUIRE_EMA200_1H and (ema200_1h <= 0 or close_1h <= ema200_1h):
            log_buy_rejection(symbol, "1H EMA200 trend filter", spread=spread_rate, adx=adx_15m)
            continue

        book = get_orderbook_metrics(client, symbol)
        if config.ORDERBOOK_FILTER_ENABLED and book["available"]:
            if (book["bid_depth_thb"] + book["ask_depth_thb"]) < config.ORDERBOOK_MIN_DEPTH_THB:
                log_buy_rejection(symbol, "orderbook depth too thin", spread=spread_rate, adx=adx_15m)
                continue
            if book["imbalance"] < config.ORDERBOOK_MIN_IMBALANCE:
                log_buy_rejection(symbol, f"orderbook imbalance {book['imbalance']:.2f}", spread=spread_rate, adx=adx_15m)
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

        # EMA50 > EMA200 (+2) and 1H EMA200 alignment (+2)
        if row_now.get("ema_50", 0) > row_now.get("ema_200", 0):
            new_score += 2
        if close_1h > ema200_1h:
            new_score += 2
        if book["available"] and book["imbalance"] >= 0.10:
            new_score += 1
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
        technical_score = int(max(0, min(100, new_score * 10)))
        final_score = technical_score + learning_score
        print(
            f"[{symbol.upper()}] SCORE: Technical={technical_score} | "
            f"Learning={learning_score} | Final={final_score} | "
            f"Trades={learning_stats.get('trade_count', 0)} | "
            f"WinRate={learning_stats.get('win_rate')} | "
            f"AvgPNL={learning_stats.get('avg_pnl_rate')} | "
            f"NetEdge={edge.get('net_rate', 0.0)*100:.2f}%"
        )

        # Final decision = Technical Score + DB Learning Score.
        # Keep the existing technical gate as a safety precondition, then use
        # the new 0-100 final threshold for the actual BUY decision.
        technical_gate = config.MIN_BUY_SCORE * 10.0
        if technical_score < technical_gate:
            learning.decision(
                symbol, "HOLD", price=price, score=final_score,
                min_score=config.FINAL_MIN_BUY_SCORE,
                expected=edge.get('expected_rate'),
                required=edge.get('required_rate'),
                net_edge=edge.get('net_rate'),
                reason=f"technical score {technical_score} < technical gate {technical_gate:.0f}; learning={learning_score}",
                stats=learning_stats,
            )
            log_buy_rejection(symbol, "technical score below gate", spread=spread_rate,
                              net_edge=edge.get('net_rate'), score=final_score,
                              expected=edge.get('expected_rate'), fee=config.TRADING_FEE_RATE*2)
            continue

        if config.V7_ENTRY_GATE_ENABLED:
            v7_market_status = get_v7_market_regime(client)
            v7_gate = v7_entry_gate.calculate_v7_score(
                df_5m, df_15m, market_status=v7_market_status,
            )
            print(
                f"[{symbol.upper()}] V7 BUY Gate: "
                f"score={v7_gate['score']}/{config.V7_ENTRY_GATE_MIN_SCORE}, "
                f"market={v7_market_status}, "
                f"reasons={', '.join(v7_gate.get('reasons', [])) or 'none'}"
            )
            if v7_gate["score"] < config.V7_ENTRY_GATE_MIN_SCORE:
                learning.decision(
                    symbol, "HOLD", price=price, score=final_score,
                    min_score=config.FINAL_MIN_BUY_SCORE,
                    expected=edge.get('expected_rate'), required=edge.get('required_rate'),
                    net_edge=edge.get('net_rate'),
                    reason=f"V7 gate failed; technical={technical_score}, learning={learning_score}, final={final_score}",
                    stats=learning_stats,
                )
                log_buy_rejection(symbol, f"V7 Ultimate gate failed ({v7_gate['score']}/{config.V7_ENTRY_GATE_MIN_SCORE})",
                                  spread=spread_rate, adx=adx, net_edge=edge.get('net_rate'),
                                  score=final_score, expected=edge.get('expected_rate'), fee=config.TRADING_FEE_RATE*2)
                continue
        else:
            v7_gate = {}

        # Anti-chase safety gate: strategy may still signal BUY, but we refuse
        # entries that arrive after a fast spike or at an overheated price.
        anti_chase_ok, anti_chase_reason = anti_chase_check(df_5m, df_15m, price)
        if not anti_chase_ok:
            learning.decision(
                symbol, "HOLD", price=price, score=final_score,
                min_score=config.FINAL_MIN_BUY_SCORE,
                expected=edge.get('expected_rate'), required=edge.get('required_rate'),
                net_edge=edge.get('net_rate'),
                reason=f"anti-chase blocked: {anti_chase_reason}",
                stats=learning_stats,
            )
            log_buy_rejection(symbol, f"anti-chase: {anti_chase_reason}",
                              spread=spread_rate, adx=adx, net_edge=edge.get('net_rate'),
                              score=final_score, expected=edge.get('expected_rate'),
                              fee=config.TRADING_FEE_RATE*2)
            continue

        if final_score < config.FINAL_MIN_BUY_SCORE:
            learning.decision(
                symbol, "HOLD", price=price, score=final_score,
                min_score=config.FINAL_MIN_BUY_SCORE,
                expected=edge.get('expected_rate'), required=edge.get('required_rate'),
                net_edge=edge.get('net_rate'),
                reason=f"final score {final_score} < {config.FINAL_MIN_BUY_SCORE}; technical={technical_score}, learning={learning_score}",
                stats=learning_stats,
            )
            log_buy_rejection(symbol, f"final score {final_score} < {config.FINAL_MIN_BUY_SCORE}",
                              spread=spread_rate, net_edge=edge.get('net_rate'), score=final_score,
                              expected=edge.get('expected_rate'), fee=config.TRADING_FEE_RATE*2)
            continue

        if not config.ALLOW_ADD_TO_POSITION and asset_balances.get(coin, 0.0) > 0:
            log_buy_rejection(
                symbol,
                "existing position and add‑to‑position disabled",
                spread=spread_rate,
                net_edge=edge.get('net_rate'),
                score=final_score,
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
            "score": final_score,
            "technical_score": technical_score,
            "learning_score": learning_score,
            "learning_trade_count": learning_stats.get("trade_count", 0),
            "learning_win_rate": learning_stats.get("win_rate"),
            "learning_avg_pnl_rate": learning_stats.get("avg_pnl_rate"),
            "prediction_id": None,
            "orderbook_imbalance": book.get("imbalance", 0.0),
            "bid_depth_thb": book.get("bid_depth_thb", 0.0),
            "ask_depth_thb": book.get("ask_depth_thb", 0.0),
            "regime": market_regime(row_now),
            **edge,
        }

        # Re-estimate edge after initial checks
        edge = estimate_trade_edge(df_15m, "BUY", spread_rate)
        # Preserve the previously calculated score and update opportunity with new edge data
        opportunity.update(edge)

        # Final hard profitability gate.  Keep this check immediately before
        # any BUY execution path so scanner/AI candidates cannot bypass the
        # minimum edge requirement.  MIN_NET_EDGE has a 0.50% hard floor in
        # config.py for live-money safety.
        if edge["net_rate"] < config.MIN_NET_EDGE:
            log_buy_rejection(
                symbol,
                f"net edge {edge.get('net_rate', 0.0)*100:.2f}% < minimum {config.MIN_NET_EDGE*100:.2f}%",
                spread=spread_rate,
                net_edge=edge.get('net_rate'),
                score=final_score,
                expected=edge.get('expected_rate'),
                fee=config.TRADING_FEE_RATE * 2,
            )
            continue

        # Record a counterfactual prediction before execution.
        prediction_id = None
        if config.PREDICTION_LEARNING_ENABLED:
            try:
                prediction_id = learning.prediction(
                    symbol, price, edge.get("expected_rate"), score=final_score,
                    net_edge=edge.get("net_rate"),
                    v7_score=v7_gate.get("score") if 'v7_gate' in locals() else None,
                    learning_stats=learning_stats, executed=False
                )
            except Exception as e:
                print(f"[Supabase] prediction log failed {symbol}: {e}")
        opportunity["prediction_id"] = prediction_id

        if config.GEMINI_ENABLED:
            print(f"[🤖 AI Filter] Requesting Gemini validation for {symbol.upper()}...")
            gemini_signal, reason = get_gemini_decision(df_15m, "BUY", symbol)
            print(f"[🤖 AI Response] Gemini decision: {gemini_signal}. Reason: {reason}")
            if gemini_signal != "BUY":
                print(f"Skip {symbol.upper()} BUY: Not confirmed by Gemini")
                continue
            opportunity["reason"] = f"Gemini: {reason}"

        learning.decision(
            symbol, "BUY", price=price, score=final_score,
            min_score=config.FINAL_MIN_BUY_SCORE,
            expected=edge.get('expected_rate'), required=edge.get('required_rate'),
            net_edge=edge.get('net_rate'),
            reason=f"BUY: technical={technical_score} + learning={learning_score} = final={final_score}",
            stats=learning_stats,
        )
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



def _hold_minutes_from_state(state):
    try:
        opened = state.get("opened_at")
        if not opened:
            return None
        dt = datetime.fromisoformat(str(opened).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max((datetime.now(timezone.utc) - dt).total_seconds() / 60.0, 0.0)
    except Exception:
        return None


def _update_learning_position_metrics(coin, price, state):
    """Update highest/max-profit/trailing telemetry in state.json and Supabase."""
    if not isinstance(state, dict) or price <= 0:
        return
    entry = safe_float(state.get("entry_price", price)) or price
    opened = state.get("opened_at")
    held = _hold_minutes_from_state(state) or 0.0
    highest = max(safe_float(state.get("highest_price", price)) or price, price)
    max_profit_pct = (highest / entry - 1.0) if entry > 0 else 0.0
    if max_profit_pct > safe_float(state.get("max_profit_pct", -999.0)):
        state["max_profit_pct"] = max_profit_pct
        state["max_profit_minutes"] = held
    state["highest_price"] = highest

    # Learned trailing is a safety net only; technical sell signals remain primary.
    learned = None
    try:
        learned = learning.get_exit_learning(f"{coin}_THB") if config.LEARNING_ENABLED else None
    except Exception:
        learned = None
    trail_rate = config.TRAILING_STOP_RATE
    if learned and learned.get("trade_count", 0) >= 10:
        avg_giveback = safe_float(learned.get("avg_peak_to_sell_pct"), 0.0) or 0.0
        if avg_giveback > 0:
            trail_rate = min(max(trail_rate, avg_giveback * 0.80), 0.03)
    if max_profit_pct >= config.TRAILING_MIN_PROFIT_RATE:
        state["trailing_stop"] = highest * (1.0 - trail_rate)
    else:
        state["trailing_stop"] = None

    if state.get("supabase_position_id") and config.LEARNING_ENABLED:
        try:
            learning.update_position_metrics(
                state["supabase_position_id"], highest_price=highest,
                max_profit_pct=max_profit_pct, trailing_stop=state.get("trailing_stop"),
                max_profit_minutes=state.get("max_profit_minutes"),
            )
        except Exception as exc:
            print(f"[Supabase] position metrics update failed {coin}: {exc}")

def _learning_open_position(opportunity, entry_price, amount, buy_value, buy_fee=0.0):
    if not config.LEARNING_ENABLED:
        return None
    if config.DRY_RUN and not getattr(config, "LEARNING_INCLUDE_DRY_RUN", True):
        return None
    try:
        return learning.open_position(
            opportunity["symbol"],
            entry_price,
            amount,
            buy_value,
            buy_fee=buy_fee,
            entry_score=opportunity.get("score"),
            entry_net_edge=opportunity.get("net_rate"),
            reason="DRY_RUN" if config.DRY_RUN else "LIVE",
        )
    except Exception as e:
        print(f"[Supabase] open position failed: {e}")
        return None


def _learning_close_position(state, opportunity, exit_price, amount, sell_value,
                             sell_fee, pnl, pnl_rate, reason):
    if not config.LEARNING_ENABLED:
        return
    if config.DRY_RUN and not getattr(config, "LEARNING_INCLUDE_DRY_RUN", True):
        return
    try:
        position_id = state.get("supabase_position_id") if isinstance(state, dict) else None
        if position_id:
            # Profit-runner telemetry: record drawdown from the peak at exit.
            peak_to_sell_pct = 0.0
            highest_price = safe_float(state.get("highest_price", exit_price), exit_price)
            if highest_price > 0 and exit_price > 0:
                peak_to_sell_pct = max(1.0 - (exit_price / highest_price), 0.0)
            learning.close_position(
                position_id,
                exit_price,
                sell_value,
                sell_fee,
                pnl,
                pnl_rate,
                _hold_minutes_from_state(state),
                reason=reason,
            )
        else:
            # Fallback for positions opened before this patch.
            learning.trade(
                opportunity["symbol"],
                "SELL",
                state.get("entry_price", exit_price),
                exit_price,
                amount,
                state.get("cost_thb", 0.0),
                0.0,
                sell_value,
                sell_fee,
                pnl,
                pnl_rate,
                _hold_minutes_from_state(state),
                reason,
                "CLOSED",
            )
    except Exception as e:
        print(f"[Supabase] close position failed: {e}")


def execute_sell(client, notifier, opportunity):
    global paper_thb, paper_balances, consecutive_losses

    amount = opportunity["amount"]
    value_thb = opportunity["value_thb"]
    is_partial = opportunity.get("is_partial", False)
    coin = opportunity["coin"]
    reason = opportunity.get("reason", "unknown")

    # Live sell safety: require a recorded cost basis and re-check the live bid.
    # A last-traded ticker price is not a safe execution price for a market sell.
    state_for_sell = position_state.get(coin, {})
    tracked_amount = safe_float(state_for_sell.get("initial_amount", 0.0))
    tracked_cost = safe_float(state_for_sell.get("cost_thb", 0.0))
    if not config.DRY_RUN:
        if state_for_sell.get("cost_basis_verified") is False or tracked_amount <= 0 or tracked_cost <= 0:
            message = (
                f"[🛑 SELL BLOCKED] {coin}: cost basis is not verified "
                f"(tracked_amount={tracked_amount:.8f}, tracked_cost={tracked_cost:.4f}). "
                "Reconcile the actual BUY fill before selling."
            )
            print(message)
            notify_trade(notifier, "[LIVE] Sell blocked: cost basis unknown", opportunity, message)
            return
        if amount > tracked_amount * 1.000001:
            message = (
                f"[🛑 SELL BLOCKED] {coin}: requested amount {amount:.8f} exceeds "
                f"tracked position {tracked_amount:.8f}; refusing unsafe cost allocation."
            )
            print(message)
            notify_trade(notifier, "[LIVE] Sell blocked: quantity mismatch", opportunity, message)
            return
        try:
            live_ticker = client.get_ticker(opportunity["symbol"])
            live_bid = safe_float(live_ticker.get("highestBid")) or safe_float(live_ticker.get("highest_bid"))
        except Exception as exc:
            live_bid = 0.0
            print(f"[🛑 SELL BLOCKED] {coin}: could not read live bid: {exc}")
        if live_bid <= 0:
            message = f"[🛑 SELL BLOCKED] {coin}: no valid live bid; no order submitted."
            print(message)
            notify_trade(notifier, "[LIVE] Sell blocked: no live bid", opportunity, message)
            return
        opportunity["price"] = live_bid
        value_thb = amount * live_bid

    # Final safety gate: never submit a sell below the configured NET profit target.
    cost_basis = position_cost_for_amount(coin, amount)
    if cost_basis <= 0:
        print(f"[🛑 SELL BLOCKED] {coin}: cost basis calculation returned {cost_basis:.4f} THB")
        return
    allowed_profit, protected_profit, net_rate_check, min_required_price = profit_only_sell_check(
        cost_basis, opportunity["price"], amount, buy_fee=0.0, sell_fee_rate=config.TRADING_FEE_RATE
    )
    if not allowed_profit:
        print(
            f"[🛑 SELL BLOCKED] {coin}: net={net_rate_check*100:.2f}% "
            f"< required={(HOLD_UNTIL_SELL_SIGNAL_MIN_NET_RATE if HOLD_UNTIL_SELL_SIGNAL else config.SELL_MIN_NET_PROFIT_RATE)*100:.2f}%. "
            f"Min sell price={min_required_price:.8f}"
        )
        return

    print(
        f"SELL {opportunity['symbol'].upper()} amount {amount:.8f}, "
        f"value about {value_thb:,.2f} THB, PnL {format_rate(opportunity['expected_rate'])}, "
        f"is_partial={is_partial}, reason={reason}"
    )

    if config.DRY_RUN:
        fee = value_thb * config.TRADING_FEE_RATE
        net_received = value_thb - fee
        paper_thb += net_received

        state = position_state.get(coin, {})
        entry_price = state.get("entry_price") or opportunity["price"]
        net_profit, _ = calculate_net_sell_profit(coin, amount, opportunity["price"])
        state = position_state.get(coin, {})
        cost_basis_for_learning = safe_float(state.get("cost_thb", 0.0))
        pnl_rate_for_learning = (net_profit / cost_basis_for_learning) if cost_basis_for_learning > 0 else 0.0
        _learning_close_position(
            state, opportunity, opportunity["price"], amount, value_thb, fee,
            net_profit, pnl_rate_for_learning, reason
        )

        # All exits are full exits and already passed the profit-only gate.
        consecutive_losses = 0
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
    # Profit-only protection cannot guarantee the fill price of a market order.
    # Use a limit order at/above the computed net-profit floor; an unfilled order
    # leaves the position open instead of risking a loss on a fast-moving bid.
    if PROFIT_ONLY_SELL_ENABLED:
        current_order_type = "limit"
        current_post_only = False
        opportunity["price"] = max(safe_float(opportunity["price"]), min_required_price)

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

        # A successful place-ask response means the order was accepted, not
        # necessarily filled. This matters for real-money Profit Vault accounting.
        # For limit orders, verify the final order status before closing the position.
        result = order_res.get("result") or {}
        order_id = result.get("id") or result.get("order_id")
        order_status = "filled" if current_order_type == "market" else None
        order_info = {}
        if order_id and current_order_type != "market":
            time.sleep(1)
            order_info = client.get_order_info(opportunity["symbol"], str(order_id), "sell")
            if order_info.get("error") == 0:
                info = order_info.get("result") or {}
                order_status = str(info.get("status") or "").lower()
                print(f"[SELL VERIFY] order={order_id} status={order_status} filled={info.get('filled')} remaining={info.get('remaining')}")
            else:
                # Unknown fill state: do not book profit or withdraw money.
                print(f"[🛑 SELL VERIFY] Could not verify order {order_id}; leaving position state intact: {order_info.get('message') or order_info.get('error')}")
                notify_trade(notifier, "[LIVE] Sell pending verification", opportunity, f"Order {order_id} accepted but fill status is unknown. No profit was locked.")
                return

        if order_status not in (None, "filled"):
            print(f"[⏳ SELL NOT FILLED] {opportunity['symbol'].upper()} order={order_id} status={order_status}")
            if PROFIT_ONLY_SELL_ENABLED:
                # Never convert a protected limit exit into a market sell: the
                # bid can move between this check and the exchange matching engine.
                notify_trade(
                    notifier, "[LIVE] Sell not filled", opportunity,
                    f"Order {order_id} status={order_status}. Protected limit remains the only allowed exit; no market retry."
                )
                return
            # Legacy fallback only applies when profit-only protection is disabled.
            try:
                if order_id:
                    cancel_res = client.cancel_order(opportunity["symbol"], str(order_id), "sell")
                    print(f"[SELL FALLBACK] cancel limit order={order_id}: {cancel_res}")
                live_ticker = client.get_ticker(opportunity["symbol"])
                live_price = safe_float(live_ticker.get("highestBid")) or safe_float(live_ticker.get("last"))
                allowed_live, _, live_net_rate, live_min_price = profit_only_sell_check(
                    cost_basis, live_price, amount, buy_fee=0.0, sell_fee_rate=config.TRADING_FEE_RATE
                )
                if allowed_live:
                    print(f"[SELL FALLBACK] retry MARKET at bid={live_price:.8f} net={live_net_rate*100:.2f}%")
                    retry = client.place_ask(
                        symbol=opportunity["symbol"], amount=amount, rate=0.0,
                        order_type="market", post_only=False,
                    )
                    if retry.get("error") == 0:
                        order_res = retry
                        result = retry.get("result") or {}
                        order_id = result.get("id") or result.get("order_id")
                        order_info = {"result": result}
                        order_status = "filled"
                        opportunity["price"] = live_price
                    else:
                        print(f"[SELL FALLBACK] market retry rejected: {retry}")
                else:
                    print(f"[SELL FALLBACK] blocked: live bid {live_price:.8f} below profit-only minimum {live_min_price:.8f}")
            except Exception as fallback_exc:
                print(f"[SELL FALLBACK] failed: {fallback_exc}")
            if order_status not in (None, "filled"):
                notify_trade(notifier, "[LIVE] Sell not filled", opportunity, f"Order {order_id} status={order_status}. Position remains protected; no profit locked.")
                return

        # Exact PnL after both sides of fees. Prefer Bitkub's actual matched
        # receive/fee values when available; fall back to the existing estimate.
        state = position_state.get(coin, {})
        entry_price = state.get("entry_price") or opportunity["price"]
        net_profit, _ = calculate_net_sell_profit(coin, amount, opportunity["price"])
        actual_result = (order_info.get("result") if isinstance(order_info, dict) else None) or result
        actual_receive = safe_float(actual_result.get("receive") or actual_result.get("rec"), 0.0)
        actual_fee = safe_float(actual_result.get("fee"), 0.0)
        cost_basis_actual = position_cost_for_amount(coin, amount)
        if actual_receive > 0 and cost_basis_actual > 0:
            net_profit = actual_receive - cost_basis_actual
            print(f"[💰 Actual SELL PnL] receive={actual_receive:,.4f} | fee={actual_fee:,.4f} | cost={cost_basis_actual:,.4f} | net={net_profit:,.4f} THB")
        pnl_rate_for_learning = (net_profit / safe_float(state.get("cost_thb", 0.0))) if safe_float(state.get("cost_thb", 0.0)) > 0 else 0.0
        _learning_close_position(
            state, opportunity, opportunity["price"], amount, value_thb,
            actual_fee if actual_fee > 0 else value_thb * config.TRADING_FEE_RATE, net_profit,
            pnl_rate_for_learning, reason
        )
        try:
            learning.order(
                state.get("supabase_position_id"),
                opportunity["symbol"], "SELL",
                order_id=(order_res.get("result") or {}).get("id") if isinstance(order_res.get("result"), dict) else None,
                order_type=current_order_type,
                requested_price=opportunity["price"],
                filled_price=opportunity["price"],
                requested_amount=amount,
                filled_amount=amount,
                gross_value_thb=value_thb,
                fee_thb=actual_fee if actual_fee > 0 else value_thb * config.TRADING_FEE_RATE,
                net_value_thb=actual_receive if actual_receive > 0 else value_thb * (1 - config.TRADING_FEE_RATE),
                status="FILLED",
                raw_result=order_res.get("result"),
            )
        except Exception as e:
            print(f"[Supabase] sell order log failed: {e}")

        consecutive_losses = 0
        # Keep residual inventory and its proportional cost basis if this sell
        # only disposed of part of the tracked position (e.g. reserved balance).
        state_after_sell = position_state.get(coin, {})
        tracked_after_sell = safe_float(state_after_sell.get("initial_amount", 0.0))
        total_cost_after_sell = safe_float(state_after_sell.get("cost_thb", 0.0))
        remaining_amount = max(tracked_after_sell - amount, 0.0)
        remaining_cost = max(total_cost_after_sell - cost_basis, 0.0)
        dust_threshold = max(1e-8, tracked_after_sell * 1e-6)
        if remaining_amount > dust_threshold and remaining_cost > 0:
            state_after_sell["initial_amount"] = remaining_amount
            state_after_sell["cost_thb"] = remaining_cost
            print(
                f"[POSITION PARTIAL EXIT] {coin}: sold={amount:.8f}, "
                f"remaining={remaining_amount:.8f}, remaining_cost={remaining_cost:.4f} THB"
            )
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

        # Profit Vault: only realized NET profit from a successful live SELL is eligible.
        # This is deliberately after the existing SELL/learning bookkeeping so the
        # trading engine remains unchanged; the vault is a separate money-control layer.
        if config.PROFIT_VAULT_ENABLED and net_profit > 0:
            try:
                order_id_for_vault = (order_res.get("result") or {}).get("id") if isinstance(order_res.get("result"), dict) else None
                trade_ref = str(order_id_for_vault or state.get("supabase_position_id") or f"{opportunity['symbol']}:{datetime.now(timezone.utc).isoformat()}")
                vault_result = profit_vault.record_realized_profit(
                    symbol=opportunity["symbol"],
                    position_id=state.get("supabase_position_id"),
                    trade_ref=trade_ref,
                    net_profit=net_profit,
                )
                print(f"[🔒 Profit Vault] Profit={net_profit:,.4f} | Locked={vault_result.get('locked', 0):,.4f} | Compound={vault_result.get('compound', 0):,.4f} | Vault={vault_result.get('vault_balance', profit_vault.balance()):,.4f}")
                if vault_result.get("ok") and not vault_result.get("duplicate"):
                    withdrawal = profit_vault.withdraw_if_ready(client)
                    if withdrawal.get("withdrawn"):
                        print(f"[🏦 AUTO WITHDRAW] Amount={withdrawal.get('amount', 0):,.2f} | Fee={withdrawal.get('fee', 0):,.2f} | Received={withdrawal.get('received_amount', 0):,.2f} | TX={withdrawal.get('withdrawal_id')}")
                    elif withdrawal.get("reason") not in ("below_threshold", "auto_withdraw_disabled"):
                        print(f"[🏦 AUTO WITHDRAW] Not executed: {withdrawal.get('reason')}")
            except Exception as e:
                print(f"[ProfitVault][ERROR] {e}")
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


def _is_broker_source_symbol(client, symbol):
    """Return True when Bitkub marks a symbol as source=broker.

    Bitkub error 61 explicitly means place-bid/place-ask does not support
    broker-source coins, so retrying the same order is pointless.
    """
    try:
        symbols_res = client.get_symbols()
        if symbols_res.get("error") not in (None, 0):
            return False
        rows = symbols_res.get("result", [])
        if isinstance(rows, dict):
            rows = rows.get("symbols", rows.get("data", []))
        target = symbol.lower()
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or row.get("sym") or "").lower()
            if sym == target:
                return str(row.get("source", "")).lower() == "broker"
        return False
    except Exception as e:
        print(f"[Warning] Could not check symbol source for {symbol}: {e}")
        return False


def execute_buy(client, notifier, opportunity, thb_balance, total_value):
    global paper_thb, paper_balances, last_buy_date

    # Last-mile profitability guard: every BUY caller (normal scanner, AI
    # fallback, stale candidate, etc.) must meet the same minimum edge.
    # This is deliberately fail-closed for live money.
    opportunity_net_edge = safe_float(opportunity.get("net_rate"), -1.0)
    if opportunity_net_edge < config.MIN_NET_EDGE:
        print(
            f"[🛑 BUY BLOCKED] {opportunity.get('symbol', 'UNKNOWN').upper()}: "
            f"NetEdge={opportunity_net_edge*100:.2f}% < "
            f"minimum={config.MIN_NET_EDGE*100:.2f}%"
        )
        try:
            learning.decision(
                opportunity.get("symbol", "UNKNOWN"), "HOLD",
                price=safe_float(opportunity.get("price")),
                score=opportunity.get("score"),
                min_score=config.FINAL_MIN_BUY_SCORE,
                expected=opportunity.get("expected_rate"),
                required=opportunity.get("required_rate"),
                net_edge=opportunity_net_edge,
                reason=(f"final BUY net-edge guard: "
                        f"{opportunity_net_edge*100:.2f}% < "
                        f"{config.MIN_NET_EDGE*100:.2f}%"),
                stats=None,
            )
        except Exception:
            pass
        return

    # Final safety guard: broker-source coins cannot be submitted to place-bid.
    # Normal scans filter them earlier, so this should only trigger if a stale
    # candidate was queued before the symbol metadata changed.
    if not config.DRY_RUN and client is not None and _is_broker_source_symbol(client, opportunity["symbol"]):
        print(
            f"[Broker filter] Skipped unsupported broker-source pair "
            f"{opportunity['symbol'].upper()} (Bitkub Error 61)."
        )
        return

    # Refresh the cash balance immediately before placing a live buy so the
    # order size reflects the actual THB available at that moment.
    if not config.DRY_RUN and client is not None:
        try:
            fresh_balances = client.get_balances()
            if fresh_balances.get("error") == 0:
                fresh_result = fresh_balances.get("result", {})
                fresh_thb = safe_float(
                    fresh_result.get("THB", {}).get("available")
                    if isinstance(fresh_result.get("THB"), dict)
                    else fresh_result.get("THB", 0.0)
                )
                if fresh_thb >= 0:
                    locked_vault = profit_vault.balance() if config.PROFIT_VAULT_ENABLED else 0.0
                    thb_balance = max(fresh_thb - locked_vault, 0.0)
                    print(f"[💰 Fresh balance] THB จริง: {fresh_thb:,.2f} | 🔒 Locked Profit: {locked_vault:,.2f} | THB ใช้เทรดได้: {thb_balance:,.2f}")
        except Exception as e:
            print(f"[⚠️ Fresh balance] อ่านยอด THB ล่าสุดไม่สำเร็จ ใช้ยอดจากรอบสแกน: {e}")

    # Final live-price anti-chase check immediately before the real BUY.
    # This closes the race where the scanner saw a good price but the market
    # moved sharply upward during the scan/decision/API latency.
    if not config.DRY_RUN and client is not None and getattr(config, "ANTI_CHASE_ENABLED", True):
        try:
            live_ticker = client.get_ticker(opportunity["symbol"])
            if live_ticker.get("error", 0) != 0:
                print(f"[ANTI-CHASE] BUY blocked: live ticker unavailable for {opportunity['symbol']}")
                return
            live_last = safe_float(live_ticker.get("last"))
            live_ask = safe_float(live_ticker.get("lowestAsk"), live_last)
            live_price = live_ask if live_ask > 0 else live_last
            reference_price = safe_float(opportunity.get("price"))
            if live_price <= 0 or reference_price <= 0:
                print(f"[ANTI-CHASE] BUY blocked: invalid live/reference price for {opportunity['symbol']}")
                return
            drift = (live_price / reference_price) - 1.0
            if drift > config.ENTRY_MAX_LIVE_PRICE_DRIFT_RATE:
                reason = (f"live price moved +{drift*100:.2f}% from scan price "
                          f"({reference_price:.8f} -> {live_price:.8f})")
                print(f"[ANTI-CHASE] BUY BLOCKED {opportunity['symbol'].upper()}: {reason}")
                try:
                    learning.decision(
                        opportunity["symbol"], "HOLD", price=live_price,
                        score=opportunity.get("score"),
                        min_score=config.FINAL_MIN_BUY_SCORE,
                        expected=opportunity.get("expected_rate"),
                        required=opportunity.get("required_rate"),
                        net_edge=opportunity.get("net_rate"),
                        reason=f"final live anti-chase: {reason}", stats=None,
                    )
                except Exception:
                    pass
                return
            # For market orders use the current ask as the effective reference
            # for the order, not the stale scanner price.
            if current_order_type := getattr(config, "ORDER_TYPE_BUY", "market"):
                if current_order_type == "market":
                    opportunity["price"] = live_price
                    print(f"[ANTI-CHASE] Live price OK: {live_price:.8f} (+{max(drift,0)*100:.2f}% from scan)")
        except Exception as e:
            print(f"[ANTI-CHASE] BUY blocked: final live-price check failed: {e}")
            return

    amount_thb = calculate_position_size(thb_balance, total_value)
    estimated_coin = amount_thb / opportunity["price"]
    entry_price = opportunity["price"]
    atr = opportunity.get("atr", 0.0)

    print(
        f"BUY {opportunity['symbol'].upper()} using {amount_thb:,.2f} THB, "
        f"estimated coin {estimated_coin:.8f}, net edge {format_rate(opportunity['net_rate'])}\n"
        f"  Exit rule: SELL only when net proceeds > total cost after fees"
    )

    if amount_thb < config.MIN_TRADE_VALUE_THB:
        print("Skip buy: THB balance is below minimum trade value")
        return

    if config.DRY_RUN:
        fee = amount_thb * config.TRADING_FEE_RATE
        net_buy_thb = amount_thb - fee
        paper_thb -= amount_thb
        coin_bought = net_buy_thb / entry_price
        # Keep paper-trading position fields consistent with the LIVE path.
        # These variables were previously referenced before assignment.
        new_amount = coin_bought
        actual_cost_thb = amount_thb
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
            prev["cost_thb"] = safe_float(prev.get("cost_thb", 0.0)) + actual_cost_thb
        else:
            # New position
            position_state[opportunity["coin"]] = {
                "entry_price": entry_price,
                "highest_price": entry_price,
                "atr_at_entry": atr,
                "initial_amount": new_amount,
                "cost_thb": actual_cost_thb,
                "cost_basis_verified": True,
                "cost_basis_source": "buy_execution",
                "tp1_sold": False,
                "tp2_sold": False,
                "opened_at": datetime.now().isoformat(timespec="seconds"),
            }

        state_ref = position_state.get(opportunity["coin"], {})
        if not state_ref.get("supabase_position_id"):
            state_ref["supabase_position_id"] = _learning_open_position(
                opportunity, entry_price, state_ref.get("initial_amount", coin_bought),
                state_ref.get("cost_thb", amount_thb), buy_fee=fee
            )
        server_day, _ = get_server_day_and_time(client)
        last_buy_date = server_day
        save_state()
        mark_traded(opportunity["symbol"])
        detail = f"Dry run bought. All available THB used. Score: {opportunity.get('score', 0)}"
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
        notify_trade(notifier, "[LIVE] Buy skipped", opportunity, detail)
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
            notify_trade(notifier, "[LIVE] Buy failed", opportunity, detail)
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
        # A market-order acknowledgement can return rat=0 and no fill price.
        # Query Bitkub's read-only order-info endpoint before falling back to
        # the scan-time ticker, so recorded entry cost is as close to the fill
        # as the exchange API allows. This does not place or retry any order.
        info_result = {}
        if order_id:
            try:
                order_info_response = client.get_order_info(opportunity["symbol"], order_id, "buy")
                if isinstance(order_info_response, dict) and order_info_response.get("error") == 0:
                    candidate_info = order_info_response.get("result", {})
                    if isinstance(candidate_info, dict):
                        info_result = candidate_info
                        print(
                            f"[BUY fill info] id={order_id} | "
                            f"status={info_result.get('status', 'unknown')} | "
                            f"filled={info_result.get('filled', 'n/a')} | "
                            f"history_fills={len(info_result.get('history', [])) if isinstance(info_result.get('history', []), list) else 0}"
                        )
                else:
                    print(f"[BUY fill info] Order-info unavailable: {order_info_response.get('message', 'unknown response') if isinstance(order_info_response, dict) else 'invalid response'}")
            except Exception as fill_exc:
                print(f"[BUY fill info] Order-info lookup failed: {fill_exc}")

        actual_price, received_amount, actual_cost_thb, price_from_history = extract_buy_fill_details(
            result, info_result, entry_price, amount_thb, config.TRADING_FEE_RATE
        )
        if actual_price > 0:
            entry_price = actual_price
            exchange_price_available = any(
                safe_float(candidate) > 0
                for candidate in (
                    result.get("rate"), result.get("rat"), result.get("avg_price"), result.get("avg"),
                    info_result.get("rate"), info_result.get("rat"), info_result.get("avg_price"), info_result.get("avg"),
                )
            )
            source = (
                "weighted order history" if price_from_history
                else "order response/info" if exchange_price_available
                else "ticker fallback (not exchange-confirmed)"
            )
            print(f"[🔥 Filled Price] {source}: {entry_price:,.8f} THB")
        else:
            print(
                f"[⚠️ BUY fill warning] No usable fill price; using ticker reference "
                f"{entry_price:,.8f} THB temporarily (reconcile against Bitkub order history)"
            )

        if actual_cost_thb <= 0:
            actual_cost_thb = amount_thb
        if received_amount > 0:
            new_amount = received_amount
        elif entry_price > 0:
            new_amount = (actual_cost_thb / entry_price) * (1 - config.TRADING_FEE_RATE)
        else:
            print("[BUY fill warning] Cannot calculate coin quantity; skipping position accounting")
            return

        server_day, _ = get_server_day_and_time(client)
        
        if config.ALLOW_ADD_TO_POSITION and opportunity["coin"] in position_state:
            prev = position_state[opportunity["coin"]]
            prev_amount = prev["initial_amount"]
            total_amount = prev_amount + new_amount
            weighted_entry = (prev["entry_price"] * prev_amount + entry_price * new_amount) / total_amount
            prev["entry_price"] = weighted_entry
            prev["highest_price"] = max(prev.get("highest_price", weighted_entry), entry_price)
            prev["initial_amount"] = total_amount
            prev["cost_thb"] = safe_float(prev.get("cost_thb", 0.0)) + actual_cost_thb
        else:
            position_state[opportunity["coin"]] = {
                "entry_price": entry_price,
                "highest_price": entry_price,
                "atr_at_entry": atr,
                "initial_amount": new_amount,
                "cost_thb": actual_cost_thb,
                "tp1_sold": False,
                "tp2_sold": False,
                "opened_at": datetime.now().isoformat(timespec="seconds"),
            }
        state_ref = position_state.get(opportunity["coin"], {})
        if not state_ref.get("supabase_position_id"):
            state_ref["supabase_position_id"] = _learning_open_position(
                opportunity, entry_price, state_ref.get("initial_amount", new_amount),
                state_ref.get("cost_thb", actual_cost_thb), buy_fee=actual_cost_thb * config.TRADING_FEE_RATE
            )
        try:
            if opportunity.get("prediction_id"):
                learning.mark_prediction_executed(opportunity.get("prediction_id"), order_id=order_id)
            learning.order(
                state_ref.get("supabase_position_id"),
                opportunity["symbol"], "BUY",
                order_id=order_id,
                order_type=current_order_type,
                requested_price=opportunity["price"],
                filled_price=entry_price,
                requested_amount=amount_thb,
                filled_amount=new_amount,
                gross_value_thb=actual_cost_thb,
                fee_thb=actual_cost_thb * config.TRADING_FEE_RATE,
                net_value_thb=actual_cost_thb - actual_cost_thb * config.TRADING_FEE_RATE,
                status="FILLED",
                raw_result=result,
            )
        except Exception as e:
            print(f"[Supabase] buy order log failed: {e}")

        last_buy_date = server_day
        save_state()
        mark_traded(opportunity["symbol"])
        detail = f"Live buy success: {order_res.get('result')}\nExit rule: profit after fees only"
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
        notify_trade(notifier, "[LIVE] Buy failed", opportunity, detail)
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
            if config.PREDICTION_LEARNING_ENABLED:
                learning.settle_predictions(current_prices)
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

            # No consecutive-loss pause: loss-making sells are disabled by design.
            # Keep the optional BTC regime filter as a separate safety switch.
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


def can_execute_profit_only_sell(entry_cost, sell_price, quantity, buy_fee=0.0, sell_fee_rate=0.0):
    """
    Final SELL gate. Call this immediately before the existing SELL API call.
    Existing strategy/risk/position logic remains unchanged.
    """
    allowed, net_profit, net_rate, min_required_price = profit_only_sell_check(
        entry_cost, sell_price, quantity, buy_fee, sell_fee_rate
    )
    if not allowed:
        try:
            logger.info(
                "SELL BLOCKED | net_profit=%.8f net_rate=%.4f%% min_price=%.8f",
                net_profit, net_rate * 100.0, min_required_price
            )
        except Exception:
            pass
    return allowed
