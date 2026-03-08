"""Bose SoundTouch Web API client."""
from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any, Callable

import aiohttp
import xmltodict

from .const import (
    API_BASS,
    API_BASS_CAPABILITIES,
    API_INFO,
    API_KEY,
    API_NOW_PLAYING,
    API_PRESETS,
    API_RECENT,
    API_SELECT,
    API_SOURCES,
    API_VOLUME,
    API_ZONE,
    API_SET_ZONE,
    API_ADD_ZONE_SLAVE,
    API_REMOVE_ZONE_SLAVE,
    DEFAULT_PORT,
    KEY_STATE_PRESS,
    KEY_STATE_RELEASE,
    WEBSOCKET_PORT,
)

_LOGGER = logging.getLogger(__name__)


class SoundTouchDevice:
    """Represents a Bose SoundTouch device."""

    def __init__(self, host: str, port: int = DEFAULT_PORT) -> None:
        """Initialize the device."""
        self.host = host
        self.port = port
        self._base_url = f"http://{host}:{port}"
        self._session: aiohttp.ClientSession | None = None
        self._ws_task: asyncio.Task | None = None
        self._ws_callbacks: list[Callable] = []
        self._device_id: str | None = None
        self._device_name: str | None = None
        self._device_type: str | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def device_id(self) -> str | None:
        return self._device_id

    @property
    def device_name(self) -> str | None:
        return self._device_name

    @property
    def device_type(self) -> str | None:
        return self._device_type

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def close(self) -> None:
        """Close the session and stop websocket."""
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, endpoint: str) -> dict[str, Any] | None:
        """Make a GET request to the device."""
        try:
            session = await self._get_session()
            async with session.get(f"{self._base_url}{endpoint}") as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return xmltodict.parse(text)
                _LOGGER.warning("GET %s returned status %s", endpoint, resp.status)
        except aiohttp.ClientError as err:
            _LOGGER.error("Error connecting to SoundTouch at %s: %s", self.host, err)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Unexpected error in GET %s: %s", endpoint, err)
        return None

    async def _post(self, endpoint: str, body: str) -> dict[str, Any] | None:
        """Make a POST request to the device."""
        try:
            session = await self._get_session()
            headers = {"Content-Type": "application/xml"}
            async with session.post(
                f"{self._base_url}{endpoint}", data=body, headers=headers
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    if text:
                        return xmltodict.parse(text)
                    return {}
                _LOGGER.warning("POST %s returned status %s", endpoint, resp.status)
        except aiohttp.ClientError as err:
            _LOGGER.error("Error connecting to SoundTouch at %s: %s", self.host, err)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Unexpected error in POST %s: %s", endpoint, err)
        return None

    # -------------------------------------------------------------------------
    # Device info
    # -------------------------------------------------------------------------

    async def get_info(self) -> dict[str, Any] | None:
        """Get device info."""
        data = await self._get(API_INFO)
        if data and "info" in data:
            info = data["info"]
            self._device_id = info.get("@deviceID")
            self._device_name = info.get("name")
            self._device_type = info.get("type")
        return data

    # -------------------------------------------------------------------------
    # Playback state
    # -------------------------------------------------------------------------

    async def get_now_playing(self) -> dict[str, Any] | None:
        """Get current playback state."""
        return await self._get(API_NOW_PLAYING)

    async def get_presets(self) -> dict[str, Any] | None:
        """Get stored presets."""
        return await self._get(API_PRESETS)

    async def get_sources(self) -> dict[str, Any] | None:
        """Get available sources."""
        return await self._get(API_SOURCES)

    async def get_recent(self) -> dict[str, Any] | None:
        """Get recently played items."""
        return await self._get(API_RECENT)

    # -------------------------------------------------------------------------
    # Volume
    # -------------------------------------------------------------------------

    async def get_volume(self) -> dict[str, Any] | None:
        """Get current volume."""
        return await self._get(API_VOLUME)

    async def set_volume(self, volume: int) -> dict[str, Any] | None:
        """Set volume (0-100)."""
        volume = max(0, min(100, int(volume)))
        body = f"<volume>{volume}</volume>"
        return await self._post(API_VOLUME, body)

    # -------------------------------------------------------------------------
    # Keys (playback control)
    # -------------------------------------------------------------------------

    async def press_key(self, key: str) -> None:
        """Press and release a key."""
        await self._post_key(key, KEY_STATE_PRESS)
        await self._post_key(key, KEY_STATE_RELEASE)

    async def _post_key(self, key: str, state: str) -> dict[str, Any] | None:
        """Send a key press or release."""
        body = f'<key state="{state}" sender="Gabbo">{key}</key>'
        return await self._post(API_KEY, body)

    # -------------------------------------------------------------------------
    # Source selection
    # -------------------------------------------------------------------------

    async def select_source(
        self,
        source: str,
        source_account: str = "",
        item_name: str = "",
        location: str = "",
        container_art: str = "",
    ) -> dict[str, Any] | None:
        """Select a source."""
        account_attr = f' sourceAccount="{source_account}"' if source_account else ""

        if location:
            # URL-based playback: location and type must be XML attributes on ContentItem
            name = item_name or "Stream"
            body = (
                f'<ContentItem source="{source}"{account_attr}'
                f' location="{location}" type="uri" isPresetable="false">' 
                f"<itemName>{name}</itemName>"
                f"</ContentItem>"
            )
        else:
            # Source-only selection (Bluetooth, AUX, etc.)
            name_elem = f"<itemName>{item_name}</itemName>" if item_name else "<itemName/>"
            art_elem = f"<containerArt>{container_art}</containerArt>" if container_art else ""
            body = (
                f'<ContentItem source="{source}"{account_attr}>' 
                f"{name_elem}"
                f"{art_elem}"
                f"</ContentItem>"
            )
        return await self._post(API_SELECT, body)


    async def play_preset(self, preset_id: int) -> None:
        """Play a preset (1-6)."""
        if 1 <= preset_id <= 6:
            await self.press_key(f"PRESET_{preset_id}")

    # -------------------------------------------------------------------------
    # Bass
    # -------------------------------------------------------------------------

    async def get_bass(self) -> dict[str, Any] | None:
        """Get current bass level."""
        return await self._get(API_BASS)

    async def get_bass_capabilities(self) -> dict[str, Any] | None:
        """Get bass capabilities."""
        return await self._get(API_BASS_CAPABILITIES)

    async def set_bass(self, level: int) -> dict[str, Any] | None:
        """Set bass level."""
        body = f"<bass>{level}</bass>"
        return await self._post(API_BASS, body)

    # -------------------------------------------------------------------------
    # Zone (multi-room)
    # -------------------------------------------------------------------------

    async def get_zone(self) -> dict[str, Any] | None:
        """Get current zone configuration."""
        return await self._get(API_ZONE)

    async def create_zone(
        self, master_id: str, master_ip: str, slave_ids: list[dict]
    ) -> dict[str, Any] | None:
        """Create a multi-room zone."""
        slaves = "".join(
            f'<member ipaddress="{s["ip"]}">{s["id"]}</member>'
            for s in slave_ids
        )
        body = (
            f'<zone master="{master_id}" senderIPAddress="{master_ip}">'
            f"{slaves}"
            f"</zone>"
        )
        return await self._post(API_SET_ZONE, body)

    async def add_zone_slave(
        self, master_id: str, slave_ip: str, slave_id: str
    ) -> dict[str, Any] | None:
        """Add a slave to an existing zone."""
        body = (
            f'<zone master="{master_id}">'
            f'<member ipaddress="{slave_ip}">{slave_id}</member>'
            f"</zone>"
        )
        return await self._post(API_ADD_ZONE_SLAVE, body)

    async def remove_zone_slave(
        self, master_id: str, slave_ip: str, slave_id: str
    ) -> dict[str, Any] | None:
        """Remove a slave from a zone."""
        body = (
            f'<zone master="{master_id}">'
            f'<member ipaddress="{slave_ip}">{slave_id}</member>'
            f"</zone>"
        )
        return await self._post(API_REMOVE_ZONE_SLAVE, body)

    # -------------------------------------------------------------------------
    # WebSocket
    # -------------------------------------------------------------------------

    def register_ws_callback(self, callback: Callable) -> None:
        """Register a callback for WebSocket notifications."""
        self._ws_callbacks.append(callback)

    def unregister_ws_callback(self, callback: Callable) -> None:
        """Unregister a WebSocket notification callback."""
        if callback in self._ws_callbacks:
            self._ws_callbacks.remove(callback)

    async def start_websocket(self) -> None:
        """Start the WebSocket listener."""
        if self._ws_task and not self._ws_task.done():
            return
        self._ws_task = asyncio.create_task(self._ws_listen())

    async def _ws_listen(self) -> None:
        """Listen for WebSocket push notifications from the device."""
        from websockets.client import connect as ws_connect  # pylint: disable=import-outside-toplevel

        ws_url = f"ws://{self.host}:{WEBSOCKET_PORT}/"
        retry_delay = 5

        while True:
            try:
                async with ws_connect(
                    ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    subprotocols=["gabbo"],
                ) as ws:
                    _LOGGER.debug("WebSocket connected to %s", self.host)
                    retry_delay = 5  # reset on successful connection
                    async for message in ws:
                        try:
                            data = xmltodict.parse(message)
                            for callback in self._ws_callbacks:
                                try:
                                    callback(data)
                                except Exception as cb_err:  # pylint: disable=broad-except
                                    _LOGGER.error("WS callback error: %s", cb_err)
                        except Exception as parse_err:  # pylint: disable=broad-except
                            _LOGGER.debug("WS parse error: %s", parse_err)

            except asyncio.CancelledError:
                _LOGGER.debug("WebSocket listener cancelled for %s", self.host)
                return
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.warning(
                    "WebSocket error for %s: %s. Retrying in %ss",
                    self.host,
                    err,
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
