Bitkub Bot V12.2 – Production DB Memory Fix

IMPORTANT:
This package is a patch bundle prepared from the confirmed database schema.
It contains the production SQL patch and deployment checklist.

Files:
- SUPABASE_FINAL_DB_AWARENESS_PATCH.sql
- DEPLOYMENT_CHECKLIST.txt

Before replacing the live bot source, back up the current V12.1 files.
Do not overwrite .env or API credentials.

Expected behavior:
1. Database schema awareness loads public schema.
2. Learning is read per symbol from bot_symbol_learning.
3. Learning score is combined with technical score.
4. bot_decisions stores learning_trade_count, learning_win_rate,
   learning_avg_pnl_rate, and learning_score when those columns exist.
5. Missing optional decision columns produce warnings rather than crashing.
6. Database/query errors skip the affected symbol and continue scanning.
7. Decision audit covers BUY/SELL/HOLD/BLOCK paths.

Verification SQL:
SELECT * FROM public.get_public_schema()
ORDER BY table_name, ordinal_position;

SELECT created_at, symbol, action, score,
       learning_trade_count, learning_win_rate,
       learning_avg_pnl_rate, learning_score, reason
FROM public.bot_decisions
ORDER BY created_at DESC
LIMIT 30;
