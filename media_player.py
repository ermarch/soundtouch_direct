"""Bose SoundTouch Direct - media_player platform."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.components import media_source
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_BASS_LEVEL,
    ATTR_MASTER,
    ATTR_PRESET_ID,
    ATTR_SLAVES,
    DOMAIN,
    KEY_NEXT_TRACK,
    KEY_PAUSE,
    KEY_PLAY,
    KEY_PLAY_PAUSE,
    KEY_POWER,
    KEY_PREV_TRACK,
    KEY_SHUFFLE_OFF,
    KEY_SHUFFLE_ON,
    KEY_STOP,
    KEY_THUMBS_DOWN,
    KEY_THUMBS_UP,
    KEY_ADD_FAVORITE,
    KEY_REMOVE_FAVORITE,
    PLAY_STATUS_BUFFERING,
    PLAY_STATUS_PAUSE,
    PLAY_STATUS_PLAY,
    PLAY_STATUS_STOP,
    SERVICE_ADD_FAVORITE,
    SERVICE_ADD_ZONE_SLAVE,
    SERVICE_CREATE_ZONE,
    SERVICE_PLAY_EVERYWHERE,
    SERVICE_PLAY_PRESET,
    SERVICE_REMOVE_FAVORITE,
    SERVICE_REMOVE_ZONE_SLAVE,
    SERVICE_SET_BASS,
    SERVICE_THUMBS_DOWN,
    SERVICE_THUMBS_UP,
    SOURCE_STANDBY,
)
from .coordinator import SoundTouchCoordinator

_LOGGER = logging.getLogger(__name__)

SUPPORT_SOUNDTOUCH = (
    MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.SHUFFLE_SET
    | MediaPlayerEntityFeature.REPEAT_SET
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.BROWSE_MEDIA
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up the SoundTouch media player from config entry."""
    coordinator: SoundTouchCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SoundTouchMediaPlayer(coordinator, entry)])

    # Register custom services
    platform = entity_platform.async_get_current_platform()

    platform.async_register_entity_service(
        SERVICE_PLAY_PRESET,
        {vol.Required(ATTR_PRESET_ID): vol.All(vol.Coerce(int), vol.Range(min=1, max=6))},
        "async_play_preset",
    )
    platform.async_register_entity_service(
        SERVICE_SET_BASS,
        {vol.Required(ATTR_BASS_LEVEL): vol.All(vol.Coerce(int), vol.Range(min=-9, max=9))},
        "async_set_bass",
    )
    platform.async_register_entity_service(
        SERVICE_CREATE_ZONE,
        {
            vol.Required(ATTR_MASTER): cv.string,
            vol.Required(ATTR_SLAVES): vol.All(cv.ensure_list, [cv.string]),
        },
        "async_create_zone",
    )
    platform.async_register_entity_service(
        SERVICE_ADD_ZONE_SLAVE,
        {vol.Required(ATTR_SLAVES): vol.All(cv.ensure_list, [cv.string])},
        "async_add_zone_slave",
    )
    platform.async_register_entity_service(
        SERVICE_REMOVE_ZONE_SLAVE,
        {vol.Required(ATTR_SLAVES): vol.All(cv.ensure_list, [cv.string])},
        "async_remove_zone_slave",
    )
    platform.async_register_entity_service(
        SERVICE_PLAY_EVERYWHERE,
        {},
        "async_play_everywhere",
    )
    platform.async_register_entity_service(
        SERVICE_THUMBS_UP,
        {},
        "async_thumbs_up",
    )
    platform.async_register_entity_service(
        SERVICE_THUMBS_DOWN,
        {},
        "async_thumbs_down",
    )
    platform.async_register_entity_service(
        SERVICE_ADD_FAVORITE,
        {},
        "async_add_favorite",
    )
    platform.async_register_entity_service(
        SERVICE_REMOVE_FAVORITE,
        {},
        "async_remove_favorite",
    )


