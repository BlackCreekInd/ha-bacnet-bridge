"""Polling coordinator, one per remote BACnet device."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .bacnet_stack import BacnetError, BacnetStack
from .const import DEFAULT_SCAN_INTERVAL, DEFAULT_WRITE_PRIORITY

_LOGGER = logging.getLogger(__name__)


class BacnetDeviceCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Reads present-value of the selected objects of one device.

    Polling first; subscribing to COV is a later improvement and would push
    updates into self.async_set_updated_data().
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        stack: BacnetStack,
        device: dict[str, Any],
        write_priority: int = DEFAULT_WRITE_PRIORITY,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"BACnet device {device['instance']}",
            update_interval=timedelta(
                seconds=device.get("scan_interval", DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.stack = stack
        self.device = device
        self.write_priority = write_priority
        self.objects: dict[str, dict[str, Any]] = {
            obj["object_id"]: obj for obj in device["objects"]
        }

    @property
    def address(self) -> str:
        return self.device["address"]

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            values = await self.stack.read_present_values(
                self.address, list(self.objects)
            )
        except BacnetError as err:
            raise UpdateFailed(str(err)) from err
        if self.objects and all(v is None for v in values.values()):
            raise UpdateFailed(f"No response from BACnet device at {self.address}")
        return values

    async def async_write(self, object_id: str, value: Any) -> None:
        """Write present-value and refresh."""
        obj = self.objects[object_id]
        priority = self.write_priority if obj.get("commandable") else None
        await self.stack.write_present_value(self.address, object_id, value, priority)
        await self.async_request_refresh()
