# PROPOSTA — Cancelamento do Modo Agente (engrenagem que não para)

Defeito relatado: ao interromper o raciocínio do Modo Agente (Esc, botão "Interromper", ou
trocar/apagar a conversa com a engrenagem girando), a execução **não é de fato cancelada** — a
engrenagem (`Gtk.Spinner` em `ui/widgets/prompt_bar.py:279`) continua girando indefinidamente,
mesmo após apagar a conversa e iniciar uma nova. Investigação por leitura de código, não
asserção; nenhuma execução ao vivo feita ainda.

**Tese: o ciclo de fim é opcional, não inevitável.** O `_is_busy` da janela só desliga em três
lugares, e o caminho do agente depende de uma chamada cooperativa que pode morrer antes de
chegar lá. Trocar de conversa não aborta — é a porta de mão única que produz o sintoma visto.

---

## 0. Medição que define o plano

### 0.1 Onde o "rodando" mora

| Símbolo | Local | Quem liga | Quem desliga |
|---|---|---|---|
| `self._is_busy` (bool) | `ui/app.py:233` | `submit` (prompt_bar:606), `start_agent_execution` (app.py:2767), `continue_agent_execution` (app.py:3010) | `_on_plan_ready` (app.py:2659), `_on_agent_finished` (app.py:3114), try/except do setup síncrono (app.py:2792) |
| `prompt_bar.set_busy(b)` | `ui/widgets/prompt_bar.py:671` | (acima) | (acima) |
| `Gtk.Spinner` | `prompt_bar.py:279` | `set_busy(True)` → `start()` | `set_busy(False)` → `stop()` |
| `self._active_agent_loop` | `ui/app.py:339` | `start_agent_execution` (app.py:2914), `continue_agent_execution` (app.py:3068) | `_on_agent_finished` (app.py:3115) |
| `self._active_agent_widget` | `ui/app.py:340` | `start_agent_execution` (app.py:2782), `continue_agent_execution` (app.py:3012) | `_on_agent_finished` (app.py:3116) |

`AgentLoop._running` (lê só via `running`, agent.py:288) é setado em `try/finally` (agent.py:319, 435),
então cobre corretamente o loop interno — o que **não** cobre é o setup que vem **antes** do
`loop.run(...)`.

### 0.2 Os quatro defeitos, todos medidos por leitura de código

**Defeito 1 — Abort cooperativo (Esc / botão "Interromper" pode levar minutos a fazer efeito).**
`AgentLoop.abort()` (agent.py:274) apenas seta `threading.Event`. O flag é checado **somente** no
topo do `while True` (agent.py:323). O `_ask_planner` (agent.py:351) chama `planner.decide()` que
pode ser uma chamada HTTP de minutos; `registry.call()` (agent.py:407) também não é interrompido.
Resultado: usuário aperta Esc, vê "⏹ Sinal de parada enviado ao agente." (`abort_active_agent`,
app.py:2205), mas a engrenagem só para quando a thread atual ceder. Documentado como
intencional em `ai/agent.py:1-9` ("o modelo nunca decide quando parar"), mas **não refletido
na UI** — o spinner deveria sinalizar "ainda terminando…".

**Defeito 2 — Trocar de conversa NÃO aborta (causa direta do sintoma).**
- `_on_new_topic` (`ui/app.py:3436`)
- `_resume_topic` (`ui/app.py:3409`)
- `on_delete_topic` (`ui/widgets/sidebar.py:432`, quando é a conversa ativa)
- `clear_history` (`ui/widgets/sidebar.py:472`)

**Nenhum chama `abort_active_agent()`.** O thread antigo mantém `_is_busy = True` na nova
conversa. Pior: quando `_on_agent_finished` finalmente disparar, ele grava o `result` do agente
antigo **na sessão atual** (`app.py:3127-3131`), que pode ser uma sessão nova/vazia — vira
lixo histórico. O `agent_widget` antigo foi removido pelo `chat_stream.rebuild()`
(`chat_stream.py:528`), mas a referência Python segue viva nas closures do thread, e o widget
em si fica órfão (sem pai GTK) — comportamento dependente de GC, indefinido.

**Defeito 3 — Setup do agente FORA do try/except (engrenagem presa para sempre).**
Em `start_agent_execution`, o try/except (`app.py:2790-2797`) cobre só até
`chat_stream.scroll_to_bottom()` (linha 2789). **Tudo dentro de `agent_thread()`** — `router.route`
(app.py:2803), `ToolRegistry(...)` (app.py:2828-2836), `_build_subagent_runner`/`registry.subagent_runner`
(app.py:2842-2844), `get_step_budget` (app.py:2846-2854), `AgentLoop(...)` (app.py:2856-2863),
setup do Vision (app.py:2866-2913) — está **fora** de qualquer guarda.

