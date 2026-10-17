# Profit Vault LIVE setup

## Rules
- Realized net profit only: unrealized profit is never locked.
- 80% -> `LOCKED_PROFIT` and is excluded from BUY sizing.
- 20% -> remains in trading capital.
- Default withdrawal threshold: 300 THB.
- When locked profit >= threshold, the bot withdraws the whole locked amount if auto-withdraw is enabled.
- A pending/unknown withdrawal is never automatically retried, to prevent duplicate bank transfers.

## Supabase
Run `SUPABASE_PROFIT_VAULT.sql` for a new table, or `SUPABASE_PROFIT_VAULT_MIGRATION.sql` if the table from the earlier SQL already exists.

For a private/RLS-protected table, set `SUPABASE_VAULT_KEY` to the Supabase service-role key in Railway. Do not commit it.

## Railway variables
Keep the existing Bitkub trading variables. Add:

```text
PROFIT_VAULT_ENABLED=true
PROFIT_VAULT_LOCK_RATIO=0.80
PROFIT_VAULT_COMPOUND_RATIO=0.20
PROFIT_VAULT_WITHDRAW_THRESHOLD=300
PROFIT_VAULT_BANK_ACCOUNT_NO=<approved Bitkub bank account number>
SUPABASE_VAULT_KEY=<Supabase service-role key>
PROFIT_VAULT_AUTO_WITHDRAW_ENABLED=false
```

### Go LIVE with automatic withdrawal
After confirming the ledger is recording correctly, set:

```text
PROFIT_VAULT_AUTO_WITHDRAW_ENABLED=true
```

The Bitkub API uses the current Fiat V4 endpoint `/api/v4/fiat/withdraw`, which requires the API key to have `withdraw` permission and accepts an approved bank account number plus amount. The bot verifies that approved bank-account data can be read before submitting the transfer.

## Important
- The Bitkub wallet still contains one THB balance. The separation is logical: the bot subtracts `Locked Profit` from the amount available for BUY.
- If a limit SELL is accepted but not filled, the bot does not book profit, does not lock profit, and does not withdraw.
- If a withdrawal request times out, the bot leaves it `PENDING` and requires reconciliation rather than risking a duplicate transfer.
