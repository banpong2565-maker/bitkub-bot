# Balance Visibility Fix — 2026-08-22

แก้ปัญหาบอทแสดงว่า THB ในพอร์ตไม่เพียงพอทั้งที่มีเงินใน Bitkub

## สิ่งที่แก้
- รองรับการอ่านยอดจาก Bitkub `/api/v4/wallet/balances`
- เพิ่ม fallback ไป `/api/v3/market/balances` หาก v4 ล้มเหลวหรือไม่มี THB
- รองรับรูปแบบ balance หลายแบบ: `available`, `free`, `balance`, `total`, `reserved`, `locked`
- แสดง log ยอด THB จริงที่อ่านได้จาก API
- หากยอด THB เป็นศูนย์ จะแสดงจำนวน asset และชื่อ key ที่ API ส่งกลับ เพื่อวิเคราะห์ได้ทันที
- ไม่เปลี่ยนกติกา Profit Vault: เงินที่ล็อก 80% ยังคงถูกกันออกจากเงินที่ใช้ซื้อ แต่ยอด THB จริงยังแสดงแยกชัดเจน

## ผลการทดสอบ
28 tests passed.
