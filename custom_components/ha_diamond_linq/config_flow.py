"""Config flow for Diamond Linq Water Softener integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    NAME,
    CONF_AUTH_TOKEN,
    DEFAULT_AUTH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# The softener advertises as "CS_Metter_Soft" (note the typo in "Metter")
DEVICE_NAME_PREFIXES = ("CS_Metter_Soft", "CS_Meter_Soft", "Diamond")


class DiamondLinqConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Diamond Linq softener."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - show discovered devices or manual entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input["address"]
            
            # Handle manual entry option
            if address == "manual":
                return await self.async_step_manual()
            
            # Check if already configured
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()

            # Get the device name if we discovered it
            device_name = NAME
            if address in self._discovered_devices:
                service_info = self._discovered_devices[address]
                if service_info.name:
                    device_name = f"{NAME} ({service_info.name})"

            # Get auth token if provided (optional)
            auth_token = user_input.get(CONF_AUTH_TOKEN, DEFAULT_AUTH_TOKEN)

            return self.async_create_entry(
                title=device_name,
                data={
                    "address": address,
                    CONF_AUTH_TOKEN: auth_token,
                },
            )

        # Discover Diamond Linq softeners via Bluetooth
        self._discovered_devices = {}
        
        for service_info in async_discovered_service_info(self.hass, connectable=True):
            # Check if device name matches our patterns
            if service_info.name and any(
                service_info.name.startswith(prefix) for prefix in DEVICE_NAME_PREFIXES
            ):
                # Check if already configured
                if self._address_already_configured(service_info.address):
                    continue
                    
                self._discovered_devices[service_info.address] = service_info
                _LOGGER.debug(
                    "Discovered Diamond Linq device: %s (%s) via %s",
                    service_info.name,
                    service_info.address,
                    service_info.source,
                )

        if self._discovered_devices:
            # Build selection list with device info
            device_options = {
                address: f"{info.name} ({address}) - RSSI: {info.rssi}"
                for address, info in self._discovered_devices.items()
            }
            # Add manual entry option
            device_options["manual"] = "Enter address manually..."
            
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required("address"): vol.In(device_options),
                        vol.Optional(CONF_AUTH_TOKEN, default=""): str,
                    }
                ),
                errors=errors,
                description_placeholders={
                    "device_count": str(len(self._discovered_devices))
                },
            )
        else:
            # No devices found - go straight to manual entry
            _LOGGER.info("No Diamond Linq devices discovered, showing manual entry")
            return await self.async_step_manual()

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual address entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input["address"].upper().strip()
            
            # Basic MAC address validation
            if not self._is_valid_mac(address):
                errors["address"] = "invalid_mac"
            else:
                # Check if already configured
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()

                auth_token = user_input.get(CONF_AUTH_TOKEN, "")
                return self.async_create_entry(
                    title=NAME,
                    data={
                        "address": address,
                        CONF_AUTH_TOKEN: auth_token,
                    },
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required("address"): str,
                    vol.Optional(CONF_AUTH_TOKEN, default=""): str,
                }
            ),
            errors=errors,
        )

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle a device discovered via Bluetooth."""
        _LOGGER.info(
            "Bluetooth discovery: %s (%s)",
            discovery_info.name,
            discovery_info.address,
        )
        
        # Set unique ID and abort if already configured
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        # Store discovery info for the confirm step
        self._discovered_devices[discovery_info.address] = discovery_info
        
        self.context["title_placeholders"] = {
            "name": discovery_info.name or NAME,
        }

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm Bluetooth discovery."""
        if user_input is not None:
            auth_token = user_input.get(CONF_AUTH_TOKEN, "")
            return self.async_create_entry(
                title=self.context.get("title_placeholders", {}).get("name", NAME),
                data={
                    "address": self.unique_id,
                    CONF_AUTH_TOKEN: auth_token,
                },
            )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_AUTH_TOKEN, default=""): str,
                }
            ),
            description_placeholders={
                "name": self.context.get("title_placeholders", {}).get("name", NAME),
            },
        )

    def _address_already_configured(self, address: str) -> bool:
        """Check if an address is already configured."""
        for entry in self._async_current_entries():
            if entry.data.get("address", "").upper() == address.upper():
                return True
            if entry.unique_id and entry.unique_id.upper() == address.upper():
                return True
        return False

    @staticmethod
    def _is_valid_mac(address: str) -> bool:
        """Validate MAC address format."""
        parts = address.replace("-", ":").split(":")
        if len(parts) != 6:
            return False
        try:
            for part in parts:
                if len(part) != 2:
                    return False
                int(part, 16)
            return True
        except ValueError:
            return False

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Get the options flow handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Diamond Linq softener."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle options (poll interval)."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self._config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self._config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL, default=current_interval
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    ),
                }
            ),
        )
