"""Fixtures: a simulated BACnet device on 127.0.0.1:47809."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from bacpypes3.app import Application
from bacpypes3.local.analog import AnalogValueObject, AnalogValueObjectCmd
from bacpypes3.local.binary import BinaryValueObject
from bacpypes3.local.multistate import MultiStateValueObject

SIM_ADDRESS = "127.0.0.1:47809"
SIM_INSTANCE = 1001
HA_INTERFACE = "127.0.0.1/8"
HA_PORT = 47808  # HA's stack on the loopback default port


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations, socket_enabled):
    """Custom integrations on; real UDP sockets allowed (loopback only)."""
    yield


@pytest.fixture
def mock_network_adapters():
    """No real adapters in tests; the user types the interface."""
    from unittest.mock import patch

    with patch(
        "homeassistant.components.network.async_get_adapters", return_value=[]
    ):
        yield


def make_app(address: str, instance: int, name: str) -> Application:
    return Application.from_args(
        SimpleNamespace(
            address=address,
            foreign=None,
            ttl=30,
            bbmd=None,
            network=0,
            instance=instance,
            name=name,
            vendoridentifier=999,
        )
    )


@pytest.fixture
async def sim_device():
    """A BACnet device with a few objects to discover and poll."""
    app = make_app("127.0.0.1/8:47809", SIM_INSTANCE, "Simulated AHU")
    common = {"statusFlags": [0, 0, 0, 0], "eventState": "normal", "outOfService": False}
    objs = {
        "temp": AnalogValueObject(
            objectIdentifier=("analog-value", 1), objectName="Supply Temp",
            presentValue=21.5, units="degreesCelsius", **common,
        ),
        "setpoint": AnalogValueObjectCmd(
            objectIdentifier=("analog-value", 2), objectName="Setpoint",
            presentValue=20.0, relinquishDefault=20.0, units="degreesCelsius", **common,
        ),
        "fan": BinaryValueObject(
            objectIdentifier=("binary-value", 1), objectName="Fan Command",
            presentValue="inactive", **common,
        ),
        "mode": MultiStateValueObject(
            objectIdentifier=("multi-state-value", 1), objectName="Mode",
            presentValue=2, numberOfStates=3, stateText=["Off", "Heat", "Cool"], **common,
        ),
    }
    for obj in objs.values():
        app.add_object(obj)
    await asyncio.sleep(0.1)
    yield SimpleNamespace(app=app, objects=objs)
    app.close()
