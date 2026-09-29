# PROPOSTA — Evolução do Modo Agente

Análise do **estado real** do Modo Agente no código de hoje e proposta de melhoria em quatro
ondas. Cada afirmação abaixo foi **medida** (leitura de código com linha citada, ou execução de
cálculo no `.venv`) — não deduzida do desenho. O que não foi medido está marcado como tal.

**Data:** 2026-09-29 · **Método:** leitura do código de produção + medições executadas
(`ToolRegistry()`, `build_planner_prompt()`, `get_step_budget()`). **Nenhuma execução ao vivo**
do agente foi feita nesta análise; onde isso importa, está dito.

---

## 0. Medições que sustentam esta proposta

| # | O que foi medido | Como | Resultado |
|---|---|---|---|
| M1 | Ferramentas no `ToolRegistry` | `ToolRegistry().names()` | **90** (a memória do projeto dizia 85; o número cresceu — reconferido agora) |
| M2 | Ferramentas builtin que o filtro de relevância deixa passar | `len(schema())` com 0 servidores MCP | **90 de 90** — o filtro só atua sobre ferramentas `mcp__*` |
| M3 | Peso do catálogo no prompt do planejador | `len(bloco_de_ferramentas)` | **19.465 chars ≈ 4.866 tokens** |
| M4 | Catálogo como fração do prompt no passo 0 | `len(bloco)/len(prompt)` | **86%** |
| M5 | Crescimento do prompt com o histórico | `build_planner_prompt` com 0/5/10/20/29 passos | 22.658 → 28.301 → 31.528 → 37.988 → **44.069 chars** (~11.017 tokens) |
| M6 | Orçamento entregue ao loop para 15/300 configurados | `get_step_budget('abrir o firefox')` e `get_step_budget('pesquisar e resumir…')` | simples → **10 passos / 120 s**; complexo → **30 passos / 480 s** |
| M7 | Quando o watchdog da UI dispara | `_arm_agent_watchdog` usa `config.agent_max_seconds + 30` | **330 s** |
| M8 | `autonomy_level="high"` promove o quê | `RiskPolicy('high', ['~/Documentos']).classify('write_file', …)` | `AUTO` — mas `'write_file' in ToolRegistry().names()` é **False** |
| M9 | A ferramenta real de escrita no modo agente | `classify('write_document', {'directory': '~/Documentos'})` | **`CONFIRM`**, mesmo em zona de confiança |
| M10 | Risco da casa inteligente | `classify('smart_home_control')`, `classify('smart_home_automate')` | **`SAFE`** (muda a casa sem pergunta) |
| M11 | `RiskPolicy` é consultada pelo caminho A? | `grep -n "RiskPolicy\|classify" src/zorin_copilot/shell/executor.py` | **zero acertos** |
| M12 | A GUI chama `plan()` (dry-run)? | `grep -n "\.plan(" ui/app.py` | **não** — só `cli.py:1156` |
| M13 | Tamanho das famílias de ferramentas | `Counter(nome.split('_')[0])` | `network_*` **17** (19% do catálogo), `read_*` 5, `click_*` 4, `study_*` 4, `system_*` 4, `smart_home_*` 4 |

---

## 1. Como o Modo Agente está implementado hoje

### 1.1 O fluxo de execução, ponta a ponta

