# Decisão de design: atalhos globais saíram de uma implementação única presa ao GNOME Settings
# Daemon e passaram a ser despachados para `core.desktop.shortcuts`, que escolhe o backend pelo
# ambiente (GNOME media-keys, hyprland.conf, sway config, kglobalshortcutsrc).
#
# Esta classe continua sendo a porta de entrada — preserva a API usada pela UI, pela CLI e pelo
# instalador — mas deixa de mentir: `register()` devolve False e explica o motivo quando o
# ambiente não oferece mecanismo de atalho.

"""Gerenciador de atalho global do sistema para o Zorin Copilot."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

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
    AppShortcut("app.command-palette", "<Control>k", "Abrir o painel de comandos"),
    AppShortcut("app.export-conversation", "<Control>s", "Exportar a conversa como Markdown"),
    AppShortcut("app.undo-action", "<Control>z", "Desfazer a última ação reversível"),
)

#: Paths legados do GNOME, mantidos para quem já tem os atalhos registrados.
COPILOT_BINDING_PATH: Final = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/zorin-copilot/"
CROP_BINDING_PATH: Final = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/zorin-copilot-crop/"
VOICE_BINDING_PATH: Final = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/zorin-copilot-voice/"

#: Flags que cada slot executa.
_SLOT_FLAGS: Final[dict[str, str]] = {
    "hud": "--toggle",
    "crop": "--crop",
    "voice": "--voice",
}


class ShortcutManager:
    """Registra os atalhos globais usando o backend adequado ao ambiente.

    A API pública permanece a mesma (``register``, ``register_crop``,
    ``register_voice``, ``is_registered``...), mas o trabalho agora é feito pelo
    backend escolhido em :func:`zorin_copilot.core.desktop.shortcuts.select_backend`.
    """

    #: Última mensagem devolvida pelo backend — a UI mostra isso quando falha.
    last_message: str = ""

    @classmethod
    def get_binary_command(cls, flag: str = "--toggle") -> str:
        """Obtém o caminho executável do zorin-copilot com a flag informada."""
        from .desktop.shortcuts import resolve_binary

        return resolve_binary(flag)

    @classmethod
    def backend_name(cls) -> str:
        """Nome do backend ativo (útil no diagnóstico e na UI)."""
        from .desktop.shortcuts import select_backend

        return select_backend().name

    @classmethod
    def _register(cls, slot: str, binding: str) -> bool:
        from .desktop.shortcuts import select_backend

        backend = select_backend()
        result = backend.register(slot, binding, cls.get_binary_command(_SLOT_FLAGS[slot]))
        cls.last_message = result.message
        if not result.ok:
            logger.warning(f"Atalho '{slot}' não registrado: {result.message}")
        return result.ok

    @classmethod
    def _unregister(cls, slot: str) -> bool:
        from .desktop.shortcuts import select_backend

        result = select_backend().unregister(slot)
        cls.last_message = result.message
        return result.ok

    @classmethod
    def _is_registered(cls, slot: str) -> bool:
        from .desktop.shortcuts import select_backend

        try:
            return select_backend().is_registered(slot)
        except Exception:
            return False

    # -------------------------------------------------------------------------
    # Atalho do HUD Principal (Super+C)
    # -------------------------------------------------------------------------
    @classmethod
    def is_registered(cls) -> bool:
        """Verifica se o atalho do HUD está cadastrado no ambiente atual."""
        return cls._is_registered("hud")

    @classmethod
    def get_current_binding(cls) -> str:
        """Combinação configurada para o HUD (a fonte da verdade é a config)."""
        from .config import CopilotConfig

        return CopilotConfig.load().global_shortcut_key

    @classmethod
    def register(cls, binding: str = "<Super>c") -> bool:
        return cls._register("hud", binding)

    @classmethod
    def unregister(cls) -> bool:
        return cls._unregister("hud")

    # -------------------------------------------------------------------------
    # Atalho de recorte inteligente (Super+Shift+S)
    # -------------------------------------------------------------------------
    @classmethod
    def is_crop_registered(cls) -> bool:
        return cls._is_registered("crop")

    @classmethod
    def get_crop_binding(cls) -> str:
        from .config import CopilotConfig

        return CopilotConfig.load().crop_shortcut_key

    @classmethod
    def register_crop(cls, binding: str = "<Super><Shift>s") -> bool:
        return cls._register("crop", binding)

    @classmethod
    def unregister_crop(cls) -> bool:
        return cls._unregister("crop")

    # -------------------------------------------------------------------------
    # Atalho de conversa por voz (Super+Shift+V / Super+V)
    # -------------------------------------------------------------------------
    @classmethod
    def is_voice_registered(cls) -> bool:
        return cls._is_registered("voice")

    @classmethod
    def get_voice_binding(cls) -> str:
        from .config import CopilotConfig

        return CopilotConfig.load().voice_shortcut_key

    @classmethod
    def register_voice(cls, binding: str = "<Super><Shift>v") -> bool:
        return cls._register("voice", binding)

    @classmethod
    def unregister_voice(cls) -> bool:
        return cls._unregister("voice")


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
        """Verifica se o autostart está ativo, por qualquer mecanismo."""
        from .desktop.autostart import is_enabled

        try:
            return is_enabled()
        except Exception:
            return False

    @classmethod
    def enable(cls, binary_command: str | None = None) -> bool:
        """Habilita o autostart no XDG e, em wlroots, no config do compositor.

        Hyprland e Sway não leem ``~/.config/autostart`` — nesses casos gravamos
        também a linha ``exec-once`` / ``exec`` no snippet do compositor.
        """
        from .desktop.autostart import enable

        if not binary_command:
            binary_command = ShortcutManager.get_binary_command("--background")

        try:
            ok, message = enable(binary_command)
            logger.info(f"Autostart: {message}")
            return ok
        except Exception as exc:
            logger.error(f"Erro ao habilitar autostart do Zorin Copilot: {exc}")
            return False

    @classmethod
    def disable(cls) -> bool:
        """Desabilita o autostart em todos os mecanismos onde foi gravado."""
        from .desktop.autostart import disable

        try:
            ok, message = disable()
            logger.info(f"Autostart removido: {message}")
            return ok
        except Exception as exc:
            logger.error(f"Erro ao desabilitar autostart: {exc}")
            return False

