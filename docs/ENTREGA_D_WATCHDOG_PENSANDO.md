# Entrega D — Watchdog de THINKING: timeout adaptativo + pedido de continuação

> Plano de ação para o **P1-8** de `docs/MELHORIAS_VOZ_TELA_AO_VIVO.md` §1
> (*"Sintonização do watchdog (estado 'pensando' explícito)"*, impacto Médio, risco Baixo).
> Cada ponto ancora no estado real do código, **lido nesta rodada (2026-09-29)**.
>
> **STATUS: IMPLEMENTADO (2026-09-29).** Entregas anteriores: `ENTREGA_A_VOZ_TELA.md` (P0-1/2/3),
> `ENTREGA_B_RECONEXAO.md` (P0-4), `ENTREGA_C_MODO_E_SINAIS.md` (P1-5).

---

## 1. Evidência do problema (código atual, verificado)

`ai/live.py:2603-2620`, ao fim de `_execute_tool_call` (`:2543`), depois de devolver as
observações ao modelo:

```python
self._set_state(LiveVoiceState.THINKING, "Processando resposta...")
...
async def _thinking_timeout_watchdog():
    await asyncio.sleep(12.0)
    if (getattr(self, "_is_running", False)
            and self.state == LiveVoiceState.THINKING
            and (not hasattr(self, "_audio_play_queue") or self._audio_play_queue.empty())):
        logger.debug("Watchdog: timeout em THINKING após toolResponse, restaurando LISTENING.")
        self._set_state(LiveVoiceState.LISTENING, "Ouvindo você...")
```

Três defeitos, todos silenciosos:

### 1.1 O timer **contradiz o servidor**

O Gemini Live informa o andamento por `interaction_status` (`:2482-2490`): `IN_PROGRESS`
significa "ainda estou trabalhando" e o cliente **já** sabe respeitar isso (`:2484`, `:2521`).
Mas o watchdog de `:2608` **não consulta** esse campo: passados 12 s ele libera para
`LISTENING` mesmo com o servidor dizendo `IN_PROGRESS`. O cliente desmente a única fonte que
realmente sabe se o turno está vivo.

### 1.2 Liberar para `LISTENING` **reabre o microfone** — é isso que "corta o turno"

`_echo_isolation_blocks_send` (`:2150`) bloqueia o envio de áudio quando o estado é
`EXECUTING`/`THINKING` (`:2170`). A cooldown acústica de 1.2 s (`:2601`) já expirou muito antes
dos 12 s. Consequência: no instante em que o watchdog escreve `LISTENING`, o portão do
microfone **abre** e o áudio da sala passa a ser enviado enquanto o modelo ainda gera a
resposta — interrompendo-a. O dano não é o rótulo errado; é o microfone reaberto.

### 1.3 O 12 s é uma constante mágica, não uma medida

O comentário de `:2604-2606` justifica o valor ("no modo Extended Thinking o modelo raciocina
bastante… janela mais folgada (12s)"), mas é o **mesmo** 12 s para uma observação de 40
caracteres e para um `git log` de 3.500. O volume que o modelo precisa digerir não entra na
conta — e a §2.4 do plano pede exatamente "timeout *adaptativo* (proporcional ao tamanho do
contexto / tipo de tarefa)".

---

## 2. Objetivo

1. O watchdog **consulta o servidor** antes de decidir que o turno morreu.
2. O timeout **cresce com o volume** devolvido pelas ferramentas, com teto.
3. Ao estourar, **pede continuação** em vez de encerrar o turno; liberar o microfone é o
   **último** recurso, e o rótulo diz a verdade sobre o que aconteceu.

**Fora de escopo:** o caminho `interaction_status`→`IDLE` (`:2486-2490`), que já é correto;
barge-in por gesto (P2-11); latência fim-a-fim (§2.4).

---

## 3. Passos

