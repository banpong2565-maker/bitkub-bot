import hashlib
import hmac
import json
import time

import requests


def _clean_number(value, max_decimals: int = 8):
    """Normalize a numeric value for Bitkub's order payloads.

    Bitkub's API explicitly rejects amounts/rates with a "trailing zero"
    (e.g. it documents that ``1000.00`` is invalid but ``1000`` is fine, and
    ``0.10000000`` is invalid but ``0.1`` is fine). Python's ``json.dumps``
    always renders whole floats as e.g. ``300.0``, which trips this rule, and
    floating point arithmetic can also leave tiny artifacts (e.g.
    ``0.1 + 0.2 == 0.30000000000000004``) that push a value past the
    exchange's allowed decimal precision. This rounds away float noise and
    returns a plain ``int`` whenever the value is a whole number so the JSON
    payload never contains an unwanted trailing ``.0``.
    """
    rounded = round(float(value), max_decimals)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def _to_quote_base(symbol: str) -> str:
    """Convert a base_quote symbol (e.g. btc_thb) to quote_base (thb_btc)."""
    parts = symbol.split("_")
    if len(parts) == 2:
        return f"{parts[1]}_{parts[0]}".lower()
    return symbol.lower()


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

    def get_symbols(self) -> dict:
        """Fetch Bitkub market symbols, including the source field.

        Error 61 from place-bid/place-ask means the symbol is a broker-source
        coin and that trading endpoint does not support it.
        """
        try:
            response = requests.get(
                f"{self.base_url}/api/v3/market/symbols",
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return data
            return {"error": -1, "message": "Unexpected symbols response", "result": []}
        except Exception as e:
            print(f"[Warning] Could not fetch market symbols: {e}")
            return {"error": -1, "message": str(e), "result": []}

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
        """Normalize Bitkub V4/V3 wallet responses into {result: {COIN: {...}}}.

        Bitkub V4 wallet/balances currently returns ``code/message/data`` and
        ``data`` is a list of {currency, available, reserved, total}. Older
        responses used ``error/result``. This parser accepts both, including
        nested ``items``/``balances`` containers.
        """
        if not isinstance(data, dict):
            return {"error": -1, "message": "Unexpected wallet response", "result": {}, "raw": data}

        code = data.get("code")
        message = str(data.get("message", "")).lower()
        error = data.get("error")
        is_success = (
            error == 0
            or str(code) in ("0", "success")
            or message == "success"
            or data.get("status") == "success"
        )
        if not is_success:
            return {
                "error": error if error is not None else (code if code is not None else -1),
                "message": data.get("message", "Wallet API request failed"),
                "result": {},
                "raw": data,
            }

        raw_result = data.get("result", data.get("data", {}))
        if isinstance(raw_result, dict):
            for key in ("items", "balances", "assets"):
                if isinstance(raw_result.get(key), list):
                    raw_result = raw_result[key]
                    break
            else:
                # V4 wallet/assets can return a simple {"BTC": "..."} map.
                # V4 wallet/balances can return {"THB": {...}}.
                pass

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
                    item.get("avail", item.get("free", item.get("balance", item.get("total", 0)))),
                )
                total = item.get("total", item.get("balance", available))
                reserved = item.get("reserved", item.get("locked", 0))
                normalized[str(currency).upper()] = {
                    "available": available,
                    "balance": total,
                    "reserved": reserved,
                }
        elif isinstance(raw_result, dict):
            for currency, balance in raw_result.items():
                if isinstance(balance, dict):
                    available = balance.get(
                        "available",
                        balance.get("avail", balance.get("free", balance.get("balance", balance.get("total", 0)))),
                    )
                    total = balance.get("total", balance.get("balance", available))
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
        """Read live Bitkub wallet balances without assuming a wallet segment.

        Some V4 deployments reject ``segment=funding``.  Try the plain wallet
        endpoints first, normalize the returned structure, and mark THB as
        verified whenever the API successfully returns a THB entry (including a
        legitimate zero balance).  No synthetic spendable balance is created.
        """
        attempts = [
            ("/api/v4/wallet/balances", ""),
            ("/api/v4/wallet/assets", ""),
        ]
        errors = []
        best_result = None

        for path, query_string in attempts:
            method = "GET"
            timestamp = self.get_server_time()
            signature = self._generate_signature(
                timestamp, method, path, query_string=query_string
            )
            headers = self._get_headers(timestamp, signature)
            url = f"{self.base_url}{path}{query_string}"

            try:
                response = requests.get(
                    url, headers=headers, timeout=self.request_timeout
                )
                try:
                    data = response.json()
                except ValueError:
                    errors.append(f"{path}: non-JSON HTTP {response.status_code}")
                    continue

                if response.status_code >= 400:
                    message = (
                        data.get("message", response.text)
                        if isinstance(data, dict) else response.text
                    )
                    errors.append(f"{path}: HTTP {response.status_code}: {message}")
                    continue

                normalized = self._normalize_balances(data)
                if normalized.get("error") != 0:
                    errors.append(
                        f"{path}: {normalized.get('message', 'wallet request failed')}"
                    )
                    continue

                result = normalized.get("result") or {}
                if result:
                    best_result = normalized

                keys = list(result.keys())
                if "THB" in result:
                    thb = result["THB"]
                    try:
                        available = float(thb.get("available", 0) or 0)
                    except (TypeError, ValueError):
                        available = 0.0
                    try:
                        reserved = float(thb.get("reserved", 0) or 0)
                    except (TypeError, ValueError):
                        reserved = 0.0
                    try:
                        total = float(thb.get("balance", available) or 0)
                    except (TypeError, ValueError):
                        total = available

                    normalized["balance_verified"] = True
                    print(
                        f"[💰 Balance API] {path} -> "
                        f"THB available={available:.8f} "
                        f"reserved={reserved:.8f} total={total:.8f}"
                    )
                    print(f"[DEBUG THB RAW] {thb}")
                    return normalized

                print(
                    f"[💰 Balance API] {path} -> THB NOT FOUND | "
                    f"assets={len(result)} | keys={keys[:20]}"
                )

            except Exception as exc:
                errors.append(f"{path}: {exc}")

        diagnostic = (
            "Bitkub V4 wallet returned no THB entry from wallet/balances or "
            "wallet/assets. Balance is UNVERIFIED and will not be treated as "
            "spendable THB."
        )
        print(f"[❌ Balance API] {diagnostic}")
        if errors:
            print("[❌ Balance API details] " + " | ".join(errors))

        return {
            "error": -2,
            "message": diagnostic,
            "result": (best_result or {}).get("result", {}),
            "raw": (best_result or {}).get("raw", {}),
            "balance_verified": False,
        }

    def get_fiat_accounts(self) -> dict:
        """Return approved bank accounts using Bitkub Fiat V4."""
        path = "/api/v4/fiat/accounts"
        method = "GET"
        query_string = "?page=1&limit=100"
        timestamp = self.get_server_time()
        signature = self._generate_signature(timestamp, method, path, query_string=query_string)
        headers = self._get_headers(timestamp, signature)
        try:
            response = requests.get(f"{self.base_url}{path}{query_string}", headers=headers, timeout=self.request_timeout)
            data = response.json()
            if response.status_code >= 300:
                return {"error": data.get("code", response.status_code), "message": data.get("message", response.text), "result": []}
            code = data.get("code", 0) if isinstance(data, dict) else -1
            if str(code) not in ("0", "success"):
                return {"error": code, "message": data.get("message", "Bitkub fiat account request failed"), "result": []}
            result = data.get("data", []) if isinstance(data, dict) else []
            if isinstance(result, dict):
                result = result.get("items", result.get("data", []))
            # Normalize common field names without changing the raw account data.
            return {"error": 0, "message": data.get("message", "success"), "result": result or []}
        except Exception as e:
            return {"error": -1, "message": f"Fiat accounts request failed: {e}", "result": []}

    def withdraw_fiat(self, bank_account_no: str, amount: float) -> dict:
        """Withdraw THB to an approved bank account using Bitkub Fiat V4."""
        path = "/api/v4/fiat/withdraw"
        method = "POST"
        payload = {"bank_account_no": str(bank_account_no), "amount": _clean_number(amount)}
        body_str = json.dumps(payload, separators=(",", ":"))
        timestamp = self.get_server_time()
        signature = self._generate_signature(timestamp, method, path, body=body_str)
        headers = self._get_headers(timestamp, signature)
        try:
            response = requests.post(f"{self.base_url}{path}", headers=headers, data=body_str, timeout=self.order_timeout)
            data = response.json()
            if response.status_code >= 300:
                code = data.get("code", response.status_code) if isinstance(data, dict) else response.status_code
                return {"error": code, "message": data.get("message", response.text) if isinstance(data, dict) else response.text, "result": {}}
            code = data.get("code", 0) if isinstance(data, dict) else -1
            if str(code) not in ("0", "success"):
                return {"error": code, "message": data.get("message", "Bitkub fiat withdrawal failed"), "result": data.get("data", {}) if isinstance(data, dict) else {}}
            return {"error": 0, "message": data.get("message", "success"), "result": data.get("data", {}) if isinstance(data, dict) else {}}
        except requests.exceptions.Timeout:
            return {"error": -2, "message": "Fiat withdrawal request timed out; status unknown. Check Bitkub withdrawal history before retrying.", "result": {}}
        except Exception as e:
            return {"error": -1, "message": f"Fiat withdrawal request failed: {e}", "result": {}}

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
            "sym": symbol.lower(),
            "amt": _clean_number(amount),
            "rat": _clean_number(rate) if order_type.lower() == "limit" else 0,
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
                code = resp_json.get("error")
                message = resp_json.get("message") or "No message returned by Bitkub"
                print(f"[ERROR] Buy request failed – Code: {code}, Message: {message}")
                print(f"[ERROR] HTTP status: {response.status_code}")
                print(f"[ERROR] Raw response: {response.text}")
                print("Request payload:")
                print(json.dumps(payload, indent=2))
                if code == 61:
                    print("[ERROR 61] This symbol is source=broker and the trading endpoint does not support broker coins. Skipping this symbol is required; do not retry the same order.")
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
            "amt": _clean_number(amount),
            "rat": _clean_number(rate) if order_type.lower() == "limit" else 0,
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

    def get_order_info(self, symbol: str, order_id: str, side: str) -> dict:
        """Fetch matched/unmatched status for a Bitkub V3 order."""
        path = "/api/v3/market/order-info"
        method = "GET"
        query_string = f"?sym={symbol.lower()}&id={order_id}&sd={side.lower()}"
        timestamp = self.get_server_time()
        signature = self._generate_signature(timestamp, method, path, query_string=query_string)
        headers = self._get_headers(timestamp, signature)
        try:
            response = requests.get(f"{self.base_url}{path}{query_string}", headers=headers, timeout=self.request_timeout)
            data = response.json()
            if response.status_code >= 300:
                return {"error": data.get("error", response.status_code), "message": data.get("message", response.text), "result": {}}
            return data if isinstance(data, dict) else {"error": -1, "message": "Unexpected order-info response", "result": {}}
        except Exception as e:
            return {"error": -1, "message": f"Order-info request failed: {e}", "result": {}}

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
        """Cancel an open order.

        As of the current Bitkub v3 API docs, /api/v3/market/cancel-order
        still requires the symbol in quote_base form (e.g. "thb_btc"), unlike
        every other v3 market endpoint which uses base_quote (e.g.
        "btc_thb"). Bitkub has announced this will change to base_quote in
        the future -- if cancels start failing with "Invalid symbol" after
        that migration, switch this back to a plain symbol.lower().
        """
        path = "/api/v3/market/cancel-order"
        method = "POST"
        payload = {
            "sym": _to_quote_base(symbol),
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