```
  GATILHO                      ROTEAMENTO                  LOOP                      MUNDO
  ───────                      ──────────                  ────                      ─────

  chip "Agente"      ┐
  /agente <obj>      ├──► prompt_bar.submit()  ──►  start_agent_execution()
  @agent <obj>       │    (prompt_bar.py:624)        (ui/app.py:2813)
  Ctrl+K → Alternar  ┘                                     │
  CLI: zorin-copilot-cli agent "obj"                        │  thread daemon
       (cli.py:1057 → cmd_agent)                            ▼
                                                   AgentRouter.route()  ── determinístico ──┐
                                                   (agent_router.py:645)                    │
                                                     ├─ simples  → Ollama (local)           │
                                                     ├─ complexo → Gemini/OpenAI (nuvem)    │
                                                     └─ FallbackPlanner([primário, reserva])│
                                                            │                               │
                                                   get_step_budget(obj) ◄───────────────────┘
                                                     ├─ simples  → 10 passos / 120 s
                                                     └─ complexo → 30 passos / 480 s
                                                            │
                                                            ▼
                                                   AgentLoop(planner, registry, max_steps,
                                                             max_seconds, memory)
                                                   (agent.py:245)
                                                            │
                        ┌───────────────────────────────────┴──────────────────────────────┐
                        │  while True:                                                     │
                        │   1. abort setado?              → STOP_ABORTED                    │
                        │   2. len(steps) >= max_steps?   → síntese + STOP_MAX_STEPS        │
                        │   3. tempo > max_seconds?       → STOP_TIMEOUT                    │
                        │   4. planner.decide(obj, schema, histórico)  ◄── 1 chamada LLM    │
                        │   5. sem tool_call?             → STOP_DONE  (sucesso)            │
                        │   6. registry.classify(tool,args) → Step.risk                    │
                        │   7. risk == CONFIRM? → on_approval(step)  (sem callback = RECUSA)│
                        │   8. registry.call(tool, args, contract)   ◄── executa no desktop │
                        │   9. on_step(step) → GLib.idle_add(agent_widget.add_step)         │
                        │  10. repetição/loop? → STOP_REPEATED / STOP_LOOP                  │
                        └───────────────────────────────────┬──────────────────────────────┘
                                                            │
                                                            ▼
                                        MemoryManager.log_action(obj, tool, args,
                                                    {agent_run_id, step, risk, approved}, …)
                                        (agent.py:601 — write-only, ver L13)
                                                            │
                                                            ▼
                                        _on_agent_finished()  →  widget.finish(result)
                                        (app.py:3187)            session.record_turn(...)
```

**Leitura do fluxo:** o núcleo é limpo. `AgentLoop` é uma máquina de estados pura (stdlib +
dataclasses), sem GTK/AT-SPI/provedor — todo acesso ao mundo entra por injeção
(`ToolRegistry`, `planner`). É isso que permite testar parada, aprovação e limites headless.
**Essa decisão está certa e não deve ser mexida.**

### 1.2 Os pontos de acionamento (5, medidos)

| # | Gatilho | Onde | Observação |
|---|---|---|---|
| A1 | Chip **"Agente"** (toggle) | `prompt_bar.py:395, 427` | Liga `ctx.agent_mode_active`; troca o placeholder do campo |
| A2 | Prefixos de texto `/agent`, `/agente`, `@agent`, `@agente` | `prompt_bar.py:625-629` | Ligam o modo **por mensagem**, sem mexer no chip |
| A3 | Chip **"Visão"** (sub-modo) | `prompt_bar.py:406, 633` | Nasce insensível; só habilita com A1 ativo. Prefixos `/visao`, `/vision`, `/agentevisao` |
| A4 | Paleta `Ctrl+K` → *Alternar Modo Agente* | `app.py:1675` (`app.toggle-agent`) | Mesma ação de A1, por teclado |
| A5 | CLI `zorin-copilot-cli agent "objetivo"` | `cli.py:1057` | Único gatilho com `--dry-run`, `--local-only`, `--cloud`, `--json`, `--yes` |

Há ainda um **acionamento interno**: a ferramenta `run_subagent` dispara um `AgentLoop` próprio
dentro de um passo do loop principal (`subagents.py:482`), com registro **restrito por allowlist**
(`_RESEARCHER` / `_REFACTORER` / `_A11Y_AUDITOR`, `subagents.py:594-688`). Nenhuma allowlist
autoriza `run_subagent` — é o que impede recursão. **Desenho correto; manter.**

### 1.3 O portão de risco — três níveis, por chamada

`RiskPolicy.classify()` (`risk.py:131`) devolve `SAFE` / `AUTO` / `CONFIRM`, e o `AgentLoop`
traduz assim (`agent.py:527-536`):

- `SAFE` → executa, nada pede.
- `CONFIRM` → `requires_approval=True`; **sem `on_approval`, o passo é recusado, não executado**
  (`agent.py:383-390`). Fail-closed correto.
- `AUTO` → `notify_after=True`; executa sem pergunta e o cartão mostra um ícone informativo
  (`agent_card.py:539`).

O teto de autonomia é aplicado em `classify()`: em `autonomy_level="low"`, todo `AUTO` rebaixa a
`CONFIRM` (`risk.py:136`). E `normalize_autonomy_level` cai em `"low"` para valor inválido
(`risk.py:41-48`) — fail-closed pelo lado que se vê. **Bom.**

### 1.4 O que já está resolvido (e não deve ser reaberto)

- Parada **nunca** por vontade do modelo: 9 motivos explícitos, todos com gatilho no código.
- Aprovação **por chamada**, não por estado do cliente (`live.py:2669` documenta o fail-open que
  existia antes).
- `--dry-run` substitui o registro por um clone com `dry_run=True`, e `call()` devolve
  `would_do` para ferramenta mutante (`agent_tools.py:553`) — o desktop nunca é tocado.
