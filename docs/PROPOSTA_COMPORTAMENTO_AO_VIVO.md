# PROPOSTA — Comportamento na GUI e em reunião ao vivo

> Continuação de `PROPOSTA_FUNCIONALIDADES_AO_VIVO.md`. Aqui descrevo *como a
> implementação se comportaria de fato* na interface e numa reunião real, com base no
> código dos widgets e dos clientes de voz (`ui/voice_pill.py`, `ui/dictation_osd.py`,
> `ui/widgets/chat_stream.py`, `ai/live.py`, `ai/local_voice.py`). Cada afirmação aponta
> arquivo:linha. Sem suposição.

## 1. Na interface gráfica (GUI)

### 1.1 O que existe para desenhar

As funcionalidades "ao vivo" não teriam uma tela própria — elas se apoiam em três widgets
já existentes:

- **Pílula flutuante (`VoicePillWindow`)** — a face principal da voz ao vivo
  (`ui/voice_pill.py:108`). Janela sem decoração, ancorada via `wlr-layer-shell` no Wayland
  (`_init_layer_shell`, `:299`), com visualizador de áudio em `Gtk.DrawingArea` desenhado a
  Cairo no *frame clock* (`_draw_wave_func`, `:1708`; `_on_visualizer_tick`, `:1617`), label
  de status (`max_width_chars=28`, `:394`), timer de sessão, dot de REC, dot de microfone,
  chip de ferramenta e borda de privacidade.
- **OSD de ditado (`DictationOSDWindow`)** — pílula menor, `Gtk.LevelBar` + label, usada no
  ditado push-to-talk (`ui/dictation_osd.py`). Não é a tela de conversa.
- **Fluxo de conversa (`ChatStream`)** — balões por turno; reconstrói iterando
  `ctx.session.turns` (`chat_stream.py:543`) e rola para o fim (`scroll_to_bottom`, `:551`).

### 1.2 Renderização e responsividade (o que é seguro)

- **Thread-safety está correta.** Todo callback do cliente que toca widget passa por
  `GLib.idle_add` (`voice_pill.py:547-577`): `on_state_change`, `on_audio_level`,
  `on_error`, `on_tool_executed`, `on_video_state_change`, `on_privacy_state_change`,
  `on_window_focus_change`. Ou seja, a thread do microfone **não** escreve em widget — o
  main loop consome. Isso impede travamentos/clitches por concorrência GTK.
- **Visualizador é barato e só roda visível.** O `_draw_wave_func` pinta uma área de
  80×26 px e só é agendado enquanto a janela está visível (`_on_visible_changed`, `:283`;
  `_start_tick`/`_stop_tick`, `:197`). Nível de áudio é *suavizado* no main thread
  (`_smooth_audio_level` interpolando em `_on_visualizer_tick`) a partir de um alvo que o
  callback só atribui —更新 de 60 fps sem tocar o worker.
- **Nível de áudio a ~10 Hz.** O worker do microfone emite `on_audio_level` por chunk de
  100 ms (`local_voice.py:704`, `live.py:2055`) → ~10 `idle_add`/s. Volume tranquilo para o
  main loop.

### 1.3 Gargalos e inconsistências visuais (onde vai falhar ou ficar feio)

1. **A pílula não cabe um resumo de reunião.** `status_lbl` trunca em 28 caracteres
   (`voice_pill.py:394`) e o rótulo de dica em 20 (`:406`). Tradução/legenda/pendências de
   reunião **precisam de um painel dedicado** (ex.: o `ChatStream` ou um novo
   `MeetingTranscriptPanel`) — jogar tudo na pílula vira reticências. Hoje não há esse
   painel.
2. **Inconsistência local × nuvem no mesmo widget.** A pílula consome sinais que o
   `LocalLiveVoiceClient` **não emite**: vídeo (`on_video_state_change`), e o cliente local
   nem implementa vídeo (`is_video_streaming()` → `False`, `local_voice.py:654`). Se a
   reunião rodar no modo local, o dot de REC, a borda de privacidade e o tooltip de foco de
   janela **nunca acendem**, enquanto no Gemini Live acendem. Mesmo visual, dois comportamentos.
