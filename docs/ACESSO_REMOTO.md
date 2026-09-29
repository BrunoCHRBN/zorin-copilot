# Acesso Remoto ao Zorin Copilot — celular e relógio

> **Status (21/09/2026): análise de arquitetura. Nada implementado.**
> Este documento responde a uma pergunta de desenho: dá para usar o Zorin Copilot
> de outros dispositivos — smartphone e Redmi Watch 5 Active/Lite — **sem portar a
> aplicação** e **sem exigir que estejam na mesma rede**? As seções 1–2 fixam o que
> o app é hoje (fatos lidos do código), 3–4 tratam de conectividade e autenticação,
> 5 dos limites reais dos dispositivos, 6–7 dos trade-offs e de uma arquitetura em
> fases, 8–9 do que não fazer e do que já existe para reusar.
>
> **Continuação:** `docs/ACESSO_REMOTO_KDE_CONNECT.md` — o transporte já está
> instalado nesta máquina (`kdeconnectd` 26.08.1). O plugin `runcommand` devolve
> `stdout` ao celular (canal de ida e volta), e o `sendnotifications` **não**
> repassa ações nem `inline-reply` (logo, sem botão de aprovação na notificação).
> Verificado no código-fonte dos plugins, não na documentação.

---

## 0. Sumário executivo

**Sim, é possível — mas não portando o app, e sim transformando um pedaço dele em
serviço.** Três conclusões mudam o desenho:

1. **O relógio não é um cliente de rede.** O Redmi Watch 5 Active/Lite lista
   **apenas Bluetooth 5.3** nas especificações oficiais (sem Wi-Fi), roda o
   HyperOS de wearable (RTOS) e a Xiaomi declara que **não aceita apps de
   terceiros**. Ele é um **periférico de exibição do celular**: não tem pilha
   TCP/IP, não fala HTTP nem MQTT, e não recebe SDK. Logo, "suporte ao relógio"
   não é um projeto de cliente — é *escolher um canal que gere boas notificações
   no celular*.
2. **O app é cliente, não servidor.** Todo o `src/` é saída; o único listener de
   produção do repositório é `scripts/ha_bridge.py` (`127.0.0.1:8787`). Não há
   socket próprio, não há API. Qualquer acesso remoto exige **criar** uma
   superfície de entrada — ela não existe hoje.
3. **A parte cara do app já roda sem GUI.** O CLI é 100% headless
   (`agent`, `ask`, `study`, `search`, `rag`, `config`). O trabalho remoto é,
   portanto, **empacotar o que já existe atrás de uma porta autenticada** — não
   reescrever o assistente.

**Recomendação:** não expor o desktop. Começar pela fase F0/F1 (notificação de
saída + aprovação remota por canal que já existe), e só depois considerar F2/F3
(consulta e comando remotos) — sempre atrás de malha privada ou túnel, com token
por dispositivo e escopo, e **nunca** liberando a superfície de ações cruas
(AT-SPI/ydotool/`run_command`), que é execução remota de código por design.

---

## 1. O que o app é hoje (fatos que restringem o desenho)

