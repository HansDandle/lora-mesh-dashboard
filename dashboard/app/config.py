"""Configuration: YAML file overridden by environment variables.

Precedence: env var > config.yaml > default. All settings have safe
defaults so the app starts (in a degraded, disconnected state) with no
config at all.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Config:
    # Meshtastic node reachable over WiFi/TCP (meshtastic API port 4403).
    # Empty host disables the TCP source.
    meshtastic_host: str = ""
    meshtastic_port: int = 4403

    # MeshCore node (Board 2) over WiFi/TCP companion (default port 5000).
    # Empty host disables the MeshCore source.
    meshcore_host: str = ""
    meshcore_port: int = 5000

    # Existing Mosquitto broker. Always use the host
    # LAN IP, never the ephemeral 172.17.x Docker-internal address.
    mqtt_host: str = "192.168.1.10"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    # Meshtastic JSON uplink topic pattern (region segment varies).
    mqtt_topic: str = "msh/+/2/json/#"

    http_port: int = 8080
    message_log_size: int = 500

    # SQLite file for message log + metrics history. Empty disables
    # persistence (in-memory only).
    db_path: str = "/data/dashboard.db"

    extras: dict = field(default_factory=dict)


_ENV_MAP = {
    "meshtastic_host": "MESHTASTIC_HOST",
    "meshtastic_port": "MESHTASTIC_PORT",
    "meshcore_host": "MESHCORE_HOST",
    "meshcore_port": "MESHCORE_PORT",
    "mqtt_host": "MQTT_HOST",
    "mqtt_port": "MQTT_PORT",
    "mqtt_username": "MQTT_USERNAME",
    "mqtt_password": "MQTT_PASSWORD",
    "mqtt_topic": "MQTT_TOPIC",
    "http_port": "HTTP_PORT",
    "message_log_size": "MESSAGE_LOG_SIZE",
    "db_path": "DB_PATH",
}


def load_config(path: str | None = None) -> Config:
    cfg = Config()

    yaml_path = Path(path or os.environ.get("CONFIG_FILE", "config.yaml"))
    if yaml_path.is_file():
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        for key, value in data.items():
            if hasattr(cfg, key) and key != "extras":
                setattr(cfg, key, value)
            else:
                cfg.extras[key] = value

    for attr, env in _ENV_MAP.items():
        raw = os.environ.get(env)
        if raw is None or raw == "":
            continue
        current = getattr(cfg, attr)
        setattr(cfg, attr, int(raw) if isinstance(current, int) else raw)

    return cfg
