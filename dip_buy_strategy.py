# dip_buy_strategy.py
"""
กลยุทธ์เสริม: ทุกรอบที่บอทสแกน (ทุก config.LOOP_INTERVAL_SECONDS วินาที) จะไล่ดูเหรียญ
ทั้งกระดาน THB แล้วเลือกเหรียญที่ "ติดลบมากที่สุด ณ ขณะนั้น" (ตาม % เปลี่ยนแปลงที่ Bitkub
แสดงบนกระดานเอง ไม่ใช่คำนวณเองจากหน้าต่างเวลาตายตัว เพราะเหรียญอาจตกมาก่อนหน้านั้นแล้วก็ได้)
มาซื้อก่อน หากซื้อไม่สำเร็จให้ไล่ตัวถัดไปที่ตกรองลงมา

จะขายก็ต่อเมื่อครบทั้ง 2 เงื่อนไขพร้อมกัน:
  1) % เปลี่ยนแปลงบนกระดานของเหรียญนั้นกลับมาเป็นบวกแล้ว (>= 0) เช่น ซื้อตอน -15% ต้องรอจน
     ค่านี้ขยับกลับมาที่ 0% ขึ้นไปก่อน
  2) กำไรสุทธิหลังหักค่าธรรมเนียมซื้อ-ขายแล้ว (เทียบราคาที่ซื้อมาจริง) ต้องเป็นบวกด้วย (ไม่ขาดทุน)
เหรียญที่ตกไม่ถึงเกณฑ์ config.DIP_BUY_MIN_DROP_PERCENT หรือวอลุ่มต่ำกว่า config.MIN_24H_VOLUME_THB
จะถูกข้าม ไม่นำมาซื้อ

สำคัญ: โมดูลนี้แยกเป็นอิสระจาก bot.py โดยสิ้นเชิง
- ไม่แก้ไข ไม่เรียกใช้ และไม่รบกวน scan_market(), execute_buy(), execute_sell()
- มี state ของตัวเอง (dip_position) และไฟล์บันทึกของตัวเอง (dip_state.json)
  แยกจาก position_state / state.json ที่กลยุทธ์หลักใช้ ดังนั้นจะไม่มีทางไปรบกวน
  การตัดสินใจซื้อ-ขายของกลยุทธ์เดิมเลย
- ถูกเปิด/ปิดด้วย config.DIP_BUY_ENABLED (ค่าเริ่มต้น False) — ถ้าปิดไว้
  พฤติกรรมของบอทจะเหมือนเดิมทุกประการ

การเชื่อม paper_thb / paper_balances ใน bot.py (เฉพาะตอน DRY_RUN) เป็นการอัปเดต
ตัวเลขยอดเงิน/เหรียญกระดาษเท่านั้น (เพราะเป็นบัญชีเดียวกัน) จะไม่แตะ position_state
หรือ logic การตัดสินใจใด ๆ ของกลยุทธ์เดิม
"""

import json
import os
from datetime import datetime

import config

DIP_STATE_FILE = "dip_state.json"

# มี position เปิดอยู่ได้ทีละ 1 ตัวเท่านั้น (ตามที่ระบุ "ซื้อตัวนั้น...ขายตัวนั้น")
# โครงสร้าง: {"symbol": "btc_thb", "coin": "BTC", "entry_price": ..., "amount": ..., "opened_at": ...}
dip_position: dict = {}


