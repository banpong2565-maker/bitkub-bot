from database_awareness import DatabaseAwareness


class FakeClient:
    def rpc(self, name, payload):
        assert name == "get_public_schema"
        return [
            {"table_name": "bot_decisions", "column_name": "symbol", "data_type": "text"},
            {"table_name": "bot_decisions", "column_name": "score", "data_type": "numeric"},
        ]


def test_schema_filtering_and_types():
    db = DatabaseAwareness(FakeClient())
    assert db.refresh(force=True)
    assert db.has_column("bot_decisions", "score")
    assert db.columns("bot_decisions")["score"] == "numeric"
    assert db.filter_payload("bot_decisions", {"symbol": "NEAR_THB", "missing": 1}) == {"symbol": "NEAR_THB"}
