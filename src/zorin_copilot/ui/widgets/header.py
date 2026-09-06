# Decisão de design: a HeaderBar concentra o estado global do assistente (modelo ativo,
# cerca espacial de monitores e acesso rápido a voz/configurações). Foi isolada da janela
# principal para que mudanças de layout não exijam tocar na orquestração de conversa.

"""Cabeçalho da janela principal: título, atalhos, badge de modelo e cerca espacial."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - apenas para type checking
    from ..app import CopilotWindow


class HeaderBarWidget:
    """Constrói e governa a HeaderBar do Copilot.

    Recebe a janela como contexto (``ctx``) para ler configuração/estado e disparar
    ações coordenadas (abrir preferências, alternar voz, alternar barra lateral).
    """

    def __init__(self, ctx: "CopilotWindow"):
        self.ctx = ctx
        self.header = Adw.HeaderBar()

        self.window_title = Adw.WindowTitle(title="Zorin Copilot", subtitle="Assistente Inteligente")
        self.header.set_title_widget(self.window_title)

        self.sidebar_toggle_btn: Gtk.Button
        self.new_chat_btn: Gtk.Button
        self.voice_call_btn: Gtk.Button
        self.status_badge_btn: Gtk.Button
        self.status_badge: Gtk.Label
        self.fence_menu_btn: Gtk.MenuButton
        self.fence_lbl: Gtk.Label

        self._build_start_buttons()
        self._build_end_buttons()

    # ------------------------------------------------------------------
    # Construção
    # ------------------------------------------------------------------
    def _build_start_buttons(self) -> None:
        """Botões do lado esquerdo: barra lateral, nova conversa e painel de comandos."""
        self.sidebar_toggle_btn = Gtk.Button.new_from_icon_name("sidebar-show-symbolic")
        self.sidebar_toggle_btn.set_tooltip_text("Alternar barra lateral de conversas (Ctrl+H)")
        self.sidebar_toggle_btn.add_css_class("flat")
        self.sidebar_toggle_btn.add_css_class("circular")
        self.sidebar_toggle_btn.add_css_class("glass-icon-btn")
        self.sidebar_toggle_btn.connect("clicked", self._on_toggle_sidebar)
        self.header.pack_start(self.sidebar_toggle_btn)

        self.new_chat_btn = Gtk.Button.new_from_icon_name("list-add-symbolic")
        self.new_chat_btn.set_tooltip_text("Nova Conversa (Ctrl+N)")
        self.new_chat_btn.add_css_class("flat")
        self.new_chat_btn.add_css_class("circular")
        self.new_chat_btn.add_css_class("glass-icon-btn")
        self.new_chat_btn.connect("clicked", lambda _: self.ctx._on_new_topic())
        self.header.pack_start(self.new_chat_btn)

        # Sem este botão o Ctrl+K seria indescobrível — a análise pedia exatamente
        # isso ao sugerir o command palette.
        self.palette_btn = Gtk.Button.new_from_icon_name("system-search-symbolic")
        self.palette_btn.set_tooltip_text("Painel de comandos (Ctrl+K)")
        self.palette_btn.add_css_class("flat")
        self.palette_btn.add_css_class("circular")
        self.palette_btn.add_css_class("glass-icon-btn")
        self.palette_btn.connect("clicked", lambda _: self.ctx._open_command_palette())
        self.header.pack_start(self.palette_btn)

    def _build_end_buttons(self) -> None:
        """Botões do lado direito: preferências, voz, cerca espacial e badge de modelo."""
        settings_btn = Gtk.Button.new_from_icon_name("preferences-system-symbolic")
        settings_btn.set_tooltip_text("Configurações do Assistente e Chaves de IA")
        settings_btn.add_css_class("flat")
        settings_btn.add_css_class("circular")
        settings_btn.add_css_class("glass-icon-btn")
        settings_btn.connect("clicked", self.ctx._open_settings)

        self.voice_call_btn = Gtk.Button.new_from_icon_name("audio-input-microphone-symbolic")
        self.voice_call_btn.set_tooltip_text("Conversa por Voz ao Vivo (Gemini Live / Ctrl+M)")
        self.voice_call_btn.add_css_class("flat")
        self.voice_call_btn.add_css_class("circular")
        self.voice_call_btn.add_css_class("glass-icon-btn")
        self.voice_call_btn.connect("clicked", lambda _: self.ctx.toggle_live_voice())

        self.status_badge_btn = Gtk.Button()
        self.status_badge_btn.add_css_class("flat")
        self.status_badge_btn.add_css_class("pill")
        self.status_badge_btn.add_css_class("glass-pill")
        self.status_badge_btn.set_tooltip_text("Clique para alterar modelo ou provedor de IA")
        self.status_badge_btn.connect("clicked", self.ctx._open_settings)

        self.status_badge = Gtk.Label()
        self.status_badge.add_css_class("caption")
        self.status_badge_btn.set_child(self.status_badge)

        self.fence_menu_btn = Gtk.MenuButton()
        self.fence_menu_btn.add_css_class("flat")
        self.fence_menu_btn.add_css_class("pill")
        self.fence_menu_btn.add_css_class("glass-pill")
        self.fence_menu_btn.set_tooltip_text("Cerca Espacial: Monitor ativo para automações")

        fence_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        fence_icon = Gtk.Image.new_from_icon_name("video-display-symbolic")
        fence_icon.set_pixel_size(14)
        active_mon = self.ctx.fence.get_active_monitor()
        mon_name_init = active_mon.name if active_mon else 'AOC 27"'
        self.fence_lbl = Gtk.Label(label=mon_name_init)
        self.fence_lbl.add_css_class("caption")
        fence_btn_box.append(fence_icon)
        fence_btn_box.append(self.fence_lbl)
        self.fence_menu_btn.set_child(fence_btn_box)

        self.build_fence_popover()

        self.header.pack_end(settings_btn)
        self.header.pack_end(self.voice_call_btn)
        self.header.pack_end(self.fence_menu_btn)
        self.header.pack_end(self.status_badge_btn)

    def build_fence_popover(self) -> None:
        """Constrói o menu suspenso de seleção de telas e Kill Switch."""
        popover = Gtk.Popover()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vbox.set_margin_top(8)
        vbox.set_margin_bottom(8)
        vbox.set_margin_start(8)
        vbox.set_margin_end(8)

        title = Gtk.Label(label="<b>Cerca de Proteção Espacial</b>", use_markup=True, xalign=0)
        title.add_css_class("caption")
        title.set_margin_bottom(4)
        vbox.append(title)

        for m in self.ctx.fence.monitors:
            suffix = " (Principal)" if m.is_primary else " (Secundária)"
            row_btn = Gtk.Button(label=f"\U0001f5a5️ {m.name}{suffix}")
            row_btn.add_css_class("flat")
            idx = m.index
            row_btn.connect("clicked", lambda _, i=idx, pop=popover: self.on_select_fence_monitor(i, pop))
            vbox.append(row_btn)

        all_btn = Gtk.Button(label="\U0001f310 Todas as Telas (Livre)")
        all_btn.add_css_class("flat")
        all_btn.connect("clicked", lambda _, pop=popover: self.on_select_all_monitors(pop))
        vbox.append(all_btn)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(4)
        sep.set_margin_bottom(4)
        vbox.append(sep)

        kill_btn = Gtk.Button(label="\U0001f6d1 Parada de Emergência (Kill Switch)")
        kill_btn.add_css_class("destructive-action")
        kill_btn.add_css_class("pill")
        kill_btn.connect("clicked", lambda _, pop=popover: self.on_toggle_kill_switch(pop))
        vbox.append(kill_btn)

        popover.set_child(vbox)
        self.fence_menu_btn.set_popover(popover)

    # ------------------------------------------------------------------
    # Ações
    # ------------------------------------------------------------------
    def _on_toggle_sidebar(self, _btn: Gtk.Button) -> None:
        self.ctx.toggle_sidebar()

    def update_provider_badge(self) -> None:
        """Reflete no badge o provedor/modelo atualmente configurado."""
        config = self.ctx.config
        if config.is_configured():
            prov_name = {
                "gemini": f"Gemini ({config.gemini_model})",
                "ollama": f"Ollama ({config.ollama_model})",
                "openai": f"API ({config.openai_model})",
            }.get(config.provider, "IA Ativa")
            self.status_badge.set_text(f"● {prov_name}")
        else:
            self.status_badge.set_text("○ IA não configurada (⚙️)")

    def refresh_fence_label(self) -> None:
        """Resincroniza o rótulo do monitor com o estado da cerca espacial."""
        if self.ctx.fence.is_emergency_stopped:
            self.fence_lbl.set_text("\U0001f6d1 BLOQUEADO")
            return
        mon = self.ctx.fence.get_active_monitor()
        self.fence_lbl.set_text(mon.name if mon else 'AOC 27"')

    def _sync_live_client_fence(self) -> None:
        """Reatribui a cerca espacial ao cliente de voz ao vivo, se estiver ativo."""
        live_client = self.ctx.live_client
        if live_client:
            live_client.fence = self.ctx.fence
            live_client.input_driver.fence = self.ctx.fence

    def on_select_fence_monitor(self, monitor_idx: int, popover: Gtk.Popover) -> None:
        popover.popdown()
        ok = self.ctx.fence.set_active_monitor(monitor_idx)
        if ok:
            mon = self.ctx.fence.get_active_monitor()
            name = mon.name if mon else f"Monitor {monitor_idx}"
            self.fence_lbl.set_text(name)
            self._sync_live_client_fence()
            self.ctx.show_toast(f"\U0001f5a5️ Cerca espacial fixada em: {name}")

    def on_select_all_monitors(self, popover: Gtk.Popover) -> None:
        popover.popdown()
        self.ctx.fence.set_all_monitors()
        self.fence_lbl.set_text("Todas as Telas")
        self._sync_live_client_fence()
        self.ctx.show_toast("\U0001f310 Cerca espacial expandida para todas as telas.")

    def on_toggle_kill_switch(self, popover: Gtk.Popover) -> None:
        popover.popdown()
        fence = self.ctx.fence
        if fence.is_emergency_stopped:
            fence.reset_emergency_stop()
            mon = fence.get_active_monitor()
            self.fence_lbl.set_text(mon.name if mon else 'AOC 27"')
            self.ctx.show_toast("✓ Parada de emergência desativada.")
        else:
            fence.trigger_emergency_stop()
            self.fence_lbl.set_text("\U0001f6d1 BLOQUEADO")
            self.ctx.show_toast("\U0001f6d1 KILL SWITCH ATIVADO: Automações suspensas.")
