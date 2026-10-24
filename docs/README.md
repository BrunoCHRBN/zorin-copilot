# Índice da Documentação — Zorin Copilot

[🇺🇸 English Version](README.en.md) | [🇧🇷 Versão em Português](README.md)

Bem-vindo ao índice central da documentação do **Zorin Copilot**. Aqui você encontra as especificações de engenharia, guias práticos de configuração, manuais de segurança e o histórico de evolução do projeto.

---

## 🧭 Mapa da Documentação

### 1. Arquitetura e Especificações Centrais

| Documento | Idioma | Descrição |
|---|---|---|
| [ARCHITECTURE.md](../ARCHITECTURE.md) | PT / [EN](../ARCHITECTURE.en.md) | Visão geral da arquitetura de 5 camadas, barramentos AT-SPI2/D-Bus e fluxo de execução. |
| [AGENT_MODE.md](AGENT_MODE.md) | PT / [EN](AGENT_MODE.en.md) | Especificação do loop de agente supervisionado, máquina de estados, ferramentas e limites de parada. |
| [LIVE_VOICE_TOOL_CALLING.md](LIVE_VOICE_TOOL_CALLING.md) | PT | Arquitetura de voz bidirecional em tempo real via WebSocket com despacho de ferramentas. |
| [VLM_GROUNDING_VISUAL.md](VLM_GROUNDING_VISUAL.md) | PT | Grounding visual de elementos via modelos multimodais e conversão de caixas delimitadoras em coordenadas. |
| [BUSCA_CODIGO_E_PROCESSOS.md](BUSCA_CODIGO_E_PROCESSOS.md) | PT | Módulos de busca semântica em arquivos locais e inspeção de processos do sistema operacional. |
| [fronteira-core-skills.md](fronteira-core-skills.md) | PT | Definição da fronteira entre ferramentas intrínsecas do núcleo e habilidades externas. |

---

### 2. Guias Práticos e Configuração de Ambientes

| Documento | Descrição |
|---|---|
| [ENDEAVOUROS_PORT.md](ENDEAVOUROS_PORT.md) | Guia completo de instalação e configuração no Arch Linux, EndeavourOS, Hyprland e Sway. |
| [google-oauth-setup.md](google-oauth-setup.md) | Configuração de credenciais Google OAuth2 (modo Desktop app) para Calendar, Drive e Gmail. |
| [CASA_INTELIGENTE.md](CASA_INTELIGENTE.md) | Integração com Home Assistant via WebSocket: monitoramento de entidades, eventos e anúncios por voz. |
| [ACESSO_REMOTO.md](ACESSO_REMOTO.md) | Arquitetura de acesso remoto em rede local e integração com KDE Connect. |
| [ACESSO_REMOTO_KDE_CONNECT.md](ACESSO_REMOTO_KDE_CONNECT.md) | Procedimentos de emparelhamento e execução de comandos via KDE Connect. |
| [ESTUDO.md](ESTUDO.md) / [PLANO_ASSISTENTE_ESTUDOS.md](PLANO_ASSISTENTE_ESTUDOS.md) | Assistente acadêmico socrático, captura de exercícios na tela e renderização de quadro negro. |

---

### 3. Confiabilidade, Testes e Segurança

| Documento | Idioma | Descrição |
|---|---|---|
| [ESTABILIDADE_TESTES.md](ESTABILIDADE_TESTES.md) | PT / [EN](ESTABILIDADE_TESTES.en.md) | Diagnóstico completo da suíte de testes: sandbox (`_sandbox.py`), isolamento de memória e proteção de processos. |
| [SECURITY.md](../SECURITY.md) | PT / [EN](../SECURITY.en.md) | Política de relato de vulnerabilidades, cerca de tela (`ScreenFenceManager`) e cofre de senhas. |
| [PROPOSTA_REVERSIBILIDADE.md](PROPOSTA_REVERSIBILIDADE.md) | PT | Arquitetura da pilha de reversibilidade (`UndoStack`) e critérios de segurança em mutações de arquivos. |
| [PROPOSTA_DEFESA_REDE.md](PROPOSTA_DEFESA_REDE.md) | PT | Inspeção de conexões ativas, auditoria de portas e terminação segura de processos suspeitos. |
| [ENTREGA_E_PRIVACY_SHIELD.md](ENTREGA_E_PRIVACY_SHIELD.md) | PT | Escudo de privacidade para sanitização de dados sensíveis antes do envio a provedores em nuvem. |
| [ENTREGA_D_WATCHDOG_PENSANDO.md](ENTREGA_D_WATCHDOG_PENSANDO.md) | PT | Temporizadores de watchdog para prevenção de deadlocks e loops infinitos no agente. |

---

### 4. Sistema Visual e Design

| Documento | Descrição |
|---|---|
| [SISTEMA_VISUAL_SUPERHUMAN.md](SISTEMA_VISUAL_SUPERHUMAN.md) | Sistema de design glassmorphism, classes GTK4/Libadwaita e paleta de cores. |
| [PROPOSTA_PILL_ANOTACAO_EDGE.md](PROPOSTA_PILL_ANOTACAO_EDGE.md) | Especificação da pílula flutuante OSD para ditado rápido e anotações por voz sem perda de foco. |
| [PROPOSTA_REFORMULACAO_GUI.md](PROPOSTA_REFORMULACAO_GUI.md) | Refatoração modular dos widgets da interface gráfica (`PromptBar`, `ChatStream`, `Blackboard`). |

---

### 5. Histórico de Entregas e Propostas de Evolução (RFCs)

Esta seção arquiva documentos de planejamento e entregas técnicas incrementais:

- [ROADMAP_STATUS.md](ROADMAP_STATUS.md): Estado de entrega dos marcos de desenvolvimento.
- [ANALISE_APRIMORAMENTO_RACIOCINIO.md](ANALISE_APRIMORAMENTO_RACIOCINIO.md): Avaliação de modelos de raciocínio e latência de inferência.
- [ENTREGA_A_VOZ_TELA.md](ENTREGA_A_VOZ_TELA.md) até [ENTREGA_G_ATRIBUICAO_DE_TURNO.md](ENTREGA_G_ATRIBUICAO_DE_TURNO.md): Registros de entrega das fases de streaming multimídia e voz.
- [PROPOSTA_JARVIS_AMBIENTE.md](PROPOSTA_JARVIS_AMBIENTE.md): Estudo arquitetural de percepção ambiental contínua.
- [prototypes/](prototypes/): Protótipos de renderização HTML de quadros e mockups de interface.

