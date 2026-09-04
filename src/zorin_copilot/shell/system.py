# Decisão de design: comandos de sistema usam gsettings e utilitários nativos (wpctl/pipewire) — mudanças aplicam na hora sem reiniciar sessão.

"""Controlador de configurações do sistema: áudio, temas, brilho e tela."""

from __future__ import annotations

import shutil
import subprocess


class SystemController:
    """Aplica controles e ajustes rápidos no ambiente Zorin OS."""

    @staticmethod
    def set_color_scheme(dark: bool) -> tuple[bool, str]:
        scheme = "prefer-dark" if dark else "prefer-light"
        theme_hint = "Modo escuro" if dark else "Modo claro"
        try:
            res = subprocess.run(
                ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", scheme],
                check=False,
                capture_output=True,
            )
            if res.returncode == 0:
                return True, f"{theme_hint} ativado com sucesso."
            return False, "Falha ao alterar o esquema de cores."
        except Exception as exc:
            return False, f"Erro: {exc}"

    @staticmethod
    def toggle_night_light(enable: bool | None = None) -> tuple[bool, str]:
        schema = "org.gnome.settings-daemon.plugins.color"
        key = "night-light-enabled"
        try:
            if enable is None:
                cur = subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True, check=False)
                enable = "true" not in cur.stdout.lower()

            val = "true" if enable else "false"
            res = subprocess.run(["gsettings", "set", schema, key, val], check=False)
            status = "ativada" if enable else "desativada"
            return True, f"Luz noturna {status}."
        except Exception as exc:
            return False, f"Erro: {exc}"

    @staticmethod
    def adjust_volume(change: str) -> tuple[bool, str]:
        """Ajusta volume via wpctl (PipeWire) ou pactl."""
        wpctl = shutil.which("wpctl")
        if wpctl:
            if change == "up":
                cmd = [wpctl, "set-volume", "@DEFAULT_AUDIO_SINK@", "5%+"]
                msg = "Volume aumentado em 5%."
            elif change == "down":
                cmd = [wpctl, "set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"]
                msg = "Volume reduzido em 5%."
            elif change == "mute":
                cmd = [wpctl, "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"]
                msg = "Áudio mutado/desmutado."
            else:
                return False, "Operação de volume inválida."

            try:
                res = subprocess.run(cmd, check=False)
                return (res.returncode == 0, msg)
            except Exception as exc:
                return False, f"Falha no wpctl: {exc}"

        return False, "Controlador de áudio PipeWire (wpctl) não encontrado."

    @staticmethod
    def lock_session() -> tuple[bool, str]:
        loginctl = shutil.which("loginctl")
        if loginctl:
            try:
                subprocess.run([loginctl, "lock-session"], check=False)
                return True, "Sessão bloqueada."
            except Exception as exc:
                return False, f"Falha ao bloquear: {exc}"
        return False, "loginctl não disponível."

    @staticmethod
    def take_screenshot() -> tuple[bool, str]:
        # Atalho de captura de tela no GNOME 46
        try:
            # Chama o visualizador interativo do GNOME Shell via D-Bus
            subprocess.run(["gnome-screenshot", "-i"], check=False)
            return True, "Ferramenta de captura aberta."
        except Exception:
            return False, "gnome-screenshot não disponível."
