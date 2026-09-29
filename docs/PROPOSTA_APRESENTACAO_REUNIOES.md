# Proposta — Apresentação Visual do Assistente em Reuniões (Teams / Meet / Zoom)

**Data:** 2026-09-22
**Autoria:** Vera (assistente de bancada)
**Escopo:** evidência do código atual. Nada aqui é especulativo — cada afirmação aponta para o arquivo e a linha. Ver também `docs/PROPOSTA_FUNCIONALIDADES_AO_VIVO.md` e `docs/PROPOSTA_COMPORTAMENTO_AO_VIVO.md`.

---

## 1. Tese (leia isto primeiro)

A pergunta pressupõe que o assistente *aparece dentro* do Teams / Google Meet / Zoom — ou como um
participante com conta própria, ou ativado por um participante. O código atual **não suporta nenhum
dos dois como participante nativo da chamada**. O Zorin Copilot é um **app de desktop local** (GTK4 +
Libadwaita, roda na máquina do usuário). Ele não tem SDK de bot, token de bot, nem nenhuma chamada
`join_meeting`.

Evidência:

- Grep por `Teams|Google Meet|Zoom|meet.google|teams.live|zoom.us` em `src/` → único resultado é
  `zoom-fit-best-symbolic` em `src/zorin_copilot/ui/widgets/prompt_bar.py:332` (um botão de
  *capturar tela inteira*, nada a ver com videoconferência).
- Grep por `def join|join_meeting|meeting_url|join_url|participar|entrar.*reunião` em `src/` →
  **zero matches**. Não existe primitiva de entrar numa reunião.

Portanto os dois cenários da pergunta têm de ser **reenquadrados**:

- **Cenário 1 (conta dedicada):** exige uma integração de *bot* de plataforma que **não existe**.
  Descrevo abaixo o que o tile *dentro* da chamada exigiria e o que já temos do lado local para
  espelhar isso. É trabalho novo, não reaproveitamento.
- **Cenário 2 (ativado por participante sem conta):** é o **estado real hoje**. O participante
  (Bruno) roda o app localmente; a presença do assistente é renderizada **só na máquina dele**, como
  janela flutuante / pílula / card sobreposta ao desktop. Os *outros* participantes não veem o
  assistente — a menos que o usuário compartilhe a tela (e, nesse caso, veem a janela flutuante dele).

A restrição dura que já vale para os dois cenários (de `PROPOSTA_FUNCIONALIDADES_AO_VIVO.md`):
**não há captura de áudio do sistema (loopback/monitor)**. Só o microfone é capturado
(`pw-record`/`arecord` em `src/zorin_copilot/core/dictation.py:266` e em
`src/zorin_copilot/ai/local_voice.py:678`). Ou seja: em nenhum dos dois cenários o assistente "ouve"
os outros participantes a menos que (a) o usuário compartilhe a tela *com áudio* numa plataforma que
permita isso, ou (b) seja construída a ponte de loopback. No caminho local isso não existe; no
caminho Gemini Live, só a câmera/tela do usuário entra (`is_video_streaming()` do local retorna
`False` em `local_voice.py:654`).

---

## 2. O que JÁ existe na UI (reaproveitável para os dois cenários)

### 2.1 Card de sessão ao vivo — `src/zorin_copilot/ui/live_view.py:183-320`

Este é o elemento central. Já existe e já tem os indicadores de presença/estado:

- `status_dot` (`:190`) — ponto de estado da sessão (`status-dot`).
- `status_lbl` (`:194`) — `"<b>Zorin Copilot Live</b>"`.
- `badge_box` + `timer_lbl` (`:201-211`) — cronômetro tabular "Ao Vivo · 00:00", nasce oculto,
  aparece quando a chamada começa. É o indicador de **tempo de sessão ativa**.
- `video_badge` (`:213-218`) — `"● TELA (1 FPS)"` verde; aparece quando há streaming de tela
  (caminho Gemini). Indicador de **estado ativo de vídeo**.
- `rec_lbl` (`:223-226`) — `"● Gravando"`; nasce oculto, só aparece com captação real
  (ver `_sync_recording_indicator`). Indicador de **microfone aberto**.
