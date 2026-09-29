# Entrega A — Plano de Implementação (Voz ao Vivo, Tela e Interação)

> Plano estruturado da **Entrega A** (itens P0 do `MELHORIAS_VOZ_TELA_AO_VIVO.md`),
> com pontos exatos de execução (`arquivo:linha`), descrição e ordem sequencial.
> Elaborado sob a lente de **Code Review**: cada passo traz o risco de implementação
> e o critério de aceitação. Tudo ancorado no código real lido nesta rodada.

**Escopo da Entrega A (3 workstreams P0):**
- **WS1** — Painel de transcrição dedicado + *batching* + append incremental.
- **WS2** — Isolamento de eco/loopback + AGC/NS no microfone durante o TTS.
- **WS3** — *Rolling summary* contínuo injetado como contexto.

**Gates de revisão (não-negociáveis):**
1. Thread-safety: todo toque em widget passa por `GLib.idle_add`/`timeout_add` (padrão já usado).
2. Evidência > asserção: renderizar em headless e **olhar o PNG** — teste verde não prova legibilidade (lição da Fase 5 do plano da pílula).
3. Anti-slop: sem *glassmorphism* (CSS já proíbe `backdrop-filter`), sem emoji em botão, sem gradiente roxo de IA.
4. Máquina sem swap: não carregar modelos pesados; WS3 é leve (só resume o buffer local).

---

## WS1 — Transcrição com batching (usabilidade + responsividade) — **IMPLEMENTADO (2026-09-27)**

### Descoberta de revisão (bug silencioso)
- **`on_transcript` estava DESCONECTADO** em ambos os widgets: `live_view.py`
  (`_connect_client_events`, ~`:506`) e `voice_pill.py` (`:546`) não ligavam o
  callback a `self.live_client.on_transcript`. O cliente emite transcrição
  (`live.py:2260`, `local_voice.py:802/905`), mas **ninguém a exibia** — defeito
  invisível a teste unitário isolado (que chama `_ui_on_transcript` direto).
  Corrigido com fan-out idempotente em ambos os widgets (não clobbera o outro).

### Evidência do problema (código atual)
- `ui/live_view.py:624` `_ui_on_transcript` — a cada *chunk* de transcrição faz
  `caption_text_lbl.set_text(clean)` (**substitui** a legenda inteira) e chama
  `_append_log_row(...)` (**uma linha de log por chunk**). Com streaming de tokens,
  a legenda pisca entre parciais e o log inunda de linhas fragmentadas.
- `ui/live_view.py:458` `_append_log_row` — anexa a linha **e** agenda
  `GLib.idle_add(_do_scroll)` (`:478` `_scroll_log_to_bottom`). → N scrolls por N chunks.
- `ui/widgets/chat_stream.py:651` `n_answers = sum(1 for t in ctx.session.turns if ...)` —
  recomputado **dentro** de `create_turn_widget`, que é chamado por turno
  (`:547`) → custo **O(n²)** conforme a sessão cresce.
- `ui/widgets/chat_stream.py:559-560` — `scroll_to_bottom` agenda **dois** rolamentos
  (`idle_add` + `timeout_add(60)`) → trabalho duplicado sob stream contínuo.

### O que foi alterado
- **live_view.py**: campos de buffer no `__init__`; `on_transcript` conectado (fan-out);
  `_ui_on_transcript` agora acumula em buffer e agenda `_flush_transcript` (~200 ms);
  nova `_commit_transcript_turn` (flush na troca de papel) e `_flush_transcript`
  (atualiza legenda + 1 linha de log por turno, com `markup_escape_text`);
  `_append_log_row` retorna o `Gtk.Label` para atualização in-place.
- **voice_pill.py**: `on_transcript` conectado (fan-out idempotente).
- **chat_stream.py**: `create_turn_widget` aceita `n_answers` e `rebuild()` calcula
  uma vez; **`_build_assistant_card` recebe e usa esse `n_answers`** (correção pós-suíte:
  o parâmetro não era repassado — ver "Regressão" abaixo); `scroll_to_bottom` remove o
  `timeout_add(60)` duplicado.
- **Testes**: `test_live_video.py` (flush + novo `test_transcript_batching_single_row_per_turn`),
  `test_preferences_live.py` e `test_live_voice_reform.py` atualizados para o flush.