3. **Loop de reconstrução O(n²) no chat.** `create_turn_widget` reconta `n_answers`
   varrendo `ctx.session.turns` a cada card (`chat_stream.py:651`). Num turno de reunião com
   centenas de falas, cada novo balão varre tudo de novo → custo quadrático e rolagem/lenta.
   O `scroll_to_bottom` ainda agenda **dois** rolamentos (idle + timeout 60 ms, `:559-560`),
   duplicando trabalho sob stream contínuo.
4. **Inanição de prioridade já documentada.** O indicador "digitando" usa
   `G_PRIORITY_LOW` de propósito para não competir com respostas que chegam por
   `GLib.idle_add`; o código cita "Inanição de prioridade" em `docs/ENDEAVOUROS_PORT.md`
   (`chat_stream.py:98`). Corolário: um *firehose* de tokens de transcrição via `idle_add`
   pode sim estrangular animações e dar soluços no visualizador — não há *throttling* de
   batching de transcrição hoje.
5. **Dependência de layer-shell.** Sem `wlr-layer-shell` (X11 ou biblioteca ausente) cai em
   `move()`; em Wayland `move()` é no-op (`voice_pill.py:104`, `:301`) → a pílula pode
   reaparecer no canto (0,0) ou onde o WM decidir, inconsistência entre sessões/ambientes.
6. **Tema.** Paleta resolvida via 5 *probe labels* de CSS com fallback em Python
   (`_FALLBACK_PALETTE`, `:47`), re-resolvida em mudança de tema (`_on_theme_changed`, `:587`).
   Claro/escuro tratados; se o CSS faltar, cai no fallback (cores coerentes, mas podem
   divergir do tema do sistema).

## 2. Em uma reunião ao vivo real (múltiplos fluxos, temas diversos)

> Pressuposto: a única via viável para tradução/resumo de reunião é o **Gemini Live**
> (visto em `PROPOSTA_FUNCIONALIDADES_AO_VIVO.md`). O cliente local é PT-cêntrico e sem
> vídeo; serve só para comandos.

### 2.1 O que estaria processando ao mesmo tempo

Uma reunião "ao vivo" sovrecarrega o mesmo session WebSocket com fluxos paralelos:

| Fluxo | Origem | Custo | Onde no código |
|---|---|---|---|
| Áudio do microfone | `pw-record` (mic worker) | subprocess + chunks 16 kHz | `live.py:2001`, `local_voice.py:678` |
| Áudio do sistema (loopback, *se implementado*) | 2º `pw-record` no monitor | +1 STT/contaminação | **inexistente hoje** (ver §2.4) |
| Quadros de tela (vídeo) | `send_screen_frame` | captura+encode ~1 fps | `live.py:110`, `:654` local=False |
| TTS de saída | `pw-play` streaming | subprocess por fala | `live.py:2262`, `local_voice.py:333` |
| LLM/tool calling | servidor Gemini | latência de rede, não CPU local | `live.py:1729` |

No modo **local**, o peso vira CPU da máquina: Whisper INT8 + Qwen (IntentEngine ou Ollama)
+ Piper rodando juntos. A máquina **não tem swap** (MEMORY do projeto) e o OOM killer já
derrubou o sistema no meio da suíte de testes — carregar os três ao vivo é o cenário de
contenção já documentado.

### 2.2 Latência (a "cadeia ao vivo")

mic → VAD/fim-de-fala → STT → (prompt de tradução) → LLM → fila TTS → reprodução. Cada
hop soma. Dois pontos medidos no código:

- **Watchdog de raciocínio de 12 s.** Se o modelo pensa >12 s (tradução longa + resumo
  simultâneo), a UI vira `LISTENING` no meio do raciocínio (`live.py:2341`, `:2345`). Turno
  pode ser cortado prematuramente em reuniões densas.
- **Barge-in local.** O cliente local cala o microfone enquanto fala/processa
  (`local_voice.py:741-746`) — bom para não se auto-interromper, mas só cobre o **mic**.

