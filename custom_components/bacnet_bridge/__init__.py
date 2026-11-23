"""BACnet Bridge: BACnet/IP client and server for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .bacnet_stack import BacnetStack
from .const import (
    CONF_DESCRIPTION,
    CONF_DEVICE_INSTANCE,
    CONF_DEVICE_NAME,
    CONF_DEVICES,
    CONF_EXPOSED,
    CONF_FOREIGN_BBMD,
    CONF_FOREIGN_TTL,
    CONF_INTERFACE,
    CONF_LOCATION,
    CONF_MODEL_NAME,
    CONF_PORT,
    CONF_WRITE_PRIORITY,
    DEFAULT_FOREIGN_TTL,
    DEFAULT_MODEL_NAME,
    DEFAULT_PORT,
    DEFAULT_WRITE_PRIORITY,
    DOMAIN,
)
from .coordinator import BacnetDeviceCoordinator
from .panel import async_register_panel, async_remove_panel, async_setup_panel_support
from .platforms import PLATFORMS
from .server import BacnetServer

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the sidebar panel's files and its websocket API."""
    await async_setup_panel_support(hass)
    return True


@dataclass
class BacnetRuntimeData:
    stack: BacnetStack
    server: BacnetServer
    coordinators: list[BacnetDeviceCoordinator] = field(default_factory=list)


type BacnetConfigEntry = ConfigEntry[BacnetRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: BacnetConfigEntry) -> bool:
    """Start HA's BACnet device, poll remote devices, expose entities."""
    data = entry.data
    version = str((await async_get_integration(hass, DOMAIN)).version or "")
    stack = BacnetStack(
        interface=data[CONF_INTERFACE],
        port=data.get(CONF_PORT, DEFAULT_PORT),
        device_instance=data[CONF_DEVICE_INSTANCE],
        device_name=data[CONF_DEVICE_NAME],
        foreign_bbmd=data.get(CONF_FOREIGN_BBMD) or None,
        foreign_ttl=data.get(CONF_FOREIGN_TTL, DEFAULT_FOREIGN_TTL),
        model_name=entry.options.get(CONF_MODEL_NAME) or DEFAULT_MODEL_NAME,
        location=entry.options.get(CONF_LOCATION, ""),
        description=entry.options.get(CONF_DESCRIPTION, ""),
        software_version=version,
    )
    try:
        await stack.start()
    except OSError as err:
        raise ConfigEntryNotReady(
            f"Cannot open BACnet/IP on {data[CONF_INTERFACE]}:{stack.port}: {err}"
        ) from err

    server = BacnetServer(hass, stack, entry.entry_id, entry.options.get(CONF_EXPOSED, []))
    try:
        await server.async_start()
    except Exception:
        stack.close()
        raise

    # HA's own BACnet device, parent of the remote devices.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "local")},
        name=f"{data[CONF_DEVICE_NAME]} (BACnet device {data[CONF_DEVICE_INSTANCE]})",
        manufacturer="Home Assistant",
        model=entry.options.get(CONF_MODEL_NAME) or DEFAULT_MODEL_NAME,
        sw_version=version or None,
    )

    priority = entry.options.get(CONF_WRITE_PRIORITY, DEFAULT_WRITE_PRIORITY)
    coordinators = [
        BacnetDeviceCoordinator(hass, entry, stack, device, priority)
        for device in entry.options.get(CONF_DEVICES, {}).values()
    ]
    _async_remove_stale_devices(hass, entry)
    entry.runtime_data = BacnetRuntimeData(stack, server, coordinators)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # First poll in the background: an offline device must not hold up HA's
    # startup (a request without answer takes ~12 s). Its entities stay
    # unavailable until a poll succeeds.
    for coordinator in coordinators:
        entry.async_create_task(
            hass, coordinator.async_refresh(), f"{DOMAIN} first poll {coordinator.name}"
        )
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await async_register_panel(hass)
    return True


def _async_remove_stale_devices(hass: HomeAssistant, entry: BacnetConfigEntry) -> None:
    """Drop registry devices for BACnet devices removed in the options."""
    registry = dr.async_get(hass)
    keep = {"local"} | {f"device-{k}" for k in entry.options.get(CONF_DEVICES, {})}
    for device_entry in dr.async_entries_for_config_entry(registry, entry.entry_id):
        idents = {i for d, i in device_entry.identifiers if d == DOMAIN}
        if idents and not idents & keep:
            registry.async_update_device(
                device_entry.id, remove_config_entry_id=entry.entry_id
            )


async def _async_update_listener(hass: HomeAssistant, entry: BacnetConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: BacnetConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        async_remove_panel(hass)
        entry.runtime_data.server.async_stop()
        entry.runtime_data.stack.close()
    return unloaded


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: BacnetConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting a remote device from the UI once it is no longer configured."""
    configured = {f"device-{k}" for k in entry.options.get(CONF_DEVICES, {})}
    return not any(
        domain == DOMAIN and (ident == "local" or ident in configured)
        for domain, ident in device_entry.identifiers
    )
