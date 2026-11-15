import sqlite3

from app.storage.sqlite import Db


def test_schema_wal_and_migration_idempotent(tmp_path):
    path = str(tmp_path / "sub" / "aether.db")
    db = Db(path)
    assert db._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 1
    db.add_event("x")
    db.close()
    db2 = Db(path)  # reopening must not re-run the migration
    assert db2.counts() == {"advisories": 0, "events": 1, "chat": 0}
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(advisories)")}
    assert {"trigger", "window_summary", "facts_used", "first_token_ms", "citations", "raw_text", "fallback"} <= cols


def test_advisory_roundtrip_with_json_fields():
    db = Db(":memory:")
    i = db.add_advisory(compartment_id="A", trigger="status_change", status="SMOLDERING_ANOMALY", confidence=0.6,
                        steps=["a", "b", "c"], citations=[{"id": "PSI-98"}], facts_used=["PSI-98"],
                        window_summary={"samples": 3}, warning="w", fallback=1, model="deterministic")
    a = db.advisory(i)
    assert a["steps"] == ["a", "b", "c"] and a["citations"] == [{"id": "PSI-98"}] and a["fallback"] is True
    assert db.latest_advisory("A")["id"] == i and db.latest_advisory("B") is None
    j = db.add_advisory(compartment_id="A", trigger="periodic")
    assert [x["id"] for x in db.advisories("A", 10)] == [j, i]
    assert db.advisories(limit=1)[0]["id"] == j


def test_events_and_chat():
    db = Db(":memory:")
    db.add_event("status_change", "A", {"from": "NOMINAL"})
    db.add_event("influx_down", None, {"detail": "x"})
    ev = db.events(10)
    assert [e["kind"] for e in ev] == ["influx_down", "status_change"]
    assert ev[1]["detail"] == {"from": "NOMINAL"} and db.events(10, "A")[0]["kind"] == "status_change"
    c = db.add_chat("A", "user", "hi")
    db.add_chat("A", "assistant", "yo", advisory_id=c)
    assert [m["role"] for m in db.chat("A")] == ["assistant", "user"]
