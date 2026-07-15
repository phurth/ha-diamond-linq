DOMAIN = "ha_diamond_linq"

MANUFACTURER = "phurth"
MODEL = "Diamond Linq Water Softener"
NAME = "Diamond Linq Water Softener"


def build_device_info(entry_id: str, firmware: str | None = None) -> dict:
    """Shared device registry info for all entities."""
    info: dict = {
        "identifiers": {(DOMAIN, entry_id)},
        "name": NAME,
        "manufacturer": MANUFACTURER,
        "model": MODEL,
    }
    if firmware:
        info["sw_version"] = firmware
    return info

# Nordic UART Service UUIDs
NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # write
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # notify

# BLE handles (from protocol analysis)
HANDLE_TX = 0x000F
HANDLE_RX = 0x000D

# Coordinator update interval (seconds), user-configurable via the options flow.
# Fast values give near-real-time readings over a held-open connection (the app
# streams ~1/s); the single BLE slot stays occupied while enabled, so the phone
# app cannot connect meanwhile. Bounds enforced in the config flow.
DEFAULT_SCAN_INTERVAL = 3
MIN_SCAN_INTERVAL = 2
MAX_SCAN_INTERVAL = 3600

# Configuration / options keys
CONF_SCAN_INTERVAL = "scan_interval"

# Configuration keys
CONF_AUTH_TOKEN = "auth_token"
CONF_PASSWORD = "password"

# Default password for PA authentication (from app analysis)
# The app stores per-device passwords with key "PWD-{address}", default is "1234"
DEFAULT_PASSWORD = "1234"

# Default auth token (empty means derive from password)
DEFAULT_AUTH_TOKEN = ""
