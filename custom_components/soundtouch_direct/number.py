"""Number entity for Bose SoundTouch bass level control."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SoundTouchCoordinator

_LOGGER = logging.getLogger(__name__)

DEFAULT_BASS_MIN = -9
DEFAULT_BASS_MAX = 9


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SoundTouch bass number entity."""
    coordinator: SoundTouchCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Fetch bass capabilities to get min/max for this device.
    bass_min = DEFAULT_BASS_MIN
    bass_max = DEFAULT_BASS_MAX
    try:
        caps = await coordinator.device.get_bass_capabilities()
        if caps:
            bc = caps.get("bassCapabilities", {})
            if bc.get("bassMin") is not None:
                bass_min = int(bc["bassMin"])
            if bc.get("bassMax") is not None:
                bass_max = int(bc["bassMax"])
    except Exception:  # pylint: disable=broad-except
        pass

    async_add_entities([SoundTouchBassNumber(coordinator, entry, bass_min, bass_max)])


class SoundTouchBassNumber(CoordinatorEntity, NumberEntity):
    """Bass level control for a SoundTouch speaker."""

    _attr_has_entity_name = True
    _attr_name = "Bass"
    _attr_icon = "mdi:equalizer"
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1.0

    def __init__(
        self,
        coordinator: SoundTouchCoordinator,
        entry: ConfigEntry,
        bass_min: int,
        bass_max: int,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = entry.data.get("device_id", entry.entry_id)
        self._attr_unique_id = f"{self._device_id}_bass"
        self._attr_native_min_value = float(bass_min)
        self._attr_native_max_value = float(bass_max)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._device_id)})

    @property
    def native_value(self) -> float | None:
        bass = self.coordinator.data.get("bass") or {}
        val = bass.get("@target") or bass.get("#text")
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
        return None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.device.set_bass(int(value))
        await self.coordinator.async_request_refresh()
