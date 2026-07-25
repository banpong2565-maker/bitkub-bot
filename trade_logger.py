import json
import os
from datetime import datetime

# Ensure the log file exists in the project directory
LOG_FILE = os.path.join(os.path.dirname(__file__), "trade_history.jsonl")

def log_trade(record: dict) -> None:
    """Append a trade record as a JSON line to trade_history.jsonl.

    Expected keys in `record`:
    - timestamp (ISO string)
    - symbol
    - side ("BUY" or "SELL")
    - reason (string describing outcome)
    - entry_price
    - exit_price (or None for buys)
    - amount (quantity of coin)
    - value_thb (THB value of trade)
    - net_pnl_thb (net profit/loss in THB)
    - is_partial (bool)
    - mode ("DRY_RUN" or "LIVE")
    """
    # Ensure the log file exists
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)
        f.write("\n")
