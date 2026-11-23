"""Sidebar panel "HA-BACnet Bridge": status of devices, objects and published entities."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration

from .const import (
    CONF_DEVICE_INSTANCE,
    CONF_DEVICE_NAME,
    CONF_INTERFACE,
    CONF_PORT,
    DEFAULT_PORT,
    DOMAIN,
)

if TYPE_CHECKING:
    from . import BacnetConfigEntry

PANEL_URL_PATH = "bacnet-bridge"
PANEL_TITLE = "HA-BACnet Bridge"
PANEL_ICON = "mdi:lan-connect"
PANEL_ELEMENT = "bacnet-bridge-panel"
STATIC_URL = f"/{DOMAIN}_static"
_STATIC_DONE = f"{DOMAIN}_static_registered"


async def async_setup_panel_support(hass: HomeAssistant) -> None:
    """Once per HA run: serve the panel's JS and register the websocket command."""
    if hass.data.get(_STATIC_DONE):
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), False)]
    )
    websocket_api.async_register_command(hass, ws_status)
    hass.data[_STATIC_DONE] = True


async def async_register_panel(hass: HomeAssistant) -> None:
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        return
    version = (await async_get_integration(hass, DOMAIN)).version
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_ELEMENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=f"{STATIC_URL}/bacnet-bridge-panel.js?v={version}",
        require_admin=True,
    )


@callback
def async_remove_panel(hass: HomeAssistant) -> None:
    frontend.async_remove_panel(hass, PANEL_URL_PATH)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/status"})
@websocket_api.require_admin
@callback
def ws_status(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Everything the panel shows, in one message."""
    entries: list[BacnetConfigEntry] = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]
    if not entries:
        connection.send_result(msg["id"], {"loaded": False})
        return
    entry = entries[0]
    runtime = entry.runtime_data
    data = entry.data

    unique_to_entity = {
        reg.unique_id: reg.entity_id
        for reg in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }

    devices = []
    for coord in runtime.coordinators:
        dev = coord.device
        values = coord.data or {}
        objects = []
        for obj in dev["objects"]:
            obj_type, _, inst = obj["object_id"].partition(",")
            objects.append(
                {
                    "object_id": obj["object_id"],
                    "name": obj.get("name"),
                    "writable": bool(obj.get("writable")),
                    "value": _jsonable(values.get(obj["object_id"])),
                    "entity_id": unique_to_entity.get(f"{dev['instance']}-{obj_type}-{inst}"),
                }
            )
        devices.append(
            {
                "instance": dev["instance"],
                "name": dev.get("name") or f"BACnet device {dev['instance']}",
                "address": dev["address"],
                "vendor": dev.get("vendor_name"),
                "model": dev.get("model_name"),
                "scan_interval": coord.update_interval.total_seconds()
                if coord.update_interval
                else None,
                "online": coord.last_update_success and coord.data is not None,
                "error": str(coord.last_exception) if coord.last_exception else None,
                "objects": objects,
            }
        )

    connection.send_result(
        msg["id"],
        {
            "loaded": True,
            "entry_id": entry.entry_id,
            "local": {
                "name": data[CONF_DEVICE_NAME],
                "instance": data[CONF_DEVICE_INSTANCE],
                "interface": data[CONF_INTERFACE],
                "port": data.get(CONF_PORT, DEFAULT_PORT),
                "model": runtime.stack.model_name,
                "location": runtime.stack.location,
            },
            "devices": devices,
            "published": [
                {"entity_id": eid, "object_id": oid}
                for eid, oid in runtime.server.published_objects().items()
            ],
        },
    )


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)
