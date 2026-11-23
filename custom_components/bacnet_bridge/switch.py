"""Writable binary objects."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
        BacnetSwitch(coordinator, obj)
        for coordinator, obj in objects_for_platform(entry.runtime_data.coordinators, Platform.SWITCH)
    )


class BacnetSwitch(BacnetObjectEntity, SwitchEntity):
    @property
    def is_on(self) -> bool | None:
        return self.raw_value

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(False)
