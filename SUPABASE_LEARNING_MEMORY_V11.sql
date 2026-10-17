-- Bitkub Bot V11 - DB Learning Memory
-- Safe migration: does not drop existing trading tables.

-- 1) Learning view from CLOSED positions only.
create or replace view public.bot_symbol_learning as
select
  symbol,
  count(*)::int as trade_count,
  round((100.0 * avg(case when net_pnl_thb > 0 then 1 else 0 end))::numeric, 2) as win_rate,
  round(avg(net_pnl_rate)::numeric, 6) as avg_pnl_rate,
  round(avg(hold_minutes)::numeric, 2) as avg_hold_minutes
from public.bot_positions
where status = 'CLOSED'
  and closed_at is not null
group by symbol;

-- 2) The bot reads the view through PostgREST.
grant select on public.bot_symbol_learning to anon, authenticated;

-- 3) Helpful indexes for the learning query/update path.
create index if not exists bot_positions_symbol_closed_idx
  on public.bot_positions(symbol, status, closed_at desc);

create index if not exists bot_decisions_symbol_created_idx
  on public.bot_decisions(symbol, created_at desc);

-- 4) Verification queries.
-- select * from public.bot_symbol_learning order by trade_count desc;
-- select * from public.bot_symbol_learning where symbol = 'NEAR_THB';
-- select * from public.bot_decisions where symbol = 'NEAR_THB' order by created_at desc limit 20;
-- select * from public.bot_positions where symbol = 'NEAR_THB' order by opened_at desc limit 20;
