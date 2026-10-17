import requests


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, enabled: bool = False):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled and bool(bot_token) and bool(chat_id)

    def send(self, message: str) -> bool:
        if not self.enabled:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            if not data.get("ok", False):
                print(f"[Telegram] Send failed: {data}")
                return False
            return True
        except Exception as e:
            print(f"[Telegram] Send failed: {e}")
            return False
