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
        self.conn.commit()

    def save_message(self, network: str, msg: dict[str, Any]) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO messages(network, time, data) VALUES (?, ?, ?)",
                (network, msg.get("time", time.time()), json.dumps(msg)),
            )
            self.conn.commit()

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
                    "(key, name, type, first_seen, last_seen, last_advert, lat, lon, data) "
                    "VALUES (?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET name=excluded.name, type=excluded.type, "
                    "last_seen=excluded.last_seen, last_advert=excluded.last_advert, "
                    "lat=excluded.lat, lon=excluded.lon, data=excluded.data",
                    (key, c.get("name"), c.get("type"), now, now, c.get("last_advert"),
                     c.get("lat") if c.get("lat") is not None else c.get("adv_lat"),
                     c.get("lon") if c.get("lon") is not None else c.get("adv_lon"),
                     json.dumps(c)),
                )
            self.conn.commit()

    def load_contacts(self) -> list[dict[str, Any]]:
        cols = ("key", "name", "type", "first_seen", "last_seen", "last_advert", "lat", "lon")
        with self._lock:
            rows = self.conn.execute(
                f"SELECT {', '.join(cols)} FROM meshcore_contacts ORDER BY last_seen DESC"
            ).fetchall()
        return [dict(zip(cols, r)) for r in rows]

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
