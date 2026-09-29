# Entrega B — Resiliência de conexão: reconexão do modo local + buffer de áudio na queda

> Plano de ação para o **P0-4** de `docs/MELHORIAS_VOZ_TELA_AO_VIVO.md` §1
> (*"Reconexão robusta do modo local + buffer de áudio na queda"*).
> Cada ponto ancora no estado real do código, **lido nesta rodada (2026-09-29)**.
>
> **STATUS: WS1 (reconexão local) e WS2 (buffer na nuvem) IMPLEMENTADOS (2026-09-29).**
> Não confundir com a Entrega A (`docs/ENTREGA_A_VOZ_TELA.md`), que cobriu
> P0-1/2/3 e está implementada.

---

## 0. Contexto

A Entrega A fechou os três P0 de usabilidade/áudio/contexto:

| P0 | Dimensão | Entrega A |
|---|---|---|
| 1 | Painel de transcrição + batching | WS1 ✅ |
| 2 | Isolamento de eco/loopback + AGC/NS no mic | WS2 ✅ |
| 3 | Resumo contínuo (rolling summary) | WS3 ✅ |

Resta o **P0-4 (Resiliência)**, que a própria proposta já diagnostica em
`docs/PROPOSTA_COMPORTAMENTO_AO_VIVO.md:118-126` (§2.4):

- **Nuvem:** reconecta com backoff, mas *"o áudio desse intervalo não é bufferizado —
  há lacuna na transcrição"* (`:120-123`).
- **Local:** *"uma queda mata a sessão sem recuperação"* (`:126`).

Este plano fecha os dois lados. São dois workstreams independentes.

---

## 1. Evidência do problema (código atual, verificado)

### 1.1 Nuvem (Gemini Live) — reconecta, mas **perde o áudio do intervalo**

- `ai/live.py:1825` `_live_session` — wrapper de reconexão com backoff exponencial
  (1.5s → 3s → 6s → 12s → 24s, máx 30s), **até 5 tentativas** (`:1839-1841`, `:1863-1872`).
- `ai/live.py:1880-1883` — a espera do backoff (`for _ in range(int(delay*10)):
  await asyncio.sleep(0.1)`) **não captura nada**: não há gravação nesse intervalo.
- `ai/live.py:2064` — o `mic_task` (`_mic_recorder_loop`) nasce **por tentativa** e é
  cancelado no `finally` de `_run_one_session` (`:2082-2084`). Ou seja, a cada queda o
  gravador morre e só renasce quando a próxima sessão conecta.
- **Efeito medido pelo desenho:** cada queda abre uma lacuna de **1.5 a 30 s** sem
  transcrição — exatamente o que a proposta chama de "lacuna na transcrição".

**Defeito adicional (vazamento):** `ai/live.py:2175` faz `self._record_proc =
subprocess.Popen(...)` sobrescrevendo o anterior **sem encerrá-lo**. Como o task é
cancelado (não encerra o filho) e a próxima tentativa cria um novo `pw-record`, **cada
reconexão deixa um processo `pw-record` órfão** (só some no `stop()`, `:1611-1620`).

### 1.2 Local (`LocalLiveVoiceClient`) — **não reconecta** (morre em silêncio)

- `ai/local_voice.py:608` `start()` → cria `_worker_thread` (`:621-622`) → `_mic_loop` (`:677`).
- `ai/local_voice.py:716` — condição do laço:
  `while self._is_running and self._record_proc and self._record_proc.poll() is None:`
- `ai/local_voice.py:718-720` — `self._record_proc.stdout.read(...)` com `except Exception: break`.
- **O método termina em `:796` — não há código pós-laço.** Se o gravador morre (mic
  desconectado, PipeWire reiniciado) ou o `read` falha, a thread **encerra** e o estado
  fica obsoleto (a UI continua mostrando "Ouvindo…"). O usuário não é avisado e não há
  recuperação — precisa reiniciar a sessão manualmente.
- **Contraste direto:** a nuvem reconecta até 5×; o local reconecta **0×**.

---

## 2. Objetivo

