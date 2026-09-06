# Decisão de design: o anexo visual (recorte, tela cheia ou imagem da área de transferência)
# fica acoplado à barra de prompt e permanece ativo entre turnos (visão contínua), por isso
# vive em componente próprio que controla o ciclo ocultar janela -> capturar -> restaurar.

"""Anexo de contexto visual: captura de tela, miniatura e visão contínua."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from ...core.vision import ScreenCaptureService

if TYPE_CHECKING:  # pragma: no cover - apenas para type checking
    from ..app import CopilotWindow


class VisionAttachment:
    """Gerencia o card de imagem anexada e o fluxo de captura de tela."""

    def __init__(self, ctx: "CopilotWindow"):
        self.ctx = ctx

        self.preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.preview_box.add_css_class("card")
        self.preview_box.add_css_class("glass-card")
        self.preview_box.set_margin_bottom(4)
        self.preview_box.set_margin_start(2)
        self.preview_box.set_margin_end(2)
        self.preview_box.set_visible(False)

        self.hdr_lbl: Gtk.Label
        self.thumbnail: Gtk.Picture
        self.active_badge: Gtk.Label

        self._build()

    def _build(self) -> None:
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hdr.set_margin_start(10)
        hdr.set_margin_end(8)
        hdr.set_margin_top(6)

        self.hdr_lbl = Gtk.Label(label="<b>Recorte de Tela Ativo</b>", use_markup=True, xalign=0)
        self.hdr_lbl.add_css_class("caption")
        self.hdr_lbl.set_hexpand(True)
        hdr.append(self.hdr_lbl)

        dismiss_btn = Gtk.Button.new_from_icon_name("window-close-symbolic")
        dismiss_btn.set_tooltip_text("Descartar anexo de imagem")
        dismiss_btn.add_css_class("flat")
        dismiss_btn.add_css_class("circular")
        dismiss_btn.add_css_class("glass-icon-btn")
        dismiss_btn.connect("clicked", self.clear)
        hdr.append(dismiss_btn)
        self.preview_box.append(hdr)

        self.thumbnail = Gtk.Picture()
        self.thumbnail.set_can_shrink(True)
        self.thumbnail.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.thumbnail.set_size_request(-1, 100)
        self.thumbnail.set_margin_start(10)
        self.thumbnail.set_margin_end(10)
        self.thumbnail.set_margin_bottom(6)
        self.preview_box.append(self.thumbnail)

        self.active_badge = Gtk.Label(
            label="\U0001f441️ <i>Próxima mensagem usará esta imagem como contexto</i>",
            use_markup=True,
            xalign=0,
        )
        self.active_badge.add_css_class("dim-label")
        self.active_badge.add_css_class("caption")
        self.active_badge.set_margin_start(10)
        self.active_badge.set_margin_bottom(6)
        self.preview_box.append(self.active_badge)

    # ------------------------------------------------------------------
    # Estado do anexo
    # ------------------------------------------------------------------
    def clear(self, _btn: Gtk.Button | None = None) -> None:
        """Descarta o contexto visual ativo, retornando o chat ao modo puramente textual."""
        self.ctx._active_image_bytes = None
        self.ctx._active_image_is_area = False
        self.ctx._active_image_is_clipboard = False
        self.ctx._current_ocr_text = None
        self.preview_box.set_visible(False)
        self.ctx.entry.set_placeholder_text("Peça ao Zorin Copilot ou digite um comando...")
        self.ctx.show_toast("Contexto visual descartado.")

    def render_thumbnail(
        self, image_bytes: bytes, is_area: bool = True, is_clipboard: bool = False
    ) -> None:
        """Renderiza a miniatura visual no card de anexo acima da barra de entrada."""
        try:
            gbytes = GLib.Bytes.new(image_bytes)
            texture = Gdk.Texture.new_from_bytes(gbytes)
            self.thumbnail.set_paintable(texture)
            if is_clipboard:
                header_label = "<b>\U0001f4cb Imagem da Área de Transferência Anexada</b>"
            elif is_area:
                header_label = "<b>✂️ Recorte de Tela Anexado</b>"
            else:
                header_label = "<b>\U0001f5a5️ Captura de Tela Inteira Anexada</b>"
            self.hdr_lbl.set_markup(header_label)
            self.active_badge.set_visible(True)
            self.preview_box.set_visible(True)
        except Exception:
            self.preview_box.set_visible(False)

    # ------------------------------------------------------------------
    # Captura de tela
    # ------------------------------------------------------------------
    def start_capture(self, interactive: bool = True, direct_mode: bool = False) -> None:
        """Inicia a captura ocultando temporariamente o Copilot para não obstruir a tela."""
        ctx = self.ctx
        if ctx._is_busy:
            return

        ctx.set_visible(False)

        prompt_typed = ctx.entry.get_text().strip() if not direct_mode else ""

        def capture_worker():
            # Aguarda a remoção visual da janela pelo compositor Wayland
            time.sleep(0.2)
            success, img_bytes, mode = ScreenCaptureService.capture(interactive=interactive)
            GLib.idle_add(
                self._on_capture_finished, success, img_bytes, mode, prompt_typed, interactive, direct_mode
            )

        threading.Thread(target=capture_worker, daemon=True).start()

    def _on_capture_finished(
        self,
        success: bool,
        image_bytes: bytes | None,
        mode: str,
        prompt_typed: str,
        is_area: bool,
        direct_mode: bool = False,
    ) -> bool:
        """Restaura a janela e anexa a captura visual acima da barra de entrada."""
        ctx = self.ctx
        if not success or not image_bytes:
            if direct_mode:
                return False
            ctx.set_visible(True)
            ctx.present()
            ctx.show_toast("Captura de tela cancelada.")
            return False

        ctx.set_visible(True)
        ctx.present()
        ctx.entry.grab_focus()

        # Mantém a imagem no contexto ativo para Visão Contínua (turnos subsequentes)
        ctx._active_image_bytes = image_bytes
        ctx._active_image_is_area = is_area
        ctx._active_image_is_clipboard = False

        self.render_thumbnail(image_bytes, is_area=is_area, is_clipboard=False)

        if prompt_typed:
            ctx.entry.set_text(prompt_typed)
            ctx.prompt_bar.submit(ctx.entry)
        else:
            ctx.entry.set_placeholder_text(
                "Faça uma pergunta sobre esta imagem ou pressione Enter..."
            )
            ctx.show_toast("\U0001f4f8 Imagem anexada! Digite sua pergunta e envie.")

        return False
