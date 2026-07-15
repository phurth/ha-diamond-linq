"""Switch platform for Diamond Linq Water Softener."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, build_device_info
from .ble_client import SoftenerBleClient
from .parser import DiamondLinqData

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class SoftenerSwitchDescription(SwitchEntityDescription):
    """Describes a Diamond Linq switch (real state read from the dashboard frame)."""

    value_fn: Callable[[DiamondLinqData], Optional[bool]]
    set_fn: Callable[[SoftenerBleClient, bool], Awaitable[bool]]


SWITCHES: tuple[SoftenerSwitchDescription, ...] = (
    SoftenerSwitchDescription(
        key="display",
        name="Display",
        icon="mdi:monitor",
        value_fn=lambda d: d.display_on,
        set_fn=lambda c, on: c.async_set_display(on),
    ),
    SoftenerSwitchDescription(
        key="bypass",
        name="Bypass",
        icon="mdi:water-off",
        value_fn=lambda d: d.bypass_active,
        set_fn=lambda c, on: c.async_set_bypass(on),
    ),
    SoftenerSwitchDescription(
        key="shutoff",
        name="Water Shutoff",
        icon="mdi:water-pump-off",
        value_fn=lambda d: d.shutoff_active,
        set_fn=lambda c, on: c.async_set_shutoff(on),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Diamond Linq switch entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    client = data["client"]
    async_add_entities(
        SoftenerSwitch(coordinator, client, entry, description) for description in SWITCHES
    )


class SoftenerSwitch(CoordinatorEntity, SwitchEntity):
    """A Diamond Linq control switch backed by the device's reported state."""

    entity_description: SoftenerSwitchDescription
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
    def is_on(self) -> Optional[bool]:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    async def _set(self, on: bool) -> None:
        if await self.entity_description.set_fn(self._client, on):
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.warning("Set '%s' failed", self.entity_description.key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
