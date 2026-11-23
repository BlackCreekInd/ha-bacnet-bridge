"""Decide which HA platform each configured BACnet object belongs to."""

from __future__ import annotations

from typing import Any

from homeassistant.const import Platform

from .bacnet_stack import split_object_id
from .const import ANALOG_TYPES, BINARY_TYPES, MULTISTATE_TYPES

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


def platform_for(obj: dict[str, Any]) -> Platform | None:
    obj_type, _ = split_object_id(obj["object_id"])
    writable = bool(obj.get("writable"))
    if obj_type in ANALOG_TYPES:
        return Platform.NUMBER if writable else Platform.SENSOR
    if obj_type in BINARY_TYPES:
        return Platform.SWITCH if writable else Platform.BINARY_SENSOR
    if obj_type in MULTISTATE_TYPES:
        return Platform.SELECT if writable and obj.get("state_text") else Platform.SENSOR
    return None


def objects_for_platform(coordinators, platform: Platform):
    """Yield (coordinator, obj) pairs for a platform."""
    for coordinator in coordinators:
        for obj in coordinator.device["objects"]:
            if platform_for(obj) == platform:
                yield coordinator, obj
