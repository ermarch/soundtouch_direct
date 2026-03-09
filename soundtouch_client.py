"""Bose SoundTouch Web API client."""
from __future__ import annotations

import asyncio
import logging
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
    API_SPEAKER,
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

    def _get_session(self) -> aiohttp.ClientSession:
        """Return a valid aiohttp session, creating one if needed."""
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
            session = self._get_session()
            async with session.get(f"{self._base_url}{endpoint}") as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return xmltodict.parse(text)
                _LOGGER.warning("GET %s returned status %s", endpoint, resp.status)
        except aiohttp.ClientError as err:
            _LOGGER.error("HTTP error in GET %s: %s", endpoint, err)
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout in GET %s", endpoint)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Unexpected error in GET %s: %r", endpoint, err)
        return None

    async def _post(self, endpoint: str, body: str) -> dict[str, Any] | None:
        """Make a POST request to the device."""
        try:
            session = self._get_session()
            headers = {"Content-Type": "application/xml"}
            async with session.post(
                f"{self._base_url}{endpoint}", data=body, headers=headers
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    if text:
                        return xmltodict.parse(text)
                    return {}
                response_text = await resp.text()
                _LOGGER.warning(
                    "POST %s returned status %s\nBody: %s\nResponse: %s",
                    endpoint, resp.status, body, response_text,
                )
        except aiohttp.ClientError as err:
            _LOGGER.error("HTTP error in POST %s: %s", endpoint, err)
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout in POST %s", endpoint)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Unexpected error in POST %s: %r", endpoint, err)
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
        return await self._get(API_NOW_PLAYING)

    async def get_presets(self) -> dict[str, Any] | None:
        return await self._get(API_PRESETS)

    async def get_sources(self) -> dict[str, Any] | None:
        return await self._get(API_SOURCES)

    async def get_recent(self) -> dict[str, Any] | None:
        return await self._get(API_RECENT)

    # -------------------------------------------------------------------------
    # Volume
    # -------------------------------------------------------------------------

    async def get_volume(self) -> dict[str, Any] | None:
        return await self._get(API_VOLUME)

    async def set_volume(self, volume: int) -> dict[str, Any] | None:
        volume = max(0, min(100, int(volume)))
        return await self._post(API_VOLUME, f"<volume>{volume}</volume>")

    # -------------------------------------------------------------------------
    # Keys (playback control)
    # -------------------------------------------------------------------------

    async def press_key(self, key: str) -> None:
        """Press and release a key."""
        await self._post_key(key, KEY_STATE_PRESS)
        await self._post_key(key, KEY_STATE_RELEASE)

    async def _post_key(self, key: str, state: str) -> dict[str, Any] | None:
        body = f'<key state="{state}" sender="Gabbo">{key}</key>'
        return await self._post(API_KEY, body)

    # -------------------------------------------------------------------------
    # Source selection
    # -------------------------------------------------------------------------

    async def select_source(
        self,
        source: str = "",
        source_account: str = "",
        item_name: str = "",
        location: str = "",
        container_art: str = "",
        media_type: str = "stationurl",
    ) -> dict[str, Any] | None:
        """Select a source or play a URL."""
        account_attr = f' sourceAccount="{source_account}"' if source_account else ""

        if location:
            # URL-based playback: location and type must be XML *attributes*.
            # Use the provided source (e.g. LOCAL_INTERNET_RADIO) or fall back
            # to TUNEIN for plain audio URL playback.
            src = source if source else "TUNEIN"
            name = item_name or "Stream"
            body = (
                f'<ContentItem source="{src}"{account_attr}'
                f' location="{location}" type="{media_type}" isPresetable="false">'
                f"<itemName>{name}</itemName>"
                f"</ContentItem>"
            )
        else:
            name_elem = f"<itemName>{item_name}</itemName>" if item_name else "<itemName/>"
            art_elem = f"<containerArt>{container_art}</containerArt>" if container_art else ""
            body = (
                f'<ContentItem source="{source}"{account_attr}>'
                f"{name_elem}"
                f"{art_elem}"
                f"</ContentItem>"
            )

        _LOGGER.debug("Sending to /select: %s", body)
        return await self._post(API_SELECT, body)

    async def play_notification(
        self,
        app_key: str,
        url: str,
        volume: int = 0,
        service: str = "HomeAssistant",
        reason: str = "TTS",
        message: str = "",
    ) -> bool:
        """Play a notification using the Bose Notification API (POST /speaker).

        This is the correct way to play one-shot audio on all SoundTouch
        devices. Requires a free app_key from developer.bose.com.
        The device automatically restores the previous source when done.

        volume=0 means 'use current volume'.
        """
        vol_tag = f"<volume>{volume}</volume>" if volume > 0 else ""
        msg_tag = f"<message>{message}</message>" if message else ""
        body = (
            f"<play_info>"
            f"<app_key>{app_key}</app_key>"
            f"<url>{url}</url>"
            f"<service>{service}</service>"
            f"<reason>{reason}</reason>"
            f"{msg_tag}"
            f"{vol_tag}"
            f"</play_info>"
        )
        _LOGGER.debug("play_notification POST /speaker: %s", url)
        result = await self._post(API_SPEAKER, body)
        return result is not None

    async def restore_content_item(self, content_item: dict) -> None:
        """Restore a previously snapshotted ContentItem to /select."""
        source = content_item.get("@source", "")
        source_account = content_item.get("@sourceAccount", "")
        location = content_item.get("@location", "")
        item_name = content_item.get("itemName", "")
        media_type = content_item.get("@type", "")

        await self.select_source(
            source=source,
            source_account=source_account,
            location=location,
            item_name=item_name,
            media_type=media_type or "stationurl",
        )

    async def play_preset(self, preset_id: int) -> None:
        if 1 <= preset_id <= 6:
            await self.press_key(f"PRESET_{preset_id}")

    async def save_preset(self, preset_id: int, content_item: dict) -> None:
        """Save a ContentItem to a preset slot (1-6)."""
        if not 1 <= preset_id <= 6:
            raise ValueError(f"preset_id must be 1-6, got {preset_id}")
        source = content_item.get("@source", "")
        location = content_item.get("@location", "")
        # SoundTouch firmware rejects HTTPS URLs.
        if location.startswith("https://"):
            location = "http://" + location[8:]
        account = content_item.get("@sourceAccount", "")
        media_type = content_item.get("@type", "")
        item_name = content_item.get("itemName", "") or (location.split("/")[2] if location else "")
        account_attr = f' sourceAccount="{account}"' if account else ""
        location_attr = f' location="{location}"' if location else ""
        type_attr = f' type="{media_type}"' if media_type else ""
        # Use /storePreset endpoint with <preset> wrapper (no outer <presets> tag).
        body = (
            f'<preset id="{preset_id}">'
            f'<ContentItem source="{source}"{location_attr}{account_attr}{type_attr} isPresetable="true">'
            f"<itemName>{item_name}</itemName>"
            f"</ContentItem>"
            f"</preset>"
        )
        await self._post("/storePreset", body)

    # -------------------------------------------------------------------------
    # Bass
    # -------------------------------------------------------------------------

    async def get_bass(self) -> dict[str, Any] | None:
        return await self._get(API_BASS)

    async def get_bass_capabilities(self) -> dict[str, Any] | None:
        return await self._get(API_BASS_CAPABILITIES)

    async def set_bass(self, level: int) -> dict[str, Any] | None:
        return await self._post(API_BASS, f"<bass>{level}</bass>")

    # -------------------------------------------------------------------------
    # Zone (multi-room)
    # -------------------------------------------------------------------------

    async def get_zone(self) -> dict[str, Any] | None:
        return await self._get(API_ZONE)

    async def create_zone(
        self, master_id: str, master_ip: str, slave_ids: list[dict]
    ) -> dict[str, Any] | None:
        slaves = "".join(
            f'<member ipaddress="{s["ip"]}">{s["id"]}</member>'
            for s in slave_ids
        )
        body = (
            f'<zone master="{master_id}" senderIPAddress="{master_ip}">'
            f"{slaves}</zone>"
        )
        return await self._post(API_SET_ZONE, body)

    async def add_zone_slave(
        self, master_id: str, slave_ip: str, slave_id: str
    ) -> dict[str, Any] | None:
        body = (
            f'<zone master="{master_id}">'
            f'<member ipaddress="{slave_ip}">{slave_id}</member></zone>'
        )
        return await self._post(API_ADD_ZONE_SLAVE, body)

    async def remove_zone_slave(
        self, master_id: str, slave_ip: str, slave_id: str
    ) -> dict[str, Any] | None:
        body = (
            f'<zone master="{master_id}">'
            f'<member ipaddress="{slave_ip}">{slave_id}</member></zone>'
        )
        return await self._post(API_REMOVE_ZONE_SLAVE, body)

    # -------------------------------------------------------------------------
    # WebSocket
    # -------------------------------------------------------------------------

    def register_ws_callback(self, callback: Callable) -> None:
        self._ws_callbacks.append(callback)

    def unregister_ws_callback(self, callback: Callable) -> None:
        if callback in self._ws_callbacks:
            self._ws_callbacks.remove(callback)

    async def start_websocket(self) -> None:
        if self._ws_task and not self._ws_task.done():
            return
        self._ws_task = asyncio.create_task(self._ws_listen())

    async def _ws_listen(self) -> None:
        """Listen for WebSocket push notifications from the device.

        The SoundTouch device does NOT respond to WebSocket ping frames, so
        ping_interval and ping_timeout must be disabled to prevent the
        websockets library from closing the connection with a 1011 error.
        """
        from websockets.client import connect as ws_connect  # pylint: disable=import-outside-toplevel

        ws_url = f"ws://{self.host}:{WEBSOCKET_PORT}/"
        retry_delay = 5

        while True:
            try:
                async with ws_connect(
                    ws_url,
                    ping_interval=None,   # SoundTouch does not respond to pings
                    ping_timeout=None,
                    subprotocols=["gabbo"],
                ) as ws:
                    _LOGGER.debug("WebSocket connected to %s", self.host)
                    retry_delay = 5
                    async for message in ws:
                        try:
                            data = xmltodict.parse(message)
                            for callback in self._ws_callbacks:
                                try:
                                    callback(data)
                                except Exception as cb_err:  # pylint: disable=broad-except
                                    _LOGGER.error("WS callback error: %r", cb_err)
                        except Exception as parse_err:  # pylint: disable=broad-except
                            _LOGGER.debug("WS parse error: %r", parse_err)

            except asyncio.CancelledError:
                _LOGGER.debug("WebSocket listener cancelled for %s", self.host)
                return
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.warning(
                    "WebSocket error for %s: %s. Retrying in %ss",
                    self.host, err, retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
