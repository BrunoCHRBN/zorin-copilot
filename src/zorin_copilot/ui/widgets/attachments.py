# Decisão de design: os anexos aparecem como chips acima da barra de prompt, ao lado
# do card de visão. Precisam ser visíveis e removíveis um a um: contexto que entra
# no prompt sem o usuário ver é exatamente o tipo de coisa que faz a resposta
# parecer arbitrária.

"""Faixa de chips dos arquivos anexados ao chat."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

from ...core.attachments import Attachment, format_size

if TYPE_CHECKING:  # pragma: no cover - apenas para type checking
    from ..app import CopilotWindow


class AttachmentBar:
    """Lista os arquivos anexados, com remoção individual."""

    def __init__(self, ctx: "CopilotWindow"):
        self.ctx = ctx

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.box.set_margin_start(2)
        self.box.set_margin_end(2)
        self.box.set_margin_bottom(4)
        self.box.set_visible(False)

    # ------------------------------------------------------------------
    # Renderização
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Reconstrói os chips a partir de `ctx.attachments`."""
        self._clear()
        attachments = list(getattr(self.ctx, "attachments", []))
        if not attachments:
            self.box.set_visible(False)
            return
        for att in attachments:
            self.box.append(self._build_chip(att))
        self.box.set_visible(True)

    def _clear(self) -> None:
        child = self.box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.box.remove(child)
            child = nxt

    def _build_chip(self, att: Attachment) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("card")
        row.add_css_class("glass-card")
        row.add_css_class("attachment-chip")
        row.set_margin_start(2)
        row.set_margin_end(2)

        icon = Gtk.Image.new_from_icon_name(att.icon_name)
        icon.set_pixel_size(16)
        icon.set_margin_start(10)
        row.append(icon)

        title = Gtk.Label(label=att.name, xalign=0, ellipsize=3)  # PANGO_ELLIPSIZE_END
        title.set_hexpand(True)
        title.set_margin_start(4)
        title.set_tooltip_text(att.path)
        row.append(title)

        detail = _chip_detail(att)
        if detail:
            badge = Gtk.Label(label=detail)
            badge.add_css_class("caption")
            badge.add_css_class("dim-label")
            row.append(badge)

        remove = Gtk.Button.new_from_icon_name("window-close-symbolic")
        remove.add_css_class("flat")
        remove.add_css_class("circular")
        remove.add_css_class("glass-icon-btn")
        remove.set_tooltip_text("Remover anexo")
        remove.set_margin_end(6)
        remove.connect("clicked", self._on_remove, att)
        row.append(remove)

        return row

    def _on_remove(self, _btn: Gtk.Button, att: Attachment) -> None:
        attachments = getattr(self.ctx, "attachments", None)
        if attachments is None:
            return
        # Identidade, não igualdade: dois anexos do mesmo arquivo soltos duas
        # vezes são objetos distintos e só um deve sair.
        self.ctx.attachments = [a for a in attachments if a is not att]
        self.refresh()
        self.ctx.show_toast(f"Anexo removido: {att.name}")


def _chip_detail(att: Attachment) -> str:
    """Texto secundário do chip: tamanho, truncamento ou erro."""
    if att.error:
        return att.error
    if att.kind.value == "imagem":
        return format_size(att.size) if att.size else ""
    if att.truncated:
        return f"{format_size(att.size)} · truncado"
    return format_size(att.size) if att.size else os.path.splitext(att.name)[1].lstrip(".")
