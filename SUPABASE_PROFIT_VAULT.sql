-- =========================================================
-- Profit Vault Ledger - LIVE 80/20
-- =========================================================
-- Run once in Supabase SQL Editor.
-- If the table already exists from an older version, run
-- SUPABASE_PROFIT_VAULT_MIGRATION.sql instead.

create table if not exists public.profit_vault_ledger (
    id bigint generated always as identity primary key,
    created_at timestamptz not null default now(),
    event_type text not null,
    symbol text,
    position_id bigint,
    amount numeric(20,8) not null,
    vault_balance numeric(20,8) not null default 0,
    status text not null default 'SUCCESS',
    withdrawal_id text,
    fee numeric(20,8) not null default 0,
    received_amount numeric(20,8),
    reference_id text,
    note text,

    constraint profit_vault_ledger_event_type_check check (
        event_type in ('PROFIT','COMPOUND','LOCK','WITHDRAW','ADJUSTMENT')
    ),
    constraint profit_vault_ledger_status_check check (
        status in ('PENDING','SUCCESS','FAILED')
    )
);

create index if not exists profit_vault_ledger_created_at_idx
    on public.profit_vault_ledger (created_at desc);

create index if not exists profit_vault_ledger_event_type_idx
    on public.profit_vault_ledger (event_type);

create index if not exists profit_vault_ledger_status_idx
    on public.profit_vault_ledger (status);

create unique index if not exists profit_vault_ledger_reference_uidx
    on public.profit_vault_ledger (reference_id)
    where reference_id is not null;

create unique index if not exists profit_vault_ledger_withdrawal_id_uidx
    on public.profit_vault_ledger (withdrawal_id)
    where withdrawal_id is not null;

-- Recommended for a server-side bot:
-- alter table public.profit_vault_ledger enable row level security;
-- Store SUPABASE_VAULT_KEY as the Supabase service-role key in Railway.
-- NEVER put the service-role key in browser/client code.
