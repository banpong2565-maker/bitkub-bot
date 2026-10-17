# Supabase RLS Fix — V10.1

The error:

`new row violates row-level security policy for table bot_market_snapshots`

means the REST request reached Supabase but the publishable-key role was not allowed to INSERT.

## Fix

1. Open Supabase SQL Editor.
2. Run `SUPABASE_V10_1_RLS_FIX.sql`.
3. Restart the bot.
4. Confirm logs no longer show `POST bot_market_snapshots failed: 401`.
5. Confirm `bot_market_snapshots` contains new rows.

The migration keeps RLS enabled and grants only the bot's required SELECT/INSERT/UPDATE operations to `anon`/`authenticated`.

Do not disable RLS globally.

## Expected logs

`[Supabase] POST bot_market_snapshots ...` should stop reporting 401/42501.

Then the bot can write:
- bot_market_snapshots
- bot_decisions
- bot_positions
- bot_orders
- bot_prediction_outcomes

and read learning/prediction history.

The bot remains configured for LIVE trading; this change only fixes database permissions.
