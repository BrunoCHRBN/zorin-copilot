# Zorin Copilot

<p align="center">
  <a href="README.en.md">🇺🇸 English Version</a> &nbsp;|&nbsp; <strong>🇧🇷 Versão em Português</strong>
</p>

<p align="center">
  <a href="https://github.com/BrunoCHRBN/zorin-copilot/actions/workflows/ci.yml"><img src="https://github.com/BrunoCHRBN/zorin-copilot/actions/workflows/ci.yml/badge.svg" alt="Status do CI"></a>
  <img src="https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue.svg" alt="Versões do Python">
  <img src="https://img.shields.io/badge/GTK-4.0%20%2B%20Libadwaita-green.svg" alt="GTK4">
  <img src="https://img.shields.io/badge/Wayland-GNOME%20%7C%20KDE%20%7C%20Hyprland%20%7C%20Sway-orange.svg" alt="Wayland">
  <img src="https://img.shields.io/badge/Licen%C3%A7a-MIT-yellow.svg" alt="Licença MIT">
</p>

---

## 📌 Visão Geral

O **Zorin Copilot** é um assistente de inteligência artificial projetado para operar nativamente no ambiente desktop Linux. O projeto foi construído para resolver um problema recorrente de assistentes convencionais: a lentidão e imprecisão de ferramentas baseadas exclusivamente em capturas periódicas de tela e OCR.

Em vez de tratar a interface gráfica como uma imagem estática opaca, o Zorin Copilot conecta-se diretamente aos barramentos de introspecção do sistema operacional (**AT-SPI2**, **D-Bus** e protocolos Wayland). Isso permite identificar botões, campos de entrada, janelas ativas e estruturas de menus com latência mínima e baixo consumo de CPU, acionando modelos multimodais de visão (VLM) apenas sob demanda para regiões específicas da tela (*Region of Interest* — ROI).

O software oferece três interfaces complementares:
- **Interface Gráfica (GUI):** Barra flutuante em GTK4 e Libadwaita com tema glassmorphism no estilo Spotlight/Raycast.
- **Linha de Comando (CLI):** Ferramenta `zorin-copilot-cli` para automação, inspeção de acessibilidade e diagnóstico de sistema.
- **Terminal HY4 (`hy4`):** Cliente de linha de comando para o modelo Tencent HY4 via WorkBuddy AI com visualização da cadeia de raciocínio (*thinking*).

---

## 🎯 Capacidades Principais

* **Inspeção Semântica da Interface (AT-SPI2):** Lê a hierarquia de widgets de aplicações GTK, Qt, Electron e navegadores (Firefox/Chromium) sem depender de OCR contínuo.
* **Modo Agente Supervisionado:** Execução autônoma de tarefas multi-etapas com visualização de progresso (*stepper*), auditoria passo a passo e parada de emergência imediata.
* **Subagentes Especializados e Memória de Trabalho:** Divisão de tarefas complexas entre subagentes (`window_analyst`, `research`, `code`) que compartilham um *scratchpad* estruturado.
* **Ditado Global por Voz (Voice Typing):** Digitação por voz em qualquer aplicativo em foco via atalho `<Super><Shift>D` ou flag `--dictate`, com transcrição local Whisper INT8, supressão de silêncio via VAD e injeção virtual de texto via `libei`/`ydotool`.
* **Voz Bidirecional ao Vivo:** Conversação contínua por áudio com suporte a interrupção natural (*barge-in*), síntese neural local via Piper TTS e palavra de ativação (*wake word*) offline via Vosk.
* **Hub Acadêmico e Modo Tutor:** Captura de questões na tela, tutoria socrática passo a passo e renderização de quadro negro com suporte a fórmulas LaTeX/KaTeX.
* **Segurança e Reversibilidade:** Cerca digital de monitores (`ScreenFenceManager`), classificação de risco de ações (`RiskPolicy`) e pilha de reversão (`UndoStack`) com suporte a `Ctrl+Z`.
* **Integrações de Produtividade:** Conexão nativa com Google Workspace (Agenda, Drive, Gmail via OAuth2 Desktop app com token em keyring), Home Assistant (WebSocket tempo real) e automações n8n / MCP.

---

## 🖥️ Matriz de Compatibilidade de Desktops

O Zorin Copilot possui módulos específicos para abstração de compositores e ambientes gráficos:

| Ambiente de Desktop | Sessão | Gerenciador de Janelas | Captura de Tela | Atalhos Globais |
|---|---|---|---|---|
| **Zorin OS 18 / GNOME 46+** | Wayland / X11 | Mutter | XDG Desktop Portal | `gsettings` (media-keys) |
| **KDE Plasma 6** | Wayland / X11 | KWin | Spectacle / Portal | `kglobalshortcutsrc` |
| **Hyprland** | Wayland | Hyprland | `grim` + `slurp` | Snippet `hyprland.conf` |
| **Sway** | Wayland (wlroots) | Sway | `grim` + `slurp` | Snippet `sway/config` |

