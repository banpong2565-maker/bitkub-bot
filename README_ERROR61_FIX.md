# Error 61 fix — broker-source pairs

Bitkub's official API documents error 61 as:

`This endpoint doesn't support broker coins ('source' = broker).`

The bot now reads `/api/v3/market/symbols`, caches the metadata for 5 minutes,
and removes `source=broker` pairs from the scan list before strategy analysis.

This prevents pairs such as `ACH_THB` from becoming BUY candidates and avoids
the misleading live-buy notification. A final execution-side safety guard
remains in place in case a queued candidate becomes broker-source after the
scan.

Important: this does **not** make broker-source coins tradable through
`/api/v3/market/place-bid` or `place-ask`; Bitkub's API itself rejects those
orders with error 61.
