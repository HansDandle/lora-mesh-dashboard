# Meshtastic node: enable WiFi + MQTT uplink

Board 1 (Heltec V3 clone, Meshtastic v2.7.x, region US). Two things to
configure so the dashboard and Home Assistant can see it:

1. **WiFi** → gives the node an IP, so the dashboard can use the TCP API
   (port 4403) via the `meshtastic` Python library.
2. **MQTT module with JSON** → node publishes telemetry/messages to the
   existing Mosquitto broker at `192.168.1.10:1883`, which Home Assistant
   already talks to.

All commands use the Meshtastic CLI (`pip install meshtastic`) over USB
serial. Plug the board in and check the COM port in Device Manager
(CP210x driver). Add `--port COM5` (or whatever it is) if auto-detect
fails.

## 1. Enable WiFi

```
meshtastic --set network.wifi_enabled true --set network.wifi_ssid "YOUR_SSID" --set network.wifi_psk "YOUR_PASSWORD"
```

The node reboots. Its IP shows on the OLED (and in your router's client
list). Consider a DHCP reservation so `MESHTASTIC_HOST` stays stable.

Note: on ESP32, enabling WiFi **disables Bluetooth** — the phone app will
no longer connect over BLE. The phone app can connect over the network
instead ("Add via IP"), and the dashboard takes over monitoring duty. If
you want BLE back, set `network.wifi_enabled false`.

## 2. Enable the MQTT uplink

```
meshtastic --set mqtt.enabled true --set mqtt.address 192.168.1.10 --set mqtt.username "" --set mqtt.password "" --set mqtt.json_enabled true --set mqtt.encryption_enabled false
```

(Mosquitto here has no auth configured; leave username/password empty.
`encryption_enabled false` is what makes the JSON payloads readable by
HA/the dashboard — fine on a private LAN broker, don't do this against a
public broker.)

Then allow the primary channel to uplink (and downlink if you ever want
to send from MQTT):

```
meshtastic --ch-index 0 --ch-set uplink_enabled true --ch-set downlink_enabled true
```

## 3. Verify

From WSL2 or any machine with mosquitto clients:

```
mosquitto_sub -h 192.168.1.10 -t 'msh/#' -v
```

Within a couple of minutes you should see JSON on topics like
`msh/US/2/json/LongFast/!xxxxxxxx` (telemetry every few minutes,
nodeinfo, and any text messages). That `!xxxxxxxx` is the node ID —
you'll need it for the Home Assistant sensor templates
([home-assistant-mqtt.md](home-assistant-mqtt.md)).

Then point the dashboard at it: set `MESHTASTIC_HOST=<node-ip>` (env var
or `dashboard/config.yaml`) and restart the container.

## Actual values for this build (Board 1)

- Node ID: `!xxxxxxxx`, name "Meshtastic e094", HW HELTEC_V3
- Serial ports: **COM7** = the Meshtastic API (use this for `--port`);
  COM6 is the ESP32-S3's other USB interface and just times out — ignore it.
- WiFi IP after setup: `192.168.1.50` (this is `MESHTASTIC_HOST`)
- MQTT JSON lands on `msh/US/2/json/LongFast/!xxxxxxxx`

## Gotchas seen in this build

- **After enabling MQTT, reboot the node** (`meshtastic --port COM7
  --reboot`). The MQTT client did not connect to the broker until a reboot
  kicked it — before that the broker saw zero publishes despite the config
  being correct. This cost real debugging time; just reboot.
- **Don't trust a host→own-LAN-IP TCP test.** `Test-NetConnection
  192.168.1.10 <port>` from the Docker host itself FAILS for *every*
  Docker Desktop service (HA 8123, Jellyfin 8096, Mosquitto 1883) even
  though phones/other LAN devices reach them fine — it's a WSL2/Docker
  Desktop hairpin artifact, not a real connectivity problem. To test the
  broker, subscribe from another device or from inside a container; don't
  chase firewall/binding ghosts based on the host-side probe.
- For the **node's** `mqtt.address`, use the host LAN IP `192.168.1.10`
  (the node is a real LAN device reaching the broker over the network).
  Only the dashboard *container* uses `host.docker.internal`. Never the
  Docker-internal `172.17.x.x` for either.
- A native Windows `mosquitto.exe` service once squatted port 1883 ahead
  of the Docker container — if MQTT looks connected but silent, check
  `netstat -abno | findstr 1883` on the Windows host.
- Only **one** TCP client can talk to the node at a time. The dashboard
  backend holds that connection; don't also point the CLI/phone-app-via-IP
  at it while the dashboard is running, or they'll fight. Configure the
  node over USB (`--port COM7`) while the dashboard is running, not
  `--host`.
