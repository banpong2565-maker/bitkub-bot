# Profit Runner + Supabase Learning

This version keeps the existing hold-until-sell-signal behavior and adds persistent exit telemetry.

## Supabase migration
Run `SUPABASE_PROFIT_RUNNER_MIGRATION.sql` once in the Supabase SQL Editor after the existing schema.

Added `bot_positions` columns:
- `highest_price`
- `max_profit_pct`
- `trailing_stop`
- `sell_signal`
- `max_profit_minutes`
- `peak_to_sell_pct`

Added view: `bot_symbol_exit_learning`.

## Runtime behavior
- Fixed take-profit exits remain disabled in `HOLD_UNTIL_SELL_SIGNAL` mode.
- The bot continuously records the highest price and maximum profit reached.
- After at least 10 closed trades for a symbol, historical average peak-to-sell giveback can widen the trailing protection up to 3%.
- Learned trailing is a safety/profit-protection exit; technical bearish signals remain the primary exit.
- If Supabase is unavailable or the migration has not been run, the bot safely falls back to its existing behavior.

## Tests
`PYTHONPATH=. pytest -q tests test_scanner.py` -> 25 passed.
