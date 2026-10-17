-- Profit-runner telemetry + learned exit behavior.
-- Safe to run after the existing SUPABASE_SCHEMA_LIVE.sql.

ALTER TABLE public.bot_positions
  ADD COLUMN IF NOT EXISTS highest_price numeric,
  ADD COLUMN IF NOT EXISTS max_profit_pct numeric,
  ADD COLUMN IF NOT EXISTS trailing_stop numeric,
  ADD COLUMN IF NOT EXISTS sell_signal text,
  ADD COLUMN IF NOT EXISTS max_profit_minutes numeric,
  ADD COLUMN IF NOT EXISTS peak_to_sell_pct numeric;

CREATE INDEX IF NOT EXISTS bot_positions_symbol_open_idx
  ON public.bot_positions(symbol, status, opened_at DESC);

DROP VIEW IF EXISTS public.bot_symbol_exit_learning;
CREATE VIEW public.bot_symbol_exit_learning AS
SELECT
  symbol,
  count(*)::int AS trade_count,
  round(avg(max_profit_pct)::numeric, 6) AS avg_max_profit_pct,
  round(avg(hold_minutes)::numeric, 2) AS avg_hold_minutes,
  round(avg(peak_to_sell_pct)::numeric, 6) AS avg_peak_to_sell_pct,
  round(avg(max_profit_minutes)::numeric, 2) AS avg_max_profit_minutes
FROM public.bot_positions
WHERE status = 'CLOSED'
  AND closed_at IS NOT NULL
GROUP BY symbol;

-- Backfill what can be derived from existing closed positions.
UPDATE public.bot_positions
SET max_profit_pct = CASE
    WHEN entry_price > 0 AND exit_price IS NOT NULL
      THEN GREATEST((exit_price / entry_price) - 1, 0)
    ELSE max_profit_pct
  END
WHERE max_profit_pct IS NULL;

UPDATE public.bot_positions
SET peak_to_sell_pct = CASE
    WHEN highest_price > 0 AND exit_price IS NOT NULL
      THEN GREATEST(1 - (exit_price / highest_price), 0)
    ELSE peak_to_sell_pct
  END
WHERE peak_to_sell_pct IS NULL;

-- Optional RLS note: keep using your existing server-side/service-role policy.
-- Do NOT expose the service-role key to the browser.