def load_dip_state() -> None:
    """โหลด state ของกลยุทธ์นี้จากไฟล์ของตัวเอง (ไม่ยุ่งกับ state.json เดิม)."""
    global dip_position
    if os.path.exists(DIP_STATE_FILE):
        try:
            with open(DIP_STATE_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                dip_position = loaded if isinstance(loaded, dict) else {}
            if dip_position:
                print(f"[DipBuy] โหลด state เดิม: ถือ {dip_position.get('symbol', '?').upper()} อยู่")
        except Exception as e:
            print(f"[DipBuy] โหลด state ไม่สำเร็จ: {e}")
            dip_position = {}


def save_dip_state() -> None:
    try:
        with open(DIP_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(dip_position, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[DipBuy] บันทึก state ไม่สำเร็จ: {e}")


def _rank_by_biggest_drop(tickers: dict, exclude_symbols: set) -> list:
    """จัดอันดับเหรียญตลาด THB ตาม % เปลี่ยนแปลงที่ "กระดาน" Bitkub แสดงอยู่จริง
    (ticker.percentChange ของ Bitkub เอง) จากตกมากสุด -> น้อยสุด

    นี่คือฟังก์ชันหลักที่ใช้จัดอันดับ "เหรียญติดลบมากที่สุดในกระดาน ณ ขณะที่สแกน" —
    ไม่ได้จำกัดว่าเหรียญต้องตกภายในหน้าต่างเวลาที่กำหนดตายตัว (เช่น 1-2 ชม.ล่าสุด)
    เพราะเหรียญอาจตกมาตั้งแต่ก่อนหน้านั้นแล้วก็ได้ สิ่งที่สำคัญคือ ณ เวลาที่บอทสแกน (ทุก
    config.LOOP_INTERVAL_SECONDS วินาที) ตัวไหนติดลบมากที่สุดบนกระดาน ตัวนั้นถูกเลือกก่อน
    กรองด้วยวอลุ่ม (config.MIN_24H_VOLUME_THB) และเกณฑ์ตกขั้นต่ำ (config.DIP_BUY_MIN_DROP_PERCENT)
    ในขั้นถัดไปที่ run_dip_buy_cycle()
    """
    import bot  # lazy import กัน circular import กับ bot.py

    ranked = []
    for ticker_key, ticker in tickers.items():
        if not ticker_key.upper().startswith("THB_") or not isinstance(ticker, dict):
            continue

        symbol = bot.ticker_key_to_symbol(ticker_key)  # เช่น "THB_BTC" -> "btc_thb"
        if symbol.upper() in exclude_symbols:
            continue

        pct = ticker.get("percentChange")
        if pct is None:
            pct = ticker.get("percent_change")
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            continue

        price = bot.safe_float(ticker.get("last"))
        if price <= 0:
            continue

        volume_thb = bot.get_ticker_volume_thb(ticker)
        if volume_thb < config.MIN_24H_VOLUME_THB:
            continue  # กันเหรียญเงียบ/สภาพคล่องต่ำ ที่ตัวเลข % เปลี่ยนแปลงอาจไม่น่าเชื่อถือ

        ranked.append({"symbol": symbol, "coin": bot.base_coin(symbol), "percent_change": pct, "price": price})

    ranked.sort(key=lambda x: x["percent_change"])  # ตกมากสุด (ค่าติดลบมากสุด) มาก่อน
    return ranked


def _attempt_buy(client, notifier, candidate: dict, available_thb: float) -> bool:
    """ลองซื้อ candidate ตัวเดียว คืนค่า True หากสำเร็จ, False หากล้มเหลว (ให้ผู้เรียกไล่ตัวถัดไป)."""
    import bot  # lazy import กัน circular import กับ bot.py

    symbol = candidate["symbol"]
    coin = candidate["coin"]
    price = candidate["price"]

    if config.DIP_BUY_USE_ALL_AVAILABLE_THB:
        amount_thb = max(0.0, available_thb - config.DIP_BUY_RESERVE_THB)
    else:
        amount_thb = min(config.DIP_BUY_AMOUNT_THB, available_thb)

    if amount_thb < config.MIN_TRADE_VALUE_THB:
        print(f"[DipBuy] ⏭️ ข้าม {symbol.upper()}: ยอดเงินที่ใช้ได้ไม่พอ ({amount_thb:,.2f} < ขั้นต่ำ {config.MIN_TRADE_VALUE_THB:,.2f} THB)")
        return False

    if config.DRY_RUN:
        if bot.paper_thb < amount_thb:
            print(f"[DipBuy] ⏭️ ข้าม {symbol.upper()}: เงินกระดาษไม่พอ ({bot.paper_thb:,.2f} THB)")
            return False

        fee = amount_thb * config.TRADING_FEE_RATE
        net_buy_thb = amount_thb - fee
        coin_bought = net_buy_thb / price

        bot.paper_thb -= amount_thb
        bot.paper_balances[coin] = bot.paper_balances.get(coin, 0.0) + coin_bought

        dip_position.clear()
        dip_position.update({
            "symbol": symbol,
            "coin": coin,
            "entry_price": price,
            "amount": coin_bought,
            "opened_at": datetime.now().isoformat(timespec="seconds"),
        })
        save_dip_state()

        detail = (
            f"[DipBuy][DRY RUN] ซื้อ {symbol.upper()} (ติดลบมากที่สุดบนกระดาน {candidate['percent_change']:.2f}%) "
            f"จำนวน {coin_bought:.8f} @ {price:,.4f} THB (ใช้เงิน {amount_thb:,.2f} THB)"
        )
        print(detail)
        _notify(notifier, detail)
        return True

    # ---- เทรดจริง (Live) ----
    broker_only = bot.get_broker_only_symbols(client)
    if symbol.upper() in broker_only:
        print(f"[DipBuy] ⏭️ ข้าม {symbol.upper()}: เป็นเหรียญ source=broker เทรดผ่าน API ไม่ได้")
        return False

    order_res = client.place_bid(symbol=symbol, amount=amount_thb, rate=0.0, order_type="market")
    if order_res.get("error") != 0:
        print(f"[DipBuy] ❌ ซื้อ {symbol.upper()} ไม่สำเร็จ: {order_res.get('message') or order_res.get('error')} — ไล่ตัวถัดไป")
        return False

    result = order_res.get("result", {})
    actual_price = bot.safe_float(
        result.get("rate") or result.get("rat") or result.get("avg_price") or result.get("avg")
    )
    fill_price = actual_price if actual_price > 0 else price
    coin_bought = (amount_thb / fill_price) * (1 - config.TRADING_FEE_RATE)

    dip_position.clear()
    dip_position.update({
        "symbol": symbol,
        "coin": coin,
        "entry_price": fill_price,
        "amount": coin_bought,
        "opened_at": datetime.now().isoformat(timespec="seconds"),
    })
    save_dip_state()

    detail = (
        f"[DipBuy][LIVE] ซื้อ {symbol.upper()} (ติดลบมากที่สุดบนกระดาน {candidate['percent_change']:.2f}%) "
        f"จำนวน {coin_bought:.8f} @ {fill_price:,.4f} THB"
    )
    print(detail)
    _notify(notifier, detail)
    return True


def _attempt_sell(client, notifier, current_prices: dict, tickers: dict) -> None:
    """ขาย position ที่เปิดอยู่ ก็ต่อเมื่อเข้าเงื่อนไขทั้ง 2 ข้อพร้อมกัน:
    1) % เปลี่ยนแปลงบนกระดาน (ticker.percentChange) ของเหรียญนั้นกลับมาเป็นบวกแล้ว (>= 0)
       เช่น ตอนซื้อติดลบอยู่ -15% ต้องรอจนกว่าค่านี้จะขยับกลับมาที่ 0% ขึ้นไปก่อน
    2) กำไรสุทธิหลังหักค่าธรรมเนียมซื้อ+ขายแล้ว (เทียบกับราคาที่เราซื้อมาจริง) ต้องเป็นบวกด้วย (ไม่ขาดทุน)
    ถ้าไม่ครบทั้ง 2 ข้อ จะถือต่อไป ไม่ขาย
    """
    import bot  # lazy import กัน circular import กับ bot.py

    if not dip_position:
        return

    coin = dip_position["coin"]
    symbol = dip_position["symbol"]
    entry_price = dip_position["entry_price"]
    amount = dip_position["amount"]

    current_price = current_prices.get(coin)
    if not current_price or current_price <= 0:
        return

    # เงื่อนไขที่ 1: % เปลี่ยนแปลงบนกระดานของเหรียญนี้ต้องกลับมาเป็นบวก (>= 0) แล้ว
    ticker_key = bot.symbol_to_ticker_key(symbol)
    ticker = tickers.get(ticker_key)
    board_pct = None
    if isinstance(ticker, dict):
        board_pct = ticker.get("percentChange")
        if board_pct is None:
            board_pct = ticker.get("percent_change")
        try:
            board_pct = float(board_pct)
        except (TypeError, ValueError):
            board_pct = None

    if board_pct is None:
        return  # หาค่าบนกระดานไม่ได้รอบนี้ (เช่น ticker หาย) -> ข้ามไปก่อน ไม่ขาย

    if board_pct < 0:
        return  # ยังติดลบอยู่บนกระดาน -> ถือต่อ ไม่ขาย

    # เงื่อนไขที่ 2: กำไรสุทธิหลังหักค่าธรรมเนียมซื้อ+ขาย (เทียบราคาที่เราซื้อมาจริง) ต้องเป็นบวก
    value_thb = amount * current_price
    fee = value_thb * config.TRADING_FEE_RATE
    net_received = value_thb - fee
    cost_basis = amount * entry_price
    net_profit = net_received - cost_basis

    if net_profit <= 0:
        return  # กระดานบวกแล้วก็จริง แต่หักค่าธรรมเนียมแล้วยังไม่คุ้ม -> ถือต่อ ไม่ขาย

    if config.DRY_RUN:
        bot.paper_thb += net_received
        bot.paper_balances[coin] = 0.0

        detail = (
            f"[DipBuy][DRY RUN] ขาย {symbol.upper()} (กระดานพลิกบวก {board_pct:.2f}%) กำไรสุทธิ {net_profit:,.2f} THB "
            f"(เข้า {entry_price:,.4f} -> ออก {current_price:,.4f})"
        )
        print(detail)
        _notify(notifier, detail)
        dip_position.clear()
        save_dip_state()
        return

    order_res = client.place_ask(symbol=symbol, amount=amount, rate=0.0, order_type="market")
    if order_res.get("error") == 0:
        detail = (
            f"[DipBuy][LIVE] ขาย {symbol.upper()} (กระดานพลิกบวก {board_pct:.2f}%) กำไรสุทธิ {net_profit:,.2f} THB "
            f"(เข้า {entry_price:,.4f} -> ออก {current_price:,.4f})"
        )
        print(detail)
        _notify(notifier, detail)
        dip_position.clear()
        save_dip_state()
    else:
        print(f"[DipBuy] ❌ ขาย {symbol.upper()} ไม่สำเร็จ: {order_res.get('message') or order_res.get('error')} — ลองใหม่รอบถัดไป")


def _notify(notifier, message: str) -> None:
    if not notifier:
        return
    try:
        notifier.send(message)
    except Exception:
        pass


def run_dip_buy_cycle(client, notifier, tickers: dict, current_prices: dict, available_thb: float = 0.0) -> None:
    """จุดเข้าเดียวที่ bot.py เรียกใช้ต่อรอบ (เพิ่มเติมจากขั้นตอนเดิม ไม่แทนที่).

    - ถ้ามี position เปิดอยู่: เช็คว่ากระดานพลิกบวก (>= 0) แล้วหรือยัง และกำไรหลังหักค่าธรรมเนียมหรือยัง
      ถ้าครบทั้ง 2 เงื่อนไข -> ขาย
    - ถ้าไม่มี position: จัดอันดับเหรียญตามที่ตกมากสุด แล้วไล่ซื้อทีละอันดับจนกว่าจะสำเร็จ
      โดยใช้ยอด THB ที่มีอยู่จริง ณ ขณะนั้น (available_thb) ตามค่า config ที่ตั้งไว้
    """
    if not getattr(config, "DIP_BUY_ENABLED", False):
        return

    try:
        if dip_position:
            _attempt_sell(client, notifier, current_prices, tickers)
            return

        import bot  # lazy import กัน circular import กับ bot.py
        broker_only = bot.get_broker_only_symbols(client) if not config.DRY_RUN else set()

        # จัดอันดับตาม % เปลี่ยนแปลงที่กระดาน Bitkub แสดงอยู่จริง ณ ขณะที่สแกนรอบนี้
        # (ดูรายละเอียดที่คอมเมนต์ใน _rank_by_biggest_drop ด้านบน)
        ranked = _rank_by_biggest_drop(tickers, exclude_symbols=broker_only)

        min_drop_percent = getattr(config, "DIP_BUY_MIN_DROP_PERCENT", -15.0)
        ranked = [c for c in ranked if c["percent_change"] <= min_drop_percent]
        if not ranked:
            print(f"[DipBuy] ⏭️ ไม่มีเหรียญไหนตกถึงเกณฑ์ {min_drop_percent:.2f}% ในรอบนี้ ข้ามไปก่อน")
            return

        max_try = getattr(config, "DIP_BUY_MAX_CANDIDATES_TO_TRY", 10)
        for candidate in ranked[:max_try]:
            if _attempt_buy(client, notifier, candidate, available_thb):
                break
    except Exception as e:
        print(f"[DipBuy] ⚠️ เกิดข้อผิดพลาดในรอบ dip-buy (ข้ามรอบนี้ ไม่กระทบกลยุทธ์หลัก): {e}")
