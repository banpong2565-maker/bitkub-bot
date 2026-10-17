# LIVE deployment

1. Run SUPABASE_SCHEMA_LIVE.sql in Supabase SQL Editor.
2. Set BITKUB_API_KEY and BITKUB_API_SECRET in the deployment environment.
3. Set DRY_RUN=false only after verifying the credentials and SQL.
4. Set SUPABASE_URL to the supplied project URL and SUPABASE_KEY to the bot's server-side key.
5. Start with a small TRADE_AMOUNT_THB / MAX_POSITION_VALUE_RATE.

The bot blocks broker-source symbols before place-bid/place-ask (Bitkub Error 61), and it blocks exits unless the estimated net result clears the configured minimum profit plus a slippage buffer. No strategy can guarantee profit.
