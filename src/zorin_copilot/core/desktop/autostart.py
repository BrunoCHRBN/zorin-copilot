# Decisão de design: o autostart por ~/.config/autostart é um contrato do XDG que o GNOME e o
# Plasma honram — mas o Hyprland e o Sway não leem esse diretório sozinhos. Neles, o que inicia
# junto com a sessão é uma linha `exec-once` / `exec` no config.
#
# Em vez de escolher um, fazemos os dois: gravamos o .desktop (para ambientes que entendem XDG)
# e, em compositores wlroots, também a linha no snippet do compositor. Sem duplicar processo:
# o .desktop só é honrado onde o compositor não o lê, e vice-versa.

"""Inicialização automática por ambiente."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .env import Environment, current_environment

logger = logging.getLogger(__name__)

APP_ID = "io.github.bruno.ZorinCopilot"

_DESKTOP_TEMPLATE = """[Desktop Entry]
Type=Application
Version=1.0
Name=Zorin Copilot
GenericName=Assistente de IA
Comment=Assistente de IA integrado ao desktop
Exec={command}
Icon=system-help-symbolic
Terminal=false
Categories=Utility;GTK;
StartupNotify=false
X-GNOME-Autostart-enabled=true
"""


def autostart_dir() -> Path:
    """Diretório XDG de autostart (respeita ``XDG_CONFIG_HOME``)."""
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    path = Path(base) / "autostart"
    path.mkdir(parents=True, exist_ok=True)
    return path


def autostart_file() -> Path:
    return autostart_dir() / f"{APP_ID}.desktop"


def enable(command: str, env: Environment | None = None) -> tuple[bool, str]:
    """Habilita o autostart por todos os mecanismos aplicáveis ao ambiente."""
    env = env or current_environment()
    if "--background" not in command:
        command = f"{command} --background"

    try:
        autostart_file().write_text(_DESKTOP_TEMPLATE.format(command=command), encoding="utf-8")
    except OSError as exc:
        return False, f"Falha ao gravar {autostart_file()}: {exc}"

    messages = [f"XDG: {autostart_file()}"]

    if env.desktop in ("hyprland", "sway"):
        ok, msg = _enable_compositor(env, command)
        messages.append(msg)
        return ok, " · ".join(messages)

    return True, " · ".join(messages)


def _enable_compositor(env: Environment, command: str) -> tuple[bool, str]:
    """Acrescenta a linha de execução ao snippet do compositor."""
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    if env.desktop == "hyprland":
        config_dir = base / "hypr"
        snippet = config_dir / "zorin-copilot.conf"
        line = f"exec-once = {command}"
    else:
        config_dir = base / "sway"
        snippet = config_dir / "zorin-copilot.conf"
        line = f"exec {command}"

    begin, end = "# >>> zorin-copilot:autostart", "# <<< zorin-copilot:autostart"
    block = f"{begin}\n{line}\n{end}\n"

    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        existing = snippet.read_text(encoding="utf-8") if snippet.exists() else ""
    except OSError as exc:
        return False, f"Falha ao ler {snippet}: {exc}"

    if begin in existing:
        import re

        pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
        updated = re.sub(pattern, block, existing)
    else:
        updated = f"{existing}\n{block}" if existing else block

    try:
        snippet.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return False, f"Falha ao gravar {snippet}: {exc}"

    # O snippet sozinho não faz nada: Hyprland/Sway só leem o config principal.
    # Sem esta linha o autostart fica "ativo" no relatório e morto no próximo
    # login. A regra é a mesma dos atalhos, por isso vem do mesmo lugar.
    from .shortcuts import ensure_snippet_sourced

    sourced, source_msg = ensure_snippet_sourced(env.desktop, snippet)
    location = f"Compositor: {snippet}"
    if not sourced:
        return True, f"{location} — ATENÇÃO: {source_msg}"
    if source_msg:
        return True, f"{location} · {source_msg}"

    return True, location


def disable(env: Environment | None = None) -> tuple[bool, str]:
    """Remove o autostart dos dois mecanismos."""
    env = env or current_environment()
    removed = []

    path = autostart_file()
    if path.exists():
        try:
            path.unlink()
            removed.append("XDG")
        except OSError as exc:
            return False, f"Falha ao remover {path}: {exc}"

    if env.desktop in ("hyprland", "sway"):
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        snippet = base / env.desktop / "zorin-copilot.conf"
        if snippet.exists():
            import re

            begin, end = "# >>> zorin-copilot:autostart", "# <<< zorin-copilot:autostart"
            try:
                content = snippet.read_text(encoding="utf-8")
                pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
                snippet.write_text(re.sub(pattern, "", content), encoding="utf-8")
                removed.append("Compositor")
            except OSError as exc:
                return False, f"Falha ao limpar {snippet}: {exc}"

    return True, (", ".join(removed) or "nada a remover")


def is_enabled(env: Environment | None = None) -> bool:
    env = env or current_environment()
    path = autostart_file()
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8")
            if "X-GNOME-Autostart-enabled=false" not in content and "Exec=" in content:
                return True
        except OSError:
            pass

    if env.desktop in ("hyprland", "sway"):
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        snippet = base / env.desktop / "zorin-copilot.conf"
        if snippet.exists() and "zorin-copilot:autostart" in snippet.read_text(encoding="utf-8", errors="ignore"):
            return True

    return False