| Fato | Onde | Consequência para acesso remoto |
|---|---|---|
| **Não há servidor.** Único listener de produção é a ponte HA: `ThreadingHTTPServer` em `127.0.0.1:8787`, rotas `POST /webhook`, `POST /ha/event`, `GET /health`, `GET /events`. | `scripts/ha_bridge.py` | É o **molde** do que um gateway remoto seria — mas hoje é loopback e unidirecional. |
| **A ponte não executa comandos**, por decisão explícita de projeto: "os únicos efeitos são log, notificação de desktop e fala". | `scripts/ha_bridge.py:9-10` | Precedente forte: *receber da rede ≠ obedecer a rede*. Se a ponte virar canal de comando, perde a garantia. |
| **IPC = instância única do GTK** (`io.github.bruno.ZorinCopilot`, `HANDLES_COMMAND_LINE`); `do_command_line` roteia `--toggle/--voice/--dictate/--background`. | `ui/app.py:3216,3407` | Dá para acordar a UI de fora (via `Gio`), mas **só na sessão gráfica local** — não serve de API. |
| **MCP: só consome** (stdio + SSE). Não se expõe como servidor. | `mcp/client.py`, `mcp/manager.py` | Expor o Copilot como servidor MCP é trabalho novo (F4), não configuração. |
| **Aprovação já existe.** `RiskLevel.CONFIRM` + `RiskPolicy.classify`; `Step.requires_approval` e callback `on_approval`; CLI com `--yes`. MCP mutante forçado a `CONFIRM`. | `shell/risk.py`, `ai/agent.py:379-392,529` | **É o gancho natural da aprovação remota** — o ponto de extensão já está no lugar certo. |
| **Segredos** em `~/.config/zorin-copilot/config.json`, modo `0600`, com fallback por variável de ambiente. Sem keyring. | `core/config.py:293` | Um token de dispositivo a mais cabe no mesmo arquivo — mas rotação/revogação não existem. |
| **Token da ponte** por header `X-Bridge-Token` **ou `?token=`**, comparado com `hmac.compare_digest`. | `ha_bridge.py:84-88` | Comparação correta; **token em query string é dívida** (vaza em log de proxy, histórico e referrer). |
| **HA é bidirecional** — REST para comandar, WebSocket `state_changed` para receber, com reconexão por backoff. | `core/home_assistant.py`, `core/home_assistant_realtime.py` | Se o HA já é alcançável de fora, **ele já é um caminho pronto** para chegar ao desktop. |
| **CLI headless** cobre agente, busca, RAG, estudo, config, `--json`, `--dry-run`, `--yes`. | `cli.py` | Metade do "usar de fora" já está resolvida sem display. |
| **Áudio é local.** Captura por `pw-record`/`arecord`; STT Whisper local; TTS piper. | `core/dictation.py:268`, `ai/local_voice.py` | **O microfone é o do desktop.** O celular não empresta o mic para o Copilot ouvir. |

---

## 2. O que "usar de fora" pode significar

Cinco intenções distintas, com custos e riscos muito diferentes. Tratá-las como
uma coisa só é o erro que produz tanto um app inútil quanto um RCE.

| # | Intenção | Precisa de sessão gráfica local? | Já funciona headless? |
|---|---|---|---|
| **A** | **Consultar** — perguntar ao assistente, RAG, busca web, ler documentos | Não | ✅ `cli.py ask/rag/search` |
| **B** | **Receber** — notificação de tarefa concluída, eventos de casa, quadro negro de estudo | Não | ✅ ponte HA + `notify-send`/`spd-say` |
| **C** | **Aprovar** — responder ao pedido de confirmação de uma ação sensível | Não | ✅ `on_approval` existe; falta um transporte |
| **D** | **Comandar o desktop** — clique, digitação, janelas, abrir app | **Sim** (AT-SPI2, libei/ydotool, Mutter) | ❌ só na sessão |
| **E** | **Capturar** — screenshot, ditado por voz | **Sim** para tela; mic é do desktop | ⚠️ parcial |

**O que é remotamente viável de verdade:** A, B e C. **O que é viável mas
perigoso:** D. **O que é ilusão:** E com áudio do celular.

Dos ~60 nomes de ferramenta em `ai/agent_tools.py`, cerca de 30 **exigem sessão
gráfica** (`click_element`, `type_element`, `keyboard_*`, `mouse_click`,
`capture_screen`, `focus_window`, `window_management`, `launch_app`,
`media_control`, `system_control`, `study_show_board`…). Os outros ~30 são
computação/arquivo/HTTP puros (`list_directory`, `read_file`, `web_search`,
`read_web_page`, `search_documents`, `git_*`, `system_info`, `memory_remember`,
`smart_home_*`, `docker_*`, `network_*`) e **podem ser invocados de fora sem
tocar na sessão**. Essa é a linha de corte natural de qualquer gateway remoto.

---

## 3. Conectividade sem depender da mesma rede

Cinco topologias. Todas funcionam com o desktop atrás de NAT ou CGNAT; a diferença
está em quem termina a conexão, o que fica exposto e quanto custa manter.

### 3.1 Malha privada (WireGuard / Tailscale / Headscale) — **recomendada**

O desktop entra numa rede overlay; o celular entra também. O tráfego sai por
UDP e ambos se alcançam por IP fixo da malha (ex.: `100.x.y.z`), mesmo em redes
diferentes, sem abrir porta no roteador e sem IP público.

- **Como o pacote chega:** direto peer-a-peer (com DERP/TURN como fallback).
- **Exposição:** nenhuma à internet pública; o serviço pode continuar em
  `127.0.0.1` e ser publicado só na interface da malha.
- **Autenticação:** identidade de dispositivo por chave (WireGuard) — mais forte
  que token compartilhado; ACLs por dispositivo na versão administrada.
- **Custo:** gratuito para uso pessoal no plano gerenciado; auto-hospedado
  (Headscale) exige um VPS.
