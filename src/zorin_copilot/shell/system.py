# Decisão de design: os controles de sistema eram gsettings do GNOME + gnome-screenshot, e em
# qualquer outro ambiente devolviam "Falha ao alterar o esquema de cores" sem explicação.
# A lógica por ambiente foi para `core.desktop.controls`; esta classe é a fachada.

"""Controlador de configurações do sistema: áudio, temas, bloqueio e captura."""

from __future__ import annotations

from ..core.desktop.controls import (
    adjust_volume as _adjust_volume,
    lock_session as _lock_session,
    notify as _notify,
    open_screenshot_tool as _open_screenshot_tool,
    set_color_scheme as _set_color_scheme,
)


class SystemController:
    """Aplica controles rápidos no ambiente, usando o mecanismo disponível."""

    @staticmethod
    def set_color_scheme(dark: bool) -> tuple[bool, str]:
        """Alterna entre tema claro e escuro pelo mecanismo do ambiente."""
        return _set_color_scheme(dark)

    @staticmethod
    def toggle_night_light(enable: bool | None = None) -> tuple[bool, str]:
        """Luz noturna. Sem equivalente fora do GNOME; degrada com honestidade."""
        import shutil
        import subprocess

        if not shutil.which("gsettings"):
            return False, "Luz noturna depende do GNOME; não disponível neste ambiente."

        schema = "org.gnome.settings-daemon.plugins.color"
        key = "night-light-enabled"
        try:
            if enable is None:
                cur = subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True, check=False)
                enable = "true" not in cur.stdout.lower()

            val = "true" if enable else "false"
            res = subprocess.run(["gsettings", "set", schema, key, val], check=False)
            if res.returncode != 0:
                return False, "Falha ao alterar a luz noturna."
            return True, f"Luz noturna {'ativada' if enable else 'desativada'}."
        except Exception as exc:
            return False, f"Erro: {exc}"

    @staticmethod
    def adjust_volume(change: str) -> tuple[bool, str]:
        """Ajusta volume via wpctl (PipeWire), pactl ou amixer."""
        return _adjust_volume(change)

    @staticmethod
    def lock_session() -> tuple[bool, str]:
        """Bloqueia a sessão (hyprlock/swaylock/loginctl, nesta ordem)."""
        return _lock_session()

    @staticmethod
    def take_screenshot() -> tuple[bool, str]:
        """Abre a ferramenta de captura do ambiente (substitui gnome-screenshot)."""
        return _open_screenshot_tool()

    @staticmethod
    def notify(title: str, message: str) -> tuple[bool, str]:
        """Envia notificação de desktop."""
        return _notify(title, message)
