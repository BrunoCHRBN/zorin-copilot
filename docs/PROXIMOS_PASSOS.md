# Próximos Passos — Modo Agente & Zorin Copilot

Este documento detalha o estado técnico atual após as melhorias de raciocínio, orçamento adaptativo e salvaguardas anti-alucinação no Modo Agente, definindo os **próximos passos prioritários** para a evolução do assistente no desktop Linux.

---

## 1. Conquistas Recentes Consolidadas

Nesta etapa, resolvemos o gargalo de interrupção prematura ("Limite de passos atingido") e riscos de alucinação do modelo durante tarefas analíticas e inspeção de código/repositórios:

1. **Orçamento Adaptativo de Passos e Tempo:**
   - Heurística determinística (`get_step_budget`) classificando tarefas simples (10 passos / 120s) e complexas/analíticas (30 passos / 480s).
   - Configuração exposta nas preferências do usuário via Libadwaita (`agent_max_steps`, `agent_adaptive_budget`).
2. **Continuação Contínua de Execução (`AgentExecutionWidget`):**
   - Botão discreto e nativo *"Continuar (+10 passos)"* quando o agente atinge o limite do orçamento sem erro irrecuperável.
   - Retomada instantânea preservando histórico acumulado e contexto do turno no SQLite.
3. **4 Salvaguardas Críticas Anti-Alucinação sob Timeout:**
   - **Payload Semântico de Timeout:** `ToolRegistry.call()` retorna `timeout: True` e `suggestion` acionável, informando que o recurso existe mas demorou, impedindo o modelo de inventar ou deletar/recriar arquivos.
   - **Regra 8 Rígida no Prompt:** Inclusão explícita no `_SYSTEM_INSTRUCTION` proibindo a presunção ou invenção de conteúdos quando ferramentas falharem ou expirarem.
   - **Janela de Observação Expandida:** Fim do afunilamento de 300 caracteres; os últimos 4 passos agora contam com até 3.500 caracteres, permitindo ler cabeçalhos, commits e logs completos.
   - **Ferramentas Nativas Locais de Alta Densidade (`git_log` e `git_status`):** Evita chamadas remotas lentas via MCP GitHub quando o repositório local está acessível, rodando em subprocessos isolados com timeouts estritos.
4. **Módulos Operacionais de Produtividade & Automação Concluídos:**
   - **Casa Inteligente & IoT (`core/home_assistant.py`):** Suporte completo a lâmpadas inteligentes (Avant Neo / Tuya), ar-condicionado e tomadas via Home Assistant, com ferramentas acionáveis no Gemini Live e no Agente.
   - **VS Code Workspace & Dev (`core/vscode.py`):** Gestão de projetos ativos, templates, leitura, escrita e patch atômico de código.
   - **Academic Hub (`core/academic_hub.py`):** Pesquisa e citação científica unificada (arXiv, OpenAlex, PubMed, Semantic Scholar).
   - **Gerenciador de Janelas (`core/window_manager.py`):** Manipulação de janelas no Hyprland/Wayland/X11.
   - **Seletor de Modelos em Tempo Real & Preview de Voz (`ui/widgets/model_selector.py` & `ai/voice_preview.py`).**
5. **Unificação Total do Gemini Live & ToolRegistry (Opção B - Concluída):**
   - **ToolRegistry como Fonte da Verdade:** `ToolRegistry` agora encapsula 53 ferramentas completas (sistema, janelas, terminal bash, git status/log/diff, contatos, memória, rag, web e casa inteligente).
   - **Despacho Dinâmico por Voz:** `GeminiLiveClient` instancia `ToolRegistry` e delega automaticamente qualquer ferramenta solicitada por voz.
   - **Exposição em Tempo Real de MCPs e Git:** Servidores MCP ativos (`mcp__*`) e ferramentas locais Git/terminal entram dinamicamente no `_live_tools_payload()`.
    - **Barreira de Risco Unificada (`RiskPolicy`):** Comandos perigosos (ex.: `run_command` com `rm`, `sudo`, `kill`) geram `confirmation_id` para aprovação verbal antes de executar.
6. **Grounding Visual por VLM Local & Cascata Inteligente (Opção C - Concluída):**
   - **Módulo `core/vlm_grounding.py`:** Integração nativa com Ollama local (`qwen2.5vl:7b`, `qwen2.5vl:3b`, `minicpm-v`, `llava`) e fallback em nuvem para `gemini-2.5-flash`.
   - **Auto-Discovery de Modelos:** Detecção automática de capacidades de visão via endpoint `/api/tags` do Ollama.
   - **Cascata Tripla de Baixa Latência:** AT-SPI (0ms) $\rightarrow$ Tesseract OCR Espacial (120ms) $\rightarrow$ VLM Grounding.
   - **Novas Ferramentas Registradas:** `locate_element_visual` e `click_visual_element` integradas ao `ToolRegistry`, ao Agente ReAct e ao cliente Gemini Live.
   - **Documentação Dedicada:** Criado `docs/VLM_GROUNDING_VISUAL.md`.
