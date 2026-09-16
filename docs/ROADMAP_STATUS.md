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

### 2.4 Qualidade, Testes e Estabilidade
- **1.398 testes unitários e de integração aprovados (100% pass)** cobrindo todos os módulos: ações, segurança, OCR, memória, RAG, rollback, MCP, UI GTK4 e cliente de voz.

---

## 3. Roadmap: Próximas Fases e Melhorias

```
                                  MAPA DO ROADMAP
                                  
  ┌─────────────────────────────────┐        ┌──────────────────────────────────┐
  │  FASE 5: UNIFICAÇÃO DO LIVE     │   ──►  │  FASE 6: GROUNDING VISUAL VLM    │
  │  (ToolRegistry + MCP no Voz)    │        │  (MiniCPM-V / Qwen2-VL local)    │
  └─────────────────────────────────┘        └──────────────────────────────────┘
                   │                                          │
                   ▼                                          ▼
  ┌─────────────────────────────────┐        ┌──────────────────────────────────┐
  │  FASE 7: MCP REMOTO & PROMPTS   │   ──►  │  FASE 8: CAPTURA & ESTUDO AVA    │
  │  (SSE/WebSockets + Prompts API) │        │  (Automação SPA/SCORM + Anki)    │
  └─────────────────────────────────┘        └──────────────────────────────────┘
```

### Fase 5: Unificação de Ferramentas no Cliente de Voz ao Vivo (`ai/live.py`)
- **Problema Atual:** O cliente de voz bidirecional WebSocket (`GeminiLiveClient`) mantém suas próprias 28 declarações de ferramentas e despacho interno duplicado em relação ao `ToolRegistry`.
- **Objetivo:**
  1. Fazer o `GeminiLiveClient` consumir diretamente o `ToolRegistry` (`agent_tools.py`).
  2. Disponibilizar dinamicamente todas as ferramentas de servidores MCP para o assistente de voz em tempo real (ex.: *"Copilot, qual o status do git atual?"* ou *"Copilot, reinicie o container docker da aplicação"*).
  3. Reutilizar os mesmos interceptadores de risco e aprovação inline durante a conversa por voz.

### Fase 6: Grounding Visual com Modelos de Visão Locais (VLM)
- **Problema Atual:** Quando o aplicativo em foco não exporta árvore de acessibilidade AT-SPI2 completa (como aplicações em canvas HTML5, jogos, SCORM de portais AVA ou desktops remotos VNC), o assistente não consegue localizar botões por ID/texto puro.
- **Objetivo:**
  1. Integração com **MiniCPM-V** ou **Qwen2-VL** rodando no Ollama local para visão computacional de tela.
  2. Implementação da ferramenta `locate_element_visual(description, screen_bbox)`: captura a região da tela e retorna coordenadas (x, y) normalizadas para o `VirtualInputDriver`.
  3. Fallback inteligente: tentar AT-SPI2 primeiro (instantâneo, sem custo de GPU); se não encontrar ou for elemento de canvas, recorrer ao VLM.

### Fase 7: Extensões MCP Avançadas (SSE / WebSockets & MCP Prompts)
- **Transporte Remoto (SSE / HTTP Streaming):**
  - Suportar servidores MCP remotos rodando em servidores locais ou nuvem via HTTP Server-Sent Events (SSE).
  - Permitir conexão segura com tokens de autenticação bearer.
- **MCP Prompts (`prompts/list` e `prompts/get`):**
  - Implementar o suporte ao terceiro pilar da especificação MCP.
  - Servidores MCP poderão registrar templates de comandos e personas customizadas diretamente na Paleta de Comandos (`Ctrl+K`) do Copilot.

### Fase 8: Módulo de Estudo & Captura de Conteúdo AVA (Plano de Estudos SENAC)
- **Objetivo:** Automatizar a captura de materiais didáticos e geração de recursos de estudo com o Modo Agente.
- **Etapas:**
  1. Sequência automatizada de navegação no portal do estudante (autenticação supervisionada, listagem de unidades curriculares).
  2. Extração de conteúdo embutido em players SCORM/SPA utilizando o leitor de árvore AT-SPI2 e Smart OCR.
  3. Pipeline de geração automática de cartões Anki (`.apkg`) e sumários executivos estruturados em Markdown via RAG local.

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
