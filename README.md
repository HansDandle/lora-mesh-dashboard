# LoRa Mesh Dashboard

One self-hosted web dashboard that monitors **two different LoRa mesh
networks at once** — a [Meshtastic](https://meshtastic.org) node and a
[MeshCore](https://meshcore.co.uk) node — in a single pane, over your LAN
(not tethered to a phone over Bluetooth).

Built as a home project on a pair of Heltec V3 (ESP32-S3 / SX1262) boards,
then generalized. Runs as one Docker container that slots into an existing
Docker / Home-Assistant / Mosquitto setup.

## What it does

- **Meshtastic panel** — node battery/SNR/uptime, the known-nodes table with
  an **RF-direct vs MQTT** contact badge (so you can tell a real over-the-air
  neighbour from an internet-bridged one), a live message log, and a send box.
- **MeshCore panel** — connects to a MeshCore **WiFi companion** over TCP on
  the LAN: status, battery, contacts, messages, send, plus **radio controls**
  (send advert, view/set frequency·BW·SF·CR).
- **Signal history** — sparklines of best-SNR and RF-node count over time, so
  you can see the before/after when you move an antenna.
- **Watchdog alerts** — browser notifications when a node goes offline or a
  battery drops below a threshold.
- **"Release to phone" toggles** — each node's TCP companion accepts one
  client at a time, so hand the slot to the phone app (or take it back) from
  the browser.
- **Persistence** — messages and metrics history survive restarts (SQLite).
- **MQTT** — subscribes to the Meshtastic JSON uplink; docs include ready-made
  Home Assistant sensor templates.

## Quick start

```bash
cp dashboard/config.example.yaml dashboard/config.yaml   # optional; env vars also work
docker compose up -d --build
```

Then open **http://localhost:8090**. It starts fine with nothing reachable
and shows per-source connection state rather than erroring.

## Configuration

Set via env vars in [`docker-compose.yml`](docker-compose.yml) or
`dashboard/config.yaml` (env vars win). Example values shown:

| Var | Meaning | Example |
|---|---|---|
| `MESHTASTIC_HOST` | Meshtastic node IP (TCP API 4403); empty = disabled | `192.168.1.50` |
| `MESHCORE_HOST` | MeshCore WiFi-companion node IP; empty = disabled | `192.168.1.51` |
| `MESHCORE_PORT` | MeshCore companion TCP port | `5000` |
| `MQTT_HOST` | Mosquitto broker (from a container, often `host.docker.internal`) | `192.168.1.10` |
| `MQTT_PORT` | Broker port | `1883` |

## Docs

- [docs/meshtastic-node-setup.md](docs/meshtastic-node-setup.md) — enable WiFi
  + MQTT JSON uplink on a Meshtastic node
- [docs/meshcore-board2-flash.md](docs/meshcore-board2-flash.md) — compile +
  flash a MeshCore **WiFi companion** firmware (there's no prebuilt WiFi
  binary) and wire it into the dashboard over the LAN
- [docs/home-assistant-mqtt.md](docs/home-assistant-mqtt.md) — ready-made
  Home Assistant `mqtt.yaml` sensors

## Architecture

FastAPI backend, vanilla-JS frontend (no build step), WebSocket push. Each
network is a pluggable "source" in `dashboard/app/` (`meshtastic_source.py`,
`meshcore_source.py`, `mqtt_source.py`). A `reticulum_source.py` stub is
included from an earlier plan (superseded by MeshCore) if you want to extend
it.

> Config placeholders (IPs, node IDs, WiFi SSID/PWD) throughout the docs are
> **examples** — substitute your own.

## License

MIT — see [LICENSE](LICENSE).
