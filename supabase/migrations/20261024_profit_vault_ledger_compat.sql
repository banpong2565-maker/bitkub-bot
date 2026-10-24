-- Additive, non-destructive compatibility migration for Profit Vault.
-- Safe to apply to an existing ledger: does not drop tables or rewrite old rows.
ALTER TABLE public.profit_vault_ledger
  ADD COLUMN IF NOT EXISTS reference_id text;

ALTER TABLE public.profit_vault_ledger
  ADD COLUMN IF NOT EXISTS position_uuid uuid;

-- Keep the ledger's legacy bigint position_id intact; bot_positions.id is UUID.
-- The new nullable column stores the UUID without changing existing records.
CREATE UNIQUE INDEX IF NOT EXISTS profit_vault_ledger_reference_id_uidx
  ON public.profit_vault_ledger (reference_id)
  WHERE reference_id IS NOT NULL;

COMMENT ON COLUMN public.profit_vault_ledger.reference_id IS
  'Stable idempotency reference used to prevent duplicate Profit Vault ledger entries.';
COMMENT ON COLUMN public.profit_vault_ledger.position_uuid IS
  'UUID reference to bot_positions.id; position_id remains for legacy bigint references.';
