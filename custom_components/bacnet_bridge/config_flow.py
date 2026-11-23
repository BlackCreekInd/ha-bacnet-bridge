"""Config flow (network setup) and options flow (devices, exposed entities)."""

from __future__ import annotations

import ipaddress
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import network
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .bacnet_stack import BacnetError, check_port_free, split_object_id
from .const import (
    CONF_DESCRIPTION,
    CONF_DEVICE_INSTANCE,
    CONF_DEVICE_NAME,
    CONF_DEVICES,
    CONF_EXPOSED,
    CONF_FOREIGN_BBMD,
    CONF_FOREIGN_TTL,
    CONF_INTERFACE,
    CONF_LOCATION,
    CONF_MODEL_NAME,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_WRITE_PRIORITY,
    DEFAULT_DEVICE_NAME,
    DEFAULT_DISCOVERY_TIMEOUT,
    DEFAULT_FOREIGN_TTL,
    DEFAULT_MODEL_NAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WRITE_PRIORITY,
    DOMAIN,
    EXPOSABLE_DOMAINS,
    TYPE_ABBREVIATIONS,
    WRITABLE_CANDIDATE_TYPES,
)
from .units import bacnet_to_ha

_LOGGER = logging.getLogger(__name__)

MAX_INSTANCE = 4194302


async def _interface_options(hass: HomeAssistant) -> list[selector.SelectOptionDict]:
    """IPv4 addresses of HA's network adapters, as 'address/prefix'."""
    options: list[selector.SelectOptionDict] = []
    for adapter in await network.async_get_adapters(hass):
        for ipv4 in adapter["ipv4"]:
            if ipv4["address"].startswith("127."):
                continue
            value = f"{ipv4['address']}/{ipv4['network_prefix']}"
            label = f"{value} ({adapter['name']})"
            if adapter["default"]:
                options.insert(0, {"value": value, "label": label})
            else:
                options.append({"value": value, "label": label})
    return options


def _valid_interface(value: str) -> bool:
    try:
        ipaddress.IPv4Interface(value)
    except ValueError:
        return False
    return "/" in value


def _valid_bbmd(value: str) -> bool:
    host, _, port = value.partition(":")
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        return False
    return not port or (port.isdigit() and 0 < int(port) < 65536)


async def _network_schema(hass: HomeAssistant, defaults: dict[str, Any]) -> vol.Schema:
    interfaces = await _interface_options(hass)
    default_iface = defaults.get(CONF_INTERFACE) or (interfaces[0]["value"] if interfaces else "")
    return vol.Schema(
        {
            vol.Required(CONF_INTERFACE, default=default_iface): selector.SelectSelector(
                selector.SelectSelectorConfig(options=interfaces, custom_value=True)
            ),
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(
                CONF_DEVICE_INSTANCE, default=defaults.get(CONF_DEVICE_INSTANCE, 47808)
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=MAX_INSTANCE)),
            vol.Required(
                CONF_DEVICE_NAME, default=defaults.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME)
            ): str,
            vol.Optional(
                CONF_FOREIGN_BBMD,
                description={"suggested_value": defaults.get(CONF_FOREIGN_BBMD, "")},
            ): str,
            vol.Required(
                CONF_FOREIGN_TTL, default=defaults.get(CONF_FOREIGN_TTL, DEFAULT_FOREIGN_TTL)
            ): vol.All(vol.Coerce(int), vol.Range(min=30, max=65535)),
        }
    )