### Validação
- `py_compile` dos 3 módulos + testes: OK.
- Import contra GTK real (`gi`): OK.
- Heurística de acúmulo provada em standalone (delta, cumulativo, misto, repetição, sobreposição): OK.
- **Suíte completa (2026-09-29):** rodada com `run_tests_safe.py` no display real →
  **160 módulos, 0 falhas, 211 s, pico 566 MB** (após corrigir a regressão abaixo).
  O display existe neste ambiente (`DISPLAY=:0`, `WAYLAND_DISPLAY=wayland-1`), então os
  testes de GUI rodam de fato — não foi preciso `xvfb`.
- **Suíte completa após a Entrega B / WS2 (2026-09-29):** **161 módulos, 0 falhas, 197 s,
  pico 501 MB**. Pegou mais uma regressão (`test_fase4_live_robustness`, 6 falhas) —
  ver `docs/ENTREGA_B_RECONEXAO.md` §WS2. Confirma o padrão: a suíte completa é o único
  juiz confiável.
- **Suíte completa após a Entrega D / P1-8 (2026-09-29):** **162 módulos, 0 falhas, 208 s,
  pico 470 MB**. O total sobe de 161 → 162 pelo novo `tests/test_thinking_watchdog.py`.
  Ver `docs/ENTREGA_D_WATCHDOG_PENSANDO.md` §6.
- **Suíte completa após a Entrega E / P1-9 (2026-09-29):** **163 módulos, 0 falhas, 219 s,
  pico 370 MB**. O total sobe de 162 → 163 pelo novo `tests/test_privacy_shield.py`.
  Ver `docs/ENTREGA_E_PRIVACY_SHIELD.md` §6.
- **Suíte completa após a Entrega F / P1-6 (2026-09-29):** **164 módulos, 0 falhas, 219 s,
  pico 497 MB**. O total sobe de 163 → 164 pelo novo `tests/test_video_pacing.py`.
  Ver `docs/ENTREGA_F_FPS_ADAPTATIVO.md` §6.
- **Suíte completa após a Entrega G / P1-7 (2026-09-29):** **165 módulos, 0 falhas, 220 s,
  pico 370 MB**. O total sobe de 164 → 165 pelo novo `tests/test_transcript_roles.py`.
  Ver `docs/ENTREGA_G_ATRIBUICAO_DE_TURNO.md` §6.

### Regressão encontrada na suíte completa (corrigida em 2026-09-29)
- **Defeito:** o refactor O(n²) (A5) moveu a contagem de `n_answers` para o `rebuild()`,
  mas **não repassou o parâmetro** de `create_turn_widget` (`chat_stream.py:589`) para
  `_build_assistant_card`, que passou a referenciar `n_answers` (atribuído localmente em
  `:662`) sem recebê-lo → **`UnboundLocalError` em todo card de resposta não-pendente**:
  o fluxo de chat normal quebrava. Invisível aos testes de WS1 (que não constroem cards de
  assistente) — **pego pela suíte completa**: `test_action_status`, `test_compare`,
  `test_gemini_ui`, `test_ui_refactor` (4 módulos, mesmo fio condutor).
- **Correção:** `_build_assistant_card(..., n_answers=None)` + repasse em `:589`. Reintroduzir
  o defeito (remover o repasse) faz os 4 módulos voltarem a falhar — é a validação do teste
  contra o defeito. O fallback `n_answers is None` foi preservado para as chamadas diretas
  (`prompt_bar.py`, `app.py`).
- **Lição:** testes de WS1 verdes ≠ app verde. Só a suíte completa (que exercita o caminho de
  render de card) expôs o defeito.

