"""Base entity for objects on remote BACnet devices."""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .bacnet_stack import BacnetError, split_object_id
from .const import DOMAIN, TYPE_ABBREVIATIONS
from .coordinator import BacnetDeviceCoordinator


class BacnetObjectEntity(CoordinatorEntity[BacnetDeviceCoordinator]):
    """One BACnet object (present-value) as an HA entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BacnetDeviceCoordinator, obj: dict[str, Any]) -> None:
        super().__init__(coordinator)
        device = coordinator.device
        self.object_id = obj["object_id"]
        self.object_type, self.object_instance = split_object_id(self.object_id)
        self.obj = obj
        self._attr_unique_id = f"{device['instance']}-{self.object_type}-{self.object_instance}"
        self._attr_name = obj["name"]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"device-{device['instance']}")},
            name=device.get("name") or f"BACnet device {device['instance']}",
            manufacturer=device.get("vendor_name") or None,
            model=device.get("model_name") or None,
            via_device=(DOMAIN, "local"),
        )
        self._attr_extra_state_attributes = {
            "bacnet_object": f"{TYPE_ABBREVIATIONS.get(self.object_type, self.object_type)} {self.object_instance}",
            "bacnet_device": device["instance"],
        }

    @property
    def raw_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self.object_id)

    @property
    def available(self) -> bool:
        return super().available and self.raw_value is not None

    async def _async_write(self, value: Any) -> None:
        try:
            await self.coordinator.async_write(self.object_id, value)
        except BacnetError as err:
            raise HomeAssistantError(str(err)) from err
