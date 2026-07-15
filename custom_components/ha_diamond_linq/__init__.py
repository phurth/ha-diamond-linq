"""Diamond Linq Water Softener integration for Home Assistant."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    DEFAULT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_PASSWORD,
    DEFAULT_PASSWORD,
)
from .ble_client import SoftenerBleClient
from .parser import DiamondLinqData

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
]

_LOGGER = logging.getLogger(__name__)

# Consecutive empty/failed polls tolerated before entities go unavailable —
# rides out brief BLE hiccups so a single missed poll doesn't blank the device.
MAX_TRANSIENT_MISSES = 4


class SoftenerDataUpdateCoordinator(DataUpdateCoordinator[DiamondLinqData]):
    """Coordinator that manages polling the softener via BLE."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SoftenerBleClient,
        address: str,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: Home Assistant instance
            client: The BLE client for the softener
            address: BLE address for logging
            scan_interval: Poll interval in seconds (user-configurable)
        """
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{address}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.address = address
        self._fail_count = 0

    async def _async_update_data(self) -> DiamondLinqData:
        """Fetch data from the softener.

        BLE links hiccup occasionally; rather than flip every entity to
        unavailable on a single missed poll, ride out up to
        MAX_TRANSIENT_MISSES consecutive misses by returning the last-good data.
        """
        _LOGGER.debug("Coordinator updating data for %s", self.address)

        try:
            data = await self.client.async_poll_once()
            got_data = data.soft_remaining_gal is not None or data.flow_gpm is not None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Poll raised for %s: %s", self.address, err)
            got_data = False

        if got_data:
            self._fail_count = 0
            return data

        # No usable data this cycle.
        self._fail_count += 1
        if self._fail_count <= MAX_TRANSIENT_MISSES and self.data is not None:
            _LOGGER.debug(
                "Transient poll miss %d/%d for %s — keeping last values",
                self._fail_count, MAX_TRANSIENT_MISSES, self.address,
            )
            return self.data

        _LOGGER.warning(
            "Softener %s unreachable (%d consecutive misses)", self.address, self._fail_count
        )
        await self.client.async_disconnect()  # force a fresh session next poll
        raise UpdateFailed(f"Softener {self.address} unreachable")

    async def async_shutdown(self) -> None:
        """Shut down the coordinator and disconnect."""
        _LOGGER.info("Shutting down coordinator for %s", self.address)
        await self.client.async_disconnect()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Diamond Linq softener from a config entry."""
    # Get the device address from config entry
    address = entry.data.get("address") or entry.unique_id
    
    if not address:
        _LOGGER.error("No address configured for Diamond Linq softener")
        return False

    _LOGGER.info("Setting up Diamond Linq softener at %s", address)

    password = entry.data.get(CONF_PASSWORD, DEFAULT_PASSWORD)

    # Poll interval: user-configurable via options, clamped to sane bounds.
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    scan_interval = max(MIN_SCAN_INTERVAL, min(MAX_SCAN_INTERVAL, int(scan_interval)))
    _LOGGER.info("Poll interval: %ds", scan_interval)

    # Create the BLE client
    client = SoftenerBleClient(hass, address, password=password)

    # Create the coordinator
    coordinator = SoftenerDataUpdateCoordinator(hass, client, address, scan_interval)

    # Store the coordinator for access by platforms
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
    }

    # First fetch before entities register so device info (incl. firmware) is
    # populated. async_refresh() never raises, so a transient out-of-range
    # device does not fail setup — entities are simply unavailable until the
    # next successful poll.
    await coordinator.async_refresh()

    # Set up platforms.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register shutdown handler
    entry.async_on_unload(coordinator.async_shutdown)

    # Reload the entry when options (e.g. poll interval) change.
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))

    return True


async def _async_reload_on_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Diamond Linq softener entry %s", entry.entry_id)
    
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data:
            coordinator = data.get("coordinator")
            if coordinator:
                await coordinator.async_shutdown()
    
    return unload_ok