---

## 🏗️ Arquitetura Resumida

O projeto adota uma arquitetura em 5 camadas com baixo acoplamento. Consulte a especificação técnica detalhada em [ARCHITECTURE.md](ARCHITECTURE.md).

```
┌─────────────────────────────────────────────────────────────┐
│                    Zorin Copilot UI                         │
│       (GTK4 / Libadwaita · Layer-Shell · OSD Pill)          │
└──────────────┬───────────────────────────────┬──────────────┘
               │                               │
               ▼                               ▼
    [Núcleo de Percepção]             [Shell & Execução]
    • AT-SPI2 (Árvore de A11y)        • Input Drivers (libei / ydotool)
    • D-Bus Active Window Monitor     • RiskPolicy (SAFE / CONFIRM)
    • VLM Grounding & Continuous ROI  • ScreenFenceManager (Zonas)
    • Local Voice (Whisper INT8)      • UndoStack (Reversibilidade)
               │                               │
               └───────────────┬───────────────┘
                               │
                               ▼
                    [Camada de Inteligência]
                    • AgentLoop & Subagents (Scratchpad)
                    • Fast RNN Router (Local vs Nuvem)
                    • Provedores: Ollama / Gemini / Claude / HY4
```

---

## 🚀 Instalação e Configuração

### Método 1: Setup Automatizado (Recomendado)

O script `setup.sh` detecta a distribuição Linux, instala pacotes de sistema, cria o virtualenv com isolamento adequado e configura os atalhos globais:

```bash
# Clone o repositório
git clone https://github.com/BrunoCHRBN/zorin-copilot.git ~/zorin-copilot
cd ~/zorin-copilot

# Execute o script de configuração
./setup.sh
```

### Método 2: Instalação Manual

O Zorin Copilot exige as bibliotecas PyGObject do sistema para acessar as typelibs GTK4 e Libadwaita. O virtualenv **deve** ser criado com `--system-site-packages`:

#### 1. Instalar dependências do sistema

* **Debian / Ubuntu / Zorin OS:**
  ```bash
  sudo apt-get update
  sudo apt-get install -y python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
    libadwaita-1-0 poppler-utils xvfb dbus-x11 python3-venv ydotool
  ```
* **Arch Linux / EndeavourOS:**
  ```bash
  sudo pacman -Syu --needed python python-pip python-gobject gtk4 libadwaita \
    poppler xorg-server-xvfb dbus at-spi2-core python-cairo ydotool
  ```

#### 2. Configurar o ambiente virtual Python

```bash
cd ~/zorin-copilot
python3 -m venv --system-site-packages .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e . --no-deps
.venv/bin/pip install requests beautifulsoup4 websockets
```

#### 3. Instalar pacotes opcionais conforme necessidade

```bash
# Suporte a voz local (faster-whisper e piper-tts)
.venv/bin/pip install faster-whisper piper-tts

# Suporte a palavra de ativação offline (vosk)
.venv/bin/pip install vosk

# Integrações Google Workspace (OAuth2 Desktop app)
.venv/bin/pip install google-api-python-client google-auth-oauthlib google-auth keyring

# Exportação para documentos Office reais (.docx e .pptx)
.venv/bin/pip install python-docx python-pptx
```

---

## 💻 Guia de Uso

### 1. Interface Gráfica (GUI)

Inicie o assistente com a janela flutuante estilo Spotlight:

```bash
zorin-copilot
```

Ou utilize o atalho de sistema registrado (`Super + C` ou `Super + Espaço`).

### 2. Linha de Comando (`zorin-copilot-cli`)

O CLI expõe todas as capacidades do núcleo para testes, scripts de automação e auditoria:

```bash
# Diagnosticar barramentos AT-SPI2, Wayland e integridade do ambiente
zorin-copilot-cli doctor

# Inspecionar a árvore de elementos da aplicação ativa na tela
zorin-copilot-cli inspect

# Executar o Modo Agente supervisionado com dry-run (apenas planejamento)
zorin-copilot-cli agent "abrir o navegador e buscar a documentação do kernel" --dry-run

# Executar o Modo Agente real (ações de risco solicitam confirmação)
zorin-copilot-cli agent "organizar a pasta ~/Downloads por extensão" --max-steps 15

# Realizar busca semântica em documentos pessoais (RAG)
zorin-copilot-cli rag "qual o valor total da fatura de internet?"

# Capturar exercício da tela e abrir sessão de estudos socrática
zorin-copilot-cli study capture
```

### 3. Cliente de Terminal HY4 (`hy4`)

Para interagir diretamente com o modelo Tencent HY4 via terminal:

