"""Map BACnet engineering units to Home Assistant units and back."""

from __future__ import annotations

# BACnet EngineeringUnits name (bacpypes3 attribute style) -> (HA unit, HA sensor device class)
# Device classes are only set where the unit makes the meaning unambiguous.
BACNET_TO_HA: dict[str, tuple[str | None, str | None]] = {
    "degreesCelsius": ("°C", "temperature"),
    "degreesFahrenheit": ("°F", "temperature"),
    "kelvin": ("K", "temperature"),
    "deltaDegreesFahrenheit": ("°F", None),
    "deltaKelvin": ("K", None),
    "percent": ("%", None),
    "percentRelativeHumidity": ("%", "humidity"),
    "watts": ("W", "power"),
    "kilowatts": ("kW", "power"),
    "megawatts": ("MW", "power"),
    "wattHours": ("Wh", "energy"),
    "kilowattHours": ("kWh", "energy"),
    "megawattHours": ("MWh", "energy"),
    "pascals": ("Pa", "pressure"),
    "kilopascals": ("kPa", "pressure"),
    "hectopascals": ("hPa", "pressure"),
    "bars": ("bar", "pressure"),
    "millibars": ("mbar", "pressure"),
    "poundsForcePerSquareInch": ("psi", "pressure"),
    "inchesOfWater": ("inH₂O", None),
    "volts": ("V", "voltage"),
    "millivolts": ("mV", "voltage"),
    "kilovolts": ("kV", "voltage"),
    "amperes": ("A", "current"),
    "milliamperes": ("mA", "current"),
    "hertz": ("Hz", "frequency"),
    "litersPerSecond": ("L/s", "volume_flow_rate"),
    "litersPerMinute": ("L/min", "volume_flow_rate"),
    "litersPerHour": ("L/h", "volume_flow_rate"),
    "cubicMetersPerHour": ("m³/h", "volume_flow_rate"),
    "cubicMetersPerSecond": ("m³/s", "volume_flow_rate"),
    "cubicFeetPerMinute": ("ft³/min", "volume_flow_rate"),
    "liters": ("L", "volume"),
    "cubicMeters": ("m³", "volume"),
    "partsPerMillion": ("ppm", None),
    "luxes": ("lx", "illuminance"),
    "seconds": ("s", "duration"),
    "minutes": ("min", "duration"),
    "hours": ("h", "duration"),
    "metersPerSecond": ("m/s", "speed"),
    "revolutionsPerMinute": ("rpm", None),
    "noUnits": (None, None),
}

_HA_TO_BACNET: dict[str, str] = {}
for _bacnet, (_ha, _dc) in BACNET_TO_HA.items():
    if _ha is not None and _ha not in _HA_TO_BACNET:
        _HA_TO_BACNET[_ha] = _bacnet
# Prefer the humidity-neutral mapping for a bare "%".
_HA_TO_BACNET["%"] = "percent"


def bacnet_to_ha(units: str | None) -> tuple[str | None, str | None]:
    """Return (unit_of_measurement, device_class) for a BACnet units name."""
    if not units:
        return None, None
    return BACNET_TO_HA.get(units, (None, None))


def ha_to_bacnet(unit: str | None) -> str:
    """Return the BACnet units name for an HA unit, or noUnits."""
    if not unit:
        return "noUnits"
    return _HA_TO_BACNET.get(unit, "noUnits")
