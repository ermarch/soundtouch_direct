"""Bose SoundTouch Direct integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import DEFAULT_PORT, DOMAIN
from .coordinator import SoundTouchCoordinator
from .soundtouch_client import SoundTouchDevice
from .stream_proxy import async_setup_stream_proxy

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER, Platform.NUMBER, Platform.BUTTON]

# Key under hass.data[DOMAIN] for the shared stream proxy
STREAM_PROXY_KEY = "stream_proxy"


def _get_ha_base_url(hass: HomeAssistant) -> str:
    """Return the HA base URL suitable for SoundTouch stream proxy URLs.

    Prefers the user-configured internal URL (which may use a hostname like
    homeassistant.local) over a raw IP, so URLs survive network/IP changes.
    Falls back to IP-based URL if no internal URL is configured.
    The SoundTouch firmware rejects HTTPS, so we always force HTTP.
    """
    url = None
    # First try: use configured internal URL (respects user's hostname setting).
    try:
        url = get_url(hass, allow_internal=True, allow_ip=False, prefer_external=False)
    except NoURLAvailableError:
        pass
    # Second try: allow IP-based internal URL.
    if not url:
        try:
            url = get_url(hass, allow_internal=True, allow_ip=True, prefer_external=False)
        except NoURLAvailableError:
            pass
    # Last resort: construct from HA config.
    if not url:
        try:
            url = get_url(hass, allow_external=True)
        except NoURLAvailableError:
            url = f"http://{hass.config.api.local_ip}:{hass.config.api.port}"  # type: ignore[union-attr]
    # Force HTTP — SoundTouch firmware cannot fetch HTTPS streams.
    if url.startswith("https://"):
        url = "http://" + url[8:]
    return url.rstrip("/")


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration — register the stream proxy HTTP view once."""
    hass.data.setdefault(DOMAIN, {})
    if STREAM_PROXY_KEY not in hass.data[DOMAIN]:
        base_url = _get_ha_base_url(hass)
        proxy = async_setup_stream_proxy(hass, base_url)
        hass.data[DOMAIN][STREAM_PROXY_KEY] = proxy
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Bose SoundTouch Direct from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Ensure stream proxy is registered (in case async_setup wasn't called)
    if STREAM_PROXY_KEY not in hass.data[DOMAIN]:
        base_url = _get_ha_base_url(hass)
        proxy = async_setup_stream_proxy(hass, base_url)
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