```bash
# Enviar um prompt único exibindo o fluxo de raciocínio
hy4 "Como otimizar a latência de chamadas D-Bus no GNOME?"

# Ocultar o processo de raciocínio (modo direto para pipes e scripts)
hy4 --no-think --raw "Liste 5 comandos úteis do journalctl"
```

---

## ⌨️ Atalhos de Teclado

| Atalho | Ação | Contexto |
|---|---|---|
| `Super + C` | Abrir / Alternar janela principal do Copilot | Global |
| `Super + Shift + D` | Iniciar / Parar ditado por voz global (Voice Typing) | Global |
| `Super + Shift + S` | Capturar região da tela para análise | Global |
| `Super + Shift + V` | Iniciar sessão de voz bidirecional ao vivo | Global |
| `Ctrl + K` | Painel de comandos (busca difusa de ações) | Janela Copilot |
| `Ctrl + Z` | Desfazer última ação reversível (`UndoStack`) | Janela Copilot |
| `Ctrl + S` | Exportar histórico da conversa como Markdown | Janela Copilot |
| `Ctrl + N` | Iniciar nova conversa | Janela Copilot |
| `Ctrl + H` | Alternar exibição da barra lateral | Janela Copilot |
| `Ctrl + Q` | Fechar o aplicativo | Janela Copilot |

---

## ↩️ Segurança e Reversibilidade de Ações

O Zorin Copilot executa ações em ambiente de desktop real. Para garantir a segurança dos dados do usuário, o sistema adota proteções estritas:

1. **Ações Reversíveis (com Snapshot):**
   - Criação ou sobrescrita de arquivos: restaura o conteúdo anterior ou remove o arquivo criado.
   - Organização de diretórios: move cada arquivo de volta à pasta de origem e remove diretórios vazios criados na operação.
2. **Ações Não-Reversíveis (Exigem Confirmação Prévia):**
   - Cliques físicos, digitação em aplicativos externos, execução de scripts shell e finalização de processos alteram estados externos do sistema operacional. O Copilot classifica essas ações como `CONFIRM` via `RiskPolicy`, exigindo aprovação antes do disparo.
3. **Cerca Digital de Tela (`ScreenFenceManager`):**
   - O usuário pode restringir a área de atuação do cursor e da visão a monitores ou coordenadas específicas, impedindo interação involuntária com janelas contendo dados confidenciais.

---

## 🧪 Suíte de Testes e Sandbox

A suíte de testes do projeto foi arquitetada para garantir isolamento estrito e proteção contra exaustão de memória RAM:

```bash
# Executar a suíte de testes com pytest
.venv/bin/python -m pytest -q
```

> [!IMPORTANT]
> **Use sempre `pytest`, nunca `python -m unittest`:**
> A suíte combina classes legadas `unittest.TestCase` e funções nativas de `pytest`. O runner do `unittest` descarta testes em estilo pytest com código de saída 5, mascarando módulos não executados.
>
> O isolamento (`tests/_sandbox.py` e `tests/conftest.py`) redireciona `XDG_CONFIG_HOME` e `XDG_DATA_HOME` para pastas temporárias e define `ZORIN_TEST_MODE=1`. Isso impede que os testes acessem suas credenciais reais ou iniciem daemons pesados em segundo plano.

Para mais detalhes sobre estabilidade e diagnósticos de execução, consulte [`docs/ESTABILIDADE_TESTES.md`](docs/ESTABILIDADE_TESTES.md).

---

## 🎨 Personalização Visual (CSS)

O tema glassmorphism é definido em `src/zorin_copilot/data/zorin-copilot.css`. É possível sobrescrever as regras visuais sem modificar o código-fonte criando o arquivo `~/.config/zorin-copilot/user.css`:

```css
/* Exemplo de personalização de cores em ~/.config/zorin-copilot/user.css */
@define-color accent_color #7d5fff;

window.glass-window > contents {
    background-color: rgba(20, 24, 32, 0.98);
}
```

A redefinição da variável `@accent_color` atualiza automaticamente todos os botões de envio, bordas de foco e indicadores da barra lateral.

---

## 📚 Documentação Adicional

Explore o diretório [`docs/`](docs/) para especificações técnicas detalhadas:
- [Índice Geral de Documentação](docs/README.md)
- [Especificação Técnica de Arquitetura](ARCHITECTURE.md)
- [Design do Modo Agente Supervisionado](docs/AGENT_MODE.md)
- [Guia de Portabilidade para Arch Linux / EndeavourOS](docs/ENDEAVOUROS_PORT.md)
- [Guia de Configuração Google OAuth2](docs/google-oauth-setup.md)
- [Integração com Home Assistant](docs/CASA_INTELIGENTE.md)
- [Diretrizes de Contribuição](CONTRIBUTING.md)
- [Política de Segurança](SECURITY.md)

---

## 📄 Licença

Distribuído sob a licença **MIT**. Consulte `LICENSE` para mais informações.
