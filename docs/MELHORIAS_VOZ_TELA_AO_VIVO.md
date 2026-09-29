# Melhorias de Voz ao Vivo, Compartilhamento de Tela e Interação

> Plano de ação concreto e priorizado para as modalidades ao vivo do Zorin Copilot.
> Cada ponto ancora no estado real do código (`arquivo:linha`), lido nesta rodada.
> Princípios do projeto que limitam as opções: **privacidade em primeiro lugar**,
> **execução local/hibrida**, **HIG do GNOME** (sem gradiente/glassmorphism/emoji em
> botão), e **máquina sem swap** (OOM killer já derrubou o sistema em suíte de testes).

---

## 0. Estado atual (o que já existe e o que dói)

| Capacidade | Onde | Estado |
|---|---|---|
| Pílula de voz ao vivo, thread-safe | `ui/voice_pill.py` (`:547-577` todos os callbacks via `GLib.idle_add`) | Sólido. Visualizador barato (80×26 px, só visível). |
| VAD + barge-in (mic) | `ai/local_voice.py:741-746` | Cobre **só o microfone**; modo nuvem não tem barge-in explícito. |
| Streaming de tela (1 fps, crop na janela ativa) | `ai/live.py:3810` `start_video_stream`, worker `:3858` | Baseado em **screenshot**, não vídeo real. `max_size=800`, `quality=70`. |
| Privacy Shield por palavra-chave de título | `ai/live.py:3874` | Frágil: só bate em substring do título da janela. |
| Frame-diffing (não reenvia tela estática) | `ai/live.py:3953` (limiar <2%, keep-alive 8 quadros) | Bom para economia de tokens, mas 8 quadros a 1 fps = 8 s sem atualização. |
| Jitter buffer / prebuffering de áudio | `ai/live.py:1380-1386`, `:1573-1582` | Presente. `echo_cooldown` de 0.40 s antipop. |
| Reconexão (modo nuvem) | `ai/live.py:1730-1787` (backoff, 5 tentativas) | OK, mas **sem buffer de áudio** no intervalo → lacuna na transcrição. |
| Resumo de sessão | `ai/live.py:1495` `get_session_summary` | **Só ao encerrar.** Sem compactação contínua (rolling summary). |
| Modo local (`LocalLiveVoiceClient`) | `ai/local_voice.py` | **Sem vídeo** (`is_video_streaming()→False`, `:654`), **sem reconexão** (`:676-688`), **PT-only** (filtro anti-alucinação `:396`), frágil sob carga (OOM). |

**Pontos de falha documentados** (detalhados em `docs/PROPOSTA_COMPORTAMENTO_AO_VIVO.md`):
1. Pílula trunca status em **28 caracteres** (`voice_pill.py:394`) → sem painel próprio, tradução/legenda/pendências viram reticências.
2. Chat reconstrói em **O(n²)** (`chat_stream.py:651` recontando `n_answers` por card) e agenda **dois** rolamentos (`:559-560`) → soluça sob stream contínuo.
3. **Sem throttling/batch de transcrição**: firehose de tokens via `idle_add` pode estrangular animações; o "digitando" usa `G_PRIORITY_LOW` de propósito (`chat_stream.py:98`), mas a transcrição compete com o visualizador.
4. **Watchdog de 12 s** corta o turno se o modelo pensa muito (`live.py:2341-2345`) — reuniões densas são cortadas.
5. **Inconsistência local × nuvem** no mesmo widget: sinais de vídeo/privacidade que o cliente local não emite nunca acendem no modo local.
6. **Dependência de `wlr-layer-shell`**: sem ele (X11/alguns compositores) `move()` é no-op (`voice_pill.py:301`) → pílula reaparece no canto (0,0).
7. **Risco de eco** se usar loopback de áudio do sink: captura a própria fala do TTS → o modelo "ouve a si mesmo". Isolamento existe só para o mic.

---

## 1. Priorização (impacto × risco × esforço)

