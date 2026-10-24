# Test Suite Stability — Diagnostic and Resolution

[🇺🇸 English Version](ESTABILIDADE_TESTES.en.md) | [🇧🇷 Versão em Português](ESTABILIDADE_TESTES.md)

> **Reported symptom:** Tests froze indefinitely or triggered system reboots during execution on developer machines.
>
> **Investigation summary:** Two independent failure modes were identified and resolved: out-of-memory crashes and unclosed pipe deadlocks.

---

## 1. Summary of Results

| Metric | Before | After |
|---|---|---|
| Complete suite execution | OOM killer triggered reboot; 20+ min hangs | **108 modules in ~89 s** |
| Memory peak per module | ~3.3 GB (30 GTK windows × 109.5 MB) | **488 MB** |
| Isolation under test runners | None (read live user config & database) | Active sandbox (`_sandbox.py`) |
| Silent hangs | Frequent, no logs | **Zero** |

---

## 2. Failure Mode 1 — Memory Exhaustion (OOM Kernel Panic)

### Evidence

```
journalctl -k -b -1  ->  systemd invoked oom-killer
                         Out of memory: Killed process 2666 (electron/python)
free -h              ->  Swap: 0B (15 GiB RAM, no swap partition)
```

### Root Cause Analysis

By default, every instance of `CopilotWindow` initializes three heavy background services:
1. **MCP Servers:** On-demand Node/Python child processes spawned via `uvx`/`npx`.
2. **RAG Vector Indexer:** Disk scans across `~/Documents` and `~/Downloads`.
3. **Wake Word Engine:** Active audio capture streams via `vosk`.

Measured resource cost **per window** including child process trees:

| Metric | Test Gate OFF | Test Gate ON |
|---|---|---|
| RAM delta per window | **+109.5 MB** | **+2.9 MB** |
| Processes spawned per window | **+1.80** | **+0.00** |
| Surviving threads post-destroy | 16 | 1 |

In tests like `test_ui_refactor.py`, which create 30 window fixtures in a single test run:
```
30 × 109.5 MB ≈ 3.3 GB RAM and 54 child processes
```
On workstations without swap space, the Linux OOM killer terminated the desktop session.

### The Fix

1. `_is_test_environment()` in `ui/app.py` was updated to accurately detect all test runners and environment flags (`ZORIN_TEST_MODE`, `PYTEST_CURRENT_TEST`, or process inspection).
2. `CopilotWindow.teardown_background_services()` was made idempotent, terminating MCP daemons, RAG threads, and audio buffers upon window disposal.
3. Overrode the `Gtk.Window.destroy()` method directly, because GTK4's `destroy` signal is not emitted by default on window destruction.
4. `LocalDocumentRAG.cancel_indexing()` introduced cooperative thread cancellation.

---

## 3. Failure Mode 2 — Pipe Deadlocks and Orphaned Subprocesses

### Root Cause Analysis

Clipboard and notification tests invoking external Wayland helpers (`wl-copy`, `notify-send`) left open file descriptors connected to standard output/error pipes. Subprocesses waiting on input pipes deadlocked when test runners attempted to join child threads.

### The Fix

1. Virtualized desktop clipboard operations and notification dispatching in test mode.
2. Introduced timeout watchdogs on all process wrappers.
3. Created `tests/_sandbox.py` to intercept filesystem access and point `XDG_CONFIG_HOME` / `XDG_DATA_HOME` to isolated `/tmp/zorin-test-...` directories.

---

## 4. Best Practices for Developers

1. **Always use `pytest`:** Run `.venv/bin/python -m pytest -q`.
2. **Execute focused modules during development:**
   ```bash
   .venv/bin/python -m pytest -q tests/test_core.py
   ```
3. **Verify sandbox activation:** Tests must never alter `~/.config/zorin-copilot/config.json` or `~/.local/share/zorin-copilot/memory.db`.

