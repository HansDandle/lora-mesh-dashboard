"""Subscribes to the Meshtastic JSON uplink on the existing Mosquitto broker.

Independent of the TCP link: if the node's WiFi/TCP API is down but its
MQTT uplink (or another gateway) is up, the dashboard still gets data.
Topic shape: msh/<region>/2/json/<channel>/!<gateway-node-id>
"""
from __future__ import annotations

import json
import logging

import paho.mqtt.client as mqtt

from .state import DashboardState

log = logging.getLogger("mqtt_source")


class MqttSource:
    def __init__(self, state: DashboardState, host: str, port: int = 1883,
                 topic: str = "msh/+/2/json/#", username: str = "", password: str = ""):
        self.state = state
        self.host = host
        self.port = port
        self.topic = topic
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id="lora-mesh-dashboard")
        if username:
            self.client.username_pw_set(username, password)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        # paho reconnects automatically inside loop_start's thread.
        self.client.reconnect_delay_set(min_delay=5, max_delay=120)

    def start(self) -> None:
        self.state.set_source_status("mqtt", False, f"connecting to {self.host}:{self.port}")
        try:
            self.client.connect_async(self.host, self.port, keepalive=60)
        except Exception as exc:
            self.state.set_source_status("mqtt", False, str(exc))
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        try:
            self.client.disconnect()
        except Exception:
            pass

    # -- callbacks (paho network thread) ------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            client.subscribe(self.topic)
            self.state.set_source_status("mqtt", True, f"{self.host}:{self.port} ({self.topic})")
        else:
            self.state.set_source_status("mqtt", False, f"connect failed: {reason_code}")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.state.set_source_status("mqtt", False, f"disconnected: {reason_code}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeError):
            return
        try:
            self._handle(payload)
        except Exception as exc:
            log.warning("bad mqtt payload on %s: %s", msg.topic, exc)

    def _handle(self, data: dict) -> None:
        sender = data.get("sender") or ""
        from_num = data.get("from")
        node_id = sender or (f"!{from_num:08x}" if isinstance(from_num, int) else None)
        if node_id is None:
            return
        msg_type = data.get("type")
        payload = data.get("payload") or {}

        fields = {"via": "mqtt"}
        if msg_type == "telemetry" or "battery_level" in payload:
            fields.update({
                "battery": payload.get("battery_level"),
                "voltage": payload.get("voltage"),
                "ch_util": payload.get("channel_utilization"),
                "air_util_tx": payload.get("air_util_tx"),
                "uptime": payload.get("uptime_seconds"),
            })
        if msg_type == "nodeinfo":
            fields.update({
                "name": payload.get("longname"),
                "short_name": payload.get("shortname"),
                "hw": payload.get("hardware"),
            })
        if data.get("snr") is not None:
            fields["snr"] = data.get("snr")
        if data.get("rssi") is not None:
            fields["rssi"] = data.get("rssi")
        if data.get("timestamp"):
            fields["last_heard"] = data["timestamp"]
        self.state.update_node(node_id, fields)

        if msg_type == "text" and payload.get("text"):
            self.state.add_message({
                "network": "meshtastic",
                "direction": "rx",
                "from": node_id,
                "text": payload["text"],
                "snr": data.get("snr"),
                "rssi": data.get("rssi"),
                "via": "mqtt",
            })
