# Anti-Chase BUY protection

เพิ่ม safety gate โดยไม่รื้อ Strategy / Sell Engine / Profit Vault เดิม

## ค่าเริ่มต้น
- ANTI_CHASE_ENABLED=true
- ANTI_CHASE_MAX_5M_EMA20_DISTANCE=0.025 (2.5%)
- ANTI_CHASE_MAX_15M_EMA20_DISTANCE=0.035 (3.5%)
- ANTI_CHASE_MAX_3BAR_RISE_5M=0.025 (2.5%)
- ANTI_CHASE_MAX_RSI_5M=72
- ANTI_CHASE_LARGE_GREEN_BAR_5M=0.012 (1.2%)
- ENTRY_MAX_LIVE_PRICE_DRIFT_RATE=0.004 (0.40%)

## การทำงาน
1. Scanner ต้องผ่าน Strategy/V7 เดิมก่อน
2. ตรวจว่าราคาไม่ได้ไล่สูงจาก EMA20/การพุ่งระยะสั้น
3. ก่อน BUY LIVE ตรวจ ticker อีกครั้ง
4. ถ้าราคา live สูงกว่าราคาที่สแกนเกิน 0.40% -> BLOCK BUY
5. ถ้าอ่านราคาสุดท้ายไม่ได้ -> BLOCK BUY (fail closed)
6. Market BUY ใช้ราคา ask ล่าสุดเป็น reference หลังผ่าน safety check

## เหตุผล
ป้องกันกรณี signal เกิดตอนราคา A แต่ราคาวิ่งขึ้นระหว่าง scan/API latency แล้วบอทซื้อที่ยอด ก่อนย่อลงทันที

## การทดสอบ
- py_compile ผ่าน
- pytest: 31 passed
