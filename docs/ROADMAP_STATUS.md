# Zorin Copilot — Status de Implementação e Roadmap

Este documento consolida o estado atual do desenvolvimento do **Zorin Copilot**, detalhando todas as funcionalidades, módulos e refinamentos implementados até o momento, além do mapeamento das próximas fases do roadmap técnico.

---

## 1. Visão Geral do Sistema e Arquitetura

O Zorin Copilot é um assistente de inteligência artificial nativo para Linux (otimizado para Zorin OS e EndeavourOS / GNOME 46), projetado com privacidade em primeiro lugar, suporte a execução local e híbrida, e integração profunda com o desktop via acessibilidade (AT-SPI2), injeção virtual de eventos e extensibilidade padronizada via **Model Context Protocol (MCP)**.

### Pilares Fundamentais:
1. **Segurança e Cerca Espacial (`core/fence.py` & `shell/risk.py`):** Nenhuma ação perigosa é executada sem confirmação humana (`RiskLevel.CONFIRM`). Áreas de tela e cliques fora dos limites configurados ou durante parada de emergência (*Kill Switch*) são bloqueados a nível de kernel/driver.
2. **Reversibilidade (`shell/undo.py`):** Mutações no sistema de arquivos e comandos reversíveis geram snapshots automáticos em pilha de desfazer (`Ctrl+Z`).
3. **Multi-Provedores Híbrido (`ai/providers.py` & `ai/agent_router.py`):** Suporte transparente a Google Gemini, Ollama (Qwen, Llama, MiniCPM-V), OpenAI e instâncias locais compatíveis.
4. **Nativo e Livre de "AI Slop":** Interface construída com GTK4 e Libadwaita, seguindo rigorosamente as diretrizes GNOME HIG — sem gradientes artificiais, sem landing pages embutidas e com tipografia e ícones simbólicos nativos.

---

## 2. O que foi Implementado até Agora

### 2.1 Interface & Experiência do Usuário (UI/UX)
- **Janela Principal Gemini-Style (`ui/app.py` & `ui/widgets/`):**
  - Barra lateral colapsável com busca em histórico e gerenciamento de tópicos persistentes em SQLite.
  - Fluxo de conversa contínuo (*multiturn stream*) com balões do usuário e cards do assistente.
  - Barra inferior de prompt com atalhos para visão computacional, colar área de transferência, ditado por voz e alternador do Modo Agente.
  - Grade de sugestões contextuais inteligentes dinâmicas acima da barra de prompt ("No foco: VS Code • 3 ações rápidas sugeridas").
- **Diálogo de Configurações Nativo (`ui/preferences.py`):**
  - **Página 1 (IA):** Seletor de provedor por controle segmentado (`Adw.ViewSwitcher`), campos dinâmicos por provedor e teste em tempo real de credenciais sem travamento de UI.
  - **Página 2 (Voz & Ditado):** Calibração de microfone com medidor em tempo real, VAD (Voice Activity Detection), palavra de ativação Vosk e ajustes de sensibilidade e intervalo anti-eco.
  - **Página 3 (Comportamento & Atalhos):** Gerenciamento de atalhos globais e autostart com o sistema.
  - **Página 4 (Memória & Fatos):** Visualizador e editor de fatos memorizados pelo assistente via RAG local.
  - **Página 5 (Extensões & MCP):** Interface com `Adw.ExpanderRow`, status em tempo real com classes semânticas, interruptores individuais e listagem detalhada de ferramentas exportadas.
- **Paleta de Comandos (`Ctrl+K`):** Acesso rápido por teclado a todas as ações, incluindo limpeza de histórico, alternância de modo agente e recarga de servidores MCP (`app.mcp-reload`).
- **Ditado Global (`Super+Shift+D`) e Palavra de Ativação (`Super+Shift+V`):**
  - Ditado de voz headless via Vosk/Whisper com OSD tipo pílula flutuante e injeção de texto direta no app em foco.
  - Palavra de ativação configurável ("ok copilot") com escuta passiva contínua de baixo consumo.