- Cancelamento: `finally` garante `_on_agent_finished` em qualquer saída + watchdog de tempo
  (`app.py:3012, 3016`) — fechado em 2026-09-27 com 9 mutações KILLED.
- Subagente recebe o registro **restrito no schema**, não uma checagem posterior.

---

## 2. Limitações medidas

Ordenadas por (gravidade × custo de conserto).

### L1 — O catálogo inteiro de 90 ferramentas vai em **todo** passo · `agent_tools.py:441`
O filtro `_filter_relevant_specs` tem nome de otimização, mas só filtra `mcp__*`; todo builtin
passa. Medido: **90 de 90** no schema, **86% do prompt** (M2/M4), **~4.866 tokens por passo**
(M3) — em 30 passos, ~146 mil tokens só de catálogo, reincluídos a cada chamada.
`active_tools` é repassado pelo loop (`agent.py:327-332`) mas só serve para casar *servidor MCP*.

### L2 — `autonomy_level="high"` é um campo que mente · `risk.py:203-211`
A promoção a `AUTO` mira `write_file`/`edit_file`. Medido: **nenhum dos dois existe no
`ToolRegistry`** (M8). A ferramenta real, `write_document`, continua `CONFIRM` mesmo dentro de
`~/Documentos` (M9). Consequência: **o nível "high" não tem efeito observável** no modo agente.
(Pendência 6.0 já registrada em `PROPOSTA_REVERSIBILIDADE.md`; esta análise confirma e localiza.)

### L3 — O watchdog mata o orçamento adaptativo — e deixa o cartão girando · `app.py:2213, 3187`
Medido: para objetivo **complexo** o loop recebe **480 s** (M6), mas o watchdog é armado com
`config.agent_max_seconds + 30` = **330 s** (M7). O orçamento de 480 s é **inalcançável**: a UI
força o fim 150 s antes.
Pior: `_fire_agent_watchdog` zera `_active_agent_widget` (`app.py:2243`) e o `_on_agent_finished`
que chega depois cai no ramo `widget is not active_widget` (`app.py:3203`) — que faz `return`
**sem chamar `widget.finish()`**. O cartão do agente fica **visível com o spinner girando para
sempre** — exatamente o sintoma que a `PROPOSTA_INTERRUPCAO_AGENTE.md` foi escrita para matar,
reintroduzido por outro caminho. O motivo de parada reportado também é `aborted`
("interrompida pelo usuário") quando na verdade foi o relógio.

### L4 — `success` é autodeclarado pelo modelo · `agent.py:549`
`success = (stop_reason == STOP_DONE)`, e `STOP_DONE` vem de o modelo emitir `final_answer` ou
chamar `done`. Não há **nenhuma** verificação de pós-condição no caminho padrão: `contract` só é
preenchido quando o planejador devolve `preconditions`/`postconditions` (que a instrução do
sistema nunca pede), e o `ValidatingToolRegistry` só é montado quando `vision=True`
(`app.py:2926`). A regra 8 do prompt pede honestidade ao modelo — mas a **máquina aceita a
palavra dele**. Um agente que diz "filtrei por menor preço" sem ter filtrado encerra com
`success=True` e toast verde.

### L5 — O campo de passos das Preferências não controla nada · `agent_router.py:153-174`
Medido (M6): com `agent_max_steps=15` e `agent_max_seconds=300` configurados, o usuário recebe
**10 passos / 120 s** (simples) ou **30 passos / 480 s** (complexo). O valor configurado é
`min()`ado para baixo nos simples e usado só como piso nos complexos. O rótulo na tela promete um
controle que o código ignora nos dois sentidos.

### L6 — O histórico cresce linearmente e é reenviado inteiro · `agent_router.py:325-349`
Medido (M5): 44.069 chars no passo 29 (~11 mil tokens) — e o custo acumulado é quadrático no
número de passos, porque cada passo reenvia todos os anteriores. Os 4 passos mais recentes
recebem 3.500 chars de observação; os antigos, 600. Não há sumarização dos passos antigos.

### L7 — Aborto cooperativo: o Esc não interrompe uma chamada de LLM em curso · `agent.py:323`
O flag é checado só no topo do `while`. Um `planner.decide()` que esteja esperando HTTP de vários
minutos não é interrompido. Já registrado como risco residual aceito em
`PROPOSTA_INTERRUPCAO_AGENTE.md §2.5` — permanece aberto, e é o que torna L3 mais provável.