7. **Ferramentas Nativas de Busca de Código & Inspeção de Processos (Concluída ✅):**
   - **Módulo `core/system_inspect.py`:** Implementa `CodeSearchEngine` (com ripgrep nativo e fallback determinístico em Python) e `ProcessInspector` (com `ps` tabular e fallback direto via `/proc`).
   - **Ferramentas Nativas Integradas:** `search_code` e `system_processes` expostas no `ToolRegistry` (`RiskLevel.SAFE`) e no `GeminiLiveClient`.
   - **Design Nativo Libadwaita:** Ícones simbólicos (`system-search-symbolic`, `utilities-system-monitor-symbolic`) e sumários claros no `AgentExecutionWidget`.
   - **Regra 13 no Prompt:** Agente explicitamente orientado a usar `search_code` em vez de inspecionar arquivos sequencialmente.
   - **Documentação e Testes:** `docs/BUSCA_CODIGO_E_PROCESSOS.md` e `tests/test_system_inspect.py`.
8. **Automação AVA / SCORM Multi-Páginas & Decks Anki Nativos (Concluída ✅):**
   - **Módulo `core/study_capture.py`:** Suporte a captura sequencial multi-páginas de players SCORM/AVA com avanço por VLM Grounding (`click_visual_element`), nós AT-SPI e atalhos de teclado.
   - **Módulo `core/study_cards.py`:** Exportador nativo de decks Anki `.apkg` (coleção SQLite `collection.anki2` + ZIP) e `.tsv` com suporte a temas Libadwaita claro e escuro.
   - **Ferramentas Integradas:** `study_generate_deck`, `study_summarize`, `study_export_anki` e `capture_lesson` com `multi_page` expostas no `ToolRegistry` (`RiskLevel.SAFE`), GUI Adwaita, assistente de voz Gemini Live e CLI (`study export-anki`).
   - **Testes Completos:** 100% de cobertura nos módulos de estudo e Gemini Live.

---

## 2. Próximos Passos Priorizados

```
                                  MAPA DE PRIORIDADES
                                  
  ┌─────────────────────────────────┐        ┌──────────────────────────────────┐
  │  1. AUTOMAÇÃO AVA / ANKI        │   ──►  │  2. MCP REMOTO (SSE / HTTP)      │
  │  (CONCLUÍDO ✅ - SCORM + Anki)  │        │  (CONCLUÍDO ✅ - Cloud / Tokens) │
  └─────────────────────────────────┘        └──────────────────────────────────┘
                   │                                          │
                   ▼                                          ▼
  ┌─────────────────────────────────┐        ┌──────────────────────────────────┐
  │  3. MCP PROMPTS NA PALETA       │   ──►  │  4. SUBAGENTES ESPECIALIZADOS    │
  │  (CONCLUÍDO ✅ - Ctrl+K)        │        │  (PRÓXIMO PASSO - Pesquisa/Code) │
  └─────────────────────────────────┘        └──────────────────────────────────┘
```

### Passo 1: Automação de Estudo AVA / SCORM & Exportação Anki (Concluído ✅)
- Captura sequencial multi-páginas para players de aula EAD com avanço automático por VLM Grounding e AT-SPI.
- Exportação direta de coleções Anki `.apkg` prontas para importação no desktop ou mobile sem dependências externas.
- Comandos CLI, ferramentas no Modo Agente e suporte de áudio no Gemini Live totalmente operacionais.

### Passo 2: Suporte a Servidores MCP Remotos (SSE / HTTP Streaming) (Concluído ✅)
- **Implementação Completa:**
  1. Suporte dual a transporte local (`stdio`) e remoto (`sse` / Server-Sent Events sobre HTTP) no cliente unificado `MCPClient` (`BaseMCPClient`, `MCPStdioClient`, `MCPSSEClient`).
  2. Leitura assíncrona streaming de eventos SSE em background com descoberta automática de endpoint de POST via evento `endpoint` e recepção de JSON-RPC via `message`.
  3. Autenticação por tokens (Bearer / API keys) com interpolação de variáveis de ambiente (`${VAR}`) nos headers HTTP sem expor segredos.
  4. Suporte a servidores remotos em `mcp_servers.json` gerenciados pelo `MCPManager`.
  5. UI de Preferências Adwaita atualizada com indicador `[SSE Remoto]` e exibição limpa de URLs.
  6. Suíte automatizada com 6 novos testes em `tests/test_mcp_sse.py` utilizando `ThreadingHTTPServer` nativo.

