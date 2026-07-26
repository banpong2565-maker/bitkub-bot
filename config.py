# config.py
"""Configuration for Bitkub Trading Bot.
All trading strategy and risk‑management parameters are defined as static constants.
Only secret credentials are loaded from environment variables.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------
# Secrets (loaded from environment variables)
# ---------------------------------------------------------------------
API_KEY = os.getenv("BITKUB_API_KEY", "your_api_key_here")
API_SECRET = os.getenv("BITKUB_API_SECRET", "your_api_secret_here")
BASE_URL = "https://api.bitkub.com"

# Telegram secrets
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "False").strip().lower() in ("true", "1", "yes")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Gemini secrets
GEMINI_ENABLED = os.getenv("GEMINI_ENABLED", "False").strip().lower() in ("true", "1", "yes")
GEMINI_KEY_COOLDOWN_SECONDS = 600
GEMINI_REQUEST_TIMEOUT_SECONDS = 15
GEMINI_SKIP_ON_FAIL = True
GEMINI_API_KEYS = [
    item.strip()
    for item in os.getenv("GEMINI_API_KEYS", "").split(",")
    if item.strip()
]
# New fallback threshold for Gemini failure (score out of 100)
GEMINI_FALLBACK_MIN_SCORE = 65

# ---------------------------------------------------------------------
# Trading parameters (static defaults)
# ---------------------------------------------------------------------
# Basic settings
SYMBOL = "btc_thb"
SYMBOL_UPPER = SYMBOL.upper()
SCAN_SYMBOLS = []
MAX_SCAN_SYMBOLS = 100
TRADE_AMOUNT_THB = 300.0  # Deprecated: use dynamic sizing instead
STRATEGY = "sma_crossover"
DRY_RUN = False
LOOP_INTERVAL_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 10
ORDER_TIMEOUT_SECONDS = 30

# Technical analysis & risk settings
TRADING_FEE_RATE = 0.0025
MIN_EXPECTED_PROFIT_RATE = 0.003
PROFIT_LOOKBACK_CANDLES = 8
MIN_TRADE_VALUE_THB = 50.0
MAX_OPEN_POSITIONS = 3
ALLOW_ADD_TO_POSITION = False
MAX_POSITION_VALUE_RATE = 0.25
MAX_PORTFOLIO_RISK_RATE = 0.02

# ATR and stop‑loss configuration
ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 2.0
ATR_TRAILING_MULTIPLIER = 1.8
ENTRY_SCORE_THRESHOLD = 6
# [FIX] ผู้ใช้ขอขยาย stop loss เป็น ~30% เพื่อลดการขายขาดทุนจากความผันผวนระยะสั้น
# หมายเหตุ: เดิม HARD_STOP_EXTRA_DROP_RATE (1.5%) แคบกว่า STOP_LOSS_RATE (3.5%) มาก
# ทำให้ "hard stop" ทำงานตัดขาดทุนก่อน stop loss ปกติเสมอ (ราคาตกแค่ 1.5% ก็ขายแล้ว)
# ตอนนี้ปรับให้ hard stop กว้างกว่า stop loss เล็กน้อย เพื่อเป็นแค่ตาข่ายนิรภัยกรณี flash crash จริง ๆ
HARD_STOP_EXTRA_DROP_RATE = 0.32
STOP_LOSS_RATE = 0.30
STOP_LOSS_CONFIRM_MINUTES = 5
PARTIAL_TAKE_PROFIT_RATE = 0.020
TAKE_PROFIT_RATE = 0.025
TRAILING_STOP_RATE = 0.012
TRAILING_MIN_PROFIT_RATE = 0.010
MIN_NET_PROFIT_RATE = 0.018  # [FIX] เพิ่มจาก 0.010 เพื่อกันไม่ให้กำไรถูกกินด้วยค่าธรรมเนียมตอนขาขึ้น
MIN_NET_EDGE = 0.012
PROFIT_LOCK_THRESHOLD = 0.001
PROFIT_LOCK_TRAILING_RATE = 0.0025
POSITION_ENTRY_PRICES = {}

# Dynamic position scoring thresholds
DYNAMIC_POS_SCORE_THRESHOLDS = {
    "high": (90, 100),
    "medium": (80, 89),
    "low": (70, 79),
}

# Exit gate settings
EXIT_GATE_MIN_HOLD_MINUTES = 45
EXIT_GATE_MIN_NET_PROFIT_RATE = 0.020
ATR_SL_ATR_MULTIPLIER = 1.5
ATR_LOW_MULTIPLIER = 3.5
ATR_HIGH_MULTIPLIER = 4.0
RECOVERY_EVAL_SECONDS = 300
REVERSAL_RSI_DROP = 15.0
REVERSAL_RSI_MAX = 60.0
REVERSAL_CONFIRM_CONSECUTIVE_BARS = 2
COOLDOWN_HIGH_FREQ = 3600
COOLDOWN_REVERSAL = 1200
ANTI_OVERTRADING_EDGE_BUMP = 0.01

# BTC regime filter
BTC_REGIME_FILTER_ENABLED = False
BTC_REGIME_ATR_SPIKE_MULTIPLIER = 3.0
BTC_REGIME_PRICE_DROP_24H = 0.05

# Time exit settings
TIME_EXIT_ENABLED = False
TIME_EXIT_MAX_HOLD_HOURS = 24.0
TIME_EXIT_MIN_PROFIT_RATE = 0.01

# Daily risk limit
DAILY_RISK_LIMIT_RATE = 0.08
MAX_TOTAL_POSITIONS = 3

# Order configuration
ORDER_TYPE_BUY = "market"
ORDER_TYPE_SELL = "market"
POST_ONLY = False

# Trade filters
MAX_SPREAD_RATE = 0.0150
MIN_24H_VOLUME_THB = 500000
TRADE_COOLDOWN_SECONDS = 180
MIN_HOLD_MINUTES = 30.0
REQUIRE_PRICE_ABOVE_EMA50_1H = False
REQUIRE_1H_UPTREND = False

SELL_CONFIRM_MIN_CONDITIONS = 1
SELL_RSI_THRESHOLD = 45.0

ADX_SIDEWAY_THRESHOLD = 15.0
SIDEWAY_FILTER_ENABLED = True

VOLUME_FILTER_ENABLED = True
MIN_VOLUME_RATIO = 0.30

PANIC_SELL_ENABLED = True
PANIC_SELL_VOLUME_SPIKE = 3.0
PANIC_SELL_PRICE_DROP = 0.03

# Dip‑entry filter — [NEW] ป้องกันการซื้อไล่ราคาที่จุดสูงสุด (buying the top)
# บังคับให้ราคาต้อง "ย่อตัวลงมา" จากจุดสูงสุดล่าสุดก่อนถึงจะเข้าซื้อ (ซื้อตอนราคาย่อ ไม่ใช่ตอนราคาพุ่ง)
DIP_ENTRY_ENABLED = True
DIP_MIN_PULLBACK_RATE = 0.015   # ต้องต่ำกว่าจุดสูงสุด (Donchian 20) อย่างน้อย 1.5% ถึงจะซื้อได้
DIP_MAX_PULLBACK_RATE = 0.12    # แต่ถ้าย่อลงมาเกิน 12% ถือว่าอาจเป็นขาลงจริง ไม่ใช่แค่ย่อ ให้งดซื้อ

# Last‑chance scanner settings
LAST_CHANCE_MIN_CONFIDENCE = 60.0
LAST_CHANCE_MAX_RISK = 5.0
LAST_CHANCE_MIN_VOLUME_THB = 1000000.0
LAST_CHANCE_MAX_SPREAD = 0.015
MIN_BUY_SCORE = 28.0
LAST_CHANCE_MIN_SCORE = 45.0

# ---------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------

def parse_position_entry_prices(raw_value: str) -> dict:
    """Parse a comma‑separated list of ``COIN:price`` entries into a dict.
    Empty or malformed entries are ignored.
    """
    prices = {}
    for item in raw_value.split(","):
        if ":" not in item:
            continue
        coin, price = item.split(":", 1)
        coin = coin.strip().upper()
        try:
            prices[coin] = float(price.strip())
        except ValueError:
            continue
    return prices

# ---------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------
def validate_config() -> bool:
    if not DRY_RUN:
        if API_KEY == "your_api_key_here" or API_SECRET == "your_api_secret_here":
            print("[Warning] LIVE mode is enabled but Bitkub API key/secret is missing")
            return False
    if TELEGRAM_ENABLED and (not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID):
        print("[Warning] TELEGRAM_ENABLED=True but token or chat id is missing")
        return False
    if GEMINI_ENABLED and not GEMINI_API_KEYS:
        print("[Warning] GEMINI_ENABLED=True but GEMINI_API_KEYS list is empty")
        return False
    if TRADE_AMOUNT_THB < MIN_TRADE_VALUE_THB:
        print("[Warning] TRADE_AMOUNT_THB is below MIN_TRADE_VALUE_THB")
        return False
    if TRADING_FEE_RATE < 0 or MIN_EXPECTED_PROFIT_RATE < 0:
        print("[Warning] Fee and expected profit settings must not be negative")
        return False
    if MAX_OPEN_POSITIONS < 1:
        print("[Warning] MAX_OPEN_POSITIONS must be at least 1")
        return False
    if not 0 < MAX_POSITION_VALUE_RATE <= 1:
        print("[Warning] MAX_POSITION_VALUE_RATE must be between 0 and 1")
        return False
    if STOP_LOSS_RATE <= 0 or PARTIAL_TAKE_PROFIT_RATE <= 0 or TAKE_PROFIT_RATE <= 0 or TRAILING_STOP_RATE <= 0:
        print("[Warning] Stop/take-profit settings must be positive")
        return False
    if TRAILING_MIN_PROFIT_RATE < 0 or MIN_NET_PROFIT_RATE < 0:
        # Debug validation print removed
        return False
    if PARTIAL_TAKE_PROFIT_RATE >= TAKE_PROFIT_RATE:
        print("[Warning] PARTIAL_TAKE_PROFIT_RATE must be lower than TAKE_PROFIT_RATE")
        return False
    if REQUEST_TIMEOUT_SECONDS <= 0 or ORDER_TIMEOUT_SECONDS <= 0:
        print("[Warning] Timeout settings must be positive")
        return False
    if GEMINI_KEY_COOLDOWN_SECONDS < 0 or GEMINI_REQUEST_TIMEOUT_SECONDS <= 0:
        print("[Warning] Gemini timeout/cooldown settings are invalid")
        return False
    if SELL_CONFIRM_MIN_CONDITIONS < 1 or SELL_CONFIRM_MIN_CONDITIONS > 3:
        print("[Warning] SELL_CONFIRM_MIN_CONDITIONS must be between 1 and 3")
        return False
    if SELL_RSI_THRESHOLD < 20 or SELL_RSI_THRESHOLD > 80:
        print("[Warning] SELL_RSI_THRESHOLD must be between 20 and 80")
        return False
    if ADX_SIDEWAY_THRESHOLD < 5 or ADX_SIDEWAY_THRESHOLD > 50:
        print("[Warning] ADX_SIDEWAY_THRESHOLD must be between 5 and 50")
        return False
    if PANIC_SELL_VOLUME_SPIKE < 1.5 or PANIC_SELL_VOLUME_SPIKE > 20:
        print("[Warning] PANIC_SELL_VOLUME_SPIKE must be between 1.5 and 20")
        return False
    if PANIC_SELL_PRICE_DROP < 0.005 or PANIC_SELL_PRICE_DROP > 0.30:
        print("[Warning] PANIC_SELL_PRICE_DROP must be between 0.005 and 0.30")
        return False
    return True

# ---------------------------------------------------------------------
# Runtime configuration logging
# ---------------------------------------------------------------------
def log_runtime_config(logger=None):
    """Print and optionally log the active runtime configuration.
    Used at startup to confirm the bot is using the intended values.
    """
    config_items = {
        "SYMBOL": SYMBOL,
        "STRATEGY": STRATEGY,
        "DRY_RUN": DRY_RUN,
        "LOOP_INTERVAL_SECONDS": LOOP_INTERVAL_SECONDS,
        "ENTRY_SCORE_THRESHOLD": ENTRY_SCORE_THRESHOLD,
        "MIN_NET_EDGE": MIN_NET_EDGE,
        "MIN_NET_PROFIT_RATE": MIN_NET_PROFIT_RATE,
        "MAX_SPREAD_RATE": MAX_SPREAD_RATE,
        "ADX_SIDEWAY_THRESHOLD": ADX_SIDEWAY_THRESHOLD,
        "VOLUME_FILTER_ENABLED": VOLUME_FILTER_ENABLED,
        "SIDEWAY_FILTER_ENABLED": SIDEWAY_FILTER_ENABLED,
        "GEMINI_ENABLED": GEMINI_ENABLED,
        "GEMINI_FALLBACK_MIN_SCORE": GEMINI_FALLBACK_MIN_SCORE,
    }
    lines = ["=== Runtime Configuration ==="]
    for k, v in config_items.items():
        lines.append(f"{k} = {v}")
    lines.append("=== End Configuration ===")
    output = "\n".join(lines)
    print(output)
    if logger:
        logger.info(output)
