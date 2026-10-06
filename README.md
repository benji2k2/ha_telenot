<h1 align="center">Telenot — Home Assistant Integration</h1>

<p align="center">
  Local push integration for the <b>Telenot complex 400</b> alarm panel over its
  <b>GMS serial interface</b> — arm, disarm, readiness, detection areas and detectors
  with their names read from the panel. No MQTT, no extra container.
</p>

<p align="center">
  <a href="README.de.md">Deutsch</a>
</p>

> **Status: in development.** The protocol core is done and tested against telegrams
> captured on a real panel; connection handling, entities and the setup flow are being
> built. Not ready for use yet.

---

## Why

The usual way to bring a complex 400 into Home Assistant is a separate bridge service that
translates the panel's GMS protocol to MQTT. That works, but adds a container, a broker hop
and hand-maintained sensor lists — and the bridge this was written to replace silently
stopped processing data after every reconnect to the serial converter, leaving Home
Assistant with a stale alarm state for hours.

This integration talks to the panel directly, reconnects without limit, marks everything
**unavailable** when the panel is unreachable and reads the detectors' **names and detection
areas from the panel** instead of making you find addresses by trial and error.

## What you get (planned for 0.1)

| Device | Entities |
|---|---|
| **Telenot complex 400** | Alarm panel (disarmed / armed home / armed away / armed night*), ready for home, ready for away, alarm, fault, battery fault, mains fault, button *reset alarm*, connection (diagnostic) |
| **One device per detection area**, named as in the panel, linked to the panel | *State* of the area, every detector of the area (disabled by default), *bypassed* (diagnostic) |

\* *Armed night* is a virtual mode: the panel is armed home, Home Assistant shows night.

Commands are sent only in the panel's send window and confirmed by the panel; the alarm
state always comes from the panel, never from the command that was sent. Area 1 only.

## Hardware

```
complex 400 ──RS232 (GMS)──▶ serial-to-Ethernet converter ──LAN──▶ Home Assistant
```

| Part | Notes |
|---|---|
| **Telenot complex 400 / 400H** | The **GMS protocol must be enabled on the serial interface**. This is done by the installer in *compasX*; it is off by default. |
| **Serial-to-Ethernet converter** | Any transparent RS232-to-TCP server works, e.g. **USR-TCP232** series. Mode *TCP server*, **9600 baud, 8N1**, no protocol of its own. Note host and port. |
| **Serial cable** | **Straight-through (1:1)**, not null-modem — TX/RX are not crossed. Only TXD, RXD and GND are used. |
| Network | Home Assistant must reach the converter's TCP port. |

Things to know:

- **One client at a time.** The converter serves a single TCP client. Stop any other bridge
  (e.g. a telenot-bridge container) before setting up this integration.
- **Place the converter outside the panel housing** and keep the serial line short. GMS
  commands are **not authenticated on the wire**: whoever can reach the cable or the
  converter's TCP port can disarm the panel. Restrict access to the converter in your
  network.
- While no client acknowledges the panel's telegrams (Home Assistant restarting, converter
  offline), the panel may register a transmission-path fault. That is a fault message, not
  an alarm.
- Opening the panel housing triggers its tamper contact — work on the wiring only with the
  panel disarmed and, if needed, in installer mode.

## Requirements

- Home Assistant **2026.8** or newer (planned minimum).
- No Python dependencies; the integration uses asyncio TCP only.

## Installation

Not yet released. Once it is: HACS → custom repository `benji2k2/ha_telenot` (category
*Integration*) → install → restart Home Assistant.

## Setup (planned)

1. *Settings → Devices & services → Add integration → Telenot*.
2. Enter host and port of the converter. The integration checks that the panel is talking.
3. **Scan**: the integration asks the panel which addresses are occupied and reads their
   names and detection areas (about 6 seconds per address, ~9 minutes for 80 addresses).
   Only read-only queries are sent during the scan.
4. Confirm. Devices and entities are created; assign rooms to the detection-area devices.

Options: alarm code, which modes need the code, virtual night mode, new scan.

## Coming from telenot-bridge (MQTT)

1. Stop the bridge container.
2. Remove the bridge's retained MQTT discovery topics (`homeassistant/binary_sensor/telenot/…`)
   and your MQTT alarm panel.
3. Set up this integration, then rename the new entities to your old entity IDs — automations,
   dashboards, HomeKit and history keep working.

## Development

```bash
python -m pytest -q
```

The protocol core (`protocol.py`, `state.py`) has no Home Assistant dependency and is tested
against reference telegrams; the connection is tested against a simulated panel over TCP.

## Credits

The GMS protocol knowledge builds on [carhensi/telenot-bridge](https://github.com/carhensi/telenot-bridge)
and [carhensi/telenot-esp-bridge](https://github.com/carhensi/telenot-esp-bridge) (both Apache-2.0),
see [NOTICE](NOTICE). Not affiliated with or endorsed by TELENOT ELECTRONIC GmbH; *Telenot*,
*complex* and *compasX* are their trademarks.

## License

[Apache-2.0](LICENSE)