### Passo 3: MCP Prompts na Paleta de Comandos (`Ctrl+K`) (Concluído ✅)
- **Problema:** Usuários precisam digitar prompts repetitivos para tarefas comuns fornecidas por servidores MCP.
- **Implementação Completa:**
  1. Primitiva `prompts/list` e `prompts/get` suportada de ponta a ponta: `MCPPrompt`/`MCPPromptArgument` em `mcp/protocol.py`, `list_prompts`/`get_prompt` em `BaseMCPClient`, agregação (`refresh_prompts_cache`, `get_all_prompts`, `get_prompt`, `render_prompt`) em `MCPManager`.
  2. Notificação `notifications/prompts/list_changed` tratada com callback próprio (`on_prompts_changed`), mantendo o cache de prompts separado do de ferramentas.
  3. Prompts descobertos entram na Paleta de Comandos como `mcp-prompt::mcp__servidor__nome`. Escolher um prompt **não executa nada no sistema** — ele preenche a caixa de entrada com o texto materializado.
  4. Diálogo Libadwaita (`Adw.Dialog` + `Adw.EntryRow`) coleta os argumentos declarados quando existem, com marcação de obrigatórios; prompts sem parâmetros preenchem direto.
  5. `prompts/get` roda fora da thread da UI e o texto entra por `GLib.idle_add`; erros do servidor viram texto legível, nunca exceção.
  6. Lógica pura isolada em `mcp/prompts.py` (nome de comando, palavras-chave de busca, validação e limpeza de argumentos), sem GTK — testável sem display.
  7. Suíte com 60 testes em `tests/test_mcp_prompts.py`, cobrindo protocolo, cliente, gerenciador, helper puro e integração com a paleta.
- **Evidência visual** (render real, GTK4 → Gsk → PNG): `docs/paleta-mcp-prompt.png` (prompt como primeiro resultado da busca), `docs/paleta-mcp-lista.png` (painel aberto) e `docs/dialogo-argumentos-prompt.png` (diálogo de argumentos com marcação de obrigatório). A verificação encontrou um defeito real, descrito abaixo.

> **Defeito encontrado ao renderizar (corrigido):** `Adw.ActionRow.title`/`subtitle` são
> interpretados como **markup** pelo GTK. Um `&`, `<` ou `>` cru faz o parsing falhar: o GTK
> emite um warning, `get_title()` continua devolvendo o texto original e o **rótulo fica
> vazio** — a linha aparece em branco, e nenhum teste que olhe só para `get_title()` percebe.
> O comando `app.hardware-health` ("Saúde de Hardware & Sensores Térmicos") já sofria disso
> antes deste passo; com prompts vindos de servidores externos o defeito passaria a ser
> alcançável por dado de terceiro. Corrigido em `CommandPalette._make_row` com
> `GLib.markup_escape_text`, com guarda em `test_command_palette.py`.
>
> O mesmo padrão existe em outros pontos que passam texto dinâmico para `title=`/`subtitle=`
> (`preferences.py`, `infra_view.py`, `chat_stream.py`). **Não** é uma troca mecânica: em
> `chat_stream.py` parte das chamadas passa markup **de propósito** (`<b>…</b>` com
> `html.escape` no conteúdo), e escapar ali quebraria a formatação.

