"""End-to-end tests against the simulated device over real UDP on loopback."""

from __future__ import annotations

import asyncio

import pytest

from bacpypes3.pdu import Address
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.bacnet_bridge.const import (
    CONF_DEVICE_INSTANCE,
    CONF_DEVICE_NAME,
    CONF_DEVICES,
    CONF_EXPOSED,
    CONF_FOREIGN_BBMD,
    CONF_FOREIGN_TTL,
    CONF_INTERFACE,
    CONF_PORT,
    DOMAIN,
)

from .conftest import HA_INTERFACE, HA_PORT, SIM_ADDRESS, SIM_INSTANCE, make_app

NETWORK = {
    CONF_INTERFACE: HA_INTERFACE,
    CONF_PORT: HA_PORT,
    CONF_DEVICE_INSTANCE: 47808,
    CONF_DEVICE_NAME: "Home Assistant",
    CONF_FOREIGN_BBMD: "",
    CONF_FOREIGN_TTL: 300,
}


async def _unload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Unload and let the UDP socket actually close before the next test."""
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    await asyncio.sleep(0.05)


async def _setup_entry(hass: HomeAssistant, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=NETWORK,
        options=options or {CONF_DEVICES: {}, CONF_EXPOSED: []},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_user_flow(hass: HomeAssistant, mock_network_adapters) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM

    bad = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**NETWORK, CONF_INTERFACE: "127.0.0.1", CONF_FOREIGN_BBMD: "nope"}
    )
    assert bad["errors"] == {CONF_INTERFACE: "invalid_interface", CONF_FOREIGN_BBMD: "invalid_bbmd"}

    done = await hass.config_entries.flow.async_configure(result["flow_id"], NETWORK)
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert done["data"][CONF_INTERFACE] == HA_INTERFACE
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.state is ConfigEntryState.LOADED
    await _unload(hass, entry)


async def test_discover_add_poll_and_write(hass: HomeAssistant, sim_device) -> None:
    entry = await _setup_entry(hass)

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.MENU
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "add_device"})
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"target": SIM_ADDRESS, "timeout": 2}
    )
    assert flow["step_id"] == "pick_device", flow
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"device": str(SIM_INSTANCE)})
    assert flow["step_id"] == "pick_objects", flow
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "objects": ["analog-value,1", "analog-value,2", "binary-value,1", "multi-state-value,1"],
            "writable": ["analog-value,2", "binary-value,1"],
            "scan_interval": 30,
        },
    )
    assert flow["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    device = entry.options[CONF_DEVICES][str(SIM_INSTANCE)]
    assert device["name"] == "Simulated AHU"
    by_id = {o["object_id"]: o for o in device["objects"]}
    assert by_id["analog-value,2"]["commandable"] is True
    assert by_id["analog-value,1"]["commandable"] is False
    assert by_id["multi-state-value,1"]["state_text"] == ["Off", "Heat", "Cool"]

    temp = hass.states.get("sensor.simulated_ahu_supply_temp")
    assert temp is not None and float(temp.state) == 21.5
    assert temp.attributes["unit_of_measurement"] == "°C"
    assert temp.attributes["state_class"] == "measurement"
    assert hass.states.get("sensor.simulated_ahu_mode").state == "Heat"
    assert hass.states.get("switch.simulated_ahu_fan_command").state == "off"
    assert float(hass.states.get("number.simulated_ahu_setpoint").state) == 20.0

    # Writes from HA reach the device.
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.simulated_ahu_fan_command"}, blocking=True
    )
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.simulated_ahu_setpoint", "value": 23.5}, blocking=True
    )
    await hass.async_block_till_done()
    objs = sim_device.objects
    assert str(objs["fan"].presentValue) == "active"
    assert float(objs["setpoint"].presentValue) == 23.5
    # Commandable object: the write landed at the configured priority (16).
    assert float(objs["setpoint"].priorityArray[15].real) == 23.5

    # Device-side change shows up after a poll.
    objs["temp"].presentValue = 18.25
    coordinator = entry.runtime_data.coordinators[0]
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.simulated_ahu_supply_temp").state) == 18.25

    # Device goes offline -> entities unavailable, no crash.
    sim_device.app.close()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.simulated_ahu_supply_temp").state == "unavailable"

    await _unload(hass, entry)


async def _add_sim_device(hass: HomeAssistant, entry: MockConfigEntry, objects, writable) -> None:
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "add_device"})
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"target": SIM_ADDRESS, "timeout": 2}
    )
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"device": str(SIM_INSTANCE)})
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"objects": objects, "writable": writable, "scan_interval": 30}
    )
    assert flow["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_edit_device_objects(hass: HomeAssistant, sim_device) -> None:
    entry = await _setup_entry(hass)
    await _add_sim_device(
        hass, entry, ["analog-value,1", "analog-value,2", "multi-state-value,1"], ["analog-value,2"]
    )
    registry = er.async_get(hass)
    uid = lambda oid: f"{SIM_INSTANCE}-{oid.replace(',', '-')}"  # noqa: E731
    assert registry.async_get_entity_id("number", DOMAIN, uid("analog-value,2"))
    assert registry.async_get_entity_id("sensor", DOMAIN, uid("multi-state-value,1"))

    # One device configured: the menu goes straight to its options.
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "edit_device"})
    assert flow["step_id"] == "device_options", flow

    bad = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"objects": [], "scan_interval": 30}
    )
    assert bad["errors"] == {"objects": "no_objects_kept"}

    # Drop MSV 1, make AV 2 read-only, then rename and look for more objects.
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "name": "AHU 1",
            "objects": ["analog-value,1", "analog-value,2"],
            "writable": [],
            "scan_interval": 60,
            "rename": True,
            "find_more": True,
        },
    )
    assert flow["step_id"] == "rename_objects", flow
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"AV 1": "Supply air"})
    assert flow["step_id"] == "add_objects", flow
    assert flow["description_placeholders"]["count"] == "2"  # BV 1 and the dropped MSV 1
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"objects": ["binary-value,1"], "writable": ["binary-value,1"]}
    )
    assert flow["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    device = entry.options[CONF_DEVICES][str(SIM_INSTANCE)]
    assert device["name"] == "AHU 1"
    assert device["scan_interval"] == 60
    by_id = {o["object_id"]: o for o in device["objects"]}
    assert set(by_id) == {"analog-value,1", "analog-value,2", "binary-value,1"}
    assert by_id["analog-value,1"]["name"] == "Supply air"
    assert by_id["analog-value,2"]["writable"] is False
    assert by_id["binary-value,1"]["writable"] is True

    # Removed and re-platformed entities are gone from the registry.
    assert registry.async_get_entity_id("sensor", DOMAIN, uid("multi-state-value,1")) is None
    assert registry.async_get_entity_id("number", DOMAIN, uid("analog-value,2")) is None
    assert registry.async_get_entity_id("sensor", DOMAIN, uid("analog-value,2"))
    assert registry.async_get_entity_id("switch", DOMAIN, uid("binary-value,1"))
    supply = registry.async_get_entity_id("sensor", DOMAIN, uid("analog-value,1"))
    assert hass.states.get(supply).attributes["friendly_name"].endswith("Supply air")

    await _unload(hass, entry)


async def test_expose_entities_as_bacnet_objects(hass: HomeAssistant) -> None:
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"pump": {"name": "Pump"}}}
    )
    hass.states.async_set("sensor.room_temp", "22.4", {"unit_of_measurement": "°C", "friendly_name": "Room"})
    entry = await _setup_entry(
        hass, {CONF_DEVICES: {}, CONF_EXPOSED: ["sensor.room_temp", "input_boolean.pump"]}
    )

    client = make_app("127.0.0.1/8:47810", 3003, "Test client")
    try:
        await asyncio.sleep(0.1)
        ha = Address(f"127.0.0.1:{HA_PORT}")
        objects = await client.read_property(ha, "device,47808", "object-list")
        ids = {f"{o[0]},{o[1]}" for o in objects}
        assert {"analog-input,1", "binary-value,1"} <= ids

        assert float(await client.read_property(ha, "analog-input,1", "present-value")) == pytest.approx(22.4, abs=1e-4)
        assert str(await client.read_property(ha, "analog-input,1", "units")) == "degrees-celsius"
        assert str(await client.read_property(ha, "analog-input,1", "object-name")) == "sensor.room_temp"

        # HA state change -> BACnet value.
        hass.states.async_set("sensor.room_temp", "23.0", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert float(await client.read_property(ha, "analog-input,1", "present-value")) == 23.0

        # Unavailable -> fault flag.
        hass.states.async_set("sensor.room_temp", "unavailable")
        await hass.async_block_till_done()
        flags = await client.read_property(ha, "analog-input,1", "status-flags")
        assert list(flags)[1] == 1

        # BACnet write -> HA service call.
        await client.write_property(ha, "binary-value,1", "present-value", "active")
        await hass.async_block_till_done()
        assert hass.states.get("input_boolean.pump").state == "on"

        # HA change -> BACnet, without echoing back a write.
        await hass.services.async_call("input_boolean", "turn_off", {"entity_id": "input_boolean.pump"}, blocking=True)
        await hass.async_block_till_done()
        assert str(await client.read_property(ha, "binary-value,1", "present-value")) == "inactive"
        assert hass.states.get("input_boolean.pump").state == "off"
    finally:
        client.close()

    # Instance numbers persist across a reload.
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    server = entry.runtime_data.server
    assert server._instances == {"sensor.room_temp": "analog-input,1", "input_boolean.pump": "binary-value,1"}
    await _unload(hass, entry)


async def test_offline_device_does_not_block_setup_and_can_be_removed(hass: HomeAssistant) -> None:
    device = {
        "instance": 1234,
        "address": "127.0.0.1:47811",  # nobody listens here
        "name": "Gone",
        "vendor_name": "",
        "model_name": "",
        "scan_interval": 30,
        "objects": [
            {"object_id": "analog-input,1", "name": "Temp", "units": "degreesCelsius",
             "state_text": [], "commandable": False, "writable": False}
        ],
    }
    entry = MockConfigEntry(domain=DOMAIN, data=NETWORK, options={CONF_DEVICES: {"1234": device}, CONF_EXPOSED: []})
    entry.add_to_hass(hass)
    loop = asyncio.get_running_loop()
    start = loop.time()
    ok = await hass.config_entries.async_setup(entry.entry_id)
    assert ok, (entry.state, entry.reason)
    assert loop.time() - start < 2  # setup did not wait for the dead device
    assert hass.states.get("sensor.gone_temp").state == "unavailable"

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "remove_devices"})
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"devices": ["1234"]})
    assert flow["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_DEVICES] == {}
    assert hass.states.get("sensor.gone_temp") is None or hass.states.get("sensor.gone_temp").state == "unavailable"
    await _unload(hass, entry)
