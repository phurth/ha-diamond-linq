"""Button platform for Diamond Linq Water Softener."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import homeassistant.util.dt as dt_util

from .const import DOMAIN, build_device_info
from .ble_client import SoftenerBleClient

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class SoftenerButtonDescription(ButtonEntityDescription):
    """Describes a Diamond Linq button."""

    press_fn: Callable[[SoftenerBleClient], Awaitable[bool]]


async def _sync_clock(client: SoftenerBleClient) -> bool:
    now = dt_util.now()
    hour12 = now.hour % 12 or 12
    return await client.async_sync_clock(hour12, now.minute, now.second, now.hour >= 12)


BUTTONS: tuple[SoftenerButtonDescription, ...] = (
    SoftenerButtonDescription(
        key="regenerate_now",
        name="Regenerate Now",
        icon="mdi:refresh",
        press_fn=lambda c: c.async_regenerate_now(),
    ),
    SoftenerButtonDescription(
        key="regenerate_next",
        name="Regenerate at Next Time",
        icon="mdi:calendar-refresh",
        press_fn=lambda c: c.async_regenerate_next(),
    ),
    SoftenerButtonDescription(
        key="sync_clock",
        name="Sync Clock",
        icon="mdi:clock-check",
        entity_category=EntityCategory.CONFIG,
        press_fn=_sync_clock,
    ),
    SoftenerButtonDescription(
        key="reset_total_gallons",
        name="Reset Total Gallons",
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        press_fn=lambda c: c.async_reset_total_gallons(),
    ),
    SoftenerButtonDescription(
        key="reset_regen_counter",
        name="Reset Regeneration Counter",
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        press_fn=lambda c: c.async_reset_regen_counter(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Diamond Linq button entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    client = data["client"]
    async_add_entities(
        SoftenerButton(coordinator, client, entry, description) for description in BUTTONS
    )


class SoftenerButton(CoordinatorEntity, ButtonEntity):
    """A Diamond Linq action button."""

    entity_description: SoftenerButtonDescription
    _attr_has_entity_name = True

    def __init__(self, coordinator, client, entry, description) -> None:
        super().__init__(coordinator)
        self._client = client
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = build_device_info(
            entry.entry_id,
            coordinator.data.firmware_version if coordinator.data else None,
        )

    async def async_press(self) -> None:
        if await self.entity_description.press_fn(self._client):
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.warning("Command '%s' failed", self.entity_description.key)
