import hashlib
import hmac
import json
import time

import requests


class BitkubClient:
    def __init__(
        self,
        api_key,
        api_secret,
        base_url="https://api.bitkub.com",
        request_timeout=10,
        order_timeout=30,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout
        self.order_timeout = order_timeout

    def get_server_time(self) -> int:
        """Fetch Bitkub server time in milliseconds."""
        try:
            response = requests.get(
                f"{self.base_url}/api/v3/servertime",
                timeout=self.request_timeout,
            )
            text = response.text.strip()
            try:
                return int(text)
            except ValueError:
                data = response.json()
                if isinstance(data, dict) and "result" in data:
                    return int(data["result"])
                return int(data)
        except Exception as e:
            print(f"[Warning] Server time sync failed, using local time: {e}")
            return int(time.time() * 1000)

    def _generate_signature(
        self,
        timestamp: int,
        method: str,
        path: str,
        query_string: str = "",
        body: str = "",
    ) -> str:
        payload = str(timestamp) + method.upper() + path + query_string + body
        return hmac.new(
            self.api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _get_headers(self, timestamp: int, signature: str) -> dict:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-BTK-APIKEY": self.api_key,
            "X-BTK-TIMESTAMP": str(timestamp),
            "X-BTK-SIGN": signature,
        }

    def get_ticker(self, symbol: str) -> dict:
        """Fetch latest ticker data for bot.py.

        The bot uses btc_thb, while Bitkub's public ticker commonly uses THB_BTC.
        """
        # Directly request ticker using the symbol as Bitkub expects (base_quote)
        try:
            response = requests.get(
                f"{self.base_url}/api/v3/market/ticker",
                params={"sym": symbol.lower()},
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and data:
                ticker_obj = data[0]
                normalized = {
                    "last": ticker_obj.get("last"),
                    "lowestAsk": ticker_obj.get("lowest_ask"),
                    "highestBid": ticker_obj.get("highest_bid"),
                    "percentChange": ticker_obj.get("percent_change"),
                    "baseVolume": ticker_obj.get("base_volume"),
                    "quoteVolume": ticker_obj.get("quote_volume"),
                    "high_24_hr": ticker_obj.get("high_24_hr"),
                    "low_24_hr": ticker_obj.get("low_24_hr"),
                }
                return normalized
            if isinstance(data, dict):
                return data
            return {}
        except Exception as e:
            print(f"[Warning] Could not fetch ticker: {e}")
            return {}

    def get_all_tickers(self) -> dict:
        """Fetch all public ticker data."""
        try:
            response = requests.get(
                f"{self.base_url}/api/v3/market/ticker",
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                return self._normalize_ticker_items(data)
            # fallback if dict returned
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"[Warning] Could not fetch tickers: {e}")
            return {}

    def get_symbols(self) -> dict:
        """Fetch all trading symbols and their metadata (e.g. 'source': 'exchange'/'broker').

        [FIX] ใช้เพื่อกรองเหรียญ broker-source ออกก่อนส่งคำสั่งซื้อ/ขาย เพราะ
        place-bid/place-ask ไม่รองรับเหรียญ source=broker (Bitkub error code 61)
        """
        try:
            response = requests.get(
                f"{self.base_url}/api/v3/market/symbols",
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"[Warning] Could not fetch symbols: {e}")
            return {"error": -1, "result": []}

    def get_depth(self, symbol: str, limit: int = 10) -> dict:
        """Fetch market depth (order book) for a symbol."""
        parts = symbol.upper().split("_")
        bitkub_symbol = f"{parts[1]}_{parts[0]}" if len(parts) == 2 else symbol.upper()
        try:
            response = requests.get(
                f"{self.base_url}/api/v3/market/depth",
                params={"sym": symbol.lower(), "lmt": limit},
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            data = response.json()
            # v3 returns dict directly, keep as is
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"[Warning] Could not fetch market depth for {symbol}: {e}")
            return {}

    def _normalize_balances(self, data: dict) -> dict:
        message = str(data.get("message", "")).lower()
        is_success = (
            data.get("error") == 0
            or message == "success"
            or data.get("status") == "success"
        )

        if not is_success:
            return {
                "error": data.get("error", -1),
                "message": data.get("message", "Wallet API request failed"),
                "result": {},
            }

        raw_result = data.get("result", data.get("data", {}))
        if isinstance(raw_result, dict) and isinstance(raw_result.get("balances"), list):
            raw_result = raw_result["balances"]

        normalized = {}
        if isinstance(raw_result, list):
            for item in raw_result:
                if not isinstance(item, dict):
                    continue
                currency = item.get("currency") or item.get("symbol") or item.get("asset")
                if not currency:
                    continue
                available = item.get(
                    "available",
                    item.get("avail", item.get("free", item.get("balance", 0))),
                )
                balance = item.get("balance", item.get("total", available))
                reserved = item.get("reserved", item.get("locked", 0))
                normalized[str(currency).upper()] = {
                    "available": available,
                    "balance": balance,
                    "reserved": reserved,
                }
        elif isinstance(raw_result, dict):
            for currency, balance in raw_result.items():
                if isinstance(balance, dict):
                    available = balance.get(
                        "available",
                        balance.get("avail", balance.get("free", balance.get("balance", 0))),
                    )
                    total = balance.get("balance", balance.get("total", available))
                    reserved = balance.get("reserved", balance.get("locked", 0))
                else:
                    available = balance
                    total = balance
                    reserved = 0
                normalized[str(currency).upper()] = {
                    "available": available,
                    "balance": total,
                    "reserved": reserved,
                }

        return {
            "error": 0,
            "message": data.get("message", "success"),
            "result": normalized,
            "raw": data,
        }

    def _normalize_ticker_items(self, items: list) -> dict:
        """Convert a v3 ticker list into legacy dict format.
        Each item in *items* is expected to be a dict with a "symbol" field like "BTC_THB".
        The returned dict uses keys in the old "THB_BTC" orientation and maps field names
        from snake_case to the camelCase names used elsewhere in the code.
        """
        result = {}
        for item in items:
            symbol = item.get("symbol")
            if not symbol:
                continue
            # Convert base_quote to quote_base (e.g., BTC_THB -> THB_BTC)
            parts = symbol.upper().split("_")
            if len(parts) == 2:
                legacy_key = f"{parts[1]}_{parts[0]}"
            else:
                legacy_key = symbol.upper()
            result[legacy_key] = {
                "last": item.get("last"),
                "lowestAsk": item.get("lowest_ask"),
                "highestBid": item.get("highest_bid"),
                "percentChange": item.get("percent_change"),
                "baseVolume": item.get("base_volume"),
                "quoteVolume": item.get("quote_volume"),
                "high_24_hr": item.get("high_24_hr"),
                "low_24_hr": item.get("low_24_hr"),
            }
        return result

    def get_balances(self) -> dict:
        """Fetch wallet balances and normalize Bitkub v3/v4 success formats."""
        path = "/api/v4/wallet/balances"
        method = "GET"
        timestamp = self.get_server_time()
        signature = self._generate_signature(timestamp, method, path)
        headers = self._get_headers(timestamp, signature)

        try:
            response = requests.get(
                f"{self.base_url}{path}",
                headers=headers,
                timeout=self.request_timeout,
            )
            try:
                data = response.json()
            except ValueError:
                return {
                    "error": -1,
                    "message": f"Wallet API returned non-JSON response (HTTP {response.status_code})",
                    "result": {},
                }

            if response.status_code >= 400:
                return {
                    "error": data.get("error", response.status_code) if isinstance(data, dict) else response.status_code,
                    "message": data.get("message", response.text) if isinstance(data, dict) else response.text,
                    "result": {},
                }

            if isinstance(data, dict):
                return self._normalize_balances(data)

            return {
                "error": -1,
                "message": "Wallet API returned an unexpected response format",
                "result": {},
            }
        except Exception as e:
            print("Wallet API Exception:", str(e))
            return {"error": -1, "message": str(e), "result": {}}

    def get_candles(self, symbol: str, resolution: str = "15", limit: int = 100) -> dict:
        """Fetch historical OHLCV candles from Bitkub TradingView endpoint."""
        url = f"{self.base_url}/tradingview/history"

        res_minutes = 15
        if resolution.isdigit():
            res_minutes = int(resolution)
        elif resolution == "D":
            res_minutes = 24 * 60
        elif resolution == "W":
            res_minutes = 7 * 24 * 60

        to_time = int(time.time())
        from_time = to_time - (limit * res_minutes * 60 * 2)

        params = {
            "symbol": symbol.upper(),
            "resolution": resolution,
            "from": from_time,
            "to": to_time,
        }

        try:
            response = requests.get(
                url,
                params=params,
                timeout=self.request_timeout,
            )
            return response.json()
        except Exception as e:
            print(f"[Warning] Could not fetch candles: {e}")
            return {"s": "error", "message": str(e)}

    def place_bid(
        self,
        symbol: str,
        amount: float,
        rate: float = 0.0,
        order_type: str = "market",
        post_only: bool = False,
    ) -> dict:
        """Place a buy order through Bitkub secure API."""
        path = "/api/v3/market/place-bid"
        method = "POST"
        payload = {
            "sym": symbol.upper(),
            "amt": float(amount),
            "rat": float(rate) if order_type.lower() == "limit" else 0,
            "typ": order_type.lower(),
        }
        if post_only:
            payload["post_only"] = True
        # ========= BUY REQUEST =========
        print("========== BUY REQUEST ===========")
        print(f"Symbol : {symbol.upper()}")
        print(f"Amount : {float(amount)} THB")
        print(f"Price  : {float(rate)}")
        print("Payload:")
        print(json.dumps(payload, indent=2))

        # Duplicate payload definition removed

        timestamp = self.get_server_time()
        body_str = json.dumps(payload, separators=(",", ":"))
        signature = self._generate_signature(timestamp, method, path, body=body_str)
        headers = self._get_headers(timestamp, signature)

        try:
            response = requests.post(
                f"{self.base_url}{path}",
                headers=headers,
                data=body_str,
                timeout=self.order_timeout,
            )
            resp_json = response.json()
            # ========= BUY RESPONSE =========
            print("========== BUY RESPONSE =========")
            print(json.dumps(resp_json, indent=2))
            print("=================================")
            if resp_json.get("error") != 0:
                print(f"[ERROR] Buy request failed – Code: {resp_json.get('error')}, Message: {resp_json.get('message')}")
                print("Request payload:")
                print(json.dumps(payload, indent=2))
            return resp_json
        except requests.exceptions.Timeout:
            return {
                "error": -2,
                "message": "Order request timed out; status unknown. Check Bitkub balance/order history before retrying.",
                "status": "unknown",
            }
        except Exception as e:
            return {"error": -1, "message": f"Connection failed: {e}"}

    def place_ask(
        self,
        symbol: str,
        amount: float,
        rate: float = 0.0,
        order_type: str = "market",
        post_only: bool = False,
    ) -> dict:
        """Place a sell order through Bitkub secure API."""
        path = "/api/v3/market/place-ask"
        method = "POST"
        payload = {
            "sym": symbol.lower(),
            "amt": amount,
            "rat": rate if order_type.lower() == "limit" else 0,
            "typ": order_type.lower(),
        }
        if post_only:
            payload["post_only"] = True

        timestamp = self.get_server_time()
        body_str = json.dumps(payload, separators=(",", ":"))
        signature = self._generate_signature(timestamp, method, path, body=body_str)
        headers = self._get_headers(timestamp, signature)

        try:
            response = requests.post(
                f"{self.base_url}{path}",
                headers=headers,
                data=body_str,
                timeout=self.order_timeout,
            )
            return response.json()
        except requests.exceptions.Timeout:
            return {
                "error": -2,
                "message": "Order request timed out; status unknown. Check Bitkub balance/order history before retrying.",
                "status": "unknown",
            }
        except Exception as e:
            return {"error": -1, "message": f"Connection failed: {e}"}

    def get_open_orders(self, symbol: str) -> dict:
        """Fetch open orders for a symbol."""
        path = "/api/v3/market/my-open-orders"
        method = "GET"
        query_string = f"?sym={symbol.lower()}"

        timestamp = self.get_server_time()
        signature = self._generate_signature(
            timestamp,
            method,
            path,
            query_string=query_string,
        )
        headers = self._get_headers(timestamp, signature)

        try:
            response = requests.get(
                f"{self.base_url}{path}{query_string}",
                headers=headers,
                timeout=self.request_timeout,
            )
            return response.json()
        except Exception as e:
            return {"error": -1, "message": f"Connection failed: {e}", "result": []}

    def cancel_order(self, symbol: str, order_id: str, side: str) -> dict:
        """Cancel an open order."""
        path = "/api/v3/market/cancel-order"
        method = "POST"
        payload = {
            "sym": symbol.lower(),
            "id": str(order_id),
            "sd": side.lower(),
        }

        timestamp = self.get_server_time()
        body_str = json.dumps(payload, separators=(",", ":"))
        signature = self._generate_signature(timestamp, method, path, body=body_str)
        headers = self._get_headers(timestamp, signature)

        try:
            response = requests.post(
                f"{self.base_url}{path}",
                headers=headers,
                data=body_str,
                timeout=self.order_timeout,
            )
            return response.json()
        except requests.exceptions.Timeout:
            return {
                "error": -2,
                "message": "Cancel request timed out; status unknown. Check Bitkub open orders before retrying.",
                "status": "unknown",
            }
        except Exception as e:
            return {"error": -1, "message": f"Connection failed: {e}"}
