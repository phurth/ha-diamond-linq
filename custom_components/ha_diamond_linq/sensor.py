"""Sensor platform for Diamond Linq Water Softener."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, build_device_info
from .parser import DiamondLinqData

if TYPE_CHECKING:
    from . import SoftenerDataUpdateCoordinator


@dataclass(frozen=True, kw_only=True)
class SoftenerSensorEntityDescription(SensorEntityDescription):
    """Describes a Diamond Linq sensor entity."""
    
    value_fn: Callable[[DiamondLinqData], Any]


SENSORS: tuple[SoftenerSensorEntityDescription, ...] = (
    SoftenerSensorEntityDescription(
        key="flow_gpm",
        name="Current Flow",
        native_unit_of_measurement="gal/min",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water",
        value_fn=lambda d: d.flow_gpm,
    ),
    SoftenerSensorEntityDescription(
        key="peak_flow_gpm",
        name="Peak Flow Today",
        native_unit_of_measurement="gal/min",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-alert",
        value_fn=lambda d: d.peak_flow_gpm,
    ),
    SoftenerSensorEntityDescription(
        key="soft_remaining_gal",
        name="Soft Water Remaining",
        native_unit_of_measurement="gal",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-check",
        value_fn=lambda d: d.soft_remaining_gal,
    ),
    SoftenerSensorEntityDescription(
        key="water_used_today_gal",
        name="Water Used Today",
        native_unit_of_measurement="gal",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:water-plus",
        value_fn=lambda d: d.water_used_today_gal,
    ),
    SoftenerSensorEntityDescription(
        key="days_until_regen",
        name="Days Until Regeneration",
        native_unit_of_measurement="d",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar-clock",
        value_fn=lambda d: d.days_until_regen,
    ),
    SoftenerSensorEntityDescription(
        key="regen_time",
        name="Regeneration Time",
        icon="mdi:clock-outline",
        value_fn=lambda d: d.regen_time,
    ),
    SoftenerSensorEntityDescription(
        key="water_hardness_gpg",
        name="Water Hardness",
        native_unit_of_measurement="gpg",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-opacity",
        value_fn=lambda d: d.water_hardness_gpg,
    ),
    SoftenerSensorEntityDescription(
        key="reserve_capacity_pct",
        name="Reserve Capacity",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:gauge",
        value_fn=lambda d: d.reserve_capacity_pct,
    ),
    SoftenerSensorEntityDescription(
        key="resin_capacity_grains",
        name="Resin Capacity",
        native_unit_of_measurement="grains",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:grain",
        value_fn=lambda d: d.resin_capacity_grains,
        entity_registry_enabled_default=False,
    ),
    SoftenerSensorEntityDescription(
        key="salt_remaining_lbs",
        name="Salt Remaining",
        native_unit_of_measurement="lb",
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:shaker",
        value_fn=lambda d: d.salt_remaining_lbs,
    ),
    SoftenerSensorEntityDescription(
        key="salt_remaining_pct",
        name="Salt Level",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:gauge",
        value_fn=lambda d: d.salt_remaining_pct,
    ),
    SoftenerSensorEntityDescription(
        key="regens_remaining",
        name="Regenerations Remaining",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:counter",
        value_fn=lambda d: d.regens_remaining,
    ),
    SoftenerSensorEntityDescription(
        key="salt_total_lbs",
        name="Salt Capacity",
        native_unit_of_measurement="lb",
        device_class=SensorDeviceClass.WEIGHT,
        icon="mdi:shaker-outline",
        value_fn=lambda d: d.salt_total_lbs,
        entity_registry_enabled_default=False,
    ),
    SoftenerSensorEntityDescription(
        key="brine_tank_size",
        name="Brine Tank Size",
        icon="mdi:barrel",
        value_fn=lambda d: d.brine_tank_size,
        entity_registry_enabled_default=False,
    ),
    SoftenerSensorEntityDescription(
        key="regen_day_override",
        name="Regen Day Override",
        icon="mdi:calendar-refresh",
        value_fn=lambda d: d.regen_day_override,
    ),
    SoftenerSensorEntityDescription(
        key="firmware_version",
        name="Firmware Version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.firmware_version,
    ),
    SoftenerSensorEntityDescription(
        key="battery_pct",
        name="Battery",
        native_unit_of_measurement="%",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.battery_pct,
        entity_registry_enabled_default=False,  # AC-powered units read 0
    ),
    SoftenerSensorEntityDescription(
        key="avg_daily_use_gal",
        name="Average Daily Use",
        native_unit_of_measurement="gal",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:chart-line",
        value_fn=lambda d: d.avg_daily_use_gal,
    ),
    SoftenerSensorEntityDescription(
        key="backwash_min",
        name="Backwash Time",
        native_unit_of_measurement="min",
        icon="mdi:timer-cog",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.backwash_min,
    ),
    SoftenerSensorEntityDescription(
        key="brine_draw_min",
        name="Brine Draw Time",
        native_unit_of_measurement="min",
        icon="mdi:timer-cog",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.brine_draw_min,
    ),
    SoftenerSensorEntityDescription(
        key="rapid_rinse_min",
        name="Rapid Rinse Time",
        native_unit_of_measurement="min",
        icon="mdi:timer-cog",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.rapid_rinse_min,
    ),
    SoftenerSensorEntityDescription(
        key="brine_refill_min",
        name="Brine Refill Time",
        native_unit_of_measurement="min",
        icon="mdi:timer-cog",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.brine_refill_min,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Diamond Linq sensor entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: DataUpdateCoordinator[DiamondLinqData] = data["coordinator"]

    entities: list[SoftenerSensor] = [
        SoftenerSensor(coordinator, entry, description)
        for description in SENSORS
    ]

    async_add_entities(entities)


class SoftenerSensor(CoordinatorEntity[DataUpdateCoordinator[DiamondLinqData]], SensorEntity):
    """Sensor entity for Diamond Linq water softener."""

    entity_description: SoftenerSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[DiamondLinqData],
        entry: ConfigEntry,
        description: SoftenerSensorEntityDescription,
    ) -> None:
        """Initialize the sensor.
        
        Args:
            coordinator: The data update coordinator
            entry: The config entry
            description: The sensor entity description
        """
        super().__init__(coordinator)
        self.entity_description = description
        
        # Set unique ID using entry ID and sensor key
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        
        # Device info for grouping entities
        self._attr_device_info = build_device_info(
            entry.entry_id,
            coordinator.data.firmware_version if coordinator.data else None,
        )

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.native_value is not None
        )
