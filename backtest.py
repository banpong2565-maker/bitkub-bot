import pandas as pd
import numpy as np
import requests
import time
import os
import sys

# เพิ่มไดเรกทอรีปัจจุบันลงใน sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from strategy import parse_candles_to_dataframe, calculate_indicators
import config


def run_backtest(symbol: str = "btc_thb", limit: int = 1000):
    print("=" * 60)
    print(f"📈 เริ่มทำการจำลองย้อนหลัง (Backtesting) ด้วยระบบ AI Scoring สำหรับ {symbol.upper()}")
    print(f"จำนวนแท่งเทียนย้อนหลัง: {limit} แท่ง (Timeframe: 15m)")
    print("=" * 60)

    # 1. ดึงข้อมูลแท่งเทียนประวัติศาสตร์จาก Bitkub
    url = "https://api.bitkub.com/tradingview/history"
    to_time = int(time.time())
    from_time = to_time - (limit * 15 * 60 * 2)

    params = {
        "symbol": symbol.upper(),
        "resolution": "15",
        "from": from_time,
        "to": to_time,
    }

    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        candles_data = response.json()
    except Exception as e:
        print(f"[⚠️ Error] ดึงข้อมูลแท่งเทียนล้มเหลว: {e}")
        return

    df = parse_candles_to_dataframe(candles_data)
    if df.empty or len(df) < 50:
        print("[⚠️ Error] ข้อมูลแท่งเทียนมีจำนวนไม่เพียงพอในการทดสอบ")
        return

    # 2. คำนวณอินดิเคเตอร์ทั้งหมด (รวม EMA 50, Bollinger Bands, SuperTrend, VWAP)
    df = calculate_indicators(df)

    # 3. เริ่มการรันกลยุทธ์จำลอง
    initial_balance = 10000.0  # เงินเริ่มต้น 10,000 บาท
    balance = initial_balance
    position = 0.0  # จำนวนเหรียญที่ถือครอง
    entry_price = 0.0
    fee_rate = 0.0025  # ค่าธรรมเนียม 0.25%

    trades = []
    highest_price_during_trade = 0.0
    atr_at_entry = 0.0
    initial_amount = 0.0
    current_trade_cost = 0.0
    current_trade_received = 0.0
    # [NEW] บันทึกเวลาเข้าเพื่อคำนวณ Avg Hold Time
    entry_time = None
    # [NEW] Equity Curve สำหรับคำนวณ Max Drawdown
    equity_curve = [initial_balance]

    # สถานะเป้ากำไรบางส่วน
    tp1_sold = False
    tp2_sold = False

    # [UPDATED] พารามิเตอร์การจัดการความเสี่ยง (sync กับ config.py)
    stop_loss_rate = 0.035       # -3.5% (เดิม -1.5%)
    trailing_stop_rate = 0.012   # 1.2% (เดิม 0.3%)
    trailing_min_profit_rate = 0.010  # 1.0% (เดิม 0.8%)
    min_net_profit_rate = 0.010  # 1.0% (เดิม 0.4%)
    min_trade_val = 50.0
    sell_rsi_threshold = 45.0    # [NEW] RSI < 45 = สัญญาณขาย
    sell_confirm_min = 2         # [NEW] ต้องมี >= 2 ใน 3

    # เลื่อนตามประวัติเพื่อจำลองการเทรดแบบทีละแท่ง
    for i in range(30, len(df)):
        current_row = df.iloc[i]
        prev_row = df.iloc[i - 1]

        close_price = current_row["close"]
        high_price = current_row["high"]
        low_price = current_row["low"]
        volume = current_row["volume"]

        # ดึงอินดิเคเตอร์สำหรับแท่งเทียนนั้นๆ
        ema_fast_now = current_row["ema_fast"]
        ema_slow_now = current_row["ema_slow"]
        ema_50 = current_row["ema_50"]
        ema_fast_prev = prev_row["ema_fast"]
        ema_slow_prev = prev_row["ema_slow"]

        rsi = current_row["rsi"]
        rsi_prev = prev_row["rsi"]
        macd = current_row["macd"]
        macd_signal = current_row["macd_signal"]
        macd_hist = current_row["macd_hist"]
        adx = current_row["adx"]
        plus_di = current_row["plus_di"]
        minus_di = current_row["minus_di"]
        volume_ma = current_row["volume_ma"]
        atr = current_row["atr"]
        atr_ma = df["atr"].rolling(20).mean().iloc[i]

        # ตรวจเช็คสถานะการถือครอง
        if position > 0:
            # กำลังถือสถานะอยู่ -> ตรวจสอบเงื่อนไขการออก (Exit rules)
            highest_price_during_trade = max(highest_price_during_trade, high_price)
            pnl_rate = (close_price / entry_price) - 1
            drawdown_from_high = (close_price / highest_price_during_trade) - 1

            # ดึงราคาระดับ SL เริ่มต้น
            sl_price = entry_price - (atr_at_entry * 1.5) if atr_at_entry > 0 else entry_price * (1 - stop_loss_rate)
            
            # หากแตะ TP1 แล้ว เลื่อน SL มาที่ทุน (Break-even Stop)
            if tp1_sold:
                sl_price = entry_price

            reason = None
            amount_to_sell = position
            is_partial = False

            # 1. Stop Loss (Full Exit)
            if low_price <= sl_price:
                reason = "Stop Loss (ATR/Breakeven)"
                close_price = sl_price
                amount_to_sell = position
                is_partial = False
            # 2. Trailing Stop (Full Exit)
            # [FIX] อิง net_pnl_rate (หลังหักค่าธรรมเนียม) >= trailing_min_profit_rate
            elif (drawdown_from_high <= -trailing_stop_rate) and ((pnl_rate - fee_rate * 2) >= trailing_min_profit_rate):
                reason = "Trailing Stop"
                amount_to_sell = position
                is_partial = False
            # 3. Dynamic Exits based on ATR targets (TP1, TP2, TP3)
            elif atr_at_entry > 0:
                tp1_target = entry_price + (atr_at_entry * 1.0)
                tp2_target = entry_price + (atr_at_entry * 2.0)
                tp3_target = entry_price + (atr_at_entry * 3.0)

                # TP3: ขาย 40% ที่เหลือทั้งหมด
                if high_price >= tp3_target:
                    reason = "Take Profit (TP3 - Full)"
                    close_price = tp3_target
                    amount_to_sell = position
                    is_partial = False
                # TP2: ขาย 30%
                elif high_price >= tp2_target and not tp2_sold:
                    reason = "Take Profit (TP2 - 30%)"
                    close_price = tp2_target
                    amount_to_sell = min(initial_amount * 0.3, position)
                    is_partial = True
                # TP1: ขาย 30%
                elif high_price >= tp1_target and not tp1_sold:
                    reason = "Take Profit (TP1 - 30%)"
                    close_price = tp1_target
                    amount_to_sell = min(initial_amount * 0.3, position)
                    is_partial = True
            # 4-6. Technical Exit — [FIX] ต้องมี >= sell_confirm_min สัญญาณใน 3 (EMA, MACD, RSI)
            else:
                ema_exit_bt = ema_fast_prev >= ema_slow_prev and ema_fast_now < ema_slow_now
                macd_exit_bt = macd_hist < 0 and prev_row["macd_hist"] >= 0
                rsi_exit_bt = rsi < sell_rsi_threshold
                tech_count = sum([ema_exit_bt, macd_exit_bt, rsi_exit_bt])
                if tech_count >= sell_confirm_min:
                    tech_labels = []
                    if ema_exit_bt:
                        tech_labels.append("EMA")
                    if macd_exit_bt:
                        tech_labels.append("MACD")
                    if rsi_exit_bt:
                        tech_labels.append(f"RSI<{sell_rsi_threshold}")
                    reason = f"Technical Exit ({'+'.join(tech_labels)})"
                    amount_to_sell = position
                    is_partial = False

            # Enforce Minimum Net Profit — ยกเว้น Stop Loss, Trailing Stop
            if reason and reason not in ("Stop Loss (ATR/Breakeven)", "Trailing Stop"):
                net_rate = pnl_rate - (fee_rate * 2)
                if net_rate < min_net_profit_rate:
                    reason = None

            if reason:
                amount_to_sell = min(amount_to_sell, position)
                value_sold = amount_to_sell * close_price
                fee = value_sold * fee_rate
                net_received = value_sold - fee
                balance += net_received
                current_trade_received += net_received
                pnl = net_received - (amount_to_sell * entry_price)

                if is_partial:
                    position -= amount_to_sell
                    if "TP1" in reason:
                        tp1_sold = True
                        print(f"🟠 PARTIAL SELL TP1ที่ {close_price:,.2f} | PnL: {pnl:,.2f} THB | เลื่อน SL ไปที่ทุน | เวลา: {current_row['datetime']}")
                    elif "TP2" in reason:
                        tp2_sold = True
                        tp1_sold = True
                        print(f"🟠 PARTIAL SELL TP2ที่ {close_price:,.2f} | PnL: {pnl:,.2f} THB | เลื่อน SL ไปที่ทุน | เวลา: {current_row['datetime']}")
                else:
                    cycle_pnl = current_trade_received - current_trade_cost
                    # [NEW] คำนวณ hold duration
                    hold_candles = (i - trades[-1].get("buy_index", i)) if trades else 0
                    hold_minutes = hold_candles * 15
                    trades.append(
                        {
                            "type": "SELL",
                            "price": close_price,
                            "time": current_row["datetime"],
                            "pnl": cycle_pnl,
                            "reason": reason,
                            "hold_minutes": hold_minutes,
                        }
                    )
                    print(
                        f"🔴 FULL SELL ที่ {close_price:,.2f} | รวมกำไร/ขาดทุนรอบนี้: {cycle_pnl:,.2f} THB | เหตุผล: {reason} | Hold: {hold_minutes}m | เวลา: {current_row['datetime']}"
                    )
                    position = 0.0
                    entry_price = 0.0
                    initial_amount = 0.0
                    tp1_sold = False
                    tp2_sold = False
                    entry_time = None
                    # [NEW] บันทึก equity curve
                    equity_curve.append(balance + 0)  # อยู่ในเงินสดเต็มแล้ว

        else:
            # ไม่มีของ ถือเงินสดอยู่ -> คำนวณสัญญาณซื้ออิงระบบ AI Scoring
            score = 0

            # A. EMA trend score (Max 25 pts)
            if ema_fast_now > ema_slow_now and close_price > ema_50:
                score += 25
            elif ema_fast_now > ema_slow_now:
                score += 15

            # B. MACD score (Max 20 pts)
            if macd > macd_signal and macd_hist > 0:
                score += 20
            elif macd > macd_signal:
                score += 10

            # C. RSI score (Max 15 pts)
            if rsi > 50.0 and rsi < 65.0 and rsi > rsi_prev:
                score += 15
            elif rsi > 50.0 and rsi < 65.0:
                score += 10

            # D. ADX score (Max 15 pts)
            downtrend_strong = (adx > 22.0) and (minus_di > plus_di)
            if not downtrend_strong and adx > 18.0:
                score += 15

            # E. Volume score (Max 15 pts)
            if volume > volume_ma * 1.2:
                score += 15
            elif volume > volume_ma * 1.1:
                score += 10

            # F. Order Book Score (สมมุติฝั่ง Bids แข็งกว่าเฉลี่ย 7 คะแนนจำลองย้อนหลัง)
            score += 7

            # G. ATR score (Max 5 pts)
            if atr > atr_ma:
                score += 5
            else:
                score += 2

            # คอนเฟิร์มสัญญาณเมื่อ Score >= config.MIN_BUY_SCORE และไม่อยู่ในโครงสร้าง Downtrend 1H
            # (เนื่องจากไม่มีข้อมูล 1H ในการคำนวณย้อนหลังพร้อม 15m ในสคริปต์เดี่ยว จึงอิงสัญญาณ ADX Skip Downtrend ของ 15m)
            if score >= config.MIN_BUY_SCORE:
                trade_size_thb = min(300.0, balance)
                if trade_size_thb >= min_trade_val:
                    fee = trade_size_thb * fee_rate
                    net_buy_thb = trade_size_thb - fee
                    position = net_buy_thb / close_price
                    initial_amount = position
                    entry_price = close_price
                    atr_at_entry = atr
                    highest_price_during_trade = close_price
                    balance -= trade_size_thb
                    current_trade_cost = trade_size_thb
                    current_trade_received = 0.0

                    tp1_sold = False
                    tp2_sold = False

                    trades.append(
                        {
                            "type": "BUY",
                            "price": close_price,
                            "time": current_row["datetime"],
                            "buy_index": i,  # [NEW] บันทึกตำแหน่งแท่ง index
                        }
                    )
                    print(
                        f"🟢 BUY (Score: {score}/100) ที่ {close_price:,.2f} | ขนาด: {trade_size_thb:.2f} THB | เวลา: {current_row['datetime']}"
                    )

    # 4. สรุปผลการ Backtest
    print("\n" + "=" * 60)
    print("📊 สรุปผลลัพธ์การจำลองกลยุทธ์ (Backtesting Summary)")
    print("=" * 60)
    sell_trades = [t for t in trades if t["type"] == "SELL"]
    total_trades_count = len(sell_trades)
    final_value = balance + (position * df.iloc[-1]["close"])
    if total_trades_count > 0:
        winning_trades = [t for t in sell_trades if t.get("pnl", 0.0) > 0]
        losing_trades  = [t for t in sell_trades if t.get("pnl", 0.0) <= 0]
        win_count = len(winning_trades)
        loss_count = len(losing_trades)
        win_rate = (win_count / total_trades_count) * 100
        total_pnl = sum(t.get("pnl", 0.0) for t in sell_trades)
        net_profit_percent = (total_pnl / initial_balance) * 100

        # [NEW] Profit Factor
        gross_profit = sum(t["pnl"] for t in winning_trades)
        gross_loss   = abs(sum(t["pnl"] for t in losing_trades))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

        # [NEW] Average Holding Time
        hold_times = [t.get("hold_minutes", 0) for t in sell_trades if t.get("hold_minutes", 0) > 0]
        avg_hold_minutes = sum(hold_times) / len(hold_times) if hold_times else 0
        avg_hold_h = int(avg_hold_minutes // 60)
        avg_hold_m = int(avg_hold_minutes % 60)

        # [NEW] Maximum Drawdown จาก Equity Curve
        max_dd = 0.0
        peak = equity_curve[0]
        for val in equity_curve:
            if val > peak:
                peak = val
            dd = (val - peak) / peak if peak > 0 else 0
            if dd < max_dd:
                max_dd = dd

        # [NEW] Sharpe Ratio (annualized approx from trade returns, assuming 15m candles)
        trade_returns = [(t["pnl"] / (current_trade_cost or initial_balance)) for t in sell_trades]
        if len(trade_returns) > 1:
            import statistics
            avg_r = statistics.mean(trade_returns)
            std_r = statistics.stdev(trade_returns)
            # สมมติ 252 วันซื้อขายต่อปี, 96 แท่ง 15m ต่อวัน
            trades_per_year = 252 * 96
            sharpe = (avg_r / std_r) * (trades_per_year ** 0.5) if std_r > 0 else 0.0
        else:
            sharpe = 0.0

        # [NEW] Reason breakdown
        from collections import Counter
        reason_counts = Counter(t.get("reason", "Unknown") for t in sell_trades)

        print(f"💰 เงินทุนเริ่มต้น: {initial_balance:,.2f} THB")
        print(f"💵 เงินทุนปลายทาง: {final_value:,.2f} THB")
        print(f"📈 กำไรรวมสุทธิหลังหักค่าธรรมเนียม: {total_pnl:,.2f} THB ({net_profit_percent:.2f}%)")
        print(f"🔄 จำนวนการซื้อขาย (รอบเสร็จสิ้น): {total_trades_count} รอบ")
        print(f"🏆 Win Rate: {win_rate:.2f}% (ชนะ {win_count} | แพ้ {loss_count})")
        print(f"📊 Profit Factor: {profit_factor:.2f}")
        print(f"⏱️  Avg Holding Time: {avg_hold_h}h {avg_hold_m}m (เฉลี่ย: {avg_hold_minutes:.0f} นาที)")
        print(f"📊 Maximum Drawdown: {max_dd*100:.2f}%")
        print(f"📉 Sharpe Ratio: {sharpe:.2f} (approx, annualized)")
        print(f"💰 Gross Profit: {gross_profit:,.2f} THB | Gross Loss: {gross_loss:,.2f} THB")
        print("")
        print("📝 Sell Reason Breakdown:")
        for reason_label, cnt in reason_counts.most_common():
            print(f"   {cnt:>3}x  {reason_label}")
    else:
        print("❌ ไม่มีการทำรายการซื้อขายใดๆ เกิดขึ้นในช่วงเวลาจำลองนี้")
    print("=" * 60)


if __name__ == "__main__":
    symbol_to_test = "btc_thb"
    if len(sys.argv) > 1:
        symbol_to_test = sys.argv[1]
    run_backtest(symbol=symbol_to_test, limit=1000)
