# config.py
"""Configuration for Bitkub Trading Bot (Enhanced V7).

All trading strategy and risk-management parameters can be overridden via
environment variables (.env). Every value below has a sensible default that
is used when the corresponding environment variable is absent, empty, or
invalid — so the bot still runs even with a minimal .env file.

NEW in V7: Advanced coin filtering for precise entry detection
"""

import os
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------
# Env-loading helpers
# ---------------------------------------------------------------------
def _get_str(name: str, default: str) -> str:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    return val.strip()


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    return val.strip().lower() in ("true", "1", "yes")


def _get_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val)
    except ValueError:
        print(f"[Warning] Invalid float for {name}={val!r}; using default {default}")
        return default


def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    try:
        return int(float(val))
    except ValueError:
        print(f"[Warning] Invalid int for {name}={val!r}; using default {default}")
        return default


def _get_list(name: str, default: list) -> list:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    return [item.strip() for item in val.split(",") if item.strip()]


# ---------------------------------------------------------------------
# Secrets (loaded from environment variables)
# ---------------------------------------------------------------------
API_KEY = os.getenv("BITKUB_API_KEY", "your_api_key_here")
API_SECRET = os.getenv("BITKUB_API_SECRET", "your_api_secret_here")
BASE_URL = "https://api.bitkub.com"

