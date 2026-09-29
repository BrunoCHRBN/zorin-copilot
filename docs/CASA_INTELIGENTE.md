# Integração de Casa Inteligente & IoT — Zorin Copilot

O **Zorin Copilot** possui um subsistema nativo de integração com Casa Inteligente e Internet das Coisas (IoT), permitindo controlar lâmpadas inteligentes (dimerização, temperatura Kelvin, cores RGB), ar-condicionado, tomadas, interruptores e cenas diretamente por **comandos de voz ao vivo (Gemini Live)** ou pelo **Modo Agente autônomo**.

---

## 1. Arquitetura da Integração

A integração é **bidirecional**: o Copilot controla a casa **e** fica sabendo do
que acontece nela em tempo real.

```
┌───────────────────────────────────────────────────────────────────┐
│                    Usuário (Voz, Vídeo ou Texto)                  │
└──────────────┬───────────────────────────────────┬────────────────┘
               │                                   │
               ▼                                   ▼
    [Gemini Live WebSocket]               [Agente ReAct / Chat]
    • live.py                             • agent_tools.py
    • smart_home_control                  • smart_home_control
    • smart_home_status                   • smart_home_status
    • smart_home_automate                 • smart_home_automate
    • smart_home_events                   • smart_home_events
               │                                   │
               └────────────────┬──────────────────┘
                                │
                                ▼
        [HomeAssistantManager (core/home_assistant.py)]
        • Cliente REST leve (com retry em queda de rede)
        • Cache inteligente de estados (8s)
        • Cenas, scripts e automações (scene/script/automation)
        • Parser de palavras-chave em português (Kelvin, cores)
                                │
              ┌─────────────────┴──────────────────┐
              ▼ (controla)                        ▼ (assina)
   [Home Assistant — REST]            [HA Realtime — WebSocket]
   /api/services/...                  core/home_assistant_realtime.py
              │                        • subscribe_events (state_changed)
              │                        • pub/sub + buffer de eventos
              │                        • reconexão com backoff
              │                                   │
              └─────────────────┬─────────────────┘
                                ▼
               [Home Assistant (Docker Local ou Rede)]
                                │
         ┌──────────────────────┼──────────────────────┐
         ▼                      ▼                      ▼
  [Lâmpadas Wi-Fi]      [Ar-Condicionado]      [Tomadas / Switches]
  Avant Neo, Tuya,      Wi-Fi nativo ou        Shelly, Sonoff,
  Philips Hue, Shelly   Broadlink IR Mini      Smart Plugs Tuya
                                │
                                ▼ (avisa o desktop)
                    [Ponte HA→Zorin — scripts/ha_bridge.py]
                    • webhooks de automações → notificação/fala
                    • sem dependências externas, com token
```

### 1.1 O que mudou (limitações que a versão anterior tinha)

| Limitação anterior | Impacto no dia a dia | Como foi resolvido |
|---|---|---|
| **Só polling REST** — o Copilot só sabia do estado quando perguntava | "O que mudou na casa?" era impossível; nada era proativo | Canal **WebSocket** assinando `state_changed` (`home_assistant_realtime.py`) |
| **Só light / climate / switch** | Cenas, scripts e automações (o que há de mais útil) ficavam de fora | `list_*` + `trigger_scene` / `run_script` / `trigger_automation` |
| **Sem caminho HA → desktop** | Automações não conseguiam avisar o usuário pelo Copilot | Ponte `scripts/ha_bridge.py` (webhook → notificação/fala) |
| **Falha de rede = erro seco** | Reinício do container do HA derrubava o comando | Retry curto com backoff só para falhas transitórias |
| **Sem diagnóstico** | Difícil saber se o problema era URL, token ou container | `health()` (configurado / alcançável / tempo real ativo) |
| **Estado só via rede** | Consulta lenta e dependente de cache | Buffer local de eventos (`snapshot_states`) para resposta instantânea |

