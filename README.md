# Home Assistant - Ecowitt Official Integration
 

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

 
This integration uses the locally available http APIs to obtain data from the supported devices inside the local network.

## :computer: Installation

### HACS (Preferred)
This integration can be added to Home Assistant as a [custom HACS repository](https://hacs.xyz/docs/faq/custom_repositories):
1. From the HACS page, click the 3 dots at the top right corner.
1. Select `Custom repositories`.
1. Add the URL `https://github.com/Ecowitt/ha-ecowitt-iot`
1. Select the category `Integration`.
1. Click the ADD button.
1. Restart Home Assistant
1. Click the button below, or in the HA UI go to "Configuration" -> "Integrations" click "+" and search for "Ecowitt Official Integration"

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Ecowitt&repository=ha-ecowitt-iot&category=integration)

### Manual
1. Download the latest release from [here](https://github.com/Ecowitt/ha-ecowitt-iot/releases).
1. Create a folder called `custom_components` in the same directory as the Home Assistant `configuration.yaml`.
1. Extract the contents of the zip into folder called `ha_ecowitt_iot` inside `custom_components`.
1. Restart Home Assistant
1. Click the button below, or in the HA UI go to "Configuration" -> "Integrations" click "+" and search for "Ecowitt Official Integration"

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Ecowitt&repository=ha-ecowitt-iot&category=integration)

## :bulb: Usage
Ecowitt Official Integration:
This integration uses the locally available HTTP APIs to obtain data from the supported devices inside the local network.
Ecowitt Official Integration Compatibility Instructions:
| Ecowitt Official Integration        |  IoT device    |Gateway Model|
|:-----------:|:-----------:|:-----------|
| ×      | ×      |GW1000,WS6006,WN1900,WN1910,WS2320,WS2910,HP2550,HP3500,HP2560|
| ✓      | ×      |GW1100|
| ✓  | ✓  |GW1200,GW2000,GW3000,WS6210,WN1700,WN1820,WN1821,WN1920,WN1980,WS3800,WS3820,WS3900,WS3910|

HA Default Integration: www.home-assistant.io/integrations/ecowitt/ 
This integration uses the HTTP upload to a 3rd-party to obtain data from the supported devices.
HA Default Integration Compatibility Instructions:
| HA Default Integration   |  IoT device    |Gateway Model|
|:-----------:|:-----------:|:-----------|
| ×      | ×      |WS6006|
| ✓      | ×      |GW1000,WN1900,WN1910,WS2320,WS2910,HP2550,HP3500,HP2560,GW1100,GW1200,GW2000,GW3000,WS6210,WN1700,WN1820,WN1821,WN1920,WN1980,WS3800,WS3820,WS3900,WS3910|


To set up Ecowitt Official Integration, follow these steps:
1. Configure your gateway device on your LAN using the WSView Plus app or Ecowitt app on your phone.
2. Obtain the device's IP address through the web UI or WSView Plus app.
3. Enter the device's IP address in the integration. Upon successful connection, the integration will retrieve data from the gateway device.



![Step 1](./img/TF1.jpg)
![Step 2](./img/TF2.jpg)
![Step 3](./img/TF3-3.jpg)
![Step 4](./img/TF4.jpg)

## :potted_plant: WFC01 timed "Quick Run" (fork addition)

> This section documents a feature added in this fork. It is **not** part of the
> upstream Ecowitt integration.

The upstream integration exposes the WFC01 water timer only as a plain on/off
switch that sends `always_on`, i.e. the valve runs until Home Assistant sends a
stop. If HA or the network fails mid-run, the water keeps flowing.

This fork adds a **device-side timer**: the run duration is sent to the gateway,
and the WFC01 stops by itself when the time is up, independent of HA and the
internet.

For each online WFC01 you now get:

- **Number "Run duration"** (`number.<device>_run_duration`) - the watering
  duration in seconds, persisted across restarts (default 300).
- **Button "Quick run"** (`button.<device>_quick_run`) - starts a run for the
  configured duration; the device auto-stops.

And two services (target a Quick run button entity):

- `ha_ecowitt_iot.quick_run` - start a run; optional `duration` field overrides
  the number entity for that call.
- `ha_ecowitt_iot.quick_stop` - stop immediately.

Example: water every 5 minutes only while the soil is dry, and lock out for 12h
once it gets wet (each run auto-stops via the device, so HA is never the only
thing that can stop the water):

```yaml
automation:
  - alias: "Aquaponik Puls alle 5 min"
    trigger:
      - platform: time_pattern
        minutes: "/5"
    condition:
      - condition: state
        entity_id: timer.bewaesserung_pause
        state: "idle"
      - condition: numeric_state
        entity_id: sensor.Soilmoisture_ch1
        below: 40
    action:
      - service: ha_ecowitt_iot.quick_run
        target:
          entity_id: button.wfc01_quick_run
        data:
          duration: 30
```

### How the timed run is sent

The quick run is sent to `parse_quick_cmd_iot` as `always_on:0` with a
`val_type`/`val` pair (the gateway ignores the `on_time` field for `quick_run`):

- `val_type:0`, `val:N` -> water for **N seconds**, then auto-stop
- `val_type:1`, `val:N` -> water for **N minutes**, then auto-stop

This integration uses **seconds** (`val_type:0`). Verified on a WFC01 running
firmware 114: `val_type:0, val:20` waters for ~20 s and the device stops itself.
The "Run duration" number is therefore in seconds.

## Compatibility Instructions for all ecowitt network consoles
![Compatibility Instructions for all ecowitt network consoles](https://oss.ecowitt.net/uploads/20260224/7d20e0c51af395cc66af81f2fa458115.png)

## Stargazers over time
[![Stargazers over time](https://starchart.cc/Ecowitt/ha-ecowitt-iot.svg?variant=adaptive)](https://starchart.cc/Ecowitt/ha-ecowitt-iot)
