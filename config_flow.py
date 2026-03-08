"""Config flow for Bose SoundTouch Direct integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import zeroconf
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_APP_KEY, DEFAULT_NAME, DEFAULT_PORT, DOMAIN
from .soundtouch_client import SoundTouchDevice

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_APP_KEY, default=""): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input and connect to the device."""
    device = SoundTouchDevice(data[CONF_HOST], data.get(CONF_PORT, DEFAULT_PORT))
    try:
        info = await device.get_info()
    finally:
        await device.close()

    if not info or "info" not in info:
        raise CannotConnect("Unable to retrieve device info")

    device_info = info["info"]
    return {
        "title": device_info.get("name", DEFAULT_NAME),
        "device_id": device_info.get("@deviceID"),
        "device_type": device_info.get("type"),
    }


class SoundTouchConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bose SoundTouch Direct."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovery_info: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Return the options flow."""
        return SoundTouchOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step (manual entry)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during setup")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["device_id"])
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: user_input[CONF_HOST]}
                )
                return self.async_create_entry(
                    title=info["title"],
                    data={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: user_input.get(CONF_PORT, DEFAULT_PORT),
                        CONF_APP_KEY: user_input.get(CONF_APP_KEY, ""),
                        "device_id": info["device_id"],
                        "device_type": info["device_type"],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            description_placeholders={
                "app_key_url": "https://developer.bose.com"
            },
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> FlowResult:
        """Handle zeroconf discovery."""
        host = discovery_info.host
        port = discovery_info.port or DEFAULT_PORT

        self._discovery_info = {CONF_HOST: host, CONF_PORT: port}

        try:
            info = await validate_input(self.hass, self._discovery_info)
        except (CannotConnect, Exception):
            return self.async_abort(reason="cannot_connect")

        device_id = info.get("device_id")
        if device_id:
            await self.async_set_unique_id(device_id)
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        self._discovery_info["title"] = info["title"]
        self.context["title_placeholders"] = {"name": info["title"], "host": host}

        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm zeroconf discovery."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovery_info["title"],
                data={
                    CONF_HOST: self._discovery_info[CONF_HOST],
                    CONF_PORT: self._discovery_info[CONF_PORT],
                    CONF_APP_KEY: "",
                },
            )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={
                "name": self._discovery_info.get("title", DEFAULT_NAME),
                "host": self._discovery_info[CONF_HOST],
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguration (e.g. IP address changed)."""
        errors: dict[str, str] = {}
        current = self._get_reconfigure_entry()

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    current,
                    data_updates={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: user_input.get(CONF_PORT, DEFAULT_PORT),
                        CONF_APP_KEY: user_input.get(CONF_APP_KEY, current.data.get(CONF_APP_KEY, "")),
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=current.data.get(CONF_HOST, "")): str,
                    vol.Optional(CONF_PORT, default=current.data.get(CONF_PORT, DEFAULT_PORT)): int,
                    vol.Optional(CONF_APP_KEY, default=current.data.get(CONF_APP_KEY, "")): str,
                }
            ),
            errors=errors,
        )


class SoundTouchOptionsFlow(config_entries.OptionsFlow):
    """Options flow — lets users add/change the Bose app key without re-adding the device."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options."""
        if user_input is not None:
            # Store in options, but also update config entry data so the
            # integration picks it up immediately after reload.
            return self.async_create_entry(title="", data=user_input)

        current_key = self._config_entry.options.get(
            CONF_APP_KEY,
            self._config_entry.data.get(CONF_APP_KEY, ""),
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_APP_KEY, default=current_key): str,
                }
            ),
            description_placeholders={
                "app_key_url": "https://developer.bose.com"
            },
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
