# Bose SoundTouch Direct

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A full-featured Home Assistant custom integration for **Bose SoundTouch** speakers, communicating directly with the local SoundTouch Web API — no cloud, no third-party libraries, no polling lag.

> **Why not use the built-in `soundtouch` integration?**  
> The built-in integration hasn't been meaningfully updated in years. This integration adds real-time WebSocket push updates, TTS support, media browsing, multi-room zone management, bass control, track feedback, and a proper UI config flow with auto-discovery.

---

## Features

| Feature | Details |
|---|---|
| 🎵 **Full media player** | Play, pause, stop, next/prev track, volume, mute |
| 📻 **Source selection** | Switch between physical inputs (TV, HDMI) and network sources (Spotify, TuneIn, etc.) directly from HA |
| 🔢 **Presets** | Trigger any of the 6 hardware presets from automations |
| ⚡ **Real-time updates** | WebSocket push notifications — state changes appear instantly |
| 🔊 **Multi-room zones** | Create, expand, and dissolve speaker groups |
| 🎚️ **Bass control** | Adjust bass level per speaker (-9 to +9) |
| 👍 **Track feedback** | Thumbs up/down (Pandora), add/remove favourites |
| 🗣️ **TTS support** | Works as a target in the HA Media browser and `tts.speak` service — previous source restores automatically after TTS finishes |
| 🔔 **TTS from active source** | When TTS interrupts an active source (e.g. TV), a soft chime precedes the announcement to absorb the firmware fade-in, then the original source is restored |
| 💤 **TTS from standby** | When TTS wakes the device from standby, it plays the announcement then restores the last real source before returning to standby — so the next power-on resumes the correct input |
| 🔍 **Auto-discovery** | Finds devices automatically via Zeroconf/mDNS |
| 🔄 **Reconfigure** | Update IP address without losing entity history |
| 📋 **Device registry** | Shows firmware version, model, and manufacturer |

---

> ⚠️ **Bose cloud notice:** Bose is shutting down their cloud services in May 2026. This integration is designed to work entirely locally and is not affected.

## Requirements

- Home Assistant **2024.1** or later
- Bose SoundTouch speaker on the same local network as Home Assistant
- Ports **8090** (HTTP API) and **8080** (WebSocket) reachable from HA

---

## Installation

### Via HACS (recommended)

1. Open **HACS → Integrations → ⋮ → Custom Repositories**
2. Add this repository URL, set category to **Integration**, click **Add**
3. Search for **Bose SoundTouch Direct** and click **Download**
4. Restart Home Assistant

### Manual

1. Download the latest release zip and extract it
2. Copy the `soundtouch_direct` folder into `config/custom_components/`
3. Restart Home Assistant

---

## Setup

### Auto-discovery

If your SoundTouch speaker is on the same network, Home Assistant will detect it automatically via mDNS and show a notification. Click **Configure** to add it in one step.

### Manual

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Bose SoundTouch Direct**
3. Enter the IP address of your speaker (e.g. `192.168.1.50`)
4. Click **Submit** — the device and entity are created immediately

> **Tip:** Assign your speaker a static IP (or a DHCP reservation) so the address never changes. If it does change, use **Settings → Devices & Services → Bose SoundTouch Direct → Reconfigure** to update it without losing your entity.

---

## Text-to-Speech

The integration registers as a full media browser target, so your SoundTouch speaker will appear in:

- The **Media** panel TTS target picker
- The `tts.speak` service entity dropdown
- Any automation action targeting a media player

TTS is played via an internal stream proxy using the `LOCAL_INTERNET_RADIO` source, which works entirely locally without requiring Bose cloud services.

**Restore behaviour after TTS:**

- **Active source (e.g. TV):** The SoundTouch firmware fades in audio when switching sources. To compensate, a soft chime is automatically prepended to the announcement — the fade-in silences the chime, and speech starts cleanly. After TTS finishes, the original source is restored.
- **Standby:** The device is woken, the announcement plays, and the integration then restores the last known real source (persisted across HA restarts) before returning the device to standby. This ensures the next manual power-on resumes the correct input rather than the TTS WiFi source.

> **Note for soundbars with HDMI-CEC:** When the TV powers on it will switch the soundbar to TV input via CEC regardless of the last saved source. The standby restore is most relevant when powering the soundbar on independently of the TV.

Example automation using TTS:

```yaml
action:
  - service: tts.speak
    target:
      entity_id: tts.piper  # or whichever TTS engine you use
    data:
      media_player_entity_id: media_player.living_room
      message: "Dinner is ready!"
```

---

## Custom Services

All services target `media_player` entities from this integration.

### `soundtouch_direct.play_preset`

Play one of the 6 presets stored on the device (configured via the SoundTouch app).

```yaml
service: soundtouch_direct.play_preset
target:
  entity_id: media_player.living_room
data:
  preset_id: 2   # 1–6
```

### `soundtouch_direct.set_bass`

Adjust the bass level.

```yaml
service: soundtouch_direct.set_bass
target:
  entity_id: media_player.living_room
data:
  bass_level: 4   # -9 to +9, 0 is default
```

### `soundtouch_direct.play_everywhere`

Set this speaker as the zone master and group every other SoundTouch device on your network into it.

```yaml
service: soundtouch_direct.play_everywhere
target:
  entity_id: media_player.living_room
```

### `soundtouch_direct.create_zone`

Create a multi-room zone with specific speakers. Use device IDs (found in the device registry or the `device_id` config entry attribute).

```yaml
service: soundtouch_direct.create_zone
target:
  entity_id: media_player.living_room
data:
  master: "A1B2C3D4E5F6"
  slaves:
    - "F6E5D4C3B2A1"
    - "B2C3D4E5F6A1"
```

