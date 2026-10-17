"""Production database awareness for the trading bot.

The bot never blindly assumes a table/column exists.  Schema is discovered from
PostgreSQL information_schema.columns through the Supabase RPC
get_public_schema(), then payloads are filtered against the discovered schema.
"""
from __future__ import annotations

import time
from collections import defaultdict


class DatabaseAwareness:
    REQUIRED_TABLES = (
        "bot_market_snapshots",
        "bot_decisions",
        "bot_positions",
        "bot_orders",
        "bot_symbol_learning",
    )

    def __init__(self, client, ttl_seconds: int = 60):
        self.client = client
        self.ttl_seconds = ttl_seconds
        self._loaded_at = 0.0
        self.tables = defaultdict(dict)
        self.available = False

    def refresh(self, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and self.available and now - self._loaded_at < self.ttl_seconds:
            return True
        try:
            rows = self.client.rpc("get_public_schema", None)
            if not isinstance(rows, list):
                print(f"[DB-AWARENESS] Schema RPC returned invalid data: {type(rows).__name__}")
                self.available = False
                return False
            tables = defaultdict(dict)
            for row in rows:
                table = row.get("table_name")
                column = row.get("column_name")
                if table and column:
                    tables[table][column] = row.get("data_type")
            self.tables = tables
            self.available = bool(tables)
            self._loaded_at = now
            print(f"[DB-AWARENESS] Schema loaded: {len(tables)} tables")
            for table in self.REQUIRED_TABLES:
                if table not in tables:
                    print(f"[DB-AWARENESS][WARN] Missing table/view: {table}")
            return self.available
        except Exception as exc:
            self.available = False
            print(f"[DB-AWARENESS][ERROR] Schema refresh failed: {exc}")
            return False

    def has_table(self, table: str) -> bool:
        self.refresh()
        return table in self.tables

    def has_column(self, table: str, column: str) -> bool:
        self.refresh()
        return column in self.tables.get(table, {})

    def columns(self, table: str) -> dict:
        self.refresh()
        return dict(self.tables.get(table, {}))

    def filter_payload(self, table: str, payload: dict) -> dict:
        """Return only columns confirmed by information_schema.

        Unknown columns are logged and omitted. This is deliberate: schema drift
        must not crash the trading loop.
        """
        if not self.refresh():
            raise RuntimeError("database schema unavailable")
        if table not in self.tables:
            raise RuntimeError(f"table/view {table} not found in database schema")
        allowed = self.tables[table]
        filtered = {}
        for key, value in payload.items():
            if key in allowed:
                filtered[key] = value
            else:
                print(f"[DB-AWARENESS][WARN] {table}.{key} missing; field skipped")
        return filtered

    def validate_required(self, table: str, required_columns: tuple[str, ...]) -> bool:
        if not self.refresh():
            return False
        missing = [c for c in required_columns if c not in self.tables.get(table, {})]
        if missing:
            print(f"[DB-AWARENESS][ERROR] {table} missing required columns: {missing}")
            return False
        return True
