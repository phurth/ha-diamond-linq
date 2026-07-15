# Diamond Linq Water Softener — Technical Specification

Technical reference for the `diamond_linq_softener` Home Assistant integration: the
Bluetooth Low Energy protocol it speaks, the fields it decodes, the commands it
sends, and how the integration is structured.

The target device is a Diamond Linq water softener built on the Chandler Systems
"Signature" metered valve (EVB019 series). It advertises as `CS_Meter_Soft`.

---

## 1. GATT Profile

The valve exposes a **Nordic UART Service (NUS)**. All application traffic uses it:

| Role | UUID | Handle |
|------|------|--------|
| Service | `6e400001-b5a3-f393-e0a9-e50e24dcca9e` | `0x000b` |
| RX (write) | `6e400002-b5a3-f393-e0a9-e50e24dcca9e` | `0x000d` |
| TX (notify) | `6e400003-b5a3-f393-e0a9-e50e24dcca9e` | `0x000f` |

- The link is **unencrypted and unbonded** — no SMP pairing. This is why the
  device works transparently over an ESPHome Bluetooth proxy.
- The client subscribes to TX notifications (CCCD `0x0010`) and writes to RX
  using **Write Without Response**.
- Negotiated MTU is typically 247; all application frames are ≤ 20 bytes.

---

## 2. Framing

Application frames are **20 bytes**, tagged by two leading ASCII bytes:

| Tag | Bytes | Record |
|-----|-------|--------|
| `tt` | `74 74` | DeviceList — identity, firmware, auth state |
| `uu` | `75 75` | Dashboard — live readings, salt, usage graph |
| `vv` | `76 76` | AdvancedSettings — configuration, cycle times |

**Requesting a record:** write 20 bytes of the tag byte to RX (e.g. `74`×20 for
`tt`). The valve replies with one or more notifications of that record.

**Sub-records:** for `uu` and `vv`, byte 2 selects a sub-record:

| Record | byte 2 | Contents |
|--------|--------|----------|
| `uu` | `0x00` | Dashboard: flow, remaining, usage, hardness, regen time, battery, valve state |
| `uu` | `0x01` | Brine tank: salt, tank geometry, regen-cycle counters |
| `uu` | `0x02` | Usage graph (62-day history) + continuation frames |
| `vv` | `0x00` | Config: days-until-regen, override, reserve, resin capacity |
| `vv` | `0x01` | Regeneration cycle position times |

All multi-byte numeric fields are big-endian unless noted: `value = hi<<8 | lo`.

---

## 3. Authentication

The valve streams a `tt` heartbeat continuously but withholds `uu`/`vv` records
until the connection is authenticated. Authentication is a single 20-byte write
to RX:

```
74 74 50 41 <poly> <r1> <r2> <b7> <b8> <b9> <b10> <9 random bytes>
```

- Bytes 0–1: `74 74` (`tt`); bytes 2–3: `50 41` (`PA`).
- Byte 4 `poly`: a CRC-8 polynomial chosen at random from bytes whose set-bit
  count is 4 or 5.
- Byte 5 `r1`: random 1–254 (the CRC seed).
- Byte 6 `r2`: random 1–254.
- Bytes 7–10 encode the 4-digit access code (default `1234`) folded through a
  stateful CRC-8 cascade keyed by the **connection counter** — the value in
  `tt` byte 11, which the valve broadcasts and updates.
- Bytes 11–19: random padding.

### 3.1 CRC-8

```
setOptions(poly, seed):  state.poly = poly; state.seed = seed
legacy(value):           # returns and advances the running seed
    b, s = value, state.seed
    for _ in range(8):
        carry = s & 0x80
        s = ((s << 1) | (1 if b & 0x80 else 0)) & 0xFF
        b = (b << 1) & 0xFF
        if carry: s ^= state.poly
    state.seed = s
    return s
```

### 3.2 Auth-byte derivation

