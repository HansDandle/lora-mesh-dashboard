"""SQLite persistence for message logs and the metrics time-series.

Low volume, so plain sqlite3 with a lock is plenty. All calls are wrapped
by callers in try/except — a storage hiccup must never take the dashboard
down (this stack has a history of things falling over). Node/contact state
is NOT persisted: it's re-synced live from each node's own database on
reconnect, so there's nothing to lose there.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any


class Persistence:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS messages "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, network TEXT, time REAL, data TEXT)"
        )
        # `dedup` lets imports (e.g. a phone-app DB export) be re-run without
        # duplicating rows. Live messages leave it NULL; SQLite allows many
        # NULLs under a UNIQUE index, so only keyed (imported) rows dedupe.
        mcols = {r[1] for r in self.conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "dedup" not in mcols:
            self.conn.execute("ALTER TABLE messages ADD COLUMN dedup TEXT")
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_dedup ON messages(dedup)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS history (t REAL PRIMARY KEY, data TEXT)"
        )
        # Durable MeshCore contact log: keeps every contact ever seen (even
        # ones later pruned off the node), keyed by public key.
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS meshcore_contacts ("
            "key TEXT PRIMARY KEY, name TEXT, type INTEGER, "
            "first_seen REAL, last_seen REAL, last_advert REAL, "
            "lat REAL, lon REAL, data TEXT)"
        )
        # Additive migration: `hops` (cached path length) came later. Older DBs
        # won't have the column, so add it if missing.
        cols = {r[1] for r in self.conn.execute(
            "PRAGMA table_info(meshcore_contacts)").fetchall()}
        if "hops" not in cols:
            self.conn.execute("ALTER TABLE meshcore_contacts ADD COLUMN hops INTEGER")
        self.conn.commit()

    def save_message(self, network: str, msg: dict[str, Any]) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO messages(network, time, data) VALUES (?, ?, ?)",
                (network, msg.get("time", time.time()), json.dumps(msg)),
            )
            self.conn.commit()

    def import_message(self, network: str, msg: dict[str, Any], dedup: str) -> bool:
        """Insert a message idempotently by dedup key. Returns True if inserted,
        False if it was already present."""
        with self._lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO messages(network, time, data, dedup) VALUES (?,?,?,?)",
                (network, msg.get("time", time.time()), json.dumps(msg), dedup),
            )
            self.conn.commit()
            return cur.rowcount > 0

    def save_history(self, point: dict[str, Any]) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO history(t, data) VALUES (?, ?)",
                (point["t"], json.dumps(point)),
            )
            self.conn.commit()

    def load_recent_messages(self, network: str, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT data FROM messages WHERE network=? ORDER BY time DESC LIMIT ?",
                (network, limit),
            ).fetchall()
        return [json.loads(r[0]) for r in reversed(rows)]

    def upsert_contacts(self, contacts: list[dict[str, Any]]) -> None:
        now = time.time()
        with self._lock:
            for c in contacts:
                key = c.get("key") or c.get("public_key")
                if not key:
                    continue
                self.conn.execute(
                    "INSERT INTO meshcore_contacts"
                    "(key, name, type, first_seen, last_seen, last_advert, lat, lon, hops, data) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET name=excluded.name, type=excluded.type, "
                    "last_seen=excluded.last_seen, last_advert=excluded.last_advert, "
                    "lat=excluded.lat, lon=excluded.lon, hops=excluded.hops, data=excluded.data",
                    (key, c.get("name"), c.get("type"), now, now, c.get("last_advert"),
                     c.get("lat") if c.get("lat") is not None else c.get("adv_lat"),
                     c.get("lon") if c.get("lon") is not None else c.get("adv_lon"),
                     c.get("hops") if c.get("hops") is not None else c.get("path_len"),
                     json.dumps(c)),
                )
            self.conn.commit()

    def load_contacts(self) -> list[dict[str, Any]]:
        cols = ("key", "name", "type", "first_seen", "last_seen", "last_advert", "lat", "lon", "hops")
        with self._lock:
            rows = self.conn.execute(
                f"SELECT {', '.join(cols)} FROM meshcore_contacts ORDER BY last_seen DESC"
            ).fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def find_contact_by_name(self, name: str) -> dict[str, Any] | None:
        """Look up a logged contact by display name (case-insensitive).

        Returns the stored row incl. the full public key, so we can message a
        contact that's in the log but no longer on the node's live list."""
        with self._lock:
            row = self.conn.execute(
                "SELECT key, name, type, lat, lon, data FROM meshcore_contacts "
                "WHERE name = ? COLLATE NOCASE ORDER BY last_seen DESC LIMIT 1",
                (name,),
            ).fetchone()
        if row is None:
            return None
        key, nm, typ, lat, lon, data = row
        out = {"key": key, "name": nm, "type": typ, "lat": lat, "lon": lon}
        try:
            out["data"] = json.loads(data) if data else {}
        except Exception:
            out["data"] = {}
        return out

    def count_contacts(self) -> int:
        with self._lock:
            return self.conn.execute("SELECT count(*) FROM meshcore_contacts").fetchone()[0]

    def load_history(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT data FROM history ORDER BY t DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(r[0]) for r in reversed(rows)]

    def close(self) -> None:
        with self._lock:
            self.conn.close()
