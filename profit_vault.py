"""Profit Vault for LIVE Bitkub trading.

Keeps 80% of realized net profit logically locked away from the trading engine,
while 20% remains part of the trading capital.  The vault is persisted in
Supabase and can optionally sweep the locked amount to an approved Bitkub bank
account once the configured threshold is reached.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Optional

import requests

import config


class ProfitVault:
    def __init__(self):
        self.url = (getattr(config, "SUPABASE_URL", "") or "").rstrip("/")
        # Prefer a server-side service key for the vault ledger. Never commit it.
        self.key = (
            getattr(config, "SUPABASE_VAULT_KEY", "")
            or getattr(config, "SUPABASE_KEY", "")
        )
        self.enabled = bool(self.url and self.key and getattr(config, "PROFIT_VAULT_ENABLED", True))
        self.headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        } if self.enabled else {}

    @staticmethod
    def _num(value: Any, default: float = 0.0) -> float:
        try:
            x = float(value)
            return x if math.isfinite(x) else default
        except (TypeError, ValueError):
            return default

    def _request(self, method: str, table: str, *, payload=None, params=None):
        if not self.enabled:
            return None
        try:
            r = requests.request(
                method,
                f"{self.url}/rest/v1/{table}",
                headers=self.headers,
                json=payload,
                params=params,
                timeout=10,
            )
            if r.status_code >= 300:
                print(f"[ProfitVault] Supabase {method} {table} failed: {r.status_code} {r.text[:500]}")
                return None
            if not r.text.strip():
                return []
            return r.json()
        except Exception as exc:
            print(f"[ProfitVault] Supabase {method} {table} error: {exc}")
            return None

    def balance(self) -> float:
        """Return the current locked balance from the ledger."""
        rows = self._request(
            "GET",
            "profit_vault_ledger",
            params={
                "select": "event_type,amount,status",
                "status": "eq.SUCCESS",
                "order": "id.asc",
                "limit": "5000",
            },
        )
        if not isinstance(rows, list):
            return 0.0
        balance = 0.0
        for row in rows:
            event = str(row.get("event_type", "")).upper()
            amount = self._num(row.get("amount"))
            if event in {"LOCK", "ADJUSTMENT"}:
                balance += amount
            elif event == "WITHDRAW":
                balance -= amount
        return max(balance, 0.0)

    def _insert(self, event_type: str, amount: float, *, symbol=None, position_id=None,
                reference_id=None, status="SUCCESS", fee=0.0, received_amount=None,
                withdrawal_id=None, note=None, vault_balance=None) -> bool:
        # Older ledgers used bigint position_id, while bot_positions.id is UUID.
        # Preserve legacy numeric IDs and store UUIDs in the additive position_uuid column.
        position_text = str(position_id).strip() if position_id is not None else ""
        numeric_position_id = int(position_text) if position_text.isdigit() else None
        position_uuid = position_text if position_text and not position_text.isdigit() else None
        payload = {
            "event_type": event_type,
            "symbol": symbol.upper() if symbol else None,
            "position_id": numeric_position_id,
            "position_uuid": position_uuid,
            "amount": round(float(amount), 8),
            "vault_balance": round(float(vault_balance if vault_balance is not None else self.balance()), 8),
            "status": status,
            "withdrawal_id": withdrawal_id,
            "fee": round(float(fee), 8),
            "received_amount": None if received_amount is None else round(float(received_amount), 8),
            "reference_id": reference_id,
            "note": note,
        }
        rows = self._request("POST", "profit_vault_ledger", payload=payload)
        if not isinstance(rows, list) or not rows:
            print("[ProfitVault][WARN] Ledger insert returned no row; verify migration, RLS, and key permissions")
            return False
        print(f"[ProfitVault] Ledger insert confirmed: event={event_type}, row_id={rows[0].get('id')}")
        return True

    def _patch(self, row_id, payload) -> bool:
        if not self.enabled:
            return False
        try:
            headers = dict(self.headers)
            headers["Prefer"] = "return=representation"
            r = requests.patch(
                f"{self.url}/rest/v1/profit_vault_ledger",
                headers=headers,
                params={"id": f"eq.{row_id}"},
                json=payload,
                timeout=10,
            )
            if r.status_code >= 300:
                print(f"[ProfitVault] Supabase PATCH failed: {r.status_code} {r.text[:500]}")
                return False
            return True
        except Exception as exc:
            print(f"[ProfitVault] Supabase PATCH error: {exc}")
            return False

    def record_realized_profit(self, *, symbol: str, position_id: Optional[int],
                               trade_ref: str, net_profit: float) -> dict:
        """Lock 80% of a positive realized profit and leave 20% in trading capital.

        The full profit is logged for audit.  Only the LOCK row changes the vault
        balance.  A deterministic reference prevents the same sell from being
        locked twice after a restart/retry.
        """
        profit = self._num(net_profit)
        if profit <= 0:
            return {"ok": True, "profit": profit, "locked": 0.0, "compound": 0.0, "vault_balance": self.balance()}

        # Idempotency: if this exact trade was already processed, do not lock it again.
        existing = self._request(
            "GET", "profit_vault_ledger",
            params={"select": "id,event_type,amount,vault_balance,status",
                    "reference_id": f"eq.{trade_ref}:LOCK", "limit": "1"},
        )
        if isinstance(existing, list) and existing:
            return {"ok": True, "duplicate": True, "profit": profit,
                    "locked": self._num(existing[0].get("amount")),
                    "compound": profit * 0.20, "vault_balance": self.balance()}

        lock_ratio = float(getattr(config, "PROFIT_VAULT_LOCK_RATIO", 0.80))
        compound_ratio = float(getattr(config, "PROFIT_VAULT_COMPOUND_RATIO", 0.20))
        locked = round(profit * lock_ratio, 8)
        compound = round(profit * compound_ratio, 8)

        # Audit row: does not alter the vault balance.
        if not self._insert("PROFIT", profit, symbol=symbol, position_id=position_id,
                            reference_id=f"{trade_ref}:PROFIT", note="Realized net profit"):
            return {"ok": False, "reason": "PROFIT ledger write failed"}

        if not self._insert("COMPOUND", compound, symbol=symbol, position_id=position_id,
                            reference_id=f"{trade_ref}:COMPOUND",
                            note="20% retained as trading capital"):
            return {"ok": False, "reason": "COMPOUND ledger write failed"}

        new_balance = round(self.balance() + locked, 8)
        if not self._insert("LOCK", locked, symbol=symbol, position_id=position_id,
                            reference_id=f"{trade_ref}:LOCK", vault_balance=new_balance,
                            note="80% of realized net profit locked from trading"):
            return {"ok": False, "reason": "LOCK ledger write failed"}

        return {"ok": True, "profit": profit, "locked": locked,
                "compound": compound, "vault_balance": new_balance}

    def withdraw_if_ready(self, client) -> dict:
        """Withdraw the entire locked balance once the threshold is reached."""
        vault = self.balance()
        threshold = float(getattr(config, "PROFIT_VAULT_WITHDRAW_THRESHOLD", 300.0))
        if vault < threshold:
            return {"ok": True, "withdrawn": False, "vault_balance": vault, "reason": "below_threshold"}
        if not getattr(config, "PROFIT_VAULT_AUTO_WITHDRAW_ENABLED", False):
            return {"ok": True, "withdrawn": False, "vault_balance": vault, "reason": "auto_withdraw_disabled"}

        bank_account = str(getattr(config, "PROFIT_VAULT_BANK_ACCOUNT_NO", "") or "").strip()
        if not bank_account:
            return {"ok": False, "withdrawn": False, "vault_balance": vault,
                    "reason": "PROFIT_VAULT_BANK_ACCOUNT_NO is missing"}

        # Never withdraw the same vault snapshot twice.
        ref = f"VAULT:{vault:.8f}"
        existing = self._request(
            "GET", "profit_vault_ledger",
            params={"select": "id,status,withdrawal_id,amount,received_amount,fee",
                    "reference_id": f"eq.{ref}", "event_type": "eq.WITHDRAW", "limit": "10",
                    "order": "id.desc"},
        )
        if isinstance(existing, list) and existing:
            latest = existing[0]
            if str(latest.get("status", "")).upper() == "SUCCESS":
                return {"ok": True, "withdrawn": True, "duplicate": True, "vault_balance": 0.0,
                        "withdrawal": latest}
            if str(latest.get("status", "")).upper() == "PENDING":
                # A timed-out transfer has unknown remote state. Never auto-retry
                # the same money because a duplicate bank transfer is worse than
                # a delayed withdrawal. Reconcile it manually from Bitkub history.
                return {"ok": False, "withdrawn": False, "vault_balance": vault,
                        "reason": "withdrawal_pending_reconciliation_required", "withdrawal": latest}

        # Bitkub requires the destination to be an approved bank account. The V4
        # account-list response may redact the full account number, so we verify
        # that the endpoint is readable and let Bitkub perform the final approval check.
        accounts = client.get_fiat_accounts()
        if accounts.get("error") != 0:
            return {"ok": False, "withdrawn": False, "vault_balance": vault,
                    "reason": f"Could not verify approved bank accounts: {accounts.get('message') or accounts.get('error')}"}
        if not accounts.get("result"):
            return {"ok": False, "withdrawn": False, "vault_balance": vault,
                    "reason": "No approved Bitkub bank account was returned"}

        # Check actual available THB. The vault is already excluded from BUY sizing,
        # so a successful withdrawal must not consume trading capital.
        balances = client.get_balances()
        if balances.get("error") != 0:
            return {"ok": False, "withdrawn": False, "vault_balance": vault,
                    "reason": "Could not verify THB balance before withdrawal"}
        result = balances.get("result", {})
        thb = result.get("THB", {})
        available = self._num(thb.get("available") if isinstance(thb, dict) else thb)
        if available + 1e-8 < vault:
            return {"ok": False, "withdrawn": False, "vault_balance": vault,
                    "reason": f"THB available {available:.2f} is below locked vault {vault:.2f}"}

        # Mark pending before the real transfer. If the process dies after the
        # request, the reference makes the next run stop automatic duplication.
        if not self._insert("WITHDRAW", vault, reference_id=ref, status="PENDING",
                            vault_balance=vault, note="Auto-withdraw submitted to Bitkub"):
            return {"ok": False, "withdrawn": False, "vault_balance": vault,
                    "reason": "Could not create pending withdrawal ledger row"}

        response = client.withdraw_fiat(bank_account, vault)
        if response.get("error") != 0:
            # Keep the pending audit record; do not create a negative vault movement.
            return {"ok": False, "withdrawn": False, "vault_balance": vault,
                    "reason": response.get("message") or response.get("error"), "response": response}

        data = response.get("result") or {}
        txn_id = data.get("txn_id") or data.get("id") or data.get("transaction_id")
        fee = self._num(data.get("fee"), 0.0)
        received = data.get("received_amount")
        if received is None:
            received = max(vault - fee, 0.0)
        received = self._num(received)

        # Update the same pending row to SUCCESS. Keeping one unique reference
        # makes restart/retry idempotent and prevents a second withdrawal.
        pending_rows = self._request(
            "GET", "profit_vault_ledger",
            params={"select": "id", "reference_id": f"eq.{ref}", "event_type": "eq.WITHDRAW", "limit": "1"},
        )
        if not isinstance(pending_rows, list) or not pending_rows:
            return {"ok": False, "withdrawn": False, "vault_balance": vault,
                    "reason": "Withdrawal succeeded at Bitkub but pending ledger row was not found; manual reconciliation required",
                    "response": response}
        row_id = pending_rows[0].get("id")
        if not self._patch(row_id, {
            "status": "SUCCESS",
            "withdrawal_id": txn_id,
            "fee": round(fee, 8),
            "received_amount": round(received, 8),
            "vault_balance": 0,
            "note": "Auto-withdraw accepted by Bitkub",
        }):
            return {"ok": False, "withdrawn": False, "vault_balance": vault,
                    "reason": "Withdrawal succeeded at Bitkub but SUCCESS ledger update failed; manual reconciliation required",
                    "response": response}

        return {"ok": True, "withdrawn": True, "vault_balance": 0.0,
                "amount": vault, "fee": fee, "received_amount": received,
                "withdrawal_id": txn_id, "response": response}


profit_vault = ProfitVault()