def _validate_network(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not _valid_interface(user_input[CONF_INTERFACE]):
        errors[CONF_INTERFACE] = "invalid_interface"
    bbmd = (user_input.get(CONF_FOREIGN_BBMD) or "").strip()
    user_input[CONF_FOREIGN_BBMD] = bbmd
    if bbmd and not _valid_bbmd(bbmd):
        errors[CONF_FOREIGN_BBMD] = "invalid_bbmd"
    return errors


def _object_label(obj: Any) -> str:
    """'AI 1 · Zone temp (21.5 °C)' for a discovered ObjectInfo."""
    text = f"{TYPE_ABBREVIATIONS[obj.object_type]} {obj.instance} · {obj.name}"
    if obj.present_value is not None:
        value = obj.present_value
        if obj.state_text and isinstance(value, int) and 1 <= value <= len(obj.state_text):
            value = obj.state_text[value - 1]
        unit = bacnet_to_ha(obj.units)[0]
        text += f" ({value}{' ' + unit if unit else ''})"
    return text


def _object_choice_schema(objects: list[Any]) -> dict[Any, Any]:
    """'objects' and 'writable' pickers for discovered objects."""
    object_options = [
        selector.SelectOptionDict(value=obj.object_id, label=_object_label(obj)) for obj in objects
    ]
    writable_options = [
        selector.SelectOptionDict(value=obj.object_id, label=_object_label(obj))
        for obj in objects
        if obj.object_type in WRITABLE_CANDIDATE_TYPES
    ]
    schema: dict[Any, Any] = {
        vol.Required("objects", default=[]): selector.SelectSelector(
            selector.SelectSelectorConfig(options=object_options, multiple=True)
        ),
    }
    if writable_options:
        schema[vol.Optional("writable", default=[])] = selector.SelectSelector(
            selector.SelectSelectorConfig(options=writable_options, multiple=True)
        )
    return schema


def _configured_object_key(obj: dict[str, Any]) -> str:
    """'AV 2', the field name for a configured object on the rename form."""
    obj_type, inst = split_object_id(obj["object_id"])
    return f"{TYPE_ABBREVIATIONS.get(obj_type, obj_type)} {inst}"


class BacnetConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up HA's own BACnet/IP device."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_network(user_input)
            if not errors:
                try:
                    await self.hass.async_add_executor_job(
                        check_port_free, user_input[CONF_INTERFACE], user_input[CONF_PORT]
                    )
                except OSError:
                    errors["base"] = "port_in_use"
            if not errors:
                return self.async_create_entry(
                    title=f"{user_input[CONF_DEVICE_NAME]} (BACnet {user_input[CONF_DEVICE_INSTANCE]})",
                    data=user_input,
                    options={CONF_DEVICES: {}, CONF_EXPOSED: []},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=await _network_schema(self.hass, user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_network(user_input)
            if not errors:
                # The running entry holds the port, so the free-port check is
                # skipped here; setup reports a busy port after the reload.
                return self.async_update_reload_and_abort(entry, data=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=await _network_schema(self.hass, user_input or dict(entry.data)),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> BacnetOptionsFlow:
        return BacnetOptionsFlow()


class BacnetOptionsFlow(OptionsFlow):
    """Add/remove remote devices, choose exposed entities, settings."""

    def __init__(self) -> None:
        self._found: dict[str, Any] = {}
        self._device: Any = None
        self._objects: list[Any] = []
        # Edit-device state: key of the device being edited, a working copy of
        # its config, what the user asked for next, and objects found on it
        # that are not configured yet (None = not read yet).
        self._edit_key: str | None = None
        self._edit: dict[str, Any] = {}
        self._rename_next = False
        self._find_more = False
        self._new_objects: list[Any] | None = None
        self._read_failed = False

    @property
    def _options(self) -> dict[str, Any]:
        return dict(self.config_entry.options)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_device", "edit_device", "remove_devices", "expose", "settings"],
        )

    # ----- add a remote device -----

    async def async_step_add_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        errors: dict[str, str] = {}
        if user_input is not None:
            target = (user_input.get("target") or "").strip() or None
            low = user_input.get("low_limit")
            high = user_input.get("high_limit")
            if (low is None) != (high is None):
                errors["base"] = "limits_pair"
            else:
                stack = self.config_entry.runtime_data.stack
                try:
                    found = await stack.discover(
                        target,
                        None if low is None else int(low),
                        None if high is None else int(high),
                        timeout=user_input.get("timeout", DEFAULT_DISCOVERY_TIMEOUT),
                    )
                except (BacnetError, ValueError) as err:
                    _LOGGER.debug("Discovery failed: %s", err)
                    errors["base"] = "discovery_failed"
                else:
                    configured = self._options.get(CONF_DEVICES, {})
                    self._found = {
                        str(d.instance): d for d in found if str(d.instance) not in configured
                    }
                    if self._found:
                        return await self.async_step_pick_device()
                    errors["base"] = "no_devices_found"
        schema = vol.Schema(
            {
                vol.Optional("target"): str,
                vol.Optional("low_limit"): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=MAX_INSTANCE)
                ),
                vol.Optional("high_limit"): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=MAX_INSTANCE)
                ),
                vol.Required("timeout", default=DEFAULT_DISCOVERY_TIMEOUT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=60)
                ),
            }
        )
        return self.async_show_form(
            step_id="add_device",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    async def async_step_pick_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._device = self._found[user_input["device"]]
            stack = self.config_entry.runtime_data.stack
            try:
                object_ids = await stack.read_object_list(
                    self._device.address, self._device.instance
                )
                self._objects = await stack.describe_objects(self._device.address, object_ids)
            except BacnetError as err:
                _LOGGER.warning("Reading objects of %s failed: %s", self._device.address, err)
                return self.async_abort(reason="cannot_read_device")
            if not self._objects:
                return self.async_abort(reason="no_supported_objects")
            return await self.async_step_pick_objects()

        options = [
            selector.SelectOptionDict(
                value=key,
                label=" · ".join(
                    part
                    for part in (
                        f"{d.name} (instance {d.instance})",
                        d.address,
                        " ".join(p for p in (d.vendor_name, d.model_name) if p),
                    )
                    if part
                ),
            )
            for key, d in self._found.items()
        ]
        return self.async_show_form(
            step_id="pick_device",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, mode=selector.SelectSelectorMode.LIST
                        )
                    )
                }
            ),
            description_placeholders={"count": str(len(options))},
        )

    async def async_step_pick_objects(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            chosen = set(user_input.get("objects", []))
            writable = set(user_input.get("writable", []))
            dev = self._device
            devices = dict(self._options.get(CONF_DEVICES, {}))
            devices[str(dev.instance)] = {
                "instance": dev.instance,
                "address": dev.address,
                "name": dev.name,
                "vendor_name": dev.vendor_name,
                "model_name": dev.model_name,
                "scan_interval": user_input[CONF_SCAN_INTERVAL],
                "objects": [
                    {**obj.as_dict(), "writable": obj.object_id in writable}
                    for obj in self._objects
                    if obj.object_id in chosen
                ],
            }
            return self.async_create_entry(data={**self._options, CONF_DEVICES: devices})

        schema = _object_choice_schema(self._objects)
        schema[vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL)] = vol.All(
            vol.Coerce(int), vol.Range(min=5, max=3600)
        )
        return self.async_show_form(
            step_id="pick_objects",
            data_schema=vol.Schema(schema),
            description_placeholders={
                "device": f"{self._device.name} (instance {self._device.instance})",
                "count": str(len(self._objects)),
            },
        )

    # ----- edit a configured remote device -----

    async def async_step_edit_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        devices = self._options.get(CONF_DEVICES, {})
        if not devices:
            return self.async_abort(reason="no_devices_configured")
        if user_input is None and len(devices) == 1:
            user_input = {"device": next(iter(devices))}
        if user_input is not None:
            self._edit_key = user_input["device"]
            device = devices[self._edit_key]
            self._edit = {**device, "objects": [dict(o) for o in device["objects"]]}
            return await self.async_step_device_options()
        options = [
            selector.SelectOptionDict(
                value=key, label=f"{d.get('name')} (instance {d['instance']}) · {d['address']}"
            )
            for key, d in devices.items()
        ]
        return self.async_show_form(
            step_id="edit_device",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, mode=selector.SelectSelectorMode.LIST
                        )
                    )
                }
            ),
        )

    async def async_step_device_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        dev = self._edit
        errors: dict[str, str] = {}
        if user_input is not None:
            keep = set(user_input.get("objects", []))
            if not keep:
                errors["objects"] = "no_objects_kept"
            else:
                writable = set(user_input.get("writable", []))
                dev["name"] = (user_input.get("name") or "").strip() or dev.get("name") or ""
                dev[CONF_SCAN_INTERVAL] = user_input[CONF_SCAN_INTERVAL]
                dev["objects"] = [
                    {**obj, "writable": obj["object_id"] in writable}
                    for obj in dev["objects"]
                    if obj["object_id"] in keep
                ]
                self._rename_next = bool(user_input.get("rename"))
                self._find_more = bool(user_input.get("find_more"))
                return await self._async_edit_next()

        objects = dev["objects"]

        def label(obj: dict[str, Any]) -> str:
            return f"{_configured_object_key(obj)} · {obj.get('name')}"

        object_options = [
            selector.SelectOptionDict(value=o["object_id"], label=label(o)) for o in objects
        ]
        writable_options = [
            selector.SelectOptionDict(value=o["object_id"], label=label(o))
            for o in objects
            if split_object_id(o["object_id"])[0] in WRITABLE_CANDIDATE_TYPES
        ]
        schema: dict[Any, Any] = {
            vol.Optional("name", default=dev.get("name") or ""): selector.TextSelector(),
            vol.Required(
                "objects", default=[o["object_id"] for o in objects]
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=object_options, multiple=True)
            ),
        }
        if writable_options:
            schema[
                vol.Optional(
                    "writable",
                    default=[o["value"] for o in writable_options if self._is_writable(o["value"])],
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=writable_options, multiple=True)
            )
        schema[
            vol.Required(
                CONF_SCAN_INTERVAL, default=dev.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            )
        ] = vol.All(vol.Coerce(int), vol.Range(min=5, max=3600))
        schema[vol.Optional("rename", default=False)] = selector.BooleanSelector()
        schema[vol.Optional("find_more", default=False)] = selector.BooleanSelector()
        return self.async_show_form(
            step_id="device_options",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "device": f"{dev.get('name')} (instance {dev['instance']})",
                "count": str(len(objects)),
            },
        )

    def _is_writable(self, object_id: str) -> bool:
        return any(
            o["object_id"] == object_id and o.get("writable") for o in self._edit["objects"]
        )

    async def _async_edit_next(self) -> ConfigFlowResult:
        """Go to the next step the user asked for, or save."""
        if self._rename_next:
            return await self.async_step_rename_objects()
        if self._find_more:
            return await self.async_step_add_objects()
        return self._async_save_edit()

    @callback
    def _async_save_edit(self) -> ConfigFlowResult:
        devices = dict(self._options.get(CONF_DEVICES, {}))
        devices[self._edit_key] = self._edit
        return self.async_create_entry(data={**self._options, CONF_DEVICES: devices})

    async def async_step_rename_objects(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        objects = self._edit["objects"]
        if user_input is not None:
            for obj in objects:
                name = (user_input.get(_configured_object_key(obj)) or "").strip()
                if name:
                    obj["name"] = name
            self._rename_next = False
            return await self._async_edit_next()
        # Field names are the object labels ("AV 2"); with no translation for
        # them, the frontend shows the field name itself.
        schema = {
            vol.Optional(
                _configured_object_key(obj), default=obj.get("name") or ""
            ): selector.TextSelector()
            for obj in objects
        }
        return self.async_show_form(
            step_id="rename_objects",
            data_schema=vol.Schema(schema),
            description_placeholders={"device": self._edit.get("name") or ""},
        )

    async def async_step_add_objects(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        dev = self._edit
        if self._new_objects is None:
            self._new_objects = []
            if self.config_entry.state is not ConfigEntryState.LOADED:
                self._read_failed = True
            else:
                # Read the object list straight from the stored address: no
                # Who-Is, and only the objects not configured yet are described.
                stack = self.config_entry.runtime_data.stack
                configured = {o["object_id"] for o in dev["objects"]}
                try:
                    object_ids = await stack.read_object_list(dev["address"], dev["instance"])
                    self._new_objects = await stack.describe_objects(
                        dev["address"], [oid for oid in object_ids if oid not in configured]
                    )
                except BacnetError as err:
                    _LOGGER.warning("Reading objects of %s failed: %s", dev["address"], err)
                    self._read_failed = True

        if user_input is not None:
            chosen = set(user_input.get("objects", []))
            writable = set(user_input.get("writable", []))
            dev["objects"].extend(
                {**obj.as_dict(), "writable": obj.object_id in writable}
                for obj in self._new_objects
                if obj.object_id in chosen
            )
            return self._async_save_edit()

        errors: dict[str, str] = {}
        schema: dict[Any, Any] = {}
        if self._read_failed:
            errors["base"] = "cannot_read_device"
        elif not self._new_objects:
            errors["base"] = "no_new_objects"
        else:
            schema = _object_choice_schema(self._new_objects)
        return self.async_show_form(
            step_id="add_objects",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "device": dev.get("name") or "",
                "count": str(len(self._new_objects)),
            },
        )

    # ----- remove remote devices -----

    async def async_step_remove_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        devices = dict(self._options.get(CONF_DEVICES, {}))
        if not devices:
            return self.async_abort(reason="no_devices_configured")
        if user_input is not None:
            for key in user_input.get("devices", []):
                devices.pop(key, None)
            return self.async_create_entry(data={**self._options, CONF_DEVICES: devices})
        options = [
            selector.SelectOptionDict(
                value=key, label=f"{d.get('name')} (instance {d['instance']}) · {d['address']}"
            )
            for key, d in devices.items()
        ]
        return self.async_show_form(
            step_id="remove_devices",
            data_schema=vol.Schema(
                {
                    vol.Optional("devices", default=[]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options, multiple=True)
                    )
                }
            ),
        )

    # ----- HA entities exposed as BACnet objects -----

    async def async_step_expose(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={**self._options, CONF_EXPOSED: user_input.get(CONF_EXPOSED, [])}
            )
        return self.async_show_form(
            step_id="expose",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_EXPOSED, default=self._options.get(CONF_EXPOSED, [])
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=EXPOSABLE_DOMAINS, multiple=True)
                    )
                }
            ),
        )

    # ----- settings -----

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            for key in (CONF_MODEL_NAME, CONF_LOCATION, CONF_DESCRIPTION):
                user_input[key] = (user_input.get(key) or "").strip()
            if not user_input[CONF_MODEL_NAME]:
                user_input[CONF_MODEL_NAME] = DEFAULT_MODEL_NAME
            return self.async_create_entry(data={**self._options, **user_input})
        opts = self._options
        text = selector.TextSelector()
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_WRITE_PRIORITY,
                        default=opts.get(CONF_WRITE_PRIORITY, DEFAULT_WRITE_PRIORITY),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=16)),
                    vol.Optional(
                        CONF_MODEL_NAME,
                        default=opts.get(CONF_MODEL_NAME) or DEFAULT_MODEL_NAME,
                    ): text,
                    vol.Optional(
                        CONF_LOCATION,
                        description={"suggested_value": opts.get(CONF_LOCATION, "")},
                    ): text,
                    vol.Optional(
                        CONF_DESCRIPTION,
                        description={"suggested_value": opts.get(CONF_DESCRIPTION, "")},
                    ): text,
                }
            ),
        )
