# Decisão de design: o alvo de soltar é instalado na *janela*, não no fluxo de chat.
# Assim o gesto funciona em qualquer ponto (inclusive sobre a barra de prompt), e o
# controlador de eventos do GTK propaga do widget sob o cursor até a janela quando o
# filho não aceita o tipo — o que cobre o caso de soltar em cima do campo de texto.
#
# O tipo aceito é `Gdk.FileList`: é o que o gerenciador de arquivos (Nautilus/Files)
# entrega, e não depende de URI em texto puro.

"""Alvo de arrastar-e-soltar de arquivos com feedback visual."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - apenas para type checking
    from ..app import CopilotWindow


class DropZone:
    """Recebe arquivos arrastados para a janela e avisa a janela com os caminhos."""

    def __init__(self, ctx: "CopilotWindow"):
        self.ctx = ctx
        self.active = False

        self.overlay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.overlay.set_hexpand(True)
        self.overlay.set_vexpand(True)
        self.overlay.set_halign(Gtk.Align.FILL)
        self.overlay.set_valign(Gtk.Align.FILL)
        # Não intercepta o cursor: o widget debaixo continua sendo o alvo real,
        # e o controlador na janela recebe o evento por propagação.
        self.overlay.set_can_target(False)
        self.overlay.set_visible(False)
        self.overlay.add_css_class("drop-scrim")

        hint = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        hint.set_halign(Gtk.Align.CENTER)
        hint.set_valign(Gtk.Align.CENTER)
        hint.add_css_class("drop-hint")

        icon = Gtk.Image.new_from_icon_name("document-send-symbolic")
        icon.set_pixel_size(40)
        hint.append(icon)

        title = Gtk.Label(label="Solte para anexar ao chat")
        title.add_css_class("heading")
        hint.append(title)

        subtitle = Gtk.Label(label="Imagens, PDFs e arquivos de texto")
        subtitle.add_css_class("caption")
        subtitle.add_css_class("dim-label")
        hint.append(subtitle)

        self.overlay.append(hint)

        self.target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        self.target.connect("enter", self._on_enter)
        self.target.connect("leave", self._on_leave)
        self.target.connect("drop", self._on_drop)

    # ------------------------------------------------------------------
    # Instalação
    # ------------------------------------------------------------------
    def attach_to(self, widget: Gtk.Widget) -> None:
        """Instala o alvo em um widget (a janela, para valer em toda a área)."""
        widget.add_controller(self.target)

    # ------------------------------------------------------------------
    # Callbacks de arrastar
    # ------------------------------------------------------------------
    def _on_enter(self, _target: Gtk.DropTarget, _x: float, _y: float) -> Gdk.DragAction:
        self.set_active(True)
        return Gdk.DragAction.COPY

    def _on_leave(self, _target: Gtk.DropTarget) -> None:
        self.set_active(False)

    def _on_drop(self, _target: Gtk.DropTarget, value: object, _x: float, _y: float) -> bool:
        self.set_active(False)
        files = getattr(value, "get_files", None)
        paths = []
        if callable(files):
            for gfile in files():
                path = gfile.get_path()
                if path:
                    paths.append(path)
        if not paths:
            return False
        self.ctx.attach_files(paths)
        return True

    # ------------------------------------------------------------------
    # Feedback visual
    # ------------------------------------------------------------------
    def set_active(self, active: bool) -> None:
        """Mostra/esconde o véu de "solte aqui"."""
        self.active = active
        self.overlay.set_visible(active)
