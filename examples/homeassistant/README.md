# Integração Zorin Copilot ↔ Home Assistant (exemplos)

[🇺🇸 English Version](README.en.md) | [🇧🇷 Versão em Português](README.md)

Esta pasta traz os exemplos para deixar a integração **bidirecional e fluida**:

```
  ┌──────────────────────┐   controla (voz/texto)   ┌──────────────────┐
  │   Zorin Copilot      │ ───────────────────────▶ │  Home Assistant  │
  │  (agente + voz)      │                          │   (Docker/LAN)   │
  │                      │ ◀─────────────────────── │                  │
  └──────────┬───────────┘   eventos em tempo real   └────────┬─────────┘
             │              (WebSocket state_changed)         │
             │                                                │
             │  notifica/fala no desktop                      │
             └──────────  ha_bridge.py  ◀── rest_command ─────┘
```

- **Copilot → HA**: ligar luzes, ar-condicionado, tomadas **e acionar cenas,
  scripts e automações** por voz (`smart_home_control` / `smart_home_automate`).
- **HA → Copilot**: o Copilot assina `state_changed` por WebSocket e sabe na hora
  o que mudou (`smart_home_events`, perguntas como *"o que mudou na casa?"*).
- **HA → desktop**: automações que precisam avisar o usuário chamam a ponte
  `scripts/ha_bridge.py`, que mostra notificação e/ou fala a mensagem.

## 1. Subir a ponte HA → desktop

```bash
export ZORIN_HA_BRIDGE_TOKEN="SEGREDO"          # mesmo valor que o HA usará
python3 scripts/ha_bridge.py --notify --speak
```

Teste sem o Home Assistant:

```bash
curl -s -X POST http://127.0.0.1:8787/webhook \
  -H "X-Bridge-Token: SEGREDO" -H "Content-Type: application/json" \
  -d '{"event":"teste","message":"Olá do Home Assistant"}'
```

Verifique a saúde a qualquer momento: `curl -s http://127.0.0.1:8787/health`.

### Rodar como serviço do usuário (systemd --user)

`~/.config/systemd/user/zorin-ha-bridge.service`:

```ini
[Unit]
Description=Ponte Home Assistant -> Zorin Copilot
After=network-online.target

[Service]
ExecStart=%h/zorin-copilot/scripts/ha_bridge.py --notify --speak
Environment=ZORIN_HA_BRIDGE_TOKEN=SEGREDO
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now zorin-ha-bridge.service
```

## 2. Ligar os eventos em tempo real no Copilot

Em **Preferências → Casa Inteligente**, ative **"Eventos em Tempo Real
(WebSocket)"** (ou defina `ha_realtime_enabled: true` no `config.json`). O canal
sobe junto com o aplicativo quando a integração está configurada.

## 3. Copiar os exemplos

- `configuration.yaml` — `rest_command`, `shell_command` e helpers.
- `automations.yaml` — automações prontas (campainha, lavadora, porta aberta…).

Substitua `SEGREDO` pelo seu token e ajuste os `entity_id` para os seus.

## Segurança

- A ponte escuta em `127.0.0.1` por padrão — **não** a exponha na rede.
- Use sempre `--token`. Sem ele, qualquer processo local pode publicar eventos.
- A ponte **não executa comandos arbitrários** vindos da rede: só registra,
  notifica e fala.
