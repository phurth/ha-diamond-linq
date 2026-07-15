# Diamond Linq Water Softener — Home Assistant HACS Integration

Local Home Assistant integration for **Diamond Linq smart water softeners** (Chandler Systems "Signature" metered valve, BLE).

Monitors and controls the softener directly over Bluetooth Low Energy — live water flow and usage, salt level, regeneration schedule, and valve controls. No cloud, no MQTT bridge, and no internet required at runtime.

> **Disclaimer:** This is an independent community integration and is not affiliated with, endorsed by, or supported by Diamond Linq or Chandler Systems. Use it at your own risk.

## Features

Live readings refresh over a held-open Bluetooth connection (default every ~3 s, configurable).

### Sensors

| Entity | Unit | Notes |
|--------|------|-------|
| Current Flow | gal/min | Instantaneous water flow |
| Peak Flow Today | gal/min | Highest flow seen today |
| Soft Water Remaining | gal | Soft water before the next regeneration |
| Water Used Today | gal | Treated water used today |
| Average Daily Use | gal | Average of recent daily usage |
| Water Hardness | gpg | Configured grains-per-gallon hardness |
| Regeneration Time | — | Scheduled regen time of day (e.g. `2:00 AM`) |
| Days Until Regeneration | d | Days remaining before the next regeneration |
| Reserve Capacity | % | Reserve capacity setting |
| Resin Capacity | grains | Resin bed grain capacity (diagnostic) |
| Salt Remaining | lb | Salt remaining in the brine tank |
| Salt Level | % | Salt remaining as a percentage of capacity |
| Salt Capacity | lb | Total brine-tank salt capacity (diagnostic) |
| Brine Tank Size | — | Configured tank diameter (diagnostic) |
| Regenerations Remaining | — | Regenerations of salt remaining |
| Regen Day Override | — | Forced regen-day interval, or "Disabled" |
| Backwash / Brine Draw / Rapid Rinse / Brine Refill Time | min | Regen cycle step durations (diagnostic) |
| Firmware Version | — | Valve firmware (diagnostic) |
| Battery | % | Backup-battery level (diagnostic, disabled by default; hardwired units read 0) |

### Controls

| Entity | Type | Notes |
|--------|------|-------|
| Regenerate Now | `button` | Starts a regeneration cycle immediately |
| Regenerate at Next Time | `button` | Queues a regeneration for the next scheduled time |
| Sync Clock | `button` | Sets the valve clock from Home Assistant's time |
| Water Hardness | `number` | Set grains-per-gallon hardness (0–99) |
| Reserve Capacity | `number` | Set reserve capacity (0–49 %) |
| Regen Day Interval | `number` | Force a regeneration every N days (0 = disabled) |
| Resin Capacity | `number` | Set resin grain capacity (diagnostic) |
| Regeneration Time | `select` | Set the daily regeneration time |
| Display | `switch` | Turn the valve's front-panel display on/off |
| Bypass | `switch` | Put the valve into/out of bypass (unsoftened water) |
| Water Shutoff | `switch` | Shut off / restore water flow |
| Reset Total Gallons / Reset Regeneration Counter | `button` | Reset lifetime counters (diagnostic, disabled by default) |

## Requirements

- Home Assistant 2024.1+ with the Bluetooth integration
- Bluetooth coverage of the softener — either a local adapter on the Home Assistant host or an ESPHome Bluetooth proxy in range. Home Assistant routes automatically; no adapter is "forced."
- The softener's 4-digit access code (factory default `1234`).

## Installation (HACS)

1. In Home Assistant, open **HACS → Integrations → ⋮ (top right) → Custom repositories**.
2. Add `https://github.com/phurth/ha-diamond-linq` with category **Integration**.
3. Install **Diamond Linq Water Softener**.
4. Restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration → Diamond Linq Water Softener**
   (the softener may also auto-discover and appear as a "Discovered" device — it advertises as `CS_Meter_Soft`).
2. Select the softener (or enter its Bluetooth MAC address manually) and submit.
3. The integration connects, authenticates with the softener's access code, and begins streaming data.
4. Optionally set the **poll interval** — see below.

> The integration authenticates with the factory-default access code (`1234`). If your softener uses a different code, set it back to the default, or open an issue to request a configurable-code option.

### Poll interval

Readings update over a **held-open** Bluetooth connection. The poll interval (how often the coordinator refreshes) is configurable (2–3600 s, default 3) via **Settings → Devices & Services → Diamond Linq Water Softener → Configure**.

A short interval gives near-real-time flow but keeps the softener's single Bluetooth slot occupied. A longer interval frees the radio between reads so the Diamond Linq phone app can connect.

## Notes & Limitations

- **One Bluetooth connection at a time.** The softener allows only a single BLE connection. While the integration is enabled and polling frequently, the Diamond Linq phone app may be unable to connect. Increase the poll interval (or disable the integration) to free the link for the app.
- **Physical controls are real actions.** *Regenerate Now* starts an actual regeneration (uses water and salt); *Bypass* and *Water Shutoff* change your household water supply. They act immediately when pressed/toggled.
- **Instantaneous flow is sampled.** *Current Flow* reflects whatever a poll observes, so brief water use between polls may not register; use a shorter interval or watch *Peak Flow Today* / *Water Used Today* for totals.
- **Backup-battery reading.** Hardwired/AC-powered units report a battery level of 0; the Battery sensor is disabled by default.

## License

MIT
