"""Bluetooth adapter choice: options flow and the pinned connect."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_diamond_linq.ble_client import SoftenerBleClient
from custom_components.ha_diamond_linq.const import (
    CONF_ADAPTER,
    CONF_ADAPTER_FALLBACK,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

ADDRESS = "AA:BB:CC:DD:EE:01"
HCI0 = "11:11:11:11:11:11"
PROXY = "33:33:33:33:33:33"
CF = "custom_components.ha_diamond_linq.config_flow"
BC = "custom_components.ha_diamond_linq.ble_client"


def _heard(source, name, rssi):
    return SimpleNamespace(
        scanner=SimpleNamespace(source=source, name=name),
        advertisement=SimpleNamespace(rssi=rssi),
    )


async def test_options_flow_keeps_the_interval_and_adds_the_adapter(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS, data={"address": ADDRESS})
    entry.add_to_hass(hass)
    with patch(f"{CF}.async_scanner_devices_by_address", return_value=[]), patch(
        f"{CF}.bluetooth.async_current_scanners",
        return_value=[SimpleNamespace(source=HCI0, name="hci0", connectable=True),
                      SimpleNamespace(source=PROXY, name="proxy", connectable=True)],
        create=True,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"].schema
    selector = schema[next(k for k in schema if k == CONF_ADAPTER)]
    # The softener is connected (not advertising): the known scanners are still offered.
    assert [o["value"] for o in selector.config["options"]] == ["automatic", HCI0, PROXY]

    with patch("custom_components.ha_diamond_linq.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_SCAN_INTERVAL: 10, CONF_ADAPTER: PROXY, CONF_ADAPTER_FALLBACK: True},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_INTERVAL: 10, CONF_ADAPTER: PROXY, CONF_ADAPTER_FALLBACK: True}


async def _connect(hass, **kwargs):
    client = SoftenerBleClient(hass, ADDRESS, **kwargs)
    bleak_client = SimpleNamespace(
        _connected_scanner=SimpleNamespace(source=PROXY),
        _backend=None,
        is_connected=True,
        mtu_size=247,
        start_notify=AsyncMock(),
    )
    heard = [_heard(HCI0, "hci0", -70), _heard(PROXY, "proxy", -50)]
    via = AsyncMock(return_value=bleak_client)
    with patch(f"{BC}.establish_connection_via", via), \
         patch(f"{BC}.async_scanner_devices_by_address", return_value=heard), \
         patch(f"{BC}.async_scanner_by_source", return_value=SimpleNamespace(name=f"proxy ({PROXY})")), \
         patch.object(SoftenerBleClient, "_find_device", return_value=(object(), "direct_lookup")):
        assert await client.async_connect()
    return client, via.await_args.kwargs, heard


async def test_default_is_automatic(hass) -> None:
    client, kwargs, _ = await _connect(hass)
    assert kwargs["adapter"] == "automatic" and kwargs["allow_fallback"] is True
    assert kwargs["max_attempts"] == 3
    assert client.bluetooth_path == f"proxy ({PROXY})"
    assert client.bluetooth_path_source == PROXY


async def test_connect_uses_the_stored_choice(hass) -> None:
    _, kwargs, heard = await _connect(hass, adapter=HCI0, adapter_fallback=False)
    assert kwargs["adapter"] == HCI0 and kwargs["allow_fallback"] is False
    assert kwargs["scanner_devices"] == heard


async def test_disconnect_clears_the_path(hass) -> None:
    client, kwargs, _ = await _connect(hass)
    kwargs["disconnected_callback"](None)
    assert client.bluetooth_path is None and client.bluetooth_path_source is None


def test_picker_lists_known_scanners_when_0_2_0_is_loaded(monkeypatch) -> None:
    """0.2.0's adapter_options has no scanners=; the picker must still list them."""
    import custom_components.ha_diamond_linq.pinning as pinning  # noqa: PLC0415

    def old_adapter_options(scanner_devices, current=None):  # 0.2.0 signature
        options = {"automatic": "Automatic (Home Assistant chooses)"}
        for d in scanner_devices:
            options[d.scanner.source] = f"{d.scanner.name}, {d.advertisement.rssi} dBm"
        if current and current != "automatic" and current not in options:
            options[current] = f"{current} (not hearing this device right now)"
        return options

    monkeypatch.setattr(pinning, "_adapter_options", old_adapter_options)
    scanners = [
        SimpleNamespace(source=PROXY, name="proxy", connectable=True),
        SimpleNamespace(source=HCI0, name="hci0", connectable=True),
        SimpleNamespace(source="44:44:44:44:44:44", name="passive", connectable=False),
    ]
    # Connected thermostat: nothing hears it; the stored choice is the proxy.
    options = pinning.adapter_options([], PROXY, scanners=scanners)
    assert list(options) == ["automatic", HCI0, PROXY]
    assert options[PROXY] == "proxy"
    # Heard scanners stay first, with their signal.
    options = pinning.adapter_options([_heard(PROXY, "proxy", -60)], None, scanners=scanners)
    assert list(options) == ["automatic", PROXY, HCI0]
    assert options[PROXY] == "proxy, -60 dBm"