- Visualizador de áudio `Gtk.DrawingArea` 280×85 (`:251-283`) — onda/barra/matriz animada.
- **Live Captions** (`:285-309`): `caption_speaker_lbl` ("COPILOT") + `caption_text_lbl`
  (selecionável, quebra linha). É a base de uma **transcrição flutuante**.

> Este card é a "janela flutuante" do Cenário 2. Ele já é o painel lateral/caption do assistente
> local. Falta: diarização (rótulo de quem fala), rolar histórico, e — essencial — receber o áudio
> dos outros participantes.

### 2.2 Indicadores da pílula de voz — `src/zorin_copilot/ui/voice_pill.py`

A pílula flutuante (overlay via `wlr-layer-shell`/floating) também já tem linguagem visual de
presença:

- `rec_dot` (`:346-353`) — "●" sobre o avatar; visível quando `self._video_streaming`
  (`:1013`, `:1140`); pulsa com o tick quando há vídeo (`:1633-1635`).
- `mic_dot` (`:414-420`) — "●" cuja opacidade segue o nível do microfone
  (`0.30 + 0.70 * lvl`, `:1703`); some se não há nível (`:1694-1699`, `:758`, `:236`).
- `avatar_box` + `pill-privacy-on` (`:337-358`, `:1021-1029`) — borda de privacidade; tooltip
  "Privacidade: …" (`:1031`). Mostra se a câmera/tela está sendo capturada.
- `_show_chip` (`:1051`) — chip transitório de feedback de ferramenta (ex.: "Voz: ligado", `:1365`).
- `status_dot` secundário na `status_row` (`:1182-1186`) — "●" se streaming, "○" se parado.

Esses são os **avatar + indicador de estado ativo** do Cenário 2. Já existem; só não estão *dentro*
do Teams/Meet/Zoom.

### 2.3 Caminhos de voz (define o que o assistente consegue fazer)

- `GeminiLiveClient` (`src/zorin_copilot/ai/live.py:1286`) — WebSocket bidirecional, áudio + vídeo +
  tool-calling. Suporta streaming de tela a 1 FPS (daí o `video_badge`). Reconecta com backoff
  (`max_attempts=5`, `live.py:1743`; `delay = min(base*2**(attempt-1), max)`, `:1778`); watchdog 12s
  (`:2341`). É o único caminho que permite tradução/troca de idioma e ver a tela.
- `LocalLiveVoiceClient` (`src/zorin_copilot/ai/local_voice.py`) — 100% offline, `language="pt"`
  fixo (`:470`), `is_video_streaming()` retorna `False` (`:654`), **sem reconexão**. Bom para
  transcrição PT-BR local, sem tradução, sem vídeo.

---

## 3. Cenário 1 — Assistente com conta dedicada (bot na plataforma)

### 3.1 O que isto exigiria (NÃO existe hoje)

Para o assistente ser um *tile* dentro do Teams/Meet/Zoom com nome e avatar próprios, seria
necessário, para cada plataforma, um dos seguintes — nenhum presente no repo:

- **Microsoft Teams:** Bot Framework / Microsoft Graph `calls` API ( `joinScheduledMeeting`,
  media bot com áudio mixed recebido via `mediaSession`). Exige tenant, app registration, permissão
  `Calls.AccessMedia.All`.
- **Google Meet:** não há API pública de bot que entre numa chamada e receba áudio. O caminho
  realista é *Google Meet add-on* (side panel via Calendar/Meet SDK) ou captura de áudio do sistema
  no host. Nenhum dos dois existe.
- **Zoom:** Meeting SDK (funcionalidade "join as a participant/bot") ou Zoom Apps side panel. Exige
  SDK proprietário + assinatura de cliente. Não existe.

Ou seja: "conta dedicada" = construir um **serviço de bot fora do app GTK**, que depois empurra áudio/
transcrição pro `GeminiLiveClient` ou pro `LocalLiveVoiceClient`. É um novo subsistema, não um
rearranjo da UI existente.

