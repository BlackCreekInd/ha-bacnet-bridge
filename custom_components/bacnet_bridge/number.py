"""Writable analog objects."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BacnetConfigEntry
from .entity import BacnetObjectEntity
from .platforms import objects_for_platform
from .units import bacnet_to_ha


async def async_setup_entry(
    hass: HomeAssistant, entry: BacnetConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(
        BacnetNumber(coordinator, obj)
        for coordinator, obj in objects_for_platform(entry.runtime_data.coordinators, Platform.NUMBER)
    )


class BacnetNumber(BacnetObjectEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    # BACnet objects can report min/max-pres-value; reading those is a later
    # improvement. Until then allow a wide range.
    _attr_native_min_value = -1_000_000
    _attr_native_max_value = 1_000_000
    _attr_native_step = 0.1

    def __init__(self, coordinator, obj) -> None:
        super().__init__(coordinator, obj)
        self._attr_native_unit_of_measurement = bacnet_to_ha(obj.get("units"))[0]

    @property
    def native_value(self) -> float | None:
        return self.raw_value

    async def async_set_native_value(self, value: float) -> None:
        await self._async_write(value)
