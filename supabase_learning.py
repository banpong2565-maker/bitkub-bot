import math
import time
from datetime import datetime, timezone
import requests
from urllib.parse import quote
import config
from database_awareness import DatabaseAwareness


class SupabaseLearning:
    """Automatic telemetry + historical learning through Supabase REST API."""

    def __init__(self):
        self.url = (config.SUPABASE_URL or "").rstrip("/")
        self.key = config.SUPABASE_KEY or ""
        self.enabled = bool(config.LEARNING_ENABLED and self.url and self.key)
        self.headers = (
            {
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            }
            if self.enabled
            else {}
        )
        self.db = DatabaseAwareness(self)
        # One read-only batch cache prevents per-symbol REST queries during each scan.
        self._prediction_outcome_cache = {"loaded_at": 0.0, "by_symbol": {}}
        if self.enabled:
            print(f"[Supabase] Learning enabled: {self.url}")
            self.db.refresh(force=True)
        else:
            reasons = []
            if not config.LEARNING_ENABLED:
                reasons.append("LEARNING_ENABLED=False")
            if not self.url:
                reasons.append("SUPABASE_URL missing")
            if not self.key:
                reasons.append("SUPABASE_KEY missing")
            print(f"[Supabase][WARN] Learning disabled: {', '.join(reasons) or 'configuration incomplete'}")

    @staticmethod
    def _num(value, default=None):
        """Convert numpy/pandas numbers safely; never send NaN/Infinity to PostgREST."""
        if value is None:
            return default
        try:
            x = float(value)
            return x if math.isfinite(x) else default
        except (TypeError, ValueError):
            return default

    def rpc(self, function_name, payload=None, timeout=10):
        if not self.enabled:
            return None
        try:
            r = requests.post(
                f"{self.url}/rest/v1/rpc/{function_name}",
                headers=self.headers, json=payload if payload is not None else {}, timeout=timeout
            )
            if r.status_code >= 300:
                print(f"[Supabase] RPC {function_name} failed: {r.status_code} {r.text[:500]}")
                return None
            return r.json() if r.text.strip() else {}
        except Exception as e:
            print(f"[Supabase] RPC {function_name} error: {e}")
            return None

    def _request(self, method, table, payload=None, params=None, prefer=None, timeout=10):
        if not self.enabled:
            return None
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        try:
            r = requests.request(
                method,
                f"{self.url}/rest/v1/{table}",
                headers=headers,
                json=payload,
                params=params,
                timeout=timeout,
            )
            if r.status_code >= 300:
                print(f"[Supabase] {method} {table} failed: {r.status_code} {r.text[:500]}")
                return None
            if not r.text.strip():
                return {}
            try:
                return r.json()
            except ValueError:
                return {}
        except Exception as e:
            print(f"[Supabase] {method} {table} error: {e}")
            return None

    def _post(self, table, payload, return_json=False):
        try:
            safe_payload = self.db.filter_payload(table, payload)
            if not safe_payload:
                print(f"[DB-AWARENESS][WARN] No compatible columns for {table}; write skipped")
                return [] if return_json else False
            # Request the inserted row so a 2xx response with no returned row is
            # not silently reported as a successful insert.
            data = self._request("POST", table, payload=safe_payload,
                                 prefer="return=representation")
            inserted = isinstance(data, list) and bool(data)
            if not inserted:
                print(f"[DB-AWARENESS][WARN] POST {table} returned no inserted row; verify RLS/policies and schema")
                return data if return_json and isinstance(data, list) else ([] if return_json else False)
            print(f"[DB-AWARENESS] POST {table} inserted_rows={len(data)}")
            return data if return_json else True
        except Exception as exc:
            print(f"[DB-AWARENESS][ERROR] POST {table} skipped: {exc}")
            return [] if return_json else False

    def _patch(self, table, payload, params):
        try:
            safe_payload = self.db.filter_payload(table, payload)
            if not safe_payload:
                print(f"[DB-AWARENESS][WARN] PATCH {table} skipped: no compatible columns")
                return False
            # PATCH without a representation can return 204 even when the filter
            # matched zero rows. Verify that Supabase returned the updated row.
            data = self._request("PATCH", table, payload=safe_payload, params=params,
                                 prefer="return=representation")
            updated = isinstance(data, list) and bool(data)
            if not updated:
                print(f"[DB-AWARENESS][WARN] PATCH {table} matched no visible row; verify ID, RLS, and filters")
                return False
            print(f"[DB-AWARENESS] PATCH {table} updated_rows={len(data)}")
            return True
        except Exception as exc:
            print(f"[DB-AWARENESS][ERROR] PATCH {table} skipped: {exc}")
            return False

    def snapshot(self, symbol, row, regime=None):
        payload = {
            "symbol": symbol.upper(),
            "price": self._num(row.get("close"), 0),
            "bid": self._num(row.get("bid")),
            "ask": self._num(row.get("ask")),
            "spread_rate": self._num(row.get("spread_rate"), 0),
            "rsi": self._num(row.get("rsi")),
            "adx": self._num(row.get("adx")),
            "ema_fast": self._num(row.get("ema_fast")),
            "ema_slow": self._num(row.get("ema_slow")),
            "ema_50": self._num(row.get("ema_50")),
            "ema_200": self._num(row.get("ema_200")),
            "macd": self._num(row.get("macd")),
            "macd_signal": self._num(row.get("macd_signal")),
            "atr": self._num(row.get("atr")),
            "volume": self._num(row.get("volume")),
            "volume_ma": self._num(row.get("volume_ma")),
            "regime": regime,
        }
        return self._post("bot_market_snapshots", payload)

    def decision(self, symbol, action, price=0, reason=None, score=None,
                 min_score=None, expected=None, required=None, net_edge=None, stats=None):
        stats = stats or {}
        payload = {
            "symbol": symbol.upper(),
            "action": action,
            "reason": reason,
            "price": self._num(price),
            "score": self._num(score),
            "effective_min_score": self._num(min_score),
            "expected_rate": self._num(expected),
            "required_rate": self._num(required),
            "net_edge": self._num(net_edge),
            "learning_trade_count": int(float(stats.get("trade_count", 0) or 0)),
            "learning_win_rate": self._num(stats.get("win_rate")),
            "learning_avg_pnl_rate": self._num(stats.get("avg_pnl_rate")),
        }
        return self._post("bot_decisions", payload)

    def open_position(self, symbol, entry_price, quantity, buy_value, buy_fee=0,
                      entry_score=None, entry_net_edge=None, reason=None):
        payload = {
            "symbol": symbol.upper(),
            "entry_price": self._num(entry_price, 0),
            "quantity": self._num(quantity, 0),
            "buy_value_thb": self._num(buy_value, 0),
            "buy_fee_thb": self._num(buy_fee, 0),
            "entry_score": self._num(entry_score),
            "entry_net_edge": self._num(entry_net_edge),
            "status": "OPEN",
            "exit_reason": reason,
        }
        rows = self._post("bot_positions", payload, return_json=True)
        if isinstance(rows, list) and rows:
            return rows[0].get("id")
        return None

    def update_open_position(self, position_id, entry_price, quantity, buy_value, buy_fee=0,
                             entry_score=None, entry_net_edge=None):
        if not position_id:
            return False
        payload = {
            "entry_price": self._num(entry_price, 0),
            "quantity": self._num(quantity, 0),
            "buy_value_thb": self._num(buy_value, 0),
            "buy_fee_thb": self._num(buy_fee, 0),
            "entry_score": self._num(entry_score),
            "entry_net_edge": self._num(entry_net_edge),
        }
        return self._patch("bot_positions", payload, {"id": f"eq.{position_id}"})

    def update_position_metrics(self, position_id, highest_price=None, max_profit_pct=None,
                                trailing_stop=None, sell_signal=None, max_profit_minutes=None,
                                peak_to_sell_pct=None):
        """Persist live hold/trailing telemetry for an open position."""
        if not position_id:
            return False
        payload = {
            "highest_price": self._num(highest_price),
            "max_profit_pct": self._num(max_profit_pct),
            "trailing_stop": self._num(trailing_stop),
            "sell_signal": sell_signal,
            "max_profit_minutes": self._num(max_profit_minutes),
            "peak_to_sell_pct": self._num(peak_to_sell_pct),
        }
        return self._patch("bot_positions", payload, {"id": f"eq.{position_id}"})

    def get_exit_learning(self, symbol):
        """Read learned exit behavior for one symbol from the DB view."""
        defaults = {"symbol": str(symbol).upper(), "trade_count": 0,
                    "avg_max_profit_pct": None, "avg_hold_minutes": None,
                    "avg_peak_to_sell_pct": None, "avg_max_profit_minutes": None}
        try:
            if not self.db.has_table("bot_symbol_exit_learning"):
                return None
            rows = self._request("GET", "bot_symbol_exit_learning", params={
                "symbol": f"eq.{str(symbol).upper()}", "select": "*", "limit": "1"
            })
            if isinstance(rows, list) and rows:
                row = dict(defaults); row.update(rows[0])
                row["trade_count"] = int(float(row.get("trade_count") or 0))
                return row
            return defaults
        except Exception as exc:
            print(f"[DB-AWARENESS][ERROR] exit learning {symbol}: {exc}")
            return None

    def close_position(self, position_id, exit_price, sell_value, sell_fee,
                       pnl, pnl_rate, hold_minutes, reason=None):
        if not position_id:
            return False
        payload = {
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "exit_price": self._num(exit_price),
            "sell_value_thb": self._num(sell_value),
            "sell_fee_thb": self._num(sell_fee, 0),
            "net_pnl_thb": self._num(pnl),
            "net_pnl_rate": self._num(pnl_rate),
            "hold_minutes": self._num(hold_minutes),
            "exit_reason": reason,
            "status": "CLOSED",
        }
        return self._patch("bot_positions", payload, {"id": f"eq.{position_id}"})

    def order(self, position_id, symbol, side, order_id=None, order_type=None,
              requested_price=None, filled_price=None, requested_amount=None,
              filled_amount=None, gross_value_thb=None, fee_thb=None,
              net_value_thb=None, status=None, raw_result=None):
        payload = {
            "position_id": position_id,
            "symbol": symbol.upper(),
            "side": side,
            "order_id": str(order_id) if order_id is not None else None,
            "order_type": order_type,
            "requested_price": self._num(requested_price),
            "filled_price": self._num(filled_price),
            "requested_amount": self._num(requested_amount),
            "filled_amount": self._num(filled_amount),
            "gross_value_thb": self._num(gross_value_thb),
            "fee_thb": self._num(fee_thb),
            "net_value_thb": self._num(net_value_thb),
            "status": status,
            "raw_result": raw_result,
        }
        return self._post("bot_orders", payload)


    def prediction(self, symbol, entry_price, expected_rate, score=None,
                   net_edge=None, v7_score=None, learning_stats=None,
                   executed=False):
        """Record every qualifying BUY signal so the model can learn even when no order is executed."""
        stats = learning_stats or {}
        payload = {
            "symbol": symbol.upper(),
            "predicted_at": datetime.now(timezone.utc).isoformat(),
            "entry_price": self._num(entry_price, 0),
            "expected_rate": self._num(expected_rate),
            "score": self._num(score),
            "net_edge": self._num(net_edge),
            "v7_score": self._num(v7_score),
            "learning_trade_count": int(float(stats.get("trade_count", 0) or 0)),
            "learning_win_rate": self._num(stats.get("win_rate")),
            "learning_avg_pnl_rate": self._num(stats.get("avg_pnl_rate")),
            "executed": bool(executed),
            "status": "PENDING",
        }
        rows = self._post("bot_prediction_outcomes", payload, return_json=True)
        if isinstance(rows, list) and rows:
            return rows[0].get("id")
        return None

    def mark_prediction_executed(self, prediction_id, order_id=None):
        if not prediction_id:
            return False
        try:
            return self._request(
                "PATCH", "bot_prediction_outcomes",
                payload={"executed": True, "order_id": str(order_id) if order_id else None},
                params={"id": f"eq.{prediction_id}"}
            ) is not None
        except Exception as exc:
            print(f"[Supabase] prediction update failed: {exc}")
            return False

    def settle_predictions(self, price_by_coin):
        """Settle pending 5/15/30/60m forward returns using current prices."""
        if not self.enabled or not price_by_coin:
            return 0
        try:
            rows = self._request(
                "GET", "bot_prediction_outcomes",
                params={
                    "status": "eq.PENDING",
                    "select": "id,symbol,predicted_at,entry_price",
                    "limit": "500",
                    "order": "predicted_at.asc",
                }
            )
            if not isinstance(rows, list):
                return 0
            now = datetime.now(timezone.utc)
            settled = 0
            horizons = (5, 15, 30, 60)
            for row in rows:
                sym = str(row.get("symbol","")).upper()
                coin = sym.split("_")[0]
                px = self._num(price_by_coin.get(coin))
                entry = self._num(row.get("entry_price"), 0)
                if px is None or entry <= 0:
                    continue
                try:
                    predicted_at = datetime.fromisoformat(str(row["predicted_at"]).replace("Z","+00:00"))
                except Exception:
                    continue
                age = (now - predicted_at).total_seconds() / 60.0
                updates = {}
                if age >= 5: updates["return_5m"] = px/entry - 1
                if age >= 15: updates["return_15m"] = px/entry - 1
                if age >= 30: updates["return_30m"] = px/entry - 1
                if age >= 60: updates["return_60m"] = px/entry - 1
                if age >= 60:
                    updates["status"] = "SETTLED"
                    updates["settled_at"] = now.isoformat()
                if updates:
                    if self._request("PATCH","bot_prediction_outcomes",
                                     payload={k:self._num(v) if k.startswith("return_") else v for k,v in updates.items()},
                                     params={"id":f"eq.{row.get('id')}"}) is not None:
                        settled += 1
            return settled
        except Exception as e:
            print(f"[Supabase] prediction settlement error: {e}")
            return 0


    def get_prediction_outcome_stats(self, symbol, refresh_seconds=900):
        """Return read-only, settled prediction outcomes for a symbol.

        This is shadow analysis only: it never changes a trading decision.
        Results are batch-loaded and cached so scanning many symbols adds at
        most one Supabase read per refresh interval.
        """
        if not self.enabled or not self.db.has_table("bot_prediction_outcomes"):
            return None

        now = time.monotonic()
        cache = self._prediction_outcome_cache
        if now - cache["loaded_at"] >= max(float(refresh_seconds), 60.0):
            rows = self._request(
                "GET", "bot_prediction_outcomes",
                params={
                    "select": "symbol,return_15m,return_60m,executed,status",
                    "status": "eq.SETTLED",
                    "order": "predicted_at.desc",
                    "limit": "5000",
                },
                timeout=15,
            )
            if not isinstance(rows, list):
                # Do not cache errors; a later scan may recover after a transient outage.
                return None
            grouped = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                key = str(row.get("symbol") or "").upper()
                if not key:
                    continue
                grouped.setdefault(key, []).append(row)
            cache["by_symbol"] = grouped
            cache["loaded_at"] = now

        records = cache["by_symbol"].get(str(symbol).upper(), [])
        if not records:
            return {
                "sample_count": 0, "executed_count": 0, "not_executed_count": 0,
                "avg_return_15m": None, "win_rate_15m": None,
                "avg_return_60m": None, "win_rate_60m": None,
                "executed_avg_return_60m": None, "not_executed_avg_return_60m": None,
            }

        def values(items, key):
            return [self._num(item.get(key)) for item in items
                    if self._num(item.get(key)) is not None]

        def summary(items, key):
            vals = values(items, key)
            if not vals:
                return None, None
            return sum(vals) / len(vals), sum(1 for value in vals if value > 0) / len(vals)

        ret15, win15 = summary(records, "return_15m")
        ret60, win60 = summary(records, "return_60m")
        executed = [r for r in records if r.get("executed") is True]
        not_executed = [r for r in records if r.get("executed") is not True]
        exec_ret60, _ = summary(executed, "return_60m")
        skipped_ret60, _ = summary(not_executed, "return_60m")
        sample_count = sum(
            1 for row in records
            if self._num(row.get("return_15m")) is not None
            or self._num(row.get("return_60m")) is not None
        )
        return {
            "sample_count": sample_count,
            "executed_count": len(executed),
            "not_executed_count": len(not_executed),
            "avg_return_15m": ret15,
            "win_rate_15m": win15,
            "avg_return_60m": ret60,
            "win_rate_60m": win60,
            "executed_avg_return_60m": exec_ret60,
            "not_executed_avg_return_60m": skipped_ret60,
        }

    def health_check(self):
        """Check Supabase REST access and return a compact diagnostic."""
        if not self.enabled:
            return {"ok": False, "reason": "SUPABASE_DISABLED"}
        checks = {}
        for table in ("bot_market_snapshots", "bot_decisions", "bot_positions",
                      "bot_orders", "bot_symbol_learning", "bot_symbol_exit_learning"):
            try:
                result = self._request("GET", table, params={"select": "*", "limit": "1"})
                checks[table] = "OK" if isinstance(result, list) else "ERROR"
            except Exception as e:
                checks[table] = f"ERROR: {e}"
        return {"ok": all(v == "OK" for v in checks.values()), "tables": checks}

    def trade(self, symbol, side, entry_price, exit_price=None, quantity=0,
              buy_value=0, buy_fee=0, sell_value=None, sell_fee=0, pnl=None,
              pnl_rate=None, hold_minutes=None, reason=None, status="CLOSED"):
        """Backward-compatible helper for a complete closed position."""
        payload = {
            "symbol": symbol.upper(),
            "entry_price": self._num(entry_price, 0),
            "exit_price": self._num(exit_price),
            "quantity": self._num(quantity, 0),
            "buy_value_thb": self._num(buy_value, 0),
            "buy_fee_thb": self._num(buy_fee, 0),
            "sell_value_thb": self._num(sell_value),
            "sell_fee_thb": self._num(sell_fee, 0),
            "net_pnl_thb": self._num(pnl),
            "net_pnl_rate": self._num(pnl_rate),
            "hold_minutes": self._num(hold_minutes),
            "exit_reason": reason,
            "status": status,
        }
        return self._post("bot_positions", payload)

    def latest_market_snapshot(self, symbol):
        """Read the latest snapshot for a symbol; returns None on schema/query error."""
        try:
            if not self.db.has_table("bot_market_snapshots"):
                print(f"[DB-AWARENESS][ERROR] bot_market_snapshots missing; skip {symbol}")
                return None
            if not self.db.validate_required("bot_market_snapshots", ("symbol", "captured_at")):
                print(f"[DB-AWARENESS][ERROR] snapshot query columns unavailable; skip {symbol}")
                return None
            rows = self._request(
                "GET", "bot_market_snapshots",
                params={
                    "symbol": f"eq.{str(symbol).upper()}",
                    "order": "captured_at.desc",
                    "limit": "1",
                    "select": "*",
                },
            )
            return rows[0] if isinstance(rows, list) and rows else None
        except Exception as exc:
            print(f"[DB-AWARENESS][ERROR] latest snapshot {symbol}: {exc}")
            return None

    def get_symbol_learning(self, symbol):
        """Read learning memory for exactly one symbol. None means DB error."""
        defaults = {"symbol": str(symbol).upper(), "trade_count": 0,
                    "win_rate": None, "avg_pnl_rate": None, "avg_hold_minutes": None}
        try:
            if not self.db.has_table("bot_symbol_learning"):
                print(f"[DB-AWARENESS][ERROR] bot_symbol_learning missing; skip {symbol}")
                return None
            data = self._request("GET", "bot_symbol_learning", params={
                "symbol": f"eq.{str(symbol).upper()}",
                "select": "*", "limit": "1"
            })
            if isinstance(data, list) and data:
                row = dict(defaults); row.update(data[0])
                row["trade_count"] = int(float(row.get("trade_count") or 0))
                return row
            return defaults
        except Exception as exc:
            print(f"[DB-AWARENESS][ERROR] learning query {symbol}: {exc}")
            return None

    # Backward-compatible alias used by older bot code.
    def stats(self, symbol):
        return self.get_symbol_learning(symbol)

    @staticmethod
    def calculate_learning_score(stats):
        """Calculate the exact Learning Score defined by the trading policy.

        Trade count: >=30 => +10; otherwise 0.
        Win rate: >=70% => +20; 50-<70% => +10; <50% => -10.
        Average PNL: >1% => +10; 0-1% => +5; <0% => -10.

        A symbol with no history has Learning Score 0.
        """
        stats = stats or {}
        trade_count = int(float(stats.get("trade_count") or 0))
        if trade_count <= 0:
            return 0

        score = 10 if trade_count >= 30 else 0

        win_rate = stats.get("win_rate")
        if win_rate is not None:
            wr = float(win_rate)
            if wr <= 1.0:
                wr *= 100.0
            if wr >= 70.0:
                score += 20
            elif wr >= 50.0:
                score += 10
            else:
                score -= 10

        avg_pnl_rate = stats.get("avg_pnl_rate")
        if avg_pnl_rate is not None:
            pnl = float(avg_pnl_rate)
            # bot_symbol_learning stores avg_pnl_rate as a decimal (0.015 = 1.5%).
            if abs(pnl) <= 1.0:
                pnl_pct = pnl * 100.0
            else:
                pnl_pct = pnl
            if pnl_pct > 1.0:
                score += 10
            elif pnl_pct >= 0.0:
                score += 5
            else:
                score -= 10

        return score


learning = SupabaseLearning()
