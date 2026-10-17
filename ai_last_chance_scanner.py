import time
import json
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

import config
from strategy import parse_candles_to_dataframe, calculate_indicators, calculate_score

# Global state for key rotation
current_key_index = 0
gemini_key_available_at = {}


def safe_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def base_coin(symbol: str) -> str:
    return symbol.split("_")[0].upper()


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
    required_rate = (config.TRADING_FEE_RATE * 2) + spread_rate
    return {
        "expected_rate": expected_rate,
        "required_rate": required_rate,
        "net_rate": expected_rate - required_rate,
    }


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
                gemini_key_available_at[key_index] = now + 300  # Cooldown 5 mins
            else:
                print(f"[⚠️ Gemini Error] HTTP {response.status_code}: {response.text}")
        except Exception as e:
            print(f"[⚠️ Gemini Exception] {e}")
            gemini_key_available_at[key_index] = now + 60

    return ""


def display_result(selected_pair, decision, main_reasons, confidence=None, expected_move=None, expected_net_profit=None, risk_score=None):
    print("=" * 50)
    print("  AI LAST CHANCE SCANNER RESULT")
    print("=" * 50)
    print(f"Selected Pair: {selected_pair if selected_pair else 'None'}")
    print(f"AI Confidence: {f'{confidence:.1f}%' if confidence is not None else 'N/A'}")
    print(f"Expected Move: {f'{expected_move:.2f}%' if expected_move is not None else 'N/A'}")
    print(f"Expected Net Profit: {f'{expected_net_profit:.2f}%' if expected_net_profit is not None else 'N/A'}")
    print(f"Risk Score: {f'{risk_score}/10' if risk_score is not None else 'N/A'}")
    print(f"Main Reasons: {main_reasons}")
    print(f"Decision: {decision}")
    print("=" * 50)


