# BACnet Bridge for Home Assistant

A custom integration (HACS-style) that makes Home Assistant a BACnet/IP device. It works in two directions over one BACnet stack:

* **Client:** discover BACnet devices on the network, pick objects in the UI, and get them as HA entities. The recorder keeps their history like any other sensor.
* **Server:** publish chosen HA entities as BACnet objects, so a BMS front end can read them and write to the writable ones.

Built on [BACpypes3](https://github.com/JoelBender/BACpypes3) (MIT). Status: first working base, version 0.1.0.

## What it does today

| Area | Behaviour |
| --- | --- |
| Setup | Config flow asks for network interface (address/prefix), UDP port (47808), device instance and name. Optional BBMD address and TTL for foreign device registration. Network settings can be changed later with **Reconfigure**. |
| Discovery | Options → *Discover and add a BACnet device*. Broadcast Who-Is on the local subnet, or a directed Who-Is to one IP, with optional instance range. |
| Objects | Reads the device's object list (falls back to element-by-element for large lists), then names, units, state texts and current values, so you can choose objects. |
| Client entities | analog → `sensor` (or `number` when marked writable); binary → `binary_sensor` (or `switch`); multi-state → `sensor` with state texts (or `select`). Units map to HA units and device classes; analog sensors have `state_class: measurement` for long-term statistics. |
| Polling | ReadPropertyMultiple in small chunks, falls back to ReadProperty for devices without RPM. Poll interval per device. An offline device makes its entities unavailable without blocking HA startup. |
| Writes | Writes present-value. Commandable objects (those with a priority array) are written at the configured priority (default 16). |
| Server | Options → *Publish Home Assistant entities*. sensor → analog-input, binary_sensor → binary-input, number/input_number → analog-value (writable), switch/input_boolean/light/fan → binary-value (writable). Object names are entity ids, descriptions are friendly names. Instance numbers persist across restarts. Unavailable entities set the fault flag. BACnet writes become HA service calls. |

## Layout

```
custom_components/bacnet_bridge/
  manifest.json        integration metadata, requires bacpypes3
  __init__.py          setup/unload: starts the stack, server, coordinators
  bacnet_stack.py      the only module that talks to BACpypes3 (discover, read, write, close)
  server.py            HA entities -> local BACnet objects, write-back to HA
  coordinator.py       one polling coordinator per remote device
  config_flow.py       network setup, reconfigure, options menu (devices, publishing, settings)
  entity.py            base entity for remote objects
  platforms.py         object type + writable -> HA platform
  sensor.py binary_sensor.py number.py switch.py select.py
  units.py             BACnet engineering units <-> HA units
  strings.json, translations/en.json
tests/                 end-to-end tests over real UDP on loopback with a simulated device
hacs.json
```

## Install (manual, for testing)

1. Copy `custom_components/bacnet_bridge` into your HA config folder under `custom_components/`.
2. Restart Home Assistant.
3. Settings → Devices & services → Add integration → **BACnet Bridge**.
4. Choose the interface on the BACnet subnet (for example `192.168.1.50/24`) and a device instance not used elsewhere on your network.
5. Open the integration's **Configure** to add devices or publish entities.

Notes:

* HA must own UDP 47808 on that interface. If another BACnet integration or add-on uses it, setup reports the port as busy.
* In Docker, HA needs host networking for broadcasts to work.
* Vendor id 999 is the test id. A product shipped widely should register a vendor id with ASHRAE.

## Development

```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest
```

The tests start a simulated BACnet device on `127.0.0.1:47809` and HA's stack on `127.0.0.1:47808`, then run the real config/options flows: discovery, polling, writes from HA, offline handling, and publishing HA entities (read values, state updates, fault flag, BACnet writes into HA).

## Roadmap ideas

* COV subscriptions instead of polling where devices support it.
* Read min/max-pres-value for number ranges; relinquish (write null) support.
* Foreign device registration is wired into setup; next is testing against a real BBMD and showing registration status.
* Per-object options after adding a device (rename, add/remove objects without re-discovering).
* Publish more entity types (climate setpoints, multi-state for select/input_select), and choose by HA label as well as by list.
* Diagnostics download and a `bacnet_bridge.write_property` service for advanced writes.
* Trend log objects and schedules, if needed.
# BACnet-Bridge
# ha-bacnet-bridge
# ha-bacnet-bridge
