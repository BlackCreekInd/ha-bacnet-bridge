"""Expose selected Home Assistant entities as objects on HA's BACnet device.

Mapping:
  sensor (numeric)                        -> analog-input   (read only)
  binary_sensor                           -> binary-input   (read only)
  number, input_number                    -> analog-value   (writable)
  switch, input_boolean, light, fan       -> binary-value   (writable)

Object instance numbers are kept in HA storage so a BACnet front end keeps
pointing at the same entity across restarts and when entities are added or
removed.
"""

from __future__ import annotations

import logging
from typing import Any

from bacpypes3.local.analog import AnalogInputObject, AnalogValueObject
from bacpypes3.local.binary import BinaryInputObject, BinaryValueObject

from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from .bacnet_stack import BacnetStack
from .const import DOMAIN
from .units import ha_to_bacnet

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

_TYPE_FOR_DOMAIN = {
    "sensor": "analog-input",
    "binary_sensor": "binary-input",
    "number": "analog-value",
    "input_number": "analog-value",
    "switch": "binary-value",
    "input_boolean": "binary-value",
    "light": "binary-value",
    "fan": "binary-value",
}
_WRITABLE_TYPES = ("analog-value", "binary-value")
# Status flags are derived from reliability by BACpypes3: anything other than
# no-fault-detected sets the fault flag.
_OK = "noFaultDetected"
_UNAVAILABLE = "communicationFailure"
_BAD_VALUE = "unreliableOther"


def object_type_for(entity_id: str) -> str | None:
    """BACnet object type used for an entity, or None if not exposable."""
    return _TYPE_FOR_DOMAIN.get(entity_id.split(".", 1)[0])


class BacnetServer:
    """Keeps local BACnet objects in sync with HA entity states."""

    def __init__(
        self, hass: HomeAssistant, stack: BacnetStack, entry_id: str, entity_ids: list[str]
    ) -> None:
        self.hass = hass
        self.stack = stack
        self.entity_ids = entity_ids
        self._store: Store[dict[str, str]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.objects"
        )
        self._instances: dict[str, str] = {}  # entity_id -> "analog-input,3"
        self._objects: dict[str, Any] = {}  # entity_id -> local object
        self._syncing = False  # True while we push HA state into an object
        self._unsub = None

    async def async_start(self) -> None:
        """Create objects for the exposed entities and start tracking."""
        self._instances = await self._store.async_load() or {}
        changed = False
        for entity_id in self.entity_ids:
            obj_type = object_type_for(entity_id)
            if obj_type is None:
                _LOGGER.warning("Cannot expose %s over BACnet", entity_id)
                continue
            object_id = self._instances.get(entity_id)
            if object_id is None or not object_id.startswith(obj_type + ","):
                object_id = self._next_object_id(obj_type)
                self._instances[entity_id] = object_id
                changed = True
            state = self.hass.states.get(entity_id)
            obj = self._create_object(entity_id, object_id, state)
            self.stack._app.add_object(obj)
            self._objects[entity_id] = obj
            if obj_type in _WRITABLE_TYPES:
                obj._property_monitors["presentValue"].append(
                    self._make_write_handler(entity_id, obj_type)
                )
        if changed:
            await self._store.async_save(self._instances)
        if self._objects:
            self._unsub = async_track_state_change_event(
                self.hass, list(self._objects), self._async_state_changed
            )

    @callback
    def async_stop(self) -> None:
        """Stop tracking. The objects disappear when the stack closes."""
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _next_object_id(self, obj_type: str) -> str:
        used = {
            int(oid.split(",")[1])
            for oid in self._instances.values()
            if oid.startswith(obj_type + ",")
        }
        instance = 1
        while instance in used:
            instance += 1
        return f"{obj_type},{instance}"

    def _create_object(
        self, entity_id: str, object_id: str, state: State | None
    ) -> Any:
        obj_type, inst = object_id.split(",")
        name = entity_id  # entity ids are unique, friendly names are not
        description = (
            str(state.attributes.get(ATTR_FRIENDLY_NAME, entity_id)) if state else entity_id
        )
        common: dict[str, Any] = {
            "objectIdentifier": (obj_type, int(inst)),
            "objectName": name,
            "description": description[:255],
            "statusFlags": [0, 0, 0, 0],
            "reliability": _OK,
            "eventState": "normal",
            "outOfService": False,
        }
        if obj_type in ("analog-input", "analog-value"):
            unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) if state else None
            cls = AnalogInputObject if obj_type == "analog-input" else AnalogValueObject
            obj = cls(**common, presentValue=0.0, units=ha_to_bacnet(unit))
        else:
            if obj_type == "binary-input":
                obj = BinaryInputObject(**common, presentValue="inactive", polarity="normal")
            else:
                obj = BinaryValueObject(**common, presentValue="inactive")
        self._apply_state(obj, obj_type, state)
        return obj

    def _apply_state(self, obj: Any, obj_type: str, state: State | None) -> None:
        """Push an HA state into a local object (does not trigger write-back)."""
        self._syncing = True
        try:
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                obj.reliability = _UNAVAILABLE
                return
            if obj_type.startswith("analog"):
                try:
                    obj.presentValue = float(state.state)
                except ValueError:
                    obj.reliability = _BAD_VALUE
                    return
            else:
                obj.presentValue = "active" if state.state == STATE_ON else "inactive"
            obj.reliability = _OK
        finally:
            self._syncing = False

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        obj = self._objects.get(entity_id)
        if obj is None:
            return
        self._apply_state(obj, self._instances[entity_id].split(",")[0], event.data["new_state"])

    def _make_write_handler(self, entity_id: str, obj_type: str):
        @callback
        def _handle(old_value: Any, new_value: Any) -> None:
            if self._syncing:
                return
            _LOGGER.debug("BACnet write to %s: %s -> %s", entity_id, old_value, new_value)
            self.hass.async_create_task(
                self._async_apply_write(entity_id, obj_type, new_value)
            )

        return _handle

    async def _async_apply_write(self, entity_id: str, obj_type: str, value: Any) -> None:
        """Turn a BACnet write into an HA service call."""
        domain = entity_id.split(".", 1)[0]
        try:
            if obj_type == "analog-value":
                await self.hass.services.async_call(
                    domain,
                    "set_value",
                    {ATTR_ENTITY_ID: entity_id, "value": float(value)},
                    blocking=True,
                )
            else:
                service = "turn_on" if str(value) == "active" else "turn_off"
                # switch, input_boolean, light and fan all have turn_on/turn_off.
                await self.hass.services.async_call(
                    domain, service, {ATTR_ENTITY_ID: entity_id}, blocking=True
                )
        except Exception:  # noqa: BLE001 - any failure: restore HA's value
            _LOGGER.exception("Applying BACnet write to %s failed", entity_id)
            self._apply_state(
                self._objects[entity_id], obj_type, self.hass.states.get(entity_id)
            )