Password digits are `[units, tens, hundreds, thousands]`; `cc` is the connection
counter from `tt` byte 11:

```
setOptions(poly, r1)
mixed = cc ^ legacy(r2)
b7  = legacy(mixed) ^ thousands
b8  = hundreds ^ legacy(b7)
b9  = tens     ^ legacy(b8)
b10 = units    ^ legacy(b9)
```

The random `poly`/`r1`/`r2`/padding mean every valid auth frame is unique while
encoding the same code, so frames are never replayed.

### 3.3 Auth confirmation

Success is signalled in the `tt` status word at bytes 6–7: **bit 15** flips from
clear (`0x0038`) to set (`0x8038`). The integration reads a fresh connection
counter, sends one auth frame, and waits for the bit; if it does not confirm, it
drops the link and retries with a fresh session on the next poll.

---

## 4. Data Fields

### 4.1 `tt` — DeviceList
| Field | Offset | Decode |
|-------|--------|--------|
| Auth status | 6–7, bit 15 | `0x8038` = authenticated |
| Connection counter | 11 | Keys the auth CRC |
| Firmware | 5 (major), 6 (minor, BCD) | `n = major*100 + bcd(minor)` → `"C{n//100}.{n%100:02d}"` |

### 4.2 `uu` sub-record 0 — Dashboard
| Field | Offset | Decode |
|-------|--------|--------|
| Current flow (gal/min) | 7–8 | `/100` |
| Soft water remaining (gal) | 9–10 | — |
| Water used today (gal) | 11–12 | — |
| Peak flow today (gal/min) | 13–14 | `/100` |
| Water hardness (gpg) | 15 | — |
| Regeneration hour | 16 | 1–12 |
| Regeneration AM/PM | 17 | `0` = AM |
| Battery ADC | 6 | `volts = byte*0.088`; piecewise → 0–100 % (0 on AC units) |
| Valve state | 18 | bit 3 = shutoff, bit 4 = bypass, bit 5 = display-off |

### 4.3 `uu` sub-record 1 — Brine tank
| Field | Offset | Decode |
|-------|--------|--------|
| Regenerations remaining | 13 | `0xFF` = brine tank not configured |
| Tank diameter (in) | 15 | `16`/`18`/`24`/`30` |
| Fill height (in) | 16 | — |
| Refill time (min) | 17 | — |

Derived salt values (residential softener):

```
lbs_per_inch  = {16: 8.1, 18: 10.4, 24: 18.6, 30: 29.55}[tank]
salt_total    = round(fill_height * lbs_per_inch)
salt_remaining = round(refill_time * 1.5 * regens_remaining)   # lb
salt_percent   = ceil(salt_remaining / salt_total * 100)
```

### 4.4 `uu` sub-record 2 — Usage graph
A 62-day daily-usage history assembled from the sub-record-2 frame plus three
untagged continuation frames (offsets 0..16, 17..36, 37..56, 57..61; each day =
`byte * 10` gal). **Average Daily Use** = the mean of the most recent 32 days
(indices 30..61), counting only non-zero days.

### 4.5 `vv` sub-record 0 — Config
| Field | Offset | Decode |
|-------|--------|--------|
| Days until regeneration | 3 | — |
| Regen-day override | 4 | `0` = disabled, else N days |
| Reserve capacity (%) | 5 | — |
| Resin capacity (grains) | 6–7 | `×1000` |

### 4.6 `vv` sub-record 1 — Cycle times
Regen cycle position durations in minutes (`byte & 0x7F`): byte 3 = Backwash,
byte 4 = Brine Draw, byte 5 = Rapid Rinse, byte 6 = Brine Refill.

---

## 5. Commands

Each command starts from a 20-byte buffer filled with the target record's tag
(`u`=`0x75`, `v`=`0x76`, `w`=`0x77`), with a command letter at byte 13 and values
following. Written to RX, Write Without Response, on the authenticated session.

