# V7 Ultimate BUY Gate Integration

The supplied Google Apps Script criteria are integrated as an **additional BUY gate**.

## What changed

- Existing `scan_market()` flow remains in place.
- Existing score, spread, volume, ADX, fee/net-edge, Gemini, daily-loss, position-limit, and order-execution checks remain active.
- A candidate must also pass the supplied V7 Ultimate score threshold (default `76/100`) before it reaches `execute_buy()`.
- SELL logic is unchanged.
- AI Last Chance Scanner remains unchanged.
- The V7 gate is isolated in `v7_entry_gate.py`.
- Market regime uses Bitkub `BTC_THB` 15-minute candles with the same EMA20/EMA50 rule as the supplied script's BTC regime logic.

## Configuration

```env
V7_ENTRY_GATE_ENABLED=true
V7_ENTRY_GATE_MIN_SCORE=76
```

Set `V7_ENTRY_GATE_ENABLED=false` to disable only this new gate without changing the rest of the bot.

## V7 criteria mapped from the supplied script

- Exhaustion: price distance from EMA20 < 5.5%
- 15m trend: EMA9 > EMA21 > EMA50
- 5m breakout confirmation
- 5m Smart Whale accumulation
- 5m volume expansion
- 5m RSI 45–68, with RSI Origin bonus
- 5m MACD acceleration
- BTC market regime bonus/penalty
- fake-pump price-efficiency penalty
- BUY gate passes at score >= 76

The execution layer is not modified by this integration; the gate only determines whether an existing BUY candidate is allowed to proceed.