### 2.2 Modo Agente (Autonomia Supervisionada do Desktop)
- **Núcleo Headless (`ai/agent.py` & `ai/agent_router.py`):**
  - Máquina de estados desacoplada de bibliotecas gráficas, permitindo execução pura via CLI ou GUI.
  - Modos `plan()` (dry-run) e `run()` (execução real com callbacks `on_step` e `on_approval`).
  - **Orçamento Adaptativo de Passos:** Heurística `get_step_budget` diferenciando tarefas simples (10 passos / 120s) e tarefas analíticas/pesquisa (30 passos / 480s), configurável nas Preferências.
  - **Síntese Automática ao Exaurir Orçamento:** Quando o limite de passos é atingido, o agente sintetiza os achados acumulados via LLM em vez de falhar silenciosamente.
  - **Salvaguardas Críticas Anti-Alucinação:** Regra 8 rígida no prompt (`_SYSTEM_INSTRUCTION`), expansão da janela de observação para até 3.500 caracteres nos passos recentes (evitando cegueira por clip de 300 chars) e timeouts individuais por ferramenta no `ToolRegistry` retornando payload estruturado com sugestão acionável.
  - **Ferramentas Nativas de Git (`git_log` e `git_status`):** Consulta ultrarrápida de commits e working tree local sem dependência de APIs lentas de rede.
  - Critérios estritos de parada: `done`, `max_steps`, `timeout`, `repeated_failure`, `rejected`, `aborted` e `error`.
  - Roteamento determinístico entre Ollama (tarefas simples) e Gemini (sínteses complexas).
- **Interface Gráfica do Agente (`ui/widgets/agent_card.py`):**
  - *Stepper visual* com timeline interativa em tempo real.
  - **Botão de Continuação ("Continuar (+10 passos)"):** Permite retomar a execução imediatamente a partir do último passo, preservando histórico e dados do turno sem perda de contexto.
  - Inspeção detalhada de raciocínio, parâmetros e saídas com botão expansor.
  - Banner de aprovação para ações sensíveis com opção "Aprovar Todas" para fluxos contínuos.
  - Reversão com botão de desfazer integrado ao card.

### 2.3 Ecossistema MCP (Model Context Protocol) — Fases 1 a 4
- **Protocolo & Transporte (`mcp/protocol.py` & `mcp/client.py`):**
  - Implementação completa da especificação JSON-RPC 2.0 sobre transporte `stdio`.
  - Tratamento assíncrono de requisições com `threading.RLock` contra deadlocks.
  - **Self-Healing:** Auto-recuperação de processos filhos com backoff exponencial (`max_restarts=3`).
  - **Notificações Bidirecionais:** Processamento de `notifications/tools/list_changed` e `notifications/resources/list_changed`.
  - **Recursos MCP:** Suporte a descoberta (`resources/list`) e leitura (`resources/read`).
- **Gerenciamento e Orquestração (`mcp/manager.py`):**
  - Arquivo de configuração padronizado em `~/.config/zorin-copilot/mcp_servers.json`.
  - Monitoramento inotify via `Gio.File.monitor_file` com debounce de 500ms para *hot-reload* instantâneo.
  - Template inicial pronto com servidores padrão (Git, SQLite, Docker, Fetch).
- **Integração com Ferramentas do Agente (`mcp/adapter.py`):**
  - Registro dinâmico de ferramentas no `ToolRegistry`.
  - Heurística automática de classificação de risco (mutante vs. leitura).
  - Exposição da ferramenta segura `mcp__read_resource` (`RiskLevel.SAFE`) para anexar contexto técnico a prompts.

