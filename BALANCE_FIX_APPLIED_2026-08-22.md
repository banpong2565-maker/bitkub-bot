# Balance Fix Applied — 2026-08-22

แก้ปัญหา LIVE log แสดง `THB balance: 0.00` ทั้งที่ portfolio ยังมีมูลค่า

## แก้ไข
- ใช้ Bitkub V4 funding wallet โดยส่ง `?segment=funding` ให้ `/api/v4/wallet/balances`
- รวม query string ใน HMAC signature ให้ตรงกับ URL ที่เรียกจริง
- ใช้ `/api/v4/wallet/assets?segment=funding` เป็น fallback
- หาก API ตอบสำเร็จแต่ไม่พบ THB จะไม่ตีความเป็น THB=0 และไม่เปิด BUY
- เพิ่ม diagnostic log แสดง THB available/reserved/total และรายชื่อ asset ที่ API คืนมา
- ไม่ใช้ V3 balances endpoint เป็น live fallback

## Verification
- `python -m py_compile *.py tests/*.py` ผ่าน
- `pytest -q` ผ่าน: 31 tests
