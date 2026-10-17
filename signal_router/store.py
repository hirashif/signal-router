from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone

from .models import Company, Signup

SCHEMA = """
CREATE TABLE IF NOT EXISTS signups (
    id          TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    payload     TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS enrichment_cache (
    domain     TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_log (
    signup_id          TEXT PRIMARY KEY,
    email              TEXT NOT NULL,
    hubspot_contact_id TEXT,
    hubspot_company_id TEXT,
    hubspot_task_id    TEXT,
    owner_email        TEXT,
    tier               TEXT,
    score              INTEGER,
    fingerprint        TEXT NOT NULL,
    synced_at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS router_state (
    queue    TEXT PRIMARY KEY,
    position INTEGER NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """Local SQLite state: inbound signups, cached enrichment, and what we synced.

    The sync log is the source of truth the nightly reconciliation compares
    against HubSpot.
    """

    def __init__(self, path: str = "signal_router.db"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # signups -----------------------------------------------------------
    def add_signup(self, s: Signup) -> bool:
        """Insert a signup. Returns False if we've already seen this id."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO signups (id, email, payload, received_at) VALUES (?, ?, ?, ?)",
            (s.id, s.email.lower(), json.dumps(asdict(s)), now()),
        )
        self.db.commit()
        return cur.rowcount == 1

    def signups(self) -> list[Signup]:
        rows = self.db.execute("SELECT payload FROM signups ORDER BY received_at, id").fetchall()
        return [Signup(**json.loads(r["payload"])) for r in rows]

    # enrichment cache --------------------------------------------------
    def cached_company(self, domain: str) -> Company | None:
        row = self.db.execute(
            "SELECT payload FROM enrichment_cache WHERE domain = ?", (domain,)
        ).fetchone()
        return Company(**json.loads(row["payload"])) if row else None

    def cache_company(self, c: Company) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO enrichment_cache (domain, payload, fetched_at) VALUES (?, ?, ?)",
            (c.domain, json.dumps(asdict(c)), now()),
        )
        self.db.commit()

    # sync log ----------------------------------------------------------
    def last_sync(self, signup_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM sync_log WHERE signup_id = ?", (signup_id,)
        ).fetchone()

    def record_sync(self, **row) -> None:
        row.setdefault("synced_at", now())
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.db.execute(
            f"INSERT OR REPLACE INTO sync_log ({cols}) VALUES ({marks})", tuple(row.values())
        )
        self.db.commit()

    def synced(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM sync_log ORDER BY synced_at").fetchall()

    # round-robin position ---------------------------------------------
    def router_state(self) -> dict[str, int]:
        rows = self.db.execute("SELECT queue, position FROM router_state").fetchall()
        return {r["queue"]: r["position"] for r in rows}

    def save_router_state(self, state: dict[str, int]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO router_state (queue, position) VALUES (?, ?)", state.items()
        )
        self.db.commit()
