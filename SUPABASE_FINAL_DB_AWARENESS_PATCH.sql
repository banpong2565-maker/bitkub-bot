-- V12.2 Production DB Memory / Decision Audit Fix
-- Run in Supabase SQL Editor

create or replace function public.get_public_schema()
returns table (
  schema_name text,
  table_name text,
  column_name text,
  data_type text,
  ordinal_position integer
)
language sql
security definer
set search_path = public
as $$
  select
    c.table_schema::text,
    c.table_name::text,
    c.column_name::text,
    c.data_type::text,
    c.ordinal_position::integer
  from information_schema.columns c
  where c.table_schema = 'public'
  order by c.table_name, c.ordinal_position;
$$;

grant execute on function public.get_public_schema() to anon, authenticated, service_role;

alter table public.bot_decisions
  add column if not exists learning_score numeric;

notify pgrst, 'reload schema';