| # | Arquivo:linha | O que fazer | Riscos / armadilhas |
|---|---|---|---|
| W1 | `ai/live.py` (módulo) | `thinking_watchdog_seconds(total_output_chars, n_tool_calls, base_sec, max_sec)` — função **pura**, testável sem asyncio/GTK. | Nunca devolver < `base_sec`; respeitar o teto. |
| W2 | `core/config.py` | `live_thinking_watchdog_base_sec=12.0`, `live_thinking_watchdog_max_sec=60.0`, `live_thinking_max_nudges=2` (`0` desliga a continuação). | `PreferencesDialog` mantém o padrão (config fresco). |
| W3 | `ai/live.py:2607` | Extrair o watchdog para o método `_thinking_watchdog(ws, timeout)` (testável direto). | Não vazar task: encerra quando o estado muda. |
| W4 | `ai/live.py` (watchdog) | Se `_interaction_status == "IN_PROGRESS"`, o turno está vivo → **re-armar** sem liberar o mic. | `IN_PROGRESS` preso (servidor travado) não pode prender o usuário para sempre → contar re-armes e cair no nudge. |
| W5 | `ai/live.py` (watchdog) | No estouro com nudges disponíveis: `_set_state(THINKING, "Ainda processando…")` + enviar `build_realtime_text_msg(NUDGE)` e re-armar. | Só enviar se `ws` vivo; erro de envio → liberar. |
| W6 | `ai/live.py` (watchdog) | Esgotados os nudges: `LISTENING` com mensagem **honesta** ("Sem resposta do modelo — pode falar de novo."), não "Ouvindo você...". | Manter THINKING para sempre seria pior: prenderia o mic. |
| W7 | `tests/test_live.py` (+ novo p/ a função pura) | Função pura: base, escala com volume, teto. Watchdog: `IN_PROGRESS` não libera; nudge é enviado; esgotado libera com rótulo honesto. | Sem rede: `ws.send` é AsyncMock; timeout minúsculo injetado. |

---

## 4. Critérios de aceitação

- Com o servidor em `IN_PROGRESS`, o watchdog **não** libera o microfone.
- Observação grande → timeout maior que observação pequena; nunca acima do teto.
- No primeiro estouro, o modelo recebe um pedido de continuação e o estado **permanece**
  `THINKING` (microfone fechado).
- Só depois de esgotar os pedidos o microfone abre, e a mensagem diz que não houve resposta.

---

## 5. Implementado (2026-09-29) — âncoras reais

### 5.1 Função pura e constantes — `ai/live.py`

| Âncora | Linha | O que é |
|---|---|---|
| `THINKING_CONTINUATION_NUDGE` | `:126` | Texto do pedido de continuação ("Você ainda está no mesmo turno…"). |
| `_CHARS_PER_EXTRA_SECOND` | `:134` | `2000.0` — caracteres de observação que "custam" 1 s a mais de paciência. Heurística **não medida** contra a API (por isso é parâmetro). |
| `_MAX_IN_PROGRESS_WAITS` | `:139` | `3` — teto de re-armes por `IN_PROGRESS` antes de tratar como silêncio real. |
| `thinking_watchdog_seconds()` | `:142-163` | Função pura. `min(topo, base + chars/2000 + (chamadas-1))`. `topo = max(base, max_sec)` — teto incoerente não pode virar piso. |

### 5.2 Fiação em `_execute_tool_call` — `ai/live.py:2650-2663`

Depois de devolver as observações, o timeout passa a ser **calculado** a partir do volume real:

```python
total_chars = sum(
    len(str(r.get("response", {}).get("output") or "")) for r in responses
)
timeout = thinking_watchdog_seconds(
    total_chars, len(responses),
    base_sec=float(getattr(cfg, "live_thinking_watchdog_base_sec", 12.0)),
    max_sec=float(getattr(cfg, "live_thinking_watchdog_max_sec", 60.0)),
)
try:
    asyncio.create_task(self._thinking_watchdog(ws, timeout))
except Exception:
    pass
```

### 5.3 O watchdog — `ai/live.py:2665-2728`

Substitui a closure de `asyncio.sleep(12.0)` fixo. Três regras, nesta ordem:

1. **`:2697-2706` — o servidor manda.** `_interaction_status == "IN_PROGRESS"` e
   `in_progress_waits < _MAX_IN_PROGRESS_WAITS` → re-arma **sem liberar o mic**. O limite
   evita que um `IN_PROGRESS` preso (servidor travado) prenda o usuário para sempre.
