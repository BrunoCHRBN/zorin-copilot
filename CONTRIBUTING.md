# Guia de Contribuição — Zorin Copilot

[🇺🇸 English Version](CONTRIBUTING.en.md) | [🇧🇷 Versão em Português](CONTRIBUTING.md)

Obrigado pelo interesse em contribuir com o **Zorin Copilot**! Este projeto é desenvolvido com foco em baixo overhead, determinismo e fidelidade técnica na interação nativa com desktops Linux (GNOME, KDE Plasma, Hyprland e Sway).

Para manter a qualidade e a estabilidade do sistema, siga as diretrizes abaixo.

---

## 1. Princípios de Design do Projeto

Ao implementar novas funcionalidades ou correções, preserve os seguintes pilares de arquitetura:

1. **Separação estrita de camadas:**
   - O núcleo do agente (`zorin_copilot.ai.agent`) **não pode importar** GTK, AT-SPI2 ou bibliotecas gráficas. Toda comunicação com o sistema operacional ocorre via injeção de dependências através do `ToolRegistry`. Isso viabiliza testes headless em CI sem necessidade de servidor gráfico ou barramentos ativos.
2. **Determinismo e Tool Calling estruturado:**
   - Modelos de IA não executam comandos shell arbitrários nem código solto sem validação. Toda ação do desktop passa por contratos de schema validados por `RiskPolicy`, com confirmação interativa para ações destrutivas.
3. **Reversibilidade por padrão:**
   - Operações em arquivos e diretórios devem registrar snapshots no `UndoStack` antes de modificar o estado do disco.
4. **Respeito ao ecossistema da distribuição:**
   - PyGObject e typelibs do GTK4/Libadwaita pertencem ao sistema operacional. Nunca instale PyGObject via pip em distribuições com políticas restritivas (como Arch Linux / PEP 668).

---

## 2. Preparando o Ambiente de Desenvolvimento

### Pré-requisitos de Sistema

#### Ubuntu 24.04 LTS / Zorin OS 18
```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
  libadwaita-1-0 poppler-utils xvfb dbus-x11 python3-venv git
```

#### Arch Linux / EndeavourOS
```bash
sudo pacman -Syu --needed \
  python python-pip python-gobject gtk4 libadwaita \
  poppler xorg-server-xvfb dbus at-spi2-core python-cairo git
```

### Configurando o Virtualenv

O virtualenv **deve** ser criado com `--system-site-packages` para herdar os bindings do PyGObject e as typelibs nativas:

```bash
# Clone seu fork do repositório
git clone https://github.com/SEU_USUARIO/zorin-copilot.git
cd zorin-copilot

# Crie o venv herdando pacotes do sistema
python3 -m venv --system-site-packages .venv

# Instale o pacote em modo editável sem puxar dependências binárias redundantes
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[test]" --no-deps
.venv/bin/pip install pytest Pillow requests beautifulsoup4 websockets setuptools python-docx python-pptx
```

---

## 3. Diretrizes de Testes e Sandbox

### Por que usar exclusivamente `pytest`?

> **Atenção:** Nunca use `python -m unittest`.

A suíte de testes contém classes legadas `unittest.TestCase` combinadas com funções nativas do `pytest`. O executor do `unittest` descarta testes em estilo pytest, retornando `exit code 5` (nenhum teste coletado nos módulos correspondentes), criando a falsa impressão de uma suíte verde quando centenas de testes foram ignorados.

### Isolamento de Ambiente (`tests/_sandbox.py`)

A suíte ativa automaticamente uma sandbox de isolamento carregada via `tests/conftest.py`:
- Redireciona `XDG_CONFIG_HOME` e `XDG_DATA_HOME` para diretórios temporários (`/tmp/zorin-test-...`).
- Ativa a variável de ambiente `ZORIN_TEST_MODE=1`.
- Desativa serviços em segundo plano com alto consumo (servidores MCP sob demanda, embeddings locais em disco e escuta de wake word).

Se o isolamento falhar, os testes tentarão ler o `config.json` real e o `memory.db` de produção do seu usuário, criando processos MCP órfãos que esgotam a memória RAM.

### Executando Testes

Para rodar um módulo individual ou subconjunto:

```bash
# Módulo individual
.venv/bin/python -m pytest -q tests/test_core.py

# Testes de ferramentas do agente
.venv/bin/python -m pytest -q tests/test_agent_tools.py tests/test_subagents.py
```

### Testes de Interface e Guarda de Memória

Testes que criam janelas GTK acumulam buffers de renderização. Em máquinas com memória limitada ou sem partição swap, execute os testes com o runner seguro por processo:

```bash
# Execução da suíte completa com proteção por processo (< 900 MB por módulo)
.venv/bin/python -m pytest -q tests/test_desktop_portability.py
```

No CI (ou com servidor gráfico virtual local):

```bash
xvfb-run -a .venv/bin/python -m pytest -q
```

---

## 4. Padrões de Código e Idioma Técnico

- **Linguagem e Didática:** Mantenha comentários, docstrings e documentação objetivos, claros e focados no mecanismo técnico real. Evite introduções prolixas ou adjetivos vagos.
- **Termos Técnicos:** Conserve termos técnicos consolidados no idioma original inglês (*backend, frontend, pipeline, runtime, CLI, GUI, daemon, tool calling, dry-run, agent loop, subagents, scratchpad, fallback, bypass, sandbox, OSD, hotkey, token, barge-in, wake word, ROI, VAD, typelibs, toast, stepper, mock, fixture, compositor, layer-shell, overlay*).
- **Tipagem:** Utilize anotações de tipo Python (`from __future__ import annotations`, tipos padrão de coleções `list[str]`, `dict[str, Any]`, `Callable`, `Optional`).
- **Tratamento de Exceções:** capture exceções específicas. Falhas de D-Bus ou subprocessos devem registrar logs descritivos e acionar fallbacks graciosos sempre que aplicável.

---

## 5. Fluxo de Trabalho Git e Pull Requests

1. Crie uma branch descritiva a partir de `main`:
   ```bash
   git checkout -b feat/nome-da-funcionalidade
   # ou
   git checkout -b fix/descricao-do-bug
   ```
2. Adicione testes automatizados cobrindo a nova funcionalidade ou a regressão corrigida.
3. Garanta que a suíte de testes continue 100% verde:
   ```bash
   .venv/bin/python -m pytest -q
   ```
4. Escreva mensagens de commit claras e padronizadas no formato Conventional Commits:
   - `feat(agent): add multi-window inspection`
   - `fix(a11y): handle null coordinates on unmapped wayland surfaces`
   - `docs(readme): clarify multi-desktop setup instructions`
5. Abra o Pull Request preenchendo todos os itens do template em `.github/pull_request_template.md`.

