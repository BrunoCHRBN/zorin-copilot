# Documentation Index — Zorin Copilot

[🇺🇸 English Version](README.en.md) | [🇧🇷 Versão em Português](README.md)

Welcome to the central documentation index for **Zorin Copilot**. This directory contains engineering specifications, desktop environment guides, reliability and security manuals, and the historical evolution logs of the project.

---

## 🧭 Documentation Map

### 1. Architecture and Core Specifications

| Document | Language | Description |
|---|---|---|
| [ARCHITECTURE.md](../ARCHITECTURE.md) | PT / [EN](../ARCHITECTURE.en.md) | Five-layer architecture overview, AT-SPI2/D-Bus system buses, and execution lifecycle. |
| [AGENT_MODE.md](AGENT_MODE.md) | PT / [EN](AGENT_MODE.en.md) | Supervised desktop agent state machine specification, tool registries, and stopping limits. |
| [LIVE_VOICE_TOOL_CALLING.md](LIVE_VOICE_TOOL_CALLING.md) | PT | Bidirectional realtime voice architecture over WebSocket with tool dispatching. |
| [VLM_GROUNDING_VISUAL.md](VLM_GROUNDING_VISUAL.md) | PT | Visual UI grounding via multimodal models and normalized bounding box coordinate mapping. |
| [BUSCA_CODIGO_E_PROCESSOS.md](BUSCA_CODIGO_E_PROCESSOS.md) | PT | Local filesystem semantic search and operating system process inspection modules. |
| [fronteira-core-skills.md](fronteira-core-skills.md) | PT | Formal boundaries between intrinsic core capabilities and external agent skills. |

---

### 2. Practical Guides & Desktop Environment Setup

| Document | Description |
|---|---|
| [ENDEAVOUROS_PORT.md](ENDEAVOUROS_PORT.md) | Complete installation and configuration guide for Arch Linux, EndeavourOS, Hyprland, and Sway. |
| [google-oauth-setup.md](google-oauth-setup.md) | Google OAuth2 Desktop App credential setup for Google Calendar, Drive, and Gmail. |
| [CASA_INTELIGENTE.md](CASA_INTELIGENTE.md) | Home Assistant integration over WebSocket: entity monitoring, state events, and TTS announcements. |
| [ACESSO_REMOTO.md](ACESSO_REMOTO.md) | Local network remote access architecture and integration with KDE Connect. |
| [ACESSO_REMOTO_KDE_CONNECT.md](ACESSO_REMOTO_KDE_CONNECT.md) | Device pairing procedures and command execution via KDE Connect. |
| [ESTUDO.md](ESTUDO.md) / [PLANO_ASSISTENTE_ESTUDOS.md](PLANO_ASSISTENTE_ESTUDOS.md) | Socratic study assistant, on-screen exercise capture, and chalkboard rendering. |

---

### 3. Reliability, Testing, and Security

| Document | Language | Description |
|---|---|---|
| [ESTABILIDADE_TESTES.md](ESTABILIDADE_TESTES.md) | PT / [EN](ESTABILIDADE_TESTES.en.md) | Complete test suite diagnostic: environment sandbox (`_sandbox.py`), memory isolation, and process safety. |
| [SECURITY.md](../SECURITY.md) | PT / [EN](../SECURITY.en.md) | Vulnerability disclosure policy, digital screen fence (`ScreenFenceManager`), and credential keyring security. |
| [PROPOSTA_REVERSIBILIDADE.md](PROPOSTA_REVERSIBILIDADE.md) | PT | Reversibility stack (`UndoStack`) design and snapshot criteria for filesystem mutations. |
| [PROPOSTA_DEFESA_REDE.md](PROPOSTA_DEFESA_REDE.md) | PT | Active connection inspection, port auditing, and safe process termination. |
| [ENTREGA_E_PRIVACY_SHIELD.md](ENTREGA_E_PRIVACY_SHIELD.md) | PT | Privacy shield for confidential data sanitization prior to cloud provider transmission. |
| [ENTREGA_D_WATCHDOG_PENSANDO.md](ENTREGA_D_WATCHDOG_PENSANDO.md) | PT | Watchdog timers to prevent deadlocks and runaway execution loops in the agent. |

---

### 4. Visual System and Design

| Document | Description |
|---|---|
| [SISTEMA_VISUAL_SUPERHUMAN.md](SISTEMA_VISUAL_SUPERHUMAN.md) | Glassmorphism design system, GTK4/Libadwaita style classes, and CSS token variables. |
| [PROPOSTA_PILL_ANOTACAO_EDGE.md](PROPOSTA_PILL_ANOTACAO_EDGE.md) | Floating OSD pill specification for rapid dictation and voice note-taking without focus theft. |
| [PROPOSTA_REFORMULACAO_GUI.md](PROPOSTA_REFORMULACAO_GUI.md) | Modular UI widget refactoring plan (`PromptBar`, `ChatStream`, `Blackboard`). |

---

### 5. Historical Deliveries & RFC Archive

This section archives incremental design RFCs and implementation deliverables:

- [ROADMAP_STATUS.md](ROADMAP_STATUS.md): Development milestone completion logs.
- [ANALISE_APRIMORAMENTO_RACIOCINIO.md](ANALISE_APRIMORAMENTO_RACIOCINIO.md): Reasoning model benchmark analysis and inference latencies.
- [ENTREGA_A_VOZ_TELA.md](ENTREGA_A_VOZ_TELA.md) through [ENTREGA_G_ATRIBUICAO_DE_TURNO.md](ENTREGA_G_ATRIBUICAO_DE_TURNO.md): Multimedia streaming and voice integration logs.
- [PROPOSTA_JARVIS_AMBIENTE.md](PROPOSTA_JARVIS_AMBIENTE.md): Architectural exploration of ambient continuous awareness.
- [prototypes/](prototypes/): HTML prototypes and interface layout mockups.

