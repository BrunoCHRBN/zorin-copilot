# Modo Agente (uso autônomo supervisionado do desktop)

Este documento é a referência de design do **modo agente** do Zorin Copilot: a capacidade
de receber um objetivo em linguagem natural ("abre o Firefox, pesquisa X e salva o PDF em
`~/Documentos`") e executá-lo como uma sequência de ações reais no desktop, com
**confirmação, cerca digital e desfazer**.

> **Status:** Fase 1 (núcleo) — implementada. Fases 2–4 planejadas abaixo.

---

## 1. Por que existe: o que já tínhamos

O `GeminiLiveClient` (`ai/live.py`) já contém um loop de tool-calling com **28 ferramentas**
(`get_ui_tree`, `locate_element`, `click_element`, `type_element`, `mouse_click`,
`keyboard_hotkey`, `launch_app`, `write_document`, `organize_directory`, `capture_screen`,
`screen_fence_control`, `confirm_action`, ...). Três pilares de segurança já existem e são
reaproveitados integralmente:

| Pilar | Módulo | Papel |
|---|---|---|
| Política de risco | `shell/risk.py` | `RiskPolicy.classify(nome, args) -> (SAFE\|CONFIRM, desc)`; sensível a argumentos (`end_session` só é arriscado em `mode=quit`) e a atalhos destrutivos (`alt+f4`, `ctrl+q`) |
| Cerca digital | `core/fence.py` | `ScreenFenceManager.is_coordinate_allowed(x, y)`; zonas vermelhas; parada de emergência |
| Desfazer | `shell/undo.py` | `UndoStack` com snapshots antes de escrita/organização de arquivos |
| Auditoria | `core/memory.py` | `log_action(...)` grava cada ação na tabela `action_logs` |

**O que faltava:** aquele loop só existe dentro do WebSocket de voz do Gemini. Não há como
acioná-lo por texto, sem microfone, sem chave de API paga, nem testá-lo headless.

## 2. Objetivo da Fase 1

Extrair o loop do transporte de voz para um **núcleo puro e testável**, acionado por texto,
roteando o raciocínio para o **modelo local (Ollama/Qwen)** quando possível e para a nuvem
apenas quando necessário.

Requisitos não negociáveis:

1. **Nada de GTK/AT-SPI no núcleo** — `ai/agent.py` importa apenas stdlib + tipagem; todo
   acesso a desktop entra por injeção (registry de ferramentas). Isso mantém os testes
   rodando headless, sem `xvfb` e sem `dbus-run-session`.
2. **Dry-run obrigatório como padrão de desenvolvimento** — `plan()` nunca executa.
3. **Toda ação de risco passa por aprovação** — callback `on_approval`; sem callback, o agente
   recusa em vez de executar.
4. **Parada sempre por condição explícita** — nunca "porque o modelo quis".

## 3. Arquitetura

```
                 CLI: zorin-copilot-cli agent "objetivo" [--dry-run] [--local-only]
                                  │
                                  ▼
                      ai/agent_router.py        (roteamento determinístico)
                        ├─ local  (OllamaProvider / Qwen)
                        └─ cloud  (GeminiProvider)  ── fallback / escalonamento
                                  │
                                  ▼
                        ai/agent.py :: AgentLoop
                        ├─ plan()            → lista de passos (dry-run)
                        ├─ run(on_step, on_approval)
                        └─ abort()           → threading.Event
                                  │
                                  ▼
                      ai/agent_tools.py :: ToolRegistry
                        ├─ RiskPolicy.classify()     (shell/risk.py)
                        ├─ ScreenFenceManager        (core/fence.py)
                        ├─ UndoStack                 (shell/undo.py)
                        └─ DesktopInspector / FileManager / AppManager / InputDriver
                                  │
                                  ▼
                      MemoryManager.log_action()     (auditoria por passo)
```

### 3.1 `ai/agent.py` — `AgentLoop`

Máquina de estados pura. Construtor:

```python
AgentLoop(router, registry, *, max_steps=12, max_seconds=180, max_repeats=2, policy=None)
```

| Membro | Comportamento |
|---|---|
| `plan(objective) -> list[Step]` | Gera o plano via roteador e o devolve **sem executar nada**. Marca `risk` e `requires_approval` por passo. |
| `run(objective, on_step=None, on_approval=None) -> AgentResult` | Executa passo a passo; `on_step(step)` observa, `on_approval(step) -> bool` aprova ações `CONFIRM` (recusa se ausente). |
| `abort()` | Sinaliza parada; o loop encerra no próximo passo. |

**Condições de parada** (cada uma registrada em `AgentResult.stop_reason`):

| `stop_reason` | Gatilho |
|---|---|
| `done` | O modelo chamou a ferramenta `done` (ou `finish`) |
| `max_steps` | `len(steps) >= max_steps` |
| `timeout` | Tempo total > `max_seconds` |
| `repeated_failure` | **Mesma ferramenta + mesmos argumentos** falhando pela 2ª vez consecutiva |
| `aborted` | `abort()` chamado |
| `rejected` | Política de risco/pedido de aprovação negado |
| `error` | Falha irrecuperável do provedor |
| `no_provider` | Nenhum provedor disponível para o modo pedido |

`AgentResult` carrega `objective`, `steps` (cada um com `tool`, `args`, `observation`, `ok`,
`risk`, `elapsed`), `success`, `stop_reason`, `elapsed`, `error`.

### 3.2 `ai/agent_tools.py` — `ToolRegistry`

Registro nome → callable, com metadados (`description`, `parameters` em JSON-Schema-ish) para
montar o prompt do provedor. `call(name, args) -> dict` sempre devolve um dicionário
serializável (nunca levanta), para que o loop possa registrar a observação e continuar.

Ferramentas do primeiro corte (Fase 1):

| Ferramenta | Efeito | Risco |
|---|---|---|
| `get_ui_tree` | Árvore de acessibilidade resumida do app em foco (ou de `app`) | safe |
| `find_element` | Localiza elemento por nome/papel → uid + bounds | safe |
| `click_element` | Clica pelo uid do elemento (prefere AT-SPI a coordenada crua) | safe |
| `type_text` | Digita no elemento em foco | safe |
| `mouse_click` | Clique por coordenada absoluta/relativa — **passa pela cerca** | safe |
| `press_hotkey` | Atalho de teclado (atalhos destrutivos → CONFIRM) | confirm* |
| `launch_app` | Abre aplicativo (com espera de foco e fallback `$PATH`) | safe |
| `list_directory` | Lista arquivos | safe |
| `read_file` | Leitura de texto (com limite de bytes) | safe |
| `write_document` | Escreve arquivo — snapshot no `UndoStack` | **confirm** |
| `organize_directory` | Organiza pasta — snapshot no `UndoStack` | **confirm** |
| `capture_screen` | Screenshot + (futuro) legenda por MiniCPM-V | safe |
| `undo_last` | Desfaz a última ação reversível | safe |
| `done` / `finish` | Encerra o loop com a resposta final | safe |

\* `press_hotkey` herda `DESTRUCTIVE_HOTKEYS` de `shell/risk.py`; `write_document` e
`organize_directory` herdam `HIGH_RISK_TOOLS`. **Nenhuma lista de risco é duplicada** — o
registry consulta `RiskPolicy`.

### 3.3 `ai/agent_router.py` — roteamento local → nuvem

Determinístico (sem custo de classificação por LLM):

1. `--local-only` → só Ollama; `--cloud` → só Gemini.
2. Objetivo interpretado como **simples** (1–2 passos, verbos do léxico local: abrir, clicar,
   digitar, listar, capturar, fechar janela) → **local**.
3. Objetivo **longo/composto** (conectores "e depois", "em seguida", mais de 2 verbos, ou
   pedido de pesquisa/síntese) → **nuvem**, com volta ao local se a nuvem falhar.
4. Modo `hybrid` do `CopilotConfig` → começa local, escala para nuvem após 2 falhas.

`AgentProvider` é uma interface mínima: `plan(objective, tools, history) -> list[ToolCall]`.
`OllamaProvider` e `GeminiProvider` ganham adaptadores finos; o núcleo não conhece nenhum dos dois.

### 3.4 CLI

```bash
# Ver o plano, sem tocar no desktop
zorin-copilot-cli agent "abrir o Firefox e pesquisar contabilidade gerencial" --dry-run

# Executar de verdade (ações de risco pedem confirmação no terminal)
zorin-copilot-cli agent "organizar ~/Downloads por tipo" --max-steps 15

# Forçar raciocínio local
zorin-copilot-cli agent "clicar em Iniciar" --local-only --dry-run
```

Flags: `--dry-run`, `--max-steps N`, `--max-seconds N`, `--local-only`, `--cloud`,
`--json` (saída legível por máquina), `--verbose`.

## 4. Segurança

| Camada | Mecanismo | Onde |
|---|---|---|
| Aprovação | `RiskPolicy.classify` → callback `on_approval`; sem callback ⇒ **recusa** | `agent.py` + `shell/risk.py` |
| Cerca espacial | Toda coordenada passa por `is_coordinate_allowed`; `mouse_click` do `VirtualInputDriver` já valida internamente | `core/fence.py` |
| Desfazer | `UndoStack` snapshot antes de escrita/organização; ferramenta `undo_last` | `shell/undo.py` |
| Limites | `max_steps`, `max_seconds`, `max_repeats` | `agent.py` |
| Auditoria | `MemoryManager.log_action` por passo, com `agent_run_id` em `params` | `core/memory.py` |
| Apps bloqueados | `BLOCKED_APPS` / `is_blocked_app` continuam valendo para `launch_app` | `shell/risk.py` |

**Dry-run não é decorativo:** em `--dry-run` o `ToolRegistry` é substituído por um registry
falso que devolve observações sintéticas. Isso garante que um bug de planejamento nunca
alcance o desktop.

## 5. Testes

`tests/test_agent_tools.py`, `tests/test_agent_loop.py`, `tests/test_agent_router.py` — todos
headless, sem GTK: usam registry e provedores falsos. Cobertura mínima:

- ferramenta `done` encerra com `stop_reason == "done"`;
- `max_steps` e `max_seconds` produzem as respectivas paradas;
- ação CONFIRM sem `on_approval` ⇒ `stop_reason == "rejected"` e **nenhuma execução**;
- repetição da mesma ação falha 2× ⇒ `repeated_failure`;
- `abort()` durante o passo ⇒ `aborted`;
- coordenada fora da cerca ⇒ observação de bloqueio, sem exceção;
- dry-run não executa nada;
- roteamento local/cloud/`--local-only`.

A suíte completa continua rodando sob `xvfb-run -a dbus-run-session` (o AT-SPI2 ainda é
exigido pelos testes de UI existentes).

## 6. Fases seguintes

| Fase | Entrega |
|---|---|
| **2** | UI do modo agente (painel de plano, aprovação por passo, barra de parada) + reaproveitar o registry no `live.py` |
| **3** | Grounding visual: MiniCPM-V (Ollama) para localizar elementos quando a árvore AT-SPI é insuficiente (canvas remoto, SCORM do AVA) |
| **4** | Refatorar `ai/live.py` para consumir o mesmo `ToolRegistry`, eliminando a duplicação de 28 ferramentas |

## 7. Relação com o estudo (contexto de uso)

O modo agente é o que viabiliza a **Fase A** do plano de estudos: capturar material do AVA da
SENAC EAD. O AVA entrega um *shell* de SPA em iframe/SCORM que extratores de HTML não leem;
o `get_ui_tree` via AT-SPI2 lê o que está **renderizado**, que é exatamente o caso de uso.