1. **Nuvem:** nenhuma fala é perdida por uma queda transitória — o áudio capturado
   durante o backoff é **bufferizado (limitado)** e **reenviado** ao reconectar.
2. **Local:** a captura de microfone **se recupera sozinha** de uma morte do gravador,
   com backoff, sem derrubar a sessão nem mentir no estado.
3. Sem regressão de memória (máquina **sem swap**): todo buffer é **limitado por tempo**.

---

## 3. Workstreams

### WS1 — Reconexão do modo local (gravador de microfone) — **IMPLEMENTADO (2026-09-29)**

Espelhar o idioma já provado na nuvem (`_live_session`, `ai/live.py:1825`), mas para o
processo de captura local:

- Extrair a criação do gravador para um helper `_open_recorder()` que aplica o **source
  filtrado WS2** (`core/audio/capture.py:102` `get_processed_capture_source` +
  `:125` `enrich_capture_cmd`) — hoje isso está inline em `ai/local_voice.py:689-693`.
- Envolver a captura num laço de reconexão com **backoff** (mesma curva da nuvem:
  1.5s→30s, máx 5 tentativas). Ao morrer o gravador: **encerrar o processo antigo**,
  emitir estado `CONNECTING` *"Reconectando microfone…"*, dormir com backoff (cancelável
  por `stop()` via `_is_running`), reabrir e **retomar o laço**.
- Só ir para `ERROR` após esgotar as tentativas (`ai/local_voice.py:695/709` hoje vão
  direto para `ERROR` na primeira falha).
- **Preservar a fala em curso:** não limpar `_speech_buffer`/`_preroll_buffer` na
  reconexão (a fala do usuário pode estar no meio quando o mic cai).

### WS2 — Buffer de áudio na nuvem (fechar a lacuna) — **IMPLEMENTADO (2026-09-29)**

O problema de fundo é que **captura e envio estão acoplados** ao `ws` (nascem/morrem
junto com a tentativa). A correção é desacoplar:

- **Buffer limitado** `core/audio/buffer.py` (novo): `PcmRingBuffer(seconds)` com
  `push(chunk)` / `drain() -> bytes` / `clear()`, **teto por segundos** (não por bytes
  infinitos) — respeitando a máquina sem swap.
- **Bomba de captura persistente:** subir o `pw-record` **uma vez por sessão lógica**
  (no `_live_session`, `ai/live.py:1825`), não por tentativa. Um task persistente lê o
  PCM e **empurra no ring buffer**; o `_mic_recorder_loop` (por tentativa) **drena o
  buffer e envia**. Enquanto o `ws` cai, a bomba continua enchendo (o buffer descarta o
  mais antigo quando estoura).
- **Flush na reconexão:** após `setupComplete` da nova tentativa, drenar o buffer e
  enviar os chunks **antes** do áudio ao vivo. Limite de flush: só reenviar se o gap for
  ≤ `live_reconnect_buffer_sec`; acima disso, **descartar** (áudio velho confunde o modelo
  e gasta tokens).
- **Encerrar o processo antigo** ao trocar de tentativa (corrige o vazamento de `:2175`).
- **Interação com WS2/B3:** o flush deve respeitar `_echo_isolation_blocks_send`
  (`ai/live.py:2111`) — não reenviar backlog enquanto o assistente fala/processa.

---

## 4. Passos sequenciais

### WS1 — Local (reconexão)

| # | Arquivo:linha | O que fazer | Riscos / armadilhas |
|---|---|---|---|
| L1 | `ai/local_voice.py:690-702` | Extrair `_open_recorder()` (Popen + `enrich_capture_cmd`); retorna `Popen` ou `None`. | Manter o fallback sem PipeWire (WS2/R5). |
| L2 | `ai/local_voice.py:677-716` | Envolver a captura em `_capture_with_reconnect()`: laço com backoff (1.5s→30s, máx 5), espelhando `_live_session`. | Backoff cancelável por `stop()` (checar `_is_running` no sono). |
| L3 | `ai/local_voice.py:695/709` | Não ir a `ERROR` na 1ª falha; emitir `CONNECTING` "Reconectando microfone…"; `ERROR` só ao esgotar. | Não regredir o caminho "nenhum gravador disponível" (erro permanente). |
| L4 | `ai/local_voice.py:742-745` | Preservar `_speech_buffer`/`_preroll_buffer` na reconexão (fala em curso). | Não vazar estado entre sessões (`stop()` limpa). |
| L5 | `ai/local_voice.py:633-647` | `stop()` deve interromper o backoff e encerrar o gravador corrente. | Corrida com o laço de reconexão. |
| L6 | `tests/test_local_voice.py` | Simular morte do `_record_proc` (`poll()` != None) → laço reabre; `stop()` durante backoff encerra. | Mock de `subprocess.Popen`; sem áudio real. |

