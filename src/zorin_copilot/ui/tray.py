# Decisão de design: a bandeja anterior usava AppIndicator3/Ayatana — GTK3 + XEmbed, duas coisas
# que não existem em Wayland — e ainda tinha `from gi.repository import importlib`, um import de
# módulo stdlib pelo namespace do GI que estourava sempre e era engolido por um except nu.
# Resultado: a bandeja nunca funcionou, em distro nenhuma.
#
# O caminho único agora é StatusNotifierItem (`core.desktop.tray`) com menu
# `com.canonical.dbusmenu` (`core.desktop.menu`). SNI também cobre X11 (Plasma X11 e
# qualquer watcher), então manter o AppIndicator só custaria: ele exige
# `gi.require_version("Gtk", "3.0")`, o que é incompatível com o Gtk4 que o resto do
# app fixa — o PyGObject recusa duas versões do mesmo namespace no mesmo processo.
#
# Este módulo não importa Gtk: é D-Bus puro.

"""Indicador de bandeja do sistema para o Zorin Copilot."""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _deferred(callback: Callable[[], None]) -> Callable[[], None]:
    """Envolve um callback para rodar na main loop, com fallback a chamada direta.

    O barramento D-Bus invoca o handler fora da thread principal; mexer em widget
    GTK de lá é pedir por corrupção de estado.
    """

    def wrapper() -> None:
        try:
            import gi

            gi.require_version("GLib", "2.0")
            from gi.repository import GLib

            GLib.idle_add(_safe, callback)
        except Exception:
            _safe(callback)

    return wrapper


def _safe(callback: Callable[[], None]) -> bool:
    try:
        callback()
    except Exception as exc:
        logger.error(f"Erro na ação da bandeja: {exc}")
    return False  # GLib.SOURCE_REMOVE


class SystemTrayIndicator:
    """Ícone de bandeja via StatusNotifierItem, com menu D-Bus completo.

    Cada callback opcional vira uma entrada no menu; callbacks ausentes simplesmente
    não aparecem, em vez de virarem itens inúteis e desabilitados.
    """

    # Rótulos e ícones ficam aqui para a UI não precisar saber de D-Bus.
    LABEL_HUD = "Abrir Zorin Copilot"
    LABEL_CROP = "Recorte Inteligente"
    LABEL_SYNC = "Sincronizar Documentos (RAG)"
    LABEL_PREFS = "Preferências do Sistema…"
    LABEL_KILL = "Kill Switch (Bloquear Ações Físicas)"
    LABEL_QUIT = "Sair do Copilot"

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

        # Estado do toggle do kill switch, refletido no menu quando a barra suporta.
        self.kill_switch_active: bool = False

        self._sni: Any = None
        self._menu: Any = None
        self._is_active = False

    # ---------------------------------------------------------------- suporte
    @classmethod
    def is_available(cls) -> bool:
        """Existe um watcher de bandeja nesta sessão (waybar, Plasma, extensão GNOME)?"""
        from ..core.desktop.tray import watcher_present

        return watcher_present()

    @property
    def is_active(self) -> bool:
        return self._is_active

    # ------------------------------------------------------------------ setup
    def setup(self) -> bool:
        """Publica ícone + menu. Falso quando não há watcher nem barramento."""
        from ..core.desktop.menu import DbusMenu
        from ..core.desktop.tray import StatusNotifierTray

        self._menu = DbusMenu(self._build_items())
        sni = StatusNotifierTray(
            on_activate=self.on_toggle_hud,
            on_secondary=self.on_preferences or self.on_toggle_hud,
            menu=self._menu,
        )
        if not sni.setup():
            self._menu = None
            logger.info(
                "Sem bandeja disponível; o Copilot segue acessível pelos atalhos globais."
            )
            return False

        self._sni = sni
        self._is_active = True
        return True

    def _build_items(self) -> list[Any]:
        """Monta a árvore do menu a partir dos callbacks recebidos."""
        spec: list[dict[str, Any]] = []
        next_id = 1

        def add(label: str, callback: Callable[[], None] | None, icon: str = "", **extra: Any):
            nonlocal next_id
            if callback is None:
                return
            entry: dict[str, Any] = {"id": next_id, "label": label, "icon": icon}
            entry.update(extra)
            # O D-Bus despacha na sua própria thread; devolvemos a chamada para a
            # main loop do GTK antes de tocar em widget.
            entry["on_clicked"] = _deferred(callback)
            spec.append(entry)
            next_id += 1

        def separator():
            nonlocal next_id
            spec.append({"id": next_id, "separator": True})
            next_id += 1

        add(self.LABEL_HUD, self.on_toggle_hud, "zorin-copilot-symbolic")
        add(self.LABEL_CROP, self.on_crop, "edit-select-all-symbolic")
        add(self.LABEL_SYNC, self.on_sync_rag, "folder-download-symbolic")
        if self.on_preferences or self.on_kill_switch:
            separator()
        add(self.LABEL_PREFS, self.on_preferences, "preferences-system-symbolic")
        add(
            self.LABEL_KILL,
            self.on_kill_switch,
            "security-high-symbolic",
            toggle=self.kill_switch_active,
        )
        if self.on_quit:
            separator()
        add(self.LABEL_QUIT, self.on_quit, "application-exit-symbolic")

        # Sem callback nenhum o menu sairia vazio; aí é melhor não publicar menu
        # e deixar a barra no Activate puro.
        return [_item_from(entry) for entry in spec]

    # ------------------------------------------------------------- atualização
    def refresh_menu(self) -> None:
        """Reconstrói o menu (ex.: depois de alternar o kill switch)."""
        if self._menu is None:
            return
        self._menu.set_items(self._build_items())

    def set_kill_switch_active(self, active: bool) -> None:
        """Alterna o visto do item de kill switch no menu da bandeja."""
        self.kill_switch_active = bool(active)
        self.refresh_menu()

    def set_icon(self, icon_name: str) -> None:
        if self._sni is not None:
            self._sni.set_icon(icon_name)

    def teardown(self) -> None:
        """Remove o ícone da bandeja ao encerrar."""
        if self._sni is not None:
            self._sni.teardown()
            self._sni = None
        self._menu = None
        self._is_active = False


def _item_from(entry: dict[str, Any]) -> Any:
    """Converte um dict do spec em :class:`MenuItem`."""
    from ..core.desktop.menu import MenuItem

    if entry.get("separator"):
        return MenuItem(id=entry["id"], type="separator")
    children = [_item_from(child) for child in entry.get("children") or []]
    toggle = entry.get("toggle")
    return MenuItem(
        id=entry["id"],
        label=entry.get("label", ""),
        icon_name=entry.get("icon", ""),
        enabled=bool(entry.get("enabled", True)),
        toggle_type="checkmark" if toggle is not None else "",
        toggle_state=1 if toggle else 0,
        children_display="submenu" if children else "",
        children=children,
        on_clicked=entry.get("on_clicked"),
    )