### L8 — Os dois caminhos de execução divergem, e um deles ignora o risco
O caminho **A** (chat / `IntentEngine` → `shell/executor.py`) tem **zero referências** a
`RiskPolicy` (M11); quem confirma lá é o booleano `DesktopAction.requires_confirmation`, cravado
em `providers.py`. O caminho **B** (agente / Live) consulta a política. Um nome de ação do
caminho A (`write_file`) não existe no B, e vice-versa (`write_document`). Regra prática: ao
mexer em risco, **perguntar em qual caminho a ferramenta vive**.

### L9 — A casa inteligente muda em silêncio · `risk.py:148-240`
Medido (M10): `smart_home_control` e `smart_home_automate` classificam **`SAFE`**. O agente pode
apagar uma luz, trancar uma porta ou disparar uma cena sem perguntar e sem avisar depois.
`smart_home_status` também é `SAFE` — esse está certo.

### L10 — A GUI não mostra o plano antes de agir · `app.py` (nenhuma chamada a `plan()`)
Medido (M12): `loop.plan()` existe e o CLI o expõe em `--dry-run` (`cli.py:1156`), mas a GUI
**nunca** o chama. O usuário do desktop não tem como ver o que o agente pretende fazer antes de
ele começar a fazer — o único pré-voo é a linha de comando.

### L11 — Roteamento por léxico tem falso-positivo e falso-negativo previsíveis
`classify_objective` (`agent_router.py:127`) decide local × nuvem por substring de verbo e
conector. Duas consequências medidas por leitura: um objetivo com a palavra "relatório" escala
para a nuvem mesmo sendo "abrir o relatório.pdf"; e uma tarefa local composta sem conector
explícito (ex.: "clica em Iniciar, escreve notas, salva") não é detectada como complexa se usar
≤2 verbos do léxico. O desenho é deliberado (evita gastar uma chamada de LLM para classificar) —
a limitação é a **calibração**, não a ideia.

### L12 — Uma ferramenta por passo, sempre — inclusive leituras independentes
O loop executa exatamente um `registry.call` por iteração. Duas leituras que não dependem uma da
outra (ex.: `git_status` + `list_open_windows`) custam dois passos e duas chamadas de LLM. Não há
noção de passo composto nem de paralelismo de ferramentas `SAFE` de leitura.

### L13 — A auditoria é escrita e nunca lida · `agent.py:601`
`_audit` grava cada passo em `action_logs` com `agent_run_id`, `risk` e `approved`. Medido: o
`agent_run_id` **não tem nenhum leitor** em `src/` (só um consumidor acidental em
`browser_runtime.py:132`, que o usa como fallback de assinatura de estado). O único caminho de
leitura para o usuário é a exportação Markdown da conversa. A consolidação ainda **poda** esses
logs por idade (`consolidator.py:109`).

### L14 — Aprovação sem escopo nem validade · `app.py:2979-2989`, `agent_card.py:452`
"Aprovar Todas" liga `auto_approve_rest` no widget (`agent_card.py:220, 453, 714`) e vale para
**qualquer** ação subsequente daquela execução — inclusive uma categoria diferente da que o
usuário viu. Se o usuário não responder, `approval_event.wait(timeout=180.0)` estoura e o passo
vira recusa (`approved=False`), encerrando o run por `rejected` sem explicar que foi timeout.

---

## 3. Propostas

Quatro ondas. Cada item: **o que muda · onde · impacto esperado · risco · como validar.**
Impacto declarado em unidade medível — a régua é: se não dá para medir depois, não é melhoria.

### Onda 1 — Parar de mentir (barato, alto valor)

#### P1 · Religar ou remover a promoção `AUTO` (L2) — *decisão já pendente: item 6.0*
- **O que muda:** `risk.py:203` passa a mirar os nomes **reais** do modo agente —
  `write_document` e `organize_directory` — com a mesma condição (zona de confiança + `high`),
  **ou** a regra e o campo `autonomy_level` saem da UI. Não as duas coisas.
- **Impacto:** `autonomy_level="high"` passa a ter efeito observável (ou deixa de existir como
  promessa). Elimina um campo que mente.
- **Risco:** escrita automática em `~/Documentos` é irreversível em binário/arquivo grande —
  `snapshot_file` devolve `None` nesses casos (`REF-arquitetura.md`). Se P1 for "religar", ela
  **depende** da regra `AUTO ⊆ REVERSIBLE` por chamada de `PROPOSTA_REVERSIBILIDADE.md`.
- **Validar:** teste de `classify` para os dois nomes reais + mutação que troca o nome de volta
  para `write_file` (deve morrer).

