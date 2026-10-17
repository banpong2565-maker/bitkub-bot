# Fix: bot not trading (2026-10-17)

Minimal config-only adjustment; trading flow and strategy structure are unchanged.

- Lowered the hard minimum net-edge floor from 0.30% to 0.10%. This is *after* the estimated round-trip fees, spread, and minimum expected-profit buffer, so it still requires a positive estimated edge.
- Lowered the default final BUY score from 45 to 35 to reduce false negatives when the technical signal is reasonable but the learning score is neutral.
- Deployment environment variables override these defaults if set. Check the Railway/Northflank/Render environment for `MIN_NET_EDGE` and `FINAL_MIN_BUY_SCORE`; the running service must be redeployed/restarted to load changes.

This does not guarantee a trade: the bot still blocks unsupported broker-source pairs, stale/spiking entries, insufficient THB, daily risk limits, and positions over the configured cap. Verify logs and start with a small amount; live trading can lose money.