### Passos sequenciais
| # | Arquivo:linha | O que fazer | Riscos / armadilhas |
|---|---|---|---|
| A1 | `ui/live_view.py` (~`:90`) | Criar buffer de batch: `self._transcript_buf: dict[str,str]` (role→texto acumulado), `self._transcript_flush_id: int|None`, `self._last_transcript_role`. | Não confundir buffer de user vs assistant; zerar ao trocar de turno. |
| A2 | `ui/live_view.py:624` | `_ui_on_transcript`: **não** fazer `set_text`+append por chunk. Acumular em `_transcript_buf[role]` e (re)agendar flush com `GLib.timeout_add(200, self._flush_transcript)`. | Timeout de 200 ms pode atrasar a legenda → ver R1. |
| A3 | `ui/live_view.py` (novo `_flush_transcript`) | Atualizar `caption_text_lbl` com o texto acumulado da fala em curso; ao detectar fim de turno (`turnComplete` / mudança de role), "fechar" **uma só** linha de log via `_append_log_row` com o texto final e zerar o buffer. | Precisa do sinal de fim de turno — ver R1. |
| A4 | `ui/live_view.py:478` | `_scroll_log_to_bottom`: chamar scroll **só no flush** (1×/200 ms), não por chunk. | Manter pelo menos 1 caminho de rolagem (R2). |
| A5 | `ui/widgets/chat_stream.py:651` | Pré-computar `n_answers` uma vez por rebuild e passar como parâmetro / cachear em `ctx.session`; eliminar o loop aninhado. | Não recontar turns em loop — R3. |
| A6 | `ui/widgets/chat_stream.py:559-560` | Remover o `timeout_add(60, _do_scroll)` duplicado; manter só o `idle_add`. | Não quebrar o "rolar para o fim" ao remover um dos dois. |
| A7 | `tests/test_live_view.py` (criar/estender) | Testar que N chunks de um turno → **1 linha** de log; caption reflete acumulado; teste de não-O(n²) (100 turnos sob limiar de tempo). | Mock do `live_client` e do `GLib` timer. |

**Critério de aceitação WS1:** renderizar em headless e olhar o PNG — legenda estável (sem piscar entre parciais), log com 1 linha por intervenção, e o chat não trava sob stream contínuo.

---

## WS2 — Áudio: AGC/NS + isolamento de eco/loopback — **IMPLEMENTADO (2026-09-27)**

### Evidência do problema (pré-implementação)
- `ai/live.py:2112-2124` e `ai/local_voice.py:679` — `pw-record` **sem source processado**
  (captura crua 16 kHz, sem AGC/NS).
- `ai/live.py:2210-2217` (pré-WS2) — só ducking RMS durante `SPEAKING`; **sem portão de envio
  explícito** quando o ducking está desligado → eco acústico de sala podia alimentar o mic.
- `ai/local_voice.py:739-746` — o modo local **já isola** o mic durante fala
  (limpa buffers se `state in (SPEAKING, THINKING, EXECUTING)`). Estendido para nuvem em B3.

### Passos sequenciais
| # | Arquivo:linha | O que fazer | Riscos / armadilhas |
|---|---|---|---|
| B1 | `core/audio/capture.py` (novo) | `get_processed_capture_source()` — se PipeWire presente, cria/usa nó de captura filtrado (RNNoise p/ NS + AGC via `module-filter-chain`/`pw-cli`), retorna o nome do source; **idempotente**; fallback p/ default mic. | PipeWire pode estar ausente → fallback obrigatório (R5). **Armadilha encontrada:** `pw-cli load-module` retorna código 0 mesmo falhando (erro no stderr) → detecção por `returncode` só dava falso positivo; corrigido checando stdout+stderr. |
| B2 | `ai/live.py:2168-2172`, `ai/local_voice.py:689-693` | Passar `--target <source>` (pw-record) / `-D <device>` (arecord) ao gravador apontando para o source de B1 quando disponível; cai no default se `None`. | Sintaxe do `pw-record` varia por versão; graph do `module-filter-chain` (RNNoise/compander) **validar em máquina real** (R5). |
| B3 | `ai/live.py:2111` (`_echo_isolation_blocks_send`) + chamada em `_mic_recorder_loop` | Espelhar o isolamento do local: enquanto `state in (SPEAKING, EXECUTING, THINKING)` (ou fila de play/echo-cooldown ativa), **bloquear o envio** do chunk ao servidor — salvo barge-in voluntário (RMS >= limiar). `norm_level` continua sendo calculado e enviado ao visualizador. | Não parar de calcular `norm_level` (visualizador precisa continuar). Portão atua **independente do ducking**. |
| B4 | `docs/PROPOSTA_COMPORTAMENTO_AO_VIVO.md:§2.4.2` | *Loopback* (futuro): **NÃO IMPLEMENTADO** — hoje não existe captura de áudio do sistema. Deixado como sub-tarefa documentada para quando houver loopback. | Risco de eco só se/quando houver loopback; não inventar feature. |
| B5 | `tests/test_ws2_capture.py` (novo) + `tests/test_live.py::LiveEchoIsolationTest` (novo) | `enrich_capture_cmd` insere `--target`/`-D`; `get_processed_capture_source` retorna `None` sem PipeWire e no falso-positivo do `pw-cli`; `_echo_isolation_blocks_send` bloqueia durante fala/processamento e libera no barge-in. | 17 testes passam em headless; AEC/NS real exige áudio real → validar manualmente (R5). |