#### P2 · Casa inteligente deixa de ser `SAFE` (L9)
- **O que muda:** `smart_home_control` e `smart_home_automate` passam a classificar por
  argumento: leitura (`action` de consulta) → `SAFE`; **mudança de estado** → `CONFIRM`;
  cena reversível → `AUTO` (executa e avisa). `smart_home_status` fica `SAFE`.
- **Impacto:** o agente para de mudar a casa em silêncio. Custo: uma pergunta antes de acender
  a luz — aceitável, e coerente com "a casa é o mundo real".
- **Risco:** fadiga de confirmação em uso por voz. Mitigar com P4 (escopo) e com `AUTO` para
  cena reversível.
- **Validar:** teste arg-aware (luz on vs. status) + mutação que devolve `SAFE` incondicional.
- **Decisão do Bruno (já registrada em memória):** `smart_home_automate` hoje é `SAFE`; o honesto
  é `CONFIRM`, mas isso muda a UX de "rodar cena". **Não aplicar sem a resposta dele.**

#### P3 · O aviso posterior passa a existir de verdade (§1.3 e L14)
- **O que muda:** hoje `notify_after` só rende um ícone no cartão (`agent_card.py:539`) — e o
  método `ToolRegistry.notify_after()` não tem chamador em `src/` (medido). Passa a produzir um
  **resumo pós-execução** no fim do run ("executei 3 ações sem perguntar: …") — toast + linha no
  `widget.finish()`.
- **Impacto:** `AUTO` deixa de ser silêncio; é o que torna P1 e P2 defensáveis.
- **Risco:** baixo (apresentação).
- **Validar:** teste do `AgentResult` → widget com ≥1 passo `notify_after` produz o resumo.

#### P4 · Aprovação com escopo e validade (L14)
- **O que muda:** "Aprovar Todas" passa a valer **por ferramenta** (ou por `RiskLevel`), não por
  execução inteira, e o escopo aparece no rótulo do botão ("Aprovar todas as escritas de
  arquivo"). O timeout de 180 s passa a ser **visível** ("aprovação expirou" em vez de recusa
  silenciosa).
- **Impacto:** menos fadiga **e** menos alcance por clique. Remove um `rejected` que mente.
- **Risco:** baixo. O `auto_approve_rest` é por widget; a mudança é de granularidade.
- **Validar:** teste com duas categorias de risco na mesma execução — aprovar-todas de escrita
  **não** aprova a próxima de rede.

#### P5 · Watchdog coerente com o orçamento, e o cartão sempre fecha (L3) — *defeito, não melhoria*
- **O que muda:** (a) `_arm_agent_watchdog` recebe o **orçamento real** (`max_seconds` devolvido
  por `get_step_budget`) em vez de `config.agent_max_seconds`; (b) o watchdog, ao forçar o fim,
  chama `widget.finish_error(...)` (ou `_on_agent_finished` deixa de fazer `return` mudo e
  finaliza o widget ao descartar); (c) o `stop_reason` do caminho do relógio é `timeout`, não
  `aborted`.
- **Impacto:** os 480 s prometidos a tarefas complexas voltam a ser alcançáveis, e o cartão do
  agente **nunca** fica girando. É a correção mais barata de maior efeito visível.
- **Risco:** baixo — é fechar um furo, não abrir comportamento.
- **Validar:** teste que arma o watchdog com orçamento curto e exige `widget.finish*` chamado e
  spinner parado. Mutação: devolver `agent_max_seconds` no `_arm_agent_watchdog` → deve morrer.

#### P6 · O campo de passos passa a controlar algo (L5)
- **O que muda:** `get_step_budget` deixa de `min()`ar para baixo nos simples e de tratar o
  configurado como piso nos complexos. Duas opções honestas: (i) o valor configurado é o **teto**
  e a heurística só reduz por *risco*, com o valor efetivo mostrado no cartão; (ii) a UI mostra
  "15 → efetivo 10 (tarefa simples)" para o campo não mentir.
- **Impacto:** o usuário volta a poder aumentar o orçamento. Hoje, subir de 15 para 20 não muda
  nada em tarefa simples.
- **Risco:** baixo. Exige decidir entre (i) e (ii) — ver §5.
- **Validar:** teste de `get_step_budget` com `base=20` em objetivo simples.

### Onda 2 — Cortar custo e latência