---

## 2. Aparelhos Suportados

### 2.1 Iluminação Inteligente (Avant Neo, Tuya, Smart Life, Shelly, etc.)
- **Ligar / Desligar / Alternar (Toggle)**.
- **Brilho e Dimerização:** Ajuste percentual de 1% a 100% ou escala 0-255.
- **Temperatura de Cor (Kelvin):**
  - Mapeamento por palavras-chave:
    - *"quente"*, *"âmbar"*, *"relax"* $\rightarrow$ **2700K** (Branco quente aconchegante)
    - *"suave"* $\rightarrow$ **3200K**
    - *"neutro"*, *"natural"* $\rightarrow$ **4000K** (Branco neutro balanceado)
    - *"dia"* $\rightarrow$ **4500K**
    - *"frio"*, *"foco"*, *"estudo"*, *"trabalho"* $\rightarrow$ **6500K** (Branco frio de alta produtividade)
- **Cores RGB:**
  - Nomes em português (*"vermelho"*, *"azul"*, *"verde"*, *"amarelo"*, *"laranja"*, *"roxo"*, *"rosa"*, *"ciano"*, *"branco"*) ou código hexadecimal (`#FF5500`).

### 2.2 Climatização & Ar-Condicionado
- **Ligar / Desligar**.
- **Temperatura alvo:** Ajuste em graus Celsius (ex: 22°C).
- **Modos de Operação (HVAC):** `cool` (frio / refrigerar), `heat` (quente / aquecer), `fan_only` (ventilação), `auto` (automático).

### 2.3 Tomadas & Interruptores (Switches)
- **Ligar / Desligar / Alternar** tomadas de computador, monitores, cafeteiras ou iluminação de bancada.

### 2.4 Cenas, Scripts e Automações
O atalho que mais rende no dia a dia: uma frase aciona vários aparelhos de uma vez.
- **Cenas** (`scene.*`): *"aciona a cena cinema"*, *"modo boa noite"*.
- **Scripts** (`script.*`): *"roda o script de café"*.
- **Automações** (`automation.*`): *"dispara a automação de alarme"* (ignora as condições, dispara na hora).
- **Descoberta**: *"quais cenas eu tenho?"* → `list_scenes` / `list_scripts` / `list_automations`.

### 2.5 Eventos em Tempo Real
Com o canal WebSocket ligado, o Copilot **sabe** das mudanças sem perguntar:
- *"O que mudou na casa agora?"* → `smart_home_events`.
- Perguntas sobre o estado atual respondem do **buffer local** (instantâneo, sem bater na API).
- Base para reações/proatividade (ex.: anunciar quando algo muda).

---

## 3. Configuração Passo a Passo

### Passo 1: Iniciar o Home Assistant no Docker

Na sua máquina Linux:
```bash
docker run -d \
  --name homeassistant \
  --privileged \
  --restart=unless-stopped \
  -e TZ=America/Sao_Paulo \
  -v "$HOME/.homeassistant:/config" \
  --network=host \
  ghcr.io/home-assistant/home-assistant:stable
```

### Passo 2: Parear a Lâmpada Avant Neo (Tuya)
1. Conecte a lâmpada no aplicativo **Smart Life** (ou Tuya Smart) pelo celular.
2. Acesse no navegador: `http://localhost:8123` e finalize a criação do usuário.
3. Vá em **Configurações > Dispositivos e Serviços > Adicionar Integração > Tuya**.
4. Escaneie o QR Code na tela usando o app Smart Life no celular (**Perfil > Escanear QR Code**).

### Passo 3: Gerar Token de Acesso de Longa Duração
1. No Home Assistant, clique no seu perfil de usuário (canto inferior esquerdo).
2. Na aba **Segurança**, role até o final em **Tokens de Acesso de Longa Duração**.
3. Clique em **Criar Token**, atribua um nome (ex: `zorin-copilot`) e copie a chave gerada.