### Evidência da implementação (anchors reais)
- `core/config.py:102,106,107` — flags `live_mic_speaker_isolation` / `live_mic_ns_enabled` / `live_mic_agc_enabled` (default `True`; `PreferencesDialog` mantém padrão pois constrói `CopilotConfig()` fresco).
- `core/audio/capture.py:59` `_create_filtered_source`, `:102` `get_processed_capture_source`, `:125` `enrich_capture_cmd`.
- `ai/live.py:2111` `_echo_isolation_blocks_send`, `:2168-2172` B2 (nuvem), chamada do portão no loop.
- `ai/local_voice.py:689-693` B2 (local).
- Testes: `tests/test_ws2_capture.py`, `tests/test_live.py::LiveEchoIsolationTest` → **17 passed**; `tests/test_live.py` completo → **64 passed** (sem regressão).

**Critério de aceitação WS2:** gravar áudio com o speaker do assistente ligado e confirmar que o STT não capta o eco (transcrição limpa); degradar graciosamente sem PipeWire. **Status:** lógica e testes implementados; **falta validação em hardware real** (R5) — o graph exato do `module-filter-chain` e o efeito de NS/AGC só podem ser confirmados na máquina-alvo.

---

## WS3 — Rolling summary contínuo

### Evidência do problema
- `ai/live.py:1495` `get_session_summary` — **só ao encerrar**; sem compactação contínua →
  reunião longa perde o início do contexto.
- `ai/live.py:2257` — transcrição chega por *part*; não há timer de resumo periódico.

### Passos sequenciais
| # | Arquivo:linha | O que fazer | Riscos / armadilhas |
|---|---|---|---|
| C1 | `ai/live.py:1467` (`start()`) ou `_live_session` | Agendar `self._summary_timer_id = GLib.timeout_add_seconds(interval_min*60, self._periodic_summary)`; guardar p/ cancelar. | Só para `GeminiLiveClient` (tem ws) — **não** portar p/ `LocalLiveVoiceClient` (R4). |
| C2 | `ai/live.py` (novo `_periodic_summary`) | Chamar `get_session_summary()`, compor `build_realtime_text_msg` com instrução de contexto ("Resumo da sessão até agora: …") e enviar via `asyncio.run_coroutine_threadsafe(ws.send(...), self._loop)`. Tratar loop None / sessão não iniciada. | Não enviar se `self._is_running` falso; evitar spam se pouco contexto. |
| C3 | `ai/live.py:1470` (`stop()`) + reconexão | `GLib.source_remove(self._summary_timer_id)` em `stop()` e relançar após reconexão (não vazar timer). | Timer órfão após reconectar → R4/R8. |
| C4 | `core/config.py` | Novo `live_summary_interval_min` (default 4; `0` desliga). | Preservar em `PreferencesDialog._collect_current_config` (padrão do plano da pílula). |
| C5 | `tests/test_live.py` | Mock do `ws`; avançar o timer; verificar `send` chamado com contexto de resumo; cancelamento em `stop`. | Usar `run_tests_safe.py` (sem swap). |

**Critério de aceitação WS3:** sessão longa mantém contexto (resumo injetado visível no log como "Resumo automático"); sem vazamento de timer; config `0` desliga.

### Implementado (2026-09-27)
- **C1 (ancoragem corrigida):** o resumo nasce **com a conexão**, não em `start()` — em
  `ai/live.py` `_run_one_session` (~`:1990`), logo após `mic_task`/`server_task`, cria
  `summary_task = asyncio.create_task(self._summary_loop())` e guarda em `self._summary_task`.
  *Desvio do plano:* o plano sugeria `GLib.timeout_add_seconds`, mas `live.py` é backend e
  **não importa GLib** (ver comentário em `:1355` — "este módulo não importa gi"). O idioma
  correto é uma task do próprio `self._loop`: ela morre com a sessão e não vaza entre
  reconexões. Por isso o "timer" do plano virou uma task cancelável.