- **Limite:** **o relógio não entra na malha** (sem Wi-Fi, sem app). A malha
  resolve celular e notebook; o relógio continua sendo notificação via celular.

### 3.2 Túnel reverso (Cloudflare Tunnel, ngrok, frp, `ssh -R`)

O desktop **abre** uma conexão de saída para um ponto público; o tráfego de volta
chega por esse túnel. Nada de porta aberta no roteador.

- **Como o pacote chega:** URL pública (`https://algo.exemplo.ts.net`), TLS
  terminado na borda.
- **Exposição:** o serviço **fica na internet**. Sem camada de identidade
  (Cloudflare Access / OAuth na frente), o token é a única barreira.
- **Autenticação:** idealmente Access/Zero Trust + token do app (defesa em
  profundidade); caso contrário, TLS + token.
- **Custo:** gratuito nos planos básicos; domínio próprio no Cloudflare.
- **Limite:** latência do caminho de volta; depende do provedor estar no ar;
  o `ssh -R` precisa de um host sempre ligado.

### 3.3 Relay em VPS (broker próprio / MQTT)

Um servidor pequeno faz de intermediário: o desktop assina um tópico, o celular
publica. Bom para **notificação e comando assíncrono** (não precisa de conexão
simultânea).

- **Exposição:** só o VPS; o desktop nunca é alcançável diretamente.
- **Autenticação:** usuário/senha por dispositivo no broker + TLS; ACLs por tópico.
- **Custo:** VPS barato, manutenção contínua.
- **Limite:** fila de mensagens não é sessão interativa; latência de publicação;
  o desktop precisa estar de pé para consumir.

### 3.4 Home Assistant como hub — **o caminho mais barato hoje**

O HA já é o ponto médio: `HomeAssistantManager` fala REST e WebSocket com ele, e
`scripts/ha_bridge.py` recebe webhooks dele. Se o HA já é acessível de fora
(Nabu Casa, túnel próprio ou malha), **o celular já tem um app instalado que fala
com o HA** — e esse app suporta **notificações acionáveis** (botões na
notificação) que disparam webhooks de volta.

- **Como o pacote chega:** HA → webhook autenticado → ponte no desktop.
- **Exposição:** a mesma que o HA já tem. Nada novo na internet.
- **Autenticação:** token do HA (já configurado) + `X-Bridge-Token` da ponte.
- **Custo:** zero, se o HA já existe.
- **Limite:** **assíncrono** — não é um cliente interativo; e o que trafega
  passa pela infraestrutura do HA (nuvem da Nabu Casa, se for o caso).
- **Vantagem decisiva:** é o único caminho que **já está construído até a
  metade** — falta apenas a direção de volta (aprovação) e o empacotamento de
  consultas.

### 3.5 Nuvem de terceiros como transporte (bot de chat / ntfy / Gotify)

Um bot do Telegram (ou ntfy/Gotify para push puro) faz as vezes de gateway: o
desktop **consulta** a API (saída), o celular fala com o serviço.

- **Exposição:** **nenhuma porta de entrada no desktop.** Ele só faz requisições
  de saída — o melhor perfil de risco de todas as opções.
- **Autenticação:** token do bot + **allow-list de `chat_id`** (essencial: sem
  ela, qualquer pessoa que descubra o bot conversa com o seu desktop).
- **Custo:** zero.
- **Limite:** dependência de terceiro, conteúdo trafegando por servidor alheio,
  latência de polling/long-poll, e **o relógio só vê a notificação** — não
  interage com o bot (não há app de Telegram no HyperOS de wearable).
- **Vantagem:** é o caminho que **mais rapidamente chega ao pulso**, porque a
  notificação do app do celular é espelhada para o relógio por Bluetooth.

### Comparação

| Topologia | Porta aberta? | Funciona atrás de CGNAT? | Relógio recebe? | Exposição | Custo | Manutenção |
|---|---|---|---|---|---|---|
| Malha privada (Tailscale) | Não | Sim | Via celular | Mínima | Baixo | Baixa |
| Túnel reverso | Não | Sim | Via celular | **Alta** (serviço público) | Baixo | Baixa |
| Relay em VPS | Não (no desktop) | Sim | Via celular | Média | Médio | **Alta** |
| **HA como hub** | Herdada do HA | Sim | Via celular | A do HA | **Zero** | **Baixa** |
| Bot de chat / ntfy | **Não** | Sim | Via celular | **Mínima** | Zero | Baixa |

---