### 3.2 O que os *outros participantes* veriam (tile dentro da chamada)

Se o bot fosse construído, o tile na grade do Teams/Meet/Zoom seguiria o padrão de qualquer
participante e deveria carregar, no mínimo:

| Elemento | Em Teams/Meet/Zoom | Estado no código |
|---|---|---|
| **Avatar** | foto/ícone do "Zorin Copilot" no tile | inexistente no app; seria o `avatar_box` (`:337`) *espelhado* no tile remoto |
| **Indicador de presença** | anel/ponto "ao vivo" no avatar | `status_dot` (`:190`, `:1182`) já existe localmente |
| **Rótulo de estado** | "Zorin Copilot · Transcrevendo" | `status_lbl` (`:194`) / `rec_lbl` (`:223`) existem localmente |
| **Badge de vídeo** | "● Tela" / "● Câmera" | `video_badge` (`:213`) existe localmente (só Gemini) |
| **Transcrição/Caption** | legendas na call ou painel lateral | `caption_*` (`:292-304`) existe localmente; do lado da plataforma seria o *Live Captions* nativo ou um add-on panel |
| **Painel lateral** | side panel do Meet/Teams/Zoom app | não existe; seria um add-on proprietary por plataforma |

O ponto-chave: **a plataforma é quem renderiza o tile**, não o GTK. O app só fornece os *dados*
(nome, estado, transcrição). A UI que o usuário vê *dentro* da call pertence ao Teams/Meet/Zoom.

### 3.3 O que o *usuário dono do bot* veria na própria máquina Linux

Espelhamento local do `live_view.py` (seção 2.1): o mesmo card com `status_dot`, `timer_lbl`,
`video_badge`, `rec_lbl` e `caption_*`. Hoje isso já funciona como **overlay local**; no Cenário 1
ele vira um "espelho de controle" do bot remoto. Nenhuma UI nova — só alimentar os mesmos widgets
com o estado do bot.

### 3.4 Riscos específicos do Cenário 1

- **Loopback ainda é necessário** para ouvir a sala se o bot não receber áudio misturado da
  plataforma (Meet não entrega; Zoom/Teams só via SDK de mídia). Sem áudio misturado, o bot ouve só
  o mic do host → mesma limitação do Cenário 2.
- **Reconexão:** o `GeminiLiveClient` reconecta (`:1743`); um bot numa call de 1h precisa de
  *rejoin* na plataforma também, que não existe.
- **Privacidade/consentimento:** participante-bot em reunião alheia tem implicação legal (LGPD/GDPR).
  O `pill-privacy-on` (`:1021`) é a linguagem visual certa para sinalizar captação — mas do lado da
  plataforma isso teria de ser explícito na entrada da call.

---

## 4. Cenário 2 — Ativado por participante sem conta própria (estado real hoje)

Este é o cenário que o código **já cobre parcialmente**. O assistente não é um participante da call;
é um overlay na máquina de quem o ativou.

### 4.1 O que o *participante que ativou* vê (na própria máquina Linux)

Tudo já construído (seção 2):

1. **Janela flutuante / pílula** (`voice_pill.py`) — overlay não-focável ancorado no desktop
   (Wayland via `wlr-layer-shell`; fallback `move()` no-op, ver `PROPOSTA_COMPORTAMENTO_AO_VIVO.md`).
   Leva `avatar_box` (`:337`), `rec_dot` (`:346`), `mic_dot` (`:414`), `status_dot` (`:1182`).
2. **Card de sessão ao vivo** (`live_view.py:183`) — com `status_dot`, `status_lbl`,
   `badge_box`+`timer_lbl` (cronômetro), `video_badge` (se tela ativa), `rec_lbl` ("● Gravando").
3. **Visualizador de áudio** (`:251`) — onda animada 280×85, confirma que está "vivo".
4. **Live Captions** (`:285`) — `caption_speaker_lbl`="COPILOT" + `caption_text_lbl` rolável e
   selecionável. É a "transcrição flutuante".
5. **Chip de ferramenta** (`_show_chip`, `:1051`) — feedback transitório ("Voz: ligado").

