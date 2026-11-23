"""Read-only binary objects."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BacnetConfigEntry
from .entity import BacnetObjectEntity
from .platforms import objects_for_platform


async def async_setup_entry(
    hass: HomeAssistant, entry: BacnetConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(
        BacnetBinarySensor(coordinator, obj)
        for coordinator, obj in objects_for_platform(entry.runtime_data.coordinators, Platform.BINARY_SENSOR)
    )


class BacnetBinarySensor(BacnetObjectEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool | None:
        return self.raw_value