#### P7 · Filtro de ferramentas que filtra de verdade (L1) — *maior ganho isolado*
- **O que muda:** estender `_filter_relevant_specs` para os builtins. Já existem as pistas no
  próprio código: `active_tools` (as ferramentas já usadas no run ficam sempre), o léxico do
  objetivo, e — o mais forte — **o catálogo já é agrupável por prefixo**, e as famílias são
  grandes e quase sempre irrelevantes para o objetivo do momento. Medido por prefixo:
  `network_*` **17**, `read_*` 5, `click_*` 4, `study_*` 4, `system_*` 4, `smart_home_*` 4,
  `git_*` 3, `docker_*` 3, `open_*` 3. Só a família de rede é **19% do catálogo** e não serve a
  "abrir o Firefox". Regra segura: sempre incluir o núcleo (navegação/arquivo/UI/`done`/
  `run_subagent`), incluir um grupo só se o objetivo ou o histórico citar o domínio, e **nunca**
  esconder `run_command`/`write_document` de um objetivo de código.
- **Impacto medido no papel:** o catálogo é 86% do prompt (M4). Cortar de 90 para ~35 ferramentas
  tira ~2.000 tokens de **cada** chamada — em 30 passos, ~60 mil tokens. Também reduz a
  superfície de escolha errada do modelo (menos ferramentas = menos alucinação de nome).
- **Risco:** **o risco real é o oposto do ganho** — esconder a ferramenta que o modelo precisa
  produz um loop de tentativa errada. Mitigação obrigatória: (a) teste que garante que toda
  ferramenta citada em `SIMPLE_VERBS`/instrução do sistema tem gatilho de inclusão; (b) escape
  hatch explícito — se o modelo pedir ferramenta fora do schema, o `call()` já devolve "Ferramenta
  desconhecida: … Disponíveis: …"; transformar isso em **uma** reemissão com o catálogo completo
  antes de desistir; (c) `describe_for_prompt` mantém a lista textual para modelos locais.
- **Validar:** medir `len(build_planner_prompt(...))` antes/depois (número, não impressão) +
  teste de mutação: remover o gatilho de um grupo → o teste que exige a ferramenta presente falha.

#### P8 · Sumarização do histórico antigo (L6)
- **O que muda:** os passos além dos 4 mais recentes são substituídos por **um bloco consolidado**
  ("passos 1–12: leu 5 arquivos, encontrou X, falhou 1× em Y") gerado pelo próprio planejador ou
  por regra determinística. Hoje eles já são clipados para 600 chars cada, mas continuam sendo
  N linhas.
- **Impacto:** teto de crescimento passa de linear-por-passo para **constante após o passo k**.
  Medido: hoje 44 mil chars no passo 29 (M5); com bloco consolidado, o alvo é ~25 mil estáveis.
- **Risco:** perda de detalhe que o modelo ainda precisava. Mitigação: o bloco preserva
  **caminhos e erros**, não prosa; e os 4 recentes seguem intactos.
- **Validar:** `len(prompt)` no passo 29 ≤ 1,2 × `len(prompt)` no passo 10.

#### P9 · Interromper de verdade a chamada em voo (L7)
- **O que muda:** `AgentPlanner.decide` ganha um contrato de **deadline por chamada** (ex.: o
  restante do orçamento, com piso), e o provedor recebe `timeout` explícito em vez de herdar o
  default do cliente HTTP. No abort, a chamada corrente é abandonada no timeout curto em vez de
  esperar o timeout longo do provedor.
- **Impacto:** Esc passa a fazer efeito em **segundos**, não em minutos — e o watchdog (P5) vira
  rede de segurança, não o mecanismo principal.
- **Risco:** timeout curto demais aborta tarefa que ia responder. Mitigação: deadline =
  `min(60s, restante)` e um retry único.
- **Validar:** teste com planejador que dorme 5 s e deadline de 1 s → `run()` retorna ≤2 s.

#### P10 · Passo composto para leituras independentes (L12)
- **O que muda:** permitir que o planejador peça **um lote** de ferramentas `SAFE` de leitura
  (`{"tools": [...]}`), executadas em paralelo no `ThreadPoolExecutor` que o registro **já tem**
  (`agent_tools.py:335`, `max_workers=4`), e cujos resultados voltam como **um** passo com
  observações agregadas.
- **Impacto:** tarefas de reconhecimento (ler 5 arquivos, checar git + processos + janelas)
  caem de N passos para 1 — N−1 chamadas de LLM economizadas. Sem tocar no risco: **só `SAFE` de
  leitura entra no lote**; `CONFIRM` continua um por vez, com aprovação individual.
- **Risco:** ordem importa em algumas leituras (ex.: `focus_window` → `get_ui_tree`). Mitigação:
  lote só para ferramentas declaradas independentes por allowlist explícita; qualquer ferramenta
  com efeito de foco/estado fica fora.