**Implementado (2026-09-29) — anchors reais:**
- `ai/local_voice.py:671` `_build_recorder_cmd`, `:693` `_open_recorder`, `:708`
  `_close_recorder`, `:723` `_mic_loop` (reconexão com backoff 1.5s→30s),
  `:779` `_sleep_cancellable`, `:788` `_capture_loop`, `:813` `_process_chunk`
  (VAD extraído). `stop()` (`:633`) passou a usar `_close_recorder()`.
- `core/config.py:119-120` — `live_local_reconnect_enabled`, `live_local_reconnect_max_attempts`.
- **Bug corrigido junto:** a edição WS2/B2 do turno anterior deixou o tratamento de erro
  do ramo `else` (sem gravador) **dentro** de `if filtered_source:` → encontrar um source
  filtrado **abortava** a abertura do gravador (o modo local não gravava) e, sem gravador,
  `cmd` ficava indefinido. Passou verde porque `_mic_loop` não era testado. Corrigido na
  reescrita; coberto por `test_open_recorder_uses_filtered_source_without_aborting`.
- **Validação:** 14 testes novos (`TestLocalMicReconnect`), **validados contra o defeito**
  (reintroduzir a mutação → falha; restaurar → verde). Módulos `test_local_voice.py` +
  `test_ws2_capture.py` → **53 passed**; suíte completa → ver `docs/ENTREGA_A_VOZ_TELA.md`.

### WS2 — Nuvem (buffer) — **IMPLEMENTADO (2026-09-29)**

| # | Arquivo:linha | O que fazer | Riscos / armadilhas |
|---|---|---|---|
| N1 | `core/audio/buffer.py` (novo) | `PcmRingBuffer(seconds, rate=16000, width=2)`: `push`, `drain`, `clear`, `len_seconds`. Teto por segundos. | Sem swap: teto duro; nunca crescer sem limite. |
| N2 | `ai/live.py:1825` (`_live_session`) | Subir a **bomba de captura** persistente (1× por sessão lógica) que enche o ring buffer. | Encerrar a bomba no fim/`stop()`; não vazar `pw-record`. |
| N3 | `ai/live.py:2064-2084` | `_mic_recorder_loop` (por tentativa) passa a **drenar o buffer** e enviar; não abre mais o `pw-record` por sessão. | Preservar `norm_level`/`on_audio_level` (visualizador) e o gate de eco B3. |
| N4 | `ai/live.py:2175` | Encerrar o `_record_proc` anterior antes de abrir o novo (corrige o vazamento). | — |
| N5 | `ai/live.py` (pós-`setupComplete`, ~`:2061`) | Flush do buffer na reconexão, limitado a `live_reconnect_buffer_sec`; acima disso, descartar. | Não reenviar backlog durante `SPEAKING` (B3). |
| N6 | `core/config.py` | Novas flags: `live_reconnect_buffer_sec: float = 3.0`, `live_local_reconnect_enabled: bool = True`, `live_local_reconnect_max_attempts: int = 5`. | `PreferencesDialog` mantém o padrão (constrói `CopilotConfig()` fresco — ver Entrega A). |
| N7 | `tests/test_live.py` + novo p/ `buffer.py` | Ring buffer: teto, descarte do mais antigo, `drain`. Nuvem: queda → buffer enche → flush no reconnect; gap grande → descarta. | Mock de `ws.send` (AsyncMock). |