| # | Ação | Dimensão | Impacto | Risco | Prioridade |
|---|---|---|---|---|---|
| 1 | Painel de transcrição dedicado + batching (~200 ms) | Usabilidade / Responsividade | Alto | Baixo | **P0** |
| 2 | Isolamento de eco/loopback + AGC/NS no mic durante TTS | Áudio | Alto | Baixo | **P0** |
| 3 | Resumo contínuo (rolling summary) injetado como contexto | Interação / Contexto | Alto | Médio | **P0** |
| 4 | Reconexão robusta do modo local + buffer de áudio na queda | Resiliência | Alto | Médio | **P0** |
| 5 | Indicador de modo (local × nuvem) e reconciliação de sinais | Usabilidade | Médio | Baixo | P1 |
| 6 | FPS adaptativo + captura sob demanda em alta resolução | Vídeo | Médio | Médio (banda) | P1 |
| 7 | Diarização / marcadores de turno de falante | Interação | Médio | Alto | P1 |
| 8 | Sintonização do watchdog (estado "pensando" explícito) | Latência | Médio | Baixo | P1 |
| 9 | Privacy Shield por classe/role da janela + allowlist | Privacidade | Médio | Baixo | P1 |
| 10 | Filtro anti-alucinação STT multilíngue | Áudio | Baixo | Baixo | P2 |
| 11 | Barge-in também no modo nuvem + interrupção por gesture | Interação | Baixo | Médio | P2 |

---

## 2. Melhorias por dimensão

### 2.1 Usabilidade
- **Painel de transcrição próprio (P0-1).** Hoje a pílula trunca em 28 chars (`voice_pill.py:394`). Criar `MeetingTranscriptPanel` (ou reaproveitar `ChatStream`) que acumula tokens ~200 ms e repinta 1× via `idle_add`, em vez de 1 balão por palavra. Resolve o throttle do firehose **e** o O(n²) de `chat_stream.py:651` (trocar reconstrução total por *append incremental* de novo card, guardando contagem já computada).
- **Indicador de modo (P1-5).** `LocalLiveVoiceClient` não emite `on_video_state_change`/`on_privacy_state_change` (`live.py:51` na PROPOSTA). Acender na pílula um chip "Local / Nuvem" e, no modo local, deixar REC/privacidade **apagados com rótulo** ("recurso indisponível no modo local") em vez de simplesmente mudos — evita o "mesmo visual, dois comportamentos".
  **→ IMPLEMENTADO (2026-09-29)** — ver `docs/ENTREGA_C_MODO_E_SINAIS.md` (chip de modo; controles de tela rotulados como indisponíveis no local; linha do modelo e da cerca corrigidas por modo; evidência visual em `docs/indicador-modo-*.png`).
- **Descoberta do toggle de tela.** O `toggle_video_stream` existe (`live.py:3850`), mas falta atalho/entrada de menu visível e confirmação de consentimento explícita na primeira vez (hoje o streaming entra sem prompt de autorização por janela).
- **Reposicionamento confiável (P1).** O menu "Posição" (Fase 4 do `plano-melhorias-pilula-voz.md`) já é o caminho confiável no Wayland; estender para o popover de controle de tela.

### 2.2 Qualidade de áudio
- **Isolamento de eco/loopback (P0-2).** Estender o barge-in de `local_voice.py:741` para o áudio do *sistema*: ao iniciar o TTS, **silenciar o source de loopback** (PipeWire `pw-cli`/`module-loopback` ou mutar o node via `pw-dump`+`pw-cli set-node-volume 0`) e restaurar ao fim. Sem isso, reunião com áudio do sistema vira eco (PROPOSTA §2.4.2).
- **AGC / supressão de ruído no `pw-record` (P0-2).** Hoje grava 16 kHz cru (`live.py:2001`). Injetar fila de pré-processamento (RNNoise ou `webrtc-audio-processing` via `pw-record`+`ladspa`/`pipewire-filter-chain`) para AGC + NS antes do STT → menos RTF e menos "lixo" em sala ruidosa.
- **Jitter buffer já existe** (`:1380`); monitorar *underruns* e expor no painel um indicador de estabilidade de áudio em vez de só o dot de REC.
- **Barge-in no modo nuvem (P2-11).** No Gemini Live, implementar interrupção por detecção de fala do usuário (VAD no mic) que cancele o TTS em andamento — hoje só o local faz.

### 2.3 Qualidade de vídeo / tela
- **FPS adaptativo (P1-6).** `start_video_stream(fps=1.0)` fixo (`live.py:3810`). Subir para 2–4 fps **só quando há movimento** (o frame-diffing já calcula `diff` em `:3953`) e baixar para 0,5 fps em tela estática, respeitando o keep-alive. Isso melhora leitura de UI sem drenar uplink/CPU.
  **→ IMPLEMENTADO (2026-09-29)** — ver `docs/ENTREGA_F_FPS_ADAPTATIVO.md` (`adaptive_video_delay()`: o `diff`, que já era calculado, passa a dosar o intervalo — 2 fps em movimento / 0,5 fps parado com `fps=1`, dentro de `[0,25 s, 4,0 s]`; o ritmo continua derivado do `fps` pedido). Harness `mutate_video_pacing.py`: 12 KILLED / 0 sobreviventes.
