"""SQLite (WAL) for advisories, events and chat. Small tables, tiny writes, one shared connection."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MIGRATIONS: list[str] = [
    # v1
    """
    CREATE TABLE advisories(
        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, compartment_id TEXT NOT NULL,
        trigger TEXT NOT NULL,            -- status_change | periodic | ask | dust_rejected
        status TEXT, confidence REAL, window_summary TEXT, facts_used TEXT,   -- JSON
        model TEXT, latency_ms INTEGER, first_token_ms INTEGER,
        warning TEXT, steps TEXT, citations TEXT, raw_text TEXT,
        fallback INTEGER NOT NULL DEFAULT 0   -- 1 = deterministic text, not LLM output
    );
    CREATE INDEX idx_advisories_cid ON advisories(compartment_id, id DESC);
    CREATE TABLE events(
        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, compartment_id TEXT,
        kind TEXT NOT NULL, detail TEXT   -- JSON
    );
    CREATE INDEX idx_events_id ON events(id DESC);
    CREATE TABLE chat(
        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, compartment_id TEXT,
        role TEXT NOT NULL, content TEXT NOT NULL, advisory_id INTEGER
    );
    CREATE INDEX idx_chat_cid ON chat(compartment_id, id DESC);
    """,
]

_ADVISORY_JSON = ("window_summary", "facts_used", "steps", "citations")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Db:
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=3000")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
                self._conn.executescript("BEGIN;" + script + f"PRAGMA user_version={i};COMMIT;")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, args)

    def _all(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    # ---- events -----------------------------------------------------------------------------
    def add_event(self, kind: str, compartment_id: str | None = None, detail: Any = None) -> int:
        cur = self._exec(
            "INSERT INTO events(created_at, compartment_id, kind, detail) VALUES (?,?,?,?)",
            (_now(), compartment_id, kind, json.dumps(detail, separators=(",", ":")) if detail is not None else None),
        )
        return cur.lastrowid

    def events(self, limit: int = 50, compartment_id: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM events", ()
        if compartment_id:
            sql, args = sql + " WHERE compartment_id = ?", (compartment_id,)
        rows = self._all(sql + " ORDER BY id DESC LIMIT ?", args + (limit,))
        return [{**dict(r), "detail": json.loads(r["detail"]) if r["detail"] else None} for r in rows]

    # ---- advisories -------------------------------------------------------------------------
    def add_advisory(self, **f: Any) -> int:
        row = {
            "created_at": _now(), "compartment_id": None, "trigger": None, "status": None, "confidence": None,
            "window_summary": None, "facts_used": None, "model": None, "latency_ms": None,
            "first_token_ms": None, "warning": None, "steps": None, "citations": None, "raw_text": None,
            "fallback": 0,
        }
        row.update(f)
        for key in _ADVISORY_JSON:
            if row[key] is not None and not isinstance(row[key], str):
                row[key] = json.dumps(row[key], separators=(",", ":"))
        cols = ",".join(row)
        cur = self._exec(f"INSERT INTO advisories({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        return cur.lastrowid

    @staticmethod
    def _advisory(row: sqlite3.Row) -> dict:
        d = dict(row)
        for key in _ADVISORY_JSON:
            if d[key] is not None:
                d[key] = json.loads(d[key])
        d["fallback"] = bool(d["fallback"])
        return d

    def advisories(self, compartment_id: str | None = None, limit: int = 20) -> list[dict]:
        sql, args = "SELECT * FROM advisories", ()
        if compartment_id:
            sql, args = sql + " WHERE compartment_id = ?", (compartment_id,)
        return [self._advisory(r) for r in self._all(sql + " ORDER BY id DESC LIMIT ?", args + (limit,))]

    def advisory(self, advisory_id: int) -> dict | None:
        rows = self._all("SELECT * FROM advisories WHERE id = ?", (advisory_id,))
        return self._advisory(rows[0]) if rows else None

    def latest_advisory(self, compartment_id: str) -> dict | None:
        rows = self.advisories(compartment_id, 1)
        return rows[0] if rows else None

    # ---- chat -------------------------------------------------------------------------------
    def add_chat(self, compartment_id: str | None, role: str, content: str, advisory_id: int | None = None) -> int:
        cur = self._exec(
            "INSERT INTO chat(created_at, compartment_id, role, content, advisory_id) VALUES (?,?,?,?,?)",
            (_now(), compartment_id, role, content, advisory_id),
        )
        return cur.lastrowid

    def chat(self, compartment_id: str | None = None, limit: int = 50) -> list[dict]:
        sql, args = "SELECT * FROM chat", ()
        if compartment_id:
            sql, args = sql + " WHERE compartment_id = ?", (compartment_id,)
        return [dict(r) for r in self._all(sql + " ORDER BY id DESC LIMIT ?", args + (limit,))]

    def counts(self) -> dict[str, int]:
        return {t: self._all(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] for t in ("advisories", "events", "chat")}
