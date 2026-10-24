# Zorin Copilot

<p align="center">
  <strong>🇺🇸 English Version</strong> &nbsp;|&nbsp; <a href="README.md">🇧🇷 Versão em Português</a>
</p>

<p align="center">
  <a href="https://github.com/BrunoCHRBN/zorin-copilot/actions/workflows/ci.yml"><img src="https://github.com/BrunoCHRBN/zorin-copilot/actions/workflows/ci.yml/badge.svg" alt="CI Status"></a>
  <img src="https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue.svg" alt="Python Versions">
  <img src="https://img.shields.io/badge/GTK-4.0%20%2B%20Libadwaita-green.svg" alt="GTK4">
  <img src="https://img.shields.io/badge/Wayland-GNOME%20%7C%20KDE%20%7C%20Hyprland%20%7C%20Sway-orange.svg" alt="Wayland">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT License">
</p>

---

## 📌 Overview

**Zorin Copilot** is a native, high-fidelity AI assistant built specifically for the Linux desktop. It solves a fundamental flaw in conventional desktop AI assistants: the latency, excessive resource usage, and visual brittleness of approaches based solely on continuous full-screen screenshots and OCR.

Instead of treating the desktop display as an opaque static image, Zorin Copilot directly queries native operating system introspection buses (**AT-SPI2**, **D-Bus**, and Wayland compositor protocols). This enables semantic identification of buttons, text entries, active windows, and menu trees with sub-millisecond overhead and minimal CPU load, invoking multimodal Vision Language Models (VLM) strictly on-demand for specific Regions of Interest (ROI).

The software provides three complementary interfaces:
- **Graphical User Interface (GUI):** A GTK4 and Libadwaita floating window with a custom glassmorphism design in a Spotlight / Raycast layout.
- **Command Line Interface (CLI):** `zorin-copilot-cli` for automated desktop tasks, accessibility introspection, and system diagnostics.
- **HY4 Terminal Client (`hy4`):** A dedicated command-line client for the Tencent HY4 model via WorkBuddy AI with real-time thinking process visualization.

---

## 🎯 Key Capabilities

* **Semantic Interface Introspection (AT-SPI2):** Directly parses widget trees across GTK, Qt, Electron, and browser windows (Firefox/Chromium) without continuous OCR polling.
* **Supervised Desktop Agent Mode:** Autonomous execution of multi-step tasks featuring a step-by-step progress stepper, action review, and an immediate emergency stop mechanism.
* **Specialized Subagents & Working Scratchpad:** Dynamic division of labor across specialized subagents (`window_analyst`, `research`, `code`) sharing a structured scratchpad.
* **System-Wide Voice Typing (Dictation):** Push-to-talk speech-to-text in any focused application via `<Super><Shift>D` or `--dictate`, powered by local INT8 Whisper transcription, Voice Activity Detection (VAD), and virtual input synthesis via `libei` / `ydotool`.
* **Bidirectional Live Voice:** Real-time continuous voice conversation with natural barge-in support, local neural speech synthesis via Piper TTS, and offline wake word activation via Vosk.
* **Academic Hub & Socratic Tutor:** Region-based screen capture for problem sets, step-by-step guidance, and mathematical chalkboard rendering with KaTeX/LaTeX support.
* **Safety Sandbox & Reversibility:** Spatial monitor boundaries (`ScreenFenceManager`), deterministic risk classification (`RiskPolicy`), and pre-execution state snapshots (`UndoStack`) with `Ctrl+Z` rollback.
* **Productivity Integrations:** First-class integrations for Google Workspace (Calendar, Drive, Gmail via OAuth2 Desktop app with Secret Service keyring storage), Home Assistant (real-time WebSocket state events), and n8n / MCP automations.

---

## 🖥️ Desktop Compatibility Matrix

Zorin Copilot implements modular abstractions across compositors and desktop environments:

| Desktop Environment | Session Type | Window Manager | Screen Capture | Global Shortcuts |
|---|---|---|---|---|
| **Zorin OS 18 / GNOME 46+** | Wayland / X11 | Mutter | XDG Desktop Portal | `gsettings` (media-keys) |
| **KDE Plasma 6** | Wayland / X11 | KWin | Spectacle / Portal | `kglobalshortcutsrc` |
| **Hyprland** | Wayland | Hyprland | `grim` + `slurp` | Config snippet in `hyprland.conf` |
| **Sway** | Wayland (wlroots) | Sway | `grim` + `slurp` | Config snippet in `sway/config` |