2. **`:2709-2720` — pede continuação.** `remaining > 0` → `_set_state(THINKING, "Ainda
   processando…")` + `ws.send(build_realtime_text_msg(THINKING_CONTINUATION_NUDGE))` e
   re-arma. Falha no envio **não** interrompe o laço (o nudge já foi descontado → as voltas
   seguintes caem na liberação honesta; sair daqui deixaria o estado preso em THINKING).
3. **`:2722-2728` — liberação honesta.** Esgotados os pedidos: `LISTENING` com
   `"Sem resposta do modelo — pode falar de novo."` (não "Ouvindo você…").

Saídas antecipadas em `:2688-2693`: não está rodando / o estado já mudou (o modelo falou ou o
usuário interrompeu) / já há áudio na fila (a resposta chegou).

### 5.4 Config — `core/config.py:133-135`

```python
live_thinking_watchdog_base_sec: float = 12.0   # piso preserva o turno comum já validado
live_thinking_watchdog_max_sec: float = 60.0    # teto protege o usuário
live_thinking_max_nudges: int = 2               # 0 desliga a continuação
```

### 5.5 Por que isto corrige os três defeitos da §1

| Defeito | Correção |
|---|---|
| §1.1 timer contradiz o servidor | Regra 1 lê `_interaction_status` antes de decidir (com teto). |
| §1.2 liberar para `LISTENING` reabre o mic | Regras 1 e 2 **mantêm** `THINKING` — o portão de eco (`_echo_isolation_blocks_send`) segue bloqueando o envio; só a regra 3 abre o mic, e apenas quando não há mais o que esperar. |
| §1.3 o 12 s é constante mágica | `thinking_watchdog_seconds` escala com o volume devolvido e o nº de ferramentas. |

---

## 6. Validação (medida nesta rodada)

### 6.1 Testes unitários — `tests/test_thinking_watchdog.py` (novo, 19 testes)

Arquivo dedicado (não dentro de `test_live.py`) por **determinismo**: isola a matemática pura
e o laço assíncrono sem carregar o harness do cliente Live inteiro. Três classes:

- `ThinkingWatchdogSecondsTest` — pura: base, escala com volume, teto, `base > max`, entradas
  negativas, cada ferramenta extra soma 1 s.
- `ThinkingWatchdogTest` — comportamento: `IN_PROGRESS` não libera; nudge é enviado e o estado
  permanece `THINKING`; esgotado libera com rótulo honesto; falha de envio não prende o estado;
  retorna quando não está rodando / o estado mudou / já há áudio na fila.
- `ThinkingWatchdogWiringTest` — a fiação: `_execute_tool_call` **arma** o watchdog e passa o
  timeout **calculado** (não a base fixa). As asserções verificam **relações** (cresce com o
  volume, respeita teto/base), não constantes adivinhadas — a fiação conta caracteres da
  observação **serializada**, não da string crua.

`tests/test_live.py` voltou ao seu estado original (64 passed) — os testes da Entrega D foram
movidos para o arquivo novo.

### 6.2 Harness de mutação — `tests/mutacao/mutate_thinking_watchdog.py` (novo)

**9 KILLED / 0 sobreviventes / 0 equivalentes / 0 inválidas**, `exit=0`. Inclui duas mutações
de **fiação** (a CHAMADA apagada — lacuna clássica; e armar com a base fixa). `live.py`
verificado **byte-idêntico** após a rodada (hash `82d09aac…` = backup). Âncoras estáticas
(`tests/test_mutacao_anchors.py`) → 3 passed.

### 6.3 Suíte completa (runner seguro)

Executada pelo `run_tests_safe.py` (sem swap → nunca `pytest` direto). Resultado no fim desta
entrega, registrado em `ENTREGA_A_VOZ_TELA.md` §suíte:

```
módulos: 162   falhas: 0   tempo: 208s
pico de RSS em qualquer arquivo: 470 MB
```

O total subiu de 161 → **162** módulos porque esta entrega adiciona
`tests/test_thinking_watchdog.py`.
