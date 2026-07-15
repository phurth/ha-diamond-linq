"""Number platform for Diamond Linq Water Softener (configuration setpoints)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, build_device_info
from .ble_client import SoftenerBleClient
from .parser import DiamondLinqData

_LOGGER = logging.getLogger(__name__)


def _override_days(data: DiamondLinqData) -> Optional[int]:
    """Read the regen-day-override string ("Disabled" / "N days") as an int."""
    v = data.regen_day_override
    if not v or v == "Disabled":
        return 0
    try:
        return int(v.split()[0])
    except (ValueError, IndexError):
        return None


@dataclass(frozen=True, kw_only=True)
class SoftenerNumberDescription(NumberEntityDescription):
    """Describes a Diamond Linq number setpoint."""

    value_fn: Callable[[DiamondLinqData], Any]
    set_fn: Callable[[SoftenerBleClient, float], Awaitable[bool]]


NUMBERS: tuple[SoftenerNumberDescription, ...] = (
    SoftenerNumberDescription(
        key="set_hardness",
        name="Water Hardness",
        native_unit_of_measurement="gpg",
        native_min_value=0,
        native_max_value=99,
        native_step=1,
        mode=NumberMode.BOX,
        icon="mdi:water-opacity",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda d: d.water_hardness_gpg,
        set_fn=lambda c, v: c.async_set_hardness(int(v)),
    ),
    SoftenerNumberDescription(
        key="set_reserve_capacity",
        name="Reserve Capacity",
        native_unit_of_measurement="%",
        native_min_value=0,
        native_max_value=49,
        native_step=1,
        mode=NumberMode.BOX,
        icon="mdi:gauge",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda d: d.reserve_capacity_pct,
        set_fn=lambda c, v: c.async_set_reserve_capacity(int(v)),
    ),
    SoftenerNumberDescription(
        key="set_regen_day_interval",
        name="Regen Day Interval",
        native_unit_of_measurement="d",
        native_min_value=0,
        native_max_value=30,
        native_step=1,
        mode=NumberMode.BOX,
        icon="mdi:calendar-refresh",
        entity_category=EntityCategory.CONFIG,
        value_fn=_override_days,
        set_fn=lambda c, v: c.async_set_regen_day_interval(int(v)),
    ),
    SoftenerNumberDescription(
        key="set_resin_grains",
        name="Resin Capacity",
        native_unit_of_measurement="grains",
        native_min_value=0,
        native_max_value=399000,
        native_step=1000,
        mode=NumberMode.BOX,
        icon="mdi:grain",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.resin_capacity_grains,
        set_fn=lambda c, v: c.async_set_resin_grains(int(v)),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Diamond Linq number entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    client = data["client"]
    async_add_entities(
        SoftenerNumber(coordinator, client, entry, description) for description in NUMBERS
    )


class SoftenerNumber(CoordinatorEntity, NumberEntity):
    """A Diamond Linq configuration setpoint."""

    entity_description: SoftenerNumberDescription
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

    @property
    def native_value(self) -> Optional[float]:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_set_native_value(self, value: float) -> None:
        if await self.entity_description.set_fn(self._client, value):
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.warning("Set '%s' failed", self.entity_description.key)
