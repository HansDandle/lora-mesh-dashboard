"""Shared in-memory dashboard state.

Written to from background threads (meshtastic pubsub callbacks, paho-mqtt
network thread) and read from the asyncio event loop, so all mutation goes
through a threading.Lock and change notification hops onto the loop with
call_soon_threadsafe.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Optional

import asyncio


class DashboardState:
    def __init__(self, message_log_size: int = 500):
        self._lock = threading.Lock()
        self._nodes: dict[str, dict[str, Any]] = {}
        self._messages: deque[dict[str, Any]] = deque(maxlen=message_log_size)
        self._sources: dict[str, dict[str, Any]] = {}
        self._reticulum: dict[str, Any] = {}
        self._my_node: dict[str, Any] = {}
        # MeshCore (Board 2) — a separate network with its own self info,
        # contacts, and message log.
        self._meshcore_self: dict[str, Any] = {}
        self._meshcore_contacts: dict[str, dict[str, Any]] = {}
        self._meshcore_messages: deque[dict[str, Any]] = deque(maxlen=message_log_size)
        # Channels configured on Board 2, and the (separate) channel message log.
        self._meshcore_channels: list[dict[str, Any]] = []
        self._meshcore_channel_messages: deque[dict[str, Any]] = deque(maxlen=message_log_size)
        # Time-series for the antenna/signal tracker (one point per sample).
        # ~4h at 60s spacing.
        self._history: deque[dict[str, Any]] = deque(maxlen=240)
        self._version = 0
        self._meshcore_logged = 0  # count of contacts durably logged to the DB
        # Optional Persistence handle; set by main after construction.
        self.persistence = None

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._changed: Optional[asyncio.Event] = None

    # -- asyncio wiring -----------------------------------------------------

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._changed = asyncio.Event()

    async def wait_for_change(self) -> None:
        assert self._changed is not None
        await self._changed.wait()
        self._changed.clear()

    def _notify(self) -> None:
        self._version += 1
        if self._loop is not None and self._changed is not None:
            self._loop.call_soon_threadsafe(self._changed.set)

    # -- writers (called from source threads) -------------------------------

    def update_node(self, node_id: str, fields: dict[str, Any]) -> None:
        with self._lock:
            node = self._nodes.setdefault(node_id, {"id": node_id})
            node.update({k: v for k, v in fields.items() if v is not None})
            node["updated"] = time.time()
            self._notify()

    def set_my_node(self, fields: dict[str, Any]) -> None:
        with self._lock:
            self._my_node.update(fields)
            self._notify()

    def add_message(self, message: dict[str, Any]) -> None:
        with self._lock:
            message.setdefault("time", time.time())
            self._messages.append(message)
            self._notify()
        self._persist_message("meshtastic", message)

    def set_source_status(self, name: str, connected: bool, detail: str = "") -> None:
        with self._lock:
            self._sources[name] = {
                "connected": connected,
                "detail": detail,
                "since": time.time(),
            }
            self._notify()

    def set_reticulum(self, status: dict[str, Any]) -> None:
        with self._lock:
            self._reticulum = status
            self._notify()

    # -- MeshCore writers ---------------------------------------------------

    def set_meshcore_self(self, fields: dict[str, Any]) -> None:
        with self._lock:
            self._meshcore_self.update({k: v for k, v in fields.items() if v is not None})
            self._notify()

    def set_meshcore_contacts(self, contacts: list[dict[str, Any]]) -> None:
        with self._lock:
            self._meshcore_contacts = {c["key"]: c for c in contacts if c.get("key")}
            self._notify()
        if self.persistence is not None:
            try:
                self.persistence.upsert_contacts(contacts)
                self._meshcore_logged = self.persistence.count_contacts()
            except Exception:
                pass

    def add_meshcore_message(self, message: dict[str, Any]) -> None:
        with self._lock:
            message.setdefault("time", time.time())
            self._meshcore_messages.append(message)
            self._notify()
        self._persist_message("meshcore", message)

    def set_meshcore_channels(self, channels: list[dict[str, Any]]) -> None:
        with self._lock:
            self._meshcore_channels = list(channels)
            self._notify()

    def add_meshcore_channel_message(self, message: dict[str, Any]) -> None:
        with self._lock:
            message.setdefault("time", time.time())
            self._meshcore_channel_messages.append(message)
            self._notify()
        self._persist_message("meshcore_channel", message)

    def _persist_message(self, network: str, message: dict[str, Any]) -> None:
        if self.persistence is not None:
            try:
                self.persistence.save_message(network, message)
            except Exception:
                pass

    # -- history sampler ----------------------------------------------------

    def record_history(self) -> None:
        """Snapshot current signal metrics into the time-series. Called on a
        timer; does not notify (history rides along on the next change)."""
        with self._lock:
            rf = [n for n in self._nodes.values()
                  if n.get("rssi") and n.get("hops") == 0 and n.get("snr") is not None]
            best_snr = max((n["snr"] for n in rf), default=None)
            point = {
                "t": time.time(),
                "best_snr": best_snr,
                "rf_nodes": len(rf),
                "mesh_nodes": len(self._nodes),
                "mc_contacts": len(self._meshcore_contacts),
            }
            self._history.append(point)
            self._version += 1
        if self.persistence is not None:
            try:
                self.persistence.save_history(point)
            except Exception:
                pass

    def load_from(self, persistence) -> None:
        """Prime in-memory state from the DB at startup."""
        with self._lock:
            for m in persistence.load_recent_messages("meshtastic", self._messages.maxlen):
                self._messages.append(m)
            for m in persistence.load_recent_messages("meshcore", self._meshcore_messages.maxlen):
                self._meshcore_messages.append(m)
            for m in persistence.load_recent_messages(
                    "meshcore_channel", self._meshcore_channel_messages.maxlen):
                self._meshcore_channel_messages.append(m)
            for p in persistence.load_history(self._history.maxlen):
                self._history.append(p)
        self._meshcore_logged = persistence.count_contacts()

    # -- reader -------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "version": self._version,
                "my_node": dict(self._my_node),
                "nodes": [dict(n) for n in self._nodes.values()],
                "messages": [dict(m) for m in self._messages],
                "sources": {k: dict(v) for k, v in self._sources.items()},
                "reticulum": dict(self._reticulum),
                "meshcore": {
                    "self": dict(self._meshcore_self),
                    "contacts": [dict(c) for c in self._meshcore_contacts.values()],
                    "messages": [dict(m) for m in self._meshcore_messages],
                    "channels": [dict(c) for c in self._meshcore_channels],
                    "channel_messages": [dict(m) for m in self._meshcore_channel_messages],
                    "logged": self._meshcore_logged,
                },
                "history": [dict(p) for p in self._history],
                "server_time": time.time(),
            }
