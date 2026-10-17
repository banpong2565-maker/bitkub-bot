# Balance Fix V3 — Bitkub V4 funding segment

## Why V2 still showed THB=0

The live log showed HTTP-successful V4 wallet responses containing many assets but no THB. The previous implementation called `/api/v4/wallet/balances` without explicitly sending the documented `segment=funding` query parameter.

Bitkub's current V4 documentation defines `segment=funding` for both `/api/v4/wallet/balances` and `/api/v4/wallet/assets`. V3 `/api/v3/market/balances` was removed on 2026-05-26 and must not be used as a live fallback.

## V3 changes

- Calls `/api/v4/wallet/balances?segment=funding` explicitly.
- Signs the exact query string used in the request.
- Calls `/api/v4/wallet/assets?segment=funding` as the secondary diagnostic view.
- Removes the obsolete V3 balances fallback.
- Never fabricates THB when Bitkub does not return it.
- Keeps the safety behavior: an unverified balance cannot be treated as spendable THB.
- Adds a regression test for the exact funding-segment URL.

## Verification

`31 passed`

If V3 still reports `THB NOT FOUND` with `code=0` and HTTP 200, the next diagnosis is no longer a parser problem. It means the authenticated Bitkub account/API key is not exposing a THB balance through the documented funding wallet endpoint, and the raw diagnostic response/permissions need to be checked.
