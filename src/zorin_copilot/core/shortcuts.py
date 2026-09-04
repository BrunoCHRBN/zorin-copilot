# Decisão de design: Integração nativa de atalhos globais no GNOME / Zorin OS via
# org.gnome.settings-daemon.plugins.media-keys.custom-keybinding, garantindo compatibilidade
# oficial com Wayland sem necessidade de hooks em nível de root ou instabilidade no compositor.

"""Gerenciador de atalho global do sistema para o Zorin Copilot."""

from __future__ import annotations

import logging
import os
import shutil
from typing import Final

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

logger = logging.getLogger(__name__)

MEDIA_KEYS_SCHEMA: Final = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_KEY_SCHEMA: Final = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
COPILOT_BINDING_PATH: Final = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/zorin-copilot/"
COPILOT_BINDING_NAME: Final = "Zorin Copilot"


class ShortcutManager:
    """Gerencia o registro e remoção do atalho de teclado global do Copilot no GNOME/Zorin OS."""

    @classmethod
    def get_binary_command(cls) -> str:
        """Obtém o caminho executável do zorin-copilot com o argumento --toggle."""
        local_bin = os.path.expanduser("~/.local/bin/zorin-copilot")
        if os.path.exists(local_bin):
            return f"{local_bin} --toggle"
        which_bin = shutil.which("zorin-copilot")
        if which_bin:
            return f"{which_bin} --toggle"
        return "zorin-copilot --toggle"

    @classmethod
    def is_registered(cls) -> bool:
        """Verifica se o atalho do Zorin Copilot está atualmente cadastrado no GNOME."""
        try:
            settings = Gio.Settings.new(MEDIA_KEYS_SCHEMA)
            existing = list(settings.get_strv("custom-keybindings"))
            return COPILOT_BINDING_PATH in existing
        except Exception as exc:
            logger.warning(f"Não foi possível ler configurações de atalhos do GNOME: {exc}")
            return False

    @classmethod
    def get_current_binding(cls) -> str:
        """Retorna a combinação de teclas atualmente cadastrada (ex: '<Super>c')."""
        if not cls.is_registered():
            return ""
        try:
            custom_setting = Gio.Settings.new_with_path(CUSTOM_KEY_SCHEMA, COPILOT_BINDING_PATH)
            return custom_setting.get_string("binding")
        except Exception:
            return ""

    @classmethod
    def register(cls, binding: str = "<Super>c") -> bool:
        """Cadastra o atalho global no sistema operacional."""
        try:
            settings = Gio.Settings.new(MEDIA_KEYS_SCHEMA)
            existing = list(settings.get_strv("custom-keybindings"))
            if COPILOT_BINDING_PATH not in existing:
                existing.append(COPILOT_BINDING_PATH)
                settings.set_strv("custom-keybindings", existing)

            custom_setting = Gio.Settings.new_with_path(CUSTOM_KEY_SCHEMA, COPILOT_BINDING_PATH)
            custom_setting.set_string("name", COPILOT_BINDING_NAME)
            custom_setting.set_string("command", cls.get_binary_command())
            custom_setting.set_string("binding", binding)
            logger.info(f"Atalho global registrado com sucesso: {binding}")
            return True
        except Exception as exc:
            logger.error(f"Erro ao registrar atalho global do GNOME: {exc}")
            return False

    @classmethod
    def unregister(cls) -> bool:
        """Remove o atalho global do sistema operacional."""
        try:
            settings = Gio.Settings.new(MEDIA_KEYS_SCHEMA)
            existing = list(settings.get_strv("custom-keybindings"))
            if COPILOT_BINDING_PATH in existing:
                existing.remove(COPILOT_BINDING_PATH)
                settings.set_strv("custom-keybindings", existing)

            custom_setting = Gio.Settings.new_with_path(CUSTOM_KEY_SCHEMA, COPILOT_BINDING_PATH)
            custom_setting.set_string("name", "")
            custom_setting.set_string("command", "")
            custom_setting.set_string("binding", "")
            logger.info("Atalho global desregistrado com sucesso.")
            return True
        except Exception as exc:
            logger.error(f"Erro ao desregistrar atalho global do GNOME: {exc}")
            return False
