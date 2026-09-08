# Decisão de design: a bandeja anterior usava AppIndicator3/Ayatana — GTK3 + XEmbed, duas coisas
# que não existem em Wayland — e ainda tinha `from gi.repository import importlib`, um import de
# módulo stdlib pelo namespace do GI que estourava sempre e era engolido por um except nu.
# Resultado: a bandeja nunca funcionou, em distro nenhuma.
#
# Agora o caminho principal é StatusNotifierItem puro D-Bus (`core.desktop.tray`), que é o que
# waybar/Plasma/GNOME-com-extensão entendem. O AppIndicator fica só como alternativa em X11.

"""Indicador de bandeja do sistema para o Zorin Copilot."""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

HAS_APP_INDICATOR = False
AppIndicatorModule = None

# AppIndicator3 roda em GTK3/XEmbed: só vale a pena em sessão X11.
try:  # pragma: no cover - depende do ambiente
    import os

    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "x11":
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk as Gtk3  # noqa: F401

        for _mod_name in ("AyatanaAppIndicator3", "AppIndicator3"):
            try:
                gi.require_version(_mod_name, "0.1")
                AppIndicatorModule = __import__(f"gi.repository.{_mod_name}", fromlist=[_mod_name])
                HAS_APP_INDICATOR = True
                break
            except Exception:
                continue
except Exception:
    HAS_APP_INDICATOR = False


class SystemTrayIndicator:
    """Ícone de bandeja, preferindo StatusNotifierItem (Wayland) a AppIndicator (X11).

    Mantém o menu completo quando há AppIndicator; com SNI a barra controla o menu
    e o clique principal chama ``on_toggle_hud``.
    """

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
        self._sni = None
        self._is_active = False

    @classmethod
    def is_available(cls) -> bool:
        """Existe algum mecanismo de bandeja utilizável nesta sessão?"""
        from ..core.desktop.tray import watcher_present

        return HAS_APP_INDICATOR or watcher_present()

    def setup(self) -> bool:
        """Tenta StatusNotifierItem primeiro; cai para AppIndicator em X11."""
        if self._setup_sni():
            return True
        if HAS_APP_INDICATOR:
            return self._setup_app_indicator()
        logger.info("Sem bandeja disponível; o Copilot seguirá acessível pelos atalhos globais.")
        return False

    # ------------------------------------------------------------------ SNI
    def _setup_sni(self) -> bool:
        from ..core.desktop.tray import StatusNotifierTray

        sni = StatusNotifierTray(
            on_activate=self.on_toggle_hud,
            on_secondary=self.on_preferences,
        )
        if not sni.setup():
            return False
        # setup() publica o nome de forma assíncrona; só marcamos ativo se o
        # barramento foi adquirido de fato.
        self._sni = sni
        self._is_active = True
        return True

    # --------------------------------------------------------- AppIndicator
    def _setup_app_indicator(self) -> bool:  # pragma: no cover - só em X11
        if AppIndicatorModule is None:
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

            menu = Gtk3.Menu()

            item_hud = Gtk3.MenuItem(label="Abrir Zorin Copilot")
            if self.on_toggle_hud:
                item_hud.connect("activate", lambda _: self.on_toggle_hud())
            menu.append(item_hud)

            item_crop = Gtk3.MenuItem(label="Recorte Inteligente")
            if self.on_crop:
                item_crop.connect("activate", lambda _: self.on_crop())
            menu.append(item_crop)

            item_sync = Gtk3.MenuItem(label="Sincronizar Documentos (RAG)")
            if self.on_sync_rag:
                item_sync.connect("activate", lambda _: self.on_sync_rag())
            menu.append(item_sync)

            menu.append(Gtk3.SeparatorMenuItem())

            item_prefs = Gtk3.MenuItem(label="Preferências do Sistema...")
            if self.on_preferences:
                item_prefs.connect("activate", lambda _: self.on_preferences())
            menu.append(item_prefs)

            item_kill = Gtk3.MenuItem(label="Kill Switch (Bloquear Ações Físicas)")
            if self.on_kill_switch:
                item_kill.connect("activate", lambda _: self.on_kill_switch())
            menu.append(item_kill)

            menu.append(Gtk3.SeparatorMenuItem())

            item_quit = Gtk3.MenuItem(label="Sair do Copilot")
            if self.on_quit:
                item_quit.connect("activate", lambda _: self.on_quit())
            menu.append(item_quit)

            menu.show_all()
            self.indicator.set_menu(menu)
            self._is_active = True
            logger.info("SystemTrayIndicator registrado via AppIndicator.")
            return True
        except Exception as exc:
            logger.warning(f"Erro ao inicializar AppIndicator: {exc}")
            return False

    @property
    def is_active(self) -> bool:
        return self._is_active

    def teardown(self) -> None:
        """Remove o ícone da bandeja ao encerrar."""
        if self._sni is not None:
            self._sni.teardown()
            self._sni = None
        self._is_active = False
