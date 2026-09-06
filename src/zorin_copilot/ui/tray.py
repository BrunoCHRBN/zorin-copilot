# Decisão de design: indicador de bandeja do sistema (Tray Icon / StatusNotifierItem) para o Zorin OS.
# Fornece acesso rápido às funções do assistente (HUD, Recorte, Sincronização de Documentos, Preferências e Kill Switch)
# quando o Zorin Copilot estiver operando em segundo plano (--background).

"""Indicador de bandeja do sistema para o Zorin Copilot."""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

HAS_APP_INDICATOR = False
AppIndicatorModule = None

for mod_name in ("AyatanaAppIndicator3", "AppIndicator3"):
    try:
        import gi
        gi.require_version(mod_name, "0.1")
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk as Gtk3
        from gi.repository import importlib
        AppIndicatorModule = __import__(f"gi.repository.{mod_name}", fromlist=[mod_name])
        HAS_APP_INDICATOR = True
        break
    except Exception:
        pass


class SystemTrayIndicator:
    """Gerencia o ícone e menu de contexto na bandeja do sistema operacional."""

    def __init__(
        self,
        on_toggle_hud: Callable[[], None] | None = None,
        on_crop: Callable[[], None] | None = None,
        on_sync_rag: Callable[[], None] | None = None,
        on_preferences: Callable[[], None] | None = None,
        on_kill_switch: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
    ):
        self.on_toggle_hud = on_toggle_hud
        self.on_crop = on_crop
        self.on_sync_rag = on_sync_rag
        self.on_preferences = on_preferences
        self.on_kill_switch = on_kill_switch
        self.on_quit = on_quit
        self.indicator = None
        self._is_active = False

    @classmethod
    def is_available(cls) -> bool:
        """Indica se as bibliotecas de AppIndicator estão disponíveis no sistema."""
        return HAS_APP_INDICATOR

    def setup(self) -> bool:
        """Inicializa o ícone e menu da bandeja se suportado pelo ambiente desktop."""
        if not HAS_APP_INDICATOR or AppIndicatorModule is None:
            logger.info("AyatanaAppIndicator indisponível; o assistente continuará via atalhos globais.")
            return False

        try:
            from gi.repository import Gtk as Gtk3

            category = AppIndicatorModule.IndicatorCategory.APPLICATION_STATUS
            self.indicator = AppIndicatorModule.Indicator.new(
                "io.github.bruno.ZorinCopilot",
                "dialog-information-symbolic",
                category,
            )
            self.indicator.set_status(AppIndicatorModule.IndicatorStatus.ACTIVE)
            self.indicator.set_title("Zorin Copilot")

            # Cria o menu de contexto
            menu = Gtk3.Menu()

            # 1. Abrir Copilot
            item_hud = Gtk3.MenuItem(label="Abrir Zorin Copilot (Super + C)")
            if self.on_toggle_hud:
                item_hud.connect("activate", lambda _: self.on_toggle_hud())
            menu.append(item_hud)

            # 2. Recorte Inteligente
            item_crop = Gtk3.MenuItem(label="Recorte Inteligente (Super + Shift + S)")
            if self.on_crop:
                item_crop.connect("activate", lambda _: self.on_crop())
            menu.append(item_crop)

            # 3. Sincronizar Documentos RAG
            item_sync = Gtk3.MenuItem(label="Sincronizar Documentos (RAG)")
            if self.on_sync_rag:
                item_sync.connect("activate", lambda _: self.on_sync_rag())
            menu.append(item_sync)

            menu.append(Gtk3.SeparatorMenuItem())

            # 4. Preferências
            item_prefs = Gtk3.MenuItem(label="Preferências do Sistema...")
            if self.on_preferences:
                item_prefs.connect("activate", lambda _: self.on_preferences())
            menu.append(item_prefs)

            # 5. Kill Switch
            item_kill = Gtk3.MenuItem(label="Kill Switch (Bloquear Ações Físicas)")
            if self.on_kill_switch:
                item_kill.connect("activate", lambda _: self.on_kill_switch())
            menu.append(item_kill)

            menu.append(Gtk3.SeparatorMenuItem())

            # 6. Sair
            item_quit = Gtk3.MenuItem(label="Sair do Copilot")
            if self.on_quit:
                item_quit.connect("activate", lambda _: self.on_quit())
            menu.append(item_quit)

            menu.show_all()
            self.indicator.set_menu(menu)
            self._is_active = True
            logger.info("SystemTrayIndicator registrado com sucesso na bandeja do Zorin OS.")
            return True

        except Exception as exc:
            logger.warning(f"Erro ao inicializar SystemTrayIndicator: {exc}")
            return False
