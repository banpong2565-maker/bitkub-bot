-- Run this only if Supabase reports 401/403 on INSERT/UPDATE.
-- The bot uses the publishable key, so these policies permit the bot's telemetry.
-- If you prefer server-side security, use a secret/service-role key instead and keep it out of source control.

alter table public.bot_market_snapshots enable row level security;
alter table public.bot_decisions enable row level security;
alter table public.bot_positions enable row level security;
alter table public.bot_orders enable row level security;

drop policy if exists bot_learning_snapshots_insert on public.bot_market_snapshots;
create policy bot_learning_snapshots_insert
on public.bot_market_snapshots for insert
to anon, authenticated
with check (true);

drop policy if exists bot_learning_decisions_insert on public.bot_decisions;
create policy bot_learning_decisions_insert
on public.bot_decisions for insert
to anon, authenticated
with check (true);

drop policy if exists bot_learning_positions_insert on public.bot_positions;
create policy bot_learning_positions_insert
on public.bot_positions for insert
to anon, authenticated
with check (true);

drop policy if exists bot_learning_positions_update on public.bot_positions;
create policy bot_learning_positions_update
on public.bot_positions for update
to anon, authenticated
using (true)
with check (true);

drop policy if exists bot_learning_orders_insert on public.bot_orders;
create policy bot_learning_orders_insert
on public.bot_orders for insert
to anon, authenticated
with check (true);

-- The bot needs to read the aggregate learning view.
-- View access is granted separately.
grant select on public.bot_symbol_learning to anon, authenticated;
grant insert on public.bot_market_snapshots to anon, authenticated;
grant insert on public.bot_decisions to anon, authenticated;
grant insert, update on public.bot_positions to anon, authenticated;
grant insert on public.bot_orders to anon, authenticated;
