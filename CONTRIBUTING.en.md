# Contributing Guide — Zorin Copilot

[🇺🇸 English Version](CONTRIBUTING.en.md) | [🇧🇷 Versão em Português](CONTRIBUTING.md)

Thank you for your interest in contributing to **Zorin Copilot**! This project is engineered with a strict focus on low overhead, deterministic behavior, and high-fidelity native desktop integration across Linux environments (GNOME, KDE Plasma, Hyprland, and Sway).

To ensure system stability and code quality, please adhere to the guidelines outlined below.

---

## 1. Core Architectural Principles

When implementing new features or bug fixes, maintain the following architectural pillars:

1. **Strict Layer Decoupling:**
   - The agent core (`zorin_copilot.ai.agent`) **must never import** GTK, AT-SPI2, or graphical libraries. All operating system interactions occur strictly via dependency injection through the `ToolRegistry`. This allows headless CI testing without requiring virtual display servers or active D-Bus accessibility daemons.
2. **Determinism and Structured Tool Calling:**
   - AI models do not run arbitrary shell scripts or unvalidated commands. Every desktop action must adhere to strict schemas validated by `RiskPolicy`, enforcing interactive user confirmations for destructive or high-risk operations.
3. **Reversibility by Default:**
   - File and folder operations must record state snapshots in `UndoStack` prior to disk mutations, enabling clean rollbacks.
4. **Respect Distribution Packaging Standards:**
   - PyGObject bindings and GTK4 / Libadwaita typelibs belong to system package managers. Never install PyGObject via pip in distributions with externally managed Python environments (e.g., Arch Linux PEP 668).

---

## 2. Setting Up the Development Environment

### System Prerequisites

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

### Virtual Environment Configuration

The virtual environment **must** be created with `--system-site-packages` to inherit the system-provided PyGObject bindings and native typelibs:

```bash
# Clone your fork of the repository
git clone https://github.com/YOUR_USERNAME/zorin-copilot.git
cd zorin-copilot

# Create the venv inheriting system site-packages
python3 -m venv --system-site-packages .venv

# Install editable package without pulling redundant binary packages
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[test]" --no-deps
.venv/bin/pip install pytest Pillow requests beautifulsoup4 websockets setuptools python-docx python-pptx
```

---

## 3. Testing and Sandbox Directives

### Why You Must Run `pytest` Exclusively

> **Warning:** Never use `python -m unittest`.

The test suite contains both legacy `unittest.TestCase` classes and standard pytest test functions. The `unittest` runner ignores pytest-style functions, exiting with `exit code 5` ("no tests collected") across those test modules. This gives a false impression of a passing suite while hundreds of tests are skipped.

### Environment Sandbox (`tests/_sandbox.py`)

The test suite automatically isolates your runtime environment via `tests/conftest.py`:
- Redirects `XDG_CONFIG_HOME` and `XDG_DATA_HOME` to isolated temporary directories (`/tmp/zorin-test-...`).
- Sets `ZORIN_TEST_MODE=1`.
- Suppresses heavy background daemons (on-demand MCP servers, disk RAG vector indexing, and background wake word listeners).

Without this sandbox, tests would read and mutate your live `config.json` and production `memory.db`, spawning orphaned MCP server processes that quickly exhaust system RAM.

### Running Tests

To run an individual module or focused suite:

```bash
# Single module
.venv/bin/python -m pytest -q tests/test_core.py

# Agent tool suite
.venv/bin/python -m pytest -q tests/test_agent_tools.py tests/test_subagents.py
```

### UI Tests and Memory Guard

Tests creating GTK windows retain graphics buffers. On systems without swap space or with limited memory, run focused tests per process:

```bash
# Desktop portability suite
.venv/bin/python -m pytest -q tests/test_desktop_portability.py
```

Under CI (or using a local virtual display server):

```bash
xvfb-run -a .venv/bin/python -m pytest -q
```

---

## 4. Code Standards & Technical Vocabulary

- **Clarity and Didactics:** Write docstrings, comments, and documentation that explain the exact mechanism, prerequisites, and failure modes. Avoid buzzwords and vague summaries.
- **Technical Vocabulary:** Keep established software engineering and Linux terminology intact (*backend, frontend, pipeline, runtime, CLI, GUI, daemon, tool calling, dry-run, agent loop, subagents, scratchpad, fallback, bypass, sandbox, OSD, hotkey, token, barge-in, wake word, ROI, VAD, typelibs, toast, stepper, mock, fixture, compositor, layer-shell, overlay*).
- **Type Annotations:** Use Python type hints (`from __future__ import annotations`, collection types `list[str]`, `dict[str, Any]`, `Callable`, `Optional`).
- **Exception Handling:** Catch specific exceptions. D-Bus or subprocess errors should log detailed diagnostics and fall back gracefully whenever viable.

---

## 5. Git Workflow and Pull Requests

1. Create a focused branch off `main`:
   ```bash
   git checkout -b feat/feature-name
   # or
   git checkout -b fix/bug-description
   ```
2. Include automated tests covering the new behavior or regression.
3. Verify that the full test suite remains green:
   ```bash
   .venv/bin/python -m pytest -q
   ```
4. Write structured commit messages following Conventional Commits:
   - `feat(agent): add multi-window inspection`
   - `fix(a11y): handle null coordinates on unmapped wayland surfaces`
   - `docs(readme): clarify multi-desktop setup instructions`
5. Open a Pull Request completing all sections in `.github/pull_request_template.md`.