async def _is_live_stream(url: str, ha_base_url: str = "") -> bool:
    """Return True if the URL is a live/infinite stream (e.g. internet radio).

    HA TTS/proxy paths are always finite. External audio without Content-Length
    or with icy-* headers is a live stream.
    """
    import aiohttp
    from urllib.parse import urlparse

    # HA TTS and media proxy paths are always finite audio files
    parsed = urlparse(url)
    HA_LOCAL_PATHS = ("/api/tts_proxy/", "/api/tts/", "/api/soundtouch_direct/")
    if any(parsed.path.startswith(p) for p in HA_LOCAL_PATHS):
        return False

    # Also match by host if we know HA's base URL
    if ha_base_url:
        ha_host = urlparse(ha_base_url).netloc
        if ha_host and parsed.netloc == ha_host:
            return False

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(connect=5, sock_read=3),
            ) as resp:
                if resp.headers.get("icy-name") or resp.headers.get("icy-genre"):
                    return True
                content_type = resp.headers.get("Content-Type", "")
                content_length = resp.headers.get("Content-Length")
                if content_type.startswith("audio/") and not content_length:
                    return True
                return False
    except Exception:  # pylint: disable=broad-except
        return False



class SoundTouchMediaPlayer(CoordinatorEntity[SoundTouchCoordinator], MediaPlayerEntity):
    """Representation of a Bose SoundTouch device as a media player."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = SUPPORT_SOUNDTOUCH
    _attr_media_content_type = MediaType.MUSIC

    def __init__(self, coordinator: SoundTouchCoordinator, entry: ConfigEntry) -> None:
        """Initialize the media player."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = entry.data.get("device_id", entry.entry_id)
        # Track the last real (non-TTS) ContentItem for snapshot/restore.
        # Stored in hass.data so it survives integration reloads.
        self._last_real_content_item: dict | None = None

    async def async_added_to_hass(self) -> None:
        """Restore last stream URL from config entry options on startup."""
        await super().async_added_to_hass()
        stored_url = self._entry.options.get("last_url")
        if stored_url:
            self.hass.data.setdefault(DOMAIN, {})[f"last_url_{self._attr_unique_id}"] = stored_url
            _LOGGER.debug("SoundTouch: loaded last_url from config entry: %s", stored_url)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        info = self.coordinator.data.get("info") or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=info.get("name", self._entry.title),
            manufacturer="Bose",
            model=info.get("type", "SoundTouch"),
            sw_version=self._get_sw_version(info),
            configuration_url=f"http://{self._entry.data[CONF_HOST]}:8090",
        )

    def _get_sw_version(self, info: dict) -> str | None:
        """Extract firmware version from info."""
        components = info.get("components", {})
        if isinstance(components, dict):
            component = components.get("component")
            if isinstance(component, list):
                for c in component:
                    if c.get("componentCategory") == "SCM":
                        return c.get("softwareVersion")
            elif isinstance(component, dict):
                return component.get("softwareVersion")
        return None

    # -------------------------------------------------------------------------
    # State properties
    # -------------------------------------------------------------------------

    @callback
    @callback
    def _handle_coordinator_update(self) -> None:
        """Update state and track last real (non-TTS) content item."""
        now_playing = self.coordinator.data.get("now_playing") or {}
        source = now_playing.get("@source", "")
        if source and source not in ("STANDBY", "INVALID_SOURCE", "LOCAL_INTERNET_RADIO", ""):
            content_item = now_playing.get("ContentItem")
            if isinstance(content_item, dict) and content_item.get("@source"):
                self._last_real_content_item = content_item
            # Cancel any pending restore if the user manually changed source
            if self._restore_task and not self._restore_task.done():
                _LOGGER.debug("SoundTouch: manual source change, cancelling pending restore")
                self._restore_task.cancel()
                self._restore_task = None
        self.async_write_ha_state()


    @property
    def _now_playing(self) -> dict:
        return self.coordinator.data.get("now_playing") or {}

    @property
    def _volume_data(self) -> dict:
        return self.coordinator.data.get("volume") or {}

    @property
    def _presets_data(self) -> dict:
        return self.coordinator.data.get("presets") or {}

    @property
    def _sources_data(self) -> dict:
        return self.coordinator.data.get("sources") or {}

    @property
    def state(self) -> MediaPlayerState | None:
        """Return the state of the device."""
        source = self._now_playing.get("@source", "")
        if source == SOURCE_STANDBY or not source:
            return MediaPlayerState.OFF

        play_status = self._now_playing.get("playStatus", "")
        if play_status == PLAY_STATUS_PLAY:
            return MediaPlayerState.PLAYING
        if play_status == PLAY_STATUS_PAUSE:
            return MediaPlayerState.PAUSED
        if play_status == PLAY_STATUS_STOP:
            return MediaPlayerState.IDLE
        if play_status == PLAY_STATUS_BUFFERING:
            return MediaPlayerState.BUFFERING

        return MediaPlayerState.IDLE

    @property
    def volume_level(self) -> float | None:
        """Return the volume level (0.0 to 1.0)."""
        actual = self._volume_data.get("actualvolume")
        if actual is not None:
            try:
                return int(actual) / 100
            except (ValueError, TypeError):
                pass
        return None

    @property
    def is_volume_muted(self) -> bool | None:
        """Return True if volume is muted."""
        muted = self._volume_data.get("muteenabled")
        if muted is not None:
            return str(muted).lower() == "true"
        return None

    @property
    def media_content_type(self) -> MediaType | None:
        """Return the content type of current media."""
        source = self._now_playing.get("@source", "")
        if source in ("INTERNET_RADIO",):
            return MediaType.MUSIC
        return MediaType.MUSIC

    @property
    def media_title(self) -> str | None:
        """Return the title of current media."""
        return self._now_playing.get("song") or self._now_playing.get("stationName")

    @property
    def media_artist(self) -> str | None:
        """Return the artist of current media."""
        return self._now_playing.get("artist")

    @property
    def media_album_name(self) -> str | None:
        """Return the album of current media."""
        return self._now_playing.get("album")

    @property
    def media_image_url(self) -> str | None:
        """Return the URL of the media cover art."""
        art = self._now_playing.get("art")
        if isinstance(art, dict):
            url = art.get("#text") or art.get("@url")
            if url and art.get("@artImageStatus") == "IMAGE_PRESENT":
                return url
        elif isinstance(art, str):
            return art
        return None

    @property
    def media_duration(self) -> int | None:
        """Return duration of current media."""
        duration = self._now_playing.get("time", {})
        if isinstance(duration, dict):
            total = duration.get("@total")
            if total:
                try:
                    return int(total)
                except (ValueError, TypeError):
                    pass
        return None

    @property
    def media_position(self) -> int | None:
        """Return position of current media."""
        duration = self._now_playing.get("time", {})
        if isinstance(duration, dict):
            pos = duration.get("#text")
            if pos:
                try:
                    return int(pos)
                except (ValueError, TypeError):
                    pass
        return None

    @property
    def source(self) -> str | None:
        """Return the current source."""
        return self._now_playing.get("@source")

    @property
    def source_list(self) -> list[str] | None:
        """Return a list of available input sources."""
        sources_data = self._sources_data
        source_items = sources_data.get("sourceItem")
        if not source_items:
            return None
        if isinstance(source_items, dict):
            source_items = [source_items]
        return [
            s.get("@source", "")
            for s in source_items
            if s.get("@status") == "READY" and s.get("@source") != SOURCE_STANDBY
        ]

    @property
    def shuffle(self) -> bool | None:
        """Return True if shuffle is on."""
        shuffle = self._now_playing.get("shuffleSetting")
        if shuffle:
            return shuffle == "SHUFFLE_ON"
        return None

    @property
    def repeat(self) -> RepeatMode | None:
        """Return current repeat mode."""
        repeat = self._now_playing.get("repeatSetting")
        if repeat == "REPEAT_ALL":
            return RepeatMode.ALL
        if repeat == "REPEAT_ONE":
            return RepeatMode.ONE
        if repeat == "REPEAT_OFF":
            return RepeatMode.OFF
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {}

        # Station name for radio
        station = self._now_playing.get("stationName")
        if station:
            attrs["station_name"] = station

        # Station location
        station_location = self._now_playing.get("stationLocation")
        if station_location:
            attrs["station_location"] = station_location

        # Presets
        preset_list = self._presets_data.get("preset")
        if preset_list:
            if isinstance(preset_list, dict):
                preset_list = [preset_list]
            attrs["presets"] = [
                {
                    "id": int(p.get("@id", 0)),
                    "name": (p.get("ContentItem", {}) or {}).get("itemName", ""),
                    "source": (p.get("ContentItem", {}) or {}).get("@source", ""),
                }
                for p in preset_list
                if isinstance(p, dict)
            ]

        # Source account (e.g. Spotify username)
        source_account = self._now_playing.get("@sourceAccount")
        if source_account:
            attrs["source_account"] = source_account

        return attrs

    # -------------------------------------------------------------------------
    # Commands
    # -------------------------------------------------------------------------

    async def async_turn_on(self) -> None:
        """Turn the device on."""
        await self.coordinator.device.press_key(KEY_POWER)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        """Turn the device off (standby)."""
        source = self._now_playing.get("@source", "")
        if source != SOURCE_STANDBY:
            await self.coordinator.device.press_key(KEY_POWER)
            await self.coordinator.async_request_refresh()

    async def async_media_play(self) -> None:
        """Send play command."""
        await self.coordinator.device.press_key(KEY_PLAY)
        await self.coordinator.async_request_refresh()

    async def async_media_pause(self) -> None:
        """Send pause command."""
        await self.coordinator.device.press_key(KEY_PAUSE)
        await self.coordinator.async_request_refresh()

    async def async_media_stop(self) -> None:
        """Send stop command."""
        await self.coordinator.device.press_key(KEY_STOP)
        await self.coordinator.async_request_refresh()

    async def async_media_play_pause(self) -> None:
        """Toggle play/pause."""
        await self.coordinator.device.press_key(KEY_PLAY_PAUSE)
        await self.coordinator.async_request_refresh()

    async def async_media_next_track(self) -> None:
        """Send next track command."""
        await self.coordinator.device.press_key(KEY_NEXT_TRACK)
        await self.coordinator.async_request_refresh()

    async def async_media_previous_track(self) -> None:
        """Send previous track command."""
        await self.coordinator.device.press_key(KEY_PREV_TRACK)
        await self.coordinator.async_request_refresh()

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level (0.0 - 1.0)."""
        await self.coordinator.device.set_volume(int(volume * 100))
        await self.coordinator.async_request_refresh()

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute the volume."""
        from .const import KEY_MUTE
        await self.coordinator.device.press_key(KEY_MUTE)
        await self.coordinator.async_request_refresh()

    async def async_select_source(self, source: str) -> None:
        """Select an input source."""
        # Find source details from the sources list
        sources_data = self._sources_data
        source_items = sources_data.get("sourceItem")
        if source_items:
            if isinstance(source_items, dict):
                source_items = [source_items]
            for item in source_items:
                if item.get("@source") == source:
                    await self.coordinator.device.select_source(
                        source=source,
                        source_account=item.get("@sourceAccount", ""),
                        item_name=item.get("#text", source),
                    )
                    await self.coordinator.async_request_refresh()
                    return

        # Fallback
        await self.coordinator.device.select_source(source=source)
        await self.coordinator.async_request_refresh()

    async def async_set_shuffle(self, shuffle: bool) -> None:
        """Enable or disable shuffle."""
        key = KEY_SHUFFLE_ON if shuffle else KEY_SHUFFLE_OFF
        await self.coordinator.device.press_key(key)
        await self.coordinator.async_request_refresh()

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        """Set repeat mode."""
        from .const import KEY_REPEAT_ALL, KEY_REPEAT_ONE, KEY_REPEAT_OFF
        key_map = {
            RepeatMode.ALL: KEY_REPEAT_ALL,
            RepeatMode.ONE: KEY_REPEAT_ONE,
            RepeatMode.OFF: KEY_REPEAT_OFF,
        }
        key = key_map.get(repeat)
        if key:
            await self.coordinator.device.press_key(key)
            await self.coordinator.async_request_refresh()

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Implement the websocket media browsing helper (required for TTS target)."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a piece of media (TTS or direct audio URL).

        For finite audio (TTS): snapshot current state, play, restore when done.
        For live streams: pass URL directly, no snapshot needed.
        """
        import secrets
        from homeassistant.helpers.network import get_url, NoURLAvailableError
        from .__init__ import STREAM_PROXY_KEY
        from .const import CONF_APP_KEY, DOMAIN


        # Resolve media-source:// URIs (e.g. TTS) into a real HTTP URL
        if media_source.is_media_source_id(media_id):
            sourced = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = sourced.url

        # Resolve relative paths to absolute internal URLs
        if media_id.startswith("/"):
            try:
                base = get_url(self.hass, allow_internal=True, allow_ip=True, prefer_external=False)
            except NoURLAvailableError:
                try:
                    base = get_url(self.hass, allow_external=True)
                except NoURLAvailableError:
                    _LOGGER.error("Cannot determine HA base URL for TTS playback")
                    return
            media_id = f"{base}{media_id}"

        if not media_id.startswith(("http://", "https://")):
            _LOGGER.warning("SoundTouch: unplayable media_id: %s", media_id)
            return

        # Prefer the Bose Notification API when an app_key is available.
        app_key = self.coordinator.config_entry.options.get(
            CONF_APP_KEY,
            self.coordinator.config_entry.data.get(CONF_APP_KEY, ""),
        )
        if app_key:
            _LOGGER.debug("SoundTouch: using Notification API for %s", media_id)
            success = await self.coordinator.device.play_notification(
                app_key=app_key,
                url=media_id,
            )
            if success:
                await self.coordinator.async_request_refresh()
                return
            _LOGGER.warning("SoundTouch: Notification API failed, falling back to stream proxy")

        # --- Stream proxy fallback ---
        proxy = self.hass.data.get(DOMAIN, {}).get(STREAM_PROXY_KEY)
        if proxy is None:
            _LOGGER.error("SoundTouch stream proxy not initialised and no app_key set")
            return

        # Build base URL — force HTTP, SoundTouch firmware rejects HTTPS stream URLs.
        try:
            base = get_url(self.hass, allow_internal=True, allow_ip=True, prefer_external=False)
        except NoURLAvailableError:
            base = get_url(self.hass, allow_external=True)
        if base.startswith("https://"):
            base = "http://" + base[8:]
        base = base.rstrip("/")

        # Store any external URL as the last played source for restore purposes.
        # We do this before the live/TTS split so it works regardless of detection.
        parsed_media = __import__("urllib.parse", fromlist=["urlparse"]).urlparse(media_id)
        HA_LOCAL_PATHS = ("/api/tts_proxy/", "/api/tts/", "/api/soundtouch_direct/")
        _LOGGER.warning("play_media: ENTER media_id=%s path=%s existing_last_url=%s",
            media_id, parsed_media.path,
            self.hass.data.get(DOMAIN, {}).get(f"last_url_{self._attr_unique_id}"))
        if not any(parsed_media.path.startswith(p) for p in HA_LOCAL_PATHS):
            self.hass.data.setdefault(DOMAIN, {})[f"last_url_{self._attr_unique_id}"] = media_id
            _LOGGER.warning("play_media: stored last_url=%s", media_id)
            # Persist to config entry so it survives HA restarts.
            self.hass.config_entries.async_update_entry(
                self._entry, options={**self._entry.options, "last_url": media_id}
            )
        else:
            _LOGGER.warning("play_media: skipped storing (HA local path)")
    
        # Detect live/infinite streams — they skip snapshot/restore and pre-fetch.
        is_live = await _is_live_stream(media_id, base)

        token = secrets.token_urlsafe(12)

        if is_live:
            direct_url = media_id
            if direct_url.startswith("https://"):
                direct_url = "http://" + direct_url[8:]
            proxy.register_direct(token, direct_url)
            _LOGGER.warning("SoundTouch: live stream, passing URL directly: %s", direct_url)

            station_url = f"{base}/api/soundtouch_direct/station/{token}.json"
            await self.coordinator.device.select_source(
                source="LOCAL_INTERNET_RADIO",
                source_account="",
                location=station_url,
                item_name="Radio",
                media_type="stationurl",
            )
            await self.coordinator.async_request_refresh()
            return

        # TTS: snapshot current state, play directly, then restore.
        # Use _last_real_media_url if available (live stream we played via play_media),
        # otherwise fall back to _last_real_content_item (preset, Spotify, etc).
        _LOGGER.warning("SoundTouch: restore lookup key=last_url_%s domain_keys=%s",
            self._attr_unique_id,
            list(self.hass.data.get(DOMAIN, {}).keys()))
        restore_url = self.hass.data.get(DOMAIN, {}).get(f"last_url_{self._attr_unique_id}")
        snapshot = self._last_real_content_item if not restore_url else None
        if restore_url:
            _LOGGER.warning("SoundTouch: will restore live stream URL: %s", restore_url)
        elif snapshot:
            _LOGGER.warning(
                "SoundTouch: will restore ContentItem source=%s location=%s",
                snapshot.get("@source"), snapshot.get("@location"),
            )
        else:
            _LOGGER.warning("SoundTouch: no previous source to restore")

        # Point the JSON descriptor directly at the TTS URL (force HTTP).
        # The device fetches it natively — no proxy stream required.
        tts_url = media_id
        if tts_url.startswith("https://"):
            tts_url = "http://" + tts_url[8:]
        proxy.register_direct(token, tts_url)

        station_url = f"{base}/api/soundtouch_direct/station/{token}.json"
        _LOGGER.warning("SoundTouch: TTS station URL: %s -> %s", station_url, tts_url)

        await self.coordinator.device.select_source(
            source="LOCAL_INTERNET_RADIO",
            source_account="",
            location=station_url,
            item_name="TTS",
            media_type="stationurl",
        )
        await self.coordinator.async_request_refresh()

        # Watch for TTS completion via WebSocket then restore previous source.
        if restore_url or snapshot:
            self._restore_task = self.hass.async_create_task(
                self._restore_after_tts(token, proxy, restore_url, snapshot, tts_url),
                name="soundtouch_restore",
            )

    async def _restore_after_tts(
        self,
        token: str,
        proxy: Any,
        restore_url: str | None,
        snapshot: dict | None,
        tts_url: str = "",
    ) -> None:
        """Wait for TTS to finish then restore the previous source.

        Estimates TTS duration from MP3 size and fires restore 1s early.
        WS STANDBY event is used as fallback if estimate is too short.
        Restore strategy:
          - restore_url set: send /select directly with the original live stream URL
          - snapshot set: use restore_content_item to replay the ContentItem directly
        """
        _LOGGER.warning("SoundTouch: _restore_after_tts started, restore_url=%s", restore_url)
        import aiohttp, time as _time
        try:
            done = asyncio.Event()
            _tts_start = _time.monotonic()

            def _on_update() -> None:
                """Fired by coordinator on every nowPlaying WS push."""
                now = self.coordinator.data or {}
                np = now.get("now_playing", {})
                source = np.get("@source", "")
                if source in ("STANDBY", "INVALID_SOURCE", ""):
                    _LOGGER.warning("SoundTouch: WS STANDBY after %.1fs", _time.monotonic() - _tts_start)
                    done.set()

            remove_listener = self.coordinator.async_add_listener(_on_update)

            # Estimate TTS duration from MP3 size, fire restore 1s before expected end.
            # WS STANDBY event will also trigger restore if it fires first.
            early_wait = None
            if tts_url:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(tts_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                            if resp.status == 200:
                                data = await resp.read()
                                # Detect actual MP3 bitrate from frame header instead of assuming 128kbps.
                                _BITRATES = {
                                    0x1: 32, 0x2: 40, 0x3: 48, 0x4: 56, 0x5: 64,
                                    0x6: 80, 0x7: 96, 0x8: 112, 0x9: 128, 0xA: 160,
                                    0xB: 192, 0xC: 224, 0xD: 256, 0xE: 320,
                                }
                                bitrate = 64  # conservative default
                                for _i in range(min(len(data) - 3, 8192)):
                                    if data[_i] == 0xFF and (data[_i + 1] & 0xE0) == 0xE0:
                                        _br = _BITRATES.get((data[_i + 2] >> 4) & 0xF)
                                        if _br:
                                            bitrate = _br
                                            break
                                duration = len(data) / (bitrate * 125)
                                early_wait = duration + 0.5
                                _LOGGER.warning("SoundTouch: TTS %.1fs @ %dkbps, firing restore in %.1fs", duration, bitrate, early_wait)
                except Exception as err:
                    _LOGGER.warning("SoundTouch: TTS size probe failed: %r, using WS only", err)

            try:
                if early_wait is not None:
                    # Race: whichever fires first — early timer or WS STANDBY
                    await asyncio.wait_for(done.wait(), timeout=early_wait)
                    _LOGGER.warning("SoundTouch: WS beat timer, restoring now")
                else:
                    await asyncio.wait_for(done.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                _LOGGER.warning("SoundTouch: timer elapsed at %.1fs, restoring early", _time.monotonic() - _tts_start)
            finally:
                remove_listener()

            proxy.unregister(token)
            _LOGGER.warning("SoundTouch: sending restore /select at %.1fs", _time.monotonic() - _tts_start)

            if restore_url:
                _LOGGER.warning("SoundTouch: restoring live stream directly: %s", restore_url)
                # Force HTTP — SoundTouch rejects HTTPS stream URLs.
                direct_url = restore_url
                if direct_url.startswith("https://"):
                    direct_url = "http://" + direct_url[8:]
                import secrets as _secrets
                restore_token = _secrets.token_urlsafe(12)
                proxy.register_direct(restore_token, direct_url)
                _base = self.hass.data.get(DOMAIN, {}).get("ha_base_url", "")
                if not _base:
                    from homeassistant.helpers.network import get_url, NoURLAvailableError
                    try:
                        _base = get_url(self.hass, allow_internal=True, allow_ip=True, prefer_external=False)
                    except NoURLAvailableError:
                        _base = get_url(self.hass, allow_external=True)
                    if _base.startswith("https://"):
                        _base = "http://" + _base[8:]
                    _base = _base.rstrip("/")
                station_url = f"{_base}/api/soundtouch_direct/station/{restore_token}.json"
                await self.coordinator.device.select_source(
                    source="LOCAL_INTERNET_RADIO",
                    source_account="",
                    location=station_url,
                    item_name="Radio",
                    media_type="stationurl",
                )
                await self.coordinator.async_request_refresh()
            elif snapshot:
                _LOGGER.warning(
                    "SoundTouch: restoring ContentItem source=%s location=%s",
                    snapshot.get("@source"), snapshot.get("@location"),
                )
                await self.coordinator.device.restore_content_item(snapshot)
                await self.coordinator.async_request_refresh()

            _LOGGER.warning("SoundTouch: restore complete")
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("SoundTouch: _restore_after_tts crashed: %r", err)

    # -------------------------------------------------------------------------
    # Custom services
    # -------------------------------------------------------------------------

    async def async_play_preset(self, preset_id: int) -> None:
        """Play a stored preset (1-6)."""
        await self.coordinator.device.play_preset(preset_id)
        await self.coordinator.async_request_refresh()

    async def async_set_bass(self, bass_level: int) -> None:
        """Set bass level (-9 to 9)."""
        await self.coordinator.device.set_bass(bass_level)

    async def async_thumbs_up(self) -> None:
        """Send thumbs up (like) for the current track."""
        await self.coordinator.device.press_key(KEY_THUMBS_UP)

    async def async_thumbs_down(self) -> None:
        """Send thumbs down (dislike) for the current track."""
        await self.coordinator.device.press_key(KEY_THUMBS_DOWN)

    async def async_add_favorite(self) -> None:
        """Add current track to favorites."""
        await self.coordinator.device.press_key(KEY_ADD_FAVORITE)

    async def async_remove_favorite(self) -> None:
        """Remove current track from favorites."""
        await self.coordinator.device.press_key(KEY_REMOVE_FAVORITE)

    async def async_create_zone(self, master: str, slaves: list[str]) -> None:
        """Create a multi-room zone with this device as master."""
        hass_data = self.hass.data.get(DOMAIN, {})
        slave_list = []
        for entry_id, coord in hass_data.items():
            if coord.device.device_id in slaves:
                slave_list.append(
                    {"id": coord.device.device_id, "ip": coord.device.host}
                )
        if slave_list and self.coordinator.device.device_id:
            await self.coordinator.device.create_zone(
                master_id=self.coordinator.device.device_id,
                master_ip=self.coordinator.device.host,
                slave_ids=slave_list,
            )
            await self.coordinator.async_request_refresh()

    async def async_add_zone_slave(self, slaves: list[str]) -> None:
        """Add a slave device to this zone."""
        hass_data = self.hass.data.get(DOMAIN, {})
        for entry_id, coord in hass_data.items():
            if coord.device.device_id in slaves:
                await self.coordinator.device.add_zone_slave(
                    master_id=self.coordinator.device.device_id,
                    slave_ip=coord.device.host,
                    slave_id=coord.device.device_id,
                )

    async def async_remove_zone_slave(self, slaves: list[str]) -> None:
        """Remove a slave device from this zone."""
        hass_data = self.hass.data.get(DOMAIN, {})
        for entry_id, coord in hass_data.items():
            if coord.device.device_id in slaves:
                await self.coordinator.device.remove_zone_slave(
                    master_id=self.coordinator.device.device_id,
                    slave_ip=coord.device.host,
                    slave_id=coord.device.device_id,
                )

    async def async_play_everywhere(self) -> None:
        """Set this device as master and add all other SoundTouch devices as slaves."""
        hass_data = self.hass.data.get(DOMAIN, {})
        slave_list = [
            {"id": coord.device.device_id, "ip": coord.device.host}
            for entry_id, coord in hass_data.items()
            if coord.device.device_id != self.coordinator.device.device_id
            and coord.device.device_id is not None
        ]
        if self.coordinator.device.device_id:
            await self.coordinator.device.create_zone(
                master_id=self.coordinator.device.device_id,
                master_ip=self.coordinator.device.host,
                slave_ids=slave_list,
            )
            await self.coordinator.async_request_refresh()
