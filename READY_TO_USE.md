# Bitkub Bot – Ready-to-use configuration

ชุดนี้ปรับตามงานที่ร้องขอ:

- MAX_SPREAD_RATE = 1.8%
- MIN_NET_EDGE = 0.50%
- MIN_EXPECTED_PROFIT_RATE = 0.10%
- MIN_BUY_SCORE = 5
- จำกัด 1 position และเลือก candidate ที่ Net Edge สูงสุด
- All-in จาก THB ที่พร้อมใช้
- ปิด V7 Ultimate gate เพื่อไม่ให้เป็นตัวกรองซ้ำอีกชั้น
- SELL ต้องผ่านกำไรสุทธิหลังค่าธรรมเนียมก่อนส่งคำสั่ง
- ไม่ใช้ stop-loss/panic-loss เป็นเหตุผลขายขาดทุน
- Log แสดงค่า MAX_SPREAD_RATE จริง ไม่ hardcode 1.5%
- Log BUY แสดง Spread, Expected Move, Required และ Net Edge ให้ตรวจสอบง่าย

## ก่อน Deploy

1. ใส่ `BITKUB_API_KEY` และ `BITKUB_API_SECRET` ใน `.env` หรือ Environment Variables ของ Railway/Northflank
2. ตรวจ `DRY_RUN` ให้เป็น `False` เฉพาะเมื่อพร้อมเทรดเงินจริง
3. ตรวจ `TRADING_FEE_RATE` ให้ตรงกับค่าธรรมเนียมบัญชี Bitkub ของคุณ
4. อย่าอัปโหลด `.env` ที่มี API Secret ขึ้น GitHub

## ตรวจสอบก่อนรัน

```bash
python -m compileall -q .
PYTHONPATH=. pytest -q tests/test_profit_only_sell.py tests/test_position_size.py tests/test_env_config_sync.py
python bot.py
```

ผลทดสอบที่ตรวจในชุดนี้: 7 tests passed.