---

## 🏗️ Architecture Summary

The codebase is structured in a five-layer decoupled architecture. For technical specifications, see [ARCHITECTURE.en.md](ARCHITECTURE.en.md).

```
┌─────────────────────────────────────────────────────────────┐
│                    Zorin Copilot UI                         │
│       (GTK4 / Libadwaita · Layer-Shell · OSD Pill)          │
└──────────────┬───────────────────────────────┬──────────────┘
               │                               │
               ▼                               ▼
    [Perception Core]                 [Shell & Execution]
    • AT-SPI2 (A11y Hierarchy)        • Input Drivers (libei / ydotool)
    • D-Bus Active Window Monitor     • RiskPolicy (SAFE / CONFIRM)
    • VLM Grounding & Continuous ROI  • ScreenFenceManager (Zones)
    • Local Voice (Whisper INT8)      • UndoStack (Reversibility)
               │                               │
               └───────────────┬───────────────┘
                               │
                               ▼
                   [Intelligence Layer]
                   • AgentLoop & Subagents (Scratchpad)
                   • Fast RNN Router (Local vs Cloud)
                   • Providers: Ollama / Gemini / Claude / HY4
```

---

## 🚀 Installation & Setup

### Method 1: Automated Setup Script (Recommended)

The `setup.sh` script detects your Linux distribution, installs missing packages, prepares the virtual environment with appropriate system site-package inheritance, and registers global shortcuts:

```bash
# Clone the repository
git clone https://github.com/BrunoCHRBN/zorin-copilot.git ~/zorin-copilot
cd ~/zorin-copilot

# Run the setup script
./setup.sh
```

### Method 2: Manual Installation

Zorin Copilot requires host PyGObject bindings to load native GTK4 and Libadwaita typelibs. The virtual environment **must** be created using `--system-site-packages`:

#### 1. Install System Dependencies

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

#### 2. Configure Python Virtual Environment

```bash
cd ~/zorin-copilot
python3 -m venv --system-site-packages .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e . --no-deps
.venv/bin/pip install requests beautifulsoup4 websockets
```

#### 3. Install Optional Dependency Groups

```bash
# Local neural voice support (faster-whisper and piper-tts)
.venv/bin/pip install faster-whisper piper-tts

# Offline wake word recognition (vosk)
.venv/bin/pip install vosk

# Google Workspace integrations (OAuth2 Desktop app)
.venv/bin/pip install google-api-python-client google-auth-oauthlib google-auth keyring

# Office document generation (.docx and .pptx)
.venv/bin/pip install python-docx python-pptx
```

---

## 💻 Usage Guide

### 1. Graphical User Interface (GUI)

Launch the spotlight-style floating window:

```bash
zorin-copilot
```

Or trigger the registered system hotkey (`Super + C` or `Super + Space`).

### 2. Command Line Interface (`zorin-copilot-cli`)

The CLI provides complete access to core features for automation, scripting, and system auditing:

```bash
# Run system diagnostic checks for AT-SPI2, Wayland, and D-Bus integrity
zorin-copilot-cli doctor

# Inspect accessibility widget trees for the focused desktop window
zorin-copilot-cli inspect

# Run Agent Mode in dry-run mode (formulates plan without executing)
zorin-copilot-cli agent "open web browser and search for Linux kernel documentation" --dry-run

# Run Agent Mode live (prompts for confirmation on high-risk operations)
zorin-copilot-cli agent "organize ~/Downloads by file extension" --max-steps 15

# Query personal documents using local semantic retrieval (RAG)
zorin-copilot-cli rag "what was the total amount on the electricity invoice?"

# Capture on-screen exercise region and initiate a Socratic tutoring session
zorin-copilot-cli study capture
```

### 3. Dedicated HY4 Terminal Client (`hy4`)

Interact directly with the Tencent HY4 model from any terminal:

```bash
# Query HY4 displaying the internal chain-of-thought stream
hy4 "How can I reduce D-Bus roundtrip latency in GNOME Mutter?"

# Raw plain-text output without thinking process (ideal for shell pipes)
hy4 --no-think --raw "List 5 essential journalctl commands"
```

---

## ⌨️ Keyboard Shortcuts

| Shortcut | Action | Scope |
|---|---|---|
| `Super + C` | Toggle main Copilot HUD window | System-wide |
| `Super + Shift + D` | Start / Stop system-wide Voice Typing (Dictation) | System-wide |
| `Super + Shift + S` | Capture screen region for context inspection | System-wide |
| `Super + Shift + V` | Initiate bidirectional live voice session | System-wide |
| `Ctrl + K` | Open command palette (fuzzy search actions) | Copilot Window |
| `Ctrl + Z` | Undo most recent reversible filesystem action | Copilot Window |
| `Ctrl + S` | Export active conversation thread as Markdown | Copilot Window |
| `Ctrl + N` | Start new conversation session | Copilot Window |
| `Ctrl + H` | Toggle history sidebar visibility | Copilot Window |
| `Ctrl + Q` | Terminate application process | Copilot Window |

---

## ↩️ Safety, Reversibility, and Guardrails

Zorin Copilot executes verified actions on a live operating system. Several runtime boundaries protect user data:

1. **Reversible Actions (Pre-Execution Snapshots):**
   - Writing or appending to text files: restores prior content or deletes created files upon rollback.
   - Directory reorganization: moves relocated files back to original paths and purges newly created empty directories.
2. **Non-Reversible Actions (Strict User Confirmation):**
   - Simulated mouse clicks, text typing, shell script execution, and process termination modify external state. `RiskPolicy` flags these as `CONFIRM`, mandating explicit user authorization before dispatch.
3. **Digital Screen Fence (`ScreenFenceManager`):**
   - Bounds automated cursor actions and vision queries to designated displays or geometry coordinates, protecting private workspace areas.

---

## 🧪 Test Suite & Environment Sandbox

The test suite enforces environment isolation to avoid runaway memory consumption:

```bash
# Execute unit and integration tests with pytest
.venv/bin/python -m pytest -q
```

> [!IMPORTANT]
> **Always run `pytest`, never `python -m unittest`:**
> The repository uses both legacy `unittest.TestCase` structures and native `pytest` test functions. The `unittest` runner discards pytest functions with exit code 5, masking unexecuted tests.
>
> The test sandbox (`tests/_sandbox.py` and `tests/conftest.py`) isolates `XDG_CONFIG_HOME` and `XDG_DATA_HOME` to temporary directories and enforces `ZORIN_TEST_MODE=1`. This prevents test runs from modifying production configurations or spawning orphaned background services.

For full stability diagnostics, refer to [`docs/ESTABILIDADE_TESTES.en.md`](docs/ESTABILIDADE_TESTES.en.md).

---

## 🎨 Visual Customization (CSS)

The default glassmorphism styling is located at `src/zorin_copilot/data/zorin-copilot.css`. You can override stylesheet rules without modifying source code by creating `~/.config/zorin-copilot/user.css`:

```css
/* Example custom theme overrides in ~/.config/zorin-copilot/user.css */
@define-color accent_color #7d5fff;

window.glass-window > contents {
    background-color: rgba(20, 24, 32, 0.98);
}
```

Modifying `@accent_color` dynamically re-tints buttons, active sidebar selections, and focus indicators across the application.

---

## 📚 Technical Documentation

Visit [`docs/`](docs/) for in-depth documentation:
- [Central Documentation Index](docs/README.en.md)
- [Technical Architecture Specification](ARCHITECTURE.en.md)
- [Supervised Agent Mode Reference](docs/AGENT_MODE.en.md)
- [Arch Linux & EndeavourOS Porting Guide](docs/ENDEAVOUROS_PORT.md)
- [Google OAuth2 Setup Guide](docs/google-oauth-setup.md)
- [Home Assistant Integration Guide](docs/CASA_INTELIGENTE.md)
- [Contributing Guidelines](CONTRIBUTING.en.md)
- [Security Policy](SECURITY.en.md)

---

## 📄 License

Distributed under the **MIT** License. See `LICENSE` for details.

