# Decisão de design: Integração nativa de atalhos globais no GNOME / Zorin OS via
# org.gnome.settings-daemon.plugins.media-keys.custom-keybinding, garantindo compatibilidade
# oficial com Wayland sem necessidade de hooks em nível de root ou instabilidade no compositor.

"""Gerenciador de atalho global do sistema para o Zorin Copilot."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from typing import Final

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppShortcut:
    """Atalho interno da janela (escopo de aplicação).

    Diferente dos atalhos globais de sistema (que vivem no GNOME via GSettings),
    estes só têm efeito enquanto a janela do Copilot está aberta e focada.
    """

    name: str
    accelerator: str
    description: str


#: Atalhos de aplicação declarados em um único lugar, para que possam ser
#: reutilizados na instalação dos controllers e futuramente numa janela de
#: referência ("cheatsheet") exibida ao usuário.
APP_SHORTCUTS: Final[tuple[AppShortcut, ...]] = (
    AppShortcut("app.quit", "<Control>q", "Sair do Zorin Copilot"),
    AppShortcut("app.toggle-live-voice", "<Control>m", "Iniciar/encerrar conversa por voz"),
    AppShortcut("app.toggle-sidebar", "<Control>h", "Mostrar/ocultar a barra de conversas"),
    AppShortcut("app.new-topic", "<Control>n", "Iniciar uma nova conversa"),
    AppShortcut("app.toggle-pin", "<Control>p", "Fixar a conversa atual no topo"),
)

MEDIA_KEYS_SCHEMA: Final = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_KEY_SCHEMA: Final = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
COPILOT_BINDING_PATH: Final = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/zorin-copilot/"
COPILOT_BINDING_NAME: Final = "Zorin Copilot"

CROP_BINDING_PATH: Final = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/zorin-copilot-crop/"
CROP_BINDING_NAME: Final = "Zorin Copilot - Recorte Inteligente"


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
