# Security Policy — Zorin Copilot

[🇺🇸 English Version](SECURITY.en.md) | [🇧🇷 Versão em Português](SECURITY.md)

Because **Zorin Copilot** interacts directly with operating system buses, virtual input synthesis, on-screen content reading, and local file management, security is treated as a first-class architectural requirement.

---

## 1. Reporting Vulnerabilities

If you discover a security vulnerability that could lead to data compromise, permission evasion, or policy bypass:

1. **Do not create a public issue.**
2. Send a detailed report directly to the core maintainer or open a private Security Advisory on GitHub.
3. Your report should include:
   - Technical description of the attack vector or validation flaw.
   - Proof of Concept (PoC) or deterministic steps to reproduce.
   - Target environment details (Linux distribution, Wayland compositor / X11, Zorin Copilot version).
   - Assessment of potential impact.

---

## 2. Defense Layers and Containment Mechanisms

Zorin Copilot enforces four distinct layers of runtime defense:

### 2.1 Digital Screen Fence (`ScreenFenceManager`)
- **Module:** `src/zorin_copilot/core/fence.py`
- **Mechanism:** Enforces strict spatial constraints based on absolute screen coordinates and monitor boundaries. Clicks, mouse motions, or widget inspections targeting coordinates outside permitted boundaries (or within declared restricted red zones) are rejected prior to dispatching input.
- **Emergency Stop:** A global abort signal or hotkey immediately severs virtual input dispatchers (`libei` / `ydotool`).

### 2.2 Risk Classification Policy (`RiskPolicy`)
- **Module:** `src/zorin_copilot/shell/risk.py`
- **Mechanism:** Every tool call proposed by the model is deterministically evaluated before execution:
  - `SAFE`: Read-only queries, accessibility tree inspections, and passive state checks.
  - `CONFIRM`: Operations that modify state, execute shell commands, alter file content, kill processes, send communications, or trigger destructive shortcuts (`Alt+F4`, `Ctrl+Q`).
- In headless CLI sessions without an explicit approval callback (`--dry-run` or absence of `--execute`), any action marked as `CONFIRM` is blocked by default.

### 2.3 Reversibility Stack (`UndoStack`)
- **Module:** `src/zorin_copilot/shell/undo.py`
- **Mechanism:** Actions mutating local filesystem paths or organizing folders create pre-execution state snapshots. Files exceeding 1 MB or binary files bypass in-memory text snapshots to prevent corruption, mandating explicit confirmation instead.

### 2.4 Secure Credential Storage
- **Module:** `src/zorin_copilot/core/google_auth.py`
- **Mechanism:** OAuth2 tokens and API keys integrate with the Linux Secret Service daemon (via `keyring`). When system keyring daemons are unavailable, credentials fall back to restricted POSIX `0600` files inside the user configuration directory (`~/.config/zorin-copilot/`).

