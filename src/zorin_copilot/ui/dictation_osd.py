# Decisão de design: Janela OSD compacta estilo pílula (Floating Pill) para Ditado Global.
# A janela NUNCA deve receber foco do teclado (set_focusable(False) e keyboard_mode="none"),
# permitindo que o foco permaneça intacto no aplicativo onde o texto será digitado.

"""Janela flutuante minimalista (OSD) para exibição do status do Ditado Global."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from .gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from ..core.dictation import DictationState
from . import layer_shell

logger = logging.getLogger(__name__)


class DictationOSDWindow(Gtk.Window):
    """Pílula flutuante não-focalizável para visualização do estado do ditado."""

    def __init__(
        self,
        application: Gtk.Application,
        on_cancel: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(application=application)
        self.on_cancel = on_cancel
        self._close_timeout_id: Optional[int] = None

        self.set_title("Zorin Copilot Ditado")
        self.set_decorated(False)
        self.set_resizable(False)

        # Regra de ouro: NUNCA roubar o foco da janela ativa do usuário
        self.set_focusable(False)
        self.set_can_focus(False)
        self.set_can_target(True)

        self.add_css_class("glass-window")
        self.add_css_class("dark-glass")
        self.add_css_class("dictation-osd-pill")

        self._setup_layout()
        self._setup_layer_shell()

    def _setup_layout(self) -> None:
        container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        container.set_valign(Gtk.Align.CENTER)

        # Ícone de microfone/status
        self.status_icon = Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic")
        self.status_icon.set_pixel_size(16)
        self.status_icon.add_css_class("dictation-mic-active")
        container.append(self.status_icon)

        # Mini barra de volume de entrada de áudio
        self.level_bar = Gtk.LevelBar.new_for_interval(0.0, 1.0)
        self.level_bar.set_value(0.0)
        self.level_bar.set_size_request(32, 6)
        self.level_bar.set_valign(Gtk.Align.CENTER)
        container.append(self.level_bar)

        # Rótulo de status em tempo real
        self.status_label = Gtk.Label(label="Ouvindo...", xalign=0)
        self.status_label.add_css_class("caption")
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.status_label.set_max_width_chars(32)
        container.append(self.status_label)

        # Botão de cancelar/fechar
        self.cancel_btn = Gtk.Button.new_from_icon_name("window-close-symbolic")
        self.cancel_btn.add_css_class("flat")
        self.cancel_btn.add_css_class("circular")
        self.cancel_btn.set_tooltip_text("Cancelar ditado (Esc)")
        self.cancel_btn.connect("clicked", self._on_cancel_clicked)
        container.append(self.cancel_btn)

        self.set_child(container)

    def _setup_layer_shell(self) -> None:
        """Configura posicionamento no Wayland via layer-shell se disponível."""
        if layer_shell.is_supported():
            try:
                layer_shell.init(self)
                layer_shell.set_layer(self, "overlay")
                layer_shell.set_keyboard_mode(self, "none")
                layer_shell.set_anchor(self, "top", True)
                layer_shell.set_margin(self, "top", 18)
                layer_shell.set_namespace(self, "zorin-copilot-dictate")
            except Exception as exc:
                logger.debug(f"Falha ao configurar layer-shell no OSD de ditado: {exc}")

    def update_state(self, state: DictationState, message: str = "") -> None:
        """Atualiza a apresentação visual conforme o estado do ditado."""
        if self._close_timeout_id:
            GLib.source_remove(self._close_timeout_id)
            self._close_timeout_id = None

        if state == DictationState.LISTENING:
            self.status_icon.set_from_icon_name("audio-input-microphone-symbolic")
            self.status_icon.remove_css_class("success")
            self.status_icon.remove_css_class("warning")
            self.status_icon.add_css_class("dictation-mic-active")
            self.level_bar.set_visible(True)
            self.status_label.set_text(message or "Ouvindo... (fale agora)")

        elif state == DictationState.TRANSCRIBING:
            self.status_icon.set_from_icon_name("system-run-symbolic")
            self.status_icon.remove_css_class("dictation-mic-active")
            self.level_bar.set_visible(False)
            self.status_label.set_text("Transcrevendo fala...")

        elif state == DictationState.TYPING:
            self.status_icon.set_from_icon_name("input-keyboard-symbolic")
            self.status_icon.remove_css_class("dictation-mic-active")
            self.level_bar.set_visible(False)
            self.status_label.set_text("Digitando no app...")

        elif state == DictationState.DONE:
            self.status_icon.set_from_icon_name("emblem-ok-symbolic")
            self.status_icon.remove_css_class("dictation-mic-active")
            self.status_icon.add_css_class("success")
            self.level_bar.set_visible(False)
            self.status_label.set_text(message or "Concluído")
            # Fecha automaticamente após 1.2 segundos
            self._close_timeout_id = GLib.timeout_add(1200, self._auto_close)

        elif state == DictationState.ERROR:
            self.status_icon.set_from_icon_name("dialog-warning-symbolic")
            self.status_icon.remove_css_class("dictation-mic-active")
            self.status_icon.add_css_class("warning")
            self.level_bar.set_visible(False)
            self.status_label.set_text(message or "Erro no ditado")
            self._close_timeout_id = GLib.timeout_add(2500, self._auto_close)

        elif state == DictationState.IDLE:
            self.close_osd()

    def update_audio_level(self, level: float) -> None:
        """Atualiza a barra de nível de áudio durante a escuta."""
        if self.level_bar.get_visible():
            self.level_bar.set_value(max(0.0, min(1.0, level * 2.0)))

    def _auto_close(self) -> bool:
        self._close_timeout_id = None
        self.close_osd()
        return GLib.SOURCE_REMOVE

    def _on_cancel_clicked(self, _btn: Gtk.Button) -> None:
        if self.on_cancel:
            self.on_cancel()
        self.close_osd()

    def close_osd(self) -> None:
        """Oculta e fecha a janela OSD."""
        if self._close_timeout_id:
            GLib.source_remove(self._close_timeout_id)
            self._close_timeout_id = None
        self.set_visible(False)
        self.destroy()
