import os
import sys
from dotenv import load_dotenv

# Load env variables
load_dotenv()

import config
from bitkub_client import BitkubClient
import ai_last_chance_scanner


def main():
    print("Initializing BitkubClient...")
    client = BitkubClient(
        config.API_KEY,
        config.API_SECRET,
        config.BASE_URL,
        request_timeout=config.REQUEST_TIMEOUT_SECONDS,
        order_timeout=config.ORDER_TIMEOUT_SECONDS,
    )
    
    print("Testing config settings:")
    print(f"  LAST_CHANCE_MIN_CONFIDENCE: {config.LAST_CHANCE_MIN_CONFIDENCE}")
    print(f"  LAST_CHANCE_MAX_RISK: {config.LAST_CHANCE_MAX_RISK}")
    print(f"  LAST_CHANCE_MIN_VOLUME_THB: {config.LAST_CHANCE_MIN_VOLUME_THB}")
    print(f"  LAST_CHANCE_MAX_SPREAD: {config.LAST_CHANCE_MAX_SPREAD}")
    print(f"  GEMINI_API_KEYS (first 10 chars): {[k[:10] + '...' for k in config.GEMINI_API_KEYS]}")

    print("\nRunning AI Last Chance Scan...")
    opportunity = ai_last_chance_scanner.run_last_chance_scan(client)
    
    if opportunity:
        print("\nScan selected a BUY opportunity:")
        for key, val in opportunity.items():
            print(f"  {key}: {val}")
    else:
        print("\nScan completed. No BUY opportunity selected (or SKIP was chosen).")


if __name__ == "__main__":
    main()