- **Validar:** teste de que um lote de 3 leituras gera 1 passo e 3 observações; mutação que
  permite `write_document` entrar no lote → deve morrer.

### Onda 3 — Utilização

#### P11 · Pré-voo na GUI: ver o plano antes de executar (L10)
- **O que muda:** botão "Ver plano" no `AgentExecutionWidget` (ou um modo no chip) que chama o
  `loop.plan()` **já existente** e renderiza os passos com o risco de cada um, sem executar.
  Um segundo clique ("Executar") parte para o `run()`.
- **Impacto:** elimina a maior assimetria entre CLI e GUI. É o que transforma "confio e mando" em
  "reviso e mando" — e é o que torna P2/P4 aceitáveis na prática.
- **Risco:** baixo — `plan()` é `run(dry_run=True)` e o registro clone nunca toca o desktop
  (`agent_tools.py:306, 553`). Custo: uma chamada de LLM extra por revisão.
- **Validar:** teste de que o caminho de pré-voo **não** executa ferramenta mutante (o
  `would_do` aparece no lugar da observação real).

#### P12 · Custo visível no cartão
- **O que muda:** `AgentResult` ganha contagem de tokens estimada (chars/4 do prompt por passo,
  acumulada) e o cartão mostra "N passos · ~X mil tokens · Ys". O `header.refresh_token_usage()`
  já existe para o chat; o agente hoje não aparece em lugar nenhum.
- **Impacto:** torna o custo de L1/L6 **visível** — condição para o usuário decidir se quer
  orçamento maior. Sem isso, P7/P8 são otimização invisível.
- **Risco:** trivial. Estimativa é estimativa — rotular como "~".
- **Validar:** teste de que `to_dict()` carrega o campo e o widget o exibe.

#### P13 · Retomada persistente do run (L13 + utilização)
- **O que muda:** `run_id` já existe e é gravado. Passar a **ler** `action_logs` por
  `agent_run_id` para (a) reconstruir a timeline de um run passado no cartão exportado e
  (b) permitir retomar após fechar o app — o `continue_agent_execution` já aceita
  `initial_steps` (`app.py:3165`), só falta a origem persistida.
- **Impacto:** a auditoria deixa de ser write-only; o "Continuar (+10 passos)" sobrevive a um
  reinício. Fecha metade de L13.
- **Risco:** o consolidador **poda** `action_logs` por idade (`consolidator.py:109`) — um run
  antigo some. Mitigação: tabela própria de runs, ou excluir `agent_run_id` da poda.
- **Validar:** teste de round-trip: gravar run → ler por `run_id` → `initial_steps` não vazio.

### Onda 4 — Arquitetura (maior, decidir depois)

#### P14 · Fechar a divergência dos dois caminhos (L8)
- **O que muda:** uma de duas, não as duas: **(i)** `ActionExecutor` passa a consultar a
  `RiskPolicy` antes de executar (`requires_confirmation` vira a fonte de risco do caminho A
  também); ou **(ii)** o caminho A é aposentado e o chat passa a usar o `ToolRegistry`.
- **Impacto:** hoje existe uma porta pela qual uma ação de risco executa **sem** passar pela
  política — e nenhum teste acusa, porque cada caminho testa só a si mesmo.
- **Risco:** alto em ambos. (ii) é refatoração de `providers.py` + `ui/app.py:2716`.
- **Validar:** teste que exige que **todo** `ActionType` mutante do caminho A tenha classificação
  na política — falha hoje, por construção.

#### P15 · Fase 4 do `AGENT_MODE.md`: `live.py` consumir o `ToolRegistry`
- **O que muda:** `ai/live.py` já cria um `ToolRegistry` (`live.py:1329`) e já delega parte das
  chamadas (`live.py:3556`), mas mantém uma cadeia `if/elif` própria em `_dispatch_tool`
  (`live.py:2669`). Terminar a migração elimina a segunda implementação das mesmas ferramentas.
- **Impacto:** uma ferramenta corrigida em um caminho hoje pode continuar quebrada no outro — foi
  exatamente o que produziu o defeito da lixeira do Drive. Duplicação é dívida de **defeito**,
  não de estilo.
- **Risco:** médio; exige preservar os retornos em `{"success": …}` que a voz espera.
- **Validar:** teste que compara os nomes servidos pelos dois caminhos e exige que coincidam.

---

## 4. Ordem recomendada