### Passo 4: Conectar no Zorin Copilot
1. Abra o Zorin Copilot e acerte as preferências (<kbd>Ctrl</kbd> + <kbd>,</kbd> ou menu lateral).
2. Selecione a aba **Casa Inteligente**.
3. Insira a URL (`http://localhost:8123`) e cole o **Token de Acesso**.
4. Clique em **Testar Conexão** para validar.

---

## 4. Exemplos de Comandos por Voz (Gemini Live)

Com o Gemini Live ativo (<kbd>Ctrl</kbd> + <kbd>M</kbd> ou <kbd>Super</kbd> + <kbd>Shift</kbd> + <kbd>V</kbd>):

* *"Zorin, liga a lâmpada em branco quente e coloca o brilho em 30%."*
* *"Aumenta a luz para foco total a 100%."*
* *"Muda a luz para azul suave."*
* *"Como está a iluminação do quarto agora?"*
* *"Coloca o ar-condicionado em 22 graus no modo frio."*
* *"Apaga todas as luzes e desliga a tomada do monitor."*
* *"Aciona a cena boa noite."* (cenas/scripts/automações)
* *"Quais cenas eu tenho?"*
* *"O que mudou na casa nos últimos minutos?"* (eventos em tempo real)

---

## 5. Eventos em Tempo Real (WebSocket)

O canal REST só responde ao que se pergunta. Para o Copilot **reagir** — saber que
a porta abriu, que a luz mudou, que um botão foi pressionado — ele assina
`state_changed` por WebSocket.

- **Ligar**: em *Preferências → Casa Inteligente*, ative **"Eventos em Tempo Real
  (WebSocket)"**, ou defina no `config.json`:
  ```json
  { "ha_realtime_enabled": true, "ha_realtime_entities": "light.quarto,binary_sensor.porta" }
  ```
  (`ha_realtime_entities` é opcional; vazio = todas as entidades.)
- **Uso**: a ferramenta `smart_home_events` lista as mudanças recentes; o método
  `HomeAssistantManager.on_event(callback)` assina mudanças no código.
- **Robustez**: reconexão automática com backoff exponencial (1s → 2s → … → 30s).
  O cliente é **sem dependências externas** (RFC 6455 sobre `asyncio`).

```python
from zorin_copilot.core.home_assistant import HomeAssistantManager

ha = HomeAssistantManager.get_default()
ha.start_realtime()                       # liga o canal
cancelar = ha.on_event(                   # reage só a luzes
    lambda evt: print(evt["friendly_name"], "->", evt["state"]),
    domains={"light"},
)
# ... mais tarde: cancelar(); ha.stop_realtime()
```

---

## 5.1 Anúncios Falados (o Copilot fala sozinho)

Até então o canal de tempo real tinha um problema de desenho: **ele subia, recebia os eventos e
ninguém os consumia**. O Copilot só sabia das mudanças se você perguntasse. Com os anúncios
ligados, ele passa a **falar** quando algo acontece — sem turno do usuário.

Ligue em *Preferências → Casa Inteligente → **Anunciar Eventos em Voz Alta***. Exige **Eventos
em Tempo Real** ligados: sem o canal não há evento para ouvir, e o app avisa em vez de ficar
calado.

```
[luz acende]  →  WebSocket state_changed  →  decide_announcement()  →  Piper  →  🔊
                                                 (puro)                (só se valer)
```

### O que é anunciado

Só transições com frase conhecida. Não há narração genérica de "mudou para X" — um `sensor.*`
de temperatura não vira locutor de leituras.

| Domínio | Transição | Frase |
|---|---|---|
| `light` | ligou / desligou | *"Luz da sala acendeu."* / *"… apagou."* |
| `switch` | ligou / desligou | *"Tomada do monitor ligou."* |
| `binary_sensor` (porta, janela, garagem) | abriu / fechou | *"Porta da frente abriu."* |
| `binary_sensor` (movimento, fumaça, água, gás) | disparou | *"Sensor da escada detectou movimento."* |
| `lock` | trancou / destrancou | *"Fechadura trancou."* |
| `cover` | abriu / fechou | *"Persiana abriu."* |
| `person` | chegou / saiu | *"Bruno chegou em casa."* |

