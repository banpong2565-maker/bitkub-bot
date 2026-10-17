# Trading strategy changes

- Buy size: uses 100% of currently available THB balance.
- Stop loss / hard stop: removed from exit logic.
- Panic loss exit: removed; the bot no longer intentionally sells at a loss.
- Every sell has an exact final gate: net sell proceeds after the sell fee must be greater than the stored THB cost basis, including the buy-side fee.
- Partial TP exits were changed to full exits so cost accounting stays exact.
- Buy frequency defaults were loosened: lower score threshold, lower net-edge requirement, wider spread allowance, shorter cooldown, shorter minimum hold, and sideway/low-volume entry filters disabled by default.
- Consecutive-loss auto-pause was removed because loss-making sells are blocked by design.

## Railway / existing .env

Existing environment variables override config.py defaults. To activate the new entry settings in an existing deployment, update:

MIN_EXPECTED_PROFIT_RATE=0.0015
MAX_SPREAD_RATE=0.018
MIN_NET_EDGE=0.005
MIN_BUY_SCORE=5
TRADE_COOLDOWN_SECONDS=60
MIN_HOLD_MINUTES=5
SIDEWAY_FILTER_ENABLED=false
VOLUME_FILTER_ENABLED=false

These changes do not require changing the Bitkub API credentials.