| Ordem | Item | Por quê primeiro |
|---|---|---|
| 1º | **P5** (watchdog/cartão) | É defeito ativo, visível e barato. Sem ele, o resto é otimizar algo que trava. |
| 2º | **P7** (filtro de ferramentas) | Maior ganho isolado, medível em número, e habilita P8/P12. |
| 3º | **P11** (pré-voo na GUI) | Transforma a utilização; reusa código que já existe. |
| 4º | **P1 + P2 + P3 + P4** (risco honesto) | Juntos formam um bloco coerente: `AUTO` que existe, aviso que aparece, escopo que limita. |
| 5º | **P8 + P10 + P9** (custo/latência) | Depois de P7, para não otimizar duas vezes o mesmo prompt. |
| 6º | **P12 + P13 + P6** (visibilidade) | Dependem do que foi otimizado. |
| 7º | **P14 + P15** (arquitetura) | Decisão de escopo maior; não bloqueiam nada acima. |

**O que NÃO fazer:** não mexer em `AgentLoop` (máquina de estados pura, bem feita), não
reintroduzir estado de risco no cliente (o selo por chamada é correto), e não paralelizar nada
que não seja leitura `SAFE` (P10) — o desktop é um recurso com estado.

---

## 5. Decisões pendentes (para o Bruno)

| # | Decisão | Opções | Recomendação |
|---|---|---|---|
| D1 | `autonomy_level="high"` | religar (P1) / remover o campo da UI | **Religar** — mas só junto com a regra `AUTO ⊆ REVERSIBLE` de `PROPOSTA_REVERSIBILIDADE.md`. Sem ela, remover. |
| D2 | `smart_home_control` | `CONFIRM` sempre / arg-aware (`AUTO` para cena, `CONFIRM` para estado) | **Arg-aware.** Passar a perguntar antes de acender luz é mudança de UX — precisa do seu aval. |
| D3 | Filtro de ferramentas (P7) | filtro por grupo / catálogo completo + escape hatch | **Por grupo, com escape hatch.** Sem o escape hatch, o risco de esconder a ferramenta certa supera o ganho. |
| D4 | Campo de passos (P6) | valor = teto + efetivo visível / UI mostra o efetivo | **(i) teto + efetivo visível** — é o único jeito do campo não mentir. |
| D5 | Caminho A (P14) | política no executor / aposentar o caminho A | **Adiar.** Registrar o furo; decidir junto com a próxima mexida em `providers.py`. |

---

## 6. Plano de validação (régua do projeto)

Toda melhoria desta proposta exige o mesmo ritual já usado no repo — **evidência, não asserção**:

1. **Teste validado contra o defeito.** Para P5: reintroduzir `config.agent_max_seconds` no
   `_arm_agent_watchdog` → o teste novo deve **falhar** → restaurar → verde.
2. **Harness de mutação** em `tests/mutacao/`, com âncora única conferida antes por
   `tests/test_mutacao_anchors.py` (rodar o harness sem essa checagem já custou retrabalho).
   Alvos naturais: P1 (nome da ferramenta), P2 (arg-aware), P5 (fonte do orçamento),
   P7 (gatilho de grupo), P10 (allowlist do lote).
3. **Suíte completa só pelo runner seguro** — a máquina não tem swap e já foi reiniciada pelo OOM
   killer no meio de uma suíte:
   `~/.workbuddy-ai/skills/zorin-copilot-gui-change/scripts/run_tests_safe.py --python .venv/bin/python`
   Conferir a **linha de resumo** e a ausência de `pytest` órfão antes e depois.
   Baseline de referência: 159 módulos / 0 falhas (2026-09-27).
4. **Medição antes/depois para P7, P8 e P12** — número no documento, não impressão:
   `len(build_planner_prompt(...))` e contagem de tokens por passo.
5. **Validação ao vivo** para P5 e P11 (exigem o app aberto e o Bruno operando): objetivo
   complexo deve sobreviver além dos 330 s; o cartão deve fechar em vez de girar; "Ver plano"
   deve listar passos **sem** tocar no desktop.

---

## 7. O que ficou fora do escopo (registrado)

- **Grounding visual como padrão** (Fase 3 do `AGENT_MODE.md`): o caminho `vision=True` já
  existe com `ValidatingToolRegistry` e `VLMConditionChecker`; torná-lo padrão é o caminho
  natural para resolver L4 (sucesso verificado), mas é decisão de custo — cada passo ganha uma
  captura + uma chamada de VLM.
- **TrustManager aprendido** (item 7 da autonomia): depende de P1 estar fechado.
- **Voz ao vivo consumindo o modo agente**: `live.py` e `AgentLoop` continuam dois loops; unificar
  é P15 + trabalho próprio.
