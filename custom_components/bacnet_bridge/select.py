"""Writable multi-state objects."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
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
        BacnetSelect(coordinator, obj)
        for coordinator, obj in objects_for_platform(entry.runtime_data.coordinators, Platform.SELECT)
    )


class BacnetSelect(BacnetObjectEntity, SelectEntity):
    def __init__(self, coordinator, obj) -> None:
        super().__init__(coordinator, obj)
        self._attr_options = list(obj["state_text"])

    @property
    def current_option(self) -> str | None:
        value = self.raw_value
        if value is None or not 1 <= value <= len(self._attr_options):
            return None
        return self._attr_options[value - 1]

    async def async_select_option(self, option: str) -> None:
        await self._async_write(self._attr_options.index(option) + 1)
