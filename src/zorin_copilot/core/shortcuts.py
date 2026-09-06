# Decisão de design: Integração nativa de atalhos globais no GNOME / Zorin OS via
# org.gnome.settings-daemon.plugins.media-keys.custom-keybinding, garantindo compatibilidade
# oficial com Wayland sem necessidade de hooks em nível de root ou instabilidade no compositor.

"""Gerenciador de atalho global do sistema para o Zorin Copilot."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Final

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

logger = logging.getLogger(__name__)

MEDIA_KEYS_SCHEMA: Final = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_KEY_SCHEMA: Final = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
COPILOT_BINDING_PATH: Final = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/zorin-copilot/"
COPILOT_BINDING_NAME: Final = "Zorin Copilot"

CROP_BINDING_PATH: Final = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/zorin-copilot-crop/"
CROP_BINDING_NAME: Final = "Zorin Copilot - Recorte Inteligente"

VOICE_BINDING_PATH: Final = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/zorin-copilot-voice/"
VOICE_BINDING_NAME: Final = "Zorin Copilot - Conversa por Voz"


class ShortcutManager:
    """Gerencia o registro e remoção dos atalhos de teclado globais do Copilot no GNOME/Zorin OS."""

    @classmethod
    def get_binary_command(cls, flag: str = "--toggle") -> str:
        """Obtém o caminho executável do zorin-copilot com a flag informada."""
        venv_bin = os.path.expanduser("~/.local/share/zorin-copilot/venv/bin/zorin-copilot")
        if os.path.exists(venv_bin):
            return f"{venv_bin} {flag}"
        local_bin = os.path.expanduser("~/.local/bin/zorin-copilot")
        if os.path.exists(local_bin):
            return f"{local_bin} {flag}"
        which_bin = shutil.which("zorin-copilot")
        if which_bin:
            return f"{which_bin} {flag}"
        return f"zorin-copilot {flag}"

    # -------------------------------------------------------------------------
    # Métodos internos genéricos de D-Bus para custom-keybinding
    # -------------------------------------------------------------------------
    @classmethod
    def _is_path_registered(cls, path: str) -> bool:
        try:
            settings = Gio.Settings.new(MEDIA_KEYS_SCHEMA)
            existing = list(settings.get_strv("custom-keybindings"))
            return path in existing
        except Exception as exc:
            logger.warning(f"Não foi possível ler configurações de atalhos do GNOME: {exc}")
            return False

    @classmethod
    def _get_binding_at_path(cls, path: str) -> str:
        if not cls._is_path_registered(path):
            return ""
        try:
            custom_setting = Gio.Settings.new_with_path(CUSTOM_KEY_SCHEMA, path)
            return custom_setting.get_string("binding")
        except Exception:
            return ""

    @classmethod
    def _register_binding(cls, path: str, name: str, command: str, binding: str) -> bool:
        try:
            settings = Gio.Settings.new(MEDIA_KEYS_SCHEMA)
            existing = list(settings.get_strv("custom-keybindings"))
            if path not in existing:
                existing.append(path)
                settings.set_strv("custom-keybindings", existing)

            custom_setting = Gio.Settings.new_with_path(CUSTOM_KEY_SCHEMA, path)
            custom_setting.set_string("name", name)
            custom_setting.set_string("command", command)
            custom_setting.set_string("binding", binding)
            logger.info(f"Atalho global '{name}' registrado com sucesso: {binding}")
            return True
        except Exception as exc:
            logger.error(f"Erro ao registrar atalho global '{name}' do GNOME: {exc}")
            return False

    @classmethod
    def _unregister_binding(cls, path: str) -> bool:
        try:
            settings = Gio.Settings.new(MEDIA_KEYS_SCHEMA)
            existing = list(settings.get_strv("custom-keybindings"))
            if path in existing:
                existing.remove(path)
                settings.set_strv("custom-keybindings", existing)

            custom_setting = Gio.Settings.new_with_path(CUSTOM_KEY_SCHEMA, path)
            custom_setting.set_string("name", "")
            custom_setting.set_string("command", "")
            custom_setting.set_string("binding", "")
            logger.info(f"Atalho global '{path}' desregistrado com sucesso.")
            return True
        except Exception as exc:
            logger.error(f"Erro ao desregistrar atalho global '{path}' do GNOME: {exc}")
            return False

    # -------------------------------------------------------------------------
    # Atalho do HUD Principal (Ctrl+Space / Super+Z / Super+C)
    # -------------------------------------------------------------------------
    @classmethod
    def is_registered(cls) -> bool:
        """Verifica se o atalho do HUD do Copilot está atualmente cadastrado no GNOME."""
        return cls._is_path_registered(COPILOT_BINDING_PATH)

    @classmethod
    def get_current_binding(cls) -> str:
        """Retorna a combinação de teclas atualmente cadastrada para o HUD."""
        return cls._get_binding_at_path(COPILOT_BINDING_PATH)

    @classmethod
    def register(cls, binding: str = "<Super>c") -> bool:
        """Cadastra o atalho global do HUD no sistema operacional."""
        return cls._register_binding(
            path=COPILOT_BINDING_PATH,
            name=COPILOT_BINDING_NAME,
            command=cls.get_binary_command("--toggle"),
            binding=binding,
        )

    @classmethod
    def unregister(cls) -> bool:
        """Remove o atalho global do HUD do sistema operacional."""
        return cls._unregister_binding(COPILOT_BINDING_PATH)

    # -------------------------------------------------------------------------
    # Atalho Global Direto de Recorte Inteligente (Super+Shift+S)
    # -------------------------------------------------------------------------
    @classmethod
    def is_crop_registered(cls) -> bool:
        """Verifica se o atalho de recorte inteligente está atualmente cadastrado."""
        return cls._is_path_registered(CROP_BINDING_PATH)

    @classmethod
    def get_crop_binding(cls) -> str:
        """Retorna a combinação de teclas atualmente cadastrada para recorte."""
        return cls._get_binding_at_path(CROP_BINDING_PATH)

    @classmethod
    def register_crop(cls, binding: str = "<Super><Shift>s") -> bool:
        """Cadastra o atalho global de recorte inteligente no sistema operacional."""
        return cls._register_binding(
            path=CROP_BINDING_PATH,
            name=CROP_BINDING_NAME,
            command=cls.get_binary_command("--crop"),
            binding=binding,
        )

    @classmethod
    def unregister_crop(cls) -> bool:
        """Remove o atalho global de recorte inteligente do sistema operacional."""
        return cls._unregister_binding(CROP_BINDING_PATH)

    # -------------------------------------------------------------------------
    # Atalho Global Direto de Conversa por Voz (Super+Shift+V)
    # -------------------------------------------------------------------------
    @classmethod
    def is_voice_registered(cls) -> bool:
        """Verifica se o atalho de conversa por voz está atualmente cadastrado."""
        return cls._is_path_registered(VOICE_BINDING_PATH)

    @classmethod
    def get_voice_binding(cls) -> str:
        """Retorna a combinação de teclas atualmente cadastrada para conversa por voz."""
        return cls._get_binding_at_path(VOICE_BINDING_PATH)

    @classmethod
    def register_voice(cls, binding: str = "<Super><Shift>v") -> bool:
        """Cadastra o atalho global de conversa por voz no sistema operacional."""
        return cls._register_binding(
            path=VOICE_BINDING_PATH,
            name=VOICE_BINDING_NAME,
            command=cls.get_binary_command("--voice"),
            binding=binding,
        )

    @classmethod
    def unregister_voice(cls) -> bool:
        """Remove o atalho global de conversa por voz do sistema operacional."""
        return cls._unregister_binding(VOICE_BINDING_PATH)


class AutostartManager:
    """Gerencia a inicialização automática do Zorin Copilot com o sistema operacional."""

    APP_ID: Final = "io.github.bruno.ZorinCopilot"

    @classmethod
    def get_autostart_dir(cls) -> Path:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        autostart_dir = Path(base) / "autostart"
        autostart_dir.mkdir(parents=True, exist_ok=True)
        return autostart_dir

    @classmethod
    def get_autostart_file(cls) -> Path:
        return cls.get_autostart_dir() / f"{cls.APP_ID}.desktop"

    @classmethod
    def is_enabled(cls) -> bool:
        """Verifica se o autostart está ativo no sistema."""
        desktop_file = cls.get_autostart_file()
        if not desktop_file.exists():
            return False
        try:
            content = desktop_file.read_text(encoding="utf-8")
            if "X-GNOME-Autostart-enabled=false" in content:
                return False
            return "Exec=" in content
        except Exception:
            return False

    @classmethod
    def enable(cls, binary_command: str | None = None) -> bool:
        """Habilita o Zorin Copilot para iniciar com o sistema em segundo plano."""
        try:
            if not binary_command:
                binary_command = ShortcutManager.get_binary_command("--background")
            elif not binary_command.endswith("--background"):
                binary_command = f"{binary_command} --background"

            desktop_file = cls.get_autostart_file()
            content = f"""[Desktop Entry]
Type=Application
Version=1.0
Name=Zorin Copilot
GenericName=Assistente de IA
Comment=Assistente de IA integrado ao desktop Zorin OS
Exec={binary_command}
Icon=system-help-symbolic
Terminal=false
Categories=Utility;GTK;GNOME;
StartupNotify=false
X-GNOME-Autostart-enabled=true
"""
            desktop_file.write_text(content, encoding="utf-8")
            logger.info(f"Autostart do Zorin Copilot habilitado em: {desktop_file}")
            return True
        except Exception as exc:
            logger.error(f"Erro ao habilitar autostart do Zorin Copilot: {exc}")
            return False

    @classmethod
    def disable(cls) -> bool:
        """Desabilita o autostart removendo o arquivo desktop de inicialização."""
        try:
            desktop_file = cls.get_autostart_file()
            if desktop_file.exists():
                desktop_file.unlink()
            logger.info("Autostart do Zorin Copilot desabilitado.")
            return True
        except Exception as exc:
            logger.error(f"Erro ao desabilitar autostart: {exc}")
            return False