### 2.3 Temas diversos e memória de contexto

- O Gemini absorve contexto multimodal numa só sessão, mas reuniões longas estouram a
  janela de contexto. Hoje só existe `get_session_summary()` **ao encerrar**
  (`live.py:1486`, `local_voice.py:975`) — **não há compactação contínua (rolling summary)**.
  Sem checkpoint periódico, o modelo "esquece" o início da reunião e o "resumo ao vivo"
  ficaria enviesado para o final. Isso precisa ser construído.
- **Sem diarização.** Nada separa falantes; a transcrição é intercalada e o modelo pode ou
  não rotular "Quem disse". Reunião com várias vozes vira um bloco sem donos das decisões.

### 2.4 Falhas e limitações sob carga (modo Gemini Live)

1. **Queda de rede → reconexão automática.** `GeminiLiveClient` reconecta com backoff
   exponencial, até **5 tentativas** (`live.py:1730-1787`, `max_attempts=5`, `:1743`;
   `delay = min(base*2**(attempt-1), max)`). Durante o backoff a pílula mostra
   "Reconectando…" e o áudio desse intervalo **não é bufferizado** — há lacuna na
   transcrição. Após 5 falhas, a sessão termina com aviso. **O cliente local não reconecta**
   (sem loop; morte do `pw-record` → `ERROR` e fim, `local_voice.py:676-688`) — numa
   reunião local, uma queda mata a sessão sem recuperação.
2. **Loop de eco (feedback) se houver loopback.** Se a captura de reunião usar o monitor do
   sink do PipeWire, ela captura **também a própria fala do TTS** do assistente → o modelo
   "ouve a si mesmo" → eco/transcrição suja. O isolamento acústico existe só para o **mic**
   (`local_voice.py:741`); para reunião é preciso *mutar o source de loopback durante o TTS*
   — não implementado.
3. **Alucinação de STT fora do PT.** O filtro anti-fantasma do Whisper cobre só português
   (`WHISPER_PHANTOM_HALLUCINATIONS`, `local_voice.py:396`). Em reunião em outro idioma,
   lixo de transcrição passa limpo.
4. **Saturação de CPU → backlog de áudio.** Sem swap, se RSS estoura, o OOM killer mata um
   processo (app ou Ollama) → crash silencioso. Se só satura, o RTF do STT passa de 1 →
   chunks se acumulam → latência crescente (a tradução "atrasa" em relação à fala).
5. **Vídeo em carga.** `send_screen_frame` a 1 fps é leve; subir o fps para "ver a tela
   melhor" durante a apresentação drena CPU e banda do uplink, competindo com o áudio — pode
   perder palavras.

### 2.5 Resumo do comportamento esperado

- **GUI:** pílula responsiva e thread-safe; transcrição/legenda/pendências **precisam de
  painel próprio** (a pílula trunca); chat atual tem gargalo O(n²) e sem throttle de stream.
- **Reunião (Gemini Live):** resiliente a quedas (reconecta até 5×), mas sem rolling summary
  (perde contexto longo), sem diarização, e com risco de eco se usar loopback de áudio.
- **Reunião (local):** inviável para reunião multilíngue (PT fixo, sem vídeo) e frágil
  (sem reconexão; OOM sob carga).

## 3. O que construir para não quebrar

1. **Painel de transcrição dedicado** com *batching* (acumular tokens ~200 ms e atualizar 1×
   via `idle_add`), não um balão por palavra — resolve o throttle e o O(n²).
2. **Compactação contínua** (`get_session_summary` periódico injetado como contexto) para
   reuniões longas.
3. **Mute do loopback durante TTS** + VAD no loopback, estendendo o isolamento de
   `local_voice.py:741` para o áudio do sistema.
4. **Indicator de modo** na pílula (local × nuvem) para não esconder capacidades que o modo
   atual não tem (REC/privacidade apagados no local).
5. **Fallback de resumo offline** (`study_summary.generate_summary` sobre o buffer) caso a
   nuvem caia após as 5 tentativas — mantém o entregável mesmo sem reconexão.
