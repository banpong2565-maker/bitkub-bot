import os
from dotenv import load_dotenv
from telegram_notifier import TelegramNotifier

load_dotenv()

bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

print(f"Testing Telegram Notifier...")
print(f"Token: {bot_token[:15]}...{bot_token[-5:] if bot_token else ''}")
print(f"Chat ID: {chat_id}")

notifier = TelegramNotifier(bot_token, chat_id, enabled=True)
success = notifier.send("🤖 บอทเทรด Bitkub: ทดสอบระบบส่งข้อความ Telegram ทำงานได้ปกติครับ! ✅")
if success:
    print("[SUCCESS] Telegram message sent successfully!")
else:
    print("[FAILED] Failed to send Telegram message.")