def run_last_chance_scan(client) -> dict:
    """
    Scans all available trading pairs (up to 30 by 24h volume) and analyzes them using Gemini AI.
    Returns:
        opportunity (dict) if decision is BUY and all thresholds are met, else None.
    """
    tickers = client.get_all_tickers()
    if not tickers:
        print("[⚠️ Scanner] Could not fetch tickers.")
        return None

    # Filter THB pairs
    thb_pairs = []
    for ticker_key, ticker in tickers.items():
        if not ticker_key.upper().startswith("THB_") or not isinstance(ticker, dict):
            continue
        
        # Calculate volume & spread
        volume = get_ticker_volume_thb(ticker)
        spread = get_spread_rate(ticker)
        
        # Apply pre-filter to reduce API load
        if volume < config.LAST_CHANCE_MIN_VOLUME_THB:
            continue
        if spread > config.LAST_CHANCE_MAX_SPREAD:
            continue
            
        # Convert e.g. THB_BTC to btc_thb
        parts = ticker_key.upper().split("_")
        symbol = f"{parts[1]}_THB".lower()
        thb_pairs.append((volume, symbol, ticker))

    # Sort by volume and limit to top 30
    thb_pairs.sort(reverse=True)
    thb_pairs = thb_pairs[:30]

    if not thb_pairs:
        print("[⚠️ Scanner] No pairs passed volume and spread filters.")
        display_result(None, "SKIP", "No pairs passed volume and spread pre-filters.")
        return None

    print(f"[🔍 Scanner] Collecting indicators for {len(thb_pairs)} candidate pairs...")
    candidates = []
    for _, symbol, ticker in thb_pairs:
        try:
            # Fetch candles
            candles_5m = client.get_candles(symbol, resolution="5", limit=100)
            candles_15m = client.get_candles(symbol, resolution="15", limit=100)
            candles_1h = client.get_candles(symbol, resolution="60", limit=100)

            df_5m = parse_candles_to_dataframe(candles_5m)
            df_15m = parse_candles_to_dataframe(candles_15m)
            df_1h = parse_candles_to_dataframe(candles_1h)

            if df_5m.empty or df_15m.empty or df_1h.empty:
                continue
            if len(df_5m) < 30 or len(df_15m) < 30 or len(df_1h) < 30:
                continue

            df_5m = calculate_indicators(df_5m)
            df_15m = calculate_indicators(df_15m)
            df_1h = calculate_indicators(df_1h)

            row_5m = df_5m.iloc[-1]
            row_15m = df_15m.iloc[-1]
            row_1h = df_1h.iloc[-1]

            # 1h trend
            trend_1h = "bullish" if (row_1h["ema_fast"] > row_1h["ema_slow"] and row_1h["close"] > row_1h["ema_50"]) else "bearish"
            # 15m trend
            trend_15m = "bullish" if (row_15m["ema_fast"] > row_15m["ema_slow"] and row_15m["close"] > row_15m["ema_50"]) else "bearish"
            # 5m trend
            trend_5m = "bullish" if (row_5m["ema_fast"] > row_5m["ema_slow"]) else "bearish"

            # Momentum
            rsi_15m = row_15m["rsi"]
            macd_hist_15m = row_15m["macd_hist"]

            # ADX
            adx_15m = row_15m["adx"]

            # Volatility
            atr_15m = row_15m["atr"]
            atr_ratio = atr_15m / row_15m["close"] if row_15m["close"] > 0 else 0.0

            # Volume spike
            vol_15m = row_15m["volume"]
            vol_ma_15m = row_15m["volume_ma"]
            vol_ratio = vol_15m / vol_ma_15m if vol_ma_15m > 0 else 1.0

            # Order Flow
            bid_ask_ratio = 1.0
            depth = client.get_depth(symbol, limit=10)
            bids = depth.get("bids", [])
            asks = depth.get("asks", [])
            if bids and asks:
                bid_vol = sum(safe_float(b[1]) for b in bids)
                ask_vol = sum(safe_float(a[1]) for a in asks)
                if ask_vol > 0:
                    bid_ask_ratio = bid_vol / ask_vol

            # Support & Resistance
            donchian_high = row_15m["donchian_high"]
            donchian_low = row_15m["donchian_low"]
            donchian_range = donchian_high - donchian_low
            donchian_pos = (row_15m["close"] - donchian_low) / donchian_range if donchian_range > 0 else 0.5

            spread = get_spread_rate(ticker)
            edge = estimate_trade_edge(df_15m, "BUY", spread)

            candidates.append({
                "symbol": symbol,
                "coin": base_coin(symbol),
                "price": row_15m["close"],
                "spread_rate": spread,
                "volume_24h_thb": get_ticker_volume_thb(ticker),
                "trend_1h": trend_1h,
                "trend_15m": trend_15m,
                "trend_5m": trend_5m,
                "rsi_15m": rsi_15m,
                "macd_hist_15m": macd_hist_15m,
                "adx_15m": adx_15m,
                "vol_ratio_15m": vol_ratio,
                "bid_ask_ratio": bid_ask_ratio,
                "atr_ratio": atr_ratio,
                "donchian_pos_15m": donchian_pos,
                "atr": atr_15m,
                "expected_rate": edge["expected_rate"],
                "required_rate": edge["required_rate"],
                "net_rate": edge["net_rate"]
            })
        except Exception as e:
            print(f"[⚠️ Scanner Error] Failed to compute metrics for {symbol}: {e}")

    if not candidates:
        print("[⚠️ Scanner] No candidates with valid technical indicators calculated.")
        display_result(None, "SKIP", "No candidates with valid technical metrics calculated.")
        return None

    # Call Gemini to analyze the candidate list
    print(f"[🤖 AI Scanner] Requesting Gemini evaluation on {len(candidates)} candidates...")
    prompt = f"""
You are a professional crypto trading analyst executing a "Last Chance Scanner" at the end of the trading day.
Our objective is to scan the market for an extremely high-probability trade because no trades have occurred today.
We must be non-intrusive and only trade if the opportunity is exceptionally clear and profitable after fees.

Trading fee rate: {config.TRADING_FEE_RATE * 100:.4f}% per transaction ({config.TRADING_FEE_RATE * 2 * 100:.4f}% roundtrip).

Candidates list:
{json.dumps(candidates, indent=2)}

Please analyze each candidate pair using the following criteria:
1. Trend Strength: Evaluated by ADX (values above 20 show trend strength) and EMA alignments.
2. Multi-Timeframe Confirmation: Confirm if 1H, 15M, and 5M trends are aligned or showing bullish reversal.
3. Momentum: Check RSI (should be recovering from oversold, or in a bullish zone 50-65) and MACD hist (rising/positive).
4. Volume: 24h volume and volume ratio (vol_ratio_15m > 1 means volume spike).
5. Liquidity: Low spread_rate and high 24h volume.
6. Order Flow: Bid/Ask ratio from order book depth (ratio > 1.2 indicates strong buy support).
7. Smart Money Activity: Indicated by a combination of volume spikes and strong bid imbalances.
8. Support & Resistance: Evaluated by donchian_pos_15m (closer to 0 is near support, closer to 1 is near resistance).
9. Volatility: ATR ratio.
10. Risk / Reward Ratio: Expected rate vs ATR / potential stop loss.
11. Expected Profit After Fees: Net rate must be positive.

Decision Rules:
- Select the single best pair with the highest probability of a profitable trade.
- Only decide "BUY" if:
  - AI Confidence is >= {config.LAST_CHANCE_MIN_CONFIDENCE}%
  - Expected Net Profit is positive after all fees (net_rate > 0)
  - Liquidity and volume are sufficient (spread_rate <= {config.LAST_CHANCE_MAX_SPREAD} and volume_24h_thb >= {config.LAST_CHANCE_MIN_VOLUME_THB})
  - Risk score is <= {config.LAST_CHANCE_MAX_RISK} (on a scale of 1 to 10)
  - No strong bearish reversal is detected in the trend or order flow.
- Otherwise, decide "SKIP" and set selected_pair to null.

You MUST respond ONLY with a JSON object in this exact format:
{{
  "decision": "BUY" | "SKIP",
  "selected_pair": "SYMBOL_THB" | null,
  "confidence": 0-100,
  "expected_move_pct": float,
  "expected_net_profit_pct": float,
  "risk_score": 1-10,
  "main_reasons": "reasons in Thai",
  "explanation": "detailed analysis of trends, multi-timeframe confirmation, volume, order flow, etc. in Thai"
}}
"""
    response_text = call_gemini_api(prompt)
    if not response_text:
        print("[⚠️ Scanner] Gemini API call failed or returned empty response.")
        display_result(None, "SKIP", "Gemini API call failed or returned empty response.")
        return None

    try:
        # Strip code block markdown if present
        cleaned_text = response_text.strip()
        if cleaned_text.startswith("```json"):
            cleaned_text = cleaned_text[7:]
        if cleaned_text.endswith("```"):
            cleaned_text = cleaned_text[:-3]
        cleaned_text = cleaned_text.strip()

        result = json.loads(cleaned_text)
        decision = result.get("decision", "SKIP").upper()
        selected_pair = result.get("selected_pair")
        confidence = result.get("confidence")
        expected_move = result.get("expected_move_pct")
        expected_net_profit = result.get("expected_net_profit_pct")
        risk_score = result.get("risk_score")
        main_reasons = result.get("main_reasons", "No reason provided")

        display_result(
            selected_pair=selected_pair,
            decision=decision,
            confidence=confidence,
            expected_move=expected_move,
            expected_net_profit=expected_net_profit,
            risk_score=risk_score,
            main_reasons=main_reasons
        )

        if decision == "BUY" and selected_pair:
            # Find the candidate matching the selected pair
            coin_metrics = next((c for c in candidates if c["symbol"].lower() == selected_pair.lower()), None)
            if coin_metrics:
                # Construct opportunity object
                opportunity = {
                    "symbol": coin_metrics["symbol"],
                    "coin": coin_metrics["coin"],
                    "price": coin_metrics["price"],
                    "signal": "BUY",
                    "spread_rate": coin_metrics["spread_rate"],
                    "volume_thb": coin_metrics["volume_24h_thb"],
                    "atr": coin_metrics["atr"],
                    "score": int(confidence) if confidence is not None else 100,
                    "expected_rate": (expected_move / 100.0) if expected_move is not None else coin_metrics["expected_rate"],
                    "required_rate": coin_metrics["required_rate"],
                    "net_rate": (expected_net_profit / 100.0) if expected_net_profit is not None else coin_metrics["net_rate"],
                    "reason": f"AI Last Chance: {main_reasons}"
                }
                return opportunity
            else:
                print(f"[⚠️ Scanner] Selected pair '{selected_pair}' metrics not found in candidate list.")
    except Exception as e:
        print(f"[⚠️ Scanner Error] Failed to parse Gemini response: {e}")
        print(f"Raw Response: {response_text}")
        display_result(None, "SKIP", f"Failed to parse Gemini response: {e}")

    return None


