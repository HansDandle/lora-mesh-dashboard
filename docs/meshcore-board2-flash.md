# Board 2: MeshCore WiFi node + dashboard integration

Board 2 (second Heltec V3 clone, ESP32-S3, MAC `AA:BB:CC:11:22:02`) runs
**MeshCore** to join the Austin community's messaging network, and is
wired into the dashboard over the LAN. Board 1 stays Meshtastic. Done
2026-07-18.

## Current state

- **Firmware:** MeshCore companion **WiFi** build v1.16.0, **compiled from
  source** (the prebuilt release binaries are BLE-only or USB-only; WiFi
  requires a self-compile with credentials baked in — see below).
- **On the LAN:** joins WiFi (YOUR_WIFI_SSID) as `192.168.1.51`, companion API
  on **TCP port 5000**. Device name **"MeshNode"**, pubkey `<node-public-key>`.
- **Dashboard:** the MeshCore panel connects over the LAN via the
  `meshcore` Python lib (`MESHCORE_HOST=192.168.1.51`, port 5000) — status,
  self/battery, contacts, message log, and send. Board 2 can now live
  anywhere on WiFi (window/attic); no Bluetooth-proximity limit.
- **Radio settings (defaults, MUST be matched to Austin):** freq
  **910.525 MHz**, BW **62.5**, SF **7**, CR **5**, TX 22 dBm. Contacts
  stay empty until these match whatever the Austin MeshCore community runs
  — confirm via their Discord and set them (MeshCore app, or `meshcli set`).

## Why we compiled (prebuilt WiFi doesn't exist)

MeshCore's FAQ: WiFi/TCP companion is not a downloadable binary — you set
`WIFI_SSID`/`WIFI_PWD` at compile time. The prebuilt assets are
`companion_radio_ble` (BLE only) and `companion_radio_usb` (USB only).

## Build + flash (PlatformIO)

```
# clone
git clone --depth 1 https://github.com/meshcore-dev/MeshCore.git
# edit variants/heltec_v3/platformio.ini, env [Heltec_v3_companion_radio_wifi]:
#   -D WIFI_SSID='"YOUR_WIFI_SSID"'
#   -D WIFI_PWD='"YOUR_WIFI_PASSWORD"'     # a '#' in the pwd survives PIO's INI parser
pio run -e Heltec_v3_companion_radio_wifi                       # compile (~5 min first time)
pio run -e Heltec_v3_companion_radio_wifi -t upload --upload-port COM7   # flash (handles offsets)
```

`pio ... -t upload` writes bootloader/partitions/firmware at the correct
offsets — don't hand-flash the separate bins. Verify with `Hash of data
verified.` The OLED shows the node's IP:port once it joins WiFi.

## ⚠️ Identify the board before flashing

Board 2 is the **CP210x** port (COM7 here). This machine also has an
**ESP8266** Tasmota device on a CH340 port — flashing that would brick it.
Always `esptool --port <COMx> chip-id` first (must say ESP32-S3).

## Verify it's on the LAN

```
# find its IP (WiFi MAC AA:BB:CC:11:22:02)
arp -a | findstr 9c-13-9e-a1-42-4c
# confirm companion port + protocol
meshcli -t 192.168.1.51 -p 5000 infos
```

## Reconfiguring WiFi later

Because creds are compiled in, changing WiFi = recompile with new
`WIFI_SSID`/`WIFI_PWD` and reflash. (Board 1/Meshtastic sets WiFi at
runtime; MeshCore's WiFi build does not.)

## Trade-off vs the phone

This WiFi build has no BLE, so the phone can't pair over Bluetooth — but
the MeshCore app can connect over **TCP/WiFi** to `192.168.1.51:5000`
instead, and MeshCore allows multiple companion connections, so the phone
and the dashboard can both use it at once.