Se qualquer um levantar exceção (provedor indisponível, import falhando, `VisionLLMPlanner`
quebrando), o thread morre **sem** chamar `GLib.idle_add(self._on_agent_finished, ...)` (app.py:2944),
e `_is_busy` fica preso sem volta. A engrenagem gira para sempre. O caminho de `continue_agent_execution`
(app.py:3000+) tem o mesmo padrão, mas o try/except da linha 3087 só cobre o `loop.run(...)` — não
o setup.

**Defeito 4 — `submit()` engole silenciosamente quando busy.**
`ui/widgets/prompt_bar.py:582-583`:
```python
if ctx._is_busy:
    return
```
Sem toast, sem aviso. O usuário fica sem saber por que o Enter não faz nada. Multiplicador dos
defeitos 2 e 3.

### 0.3 Cobertura de testes (medida)

Não há teste que reproduza nenhum dos quatro defeitos.

| Teste | O que cobre | O que NÃO cobre |
|---|---|---|
| `tests/test_agent_loop.py:279` `test_abort_encerra_no_proximo_passo` | Abort cooperativo feliz | Planejador que dorme antes de responder |
| `tests/test_agent_loop.py:293` `test_reset_permite_nova_execucao_apos_aborto` | Reset do flag | Thread morrendo antes do reset |
| `tests/test_agent_gui.py:749` `test_start_agent_execution_invokes_turn_creation_without_name_error` | Parte síncrona antes do `threading.Thread` | Setup dentro do thread (defeito 3) |
| `tests/test_ui_refactor.py:132` `test_submit_renders_assistant_response` | `not _is_busy` em ≤10s no caminho **sem agente** | Caminho do agente |

A suíte do agente também não cobre: trocar de conversa com agente rodando; `_is_busy` preso após
exceção; `submit` durante busy.

---

## 1. A proposta — três frentes

### 1.1 Frente A — Cleanup inevitável

**Matar o defeito 3, reduzir a janela do defeito 2.**

1. Embrulhar **todo o corpo de `agent_thread`** num `try/finally` que sempre chama
   `GLib.idle_add(self._on_agent_finished, result, objective, agent_widget, error_msg)`. O `try`
   interno atual (loop.run) permanece; o `finally` cobre qualquer caminho de saída —
   exceção, retorno antecipado, `KeyboardInterrupt`. Onde está hoje (app.py:2935-2944):

   ```python
   try:
       result = loop.run(prompt_to_run, on_step=on_step, on_approval=on_approval)
   except Exception as exc:
       ...
   GLib.idle_add(self._on_agent_finished, result, objective, agent_widget, err)
   ```

   vira:

   ```python
   try:
       try:
           result = loop.run(...)
       except Exception as exc:
           result = None; err = str(exc)
   finally:
       GLib.idle_add(self._on_agent_finished, result, objective, agent_widget, err)
   ```

   Mesma transformação em `continue_agent_execution` (app.py:3087-3101).

2. Limpar `self._active_agent_loop` e `self._active_agent_widget` em `_on_new_topic`,
   `_resume_topic`, `clear_history` **antes** de qualquer outra coisa — antes mesmo do
   `_save_current_session` — para que o `_on_agent_finished` que vier atrasado encontre None
   nos refs e não duplique estado. (O `result` do thread antigo já foi gravado numa sessão
   que **era** a ativa quando rodou; re-zerar refs evita carregar lixo pra frente.)

3. **Watchdog de timeout** (front B do defeito 1): se `_is_busy` continuar `True` por mais de
   `max_seconds + 30` (config atual `agent_max_seconds = 300`), desligar com toast
   "Execução encerrada por tempo limite interno." Singleton no `CopilotWindow` (reaproveita
   o que `kill_switch` já tem). É a defesa contra o caso "planejador travou em rede ruim".

### 1.2 Frente B — Trocar de conversa cancela o que estava rodando

**Matar o defeito 2 direto.**

1. Em `_on_new_topic` (app.py:3436), `_resume_topic` (app.py:3409), `on_delete_topic`
   (sidebar.py:432, quando `ctx.session.id == topic_id`), `clear_history` (sidebar.py:472):
   **chamar `abort_active_agent()` no topo**, antes de qualquer outra coisa.
