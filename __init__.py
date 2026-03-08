"""Bose SoundTouch Direct integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import DEFAULT_PORT, DOMAIN
from .coordinator import SoundTouchCoordinator
from .soundtouch_client import SoundTouchDevice

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Bose SoundTouch Direct from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)

    device = SoundTouchDevice(host, port)

    # Initial info fetch to confirm connectivity
    info = await device.get_info()
    if not info:
        _LOGGER.error("Unable to connect to SoundTouch device at %s", host)
        await device.close()
        return False

    coordinator = SoundTouchCoordinator(hass, device)
    await coordinator.async_config_entry_first_refresh()

    # Start WebSocket for real-time push updates
    await coordinator.async_start_websocket()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: SoundTouchCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.device.close()
    return unload_ok
