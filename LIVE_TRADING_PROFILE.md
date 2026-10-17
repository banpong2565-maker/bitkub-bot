# Bitkub Bot V10 — LIVE Trading + Supabase Learning

This build is intended for real Bitkub trading (`DRY_RUN=false`).

## Entry protections
- 1H EMA50/EMA200 trend alignment is required.
- 1H price must be above EMA200.
- Sideways markets are filtered by ADX.
- Low-volume setups are filtered.
- Spread hard limit is 1.0% by default.
- BUY requires positive net edge after both-side fees, spread and profit buffer.
- V7 entry gate is enabled at 76/100.
- Order-book depth/imbalance is checked when the API supplies depth.
- Historical symbol performance can tighten the effective entry quality.
- Expected move is conservatively adjusted toward realized historical PnL only after enough completed trades exist.

## Position sizing
Default maximum allocation per new position is 25% of portfolio value / available THB, not all-in.
Maximum open positions defaults to 1.

## Learning
Every qualifying BUY signal is recorded in `bot_prediction_outcomes`, even when it is not executed.
The bot settles 5m/15m/30m/60m forward returns automatically and marks executed predictions when a real order succeeds.
Completed positions continue to populate `bot_symbol_learning`.

## Important
Live trading is enabled, but no strategy can guarantee profit. The API key should have trading permissions only; withdrawals should remain disabled.
Run `SUPABASE_V10_MIGRATION.sql` before starting so prediction learning can write successfully.