### 4.2 O que os *outros participantes* veem

**Nada do assistente** — a menos que o ativador compartilhe a tela. Se compartilhar:
- Eles veem a **janela flutuante/card do ativador** como qualquer outra janela compartilhada.
- Eles NÃO veem um tile próprio do "Zorin Copilot" (não há bot).
- A `caption_text_lbl` (`:296`) só mostra o que o assistente fala/devolve ao ativador — não a fala
  dos outros (sem loopback, sem diarização).

### 4.3 O que falta para o Cenário 2 ser "completo"

| Necessidade | Estado | Onde construir |
|---|---|---|
| Ouvir os outros participantes | **ausente** (só mic) | loopback/monitor capture (`dictation.py:266`, `local_voice.py:678`) |
| Rótulo "quem fala" (diarização) | ausente | novo no `live_view.py` caption |
| Histórico rolante de caption | parcial (`caption_container` só 1 linha) | `live_view.py:287` |
| Tradução da fala alheia | só via Gemini Live | `live.py:1286` + loopback |
| Indicar "assistente ativo" p/ terceiros | N/A (não é participante) | — |

---

## 5. Catálogo de elementos de UI pedidos, mapeado à realidade

| Elemento pedido | Cenário 1 (bot) | Cenário 2 (local) |
|---|---|---|
| **Avatar** | tile da plataforma (novo) + espelho de `avatar_box` (`:337`) | `avatar_box` já existe (`:337`) |
| **Indicador de presença/estado** | `status_dot` (`:190`/`:1182`) espelhado; anel da plataforma (novo) | `status_dot` + `rec_dot` + `mic_dot` já existem |
| **Painel lateral** | add-on proprietary por plataforma (inexistente) | o próprio `live_view.py` card é o painel |
| **Transcrição** | Live Captions nativo da plataforma OU add-on (novo) | `caption_*` (`:292-304`) já existe |
| **Janela flutuante** | espelho de controle local do bot | `voice_pill.py` + `live_view.py` já existem |

---

## 6. Limitações cruzadas (valem para os dois cenários)

1. **Sem loopback de áudio** — o assistente não ouve a sala (microfone só, `local_voice.py:678`,
   `dictation.py:266`). Este é o bloqueio #1 para "entender a reunião".
2. **Sem diarização** — não há rótulo de quem fala; `caption_speaker_lbl` fixo em "COPILOT"
   (`:292`).
3. **Sem resumo contínuo** — `get_session_summary` só no fim (`live.py:1486`); não há rolling
   summary durante a call.
4. **Reconexão** — Gemini reconecta (`:1743`); caminho local não (`:654`). Gap de até segundos sem
   buffer.
5. **Sem swap na máquina** — risco de OOM sob carga (suíte só via `run_tests_safe.py`); uma call de
   áudio+VLM+TTS simultânea pode estourar memória.
6. **Anti-slop** — qualquer UI nova deve seguir Libadwaita: ícones simbólicos (`*-symbolic`),
   sem gradiente decorativo, sem emoji em botão. O `live_view.py` já obedece (`:229-232`,
   `:243-247`).

---

## 7. Recomendação (próximo passo defensável)

Não construir bot de plataforma ainda. O valor entregável hoje é **fechar o Cenário 2**:

1. Adicionar **loopback/monitor capture** (PipeWire `pw-record` do `monitor_SOURCE`) em
   `dictation.py` / `local_voice.py` — habilita ouvir a sala.
2. Estender `caption_container` (`:287`) para **histórico rolante** + **diarização simples** (rótulo
   por energia/VAD de canal).
3. Ligar `caption_text_lbl` ao `GeminiLiveClient` para **tradução ao vivo** da fala alheia.
4. Só depois, se houver cliente/tenant, avaliar o bot (Cenário 1) como subsistema separado que
   reaproveita os mesmos widgets de estado.

Assim a apresentação visual do assistente em reuniões é honesta: ele é um **overlay local
competente** hoje, e um **participante nativo** só após a integração de bot — que é trabalho novo,
não reaproveitamento.
