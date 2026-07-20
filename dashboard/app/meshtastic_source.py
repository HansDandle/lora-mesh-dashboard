"""Meshtastic node connection over WiFi/TCP using the official python lib.

The node only accepts ONE TCP client at a time, so this service owns the
connection and everything else (browser, HA) goes through us or MQTT.
Runs in a background thread with exponential-backoff reconnect: on this
stack, the node being unreachable is a normal state, not an error.
"""
from __future__ import annotations

import logging
import threading
import time

from pubsub import pub

from .state import DashboardState

log = logging.getLogger("meshtastic_source")

_BACKOFF_START = 5
_BACKOFF_MAX = 120
_NODE_POLL_SECS = 30


class MeshtasticSource:
    def __init__(self, state: DashboardState, host: str, port: int = 4403):
        self.state = state
        self.host = host
        self.port = port
        self.interface = None
        self._my_node_id: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Runtime "release Board 1's single TCP slot to the phone" toggle.
        self._paused = False
        self._resume_evt = threading.Event()
        # Set on connect, cleared on the lib's "connection lost" event, so the
        # poll loop can tell a dead link from a live one (cached node DB reads
        # succeed even after the socket drops — that was a silent-failure hole).
        self._link_ok = threading.Event()

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if not self.host:
            self.state.set_source_status(
                "meshtastic_tcp", False,
                "disabled — set MESHTASTIC_HOST once the node has WiFi",
            )
            return
        pub.subscribe(self._on_receive, "meshtastic.receive")
        pub.subscribe(self._on_connected, "meshtastic.connection.established")
        pub.subscribe(self._on_disconnected, "meshtastic.connection.lost")
        self._thread = threading.Thread(target=self._run, daemon=True, name="meshtastic")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._resume_evt.set()
        iface = self.interface
        if iface is not None:
            try:
                iface.close()
            except Exception:
                pass

    def pause(self) -> None:
        """Release Board 1's single TCP slot (e.g. to the Meshtastic app)."""
        self._paused = True
        iface = self.interface
        if iface is not None:
            try:
                iface.close()
            except Exception:
                pass

    def resume(self) -> None:
        self._paused = False
        self._resume_evt.set()

    @property
    def paused(self) -> bool:
        return self._paused

    def send_text(self, text: str) -> None:
        iface = self.interface
        if iface is None:
            raise RuntimeError("Meshtastic node is not connected")
        iface.sendText(text)
        self.state.add_message({
            "network": "meshtastic",
            "direction": "tx",
            "from": "dashboard",
            "text": text,
        })

    # -- connection loop ----------------------------------------------------

    def _run(self) -> None:
        backoff = _BACKOFF_START
        while not self._stop.is_set():
            if self._paused:
                self.state.set_source_status(
                    "meshtastic_tcp", False, "paused — released to the phone/app")
                self._resume_evt.wait()
                self._resume_evt.clear()
                continue
            try:
                self.state.set_source_status(
                    "meshtastic_tcp", False, f"connecting to {self.host}:{self.port}")
                from meshtastic.tcp_interface import TCPInterface
                self.interface = TCPInterface(hostname=self.host, portNumber=self.port)
                self._link_ok.set()
                backoff = _BACKOFF_START
                self._poll_until_dead()
            except Exception as exc:
                log.warning("meshtastic connection failed: %s", exc)
                self.state.set_source_status("meshtastic_tcp", False, str(exc))
            finally:
                self._link_ok.clear()
                iface, self.interface = self.interface, None
                if iface is not None:
                    try:
                        iface.close()
                    except Exception:
                        pass
            if self._paused:
                continue  # loop straight to the paused-wait, no backoff
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, _BACKOFF_MAX)

    def _poll_until_dead(self) -> None:
        """Refresh the node DB periodically while the connection lives."""
        while (not self._stop.is_set() and not self._paused
               and self._link_ok.is_set() and self.interface is not None):
            try:
                self._sync_nodedb()
            except Exception as exc:
                log.warning("node DB sync failed, reconnecting: %s", exc)
                return
            if self._stop.wait(_NODE_POLL_SECS):
                return

    # -- event handlers (pubsub, called on lib threads) ---------------------

    def _on_connected(self, interface=None, **kw) -> None:
        self.state.set_source_status("meshtastic_tcp", True, f"{self.host}:{self.port}")
        try:
            info = interface.getMyNodeInfo() or {}
            user = info.get("user", {})
            metrics = info.get("deviceMetrics", {})
            self._my_node_id = user.get("id")
            self.state.set_my_node({
                "id": user.get("id"),
                "name": user.get("longName"),
                "hw": user.get("hwModel"),
                "battery": metrics.get("batteryLevel"),
                "voltage": metrics.get("voltage"),
                "uptime": metrics.get("uptimeSeconds"),
            })
        except Exception as exc:
            log.warning("getMyNodeInfo failed: %s", exc)

    def _on_disconnected(self, interface=None, **kw) -> None:
        self._link_ok.clear()
        self.state.set_source_status("meshtastic_tcp", False, "connection lost")

    def _on_receive(self, packet=None, interface=None, **kw) -> None:
        if not packet:
            return
        decoded = packet.get("decoded", {})
        portnum = decoded.get("portnum")
        sender = packet.get("fromId") or str(packet.get("from", "?"))
        snr = packet.get("rxSnr")
        rssi = packet.get("rxRssi")
        if portnum == "TEXT_MESSAGE_APP":
            self.state.add_message({
                "network": "meshtastic",
                "direction": "rx",
                "from": sender,
                "text": decoded.get("text", ""),
                "snr": snr,
                "rssi": rssi,
            })
        # Any packet proves the sender is alive.
        fields = {"last_heard": time.time(), "via": "tcp"}
        if snr is not None:
            fields["snr"] = snr
        # A non-zero RSSI means our own radio received this over the air
        # (Meshtastic reports rxRssi 0 for packets injected via MQTT), so
        # this is a genuine direct RF neighbour — 0 hops, not MQTT.
        if rssi:
            fields["rssi"] = rssi
            fields["hops"] = 0
            fields["via_mqtt"] = False
        self.state.update_node(sender, fields)

    # -- node DB ------------------------------------------------------------

    def _sync_nodedb(self) -> None:
        iface = self.interface
        if iface is None or not getattr(iface, "nodes", None):
            return
        for node_id, node in list(iface.nodes.items()):
            user = node.get("user", {})
            metrics = node.get("deviceMetrics", {})
            self.state.update_node(node_id, {
                "name": user.get("longName"),
                "short_name": user.get("shortName"),
                "hw": user.get("hwModel"),
                "battery": metrics.get("batteryLevel"),
                "voltage": metrics.get("voltage"),
                "snr": node.get("snr"),
                "last_heard": node.get("lastHeard"),
                "hops": node.get("hopsAway"),
                # Authoritative "heard over the internet, not our radio" flag
                # straight from the node DB — drives the RF-vs-MQTT badge.
                "via_mqtt": node.get("viaMqtt"),
                "via": "tcp",
            })
            # Keep the "my node" tiles (battery/voltage/uptime) live — they
            # were only seeded at connect time, so they'd otherwise show
            # whatever was true when the TCP link came up.
            if node_id == self._my_node_id:
                fresh = {
                    "battery": metrics.get("batteryLevel"),
                    "voltage": metrics.get("voltage"),
                    "uptime": metrics.get("uptimeSeconds"),
                }
                self.state.set_my_node({k: v for k, v in fresh.items() if v is not None})