**Implementado (2026-09-29) — anchors reais:**
- `core/audio/buffer.py:28` `PcmRingBuffer` — `push():43`, `pop():56`, `drain():65`,
  `clear():73`, `len_seconds():88`. Thread-safe (`threading.Lock`), teto em **bytes
  derivado de segundos** (`seconds * rate * width`), descarta o mais antigo no estouro.
- `ai/live.py:1388` `self._pcm_buffer = PcmRingBuffer(...)`, `:1391` `self._capture_task`.
- `ai/live.py:1835` `_live_session` — sobe a bomba **1× por sessão lógica** (`:1859`) e a
  cancela + fecha o gravador no `finally` (`:1903-1917`).
- `ai/live.py:2229` `_capture_pump` (produtor persistente; reabre o gravador com 1.5 s de
  backoff se ele morrer, encerrando o anterior), `:2275` `_pump_sleep` (sono cancelável),
  `:2182` `_build_recorder_cmd`, `:2214` `_close_record_proc` (idempotente).
- `ai/live.py:2284` `_mic_recorder_loop` — agora **drena** o buffer (`:2293`) em vez de abrir
  `pw-record`; preserva RMS/`on_audio_level`, o gate de eco B3 e o ducking. Se o `ws.send`
  falha, faz `return` **deixando o backlog no buffer** para o flush da reconexão.
- `core/config.py:126` `live_reconnect_buffer_sec: float = 3.0`.

**Desvios do plano (deliberados, documentados):**
- **N5 não precisou de código explícito de flush.** O plano previa "medir o gap e descartar
  acima do teto". O ring buffer já garante isso **por construção**: no estouro ele descarta
  o mais antigo, então só sobrevivem os ≤ `live_reconnect_buffer_sec` mais recentes. Como o
  `_mic_recorder_loop` é FIFO e sem pacing, ao reconectar ele drena o backlog na velocidade
  do `ws.send` e volta ao vivo. Menos código, mesmo efeito — e o teto duro protege a máquina
  sem swap.
- **A bomba é dona do `Popen`; o `_mic_recorder_loop` é só consumidor.** Isso é o que
  desacopla captura de envio (a causa raiz do problema), não um detalhe de implementação.

**Regressão encontrada pela suíte completa (corrigida):**
- `tests/test_fase4_live_robustness.py` → **6 falhas** (`LiveSessionReconnectTest`), todas
  `AttributeError: 'GeminiLiveClient' object has no attribute '_pcm_buffer'`. Causa: o helper
  `_bare_client()` constrói o cliente via `__new__` e espelha um **subconjunto** dos atributos
  de `__init__`; o WS2 adicionou `_pcm_buffer`/`_capture_task`/`_record_proc`, dos quais
  `_live_session` passou a depender. Não é defeito de produção (`__init__` é o único caminho
  real de construção), mas o espelho do teste **precisa acompanhar o contrato**.
  Corrigido em `_bare_client()`, que agora também **substitui `_capture_pump` por um no-op** —
  sem isso o teste abriria um `pw-record` de verdade (verificado: `pgrep` sem órfãos).
- **Lacuna de cobertura fechada:** os testes de WS2 cobriam a bomba (`_capture_pump`) mas
  **não** o ciclo de vida na sessão. Adicionado `CloudSessionLifecycleTest` (3 testes):
  a bomba sobe **1×** atravessando 2 reconexões (3 tentativas), é cancelada no fim da sessão,
  e o gravador é fechado no `finally` (o vazamento de um `pw-record` por reconexão).

**Validação:**
- 18 testes em `tests/test_ws2_buffer.py` (+3 de ciclo de vida) + 6 em
  `test_fase4_live_robustness.py` restaurados.
- **Harness de mutação durável:** `tests/mutacao/mutate_ws2_buffer.py` — a validação manual
  abaixo virou artefato repetível (convenção do repo). Roda os 4 defeitos silenciosos do WS2 e
  exige que a suíte os mate: **4 matadas, 0 sobreviventes, 0 inválidas** (exit 0). Os defeitos
  são silenciosos por natureza — devolver a bomba para dentro do laço, ou deixar de fechar o
  gravador, **não quebra nada visível**; só a mutação prova que os testes pegariam.
  Âncoras conferidas pela tranca estática (`tests/test_mutacao_anchors.py`, 3 passed).
