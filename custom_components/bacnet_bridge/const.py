"""Constants for the BACnet Bridge integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "bacnet_bridge"

# Config entry data (network settings of HA's own BACnet device).
CONF_INTERFACE: Final = "interface"  # "192.168.1.50/24"
CONF_PORT: Final = "port"
CONF_DEVICE_INSTANCE: Final = "device_instance"
CONF_DEVICE_NAME: Final = "device_name"
CONF_FOREIGN_BBMD: Final = "foreign_bbmd"  # "10.0.0.1" or "10.0.0.1:47808", empty = local subnet only
CONF_FOREIGN_TTL: Final = "foreign_ttl"

# Config entry options.
CONF_DEVICES: Final = "devices"  # {device_instance (str): DeviceConfig}
CONF_EXPOSED: Final = "exposed_entities"  # [entity_id, ...]
CONF_WRITE_PRIORITY: Final = "write_priority"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_PORT: Final = 47808
DEFAULT_DEVICE_NAME: Final = "Home Assistant"
DEFAULT_FOREIGN_TTL: Final = 300
DEFAULT_WRITE_PRIORITY: Final = 16
DEFAULT_SCAN_INTERVAL: Final = 30  # seconds
DEFAULT_DISCOVERY_TIMEOUT: Final = 5  # seconds

# BACnet vendor id 999 is reserved for testing. Register a real id with
# ASHRAE before shipping a product.
VENDOR_IDENTIFIER: Final = 999

# Object types this integration turns into HA entities.
ANALOG_TYPES: Final = ("analog-input", "analog-output", "analog-value")
BINARY_TYPES: Final = ("binary-input", "binary-output", "binary-value")
MULTISTATE_TYPES: Final = ("multi-state-input", "multi-state-output", "multi-state-value")
SUPPORTED_TYPES: Final = ANALOG_TYPES + BINARY_TYPES + MULTISTATE_TYPES
WRITABLE_CANDIDATE_TYPES: Final = (
    "analog-output",
    "analog-value",
    "binary-output",
    "binary-value",
    "multi-state-output",
    "multi-state-value",
)

TYPE_ABBREVIATIONS: Final = {
    "analog-input": "AI",
    "analog-output": "AO",
    "analog-value": "AV",
    "binary-input": "BI",
    "binary-output": "BO",
    "binary-value": "BV",
    "multi-state-input": "MSI",
    "multi-state-output": "MSO",
    "multi-state-value": "MSV",
}

# HA entity domains that can be exposed as BACnet objects (server side).
EXPOSABLE_DOMAINS: Final = [
    "sensor",
    "binary_sensor",
    "switch",
    "input_boolean",
    "light",
    "fan",
    "number",
    "input_number",
]