| Command | Frame (tag-filled; overwrites) |
|---------|--------------------------------|
| Regenerate now | `u` … `[13]='R'(0x52) [14]='N'(0x4E)` |
| Regenerate next | `u` … `[13]='R' [14]='T'(0x54)` |
| Set water hardness | `u` … `[13]='H'(0x48) [14]=hardness` |
| Set regeneration time | `u` … `[13]='t'(0x74) [14]=hour(1–12) [15]=ampm(0/1)` |
| Sync clock | `u` … `[13]='T'(0x54) [14]=hour [15]=min [16]=ampm [17]=sec` |
| Bypass on/off | `u` … `[13]='R' [14]='B'(0x42) [15]=state` |
| Water shutoff on/off | `u` … `[13]='R' [14]='O'(0x4F) [15]=state` |
| Display on/off | `v` … `[13]='G'(0x47) [14]=state` |
| Set reserve capacity | `v` … `[13]='B'(0x42) [14]=percent` |
| Set resin grains | `v` … `[13]='C'(0x43) [14]=hi [15]=lo` (value = grains/1000) |
| Set regen-day interval | `v` … `[13]='A'(0x41) [14]=days` |
| Reset total gallons | `w` … `[13]='A'(0x41)` |
| Reset regen counter | `w` … `[13]='B'(0x42)` |

All values are clamped to the valve's accepted ranges before writing.

---

## 6. Connection Model

- The valve keeps a **stable BLE link** while actively polled, so the integration
  holds one connection open and refreshes on a short interval (default 3 s,
  configurable 2–3600 s). This gives near-real-time flow without reconnect churn.
- **Single connection slot:** the valve accepts only one BLE connection. While
  the integration is connected, the vendor phone app cannot connect (and vice
  versa). A longer poll interval leaves gaps for the app.
- **Auth lifecycle:** authentication runs once per connection. If a held session
  will not authenticate, the integration disconnects and starts a fresh session
  on the next poll; once confirmed it stays connected for fast polling.
- **Resilience:** up to four consecutive missed polls are tolerated (last-good
  values retained) before entities report unavailable, so brief RF hiccups do not
  blank the device.

---

## 7. Integration Architecture

```
custom_components/diamond_linq_softener/
    __init__.py     Config-entry setup, DataUpdateCoordinator, options reload
    ble_client.py   Connection lifecycle, auth, frame requests, command writes
    parser.py       Frame decoding (tt / uu / vv, salt, usage graph, state bits)
    config_flow.py  Discovery + manual setup, options (poll interval)
    sensor.py / switch.py / button.py / number.py / select.py   Entity platforms
    const.py        UUIDs, defaults, device info
```

- **`SoftenerBleClient`** owns the BLE connection via Home Assistant's Bluetooth
  stack (`async_ble_device_from_address` + `bleak_retry_connector`), so it works
  over local adapters and ESPHome proxies without adapter forcing. It authenticates,
  requests `tt`/`uu`/`vv` records, and exposes command methods.
- **`SoftenerDataUpdateCoordinator`** drives polling on the configured interval and
  applies the transient-miss grace period.
- **`FrameParser`** accumulates decoded values into a `DiamondLinqData` dataclass
  read by the entities.
- Discovery is triggered by a `bluetooth` matcher in `manifest.json` on the
  `CS_Meter_Soft` local name.

### 7.1 Entities

Read: flow, peak flow, soft water remaining, water used today, average daily use,
water hardness, regeneration time, days-until-regen, reserve capacity, resin
capacity, salt remaining/level/capacity, brine tank size, regenerations remaining,
regen-day-override, cycle times, firmware, battery.

Control: Regenerate Now / at Next / Sync Clock / counter resets (buttons); water
hardness / reserve / regen-day / resin (numbers); regeneration time (select);
display / bypass / water shutoff (switches, backed by the valve's reported state).
