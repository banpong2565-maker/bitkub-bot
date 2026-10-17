# Database Awareness — Production Integration

This patch adds a PostgreSQL schema-awareness layer to the Bitkub trading bot.

## Architecture

```text
Bot startup / scan
      |
      v
Supabase RPC: get_public_schema()
      |
      v
information_schema.columns
      |
      +--> tables / columns / PostgreSQL data types
      |
      v
DatabaseAwareness cache (60s)
      |
      +--> filter every INSERT/PATCH payload
      +--> warn on missing columns
      +--> fail closed on schema/query failure for the affected symbol
      |
      v
bot_market_snapshots -> latest snapshot
      |
      v
bot_symbol_learning -> historical memory
      |
      v
Technical Score + Learning Score
      |
      v
BUY / SELL / HOLD / BLOCK
      |
      v
bot_decisions
```

## Production safety rules

1. The scanner refreshes database schema before a trading scan.
2. If schema discovery fails, the scan fails closed; it does not guess columns.
3. Every write is filtered against `information_schema.columns`.
4. Missing optional decision-learning columns are logged and skipped; the bot continues.
5. A failed learning query or latest market snapshot causes only that symbol to be skipped.
6. Missing history is a normal cold-start state: Learning Score = 0.
7. Closed positions continue to feed `bot_symbol_learning` automatically.

## Supabase setup

Run `SUPABASE_DATABASE_AWARENESS.sql` first, then verify:

```sql
select * from public.get_public_schema();
```

The RPC returns `table_name`, `column_name`, `data_type`, and `ordinal_position` from `information_schema.columns` for the `public` schema.

## Decision example

```text
[NEAR_THB] SCORE: Technical=70 | Learning=40 | Final=110 |
Trades=50 | WinRate=72 | AvgPNL=0.015

[NEAR_THB] ACTION=BUY | Technical Score=70 | Learning Score=40 |
Final Score=110 | Learning(trades=50, win_rate=72%, avg_pnl=1.50%)
```

## Learning formula

- trade_count >= 30: +10
- win_rate >= 70%: +20
- 50% <= win_rate < 70%: +10
- win_rate < 50%: -10
- avg_pnl_rate > 1%: +10
- 0% <= avg_pnl_rate <= 1%: +5
- avg_pnl_rate < 0%: -10

`Final Score = Technical Score + Learning Score`.

## Deploy

1. Apply the SQL migration in Supabase SQL Editor.
2. Copy the project files to the live bot directory.
3. Keep the existing `.env` credentials and trading settings.
4. Run the supplied `run_bot.sh`.
5. Confirm `[DB-AWARENESS] Schema loaded:` appears in logs before trading.