**Fica em silêncio** em: `unknown`/`unavailable`, transição em que o estado não mudou, eventos
de conexão do canal, e domínios sem frase conhecida.

### O que impede o anúncio de atropelar

Três coisas contam como "ocupado", e todas importam:

1. uma fala de anúncio já em andamento;
2. **uma sessão de voz ao vivo ativa** — você está conversando, não é hora de avisar que a porta
   abriu;
3. o sintetizador no meio de uma frase.

A decisão inteira vive em `core/ha_announce.py`, um módulo **puro** (sem GTK, sem áudio, sem
rede) com desfechos nomeados — `announce`, `disabled`, `not_state`, `no_state`, `filtered`,
`unchanged`, `no_phrase`, `busy`. Um booleano jogaria fora o motivo, e é o motivo que se precisa
ler no log quando nada é falado.

### Requisito de voz

A fala usa o **Piper local** (`piper-tts`), que é uma dependência **opcional** do projeto. Sem
ela o anúncio sai mudo — e o app **avisa no arranque**, por log e por toast, em vez de falhar em
silêncio a cada evento. Para instalar:

```bash
uv sync --extra voice
```

---

## 6. Ponte HA → Desktop (scripts/ha_bridge.py)

Fecha o ciclo: uma automação do Home Assistant avisa o usuário pelo desktop
(notificação e/ou fala). Veja `examples/homeassistant/` para configuração e
automações prontas.

```bash
ZORIN_HA_BRIDGE_TOKEN=SEGREDO python3 scripts/ha_bridge.py --notify --speak
```

No Home Assistant, a automação chama:

```yaml
rest_command:
  zorin_notify:
    url: "http://127.0.0.1:8787/webhook"
    method: POST
    headers:
      X-Bridge-Token: "SEGREDO"
      Content-Type: "application/json"
    payload: '{"event":"{{ event }}","message":"{{ message }}"}'
```

Endpoints: `POST /webhook` (evento), `GET /health` (diagnóstico),
`GET /events?limit=N` (histórico). A ponte escuta em `127.0.0.1` e **não executa
comandos arbitrários** — só registra, notifica e fala.

### 6.1 Quem fala pela ponte: uma boca só

A ponte **não tem voz própria**. Ela pede a frase ao Copilot
(`zorin-copilot --announce=<texto>`, pela instância única do GTK — o mesmo IPC de
`--toggle`/`--voice`) e só recorre ao `spd-say` quando não há app rodando:

| `--voice` | Comportamento |
|---|---|
| `auto` (padrão) | Voz do Copilot; se não houver instância, `spd-say`. |
| `copilot` | Só a voz do Copilot. Prefere o silêncio a um segundo timbre. |
| `system` | Só o `spd-say`; nunca chama o app. |

```bash
ZORIN_HA_BRIDGE_TOKEN=SEGREDO python3 scripts/ha_bridge.py --notify --speak --voice auto
```

Por que não falar por conta própria: enquanto a ponte tinha a própria boca, a casa
tinha **duas vozes de timbres diferentes** e nenhuma arbitragem entre elas. Agora
quem decide se é hora de falar é o `_claim_speaker()` do app, que também conhece a
sessão de voz ao vivo — a ponte não sabe que o usuário está no meio de uma conversa.

Os códigos de saída do `--announce` são o contrato entre os dois:

| Saída | Significado | O que a ponte faz |
|---|---|---|
| `0` | O app aceitou a fala | Nada mais. **Não** repete pelo `spd-say`. |
| `3` | Não havia app rodando | Cai no `spd-say` (em `auto`). |
| `4` | Pedido vazio | Nada: não havia o que dizer. |

