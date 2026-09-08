# Decisão de design: a bandeja original dependia de AppIndicator3/Ayatana, que é GTK3 + XEmbed
# — duas coisas que não existem numa sessão Wayland. Além disso ela continha um bug de import
# (`from gi.repository import importlib`) que desativava o recurso em *qualquer* distro.
#
# O protocolo que funciona de verdade em Wayland (e que waybar, Plasma e o GNOME com extensão
# implementam) é o StatusNotifierItem, falado direto no D-Bus de sessão. Sem GTK3.

"""Ícone de bandeja via StatusNotifierItem (SNI) sobre D-Bus.

Funciona em waybar (Hyprland/Sway), Plasma e no GNOME com extensão de bandeja.
Onde não houver um *watcher*, o ícone simplesmente não aparece — e
:meth:`StatusNotifierTray.is_supported` diz isso antes, para a UI não prometer o
que não vai entregar.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)

SNI_WATCHER_BUS: str = "org.kde.StatusNotifierWatcher"
SNI_WATCHER_PATH: str = "/StatusNotifierWatcher"
SNI_ITEM_IFACE: str = "org.kde.StatusNotifierItem"

SNI_XML: str = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Id" type="s" access="read"/>
    <property name="Category" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="IconAccessibleDesc" type="s" access="read"/>
    <signal name="NewIcon"/>
    <signal name="NewStatus">
      <arg type="s" name="status"/>
    </signal>
    <method name="Activate">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="Scroll">
      <arg type="i" name="delta" direction="in"/>
      <arg type="s" name="orientation" direction="in"/>
    </method>
  </interface>
</node>
"""


def watcher_present() -> bool:
    """Existe um watcher de bandeja na sessão (waybar, Plasma, extensão GNOME)?"""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
    except (ImportError, ValueError):
        return False

    try:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = conn.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ListNames",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            1000,
            None,
        )
        return SNI_WATCHER_BUS in set(reply.unpack()[0])
    except Exception:
        return False


class StatusNotifierTray:
    """Ícone de bandeja SNI puro D-Bus, sem GTK3.

    ``on_activate`` é chamado no clique principal (abrir/ocultar o HUD);
    ``on_secondary`` no clique secundário, quando a barra o suporta.
    """

    CATEGORY: str = "ApplicationStatus"
    STATUS: str = "Active"

    def __init__(
        self,
        app_id: str = "io.github.bruno.ZorinCopilot",
        title: str = "Zorin Copilot",
        icon_name: str = "dialog-information-symbolic",
        on_activate: Callable[[], None] | None = None,
        on_secondary: Callable[[], None] | None = None,
    ):
        self.app_id = app_id
        self.title = title
        self.icon_name = icon_name
        self.on_activate = on_activate
        self.on_secondary = on_secondary

        self._owner_id: int = 0
        self._registration_id: int = 0
        self._conn: Any = None
        self._bus_name: str = ""
        self._active = False

    @classmethod
    def is_supported(cls) -> bool:
        return watcher_present()

    @property
    def is_active(self) -> bool:
        return self._active

    def setup(self) -> bool:
        """Publica o item e registra no watcher. Falso se não for possível."""
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio
        except (ImportError, ValueError) as exc:
            logger.info(f"SNI indisponível (sem Gio): {exc}")
            return False

        # Nome de barramento único por processo. Se dois Copilots rodarem ao mesmo
        # tempo, o segundo fica sem ícone — o que é o comportamento correto.
        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        try:
            self._owner_id = Gio.bus_own_name(
                Gio.BusType.SESSION,
                self._bus_name,
                Gio.BusNameOwnerFlags.NONE,
                self._on_bus_acquired,
                None,
                self._on_name_lost,
            )
        except Exception as exc:
            logger.warning(f"Falha ao publicar ícone de bandeja: {exc}")
            return False

        return True

    # ------------------------------------------------------------------
    # Ciclo de vida do barramento
    # ------------------------------------------------------------------
    def _on_bus_acquired(self, conn, name, *args) -> None:
        from gi.repository import GLib

        self._conn = conn
        try:
            node_info = GLib.DBusNodeInfo.new_for_xml(SNI_XML)
            iface_info = node_info.lookup_interface(SNI_ITEM_IFACE)
            self._registration_id = conn.register_object(
                "/StatusNotifierItem",
                iface_info,
                self._handle_method,
                self._handle_get_property,
                None,
            )
        except Exception as exc:
            logger.warning(f"Falha ao registrar objeto SNI: {exc}")
            return

        self._register_with_watcher()
        self._active = True
        logger.info("Ícone de bandeja registrado via StatusNotifierItem.")

    def _on_name_lost(self, conn, name, *args) -> None:
        self._active = False

    def _register_with_watcher(self) -> None:
        if not self._conn:
            return
        try:
            from gi.repository import Gio, GLib

            self._conn.call_sync(
                SNI_WATCHER_BUS,
                SNI_WATCHER_PATH,
                SNI_WATCHER_BUS,
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (self._bus_name,)),
                None,
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
        except Exception as exc:
            # Sem watcher não há bandeja: o app segue funcionando, só sem ícone.
            logger.info(f"Nenhum watcher de bandeja ativo: {exc}")

    # ------------------------------------------------------------------
    # Handlers D-Bus
    # ------------------------------------------------------------------
    def _handle_method(self, conn, sender, path, iface, method, params, invocation, *args) -> None:
        if method == "Activate":
            self._fire(self.on_activate)
        elif method == "SecondaryActivate":
            self._fire(self.on_secondary or self.on_activate)
        elif method != "Scroll":
            logger.debug(f"Método SNI desconhecido ignorado: {method}")
        invocation.return_value(None)

    def _handle_get_property(self, conn, sender, path, iface, prop, *args):
        from gi.repository import GLib

        string_props = {
            "Id": self.app_id,
            "Category": self.CATEGORY,
            "Status": self.STATUS,
            "Title": self.title,
            "IconName": self.icon_name,
            "IconThemePath": "",
            "Menu": "/",  # sem menu D-Bus: a barra cai no Activate
            "IconAccessibleDesc": self.title,
        }
        if prop in string_props:
            return GLib.Variant("s", string_props[prop])
        if prop == "ItemIsMenu":
            return GLib.Variant("b", False)
        return None

    @staticmethod
    def _fire(callback: Callable[[], None] | None) -> None:
        if not callback:
            return
        try:
            callback()
        except Exception as exc:
            logger.error(f"Erro no callback da bandeja: {exc}")

    def set_icon(self, icon_name: str) -> None:
        """Troca o ícone e avisa as barras (NewIcon)."""
        self.icon_name = icon_name
        self._emit_signal("NewIcon")

    def set_status(self, status: str) -> None:
        self._emit_signal("NewStatus", status)

    def _emit_signal(self, name: str, *payload: str) -> None:
        if not self._conn or not self._active:
            return
        try:
            from gi.repository import GLib

            body = GLib.Variant("(s)", (payload[0],)) if payload else None
            self._conn.emit_signal(None, "/StatusNotifierItem", SNI_ITEM_IFACE, name, body)
        except Exception as exc:
            logger.debug(f"Falha ao emitir {name}: {exc}")

    def teardown(self) -> None:
        """Remove o ícone e libera o nome no barramento."""
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio
        except (ImportError, ValueError):
            return

        if self._registration_id and self._conn:
            try:
                self._conn.unregister_object(self._registration_id)
            except Exception:
                pass
        if self._owner_id:
            try:
                Gio.bus_unown_name(self._owner_id)
            except Exception:
                pass
        self._active = False
