# Zorin Copilot ↔ Home Assistant Integration (Examples)

[🇺🇸 English Version](README.en.md) | [🇧🇷 Versão em Português](README.md)

This directory provides working configuration examples to establish a **bidirectional, realtime bridge** between Zorin Copilot and Home Assistant:

```
  ┌──────────────────────┐   control (voice/text)   ┌──────────────────┐
  │   Zorin Copilot      │ ───────────────────────▶ │  Home Assistant  │
  │  (agent + voice)     │                          │   (Docker/LAN)   │
  │                      │ ◀─────────────────────── │                  │
  └──────────┬───────────┘    realtime state events └────────┬─────────┘
             │              (WebSocket state_changed)        │
             │                                               │
             │  desktop notification / voice announce        │
             └──────────  ha_bridge.py  ◀── rest_command ────┘
```

- **Copilot → HA**: Control lights, climate units, smart plugs, and **trigger scenes, scripts, and automations** via voice or text (`smart_home_control` / `smart_home_automate`).
- **HA → Copilot**: Copilot subscribes to `state_changed` events via WebSocket and detects home events instantly (`smart_home_events`, queries like *"what changed in the house recently?"*).
- **HA → Desktop**: Automations alerting the user invoke `scripts/ha_bridge.py`, which posts desktop notifications or speaks messages aloud via neural TTS.

---

## 1. Starting the HA → Desktop Bridge

```bash
export ZORIN_HA_BRIDGE_TOKEN="YOUR_SHARED_SECRET"     # identical value configured in Home Assistant
python3 scripts/ha_bridge.py --notify --speak
```

Test without Home Assistant:

```bash
curl -s -X POST http://127.0.0.1:8787/webhook \
  -H "X-Bridge-Token: YOUR_SHARED_SECRET" -H "Content-Type: application/json" \
  -d '{"event":"test","message":"Hello from Home Assistant"}'
```

Verify service health at any time: `curl -s http://127.0.0.1:8787/health`.

### Running as a systemd User Service

Create `~/.config/systemd/user/zorin-ha-bridge.service`:

```ini
[Unit]
Description=Home Assistant to Zorin Copilot Bridge
After=network-online.target

[Service]
ExecStart=%h/zorin-copilot/scripts/ha_bridge.py --notify --speak
Environment=ZORIN_HA_BRIDGE_TOKEN=YOUR_SHARED_SECRET
Restart=on-failure

[Install]
WantedBy=default.target
```

Enable and start the service:

```bash
systemctl --user daemon-reload
systemctl --user enable --now zorin-ha-bridge.service
```

---

## 2. Enable Real-Time Events in Zorin Copilot

Under **Preferences → Smart Home**, toggle **"Real-time Events (WebSocket)"** (or set `ha_realtime_enabled: true` in `config.json`). The WebSocket stream launches alongside the application when integration credentials are present.

---

## 3. Configuration Files

- `configuration.yaml` — `rest_command`, `shell_command`, and helper templates.
- `automations.yaml` — sample automations (doorbell, laundry, open windows).

Replace `YOUR_SHARED_SECRET` with your authorization token and adapt `entity_id` values to your specific smart home entity registry.

---

## Security Directives

- The webhook bridge binds to `127.0.0.1` by default — **never expose it directly to public networks**.
- Always supply a shared secret token via `--token` or `ZORIN_HA_BRIDGE_TOKEN`.
- The bridge **does not execute arbitrary shell commands** from incoming webhooks: it strictly logs events, displays desktop notifications, and performs text-to-speech synthesis.

