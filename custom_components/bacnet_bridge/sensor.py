"""Read-only analog and multi-state objects."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BacnetConfigEntry
from .const import ANALOG_TYPES
from .entity import BacnetObjectEntity
from .platforms import objects_for_platform
from .units import bacnet_to_ha


async def async_setup_entry(
    hass: HomeAssistant, entry: BacnetConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(
        BacnetSensor(coordinator, obj)
        for coordinator, obj in objects_for_platform(entry.runtime_data.coordinators, Platform.SENSOR)
    )


class BacnetSensor(BacnetObjectEntity, SensorEntity):
    def __init__(self, coordinator, obj) -> None:
        super().__init__(coordinator, obj)
        if self.object_type in ANALOG_TYPES:
            unit, device_class = bacnet_to_ha(obj.get("units"))
            self._attr_native_unit_of_measurement = unit
            if device_class:
                self._attr_device_class = SensorDeviceClass(device_class)
            # Long-term statistics in the recorder.
            self._attr_state_class = (
                SensorStateClass.TOTAL_INCREASING
                if device_class == "energy"
                else SensorStateClass.MEASUREMENT
            )
        elif obj.get("state_text"):
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(obj["state_text"])

    @property
    def native_value(self):
        value = self.raw_value
        if value is None:
            return None
        if self.object_type in ANALOG_TYPES:
            return value
        states = self.obj.get("state_text") or []
        # Multi-state values are 1-based.
        if states and 1 <= value <= len(states):
            return states[value - 1]
        return None if states else value
