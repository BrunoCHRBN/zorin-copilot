# Decisão de design: atalhos globais via org.freedesktop.portal.GlobalShortcuts.
# Backend agnóstico de compositor — funciona onde o xdg-desktop-portal correspondente
# implementa o portal (Hyprland via xdg-desktop-portal-hyprland, KDE, dde, gnome).
# Substitui o registro GNOME media-keys quando o ambiente não é GNOME/Zorin.

"""Backend de atalhos globais via XDG GlobalShortcuts portal."""

from __future__ import annotations

import logging
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

logger = logging.getLogger(__name__)

PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_IFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_IFACE = "org.freedesktop.portal.Request"


class PortalShortcutManager:
    """Registra atalhos globais via portal e despacha para um callback.

    Exemplo:
        mgr = PortalShortcutManager(lambda sid: print("pressed", sid))
        if mgr.start():
            ...  # atalhos ativos; os sinais chegam no loop principal do GTK
    """

    APP_ID = "io.github.bruno.ZorinCopilot"

    # (id, descrição PT, gatilho preferido)
    SHORTCUTS = [
        ("toggle-hud", "Abrir ou fechar o Zorin Copilot", "<Super>c"),
        ("crop", "Recorte inteligente de tela", "<Super><Shift>s"),
        ("voice", "Conversa por voz ao vivo", "<Super><Shift>v"),
    ]

    def __init__(self, on_activated: Callable[[str], None]):
        self.on_activated = on_activated
        self.available = False
        self._bus: Gio.DBusConnection | None = None
        self._session_handle: str | None = None
        self._subs: list[int] = []

    # ------------------------------------------------------------------ #
    def start(self) -> bool:
        """Cria a sessão, registra os atalhos e inscreve o sinal Activated."""
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as exc:
            logger.warning(f"GlobalShortcuts: sem D-Bus de sessão: {exc}")
            return False

        # 1) CreateSession -> obtém o session_handle real na resposta
        try:
            req = self._call("CreateSession", GLib.Variant("(a{sv})", ({},)), "(o)")
        except Exception as exc:
            logger.warning(f"GlobalShortcuts: CreateSession falhou: {exc}")
            return False
        session = self._wait_handle_response(req.unpack()[0])
        if not session:
            logger.warning("GlobalShortcuts: CreateSession não retornou session_handle.")
            return False
        self._session_handle = session

        # 2) BindShortcuts
        shortcuts = GLib.Variant(
            "a(sssv)",
            [(sid, desc, trig, trig) for sid, desc, trig in self.SHORTCUTS],
        )
        try:
            bind_req = self._call(
                "BindShortcuts",
                GLib.Variant("(oa(sssv)ss)", (session, shortcuts, "", self.APP_ID)),
                "(o)",
            )
        except Exception as exc:
            logger.warning(f"GlobalShortcuts: BindShortcuts falhou: {exc}")
            return False
        if not self._wait_simple_response(bind_req.unpack()[0]):
            logger.warning("GlobalShortcuts: BindShortcuts não confirmou.")
            return False

        # 3) Inscreve Activated na sessão (entregue no loop principal)
        self._subs.append(
            self._bus.signal_subscribe(
                PORTAL_NAME, PORTAL_IFACE, "Activated", session, None,
                Gio.DBusSignalFlags.NONE, self._on_activated_signal, None,
            )
        )
        self.available = True
        logger.info("GlobalShortcuts portal ativo com %d atalhos.", len(self.SHORTCUTS))
        return True

    # ------------------------------------------------------------------ #
    def _call(self, method: str, payload: GLib.Variant, rettype: str) -> GLib.Variant:
        return self._bus.call_sync(
            PORTAL_NAME, PORTAL_PATH, PORTAL_IFACE, method, payload,
            GLib.VariantType(rettype), Gio.DBusCallFlags.NONE, 15000, None,
        )

    def _wait_handle_response(self, req_path: str) -> str | None:
        result: dict[str, str | None] = {"session_handle": None}
        loop = GLib.MainLoop()

        def on_resp(_conn, _sender, _path, _iface, _signal, params, _user):
            try:
                resp_code, results = params.unpack()
                if resp_code == 0 and "session_handle" in results:
                    result["session_handle"] = results["session_handle"]
            except Exception as exc:
                logger.debug(f"GlobalShortcuts: erro ao ler CreateSessionResponse: {exc}")
            finally:
                loop.quit()

        sub = self._bus.signal_subscribe(
            PORTAL_NAME, REQUEST_IFACE, "Response", req_path, None,
            Gio.DBusSignalFlags.NONE, on_resp, None,
        )
        timeout = GLib.timeout_add_seconds(15, loop.quit)
        try:
            loop.run()
        finally:
            self._bus.signal_unsubscribe(sub)
            GLib.source_remove(timeout)
        return result["session_handle"]

    def _wait_simple_response(self, req_path: str) -> bool:
        ok: list[bool] = [False]
        loop = GLib.MainLoop()

        def on_resp(_conn, _sender, _path, _iface, _signal, params, _user):
            try:
                resp_code, _results = params.unpack()
                ok[0] = (resp_code == 0)
            except Exception:
                pass
            finally:
                loop.quit()

        sub = self._bus.signal_subscribe(
            PORTAL_NAME, REQUEST_IFACE, "Response", req_path, None,
            Gio.DBusSignalFlags.NONE, on_resp, None,
        )
        timeout = GLib.timeout_add_seconds(15, loop.quit)
        try:
            loop.run()
        finally:
            self._bus.signal_unsubscribe(sub)
            GLib.source_remove(timeout)
        return ok[0]

    def _on_activated_signal(self, _conn, _sender, _path, _iface, _signal, params, _user) -> None:
        try:
            _session, shortcut_id, _ts, _opts = params.unpack()
            logger.info(f"GlobalShortcuts: atalho ativado: {shortcut_id}")
            self.on_activated(shortcut_id)
        except Exception as exc:
            logger.debug(f"GlobalShortcuts: erro ao processar Activated: {exc}")

    def stop(self) -> None:
        for s in self._subs:
            try:
                self._bus.signal_unsubscribe(s)
            except Exception:
                pass
        self._subs.clear()
        self.available = False
