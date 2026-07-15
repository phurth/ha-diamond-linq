"""Select platform for Diamond Linq Water Softener (regeneration time)."""
from __future__ import annotations

import logging
from typing import Optional

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, build_device_info

_LOGGER = logging.getLogger(__name__)

# "12:00 AM", "1:00 AM" ... "11:00 AM", "12:00 PM" ... "11:00 PM"
REGEN_TIMES: list[str] = [
    f"{h}:00 {ap}" for ap in ("AM", "PM") for h in [12, *range(1, 12)]
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Diamond Linq select entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RegenTimeSelect(data["coordinator"], data["client"], entry)])


class RegenTimeSelect(CoordinatorEntity, SelectEntity):
    """Set the daily regeneration time."""

    _attr_has_entity_name = True
    _attr_name = "Regeneration Time"
    _attr_icon = "mdi:clock-outline"
    _attr_options = REGEN_TIMES
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, client, entry) -> None:
        super().__init__(coordinator)
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_set_regen_time"
        self._attr_device_info = build_device_info(
            entry.entry_id,
            coordinator.data.firmware_version if coordinator.data else None,
        )

    @property
    def current_option(self) -> Optional[str]:
        data = self.coordinator.data
        if data is None or data.regen_time not in REGEN_TIMES:
            return None
        return data.regen_time

    async def async_select_option(self, option: str) -> None:
        # option like "2:00 AM"
        try:
            clock, am_pm = option.split()
            hour = int(clock.split(":")[0])
        except (ValueError, IndexError):
            _LOGGER.warning("Bad regen time option: %s", option)
            return
        if await self._client.async_set_regen_time(hour, am_pm.upper() == "PM"):
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.warning("Set regeneration time failed")
