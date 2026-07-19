# Home Assistant sensors for the Meshtastic node

Prerequisite: the node is uplinking JSON to Mosquitto
([meshtastic-node-setup.md](meshtastic-node-setup.md)) and HA's existing
MQTT integration points at the same broker (it does).

These are **pre-filled with this build's real values** (verified on the
broker 2026-07-18):

- node ID `!xxxxxxxx` (Board 1, "Meshtastic e094") — the topic suffix
- channel `LongFast` (the default primary channel; uplink enabled)

If you ever reflash or add a second node, swap those two tokens.

HA is a plain `docker run` container here, so drop this into
`mqtt.yaml` (referenced from `configuration.yaml` via `mqtt: !include
mqtt.yaml`) inside the HA config volume, then restart HA or reload YAML.

```yaml
sensor:
  - name: "Meshtastic Battery"
    unique_id: meshtastic_battery
    state_topic: "msh/US/2/json/LongFast/!xxxxxxxx"
    unit_of_measurement: "%"
    device_class: battery
    state_class: measurement
    value_template: >-
      {% if value_json.payload.battery_level is defined %}
        {{ value_json.payload.battery_level }}
      {% else %}
        {{ states('sensor.meshtastic_battery') }}
      {% endif %}

  - name: "Meshtastic Voltage"
    unique_id: meshtastic_voltage
    state_topic: "msh/US/2/json/LongFast/!xxxxxxxx"
    unit_of_measurement: "V"
    device_class: voltage
    state_class: measurement
    value_template: >-
      {% if value_json.payload.voltage is defined %}
        {{ value_json.payload.voltage | round(2) }}
      {% else %}
        {{ states('sensor.meshtastic_voltage') }}
      {% endif %}

  - name: "Meshtastic Channel Utilization"
    unique_id: meshtastic_ch_util
    state_topic: "msh/US/2/json/LongFast/!xxxxxxxx"
    unit_of_measurement: "%"
    state_class: measurement
    value_template: >-
      {% if value_json.payload.channel_utilization is defined %}
        {{ value_json.payload.channel_utilization | round(1) }}
      {% else %}
        {{ states('sensor.meshtastic_ch_util') }}
      {% endif %}

  - name: "Meshtastic Air Util TX"
    unique_id: meshtastic_air_util_tx
    state_topic: "msh/US/2/json/LongFast/!xxxxxxxx"
    unit_of_measurement: "%"
    state_class: measurement
    value_template: >-
      {% if value_json.payload.air_util_tx is defined %}
        {{ value_json.payload.air_util_tx | round(1) }}
      {% else %}
        {{ states('sensor.meshtastic_air_util_tx') }}
      {% endif %}

  - name: "Meshtastic Last Message"
    unique_id: meshtastic_last_message
    state_topic: "msh/US/2/json/LongFast/!xxxxxxxx"
    value_template: >-
      {% if value_json.type == 'text' and value_json.payload.text is defined %}
        {{ value_json.payload.text | truncate(250) }}
      {% else %}
        {{ states('sensor.meshtastic_last_message') }}
      {% endif %}
```

Why the `else` branches: every message type (telemetry, nodeinfo, text,
position) arrives on the **same topic**, so each sensor keeps its previous
state when a non-matching type arrives instead of going `unknown`.

Optional availability: add a `Meshtastic Last Seen` timestamp sensor or an
HA automation alerting when Battery hasn't updated for >30 min — useful
given this stack's history of silent failures.

Note (from Meshtastic docs): JSON uplink is unsupported on nRF52-based
nodes — irrelevant here (ESP32-S3), just don't reuse this on nRF52 boards.