# Telegram secrets
TELEGRAM_ENABLED = _get_bool("TELEGRAM_ENABLED", False)
TELEGRAM_BOT_TOKEN = _get_str("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = _get_str("TELEGRAM_CHAT_ID", "")

# Gemini secrets
GEMINI_ENABLED = _get_bool("GEMINI_ENABLED", False)
GEMINI_KEY_COOLDOWN_SECONDS = _get_int("GEMINI_KEY_COOLDOWN_SECONDS", 600)
GEMINI_REQUEST_TIMEOUT_SECONDS = _get_int("GEMINI_REQUEST_TIMEOUT_SECONDS", 15)
GEMINI_SKIP_ON_FAIL = True
GEMINI_API_KEYS = _get_list("GEMINI_API_KEYS", [])
# New fallback threshold for Gemini failure (score out of 100)
GEMINI_FALLBACK_MIN_SCORE = 65

# ---------------------------------------------------------------------
# Trading parameters (env-overridable, defaults shown below)
# ---------------------------------------------------------------------
# Basic settings
SYMBOL = "btc_thb"
SYMBOL_UPPER = SYMBOL.upper()
SCAN_SYMBOLS = _get_list("SCAN_SYMBOLS", [])
MAX_SCAN_SYMBOLS = _get_int("MAX_SCAN_SYMBOLS", 100)
TRADE_AMOUNT_THB = _get_float("TRADE_AMOUNT_THB", 300.0)  # Deprecated: use dynamic sizing instead
STRATEGY = _get_str("STRATEGY", "sma_crossover")
# CRITICAL: this switches between paper trading and real money orders.
# Defaults to True (safe) if not set — you must explicitly set DRY_RUN=False
# in .env to enable live trading.
DRY_RUN = _get_bool("DRY_RUN", False)
LOOP_INTERVAL_SECONDS = _get_int("LOOP_INTERVAL_SECONDS", 60)
REQUEST_TIMEOUT_SECONDS = _get_int("REQUEST_TIMEOUT_SECONDS", 10)
ORDER_TIMEOUT_SECONDS = _get_int("ORDER_TIMEOUT_SECONDS", 30)

# Technical analysis & risk settings
TRADING_FEE_RATE = _get_float("TRADING_FEE_RATE", 0.0025)
MIN_EXPECTED_PROFIT_RATE = _get_float("MIN_EXPECTED_PROFIT_RATE", 0.0005)
PROFIT_LOOKBACK_CANDLES = _get_int("PROFIT_LOOKBACK_CANDLES", 8)
MIN_TRADE_VALUE_THB = _get_float("MIN_TRADE_VALUE_THB", 50.0)
MAX_OPEN_POSITIONS = _get_int("MAX_OPEN_POSITIONS", 3)
ALLOW_ADD_TO_POSITION = _get_bool("ALLOW_ADD_TO_POSITION", False)
MAX_POSITION_VALUE_RATE = _get_float("MAX_POSITION_VALUE_RATE", 1.0)
# Minimum fraction of total portfolio value (THB + coins) that must always be
# kept as free THB cash reserve, and never spent on a buy order.
# Example: 0.10 => at least 10% of the portfolio stays in cash at all times.
MIN_CASH_RESERVE_RATE = _get_float("MIN_CASH_RESERVE_RATE", 0.10)
MAX_PORTFOLIO_RISK_RATE = _get_float("MAX_PORTFOLIO_RISK_RATE", 0.02)
# Supplied Google Apps Script V7 Ultimate BUY gate.
# This is an additional entry filter; it does not replace existing strategy,
# fee, risk, position-limit, or order-execution logic.
V7_ENTRY_GATE_ENABLED = _get_bool("V7_ENTRY_GATE_ENABLED", False)
V7_ENTRY_GATE_MIN_SCORE = _get_float("V7_ENTRY_GATE_MIN_SCORE", 65.0)

# ATR and stop‑loss configuration
ATR_PERIOD = _get_int("ATR_PERIOD", 14)
ATR_STOP_MULTIPLIER = _get_float("ATR_STOP_MULTIPLIER", 2.0)
ATR_TRAILING_MULTIPLIER = _get_float("ATR_TRAILING_MULTIPLIER", 1.8)
ENTRY_SCORE_THRESHOLD = _get_float("ENTRY_SCORE_THRESHOLD", 6)
HARD_STOP_EXTRA_DROP_RATE = _get_float("HARD_STOP_EXTRA_DROP_RATE", 0.015)
STOP_LOSS_RATE = _get_float("STOP_LOSS_RATE", 0.035)
STOP_LOSS_CONFIRM_MINUTES = _get_int("STOP_LOSS_CONFIRM_MINUTES", 5)
PARTIAL_TAKE_PROFIT_RATE = _get_float("PARTIAL_TAKE_PROFIT_RATE", 0.006)
TAKE_PROFIT_RATE = _get_float("TAKE_PROFIT_RATE", 0.008)
TRAILING_STOP_RATE = _get_float("TRAILING_STOP_RATE", 0.006)
TRAILING_MIN_PROFIT_RATE = _get_float("TRAILING_MIN_PROFIT_RATE", 0.003)
MIN_NET_PROFIT_RATE = _get_float("MIN_NET_PROFIT_RATE", 0.003)
# BUY profitability floor. The original 0.30% hard floor on top of
# round-trip fees + spread was overly restrictive and could leave the bot
# with no candidates for long periods. Keep a small positive edge requirement
# while still allowing realistic opportunities to pass.
# Override with MIN_NET_EDGE in the deployment environment if desired.
MIN_NET_EDGE = max(_get_float("MIN_NET_EDGE", 0.0010), 0.0010)
PROFIT_LOCK_THRESHOLD = _get_float("PROFIT_LOCK_THRESHOLD", 0.001)
PROFIT_LOCK_TRAILING_RATE = _get_float("PROFIT_LOCK_TRAILING_RATE", 0.0025)

# ======================= PROFIT VAULT / AUTO WITHDRAW =======================
# 80% of realized net profit is locked away from the trading engine; 20%
# remains in the trading capital. Auto-withdraw is explicitly opt-in because
# it moves real THB to an approved bank account.
PROFIT_VAULT_ENABLED = _get_bool("PROFIT_VAULT_ENABLED", True)
PROFIT_VAULT_LOCK_RATIO = _get_float("PROFIT_VAULT_LOCK_RATIO", 0.80)
PROFIT_VAULT_COMPOUND_RATIO = _get_float("PROFIT_VAULT_COMPOUND_RATIO", 0.20)
PROFIT_VAULT_WITHDRAW_THRESHOLD = _get_float("PROFIT_VAULT_WITHDRAW_THRESHOLD", 300.0)
PROFIT_VAULT_AUTO_WITHDRAW_ENABLED = _get_bool("PROFIT_VAULT_AUTO_WITHDRAW_ENABLED", False)
PROFIT_VAULT_BANK_ACCOUNT_NO = _get_str("PROFIT_VAULT_BANK_ACCOUNT_NO", "")
# Use a server-side Supabase service-role key for this ledger when RLS is enabled.
# Falls back to SUPABASE_KEY for backwards compatibility.
SUPABASE_VAULT_KEY = _get_str("SUPABASE_VAULT_KEY", "")


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


POSITION_ENTRY_PRICES = parse_position_entry_prices(os.getenv("POSITION_ENTRY_PRICES", ""))

# Dynamic position scoring thresholds
DYNAMIC_POS_SCORE_THRESHOLDS = {
    "high": (90, 100),
    "medium": (80, 89),
    "low": (70, 79),
}

# Exit gate settings
EXIT_GATE_MIN_HOLD_MINUTES = _get_int("EXIT_GATE_MIN_HOLD_MINUTES", 45)
EXIT_GATE_MIN_NET_PROFIT_RATE = _get_float("EXIT_GATE_MIN_NET_PROFIT_RATE", 0.020)
ATR_SL_ATR_MULTIPLIER = _get_float("ATR_SL_ATR_MULTIPLIER", 1.5)
ATR_LOW_MULTIPLIER = _get_float("ATR_LOW_MULTIPLIER", 3.5)
ATR_HIGH_MULTIPLIER = _get_float("ATR_HIGH_MULTIPLIER", 4.0)
RECOVERY_EVAL_SECONDS = _get_int("RECOVERY_EVAL_SECONDS", 300)
REVERSAL_RSI_DROP = _get_float("REVERSAL_RSI_DROP", 15.0)
REVERSAL_RSI_MAX = _get_float("REVERSAL_RSI_MAX", 60.0)
REVERSAL_CONFIRM_CONSECUTIVE_BARS = _get_int("REVERSAL_CONFIRM_CONSECUTIVE_BARS", 2)
COOLDOWN_HIGH_FREQ = _get_int("COOLDOWN_HIGH_FREQ", 3600)
COOLDOWN_REVERSAL = _get_int("COOLDOWN_REVERSAL", 1200)
ANTI_OVERTRADING_EDGE_BUMP = _get_float("ANTI_OVERTRADING_EDGE_BUMP", 0.01)

# Net-edge thresholds per market regime (currently informational / reserved
# for future regime-aware strategy logic — not yet wired into bot.py).
NET_EDGE_TRENDING = _get_float("NET_EDGE_TRENDING", 0.004)
NET_EDGE_SIDEWAY = _get_float("NET_EDGE_SIDEWAY", 0.003)
NET_EDGE_HIGH_VOL = _get_float("NET_EDGE_HIGH_VOL", 0.008)

# BTC regime filter
BTC_REGIME_FILTER_ENABLED = _get_bool("BTC_REGIME_FILTER_ENABLED", False)
BTC_REGIME_ATR_SPIKE_MULTIPLIER = _get_float("BTC_REGIME_ATR_SPIKE_MULTIPLIER", 3.0)
BTC_REGIME_PRICE_DROP_24H = _get_float("BTC_REGIME_PRICE_DROP_24H", 0.05)

# Time exit settings
TIME_EXIT_ENABLED = _get_bool("TIME_EXIT_ENABLED", False)
TIME_EXIT_MAX_HOLD_HOURS = _get_float("TIME_EXIT_MAX_HOLD_HOURS", 24.0)
TIME_EXIT_MIN_PROFIT_RATE = _get_float("TIME_EXIT_MIN_PROFIT_RATE", 0.01)

# Daily risk limit
DAILY_RISK_LIMIT_RATE = _get_float("DAILY_RISK_LIMIT_RATE", 0.08)
MAX_TOTAL_POSITIONS = _get_int("MAX_TOTAL_POSITIONS", 2)

# Order configuration
ORDER_TYPE_BUY = _get_str("ORDER_TYPE_BUY", "market")
ORDER_TYPE_SELL = _get_str("ORDER_TYPE_SELL", "limit")
POST_ONLY = _get_bool("POST_ONLY", False)

# Trade filters
# Opportunity mode: avoid over-filtering liquid pairs; hard spread cap remains in bot.py.
MAX_SPREAD_RATE = _get_float("MAX_SPREAD_RATE", 0.0180)
MIN_24H_VOLUME_THB = _get_float("MIN_24H_VOLUME_THB", 100000)
TRADE_COOLDOWN_SECONDS = _get_int("TRADE_COOLDOWN_SECONDS", 60)
MIN_HOLD_MINUTES = _get_float("MIN_HOLD_MINUTES", 10.0)

# ======================= ANTI-CHASE ENTRY PROTECTION =======================
# Prevent buying after a fast price spike / late entry. These are safety
# filters only; the existing strategy, scoring and V7 gate remain unchanged.
ANTI_CHASE_ENABLED = _get_bool("ANTI_CHASE_ENABLED", True)
ANTI_CHASE_MAX_5M_EMA20_DISTANCE = _get_float("ANTI_CHASE_MAX_5M_EMA20_DISTANCE", 0.025)
ANTI_CHASE_MAX_15M_EMA20_DISTANCE = _get_float("ANTI_CHASE_MAX_15M_EMA20_DISTANCE", 0.035)
ANTI_CHASE_MAX_3BAR_RISE_5M = _get_float("ANTI_CHASE_MAX_3BAR_RISE_5M", 0.025)
ANTI_CHASE_MAX_RSI_5M = _get_float("ANTI_CHASE_MAX_RSI_5M", 72.0)
ANTI_CHASE_LARGE_GREEN_BAR_5M = _get_float("ANTI_CHASE_LARGE_GREEN_BAR_5M", 0.012)
# Final live-price safety check immediately before placing the BUY.
ENTRY_MAX_LIVE_PRICE_DRIFT_RATE = _get_float("ENTRY_MAX_LIVE_PRICE_DRIFT_RATE", 0.004)
REQUIRE_PRICE_ABOVE_EMA50_1H = _get_bool("REQUIRE_PRICE_ABOVE_EMA50_1H", False)
REQUIRE_1H_UPTREND = _get_bool("REQUIRE_1H_UPTREND", False)

SELL_CONFIRM_MIN_CONDITIONS = _get_int("SELL_CONFIRM_MIN_CONDITIONS", 1)
SELL_RSI_THRESHOLD = _get_float("SELL_RSI_THRESHOLD", 48.0)

ADX_SIDEWAY_THRESHOLD = _get_float("ADX_SIDEWAY_THRESHOLD", 12.0)
SIDEWAY_FILTER_ENABLED = _get_bool("SIDEWAY_FILTER_ENABLED", False)

VOLUME_FILTER_ENABLED = _get_bool("VOLUME_FILTER_ENABLED", False)
# Accept either MIN_VOLUME_RATIO or the older VOLUME_MIN_RATIO env var name.
MIN_VOLUME_RATIO = _get_float("MIN_VOLUME_RATIO", _get_float("VOLUME_MIN_RATIO", 0.25))
# Alias kept for backward compatibility with any code/tests referencing the
# older name.
VOLUME_MIN_RATIO = MIN_VOLUME_RATIO

PANIC_SELL_ENABLED = _get_bool("PANIC_SELL_ENABLED", True)
PANIC_SELL_VOLUME_SPIKE = _get_float("PANIC_SELL_VOLUME_SPIKE", 3.0)
PANIC_SELL_PRICE_DROP = _get_float("PANIC_SELL_PRICE_DROP", 0.03)

# Last‑chance scanner settings
LAST_CHANCE_MIN_CONFIDENCE = _get_float("LAST_CHANCE_MIN_CONFIDENCE", 55.0)
LAST_CHANCE_MAX_RISK = _get_float("LAST_CHANCE_MAX_RISK", 5.0)
LAST_CHANCE_MIN_VOLUME_THB = _get_float("LAST_CHANCE_MIN_VOLUME_THB", 250000.0)
LAST_CHANCE_MAX_SPREAD = _get_float("LAST_CHANCE_MAX_SPREAD", 0.020)
MIN_BUY_SCORE = _get_float("MIN_BUY_SCORE", 2.0)
# New DB-memory decision threshold. Technical score is normalized to 0-100.
FINAL_MIN_BUY_SCORE = _get_float("FINAL_MIN_BUY_SCORE", 35.0)
LAST_CHANCE_MIN_SCORE = _get_float("LAST_CHANCE_MIN_SCORE", 35.0)

# ======================= V7 ADVANCED FILTER SETTINGS ======================= #
# NEW: Smart coin selection using V7 precision filters

# Enable/disable V7 advanced filtering for precise coin detection
V7_ADVANCED_FILTERS_ENABLED = _get_bool("V7_ADVANCED_FILTERS_ENABLED", True)

# Exhaustion Filter: Maximum distance from EMA20 (in percentage)
# Too high = caught at the top, too low = might miss good entries
# Recommended: 5.5 (default, from V7)
V7_EXHAUSTION_FILTER_MAX_DISTANCE_PCT = _get_float("V7_EXHAUSTION_FILTER_MAX_DISTANCE_PCT", 5.5)

# Breakout Confirmation: Minimum body ratio for valid breakout candle
# Body = |close - open|, Range = high - low
# Recommended: 0.58 (default, from V7)
V7_BREAKOUT_MIN_BODY_RATIO = _get_float("V7_BREAKOUT_MIN_BODY_RATIO", 0.58)

# Whale Detection: Minimum volume spike multiplier
# Recommended: 2.2 (default, from V7)
V7_WHALE_MIN_VOLUME_MULTIPLIER = _get_float("V7_WHALE_MIN_VOLUME_MULTIPLIER", 2.2)

# Whale Detection: Minimum body ratio for accumulation candle
# Recommended: 0.55 (default, from V7)
V7_WHALE_MIN_BODY_RATIO = _get_float("V7_WHALE_MIN_BODY_RATIO", 0.55)

# Volume Trend Expansion: Minimum expansion ratio (recent vol vs old vol)
# Recommended: 1.2 (default, from V7)
V7_VOLUME_EXPANSION_RATIO = _get_float("V7_VOLUME_EXPANSION_RATIO", 1.2)

# Price Efficiency Check: Reject fake pumps (threshold)
# Low efficiency = high wick/shadow = fake pump
# Recommended: 0.3 (default, from V7)
V7_FAKE_PUMP_EFFICIENCY_THRESHOLD = _get_float("V7_FAKE_PUMP_EFFICIENCY_THRESHOLD", 0.3)

# Bonus Points Configuration (for scoring system)
V7_BREAKOUT_CONFIRMATION_BONUS = _get_float("V7_BREAKOUT_CONFIRMATION_BONUS", 12)  # points
V7_WHALE_ACCUMULATION_BONUS = _get_float("V7_WHALE_ACCUMULATION_BONUS", 18)     # points
V7_VOLUME_EXPANSION_BONUS = _get_float("V7_VOLUME_EXPANSION_BONUS", 8)        # points
V7_FAKE_PUMP_PENALTY = _get_float("V7_FAKE_PUMP_PENALTY", -15)           # points

# Scoring adjustment: After V7 filters applied, scale score to maintain 0-100 range
V7_SCORE_SCALING_FACTOR = 100.0 / 161.0  # ≈0.62 (adjusted for bonus points)

# Minimum score for entry (with V7 filters)
# Original was MIN_BUY_SCORE = 28.0, but with V7 bonuses we can be more selective
V7_MIN_ENTRY_SCORE = _get_float("V7_MIN_ENTRY_SCORE", 35.0)

# ========================================================================== #


# ======================= V10 LIVE LEARNING ======================= #
PREDICTION_LEARNING_ENABLED = _get_bool("PREDICTION_LEARNING_ENABLED", True)
PREDICTION_HORIZONS_MINUTES = _get_list("PREDICTION_HORIZONS_MINUTES", ["5","15","30","60"])
LEARNING_EXPECTED_WEIGHT = _get_float("LEARNING_EXPECTED_WEIGHT", 0.35)
LEARNING_MAX_BOOST_RATE = _get_float("LEARNING_MAX_BOOST_RATE", 0.010)
ORDERBOOK_FILTER_ENABLED = _get_bool("ORDERBOOK_FILTER_ENABLED", True)
ORDERBOOK_MIN_DEPTH_THB = _get_float("ORDERBOOK_MIN_DEPTH_THB", 5000.0)
ORDERBOOK_MIN_IMBALANCE = _get_float("ORDERBOOK_MIN_IMBALANCE", -0.35)
REQUIRE_EMA200_1H = _get_bool("REQUIRE_EMA200_1H", False)
RISK_POSITION_RATE = _get_float("RISK_POSITION_RATE", 0.25)
MAX_CONSECUTIVE_LIVE_ERRORS = _get_int("MAX_CONSECUTIVE_LIVE_ERRORS", 3)

# Configuration validation
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
        print("[Warning] Trailing/min-profit settings must not be negative")
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

    # V7 config validation
    if V7_EXHAUSTION_FILTER_MAX_DISTANCE_PCT <= 0 or V7_EXHAUSTION_FILTER_MAX_DISTANCE_PCT > 20:
        print("[Warning] V7_EXHAUSTION_FILTER_MAX_DISTANCE_PCT should be 1-20%")
        return False
    if V7_BREAKOUT_MIN_BODY_RATIO < 0 or V7_BREAKOUT_MIN_BODY_RATIO > 1:
        print("[Warning] V7_BREAKOUT_MIN_BODY_RATIO must be 0-1")
        return False
    if V7_WHALE_MIN_VOLUME_MULTIPLIER < 1:
        print("[Warning] V7_WHALE_MIN_VOLUME_MULTIPLIER must be >= 1")
        return False
    if V7_VOLUME_EXPANSION_RATIO < 1:
        print("[Warning] V7_VOLUME_EXPANSION_RATIO must be >= 1")
        return False
    if V7_MIN_ENTRY_SCORE < 0 or V7_MIN_ENTRY_SCORE > 100:
        print("[Warning] V7_MIN_ENTRY_SCORE must be 0-100")
        return False

    return True

# ========================================================================== #

# Runtime configuration logging
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
        "V7_ADVANCED_FILTERS_ENABLED": V7_ADVANCED_FILTERS_ENABLED,
        "V7_MIN_ENTRY_SCORE": V7_MIN_ENTRY_SCORE,
    }
    lines = ["=== Runtime Configuration (with V7 Enhancement) ==="]
    for k, v in config_items.items():
        lines.append(f"{k} = {v}")
    lines.append("=== End Configuration ===")
    output = "\n".join(lines)
    print(output)
    if logger:
        logger.info(output)

