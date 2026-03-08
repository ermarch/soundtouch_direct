"""Bose SoundTouch Direct integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import DEFAULT_PORT, DOMAIN
from .coordinator import SoundTouchCoordinator
from .soundtouch_client import SoundTouchDevice
from .stream_proxy import async_setup_stream_proxy

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER]

# Key under hass.data[DOMAIN] for the shared stream proxy
STREAM_PROXY_KEY = "stream_proxy"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration — register the stream proxy HTTP view once."""
    hass.data.setdefault(DOMAIN, {})
    if STREAM_PROXY_KEY not in hass.data[DOMAIN]:
        proxy = async_setup_stream_proxy(hass)
        hass.data[DOMAIN][STREAM_PROXY_KEY] = proxy
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Bose SoundTouch Direct from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Ensure stream proxy is registered (in case async_setup wasn't called)
    if STREAM_PROXY_KEY not in hass.data[DOMAIN]:
        proxy = async_setup_stream_proxy(hass)
        hass.data[DOMAIN][STREAM_PROXY_KEY] = proxy

    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)

    device = SoundTouchDevice(host, port)

    info = await device.get_info()
    if not info:
        _LOGGER.error("Unable to connect to SoundTouch device at %s", host)
        await device.close()
        return False

    coordinator = SoundTouchCoordinator(hass, device)
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_start_websocket()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: SoundTouchCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.device.close()
    return unload_ok