### 2.4 Módulos Operacionais Avançados
- **Casa Inteligente & IoT (`core/home_assistant.py`):**
  - Cliente REST assíncrono para Home Assistant com controle de lâmpadas (Avant Neo / Tuya), dimerização (0-100%), Kelvin (2000K-6500K), cores RGB e palavras-chave em português.
  - Controle de ar-condicionado (temperatura em °C, modos frio/calor/ventilação/auto) e tomadas inteligentes.
  - Aba dedicada nas Configurações Libadwaita com teste de conexão e gerador de comando Docker.
  - Ferramentas `smart_home_control` e `smart_home_status` integradas ao Modo Agente e ao Gemini Live por voz.
- **VS Code Workspace & Desenvolvimento (`core/vscode.py`):**
  - Detecção automática do projeto ativo no VS Code.
  - Abertura de workspaces, criação de novos projetos a partir de templates (Python, Rust, Node/JS).
  - Leitura, escrita e aplicação de patches atômicos em arquivos de código com histórico de reversão.
- **Academic Hub (`core/academic_hub.py`):**
  - Pesquisa unificada em repositórios acadêmicos e científicos (arXiv, OpenAlex, PubMed, Semantic Scholar).
  - Formatação e renderização de cartões acadêmicos com referências bibliográficas e links diretos.
- **Gerenciador de Janelas & Multitarefa (`core/window_manager.py`):**
  - Controle de foco, fechamento, minimização, maximização e organização de janelas no Hyprland, Wayland e X11.
- **Seletor de Modelos em Tempo Real (`ui/widgets/model_selector.py`) & Demonstração de Voz (`ai/voice_preview.py`):**
  - Popover integrado ao cabeçalho para alternância instantânea entre Gemini, Ollama e OpenAI.
  - Pré-escuta de vozes do Gemini Live diretamente na interface de preferências com reprodução local.
- **Gemini Live 3.8 Extended Thinking & Enquadramento Inteligente (`ai/live.py`):**
  - Suporte ao raciocínio contínuo assíncrono (`thinkingBudget: 2048`).
- **Grounding Visual por VLM Local & Telas Opacas (`core/vlm_grounding.py`):**
  - Módulo nativo integrando Ollama local (`qwen2.5vl`, `minicpm-v`) e fallback em nuvem para o Gemini 2.5 Flash.
  - Cascata inteligente tripla: AT-SPI (0ms) -> OCR Tesseract -> VLM Grounding.
  - Ferramentas `locate_element_visual` e `click_visual_element` operacionais.
- **Busca de Código Nativa & Processos do Sistema (`core/system_inspect.py`):**
  - `search_code`: Busca ultrarrápida em arquivos de código com ripgrep (`rg`) e fallback em Python puro, com suporte a regex, context lines e globs.
  - `system_processes`: Inspeção segura de consumo de CPU/RAM, PIDs e comandos via `ps` e `/proc`.
  - Exibição de ícones simbólicos e rótulos nativos Adwaita no `AgentExecutionWidget`.

### 2.5 Qualidade, Testes e Estabilidade
- **1.614 testes unitários e de integração aprovados (100% pass, 5 skipped)** cobrindo todos os módulos do sistema: ações, segurança, OCR, VLM grounding, busca de código, processos, módulo de estudo/AVA/Anki, memória, RAG, rollback, MCP, UI GTK4, Gemini Live, Home Assistant e drivers de janela.

---

## 3. Roadmap: Próximas Fases e Melhorias

```
                                  MAPA DO ROADMAP
                                  
  ┌─────────────────────────────────┐        ┌──────────────────────────────────┐
  │  FASE 5: UNIFICAÇÃO DO LIVE     │   ──►  │  FASE 6: GROUNDING VISUAL VLM    │
  │  (CONCLUÍDA ✅)                 │        │  (CONCLUÍDA ✅)                  │
  └─────────────────────────────────┘        └──────────────────────────────────┘
                   │                                          │
                   ▼                                          ▼
  ┌─────────────────────────────────┐        ┌──────────────────────────────────┐
  │  FASE 8: CAPTURA & ESTUDO AVA   │   ──►  │  FASE 7: MCP REMOTO & PROMPTS    │
  │  (CONCLUÍDA ✅ - SCORM + Anki)  │        │  (SSE/WebSockets + Prompts API)  │
  └─────────────────────────────────┘        └──────────────────────────────────┘
```