## 4. Requisitos de autenticação e autorização

O modelo atual é honesto mas insuficiente para exposição: **um token
compartilhado** em `config.json` (0600), comparação em tempo constante, sem
rotação, sem revogação, sem escopo, e aceito também em **query string**.

Mínimos para qualquer superfície remota:

| Requisito | Por quê | Estado hoje |
|---|---|---|
| **TLS ou túnel cifrado** | Token em claro é credencial vazada | Só se a topologia fornecer |
| **Token por dispositivo**, não compartilhado | Revogar o celular perdido sem derrubar o resto | ❌ inexistente |
| **Escopo por token** (`ler` / `aprovar` / `executar`) | Um celular comprometido não deve poder executar | ❌ inexistente |
| **Pareamento com código de uso único** (expira em minutos) | Evita copiar token à mão e vazamento em log | ❌ inexistente |
| **Revogação e rotação** | Resposta a incidente | ❌ inexistente |
| **Nunca token em URL** | Query string vaza em log de proxy/histórico/referrer | ⚠️ `ha_bridge.py` aceita `?token=` |
| **Rate limit + proteção contra replay** (timestamp + nonce) | Reenvio de requisição capturada | ❌ inexistente |
| **Auditoria de origem** (quem pediu, de onde, quando) | Investigar depois | ⚠️ parcial (log da ponte) |
| **Origem remota ⇒ `CONFIRM` no mínimo** | O risco é o efeito, não o pedido | ✅ `RiskPolicy` já classifica |

**Regra que não deve ser flexibilizada:** requisição de origem remota **nunca**
deve poder pular a aprovação — `--yes` e equivalentes ficam reservados à
interação local e explícita. A aprovação remota é uma *resposta a um pedido que o
desktop já fez*, não uma autorização prévia para agir.

---

## 5. Limites de compatibilidade dos dispositivos

### 5.1 Smartphone (Android 8.0+ / iOS 12.0+)

Qualquer uma das cinco topologias serve. As formas de cliente, do mais barato ao
mais caro:

- **PWA** (uma página servida pelo gateway) — sem loja, sem build nativo, funciona
  em Android e iOS; instalável na tela inicial.
