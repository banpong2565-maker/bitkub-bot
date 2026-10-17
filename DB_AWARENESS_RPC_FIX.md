# DB Awareness RPC Fix

The log:

`PGRST202 ... function public.get_public_schema ... no matches were found`

means the PostgREST API cannot see the zero-argument function. This is normally because the SQL migration creating the RPC was never run, or PostgREST's schema cache has not reloaded yet.

## Fix

1. Open Supabase Dashboard -> SQL Editor.
2. Run `SUPABASE_DATABASE_AWARENESS_FIX.sql` in full.
3. Confirm the SQL verification query returns rows such as `bot_decisions`, `bot_market_snapshots`, `bot_positions`, `bot_orders`, and `bot_symbol_learning`.
4. Restart the bot container/process.

The migration explicitly creates:

`public.get_public_schema()`

and runs:

`NOTIFY pgrst, 'reload schema';`

## API verification

After deployment, the bot log should contain:

`[DB-AWARENESS] Schema loaded: ... tables`

It should no longer contain:

`PGRST202`

or:

`PostgreSQL schema unavailable`

## Important

Do not bypass the awareness gate by hardcoding columns. If the schema RPC cannot be read, the bot remains fail-closed for new trading, while already-open positions can still be managed by the existing execution/risk logic.
