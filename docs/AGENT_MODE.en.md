# Agent Mode (Supervised Autonomous Desktop Execution)

[🇺🇸 English Version](AGENT_MODE.en.md) | [🇧🇷 Versão em Português](AGENT_MODE.md)

This document specifies the design and implementation of **Agent Mode** in Zorin Copilot: the capability to receive a high-level natural language objective (e.g., *"Open Firefox, look up X, and save the PDF summary to `~/Documents`"*) and execute it autonomously as a verified sequence of native desktop actions under **interactive confirmation, digital fencing, and undo protection**.

> **Status:** Phase 1 (CLI headless core) and Phase 2 (GTK4 Libadwaita UI / Stepper / Action Approval) are fully implemented.

---

## 1. Motivation and Architectural Context

Desktop automation often suffers from two extremes: brittle blind-scripting without perception, or slow, expensive visual loops that capture full-screen screenshots every second.

Zorin Copilot leverages the system's native accessibility bus (**AT-SPI2**) and compositor interfaces to deliver deterministic perception with negligible CPU overhead. Safety mechanisms are enforced directly at the tool registry layer:

| Pillar | Module | Role |
|---|---|---|
| Risk Policy | `shell/risk.py` | Evaluates `RiskPolicy.classify(tool, args) -> (SAFE \| CONFIRM)`. High-risk operations (file writes, shell execution, destructive hotkeys like `Alt+F4`) require explicit confirmation. |
| Digital Screen Fence | `core/fence.py` | `ScreenFenceManager.is_coordinate_allowed(x, y)` prevents mouse actions from escaping bounded display zones. |
| Undo Stack | `shell/undo.py` | Records filesystem snapshots prior to mutations, allowing immediate rollback via `Ctrl+Z`. |
| Audit Trail | `core/memory.py` | `log_action(...)` persists every step in SQLite `action_logs`. |

---

## 2. Core Design Goals

The agent core is designed around four strict engineering requirements:

1. **Zero Graphical Dependencies in the Core:** `ai/agent.py` imports only Python standard libraries and types. All operating system interaction is injected via `ToolRegistry`. This allows headless execution and fast unit testing without display servers or D-Bus launchers.
2. **Deterministic Dry-Run:** `--dry-run` utilizes synthetic test registries, preventing unplanned actions from touching the desktop.
3. **Approval Required for Risky Actions:** Actions flagged as `CONFIRM` trigger an `on_approval` callback. Without an active approval handler, the action is rejected by default.
4. **Explicit Termination States:** Execution loops terminate solely on documented stopping conditions, never on model whim.

---

## 3. Architecture & Execution Loop

```
                 CLI: zorin-copilot-cli agent "objective" [--dry-run] [--local-only]
                                   │
                                   ▼
                       ai/agent_router.py        (Deterministic routing)
                         ├─ local  (OllamaProvider / Qwen / Dolphin)
                         └─ cloud  (GeminiProvider / Claude) ── fallback / escalation
                                   │
                                   ▼
                         ai/agent.py :: AgentLoop
                         ├─ plan()            → list of steps (dry-run)
                         ├─ run(on_step, on_approval)
                         └─ abort()           → threading.Event
                                   │
                                   ▼
                       ai/agent_tools.py :: ToolRegistry
                         ├─ RiskPolicy.classify()     (shell/risk.py)
                         ├─ ScreenFenceManager        (core/fence.py)
                         ├─ UndoStack                 (shell/undo.py)
                         └─ DesktopInspector / FileManager / AppManager / InputDriver
                                   │
                                   ▼
                       MemoryManager.log_action()     (Audit record per step)
```

### 3.1 `ai/agent.py` — `AgentLoop`

A deterministic state machine:

```python
AgentLoop(router, registry, *, max_steps=12, max_seconds=180, max_repeats=2, policy=None)
```

| Method / Property | Behavior |
|---|---|
| `plan(objective) -> list[Step]` | Formulates a step sequence through the router without executing any actions. Returns step metadata including `risk` and `requires_approval`. |
| `run(objective, on_step=None, on_approval=None) -> AgentResult` | Executes steps sequentially; `on_step(step)` notifies observers, while `on_approval(step) -> bool` resolves confirmation for `CONFIRM` actions. |
| `abort()` | Signals immediate termination; the loop halts prior to the next step. |