- **Atalhos/Shortcuts, Tasker, "HTTP Shortcuts"** — o celular chama o gateway
  direto, sem app nenhum. Ótimo para ações de um toque ("o que o Copilot está
  fazendo?", "aprovar a última ação").
- **Bot de chat** — zero cliente para escrever; só funciona onde há app.
- **Cliente MCP** — só alguns apps de IA no celular suportam MCP; hoje é nicho.

### 5.2 Redmi Watch 5 Active / Lite — o que é fisicamente possível

| Capacidade | Active | Lite | Evidência |
|---|---|---|---|
| Wi-Fi | ❌ | ❌ | Especificação oficial lista **apenas "Bluetooth® 5.3"** |
| Instalar apps de terceiros | ❌ | ❌ | Xiaomi: *"does not support downloading third-party Apps"* |
| SDK de desenvolvedor | ❌ | ❌ | Sem loja; Vela é plataforma para parceiros, não para o usuário final |
| App companion obrigatório | Mi Fitness | Mi Fitness | Pareamento e sincronização só via Mi Fitness |
| Notificações do celular | ✅ espelhadas | ✅ espelhadas | Recurso documentado (com as notificações do app habilitadas no Mi Fitness) |
| Resposta a notificação | ⚠️ limitada | ⚠️ limitada | Só respostas prontas, quando o app do celular as oferecer |
| Assistente de voz próprio | Do fabricante | Do fabricante | Não programável |
| Sistemas | HyperOS (wearable/RTOS) | HyperOS (wearable/RTOS) | Não é Wear OS |

**Consequências diretas:**

1. **O relógio não é um peer de rede.** Sem Wi-Fi e sem pilha HTTP, ele nunca
   fala com o desktop nem com o gateway. Toda a comunicação é
   **celular → internet → desktop**, e o relógio é o **último centímetro por
   Bluetooth**.
2. **Não é Wear OS**, então nem o app companion do Home Assistant (que tem versão
   para Wear OS) roda nele. Isso fecha a rota "app do HA no pulso".
3. **A rota não oficial também está fechada.** Os relatos de instalação de apps
   por ADB em relógios Xiaomi exigem que o **relógio esteja numa rede Wi-Fi**
   (ou no hotspot do celular). Como Active/Lite não têm Wi-Fi, essa via não existe
   nesses modelos.
4. **O que sobra é exatamente o que basta para a fase F0/F1:** notificação
   espelhada (o pulso mostra o que o celular recebe) e, no máximo, um punhado de
   respostas prontas. **Não prometer** digitação livre, comandos por voz ao
   Copilot, nem aprovação de dois toques no relógio.

### 5.3 Matriz de capacidade

| Uso pretendido | Desktop | Celular | Watch 5 Active/Lite |
|---|---|---|---|
| Consultar o assistente | ✅ | ✅ (PWA/atalho/bot) | ❌ |
| Receber notificação/resultado | ✅ | ✅ | ✅ (espelhada) |
| Aprovar ação sensível | ✅ | ✅ (botão na notificação) | ⚠️ no máximo resposta pronta |
| Comandar o desktop | ✅ | ⚠️ só com escopo e aprovação | ❌ |
| Ver tela / usar mic do celular | ✅ | ❌ | ❌ |

---

## 6. Trade-offs, lado a lado

| Abordagem | Esforço | Superfície de ataque | Latência | Relógio | Funciona sem mesma rede | Manutenção |
|---|---|---|---|---|---|---|
| **F0** Push de saída (ntfy/Gotify/HA notify) | **Muito baixo** | **Nenhuma porta** | Baixa | ✅ | ✅ | Mínima |
| **F1** Aprovação remota (notificação acionável → webhook) | Baixo | Baixa (webhook já existe) | Baixa | ⚠️ | ✅ | Baixa |
| **F2** Gateway HTTP de **consulta** (só tools puros) | Médio | Média | Baixa | ❌ | ✅ (malha/túnel) | Média |
| **F3** Gateway de **comando supervisionado** | **Alto** | **Alta** | Baixa | ❌ | ✅ | **Alta** |
| **F4** Copilot como **servidor MCP** | Alto | Média-alta | Baixa | ❌ | ✅ | Média |
| Bot de chat (transversal a F0–F3) | Baixo | Mínima | Média (polling) | ✅ (leitura) | ✅ | Baixa |
| HA como hub (transversal) | **Baixo** | A do HA | Média | ✅ (leitura) | ✅ | **Baixa** |

Leitura da tabela: **o valor por unidade de risco está todo em F0/F1**. F3 é a
única linha que exige vigilância permanente — porque é, literalmente, um canal de
execução remota no seu desktop.

---

## 7. Arquitetura em fases

### F0 — Notificação de saída (leitura pura)

O desktop **publica** o que aconteceu; o celular recebe, e o relógio espelha.

- **Como:** reusar a ponte e o `notify-send`/`spd-say`; adicionar um destino de
  push (ntfy/Gotify/HA `notify`) configurável em `config.json`.
- **Requisito:** nenhum. Nenhuma porta de entrada, nenhum token novo.
- **Entrega:** "a tarefa terminou", "o agente pediu permissão", eventos de casa.
- **Não entrega:** nenhuma forma de perguntar nada.

### F1 — Aprovação remota (o ganho maior pelo menor custo)

A notificação carrega botões; o toque dispara um webhook autenticado que
**resolve um pedido de aprovação que o desktop já fez**.

- **Como:** `ai/agent.py` já tem `Step.requires_approval` e o callback
  `on_approval`. Falta (a) expor o pedido pendente como notificação acionável e
  (b) um endpoint autenticado que responde sim/não **para aquele `step_id`**.
- **Requisito:** token do dispositivo, `step_id` de uso único, **expiração curta**
  (um pedido não pode ser aprovado horas depois), e a decisão é vinculada ao
  pedido — não é uma autorização genérica.
- **Por que é seguro:** não adiciona nenhuma capacidade de execução. O desktop já
  ia executar; o celular só responde "sim" ou "não" a uma pergunta específica.
- **Cuidado:** reaproveitar o token da ponte **não** basta — precisa de escopo
  próprio e de vínculo com o `step_id`.

### F2 — Consulta remota (gateway de leitura)

Um gateway HTTP autenticado expondo **apenas os ~30 tools puros** (arquivo,
busca, RAG, `system_info`, `smart_home_*`), publicados **na interface da malha ou
atrás do túnel** — nunca em `0.0.0.0` cru.

- **Como:** o caminho mais curto é reusar `cli.py` (já headless e com `--json`)
  por trás de um servidor fino, ou um servidor que chame o `ToolRegistry`
  **filtrado** — o `ToolRegistry.restricted(names)` de `ai/subagents.py` já faz
  exatamente esse recorte de schema, e foi escrito para isso.
- **Requisito:** token por dispositivo com escopo `ler`; TLS (fornecido pela
  topologia); rate limit.
- **Não entra:** qualquer tool que toque na sessão gráfica. O recorte é por
  allow-list, no **schema** — não por validação a posteriori (mesma lição dos
  subagentes).

### F3 — Comando supervisionado (só se F2 provar valor)

Allow-list mínima de tools de efeito, cada uma passando por `RiskPolicy` com
`CONFIRM` obrigatório e aprovação de volta pelo canal de F1.

- **Requisito:** escopo `executar` separado, janela de tempo, auditoria de origem,
  e limite de ações por hora.
- **Trade-off honesto:** isto é um canal de execução remota no desktop. O valor
  precisa ser grande para justificar a manutenção e o risco.

### F4 — Copilot como servidor MCP

Expor o catálogo de tools por MCP (streamable HTTP) para um cliente MCP no
celular. Reusa `ToolRegistry` + `adapter.py`; a novidade é o lado servidor.

- **Limite atual:** o cliente MCP do projeto fala **stdio e SSE**, não streamable
  HTTP — então o transporte tem de ser implementado, não configurado.
- **Por que pode valer:** um cliente MCP no celular ganharia o catálogo inteiro
  sem PWA nem app próprio.

---

## 8. Riscos e o que **não** fazer

1. **Nunca `bind` em `0.0.0.0` sem TLS e token.** O único listener do repo está em
   loopback por bom motivo. O `core/network_actions.py` já tem
   `network_audit_exposure` — usar antes e depois de qualquer publicação.
2. **Nunca expor a superfície crua.** `run_command`, `keyboard_*`, `click_*`,
   `write_document`, `organize_directory` são execução remota de código por
   projeto. Se entrarem em F2/F3 sem aprovação, o resultado é um backdoor.
3. **Não transformar a ponte em canal de comando.** Ela foi escrita para *não*
   executar nada; a garantia mora nessa restrição.
4. **Não aceitar token em query string** em nada novo (e corrigir a ponte).
5. **`--yes` nunca para origem remota.**
6. **Não depender de porta aberta no roteador.** CGNAT é comum; e abrir porta
   expõe a máquina inteira, não só o serviço.
7. **Não prometer o que o relógio não faz.** Sem Wi-Fi, sem apps, sem SDK: a
   interface é a notificação. Prometer interação no pulso é promessa falsa.
8. **Não confundir canal de notificação com canal de controle.** São escopos,
   tokens e superfícies diferentes — inclusive no HA.

---

## 9. O que já existe e pode ser reusado

| Necessidade | Módulo existente | Observação |
|---|---|---|
| Servidor HTTP com token em tempo constante | `scripts/ha_bridge.py` | Molde pronto; hoje loopback e sem escopo |
| Eventos de casa em tempo real | `core/home_assistant_realtime.py` | WebSocket com reconexão por backoff |
| Comando do HA | `core/home_assistant.py` | REST com retry; singleton precisa reler config |
| **Aprovação de ação sensível** | `shell/risk.py`, `ai/agent.py` | `CONFIRM` + `on_approval` — gancho de F1 |
| Execução headless | `cli.py` | `--json`, `--dry-run`, `--yes`, `--max-steps` |
| Catálogo de tools + recorte por allow-list | `ai/agent_tools.py`, `ai/subagents.py` | `ToolRegistry.restricted(names)` recorta o **schema** |
| Segredos | `core/config.py` | `config.json` 0600 + env; falta escopo/rotação |
| Auditoria de desfecho | `core/board_outcome.py` | Padrão de "não promover desconhecido a sucesso" |
| Auditoria de exposição de rede | `core/network_actions.py` | `network_audit_exposure`, `network_mdns_services` |
| Ponte HA→desktop | `scripts/ha_bridge.py` | Precedente: receber ≠ obedecer |

---

## 10. Próximos passos (decisão pendente)

1. **Escolher a topologia** — malha privada (mais segura), túnel (mais simples de
   publicar) ou HA como hub (mais barato, já meio construído).
2. **Definir o modelo de token** antes de escrever qualquer endpoint: por
   dispositivo, com escopo, com revogação. Depois é caro retrofitar.
3. **Implementar F0 + F1** como primeiro incremento — é o que efetivamente chega
   ao celular *e* ao relógio, com o menor risco de todo o conjunto.
4. **Só então avaliar F2**, medindo se a consulta remota é usada. F3/F4 ficam
   condicionadas a isso.
