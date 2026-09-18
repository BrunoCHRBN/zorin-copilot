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

---

## 2. Próximos Passos Priorizados

```
                                  MAPA DE PRIORIDADES
                                  
  ┌─────────────────────────────────┐        ┌──────────────────────────────────┐
  │  1. GROUNDING VISUAL VLM        │   ──►  │  2. BUSCA DE CÓDIGO NATIVA       │
  │  (CONCLUÍDO - Qwen2.5-VL/Ollama)│        │  (ripgrep / code_search / ps)    │
  └─────────────────────────────────┘        └──────────────────────────────────┘
                   │                                          │
                   ▼                                          ▼
  ┌─────────────────────────────────┐        ┌──────────────────────────────────┐
  │  3. AUTOMAÇÃO AVA / ANKI        │   ──►  │  4. MCP REMOTO (SSE / HTTP)      │
  │  (Extração SCORM + Decks Anki)  │        │  (Servidores Cloud / Tokens)     │
  └─────────────────────────────────┘        └──────────────────────────────────┘
```

### Passo 1: Grounding Visual com VLM Local (Concluído ✅)
- Suporte a modelos de visão no Ollama (`qwen2.5vl`, `minicpm-v`) implementado com desambiguação geométrica, projeção pixel-perfect em monitores Wayland e fallback em nuvem. Ferramentas `locate_element_visual` e `click_visual_element` operacionais.

### Passo 2: Ferramentas Nativas de Busca de Código e Processos Locais
- **Problema:** Quando o agente precisa encontrar definições ou inspecionar processos travados no sistema, ler arquivos um a um pelo sistema de arquivos consome muitos passos do orçamento.
- **Ações:**
  1. Implementar ferramenta nativa `search_code(pattern, path, file_glob)` com subprocesso isolado chamando `ripgrep` (ou busca pura em Python), retornando snippets com número de linha e contexto.
  2. Implementar ferramenta `system_processes(filter)` para inspecionar consumo de RAM/CPU e verificar se servidores ou serviços de desenvolvimento estão ativos.

### Passo 4: Pipeline de Automação de Estudo AVA / SENAC (Fase A)
- **Problema:** A captura de materiais didáticos do portal EAD exige interação manual repetitiva através de múltiplos módulos e subunidades.
- **Ações:**
  1. Sequência automatizada de navegação supervisionada para listagem e leitura de unidades curriculares.
  2. Extração de conteúdo embutido utilizando o leitor AT-SPI2 e Smart OCR com limpeza automática de artefatos.
  3. Geração automática de cartões de repetição espaçada no formato Anki (`.apkg`) e sumários em Markdown salvos em `~/Documentos/Estudos`.

### Passo 5: Suporte a Servidores MCP Remotos (SSE / HTTP Streaming)
- **Problema:** Atualmente o subsistema MCP suporta apenas processos locais via `stdio`.
- **Ações:**
  1. Implementar transporte client SSE (Server-Sent Events) sobre HTTP no `MCPClient`.
  2. Adicionar suporte a autenticação por token (Bearer Token / API Key) para conexão com servidores MCP remotos corporativos ou de infraestrutura na nuvem.

---

## 3. Diretrizes de Qualidade e Design (Anti-Slop)

Todas as implementações subsequentes devem obrigatoriamente manter:
- **Design Nativo GNOME / Libadwaita:** Seguir estritamente as Human Interface Guidelines (HIG). Sem gradientes roxos/indigo de IA, sem landing pages embutidas em diálogos e sem emojis em botões.
- **Tipografia e Ícones Simbólicos:** Uso exclusivo de ícones simbólicos Adwaita e números tabulares (`tabular-nums`) para cronômetros e métricas.
- **Execução Headless & Não-Bloqueante:** O núcleo do agente deve permanecer testável sem dependência direta de servidor gráfico ou display Wayland/X11 ativo.
