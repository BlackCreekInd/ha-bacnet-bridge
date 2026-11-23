"""Thin async wrapper around a BACpypes3 Application.

Everything BACnet-specific lives here so the Home Assistant parts (config flow,
coordinators, entities, server) never touch BACpypes3 directly. One stack =
one UDP socket = HA's own BACnet device. Client reads/writes and the server
side (exposed HA entities) share it, because only one process can own
UDP 47808 on an interface.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import logging
import socket
from types import SimpleNamespace
from typing import Any

from bacpypes3.apdu import ErrorRejectAbortNack
from bacpypes3.app import Application
from bacpypes3.appservice import ABORTED
from bacpypes3.basetypes import ErrorType
from bacpypes3.pdu import Address

from .const import (
    ANALOG_TYPES,
    BINARY_TYPES,
    DEFAULT_PORT,
    MULTISTATE_TYPES,
    SUPPORTED_TYPES,
    VENDOR_IDENTIFIER,
)

_LOGGER = logging.getLogger(__name__)

# Keep ReadPropertyMultiple requests small so they fit devices that do not
# support segmentation.
RPM_CHUNK = 16


class BacnetError(Exception):
    """A BACnet request failed (error, reject, abort or timeout)."""


class BacnetNoResponse(BacnetError):
    """The device did not answer (after BACpypes3's own retries)."""


def _is_no_response(err: BaseException) -> bool:
    return str(getattr(err, "apduAbortRejectReason", "")) == "no-response"


@dataclass
class DiscoveredDevice:
    """A device that answered Who-Is."""

    instance: int
    address: str
    vendor_id: int
    name: str = ""
    vendor_name: str = ""
    model_name: str = ""


@dataclass
class ObjectInfo:
    """Static description of one object on a remote device."""

    object_id: str  # "analog-input,1"
    object_type: str
    instance: int
    name: str
    units: str | None = None  # bacpypes3 attribute name, e.g. "degreesCelsius"
    state_text: list[str] = field(default_factory=list)
    commandable: bool = False
    present_value: Any = None

    def as_dict(self) -> dict[str, Any]:
        """Serialize for config entry options (JSON)."""
        return {
            "object_id": self.object_id,
            "name": self.name,
            "units": self.units,
            "state_text": self.state_text,
            "commandable": self.commandable,
        }


def _is_error(value: Any) -> bool:
    return isinstance(value, (ErrorType, ErrorRejectAbortNack))


def split_object_id(object_id: str) -> tuple[str, int]:
    """'analog-input,1' -> ('analog-input', 1)."""
    obj_type, _, inst = object_id.partition(",")
    return obj_type, int(inst)


def normalize_value(object_type: str, value: Any) -> Any:
    """Convert a BACpypes3 present-value into a plain Python value."""
    if value is None or _is_error(value):
        return None
    if object_type in BINARY_TYPES:
        return str(value) == "active"
    if object_type in ANALOG_TYPES:
        return float(value)
    if object_type in MULTISTATE_TYPES:
        return int(value)
    return value


def build_address(interface: str, port: int) -> str:
    """'192.168.1.50/24' + 47808 -> BACpypes3 address string."""
    if port and port != DEFAULT_PORT:
        return f"{interface}:{port}"
    return interface


def check_port_free(interface: str, port: int) -> None:
    """Raise OSError if the UDP port is already taken on that address.

    BACpypes3 opens its sockets in the background, so a bind failure there
    would only show up in the log. Checking up front lets the config flow
    and setup report it properly.
    """
    host = interface.split("/")[0]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((host, port))
    finally:
        sock.close()


class BacnetStack:
    """HA's BACnet device, used as client and server."""

    def __init__(
        self,
        interface: str,
        port: int,
        device_instance: int,
        device_name: str,
        foreign_bbmd: str | None = None,
        foreign_ttl: int = 300,
    ) -> None:
        self.interface = interface
        self.port = port
        self.device_instance = device_instance
        self.device_name = device_name
        self.foreign_bbmd = foreign_bbmd or None
        self.foreign_ttl = foreign_ttl
        self.app: Application | None = None
        # Devices that rejected ReadPropertyMultiple; fall back to single reads.
        self._no_rpm: set[str] = set()

    # ----- lifecycle -----

    async def start(self) -> None:
        """Open the UDP socket and register as foreign device if configured."""
        # After a reload the previous socket is released on a later loop
        # iteration, so give it a moment before declaring the port busy.
        for attempt in range(20):
            try:
                check_port_free(self.interface, self.port)
                break
            except OSError:
                if attempt == 19:
                    raise
                await asyncio.sleep(0.1)
        args = SimpleNamespace(
            address=build_address(self.interface, self.port),
            foreign=self.foreign_bbmd,
            ttl=self.foreign_ttl,
            bbmd=None,
            network=0,
            instance=self.device_instance,
            name=self.device_name,
            vendoridentifier=VENDOR_IDENTIFIER,
        )
        self.app = Application.from_args(args)
        # BACpypes3 opens its UDP endpoints in background tasks that retry
        # forever. Wait for them so a failure surfaces here, and so close()
        # never races a socket that is still being opened.
        tasks = [
            task
            for link_layer in self.app.link_layers.values()
            for task in getattr(getattr(link_layer, "server", None), "_transport_tasks", [])
        ]
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
        except TimeoutError as err:
            self.close()
            raise OSError(f"Could not open BACnet/IP socket on {args.address}") from err
        _LOGGER.debug(
            "BACnet stack started on %s as device %s (foreign BBMD: %s)",
            args.address,
            self.device_instance,
            self.foreign_bbmd,
        )

    def close(self) -> None:
        """Stop outstanding requests and close the sockets.

        BACpypes3 keeps retrying unanswered requests on timers. Closing the
        socket alone would leave those timers sending on a closed socket, so
        abort the client transactions and cancel their futures first. This
        reaches into BACpypes3 internals (asap.clientTransactions, _requests);
        recheck it when upgrading BACpypes3.
        """
        app, self.app = self.app, None
        if app is None:
            return
        for transaction in list(getattr(app.asap, "clientTransactions", [])):
            transaction.stop_timer()
            with contextlib.suppress(Exception):
                transaction.set_state(ABORTED)
        for requests in list(getattr(app, "_requests", {}).values()):
            for _apdu, future in list(requests):
                future.cancel()
        app.close()

    @property
    def _app(self) -> Application:
        if self.app is None:
            raise BacnetError("BACnet stack is not running")
        return self.app

    # ----- client: discovery -----

    async def discover(
        self,
        target: str | None = None,
        low_limit: int | None = None,
        high_limit: int | None = None,
        timeout: float = 5,
    ) -> list[DiscoveredDevice]:
        """Send Who-Is (broadcast, or directed at one address) and collect I-Am."""
        address = Address(target) if target else None
        i_ams = await self._app.who_is(
            low_limit, high_limit, address=address, timeout=timeout
        )
        found: dict[int, DiscoveredDevice] = {}
        for i_am in i_ams:
            instance = i_am.iAmDeviceIdentifier[1]
            if instance == self.device_instance:
                continue  # ourselves
            found[instance] = DiscoveredDevice(
                instance=instance,
                address=str(i_am.pduSource),
                vendor_id=int(i_am.vendorID),
            )
        # Fill in names; a device that does not answer is still listed.
        await asyncio.gather(
            *(self._fill_device_names(dev) for dev in found.values())
        )
        return sorted(found.values(), key=lambda d: d.instance)

    async def _fill_device_names(self, dev: DiscoveredDevice) -> None:
        props = ("object-name", "vendor-name", "model-name")
        try:
            values = await self.read_properties(
                dev.address, [(f"device,{dev.instance}", list(props))]
            )
        except BacnetError:
            values = {}
        result = values.get(f"device,{dev.instance}", {})
        dev.name = str(result.get("object-name") or f"Device {dev.instance}")
        dev.vendor_name = str(result.get("vendor-name") or "")
        dev.model_name = str(result.get("model-name") or "")

    async def read_object_list(self, address: str, device_instance: int) -> list[str]:
        """Return the device's object list as 'type,instance' strings."""
        device_id = f"device,{device_instance}"
        try:
            objects = await self._read(address, device_id, "object-list")
        except BacnetNoResponse:
            raise
        except BacnetError:
            # Large lists can exceed what an unsegmented device can send in
            # one go. Read the length, then each element.
            _LOGGER.debug("Reading object-list of %s element by element", address)
            length = int(await self._read(address, device_id, "object-list", 0))
            objects = []
            for index in range(1, length + 1):
                objects.append(
                    await self._read(address, device_id, "object-list", index)
                )
        return [f"{obj[0]},{obj[1]}" for obj in objects]

    async def describe_objects(
        self, address: str, object_ids: list[str]
    ) -> list[ObjectInfo]:
        """Read name/units/state text/present value for supported objects."""
        wanted: list[tuple[str, list[str]]] = []
        for object_id in object_ids:
            obj_type, _ = split_object_id(object_id)
            if obj_type not in SUPPORTED_TYPES:
                continue
            props = ["object-name", "present-value"]
            if obj_type in ANALOG_TYPES:
                props.append("units")
            if obj_type in MULTISTATE_TYPES:
                props.append("state-text")
            if obj_type.endswith(("-output", "-value")):
                props.append("priority-array")
            wanted.append((object_id, props))

        values = await self.read_properties(address, wanted)
        infos: list[ObjectInfo] = []
        for object_id, _props in wanted:
            obj_type, inst = split_object_id(object_id)
            result = values.get(object_id, {})
            units = result.get("units")
            state_text = result.get("state-text")
            infos.append(
                ObjectInfo(
                    object_id=object_id,
                    object_type=obj_type,
                    instance=inst,
                    name=str(result.get("object-name") or object_id),
                    units=getattr(units, "attr", None) if units is not None else None,
                    state_text=[str(s) for s in state_text] if state_text else [],
                    commandable=result.get("priority-array") is not None,
                    present_value=normalize_value(obj_type, result.get("present-value")),
                )
            )
        return infos

    # ----- client: polling and writing -----

    async def read_present_values(
        self, address: str, object_ids: list[str]
    ) -> dict[str, Any]:
        """Read present-value of each object. Missing/failed reads map to None."""
        values = await self.read_properties(
            address, [(object_id, ["present-value"]) for object_id in object_ids]
        )
        return {
            object_id: normalize_value(
                split_object_id(object_id)[0],
                values.get(object_id, {}).get("present-value"),
            )
            for object_id in object_ids
        }

    async def read_properties(
        self, address: str, requests: list[tuple[str, list[str]]]
    ) -> dict[str, dict[str, Any]]:
        """Read several properties of several objects.

        Uses ReadPropertyMultiple in small chunks and falls back to single
        ReadProperty requests for devices that do not support it. Properties
        that return an error are left out of the result.
        """
        result: dict[str, dict[str, Any]] = {}
        if not requests:
            return result

        if address not in self._no_rpm:
            try:
                for start in range(0, len(requests), RPM_CHUNK):
                    chunk = requests[start : start + RPM_CHUNK]
                    response = await self._app.read_property_multiple(
                        Address(address), chunk
                    )
                    for objid, prop, _index, value in response:
                        if _is_error(value):
                            continue
                        key = f"{objid[0]},{objid[1]}"
                        result.setdefault(key, {})[str(prop)] = value
                return result
            except ErrorRejectAbortNack as err:
                if _is_no_response(err):
                    # Offline, not "RPM unsupported": don't fall back to many
                    # single reads that would each time out as well.
                    raise BacnetNoResponse(f"No response from {address}") from err
                # unrecognized-service, segmentation problems, etc.
                _LOGGER.debug(
                    "ReadPropertyMultiple failed on %s (%s), using ReadProperty",
                    address,
                    err,
                )
                self._no_rpm.add(address)
                result.clear()

        for object_id, props in requests:
            for prop in props:
                try:
                    value = await self._read(address, object_id, prop)
                except BacnetNoResponse:
                    raise
                except BacnetError:
                    continue
                result.setdefault(object_id, {})[prop] = value
        return result

    async def write_present_value(
        self,
        address: str,
        object_id: str,
        value: Any,
        priority: int | None = None,
    ) -> None:
        """Write present-value. Priority only applies to commandable objects."""
        obj_type, _ = split_object_id(object_id)
        if obj_type in BINARY_TYPES:
            value = "active" if value else "inactive"
        try:
            await self._app.write_property(
                Address(address), object_id, "present-value", value, priority=priority
            )
        except ErrorRejectAbortNack as err:
            raise BacnetError(f"Write to {object_id} at {address} failed: {err}") from err

    async def _read(
        self, address: str, object_id: str, prop: str, index: int | None = None
    ) -> Any:
        try:
            value = await self._app.read_property(
                Address(address), object_id, prop, index
            )
        except ErrorRejectAbortNack as err:
            if _is_no_response(err):
                raise BacnetNoResponse(f"No response from {address}") from err
            raise BacnetError(f"Read {object_id} {prop} at {address}: {err}") from err
        if _is_error(value):
            raise BacnetError(f"Read {object_id} {prop} at {address}: {value}")
        return value
