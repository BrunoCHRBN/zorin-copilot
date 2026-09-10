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
from typing import Final

from .env import Environment, current_environment

logger = logging.getLogger(__name__)

APP_ID = "io.github.bruno.ZorinCopilot"

#: Slot do bloco de autostart dentro do snippet do compositor.
SLOT_AUTOSTART: Final = "autostart"

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
    """Acrescenta a linha de execução ao snippet do compositor.

    O snippet e a diretiva saem do backend: no Hyprland com config Lua o arquivo
    é ``zorin-copilot.lua`` e a linha é um ``hl.exec_cmd(...)``, não um
    ``exec-once =``.
    """
    from .shortcuts import ensure_snippet_sourced, select_compositor_backend

    backend = select_compositor_backend(env)
    if backend is None:
        return False, "nenhum backend de compositor disponível para autostart"

    try:
        snippet = backend.write_block(SLOT_AUTOSTART, backend.autostart_directive(command))
    except OSError as exc:
        return False, f"Falha ao gravar o snippet do compositor: {exc}"

    # O snippet sozinho não faz nada: Hyprland/Sway só leem o config principal.
    # Sem esta linha o autostart fica "ativo" no relatório e morto no próximo
    # login. A regra é a mesma dos atalhos, por isso vem do mesmo lugar.
    sourced, source_msg = ensure_snippet_sourced(env.desktop, snippet, env=env)
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

    from .shortcuts import select_compositor_backend

    backend = select_compositor_backend(env)
    if backend is not None:
        try:
            if backend.remove_block(SLOT_AUTOSTART):
                removed.append("Compositor")
        except OSError as exc:
            return False, f"Falha ao limpar {backend.snippet_path()}: {exc}"

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

    from .shortcuts import select_compositor_backend

    backend = select_compositor_backend(env)
    if backend is None:
        return False
    snippet = backend.snippet_path()
    if not snippet.exists():
        return False
    marker = f"zorin-copilot:{SLOT_AUTOSTART}"
    return marker in snippet.read_text(encoding="utf-8", errors="ignore")