2. Decidir destino do resultado parcial: **(a)** descartar — mais simples, mais honesto, mais
   barato. **(b)** gravar o resultado na conversa original antes de mudar de sessão — mais
   bonito, exige carregar a sessão original nas closures do thread. **Recomendo (a) para esta
   correção.** (b) é trabalho separado, e o resultado parcial provavelmente tem pouco valor.
3. O widget órfão (removido pelo `chat_stream.rebuild`) precisa ser tratado. Forma simples:
   marcar `set_visible(False)` em todos os agent_widgets ativos quando `_on_new_topic` rodar
   antes do `rebuild()` — o widget ainda existe em memória, deixa de aparecer, e o
   `_on_agent_finished` ainda pode chamar `widget.finish(result)` (verificar que `finish` é
   seguro sem pai — leitura de `agent_card.py:738-797` mostra que sim, ele só toca `self.*`,
   nenhum `get_parent()`).

### 1.3 Frente C — Feedback honesto

**Matar o defeito 4.**

`submit` quando busy (`prompt_bar.py:582-583`):
```python
if ctx._is_busy:
    self.ctx.show_toast("Aguarde a operação atual terminar (Esc interrompe).")
    return
```
Texto alinhado à régua do projeto: tom informativo, dica de saída (Esc), sem drama. Toast
existente em `app.py` — basta usar o que já tem.

### 1.4 Decisão não pedida aqui (registrar)

A **reversibilidade por chamada** (`docs/PROPOSTA_REVERSIBILIDADE.md`) entra nessa conversa
só se você quiser. O Esc cooperativo é aceitável desde que a UI diga "ainda terminando…"
durante o wait **e** desde que trocar de conversa não deixe o spinner preso. As duas
condições juntas são as frentes A+B+C. Sem A, B e C seguem quebrados.

A `_run_loop_until(not _is_busy, timeout_ms=10_000)` que `tests/test_ui_refactor.py:132`
já usa é o tipo de teste que essas mudanças exigem — reuso direto.

---

## 2. Plano de validação (régua do projeto)

**Cada teste é validado contra o defeito: reintroduzir o bug, ver o teste falhar, restaurar.**

### 2.1 Testes novos (em `tests/test_agent_gui.py` e `tests/test_agent_loop.py`)

1. **`test_agent_thread_finally_cleans_up_on_setup_exception`** — monkeypatch `AgentLoop(...)` (ou
   `router.route(...)`) para levantar; verifica que `_is_busy = False` em ≤2s e que
   `_on_agent_finished` foi chamado com `error_msg`. Reintrodução: remover o `finally` → falha.
2. **`test_continue_thread_finally_cleans_up_on_setup_exception`** — equivalente para o caminho
   `continue_agent_execution`.
3. **`test_new_topic_aborts_running_agent`** — sobe agente num thread (planner que dorme 1s por
   passo); chama `_on_new_topic`; exige `_is_busy = False` em ≤2s e `agent_widget` com
   `get_visible() is False`. Reintrodução: remover a chamada a `abort_active_agent` em
   `_on_new_topic` → falha.
4. **`test_resume_topic_aborts_running_agent`** — idem para `_resume_topic`.
5. **`test_delete_active_topic_aborts_running_agent`** — idem para `on_delete_topic` quando é a
   sessão ativa.
6. **`test_clear_history_aborts_running_agent`** — idem para `clear_history`.
7. **`test_submit_busy_shows_toast`** — mock `_is_busy = True`; chama `submit`; exige
   `show_toast` chamado com a mensagem contendo "Esc". Reintrodução: remover o `show_toast` →
   falha.
8. **`test_abort_encerra_ate_quando_planejador_dorme`** — variante do `test_abort_encerra_no_proximo_passo`
   com planner que `time.sleep(2)` antes de cada `decide()`; verifica que `loop.run()` retorna
   em ≤3s após `abort()`. Reintrodução: remover `if self._abort.is_set():` no topo do `while` →
   falha.

### 2.2 Harness de mutação (em `tests/mutacao/mutate_agent_cleanup.py`)

Reintroduções a matar (todas como `MUTACOES`+`ALVO`, com âncora única — validado pela tranca
estática `tests/test_mutacao_anchors.py`):

1. `start_agent_execution`: `try/finally` removido (sem cleanup) → 1 mutação.
2. `start_agent_execution`: `if self._abort.is_set():` removido do loop (defeito 1) → 1 mutação
   (referência ao `AgentLoop`, não à UI).
3. `_on_new_topic`: chamada a `abort_active_agent` removida → 1 mutação.
4. `_resume_topic`: idem → 1 mutação.
5. `clear_history`: idem → 1 mutação.
6. `prompt_bar.submit`: `show_toast` quando busy removido → 1 mutação.
7. `_on_agent_finished`: zerar `_active_agent_loop`/`_active_agent_widget` removido → 2 mutações.

