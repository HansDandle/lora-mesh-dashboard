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

    def load_history(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT data FROM history ORDER BY t DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(r[0]) for r in reversed(rows)]

    def close(self) -> None:
        with self._lock:
            self.conn.close()
