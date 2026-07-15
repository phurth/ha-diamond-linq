"""Parser for Diamond Linq Water Softener BLE frames (EVB019 protocol).

Frame types (first two bytes = ASCII tag):
  tt (74 74): DeviceList       — identity / auth (handled in ble_client)
  uu (75 75): Dashboard        — live readings; subtype in byte 2
  vv (76 76): AdvancedSettings — configuration; subtype in byte 2

High/low byte pairs are big-endian: value = hi<<8 | lo. See docs/TECH_SPEC.md
for the full field map.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

_LOGGER = logging.getLogger(__name__)

FRAME_TT = bytes([0x74, 0x74])
FRAME_UU = bytes([0x75, 0x75])
FRAME_VV = bytes([0x76, 0x76])

# Brine tank salt density by tank diameter: total lbs = fill height(in) * lbs/in.
_SALT_LBS_PER_INCH = {16: 8.1, 18: 10.4, 24: 18.6, 30: 29.55}


def _battery_pct(raw_byte: int) -> int:
    """Battery % from the dashboard ADC byte.

    Maps an ADC-derived voltage through a piecewise curve. AC-powered units
    report a byte that resolves to 0.
    """
    volts = (raw_byte & 0xFF) * 0.088
    if volts >= 9.5:
        pct = 100.0
    elif volts >= 8.91:
        pct = 100 - (9.5 - volts) * 8.78
    elif volts >= 8.48:
        pct = 94.78 - (8.91 - volts) * 30.26
    elif volts >= 7.43:
        pct = 81.84 - (8.48 - volts) * 60.47
    elif volts < 6.5:
        pct = 0.0
    else:
        pct = 18.68 - (7.43 - volts) * 20.02
    return max(0, min(100, int(pct)))


@dataclass
class DiamondLinqData:
    """Decoded values from the softener."""

    # Live readings — uu / Dashboard (subtype 0)
    flow_gpm: Optional[float] = None
    peak_flow_gpm: Optional[float] = None
    soft_remaining_gal: Optional[int] = None
    water_used_today_gal: Optional[int] = None
    avg_daily_use_gal: Optional[int] = None  # from the 62-day usage graph
    water_hardness_gpg: Optional[int] = None
    regen_hour: Optional[int] = None
    regen_time: Optional[str] = None  # formatted clock time, e.g. "2:00 AM"

    # Salt / brine tank — uu (subtype 1)
    salt_remaining_lbs: Optional[int] = None
    salt_total_lbs: Optional[int] = None
    salt_remaining_pct: Optional[int] = None
    regens_remaining: Optional[int] = None
    brine_tank_size: Optional[str] = None

    # Configuration — vv / AdvancedSettings (subtype 0)
    days_until_regen: Optional[int] = None
    reserve_capacity_pct: Optional[int] = None
    resin_capacity_grains: Optional[int] = None
    regen_day_override: Optional[str] = None

    # Regen cycle position times — vv / AdvancedSettings (subtype 1), minutes
    backwash_min: Optional[int] = None
    brine_draw_min: Optional[int] = None
    rapid_rinse_min: Optional[int] = None
    brine_refill_min: Optional[int] = None

    # Valve state bits from the dashboard frame (packet[18])
    display_on: Optional[bool] = None
    bypass_active: Optional[bool] = None
    shutoff_active: Optional[bool] = None

    # Device metadata
    firmware_version: Optional[str] = None  # tt frame, e.g. "C4.38"
    battery_pct: Optional[int] = None       # uu dashboard; 0 on AC-powered units

    last_update: Optional[float] = None


class FrameParser:
    """Parser for Diamond Linq BLE notification frames."""

    def __init__(self) -> None:
        self.data = DiamondLinqData()
        # 62-day usage graph, assembled across the uu subtype-2 frame + 3
        # untagged continuation frames. _graph_state tracks which chunk is next.
        self._graph = [0.0] * 62
        self._graph_state: Optional[int] = None

    def parse_frame(self, raw: bytes) -> bool:
        """Parse a raw BLE notification frame. Returns True if handled."""
        header = raw[:2] if len(raw) >= 2 else b""
        # Usage-graph continuation frames have no ASCII tag — route them by state.
        if self._graph_state is not None and header not in (FRAME_TT, FRAME_UU, FRAME_VV):
            return self._graph_continuation(raw)
        if self._graph_state is not None:
            # A tagged frame interrupted the graph — abandon the partial capture.
            self._graph_state = None
        if len(raw) < 4:
            return False
        if header == FRAME_UU:
            return self._parse_uu(raw)
        if header == FRAME_VV:
            return self._parse_vv(raw)
        if header == FRAME_TT:
            return self._parse_tt(raw)
        _LOGGER.debug("Unknown frame header: %s", header.hex())
        return False

    def _parse_tt(self, raw: bytes) -> bool:
        """tt / DeviceList — firmware version at tt[5] (major) / tt[6] (minor BCD)."""
        try:
            if len(raw) >= 7:
                major = raw[5]
                mb = raw[6]
                minor = ((mb >> 4) * 10 + (mb & 0x0F)) if mb < 250 else 99
                n = major * 100 + minor
                self.data.firmware_version = f"C{n // 100}.{n % 100:02d}"
            return True
        except IndexError:
            return False

    # -- uu / Dashboard ------------------------------------------------------

    def _parse_uu(self, raw: bytes) -> bool:
        if len(raw) < 20:
            _LOGGER.debug("uu frame too short: %d bytes", len(raw))
            return False
        subtype = raw[2]
        if subtype == 0x00:
            return self._parse_dashboard(raw)
        if subtype == 0x01:
            return self._parse_salt_config(raw)
        if subtype == 0x02:
            return self._start_graph(raw)
        return True

    def _parse_dashboard(self, raw: bytes) -> bool:
        """uu subtype 0 — live dashboard readings."""
        try:
            self.data.flow_gpm = ((raw[7] << 8) | raw[8]) / 100.0
            self.data.soft_remaining_gal = (raw[9] << 8) | raw[10]
            self.data.water_used_today_gal = (raw[11] << 8) | raw[12]
            self.data.peak_flow_gpm = ((raw[13] << 8) | raw[14]) / 100.0
            self.data.water_hardness_gpg = raw[15]
            self.data.regen_hour = raw[16]
            # raw[17] = AM/PM flag (0 = AM). Format as a clock time of day.
            hour = raw[16]
            am_pm = "AM" if raw[17] == 0 else "PM"
            if 1 <= hour <= 12:
                self.data.regen_time = f"{hour}:00 {am_pm}"
            self.data.battery_pct = _battery_pct(raw[6])
            # Valve state bits in byte 18: bit3 = shutoff, bit4 = bypass,
            # bit5 = display-off (bit N set when value & (1 << (N-1))).
            state = raw[18]
            self.data.shutoff_active = bool(state & 0x04)
            self.data.bypass_active = bool(state & 0x08)
            self.data.display_on = not (state & 0x10)
            _LOGGER.debug(
                "uu dashboard: flow=%.2f remain=%d used_today=%d peak=%.2f "
                "hardness=%d regen=%s",
                self.data.flow_gpm, self.data.soft_remaining_gal,
                self.data.water_used_today_gal, self.data.peak_flow_gpm,
                self.data.water_hardness_gpg, self.data.regen_time,
            )
            return True
        except IndexError as e:
            _LOGGER.warning("uu dashboard parse error: %s", e)
            return False

    def _parse_salt_config(self, raw: bytes) -> bool:
        """uu subtype 1 — brine tank / salt.

        Salt remaining (lb) = refill_time * 1.5 * regens_remaining (residential).
        Total capacity = fill_height * lbs-per-inch for the configured tank
        diameter. A regens byte of 0xFF means the brine tank is not set up.
        """
        try:
            regens_remaining = raw[13]
            if regens_remaining == 0xFF:  # brine tank not configured
                self.data.regens_remaining = None
                self.data.salt_remaining_lbs = None
                self.data.salt_remaining_pct = None
                return True

            tank_width = raw[15]   # 16/18/24/30-inch tank enum
            fill_height = raw[16]  # inches
            refill_time = raw[17]  # minutes
            lbs_per_inch = _SALT_LBS_PER_INCH.get(tank_width, 8.1)
            total_lbs = fill_height * lbs_per_inch
            # 1.5 lb of salt absorbed per minute of brine refill (residential).
            remaining_lbs = refill_time * 1.5 * regens_remaining

            self.data.regens_remaining = regens_remaining
            self.data.brine_tank_size = (
                f'{tank_width}"' if tank_width in _SALT_LBS_PER_INCH else None
            )
            self.data.salt_remaining_lbs = int(remaining_lbs + 0.5)  # round half up
            self.data.salt_total_lbs = int(total_lbs + 0.5)
            self.data.salt_remaining_pct = (
                min(100, math.ceil(remaining_lbs / total_lbs * 100)) if total_lbs else None
            )
            _LOGGER.debug(
                "uu salt: remaining=%s lbs (%s%%), total=%s lbs, tank=%s, regens=%d",
                self.data.salt_remaining_lbs, self.data.salt_remaining_pct,
                self.data.salt_total_lbs, self.data.brine_tank_size, regens_remaining,
            )
            return True
        except IndexError:
            return False

    # -- uu usage graph (Average Daily Use) ---------------------------------

    def _start_graph(self, raw: bytes) -> bool:
        """uu subtype 2 — first usage-graph chunk.

        Days 0..16 come from bytes 3..19; the next 3 untagged frames carry the
        rest (day offsets 17 / 37 / 57).
        """
        self._graph = [0.0] * 62
        for i in range(3, min(20, len(raw))):
            self._graph[i - 3] = (raw[i] & 0xFF) * 10.0
        self._graph_state = 1
        return True

    def _graph_continuation(self, raw: bytes) -> bool:
        """Fill the remaining graph days from an untagged continuation frame."""
        st = self._graph_state
        if st == 1:      # days 17..36 from bytes 0..19
            for i in range(min(20, len(raw))):
                self._graph[17 + i] = (raw[i] & 0xFF) * 10.0
            self._graph_state = 2
        elif st == 2:    # days 37..56 from bytes 0..19
            for i in range(min(20, len(raw))):
                self._graph[37 + i] = (raw[i] & 0xFF) * 10.0
            self._graph_state = 3
        elif st == 3:    # days 57..61 from bytes 0..4 (6-byte terminator)
            for i in range(min(5, len(raw))):
                self._graph[57 + i] = (raw[i] & 0xFF) * 10.0
            self._graph_state = None
            self._compute_avg_daily()
        else:
            self._graph_state = None
        return True

    def _compute_avg_daily(self) -> None:
        """Average the most recent 32 days (indices 30..61), non-zero only."""
        recent = self._graph[30:62]
        nonzero = [v for v in recent if v != 0.0]
        if nonzero:
            self.data.avg_daily_use_gal = round(sum(nonzero) / len(nonzero))

    # -- vv / AdvancedSettings ----------------------------------------------

    def _parse_vv(self, raw: bytes) -> bool:
        if len(raw) < 20:
            _LOGGER.debug("vv frame too short: %d bytes", len(raw))
            return False
        # AdvancedSettings config record (byte2==0, byte19==0x42 'B').
        if raw[2] == 0x00 and raw[19] == 0x42:
            try:
                self.data.days_until_regen = raw[3]
                override = raw[4]
                self.data.regen_day_override = (
                    "Disabled" if override == 0 else f"{override} days"
                )
                self.data.reserve_capacity_pct = raw[5]
                self.data.resin_capacity_grains = ((raw[6] << 8) | raw[7]) * 1000
                _LOGGER.debug(
                    "vv config: days_until_regen=%d reserve=%d%% grains=%d",
                    self.data.days_until_regen, self.data.reserve_capacity_pct,
                    self.data.resin_capacity_grains,
                )
                return True
            except IndexError as e:
                _LOGGER.warning("vv parse error: %s", e)
                return False
        # subtype 1 = regen cycle position times at bytes 3..6 (minutes =
        # byte & 0x7F; the high bit is a not-adjustable flag). Order:
        # Backwash / Brine Draw / Rapid Rinse / Brine Refill.
        if raw[2] == 0x01:
            try:
                self.data.backwash_min = raw[3] & 0x7F
                self.data.brine_draw_min = raw[4] & 0x7F
                self.data.rapid_rinse_min = raw[5] & 0x7F
                self.data.brine_refill_min = raw[6] & 0x7F
            except IndexError:
                pass
        return True

    def get_data(self) -> DiamondLinqData:
        return self.data

    def reset(self) -> None:
        self.data = DiamondLinqData()