**Tranca de ambiguidade** (modelo do repo): trecho com `count(de) > 1` ⇒ `INVÁLIDA`. Validar
estaticamente com `tests/test_mutacao_anchors.py` antes de rodar o harness (custa 0,1s; rodar
o harness sem essa checagem já custou re-trabalho em outros harnesses).

**Meta:** 9 KILLED / 0 sobreviventes / 0 INVÁLIDAS.

### 2.3 Suíte completa

```bash
~/.workbuddy-ai/skills/zorin-copilot-gui-change/scripts/run_tests_safe.py \
    --python .venv/bin/python
```

Baseline 158 módulos / 0 falhas (2026-09-22, `MEMORY.md`). Conferir a **linha de resumo** e a
ausência de `pytest` órfão antes e depois.

### 2.4 Validação ao vivo (onde dobra nenhuma chega)

1. Mandar tarefa Agente, apertar Esc — spinner para em ≤2s, toast aparece, `widget.finish`
   chamado com `stop_reason="aborted"`.
2. Trocar de conversa com agente rodando — spinner para, agent_widget some, nenhuma referência
   zumbi (verificar com `gc.get_referrers` ou `weakref`).
3. Matar provedor (desligar internet), mandar tarefa Agente — spinner para em ~330s via
   watchdog.
4. Apagar conversa ativa com agente rodando — idem item 2.

### 2.5 Risco residual (registrar)

O `AgentLoop` permanece cooperativo. Em chamadas de planejador de **vários minutos** (LLM
remoto sem streaming), Esc ainda vai demorar para fazer efeito — isso é intencional (agent.py:6-9)
e a UI passa a dizer "ainda terminando…" via watchdog em `max_seconds + 30`. Não há, nesta
proposta, interrupção de uma chamada HTTP em curso: seria comportamento novo e exige threading
ou asyncio (decisão de arquitetura separada — fora do escopo).

---

## 3. Decisões pendentes

| # | Decisão | Opções | Recomendação |
|---|---|---|---|
| D1 | Resultado parcial ao trocar de conversa | (a) descartar / (b) gravar na sessão original | (a) — simples, honesto |
| D2 | Texto do toast quando busy | Curto (1 linha) / Com dica do Esc | "Aguarde a operação atual terminar (Esc interrompe)." |
| D3 | Watchdog de timeout | `max_seconds + 30` / configurável / só quando provedor remoto | `max_seconds + 30` (simples, sem novo campo no config) |

---

## 4. Validação executada (2026-09-27, desktop real)

Implementadas as três frentes + watchdog. Rodado no desktop do Bruno (`DISPLAY=:0`,
`WAYLAND_DISPLAY=wayland-1`, `Adw.init()` OK) — `xvfb` não existe nesta máquina, mas há display
real, então `tests/test_agent_gui.py` coleta e roda.

| Checagem | Comando | Resultado |
|---|---|---|
| Testes de GUI | `run_tests_safe.py tests.test_agent_gui` | **40 passed** (31 antigos + 9 novos) |
| Harness de mutação | `tests/mutacao/mutate_agent_cleanup.py` | **9 KILLED / 0 sobreviventes / 0 equivalentes / 0 inválidas** |
| Suíte completa | `run_tests_safe.py` | **159 módulos / 0 falhas / 239s / pico 366 MB**, sem `pytest` órfão |

**Defeito de teste encontrado e corrigido no caminho (medido, não presumido):**
`test_agent_thread_finally_cleans_up_on_setup_exception` estava **verde vacuamente** — a mutação
"start: cleanup do agent_thread removido" **sobrevivia**. O dublê `_mock_copilot_window()` deixava
`chat_stream._build_user_bubble` devolver `MagicMock`, e `turn_box.append(user_bubble)` (um
`Gtk.Box` real, `app.py:2846`) levantava `TypeError` no caminho **síncrono**; o `except` da própria
função punha `_is_busy=False` e **o thread nunca subia** — o teste passava sem exercitar o `finally`
do thread. Conserto: o dublê devolve um `Gtk.Box()` real; o thread sobe e a mutação morre. Prova
contra o defeito: reintroduzido o bug à mão → teste verde (SOBREVIVEU); com o conserto → 9 KILLED.

**Pendente:** §2.4 (validação ao vivo) — exige o app aberto e interação manual.
| D4 | Escopo da frente B | Trocar/abagar/conversa nova + retoma + clear | Aplicar nos quatro (consistência) |