- **Captura sob demanda em alta resolução (P1-6).** O stream vai a `max_size=800`/`quality=70` (`:3937`) — insuficiente para ler texto pequeno. Manter o stream leve e usar `capture_screen` (`live.py:2596`, ferramenta) para um frame full-res **sob pedido** ("olhe esse trecho"). Evita competir com o áudio no streaming contínuo.
  **→ IMPLEMENTADO (2026-09-29)** — ver `docs/ENTREGA_F_FPS_ADAPTATIVO.md` (`capture_screen` ganha `full_resolution`: `max_size=0` = resolução nativa, `quality=92`, **sob pedido** do modelo; o stream contínuo segue em `800`/`70`, agora nomeado em `STREAM_CAPTURE_MAX_SIZE`/`_QUALITY`; o caminho sem o parâmetro fica inalterado).
- **Privacy Shield por classe/role (P1-9).** Trocar a substring de título (`live.py:3874`) por correspondência de `app_id`/`wm_class` (via `inspector`) + allowlist do usuário. Default-deny para janelas de banco/senha; menos vazamento de dados sensíveis para a nuvem.
  **→ IMPLEMENTADO (2026-09-29)** — ver `docs/ENTREGA_E_PRIVACY_SHIELD.md` (o escudo de vídeo passa a usar o `PrivacyGatekeeper` — o mesmo matcher da ingestão da RNN; padrões ancorados por fronteira de palavra, que corrigem os falsos positivos medidos de `bb`/`inter`/`c6`; `privacy_shield_allowlist`/`denylist` no config; cofres de senha e prompts de autenticação são default-deny e a allowlist **não** os revela). Harness `mutate_privacy_shield.py`: 11 KILLED / 0 sobreviventes.
- **Mostrar o recorte (P1).** O `on_video_frame_preview` (`:3902`, `:3973`) já alimenta a miniatura PiP — garantir que o usuário veja **exatamente** a região que a IA recebe (crop `active_window` vs `fence`), com bordo visível e opção de travar em janela específica.

### 2.4 Latência
- **Watchdog de 12 s (P1-8).** `live.py:2341-2345` corta o turno. Substituir por estado "pensando" explícito com timeout *adaptativo* (proporcional ao tamanho do contexto / tipo de tarefa) e, se estourar, pedir continuação em vez de encerrar o turno.
  **→ IMPLEMENTADO (2026-09-29)** — ver `docs/ENTREGA_D_WATCHDOG_PENSANDO.md` (`thinking_watchdog_seconds()` proporcional ao volume com teto; watchdog consulta `interaction_status` antes de liberar; pede continuação mantendo `THINKING` — mic fechado; só libera no último recurso com rótulo honesto). Harness `mutate_thinking_watchdog.py`: 9 KILLED / 0 sobreviventes.
- **STT incremental.** Se o backend permitir, preferir streaming parcial ao chunk de 100 ms (`local_voice.py:704`) para reduzir o hop mic→texto.
- **Pré-buffering já implementado**; manter e medir a latência fim-a-fim (mic→resposta audível) como métrica de aceitação dos itens acima.

### 2.5 Sincronização e capacidade de resposta
- **Batching de transcrição (P0-1)** resolve a inanição de prioridade (PROPOSTA §1.3.4): acumular tokens e 1 `idle_add` a cada ~200 ms, nunca um por palavra.
- **Append incremental no chat (P0-1)** elimina o O(n²) de `chat_stream.py:651` e os dois rolamentos de `:559-560`.
- **Prioridade GLib coerente:** manter "digitando" em `G_PRIORITY_LOW` mas garantir que o visualizador de áudio (frame clock, `:1617`) e a transcrição batched não disputem o mesmo priority — separar em fontes distintas.
- **Respeitar `prefers-reduced-motion`** (GTK4 não tem `@keyframes` nativo): já tratado na Fase 3 do plano da pílula; estender ao painel de transcrição.

