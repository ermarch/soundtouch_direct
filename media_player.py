"""Bose SoundTouch Direct - media_player platform."""
from __future__ import annotations

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
        """Play a piece of media (supports TTS URLs and direct audio URLs)."""
        _LOGGER.debug("play_media called: type=%s id=%s", media_type, media_id)

        # Resolve media-source:// URIs (e.g. TTS) into a playable HTTP URL
        if media_source.is_media_source_id(media_id):
            sourced = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = sourced.url

        # Resolve relative URLs to absolute — must use an IP-based URL the
        # speaker can reach, so prefer internal URL and fall back to external
        if media_id.startswith("/"):
            from homeassistant.helpers.network import get_url, NoURLAvailableError
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
            _LOGGER.warning(
                "SoundTouch can only play HTTP(S) URLs, got: %s", media_id
            )
            return

        _LOGGER.debug("SoundTouch playing URL: %s", media_id)
        item_name = "TTS" if "tts" in media_id.lower() else "Stream"
        await self.coordinator.device.select_source(
            location=media_id,
            item_name=item_name,
        )
        await self.coordinator.async_request_refresh()

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
