"""BLE client for Diamond Linq Water Softener.

Handles BLE connection, notification subscription, and command sending
using Home Assistant's bluetooth integration and bleak-retry-connector.

Authentication model (see docs/hacs_viability_report.md):
- The GATT link is unencrypted and unbonded — fully proxy-compatible.
- On the wire the app writes a single "PA" frame: ``74 74 50 41`` followed by
  16 random bytes; the device then sets bit 15 of the tt-frame status word.
- The decompiled app (v3.0.2) never sends PA and streams telemetry ungated,
  so it is unclear whether PA is actually required. The client therefore runs
  a one-time NO-AUTH PROBE on the first connection to determine, empirically,
  whether uu/vv data flows without authentication, then latches the answer.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Optional

from bleak import BleakClient, BleakError
from bleak.backends.device import BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak_retry_connector import establish_connection, BleakNotFoundError

from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    async_scanner_count,
    async_discovered_service_info,
)
from homeassistant.core import HomeAssistant

from .const import NUS_RX_UUID, NUS_TX_UUID
from .parser import DiamondLinqData, FrameParser

_LOGGER = logging.getLogger(__name__)

# Connection timeouts
CONNECT_TIMEOUT = 30.0
DISCONNECT_TIMEOUT = 5.0
POLL_DURATION = 1.5  # How long to listen for notifications during a poll
REQUEST_INTERVAL = 1.0  # How often to send request frames during a poll

# Control command payloads (write to RX, 20 bytes of 0x76 with the 0x47 toggle).
CMD_DISPLAY_ON = bytes([0x76] * 13 + [0x47, 0x00] + [0x76] * 5)
CMD_DISPLAY_OFF = bytes([0x76] * 13 + [0x47, 0x01] + [0x76] * 5)

# Request frames — the header byte selects which record the device returns.
CMD_REQUEST_TT = bytes([0x74] * 20)  # telemetry
CMD_REQUEST_UU = bytes([0x75] * 20)  # usage / salt config
CMD_REQUEST_VV = bytes([0x76] * 20)  # fixed config


# CRC8 polynomials the device accepts: bytes with 4-5 set bits
# (CsCrc8.buildAllowedPolynomials: countSetBits in [4, 5]).
_ALLOWED_POLYS = [i for i in range(1, 256) if 4 <= bin(i).count("1") <= 5]


class _Crc8:
    """Stateful bit-serial CRC8, ported from CsCrc8.computeLegacy.

    setOptions(poly, seed) initializes; each legacy(value) folds one byte into
    the running seed (MSB-first) and returns/updates it.
    """

    __slots__ = ("seed", "poly")

    def __init__(self, poly: int, seed: int) -> None:
        self.poly = poly & 0xFF
        self.seed = seed & 0xFF

    def legacy(self, value: int) -> int:
        b = value & 0xFF
        b2 = self.seed
        for _ in range(8):
            carry = b2 & 0x80
            b2 = (b2 << 1) & 0xFF
            if b & 0x80:
                b2 |= 1
            b = (b << 1) & 0xFF
            if carry:
                b2 ^= self.poly
        self.seed = b2
        return b2


def build_password_pa_frame(password: str, conn_counter: int) -> bytes:
    """Build the authenticated 'PA' frame (CsDataBufferEvb019.getPasswordBuffer).

    Layout (20 bytes, verified byte-for-byte against captured frames)::

        74 74 50 41 <poly> <r1> <r2> <b7> <b8> <b9> <b10> <9 random bytes>

    The password digits are folded through a stateful CRC8 cascade keyed by the
    device's rolling connection counter (``tt`` frame byte 11). ``poly``, ``r1``,
    ``r2`` and the trailing padding are random per send, so every valid frame is
    unique yet decodes to the same password.
    """
    try:
        p = max(0, min(9999, int(password)))
    except (TypeError, ValueError):
        p = 1234
    # passwordBytes = [units, tens, hundreds, thousands]
    pb = [p % 10, (p // 10) % 10, (p // 100) % 10, (p // 1000) % 10]

    poly = secrets.choice(_ALLOWED_POLYS)
    r1 = secrets.randbelow(254) + 1          # 1..254
    r2 = ((secrets.randbelow(254) + 1) ^ r1) & 0xFF

    crc = _Crc8(poly, r1)
    cc = (conn_counter ^ crc.legacy(r2)) & 0xFF
    b7 = (crc.legacy(cc) ^ pb[3]) & 0xFF     # thousands
    b8 = (pb[2] ^ crc.legacy(b7)) & 0xFF     # hundreds
    b9 = (pb[1] ^ crc.legacy(b8)) & 0xFF     # tens
    b10 = (pb[0] ^ crc.legacy(b9)) & 0xFF    # units

    frame = bytearray([0x74] * 20)
    frame[2], frame[3] = 0x50, 0x41
    frame[4], frame[5], frame[6] = poly, r1, r2
    frame[7], frame[8], frame[9], frame[10] = b7, b8, b9, b10
    for i in range(11, 20):
        frame[i] = secrets.randbelow(254) + 1
    return bytes(frame)


class SoftenerBleClient:
    """BLE client for the Diamond Linq water softener."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        auth_token: str = "",
        password: str = "1234",
    ) -> None:
        """Initialize the BLE client.

        Args:
            hass: Home Assistant instance.
            address: BLE MAC address of the softener.
            auth_token: Unused; retained for config-entry compatibility.
            password: Unused; the device link is not password-gated.
        """
        self._hass = hass
        self._address = address
        self._auth_token = auth_token
        self._password = password
        self._client: Optional[BleakClient] = None
        self._parser = FrameParser()
        self._connected = False
        self._lock = asyncio.Lock()
        self._notification_event = asyncio.Event()

        # Auth state.
        #   _auth_required: None = not yet determined; True/False latched by the
        #     one-time NO-AUTH PROBE and kept sticky across reconnects.
        #   _pa_sent_this_conn: reset on every (re)connect.
        #   _auth_confirmed: device set bit 15 of the tt status word (diagnostic).
        self._auth_required: Optional[bool] = None
        self._pa_sent_this_conn = False
        self._auth_confirmed = False
        self._frames_seen: set[str] = set()
        # Rolling connection counter from tt frame byte 11 — keys the auth CRC.
        self._last_conn_counter: Optional[int] = None

    @property
    def address(self) -> str:
        """Return the device address."""
        return self._address

    @property
    def is_connected(self) -> bool:
        """Return True if connected to the device."""
        return self._connected and self._client is not None and self._client.is_connected

    def _reset_connection_state(self) -> None:
        """Reset per-connection flags (not the sticky _auth_required)."""
        self._connected = False
        self._pa_sent_this_conn = False
        self._auth_confirmed = False

    def _notification_handler(
        self, characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Handle incoming BLE notifications on the TX characteristic."""
        frame_type = "unknown"
        auth_flag = ""
        if len(data) >= 2:
            if data[0] == 0x74 and data[1] == 0x74:
                frame_type = "tt"
                # Connection counter (rolls each heartbeat) is tt byte 11; it
                # keys the auth CRC, so keep the freshest value.
                if len(data) >= 12:
                    self._last_conn_counter = data[11]
                # Auth status is bit 15 of the status word at bytes 6-7.
                if len(data) >= 8:
                    status_word = data[6] | (data[7] << 8)
                    if status_word & 0x8000:
                        auth_flag = " [AUTH_OK]"
                        if not self._auth_confirmed:
                            self._auth_confirmed = True
                            _LOGGER.info("Device confirmed authentication (tt bit 15 set)")
                    else:
                        auth_flag = " [NO_AUTH]"
            elif data[0] == 0x75 and data[1] == 0x75:
                frame_type = "uu"
            elif data[0] == 0x76 and data[1] == 0x76:
                frame_type = "vv"

        if frame_type != "unknown":
            self._frames_seen.add(frame_type)

        _LOGGER.debug("BLE RX %s%s: %s (%d bytes)", frame_type, auth_flag, data.hex(), len(data))

        try:
            self._parser.parse_frame(bytes(data))
            self._notification_event.set()
        except Exception as e:  # noqa: BLE001 - never let a parse error kill the callback
            _LOGGER.warning("Error parsing %s frame: %s", frame_type, e)

    def _find_device(self) -> tuple[Optional[BLEDevice], Optional[str]]:
        """Find the BLE device from HA's discovered devices (incl. proxies)."""
        ble_device = async_ble_device_from_address(
            self._hass, self._address, connectable=True
        )
        if ble_device is not None:
            _LOGGER.debug("Found device via async_ble_device_from_address: %s", ble_device)
            return ble_device, "direct_lookup"

        target_address = self._address.upper()
        for service_info in async_discovered_service_info(self._hass, connectable=True):
            if service_info.address.upper() == target_address:
                _LOGGER.debug(
                    "Found device via service_info scan: %s from %s",
                    service_info.device,
                    service_info.source,
                )
                return service_info.device, service_info.source

        scanner_count = async_scanner_count(self._hass, connectable=True)
        _LOGGER.warning(
            "Device %s not found. Active connectable scanners: %d",
            self._address,
            scanner_count,
        )
        return None, None

    async def async_connect(self) -> bool:
        """Connect to the softener and subscribe to notifications."""
        async with self._lock:
            if self.is_connected:
                _LOGGER.debug("Already connected to %s", self._address)
                return True

            _LOGGER.info("Connecting to softener at %s", self._address)

            try:
                ble_device, source = self._find_device()
                if ble_device is None:
                    _LOGGER.warning(
                        "Could not find BLE device %s - ensure it is advertising and a "
                        "bluetooth adapter/proxy can reach it",
                        self._address,
                    )
                    return False

                _LOGGER.info("Found BLE device: %s (source: %s)", ble_device, source)

                def _disconnected_callback(client: BleakClient) -> None:
                    _LOGGER.info("Disconnected from %s", self._address)
                    self._reset_connection_state()

                self._client = await establish_connection(
                    BleakClient,
                    ble_device,
                    self._address,
                    disconnected_callback=_disconnected_callback,
                    max_attempts=3,
                )
                self._reset_connection_state()
                self._connected = True
                self._frames_seen.clear()

                try:
                    _LOGGER.info("Connected to %s (MTU %d)", self._address, self._client.mtu_size)
                except Exception:  # noqa: BLE001
                    _LOGGER.info("Connected to %s", self._address)

                await self._client.start_notify(NUS_TX_UUID, self._notification_handler)
                _LOGGER.info("Subscribed to TX notifications")

                # Brief settle; the trace shows data flows almost immediately.
                await asyncio.sleep(0.1)
                return True

            except BleakNotFoundError:
                _LOGGER.warning("BLE device %s not found or not connectable", self._address)
                self._client = None
                self._reset_connection_state()
                return False
            except BleakError as e:
                _LOGGER.warning("BLE connection error: %s", e)
                self._client = None
                self._reset_connection_state()
                return False
            except Exception as e:  # noqa: BLE001
                _LOGGER.error("Unexpected error connecting: %s", e)
                self._client = None
                self._reset_connection_state()
                return False

    async def async_disconnect(self) -> None:
        """Disconnect from the softener."""
        async with self._lock:
            if self._client is None:
                return
            try:
                if self._client.is_connected:
                    try:
                        await self._client.stop_notify(NUS_TX_UUID)
                    except Exception:  # noqa: BLE001
                        pass
                    await asyncio.wait_for(
                        self._client.disconnect(), timeout=DISCONNECT_TIMEOUT
                    )
                    _LOGGER.info("Disconnected from %s", self._address)
            except asyncio.TimeoutError:
                _LOGGER.warning("Disconnect timeout for %s", self._address)
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("Error disconnecting: %s", e)
            finally:
                self._reset_connection_state()
                self._client = None

    async def _wait_for_notification(self, timeout: float = 0.5) -> bool:
        """Wait for any notification; return True if one arrived before timeout."""
        try:
            await asyncio.wait_for(self._notification_event.wait(), timeout=timeout)
            self._notification_event.clear()
            return True
        except asyncio.TimeoutError:
            return False

    async def _request_all(self, rounds: int = 4, interval: float = 0.35) -> None:
        """Write tt/uu/vv request frames a few times to elicit data."""
        for _ in range(rounds):
            for cmd in (CMD_REQUEST_TT, CMD_REQUEST_UU, CMD_REQUEST_VV):
                try:
                    await self._client.write_gatt_char(NUS_RX_UUID, cmd, response=False)
                except Exception as e:  # noqa: BLE001
                    _LOGGER.debug("Request write failed: %s", e)
                await asyncio.sleep(interval)

    async def _await_auth_bit(self, timeout: float) -> bool:
        """Wait up to `timeout` for the device to set tt bit15 (auth confirmed)."""
        end = time.time() + timeout
        while time.time() < end:
            await self._wait_for_notification(0.2)
            if self._auth_confirmed:
                return True
        return self._auth_confirmed

    async def _authenticate(self) -> bool:
        """Send one password-derived PA frame; return True if bit15 confirms.

        Retries are driven by the poll loop (which drops and re-establishes a
        fresh session between failed attempts), so this does a single clean
        attempt: read the current connection counter (tt byte 11), build the
        keyed PA frame, send it, and wait for the status word to flip to 0x8038.
        """
        self._notification_event.clear()
        try:
            # Read a fresh connection counter from a tt heartbeat.
            await self._client.write_gatt_char(NUS_RX_UUID, CMD_REQUEST_TT, response=False)
            await self._wait_for_notification(1.5)
            cc = self._last_conn_counter
            if cc is None:
                _LOGGER.debug("Auth: no connection counter yet")
                return False
            frame = build_password_pa_frame(self._password, cc)
            _LOGGER.info("Auth: sending PA (connCounter=%d)", cc)
            await self._client.write_gatt_char(NUS_RX_UUID, frame, response=False)
            await self._client.write_gatt_char(NUS_RX_UUID, CMD_REQUEST_TT, response=False)
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("Auth write failed: %s", e)
            return False
        ok = await self._await_auth_bit(2.0)
        if ok:
            _LOGGER.info("Auth SUCCESS — tt bit15 set (connCounter=%d)", cc)
        return ok

    async def _probe_no_auth(self) -> None:
        """One-time empirical test: does uu/vv data flow, with or without auth?

        Latches ``_auth_required`` so this runs at most once per determination.
        Verdicts are emitted at WARNING level so they are easy to find in logs.
        """
        if self._auth_required is not None:
            return

        _LOGGER.warning("NO-AUTH PROBE: requesting tt/uu/vv WITHOUT authentication ...")
        self._frames_seen.clear()
        await self._request_all(rounds=4, interval=0.35)
        pre = sorted(self._frames_seen)

        if "uu" in self._frames_seen or "vv" in self._frames_seen:
            self._auth_required = False
            _LOGGER.warning(
                "NO-AUTH PROBE RESULT: received %s WITHOUT auth -> auth NOT required", pre
            )
            return

        _LOGGER.warning(
            "NO-AUTH PROBE: only %s before auth; attempting ranked authentication ...",
            pre or ["nothing"],
        )
        self._auth_required = True
        auth_ok = await self._authenticate()
        self._frames_seen.clear()
        await self._request_all(rounds=4, interval=0.35)
        post = sorted(self._frames_seen)

        if "uu" in self._frames_seen or "vv" in self._frames_seen:
            _LOGGER.warning(
                "NO-AUTH PROBE RESULT: uu/vv now flowing after auth (bit15=%s) -> got %s",
                self._auth_confirmed, post,
            )
        else:
            _LOGGER.warning(
                "NO-AUTH PROBE RESULT: still no uu/vv (auth bit15=%s, got %s). Protected "
                "device did not accept any derivable credential.",
                self._auth_confirmed, post or ["nothing"],
            )

    async def _gather(self, duration: float) -> None:
        """Request tt/uu/vv repeatedly and collect notifications for `duration`."""
        start_time = time.time()
        last_request_time = 0.0
        frames_received = 0

        while (time.time() - start_time) < duration:
            elapsed = time.time() - start_time
            if last_request_time == 0.0 or (elapsed - last_request_time) >= REQUEST_INTERVAL:
                for cmd in (CMD_REQUEST_TT, CMD_REQUEST_UU, CMD_REQUEST_VV):
                    try:
                        await self._client.write_gatt_char(NUS_RX_UUID, cmd, response=False)
                    except Exception as e:  # noqa: BLE001
                        _LOGGER.debug("Request write failed: %s", e)
                    await asyncio.sleep(0.15)
                last_request_time = elapsed

            if await self._wait_for_notification(0.3):
                frames_received += 1

        _LOGGER.debug(
            "Poll: %d frames, flow=%s GPM, remaining=%s gal, hardness=%s gpg",
            frames_received,
            self._parser.data.flow_gpm,
            self._parser.data.soft_remaining_gal,
            self._parser.data.water_hardness_gpg,
        )

    async def async_poll_once(self) -> DiamondLinqData:
        """Refresh data, keeping the connection open between polls.

        For responsive readings (esp. instantaneous flow) the coordinator polls
        on a short interval and the connection is held open — the device keeps a
        stable link, so this streams fresh data without reconnect churn. It also
        means the single BLE slot stays occupied, so the phone app cannot connect
        while the integration is enabled. Auth runs once per connection.
        """
        if not await self.async_connect():
            _LOGGER.warning("Failed to connect for polling")
            return self._parser.get_data()

        self._notification_event.clear()

        # Authenticate until the device confirms (tt bit15). If a held session
        # won't authenticate, drop it so the next poll starts a fresh session —
        # that self-heals a stale link. Only once authenticated do we keep the
        # connection open for fast subsequent polls.
        if not self._auth_confirmed:
            await self._authenticate()
            if not self._auth_confirmed:
                await self.async_disconnect()
                return self._parser.get_data()

        await self._gather(POLL_DURATION)
        self._parser.data.last_update = time.time()
        return self._parser.get_data()

    async def async_write_command(self, data: bytes) -> bool:
        """Write a raw command to the RX characteristic (write-without-response)."""
        if not self.is_connected:
            if not await self.async_connect():
                _LOGGER.error("Cannot send command: not connected")
                return False
        try:
            _LOGGER.debug("Writing command to RX: %s", data.hex())
            await self._client.write_gatt_char(NUS_RX_UUID, data, response=False)
            _LOGGER.info("Command sent successfully")
            return True
        except BleakError as e:
            _LOGGER.error("Failed to write command: %s", e)
            return False
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("Unexpected error writing command: %s", e)
            return False

    async def async_set_display(self, on: bool) -> bool:
        """Turn the softener front-panel display on or off."""
        cmd = CMD_DISPLAY_ON if on else CMD_DISPLAY_OFF
        _LOGGER.info("Setting display to %s", "ON" if on else "OFF")
        return await self.async_write_command(cmd)

    # -- Write commands (EVB019, verified against v6.13.1 CsDataBufferEvb019) --
    # Frame = 20 bytes of the record tag (Dashboard 0x75, AdvancedSettings 0x76,
    # StatusAndHistory 0x77) with a command letter at [13] and values following.

    async def async_regenerate_now(self) -> bool:
        """Start a regeneration immediately (Dashboard 'RN')."""
        _LOGGER.info("Command: Regenerate Now")
        return await self.async_write_command(bytes([0x75] * 13 + [0x52, 0x4E] + [0x75] * 5))

    async def async_regenerate_next(self) -> bool:
        """Queue regeneration for the next scheduled time (Dashboard 'RT')."""
        _LOGGER.info("Command: Regenerate at Next")
        return await self.async_write_command(bytes([0x75] * 13 + [0x52, 0x54] + [0x75] * 5))

    async def async_set_hardness(self, hardness: int) -> bool:
        """Set water hardness (gpg, 0-99) — Dashboard 'H'."""
        h = max(0, min(99, int(hardness)))
        return await self.async_write_command(bytes([0x75] * 13 + [0x48, h] + [0x75] * 5))

    async def async_set_regen_time(self, hour: int, is_pm: bool) -> bool:
        """Set regeneration time (hour 1-12 + AM/PM) — Dashboard 't'."""
        h = max(1, min(12, int(hour)))
        return await self.async_write_command(
            bytes([0x75] * 13 + [0x74, h, 1 if is_pm else 0] + [0x75] * 4)
        )

    async def async_set_reserve_capacity(self, pct: int) -> bool:
        """Set reserve capacity (%, 0-49) — AdvancedSettings 'B'."""
        v = max(0, min(49, int(pct)))
        return await self.async_write_command(bytes([0x76] * 13 + [0x42, v] + [0x76] * 5))

    async def async_set_resin_grains(self, grains: int) -> bool:
        """Set resin grains capacity (grains, 0-399000) — AdvancedSettings 'C'."""
        val = max(0, min(399, int(grains) // 1000))
        return await self.async_write_command(
            bytes([0x76] * 13 + [0x43, (val >> 8) & 0xFF, val & 0xFF] + [0x76] * 4)
        )

    async def async_set_regen_day_interval(self, days: int) -> bool:
        """Set forced regen-day interval (0-30; 0=disabled) — AdvancedSettings 'A'."""
        d = max(0, min(30, int(days)))
        return await self.async_write_command(bytes([0x76] * 13 + [0x41, d] + [0x76] * 5))

    async def async_set_bypass(self, active: bool) -> bool:
        """Put the valve into/out of bypass (raw, unsoftened water) — Dashboard 'RB'."""
        _LOGGER.info("Command: Bypass %s", "ON" if active else "OFF")
        return await self.async_write_command(
            bytes([0x75] * 13 + [0x52, 0x42, 1 if active else 0] + [0x75] * 4)
        )

    async def async_set_shutoff(self, active: bool) -> bool:
        """Shut off / restore water flow — Dashboard 'RO'."""
        _LOGGER.info("Command: Shutoff %s", "ON" if active else "OFF")
        return await self.async_write_command(
            bytes([0x75] * 13 + [0x52, 0x4F, 1 if active else 0] + [0x75] * 4)
        )

    async def async_sync_clock(self, hour: int, minute: int, second: int, is_pm: bool) -> bool:
        """Set the valve real-time clock (12h) — Dashboard 'T'."""
        h = max(1, min(12, int(hour)))
        return await self.async_write_command(
            bytes([0x75] * 13
                  + [0x54, h, minute & 0xFF, 1 if is_pm else 0, second & 0xFF]
                  + [0x75] * 2)
        )

    async def async_reset_total_gallons(self) -> bool:
        """Reset the lifetime gallons totalizer — StatusAndHistory 'A'."""
        _LOGGER.info("Command: Reset total gallons")
        return await self.async_write_command(bytes([0x77] * 13 + [0x41] + [0x77] * 6))

    async def async_reset_regen_counter(self) -> bool:
        """Reset the regeneration counter — StatusAndHistory 'B'."""
        _LOGGER.info("Command: Reset regen counter")
        return await self.async_write_command(bytes([0x77] * 13 + [0x42] + [0x77] * 6))

    def get_data(self) -> DiamondLinqData:
        """Get the current parsed data without polling."""
        return self._parser.get_data()

    def reset_data(self) -> None:
        """Reset all parsed data."""
        self._parser.reset()
