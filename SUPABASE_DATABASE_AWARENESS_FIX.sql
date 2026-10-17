-- FIX for DB-AWARENESS RPC 404 / PGRST202
-- Run this entire file in Supabase SQL Editor.

create or replace function public.get_public_schema()
returns table (
    table_name text,
    column_name text,
    data_type text,
    ordinal_position integer
)
language sql
security definer
set search_path = pg_catalog, public
as $$
    select
        c.table_name::text,
        c.column_name::text,
        c.data_type::text,
        c.ordinal_position::integer
    from information_schema.columns c
    where c.table_schema = 'public'
    order by c.table_name, c.ordinal_position;
$$;

-- Allow the same API role used by the bot to call the function.
grant execute on function public.get_public_schema() to anon, authenticated, service_role;

-- Force PostgREST to reload its schema cache immediately.
notify pgrst, 'reload schema';

-- Verification 1: SQL-side check
select *
from public.get_public_schema()
order by table_name, ordinal_position;

-- Verification 2: function identity
select
    n.nspname as schema_name,
    p.proname as function_name,
    pg_get_function_identity_arguments(p.oid) as arguments
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname = 'get_public_schema';