def run_last_chance_scan_v2(client) -> dict:
    """
    Scans all available trading pairs, calculates scores using calculate_score,
    selects the candidate with the highest score >= LAST_CHANCE_MIN_SCORE and net_rate > 0.
    If GEMINI_ENABLED is True, validates the best candidate with Gemini.
    """
    tickers = client.get_all_tickers()
    if not tickers:
        print("[⚠️ Scanner] Could not fetch tickers.")
        return None

    # Filter THB pairs
    thb_pairs = []
    for ticker_key, ticker in tickers.items():
        if not ticker_key.upper().startswith("THB_") or not isinstance(ticker, dict):
            continue
        
        # Calculate volume & spread
        volume = get_ticker_volume_thb(ticker)
        spread = get_spread_rate(ticker)
        
        # Apply pre-filter to reduce API load
        if volume < config.LAST_CHANCE_MIN_VOLUME_THB:
            continue
        if spread > config.LAST_CHANCE_MAX_SPREAD:
            continue
            
        # Convert e.g. THB_BTC to btc_thb
        parts = ticker_key.upper().split("_")
        symbol = f"{parts[1]}_THB".lower()
        thb_pairs.append((volume, symbol, ticker))

    # Sort by volume and limit to top 30
    thb_pairs.sort(reverse=True)
    thb_pairs = thb_pairs[:30]

    if not thb_pairs:
        print("[⚠️ Scanner] No pairs passed volume and spread filters.")
        return None

    print(f"[🔍 Scanner V2] Collecting metrics for {len(thb_pairs)} candidate pairs...")
    candidates = []
    for _, symbol, ticker in thb_pairs:
        try:
            # Fetch candles
            candles_5m = client.get_candles(symbol, resolution="5", limit=100)
            candles_15m = client.get_candles(symbol, resolution="15", limit=100)
            candles_1h = client.get_candles(symbol, resolution="60", limit=100)

            df_5m = parse_candles_to_dataframe(candles_5m)
            df_15m = parse_candles_to_dataframe(candles_15m)
            df_1h = parse_candles_to_dataframe(candles_1h)

            if df_5m.empty or df_15m.empty or df_1h.empty:
                continue
            if len(df_5m) < 30 or len(df_15m) < 30 or len(df_1h) < 30:
                continue

            df_5m = calculate_indicators(df_5m)
            df_15m = calculate_indicators(df_15m)
            df_1h = calculate_indicators(df_1h)

            # 1h trend checks: "ไม่ downtrend ชัดเจนสุดขั้ว"
            row_1h = df_1h.iloc[-1]
            ema_fast_1h = row_1h["ema_fast"]
            ema_slow_1h = row_1h["ema_slow"]
            ema_50_1h = row_1h["ema_50"]
            close_1h = row_1h["close"]
            
            is_severe_downtrend_1h = (ema_fast_1h < ema_slow_1h) and (close_1h < ema_50_1h)
            if is_severe_downtrend_1h:
                continue

            # Calculate score using the shared helper
            score, bid_vol, ask_vol = calculate_score(client, symbol, df_15m)
            
            # Check if score passes the relaxed LAST_CHANCE_MIN_SCORE (default 45)
            if score < config.LAST_CHANCE_MIN_SCORE:
                continue

            # Calculate profit after fees
            spread = get_spread_rate(ticker)
            edge = estimate_trade_edge(df_15m, "BUY", spread)
            if edge["net_rate"] <= 0:
                continue

            row_15m = df_15m.iloc[-1]
            candidates.append({
                "symbol": symbol,
                "coin": base_coin(symbol),
                "price": row_15m["close"],
                "spread_rate": spread,
                "volume_24h_thb": get_ticker_volume_thb(ticker),
                "atr": row_15m["atr"],
                "score": score,
                "expected_rate": edge["expected_rate"],
                "required_rate": edge["required_rate"],
                "net_rate": edge["net_rate"],
                "df_15m": df_15m
            })
        except Exception as e:
            print(f"[⚠️ Scanner V2 Error] Failed to compute metrics for {symbol}: {e}")

    if not candidates:
        print("[⚠️ Scanner V2] No candidates passed scoring and edge filters.")
        return None

    # Sort: highest score first, then highest net_rate
    candidates.sort(key=lambda c: (c["score"], c["net_rate"]), reverse=True)
    best = candidates[0]
    
    print(f"[🔍 Scanner V2] Selected best candidate: {best['symbol'].upper()} (Score: {best['score']}/100, Net Edge: {best['net_rate'] * 100:.2f}%)")

    # If Gemini confirmation is enabled, ask Gemini to confirm the single best candidate
    if config.GEMINI_ENABLED:
        print(f"[🤖 AI Scanner V2] Requesting Gemini validation for best candidate {best['symbol'].upper()}...")
        df_15m = best["df_15m"]
        recent_df = df_15m.tail(15)
        candles_summary = []
        for idx, row in recent_df.iterrows():
            candles_summary.append(
                f"Time: {row['datetime']}, Close: {row['close']:.2f}, High: {row['high']:.2f}, Low: {row['low']:.2f}"
            )
        candles_text = "\n".join(candles_summary)

        latest_row = df_15m.iloc[-1]
        rsi_val = latest_row.get("rsi", 50.0)
        ema_fast = latest_row.get("ema_fast", 0.0)
        ema_slow = latest_row.get("ema_slow", 0.0)

        prompt = f"""
You are a professional crypto trading assistant.
Analyze the following market data for {best['symbol'].upper()}:

Price history (last 15 candles):
{candles_text}

Latest indicators:
- RSI (14): {rsi_val:.2f}
- EMA Fast (9): {ema_fast:.2f}
- EMA Slow (21): {ema_slow:.2f}
- Traditional Indicator Signal: BUY (Score: {best['score']}/100)

Important Constraint:
- Every single trade incurs a 0.25% trading fee (0.50% total fee for a complete buy and sell roundtrip).
- You must factor in this 0.25% fee per transaction. Only confirm a "BUY" signal if the expected price movement has enough momentum/room to comfortably cover the 0.50% roundtrip fees and yield a net profit.

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
        if response_text:
            try:
                cleaned_text = response_text.strip()
                if cleaned_text.startswith("```json"):
                    cleaned_text = cleaned_text[7:]
                if cleaned_text.endswith("```"):
                    cleaned_text = cleaned_text[:-3]
                cleaned_text = cleaned_text.strip()

                result = json.loads(cleaned_text)
                decision = result.get("decision", "HOLD").upper()
                reason = result.get("reason", "No reason provided")
                if decision != "BUY":
                    print(f"Skip {best['symbol'].upper()} BUY: Not confirmed by Gemini. Reason: {reason}")
                    return None
                best["reason"] = f"AI Last Chance (Gemini): {reason}"
                print(f"[🤖 AI Response V2] Gemini confirmed BUY for {best['symbol'].upper()}. Reason: {reason}")
            except Exception as e:
                print(f"[⚠️ Scanner V2] Failed to parse Gemini response: {e}")
                if config.GEMINI_SKIP_ON_FAIL:
                    print("[🤖 AI Scanner V2] Gemini validation failed, skipping trade due to GEMINI_SKIP_ON_FAIL=True.")
                    return None
                best["reason"] = f"AI Last Chance: {best['score']}/100 (Failed to parse Gemini response)"
        else:
            if config.GEMINI_SKIP_ON_FAIL:
                print("[🤖 AI Scanner V2] Gemini API failed, skipping trade due to GEMINI_SKIP_ON_FAIL=True.")
                return None
            best["reason"] = f"AI Last Chance: {best['score']}/100 (Gemini API failed)"
    else:
        best["reason"] = f"AI Last Chance: {best['score']}/100"

    # Clean up df_15m key before returning so it can serialize
    best.pop("df_15m", None)
    return best