### `soundtouch_direct.add_zone_slave` / `remove_zone_slave`

Add or remove a speaker from an existing zone without dissolving it.

```yaml
service: soundtouch_direct.add_zone_slave
target:
  entity_id: media_player.living_room
data:
  slaves:
    - "F6E5D4C3B2A1"
```

### `soundtouch_direct.thumbs_up` / `thumbs_down`

Send track feedback on services that support it (Pandora, etc.).

```yaml
service: soundtouch_direct.thumbs_up
target:
  entity_id: media_player.living_room
```

### `soundtouch_direct.add_favorite` / `remove_favorite`

Bookmark or remove the currently playing track.

```yaml
service: soundtouch_direct.add_favorite
target:
  entity_id: media_player.living_room
```

---

## Source Selection

The integration exposes all `READY` sources from the device as a source list with friendly names (e.g. `TV`, `HDMI_1`, `ALEXA`, `TUNEIN`). The current source is also shown with its friendly name.

The default HA media player card does not prominently expose the source selector. For the best experience, add a **media-control card** to your dashboard:

```yaml
type: media-control
entity: media_player.your_soundtouch_entity
```

You can also switch source via a service call:

```yaml
service: media_player.select_source
target:
  entity_id: media_player.living_room
data:
  source: TV
```

---

## Extra State Attributes

The entity exposes these additional attributes beyond the standard media player ones:

| Attribute | Description |
|---|---|
| `station_name` | Internet radio station name |
| `station_location` | Station location or description |
| `source_account` | Account used for the current source (e.g. Spotify username) |
| `presets` | List of all 6 presets with their name and source |

---

## Example Automations

### Morning playlist — play preset 1 at 7am

```yaml
automation:
  alias: Morning music
  trigger:
    platform: time
    at: "07:00:00"
  action:
    service: soundtouch_direct.play_preset
    target:
      entity_id: media_player.bedroom
    data:
      preset_id: 1
```

### Pause everything when you leave home

```yaml
automation:
  alias: Pause on leaving
  trigger:
    platform: state
    entity_id: person.you
    to: not_home
  action:
    service: media_player.media_pause
    target:
      entity_id: all
```

### Announce doorbell on all speakers

```yaml
automation:
  alias: Doorbell announcement
  trigger:
    platform: state
    entity_id: binary_sensor.doorbell
    to: "on"
  action:
    service: tts.speak
    target:
      entity_id: tts.piper
    data:
      media_player_entity_id: media_player.living_room
      message: "Someone is at the front door."
```

### Party mode on button press

```yaml
automation:
  alias: Party mode
  trigger:
    platform: state
    entity_id: input_button.party_mode
  action:
    service: soundtouch_direct.play_everywhere
    target:
      entity_id: media_player.living_room
```

---

## Troubleshooting

**Device not found by auto-discovery**  
Ensure mDNS traffic is not blocked between your speaker's VLAN and Home Assistant. As a fallback, add the device manually using its IP address.

**Cannot connect**  
- Verify the SoundTouch app can reach the speaker from the same network
- Confirm port 8090 is not firewalled between HA and the speaker
- Assign the speaker a static IP or DHCP reservation to prevent address changes

**WebSocket not connecting / state updates are slow**  
The WebSocket listener uses port 8080. If this port is blocked, the integration automatically falls back to polling every 10 seconds — the entity will still work, just with slightly delayed state updates. Check **Settings → System → Logs** and filter by `soundtouch_direct` for details.

**Speaker not appearing as a TTS target**  
Make sure you are on the latest version of this integration. After updating the files, do a **full HA restart** (not just a reload).

**IP address changed**  
Go to **Settings → Devices & Services → Bose SoundTouch Direct → ⋮ → Reconfigure** and enter the new IP. Your entity history is preserved.

---

## How It Works

The integration communicates entirely on your local network:

| Channel | Port | Purpose |
|---|---|---|
| HTTP REST | 8090 | All API commands (play, volume, source, etc.) |
| WebSocket | 8080 | Real-time push notifications from the device |
| HA HTTP | 8123 | Internal stream proxy for TTS and live radio (served via `LOCAL_INTERNET_RADIO`) |

On startup, HA connects to the WebSocket and listens for notifications from the speaker (now playing changed, volume changed, etc.). When a notification arrives the coordinator immediately refreshes state — no polling required. If the WebSocket drops, the listener reconnects automatically with exponential backoff, and polling kicks in as a fallback in the meantime.

---

## API Reference

Built against the **Bose SoundTouch Web API**.  
Full spec: [SoundTouch-Web-API.pdf](https://assets.bosecreative.com/m/496577402d128874/original/SoundTouch-Web-API.pdf)

| Endpoint | Method | Description |
|---|---|---|
| `/info` | GET | Device info, firmware version, capabilities |
| `/now_playing` | GET | Current playback state, track, art |
| `/volume` | GET / POST | Get or set volume level |
| `/key` | POST | Send key press (play, pause, presets, etc.) |
| `/select` | POST | Select a source or play a URL |
| `/presets` | GET | All 6 stored presets |
| `/sources` | GET | Available and ready sources |
| `/bass` | GET / POST | Bass level get/set |
| `/bassCapabilities` | GET | Min/max bass for this device |
| `/getZone` | GET | Current multi-room zone config |
| `/setZone` | POST | Create a zone |
| `/addZoneSlave` | POST | Add a slave to a zone |
| `/removeZoneSlave` | POST | Remove a slave from a zone |
| `/recent` | GET | Recently played items |
| WebSocket `:8080` | WS | Push notifications for all state changes |

---

## License

MIT License — see [LICENSE](LICENSE) for details.  
Not affiliated with or endorsed by Bose Corporation.