### Fase 8: Módulo de Estudo & Captura de Conteúdo AVA (Plano de Estudos SENAC) — CONCLUÍDA ✅
- **Automação Multi-Páginas SCORM/AVA (`core/study_capture.py`):**
  - Método `capture_multipage`: iteração sequencial por slides e páginas de players de aula.
  - Avanço inteligente por cascata de estratégias (`advance_by="auto"`): VLM Grounding (`click_visual_element("avançar aula")`), nós semânticos AT-SPI e hotkey `Page_Down`.
  - Agrupamento limpo em seções Markdown (`## Parte 1`, `## Parte 2`), deduplicação de conteúdo idêntico (detecção de fim de aula) e preservação de disciplina e metadados.
- **Exportação Nativa para Anki `.apkg` e `.tsv` (`core/study_cards.py`):**
  - Implementação nativa de compilação de decks `.apkg` baseada em SQLite `collection.anki2` comprimida em zip com manifesto de mídia, compatível com Anki Desktop, AnkiMobile e AnkiDroid sem bibliotecas externas pesadas.
  - Estilização Libadwaita e suporte a modo escuro embutido nos templates dos cards.
  - Suporte a exportação em formato tabular `.tsv` com tags e HTML limpo.
  - Subcomando CLI: `zorin-copilot-cli study export-anki --deck <id> [--out <caminho>] [--tsv]`.
- **Integração Plena no Ecossistema:**
  - Novas ferramentas registradas no `ToolRegistry` (`RiskLevel.SAFE`): `study_generate_deck`, `study_summarize`, `study_export_anki` e expansão de `capture_lesson` com `multi_page`, `max_pages` e `advance_by`.
  - Exibição de ícones simbólicos e argumentos amigáveis no widget da GUI Adwaita (`AgentExecutionWidget`).
  - Suporte nativo por voz no assistente Gemini Live com tool calling e feedback falado instantâneo.

### Fase 7: Extensões MCP Avançadas (SSE / WebSockets & MCP Prompts)
- **Transporte Remoto (SSE / HTTP Streaming) (Concluído ✅):**
  - Suporte completo a servidores MCP remotos rodando em servidores locais ou em nuvem via HTTP Server-Sent Events (SSE).
  - Conexão segura com tokens de autenticação Bearer / API Key e expansão de variáveis de ambiente (`${VAR}`).
  - Descoberta automática de endpoint POST via evento SSE `endpoint` e suporte a chamadas de ferramentas e recursos remotos.
  - Interface de preferências Libadwaita com tag `[SSE Remoto]`.
  - Bateria de testes em `tests/test_mcp_sse.py`.
- **MCP Prompts (`prompts/list` e `prompts/get`) (Próximo Passo):**
  - Implementar o suporte ao terceiro pilar da especificação MCP.
  - Servidores MCP poderão registrar templates de comandos e personas customizadas diretamente na Paleta de Comandos (`Ctrl+K`) do Copilot.

---

## 4. Guia Rápido de Execução e Desenvolvimento

### Execução da Aplicação
```bash
# Iniciar a interface gráfica completa
uv run zorin-copilot

# Iniciar o modo agente via linha de comando (CLI)
uv run zorin-copilot-cli agent "listar os arquivos da pasta Downloads" --dry-run
```

### Execução dos Testes
```bash
# Executar a suíte de testes do módulo MCP
uv run --with pytest pytest tests/test_mcp.py -vv

# Executar a suíte completa de testes do repositório
uv run --with pytest pytest
```

### Configuração de Servidores MCP
O arquivo de configuração está localizado em:
```bash
~/.config/zorin-copilot/mcp_servers.json
```
Qualquer alteração salva nesse arquivo é recarregada automaticamente pelo Copilot em tempo de execução através do File Watcher inotify.
