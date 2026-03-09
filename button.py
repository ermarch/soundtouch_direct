"""Button entities for Bose SoundTouch presets."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SoundTouchCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SoundTouch preset button entities."""
    coordinator: SoundTouchCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = entry.data.get("device_id", entry.entry_id)

    # Create one button per preset slot (1-6).
    # Names are updated dynamically from coordinator data.
    async_add_entities(
        [SoundTouchPresetButton(coordinator, entry, device_id, i) for i in range(1, 7)]
    )


class SoundTouchPresetButton(CoordinatorEntity, ButtonEntity):
    """A button that triggers one of the 6 SoundTouch presets."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:radio"

    def __init__(
        self,
        coordinator: SoundTouchCoordinator,
        entry: ConfigEntry,
        device_id: str,
        preset_id: int,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._preset_id = preset_id
        self._attr_unique_id = f"{device_id}_preset_{preset_id}"

    @property
    def name(self) -> str:
        """Return preset name from coordinator data, fall back to 'Preset N'."""
        presets = self.coordinator.data.get("presets") or {}
        preset_list = presets.get("preset", [])
        if isinstance(preset_list, dict):
            preset_list = [preset_list]
        for p in preset_list:
            if str(p.get("@id")) == str(self._preset_id):
                item = p.get("ContentItem", {})
                item_name = item.get("itemName", "").strip()
                if item_name:
                    return item_name
        return f"Preset {self._preset_id}"

    @property
    def available(self) -> bool:
        """Only available if this preset slot is actually configured."""
        presets = self.coordinator.data.get("presets") or {}
        _LOGGER.warning("preset %s data: %s", self._preset_id, presets)
        preset_list = presets.get("preset", [])
        if isinstance(preset_list, dict):
            preset_list = [preset_list]
        for p in preset_list:
            if str(p.get("@id")) == str(self._preset_id):
                return bool(p.get("ContentItem"))
        return False

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._device_id)})

    async def async_press(self) -> None:
        await self.coordinator.device.play_preset(self._preset_id)
        await self.coordinator.async_request_refresh()