- **C2 (`_summary_loop` + `_build_live_summary_text`):** `_build_live_summary_text` monta o
  resumo a partir de `_transcripts_log[-8:]` + `_executed_actions_log[-6:]` com carimbo de
  duração; `_summary_loop` dorme `interval_min*60`s e envia via
  `await self._ws.send(json.dumps(build_realtime_text_msg(text)))` **direto no loop** (sem
  `run_coroutine_threadsafe`, porque a corrotina já roda no `self._loop`). Retorna `""` sem
  atividade → o tick é pulado. Intervalo lido de `live_summary_interval_min` (0 desliga).
- **C3 (sem vazamento):** `finally` em `_run_one_session` cancela `summary_task` mesmo na
  saída por exceção; `stop()` também cancela via `self._loop.call_soon_threadsafe`
  (thread-safe, pois `stop()` roda fora do loop). A reconexão relança a task naturalmente na
  próxima `_run_one_session`.
- **C4 (`core/config.py`):** `live_summary_interval_min: float = 4.0` (0 desliga).
- **C5 (`tests/test_live.py`):** nova classe `LiveRollingSummaryTest` (7 testes): montagem do
  texto (turnos/ações/truncamento/vazio), loop desligado com intervalo 0, loop que não envia
  sem `_is_running`, envio periódico via `realtimeInput`, e cancelamento limpo da task.

**Validação:** os 7 testes de WS3 passam em headless
(`pytest tests/test_live.py::LiveRollingSummaryTest`); o módulo `test_live.py` inteiro passa
(57 verdes, sem regressão). **Não rodei a suíte completa localmente** — a máquina não tem
`xvfb-run` nem swap; testes de widget/GUI precisam de display. Na máquina-alvo use
`xvfb-run -a .venv/bin/python -m pytest -q` ou `run_tests_safe.py`.

**Risco respeitado (R4):** `_summary_loop`/`_build_live_summary_text` ficaram
**só no `GeminiLiveClient`** (tem ws); `LocalLiveVoiceClient` não recebe rolling summary.

---

## Ordem sequencial global (Sprint A)
1. **WS1 (A1→A7)** — maior impacto, menor risco, puramente UI. Fecha usabilidade + responsividade.
2. **WS3 (C1→C5)** — resiliência de contexto, complexidade média, sem dependência de SO.
3. **WS2 (B1→B5)** — toca PipeWire/ambiente, maior risco de variável de máquina; validar em硬件 real.

## Riscos e armadilhas (Code Review)
- **R1 (WS1):** batching por timeout pode atrasar a legenda. Mitigar com flush *antecipado* em `turnComplete` / mudança de `role`, não só no timer de 200 ms.
- **R2 (WS1):** ao remover o double-scroll, manter 1 caminho funcional de "rolar para o fim".
- **R3 (WS1):** O(n²) exige não recontar `turns` em loop aninhado — cachear a contagem por rebuild.
- **R4 (WS3):** `LocalLiveVoiceClient` não tem WebSocket → rolling summary **só** no `GeminiLiveClient`. Não portar.
- **R5 (WS2):** PipeWire/`pw-cli` podem estar ausentes → fallback obrigatório; nunca quebrar a captura se o filtro não existir.
- **R6 (anti-slop):** ~~remover o comentário "Glassmorphism" em `ui/live_view.py:1-3`~~ — **FEITO (2026-09-29):** o comentário foi reescrito para descrever o material de vidro sóbrio real (sem frosted glass; desfoque só do compositor `.blur-real`, senão `.blur-fallback`), referenciando `data/zorin-copilot.css:5-18`. **Decisão do dono (pendente):** confirmar se o roxo "IA falando" — `live_view.py:879` `(0.47, 0.34, 1.0)` — viola a regra "sem roxo de IA" ou é apenas cor de estado semântico (roxo=IA, verde=exec, âmbar=think, azul=listen, cinza=mute). **Não alterado** — aguarda decisão do dono.
- **R7 (recursos):** sem swap, WS2/WS3 não carregam modelos; `get_session_summary` é leve (local). OK.
- **R8 (validação):** teste verde ≠ legível — validar WS1 renderizando em headless e olhando o PNG (regra da Fase 5 do plano da pílula).

## Sequência sugerida de commits
1. `feat(live-ui): batching de transcrição + append incremental (WS1)`
2. `feat(live): rolling summary periódico (WS3)`
3. `feat(audio): source filtrado AGC/NS + isolamento de eco no mic (WS2)`
4. `chore: remove resíduo de comentário glassmorphism (R6)`