- **Validados contra o defeito** (mutação → falha → restauração; `live.py` conferido por
  `md5sum` e `grep` sem resíduo):
  - *A — bomba nunca sobe* (`create_task` removido): 2 testes falham.
  - *B — start movido para dentro do `while`* (bomba por tentativa, o acoplamento antigo):
    `3 != 1` → falha. É exatamente a regressão que o desenho evita.
  - *C — `_close_record_proc()` removido do `finally`*: o teste do vazamento falha.
  - *D — ring buffer cresce sem limite*: o teste do teto falha.
- Módulos afetados (`test_ws2_buffer`, `test_fase4_live_robustness`, `test_ws2_capture`,
  `test_local_voice`, `test_live`) → **182 passed**.
- **Suíte completa** (`run_tests_safe.py`, um arquivo por processo) → **161 módulos, 0
  falhas, 197 s**, pico de RSS 501 MB (dentro do teto de 900 MB, máquina sem swap).

**Pendência de aceitação (não medida):** o critério de §5 ("derrubar o WS por ~3 s e ver a
fala do intervalo transcrita") exige **sessão Live real com API key** — não foi executado
nesta rodada. O que está provado por teste é o mecanismo (buffer enche durante a queda, o
flush drena, sem órfão). Falta a verificação ponta-a-ponta com a API.

---

## 5. Critérios de aceitação

- **Nuvem:** derrubar o WebSocket por ~3 s (ex.: `kill` da conexão) e confirmar que, ao
  reconectar, **a fala do intervalo é transcrita** (buffer ≤ `live_reconnect_buffer_sec`).
  Gap maior que o teto → descarte **explicado no log**, sem travar.
- **Local:** matar o processo `pw-record` durante a sessão → a captura **volta sozinha**
  em ≤ 3 s, com estado "Reconectando microfone…" → "Ouvindo…", **sem reiniciar a sessão**.
- **Recursos:** nenhum `pw-record` órfão após N reconexões (contagem de processos estável);
  pico de RSS dentro do baseline da suíte (hoje 366 MB).
- **Sem regressão:** suíte completa via `run_tests_safe.py` verde.

---

## 6. Riscos e armadilhas (Code Review)

- **B1 (memória/sem swap):** buffer sem teto = OOM. **Teto por segundos** é requisito, não
  refinamento. Alinhado a R7 da Entrega A.
- **B2 (áudio velho):** reenviar backlog muito antigo confunde o modelo e gasta tokens.
  Limitar o flush e **descartar** além do teto.
- **B3 (eco):** o flush não pode furar o isolamento de eco (`_echo_isolation_blocks_send`,
  `ai/live.py:2111`) — nunca reenviar backlog durante fala/processamento do assistente.
- **B4 (vazamento de processo):** hoje cada reconexão deixa um `pw-record` órfão
  (`ai/live.py:2175`). Encerrar sempre o anterior antes de abrir o novo.
- **B5 (thrash local):** reiniciar o gravador rápido demais em falha permanente (mic
  arrancado) martela o PipeWire. Backoff + `ERROR` após esgotar.
- **B6 (estado honesto):** durante a reconexão, o estado deve ser `CONNECTING`
  ("Reconectando…"), nunca `LISTENING` mentiroso (regra de honestidade da proposta §2.4).
- **B7 (cancelamento):** o backoff precisa morrer em `stop()` — tanto no local (`_is_running`)
  quanto na nuvem (`_live_session` já faz, `:1880-1883`).

---

## 7. Ordem global e commits

1. **WS1 (L1→L6)** — local; menor risco, fecha a "morte silenciosa" (pior UX hoje).
2. **WS2 (N1→N7)** — nuvem; maior risco (refactor do acoplamento captura/envio).

**Sequência sugerida de commits:**
1. `feat(local-voice): reconexão automática do gravador com backoff (P0-4/WS1)`
2. `feat(audio): ring buffer + flush na reconexão do Gemini Live (P0-4/WS2)`
3. `fix(live): encerra pw-record anterior na reconexão (vaza processo)`
4. `test(resiliencia): reconexão local + buffer na queda`