**Stop Conditions (`AgentResult.stop_reason`):**

| `stop_reason` | Trigger |
|---|---|
| `done` | Model issued the `done` or `finish` tool call. |
| `max_steps` | Execution reached `len(steps) >= max_steps`. |
| `timeout` | Execution duration exceeded `max_seconds`. |
| `repeated_failure` | The **identical tool with identical arguments** failed twice consecutively. |
| `aborted` | User invoked `abort()`. |
| `rejected` | Action failed `RiskPolicy` evaluation or user denied interactive approval. |
| `error` | Unrecoverable provider or networking error. |
| `no_provider` | No active provider available matching the requested mode. |

---

### 3.2 `ai/agent_tools.py` — `ToolRegistry`

Maps tool names to callables along with JSON-Schema metadata for provider prompt injection. `call(name, args) -> dict` always returns a JSON-serializable dictionary without raising uncaught exceptions, ensuring the loop can record observations and continue safely.

Standard Tool Catalog:

| Tool | Effect | Risk |
|---|---|---|
| `get_ui_tree` | Retrieves summary accessibility tree of the focused window (or specified `app`). | safe |
| `find_element` | Locates element by name, role, or text label → returns uid and bounds. | safe |
| `click_element` | Semantic click via element uid (prefers AT-SPI action over raw coordinates). | safe |
| `type_text` | Synthesizes text input into the focused input widget. | safe |
| `mouse_click` | Dispatches mouse click by absolute/relative coordinate — **validated against fence**. | safe |
| `press_hotkey` | Sends keyboard shortcut (destructive combinations like `Alt+F4` flag as CONFIRM). | confirm* |
| `launch_app` | Launches an application binary with focus polling and `$PATH` fallback. | safe |
| `list_directory` | Lists filesystem directory entries. | safe |
| `read_file` | Reads text file content up to configured byte limits. | safe |
| `write_document` | Writes or appends file content — records snapshot in `UndoStack`. | **confirm** |
| `organize_directory`| Organizes folder contents — records rollback manifest in `UndoStack`. | **confirm** |
| `capture_screen` | Captures screen or cropped ROI for visual inspection. | safe |
| `undo_last` | Reverts the most recent reversible action from `UndoStack`. | safe |
| `done` / `finish` | Concludes the agent loop with the final summary response. | safe |

---

### 3.3 `ai/agent_router.py` — Local → Cloud Routing

Deterministic routing heuristics:

1. `--local-only` forces Ollama/local models; `--cloud` forces Gemini/Claude.
2. Short or deterministic commands (1–2 steps matching local verbs: open, click, type, list, screenshot) route to **local models**.
3. Multi-part compound requests (connectives like "and then", "afterwards", or research/synthesis requests) route to **cloud models**, falling back to local models upon failure.
4. `hybrid` configuration mode starts locally and escalates to cloud after two consecutive planning failures.

---

### 3.4 CLI Commands

```bash
# Preview plan without touching the desktop
zorin-copilot-cli agent "open Firefox and search for Linux kernel documentation" --dry-run

# Live execution with interactive terminal confirmation for risky actions
zorin-copilot-cli agent "organize ~/Downloads by file type" --max-steps 15

# Force local inference execution
zorin-copilot-cli agent "click Start button" --local-only --dry-run
```

---

### 3.5 GTK4 / Libadwaita Interface Integration

1. **PromptBar Toggle:** Dedicated circular icon button (`system-run-symbolic`), command palette entry (`Ctrl+K` -> *Toggle Agent Mode*), or slash command prefix (`/agent <task>`).
2. **Visual Stepper Widget (`AgentExecutionWidget`):** Live step updates powered by asynchronous GLib main context dispatchers.
3. **Step Inspection (`Revealer`):** Chevron dropdown reveals structured step observations (JSON/text), model rationale, and error messages.
4. **Inline Confirmation Banner:** Actions classified as `CONFIRM` surface an inline confirmation prompt with an "Approve All" option to authorize subsequent operations in the current session.
5. **Reversibility Action Card:** If disk operations occurred, a card footer button provides immediate rollback (`Ctrl+Z`).