Duas consequências práticas de `auto`:

* **A fala não bloqueia o webhook.** Pelo Copilot o pedido é entregue e a
  resposta volta na hora; o `spd-say` é que bloqueia (usa `-w`).
* **`0` significa "aceito", não "falado".** A frase sai numa thread do app. Se o
  motor de voz local faltar, o app registra
  `O anúncio não saiu pelo alto-falante: <texto>` — é essa a linha a procurar
  quando o webhook responde 200 e a casa fica muda.

> **Sem `spd-say` instalado, `--voice system` fica mudo** — e `auto` também, se o
> app não estiver rodando. A ponte avisa no arranque
> (`não há boca disponível: ...`) em vez de aceitar webhooks em silêncio.

---

## 7. Configuração Avançada (config.json)

| Chave | Padrão | Descrição |
|---|---|---|
| `ha_enabled` | `true` | Liga a integração de casa inteligente. |
| `ha_url` | `http://localhost:8123` | Endereço do Home Assistant. |
| `ha_token` | `""` | Token de Acesso de Longa Duração. |
| `ha_default_light` | `""` | Lâmpada padrão quando o comando não especifica. |
| `ha_realtime_enabled` | `false` | Assina eventos por WebSocket (abre conexão persistente). |
| `ha_realtime_entities` | `""` | Filtro de entidades (vírgula). Vazio = todas. |
| `ha_bridge_token` | `""` | Segredo da ponte `ha_bridge.py` (ou env `ZORIN_HA_BRIDGE_TOKEN`). |
| `ha_announce_enabled` | `false` | Fala sozinho quando algo muda na casa (§5.1). Exige `ha_realtime_enabled`. |
| `ha_announce_entities` | `""` | Filtro de entidades para os anúncios (vírgula). Vazio = todas as que tiverem frase. |

> O token também pode vir da variável de ambiente `HOME_ASSISTANT_TOKEN`, e a URL
> de `HOME_ASSISTANT_URL` — útil para não guardar segredo no arquivo de config.

Salvar as preferências tem **efeito imediato**: o gerenciador relê a configuração
(por `mtime` + reset explícito ao salvar), então não é preciso reiniciar o app para
o novo token/URL valerem — nem durante uma sessão de voz em andamento.

---

## 8. Solução de Problemas

**A voz diz "Home Assistant não configurado", mas "Testar Conexão" funciona.**
Era um bug real: o gerenciador era criado uma vez no arranque (token ainda vazio) e
nunca relia o `config.json`; a tela de preferências conectava porque cria uma
instância própria com o token digitado. **Corrigido** — agora o gerenciador relê a
configuração quando ela muda. Se você estiver numa versão anterior, reinicie o app
após salvar.

**A voz diz "Nenhuma lâmpada encontrada".** Confira se há alguma entidade do tipo
`light.*` integrada e online (veja em *Preferências → Entidades descobertas*). Antes,
essa mensagem aparecia até quando o problema era falta de configuração — hoje a
mensagem distingue os dois casos.

**A voz diz que a Casa Inteligente está desabilitada.** O interruptor
*"Habilitar Casa Inteligente"* está desligado nas preferências.

**O container não sobe / "port is already allocated".** Provavelmente já há um
Home Assistant rodando (`docker ps`). Para conferir o que está no ar:
```bash
docker ps --format '{{.Names}} | {{.Status}} | {{.Ports}}'
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8123/
```

**No navegador: "not in control of any dashboard".** É um *lock* de dashboard do
front-end do HA (outra aba ou o app do celular segurando o controle), **não** é
problema de Docker. Feche as outras abas/app, volte para `http://localhost:8123` e
clique em "Take control of dashboard" se o aviso aparecer.

**`docker compose` não existe.** Neste sistema o plugin não está instalado; use
`docker run` (comando em §3) ou instale o plugin do compose.
