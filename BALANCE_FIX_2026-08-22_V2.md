# Balance Fix V2 — 2026-08-22

## Problem observed
Live logs showed `/api/v4/wallet/balances` returning many crypto assets but no `THB`, causing the bot to calculate a 0 THB portfolio.

## Changes
- Normalize current Bitkub V4 response format: `code/message/data`.
- Support V4 `data` lists and nested `items`, `balances`, and `assets` containers.
- Read `available`, `reserved`, and `total` correctly.
- If V4 wallet balances has no THB, query `/api/v4/wallet/assets` as an independent fallback.
- Keep legacy `/api/v3/market/balances` only as a final diagnostic fallback because Bitkub deprecated it in May 2026.
- Never fabricate a THB balance.
- If no THB can be verified, return `error=-2` and `balance_verified=False` so live trading cannot silently treat the balance as zero/spendable.
- Add explicit diagnostic logs showing whether THB was found and which assets were returned.

## Verification
- `python -m py_compile *.py` passed.
- Full test suite: **30 passed**.

## Expected live log when THB is found
`[💰 Balance API] /api/v4/wallet/balances -> THB available=... reserved=... total=...`

If V4 balances has no THB but V4 assets does:
`[💰 Balance API] /api/v4/wallet/assets -> THB available=... reserved=... total=...`

If neither endpoint contains THB, the bot reports `Balance is UNVERIFIED` and must not open a live BUY using a fabricated balance.