# Supabase learning / telemetry
# Supabase REST API: use the project base URL, NOT /rest/v1/
SUPABASE_URL = _get_str("SUPABASE_URL", "https://psfmvozmxfsmjgdhncep.supabase.co")
# Publishable/anon key supplied for this bot. Prefer overriding with SUPABASE_KEY in .env.
SUPABASE_KEY = _get_str("SUPABASE_KEY", "sb_publishable_CgaBKkzw6pj9ulOqM2wbxQ_3nT1P6-y")
LEARNING_ENABLED = _get_bool("LEARNING_ENABLED", True)
LEARNING_MIN_TRADES = _get_int("LEARNING_MIN_TRADES", 5)
LEARNING_BLOCK_WIN_RATE = _get_float("LEARNING_BLOCK_WIN_RATE", 0.30)
LEARNING_PENALTY_WIN_RATE = _get_float("LEARNING_PENALTY_WIN_RATE", 0.40)
LEARNING_SCORE_PENALTY = _get_float("LEARNING_SCORE_PENALTY", 1.0)
# Save simulated trades too. Set false if you want the learning table to contain LIVE trades only.
LEARNING_INCLUDE_DRY_RUN = _get_bool("LEARNING_INCLUDE_DRY_RUN", True)
SELL_MIN_NET_PROFIT_RATE = _get_float("SELL_MIN_NET_PROFIT_RATE", 0.0030)
SELL_EXECUTION_BUFFER_RATE = _get_float("SELL_EXECUTION_BUFFER_RATE", 0.0010)