### Passo 4: Subagentes Especializados (Concluído ✅)
- **Problema:** Tarefas longas (pesquisa profunda, refatoração de código, auditoria de acessibilidade) exigem raciocínio em etapas com contexto próprio, e hoje o agente principal carrega tudo.
- **Implementação Completa:**
  1. **Contrato de subagente** (`ai/subagents.py`, módulo **puro** — sem GTK, sem rede, sem provedor). A decisão de projeto foi **não escrever um laço novo**: um subagente é um `AgentLoop` comum com três coisas trocadas. Assim todos os freios já medidos e testados do laço (teto de passos, teto de tempo, detecção de repetição e de laço, aborto) valem para o subagente **de graça**, em vez de serem reimplementados — e reimplementados pior.
  2. **Contexto isolado:** `session_context=[]` na construção do laço interno. É o ponto do recurso — se o subagente recebesse o histórico da conversa, não haveria alívio de contexto e ele repetiria o que o pai já fez.
  3. **Ferramentas restritas no *schema*, não validadas a posteriori:** `ToolRegistry.restricted(names)` devolve um clone cujo `_specs` contém **só** as ferramentas do allowlist. O subagente literalmente não vê as outras — não há caminho de código em que ele possa chamar uma ferramenta proibida, porque ela não está no catálogo que ele recebe. `resolve_allow` sempre acrescenta `FINISH_TOOLS` (`done`/`finish`), senão o subagente só conseguiria parar esgotando o orçamento.
  4. **Encadeamento com estado explícito:** `SubagentPipeline` roda os estágios em série e passa a saída de um como entrada do próximo (`chain_input`). O estado é materializado em `PipelineState`/`SubagentRun`/`PipelineResult`, todos com `to_dict()` — o relatório vira JSON para o chat e para o log. Como no `AgentLoop`, toda execução termina com **exatamente um** motivo de parada nomeado (`done`, `no_stages`, `no_planner`, `stage_failed`, `aborted`, `timeout`).
  5. **Progresso no chat sem bloquear a janela:** o pipeline é acionado por um passo normal do agente (a ferramenta `run_subagent`), que roda no thread do agente. Cada transição de estágio chega à UI por `GLib.idle_add` e vira uma linha em `AgentExecutionWidget.add_subagent_stage`. As linhas entram **sem número de etapa** e recuadas com borda à esquerda: elas são trabalho *dentro* do passo `run_subagent`, e numerá-las faria a contagem de etapas do agente principal mentir. O gancho `registry.subagent_runner` reusa o mesmo contrato do `board_opener` já existente.
  6. **Recursão bloqueada por construção:** nenhum allowlist do catálogo contém `run_subagent`, então um subagente não consegue abrir outro. Há teste afirmando isso para todos os especialistas.
  7. **Catálogo de três especialistas reais** (`researcher`, `refactorer`, `a11y_auditor`), com allowlists e orçamentos conferidos **contra o registro de verdade** antes de escrever qualquer teste: todo padrão casa com pelo menos uma ferramenta existente e todo especialista consegue terminar.
  8. Suíte com **59 testes puros** em `tests/test_subagents.py` (sem display, sem rede, sem modelo) e **9 testes de widget** em `tests/test_agent_gui.py`, cobrindo allowlist, isolamento de registro, contexto isolado, orçamentos, encadeamento, motivos de parada, notificações de progresso, repasse de aprovação e a ferramenta `run_subagent`.
- **Evidência visual** (render real, GTK4 → Gsk → PNG): `docs/agente-subagente.png`, com um pipeline de três estágios mostrando as três gravidades lado a lado — concluído, falhou e interrompido.

> **Defeito encontrado ao renderizar (corrigido):** o ícone de um estágio concluído era pintado de
> **verde** sempre que o estágio terminava, porque a cor saía direto de `SubagentRun.ok`. Mas
> `ok` é só `stop_reason == "done"` — um booleano que **joga fora o motivo**. Um estágio parado por
> `max_steps` e um que quebrou com `no_provider` chegam os dois como `ok=False`, e os dois saíam
> verdes ao lado do texto "interrompido". O ícone contradizia o rótulo, e nenhuma asserção de texto
> pegaria: o texto sempre esteve certo. Corrigido com `stage_severity`, que usa o `ok` para o caso
> feliz e o `stop_reason` para separar **"acabou o orçamento"** (trabalho parcial → âmbar,
> "interrompido") de **"quebrou"** (vermelho, "falhou"). Motivo desconhecido cai em vermelho de
> propósito: um motivo novo deve aparecer, não passar batido.

> **Defeito de orçamento encontrado pelos testes (corrigido):** `max_total_seconds` aplicava um piso
> silencioso de 1 segundo (`max(1.0, …)`), então um teto de 0,01 s virava 1 s e o primeiro estágio
> completava em vez de o pipeline desistir. O piso existia para nunca entregar orçamento zero ao
> `AgentLoop`; foi trocado por um epsilon (`_MIN_STAGE_SECONDS = 0.001`) que cumpre a mesma função
> sem inventar tempo que o chamador não deu.

> **Nota de honestidade sobre um teste:** o caso "estágio 1 conclui, orçamento acaba antes do estágio
> 2" **não tem teste determinístico** — o orçamento do estágio é o que sobrou do total, e o
> `AgentLoop` confere o orçamento no início do passo, então um estágio que conclui sempre termina
> dentro do orçamento. O teste foi reescrito para afirmar a regra que **é** determinística (com o
> total esgotado, nenhum estágio começa) e o raciocínio ficou registrado no docstring, em vez de ser
> disfarçado com um teste que passa por acidente.

---

## 3. Diretrizes de Qualidade e Design (Anti-Slop)

Todas as implementações subsequentes devem obrigatoriamente manter:
- **Design Nativo GNOME / Libadwaita:** Seguir estritamente as Human Interface Guidelines (HIG). Sem gradientes roxos/indigo de IA, sem landing pages embutidas em diálogos e sem emojis em botões.
- **Tipografia e Ícones Simbólicos:** Uso exclusivo de ícones simbólicos Adwaita e números tabulares (`tabular-nums`) para cronômetros e métricas.
- **Execução Headless & Não-Bloqueante:** O núcleo do agente deve permanecer testável sem dependência direta de servidor gráfico ou display Wayland/X11 ativo.
