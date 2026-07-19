# Board 2: flashing RTNode-HeltecV4 (Reticulum standalone node)

Target: the second Heltec V3-clone board (ESP32-S3, SX1262, 8MB flash,
U.FL antenna) becomes a standalone Reticulum transport node — LoRa ↔ WiFi
bridge to the Reticulum backbone, no host computer needed.

## 0. Pre-flash checks (do these first, the ecosystem moves)

- Firmware: **RTNode-HeltecV4** — https://github.com/jrl290/RTNode-HeltecV4
  As of July 2026 it supports **both Heltec V3 and V4 with runtime board
  auto-detection** (V3: no PSRAM, 8MB flash, SX1262 — matches this board),
  which resolves the original "built for V4 only" pinout concern. Still:
  check the repo's releases/issues for anything newer, and skim open
  issues for V3-clone reports before flashing. Author labels it Beta,
  "developed with AI assistance, lightly tested."
- Fallbacks if it misbehaves on this clone:
  - https://github.com/attermann/microReticulum_Firmware (the upstream it
    forks)
  - https://github.com/GrayHatGuy/RTNode-2400 (a fork explicitly listing
    Heltec V3 / SX1262 boards)
- V3 has **no PSRAM** → the firmware falls back to internal SRAM
  (~170 KB pool) and caps the routing table (~24 paths). Fine for a home
  node; just know it's the constrained variant.
- **Antenna attached before any TX.** U.FL press-fit; SX1262 can be
  damaged transmitting into nothing.

## 1. Flash (esptool CLI — not the web flasher)

Lesson from Board 1: the browser flashers were unreliable on this
hardware/driver setup ("device not responding"); `esptool` worked first try
with no BOOT-button gymnastics. Same CP210x COM-port dance: check Device
Manager for the port.

Download the release binary (or build with PlatformIO per the repo), then
follow the repo's flash instructions. Pattern from Board 1, if the release
ships a merged factory image:

```
esptool --port COM5 chip-id                 # sanity check: ESP32-S3
esptool --port COM5 erase-flash             # clean slate (removes nothing you need — board is blank/expendable)
esptool --port COM5 write-flash 0x0 <factory-image>.bin
```

If the release ships separate bootloader/partition/app binaries instead,
use the exact offsets from the repo's docs — don't guess them.

## 2. Configure

1. Board boots into a WiFi **captive portal** (AP mode) at
   `http://192.168.4.1` — connect to its SSID from a phone/laptop.
2. Set: home WiFi SSID/password; LoRa params. Use **US915** frequencies
   consistent with your Reticulum community/backbone settings; note that
   Reticulum and Meshtastic are separate networks — do NOT copy
   Meshtastic's exact frequency/bandwidth to avoid the two boards jamming
   each other in the shared enclosure. Pick a different center frequency
   within US915.
3. OLED shows LoRa/WiFi/WAN/LAN state + IP once up.

## 3. Discover the real monitoring surface (feeds the dashboard)

The firmware documents **no status API** (OLED + serial console only, plus
Reticulum-protocol TCP). Before wiring the dashboard's Reticulum panel:

1. Watch the serial console (`meshtastic` not needed — any monitor:
   `python -m serial.tools.miniterm COM5 115200`) and note what status
   lines it prints and how often.
2. Check whether the web portal stays reachable on the LAN IP after
   setup, and whether it exposes any status page/JSON worth scraping.
3. Alternatively probe at the RNS level from a machine running the
   Reticulum Python stack (`rnstatus` against its TCP interface).

Whatever turns out to be real, implement it as a `ReticulumSource`
subclass in [dashboard/app/reticulum_source.py](../dashboard/app/reticulum_source.py)
(the interface is documented there) and swap it in `main.py` — the panel
UI already renders `state / detail / interfaces / uptime`.