### 2.6 Interação do assistente durante as modalidades
- **Rolling summary (P0-3).** Chamar `get_session_summary` periodicamente (ex.: a cada N turnos ou T minutos) e injetar como contexto (`build_realtime_text_msg`), evitando estouro de janela e viesar o "resumo ao vivo" para o final. Sem isso, reuniões longas perdem o início.
- **Diarização (P1-7).** Sem separação de falantes, a transcrição é um bloco sem donos (PROPOSTA §2.3). Implementar pelo menos marcadores de turno (detecção de troca de voz por embeddings/tono) ou rotular "Quem disse" via prompt — requisito para atas com decisões atribuídas.
  **→ PARCIALMENTE IMPLEMENTADO (2026-09-29)** — ver `docs/ENTREGA_G_ATRIBUICAO_DE_TURNO.md`. **Feito:** marcadores de turno (o mínimo que o item autoriza) — a nuvem passou a registrar a fala do **usuário** (`inputTranscription`, que o cliente não lia, deixando o transcrito com metade dos donos); vocabulário único de papéis (`assistant`/`user`, com normalização do legado `agent`); forma única em `"transcripts"` (dict nos dois clientes — antes era tupla na nuvem e dict no local); o resumo contínuo passou a mostrar **os dois lados** (antes, na nuvem, tudo saía como `copilot:` e a metade do usuário se perdia nas reconexões). Harness `mutate_transcript_roles.py`: 10 KILLED / 0 sobreviventes / 1 equivalente medido. **Falta:** diarização multi-falante de verdade (distinguir pessoas numa reunião) — exige *embeddings* de voz, modelo que o projeto não tem; segue aberto. **Não verificado:** o campo `inputTranscription` contra a API Live real (testes com mensagens sintéticas).
- **Tool calling contínuo (já existe, `LIVE_VOICE_TOOL_CALLING.md`).** manter `ToolRegistry` unificado; apenas garantir que ações de risco (`run_command` com `rm`/`sudo`, `git push`) continuem o fluxo de confirmação verbal via `confirmation_id` durante a fala.
- **Fallback offline de resumo (P0-4).** Se a nuvem cair após as 5 tentativas (`live.py:1730`), gerar resumo local (`study_summary.generate_summary` sobre o buffer) para entregar o artefato mesmo sem reconexão.

---

## 3. Limitações e riscos a considerar

1. **Sem swap → OOM sob carga local.** Whisper INT8 + Qwen/Ollama + Piper juntos em reunião local é o cenário de contenção já documentado. **Mitigação**: cap de modelos, quantização agressiva, ou **recusar modo reunião local** acima de 2 falantes / oferecer só nuvem. Nunca carregar os três ao vivo sem guarda de memória.
2. **Banda: vídeo compete com áudio.** Subir fps/resolução drena uplink e pode perder palavras (PROPOSTA §2.4.5). Manter stream leve + captura full-res sob demanda.
3. **Privacidade de tela na nuvem.** Screenshots saem da máquina para o Gemini. Privacy Shield por título é frágil (item 2.3). Risko de vazamento se a substring errar. Default-deny + allowlist é obrigatório antes de "ligar para todos".
4. **Eco de loopback.** Se capturar áudio do sistema, a TTS do assistente alimenta o próprio STT (item 2.2). Sem mute do loopback durante TTS, a transcrição se corrompe.
5. **Dependência de PipeWire + portal/grim.** Sem `pw-record` ou sem portal/grim (X11, alguns compositores), áudio e/ou tela caem. A pílula sem `wlr-layer-shell` reaparece no (0,0) (`voice_pill.py:301`) — inconsistência entre sessões/ambientes.
6. **Modo local é inferior por design.** sem vídeo, sem reconexão, PT-only, frágil. Não vender "reunião ao vivo" no modo local; reservar para comandos.
7. **Janela de contexto.** Rolling summary ajuda, mas reuniões muito longas ainda estouram — definir retenção máxima e avisar o usuário.
8. **STT fora do PT.** Filtro anti-alucinação cobre só português (`local_voice.py:396`); reuniões multilíngue geram lixo de transcrição no modo local.

---

## 4. Sequência recomendada (2 primeiras entregas)

**Entrega A (P0, maior impacto / menor risco):**
- Painel de transcrição + batching + append incremental no chat (itens 2.1, 2.5).
- Isolamento de eco/loopback + AGC/NS no mic (2.2).
- Rolling summary injetado (2.6).

**Entrega B (P0/P1, resiliência e clareza):**
- Reconexão do modo local + buffer de áudio na queda + fallback de resumo offline (2.6, 3.1).
- Indicador de modo local×nuvem + consentimento explícito de tela (2.1).
- FPS adaptativo + captura sob demanda full-res + Privacy Shield por classe (2.3).
- Sintonização do watchdog (2.4).

**Validação (regra do projeto: evidência > asserção):**
- Rodar a suíte por `run_tests_safe.py` (sem swap) — não confiar em "teste verde" sozinho.
- Renderizar o painel em headless e **olhar** o PNG (o teste verde não prova legibilidade — lição da Fase 5 do plano da pílula).
- Medir latência fim-a-fim (mic→resposta) e RSS sob carga antes de declarar pronto.
