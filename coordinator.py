"""DataUpdateCoordinator for Bose SoundTouch Direct."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    SCAN_INTERVAL,
    WS_NOW_PLAYING_CHANGED,
    WS_NOW_PLAYING_UPDATED,
    WS_VOLUME_UPDATED,
    WS_PRESETS_CHANGED,
    WS_PRESETS_UPDATED,
    WS_NAME_UPDATED,
    WS_INFO_UPDATED,
)
from .soundtouch_client import SoundTouchDevice

_LOGGER = logging.getLogger(__name__)


class SoundTouchCoordinator(DataUpdateCoordinator):
    """Manages fetching data from a SoundTouch device."""

    def __init__(self, hass: HomeAssistant, device: SoundTouchDevice) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{device.host}",
            update_interval=timedelta(seconds=SCAN_INTERVAL),
        )
        self.device = device
        self._ws_connected = False

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the device."""
        try:
            info, now_playing, volume, presets, sources, bass = await asyncio.gather(
                self.device.get_info(),
                self.device.get_now_playing(),
                self.device.get_volume(),
                self.device.get_presets(),
                self.device.get_sources(),
                self.device.get_bass(),
                return_exceptions=True,
            )

            def _unwrap(result, key=None):
                if isinstance(result, Exception):
                    return None
                if result is None:
                    return None
                if key:
                    return result.get(key)
                return result

            return {
                "info": _unwrap(info, "info"),
                "now_playing": _unwrap(now_playing, "nowPlaying"),
                "volume": _unwrap(volume, "volume"),
                "presets": _unwrap(presets, "presets"),
                "sources": _unwrap(sources, "sources"),
                "bass": _unwrap(bass, "bass"),
            }

        except Exception as err:
            raise UpdateFailed(f"Error communicating with SoundTouch: {err}") from err

    async def async_start_websocket(self) -> None:
        """Start the WebSocket listener and register callback."""
        self.device.register_ws_callback(self._handle_ws_notification)
        await self.device.start_websocket()
        self._ws_connected = True
        _LOGGER.debug("WebSocket started for %s", self.device.host)

    @callback
    def _handle_ws_notification(self, data: dict[str, Any]) -> None:
        """Handle a push notification from the WebSocket."""
        updates = data.get("updates", {})
        if not updates:
            return

        should_refresh = False

        if WS_NOW_PLAYING_UPDATED in updates or WS_NOW_PLAYING_CHANGED in updates:
            should_refresh = True
        if WS_VOLUME_UPDATED in updates:
            should_refresh = True
        if WS_PRESETS_CHANGED in updates or WS_PRESETS_UPDATED in updates:
            should_refresh = True
        if WS_NAME_UPDATED in updates or WS_INFO_UPDATED in updates:
            should_refresh = True

        if should_refresh:
            self.hass.async_create_task(self.async_request_refresh())
