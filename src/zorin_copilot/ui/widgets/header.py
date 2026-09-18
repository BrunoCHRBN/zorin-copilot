# Decisão de design: a HeaderBar concentra o estado global do assistente (modelo ativo,
# cerca espacial de monitores e acesso rápido a voz/configurações). Foi isolada da janela
# principal para que mudanças de layout não exijam tocar na orquestração de conversa.

"""Cabeçalho da janela principal: título, atalhos, badge de modelo e cerca espacial."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, Gtk  # noqa: E402

from ...core.fence import NO_MONITOR_LABEL, FenceMode  # noqa: E402
from ...core.usage import format_tokens  # noqa: E402

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
        self.header.add_css_class("transparent-header")

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
        self.status_badge_btn.set_tooltip_text("Clique para alternar modelo ou agente de IA (Ctrl+Shift+M)")
        self.status_badge_btn.connect("clicked", lambda _: self.ctx._open_model_selector())

        self.status_badge = Gtk.Label()
        self.status_badge.add_css_class("caption")
        self.status_badge.add_css_class("tabular-nums")
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
        mon_name_init = active_mon.name if active_mon else NO_MONITOR_LABEL
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
        """Constrói o menu suspenso de seleção de telas, janelas abertas e Kill Switch."""
        popover = Gtk.Popover()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vbox.set_margin_top(8)
        vbox.set_margin_bottom(8)
        vbox.set_margin_start(8)
        vbox.set_margin_end(8)
        popover.set_child(vbox)

        def _on_visible(p: Gtk.Popover, _pspec: Any) -> None:
            if p.get_visible():
                self._populate_fence_popover(vbox, p)

        popover.connect("notify::visible", _on_visible)
        self._populate_fence_popover(vbox, popover)
        self.fence_menu_btn.set_popover(popover)

    def _populate_fence_popover(self, vbox: Gtk.Box, popover: Gtk.Popover) -> None:
        """Preenche dinamicamente o menu com foco em janela ativa, janelas abertas e monitores."""
        while child := vbox.get_first_child():
            vbox.remove(child)

        title = Gtk.Label(label="<b>Foco e Cerca Espacial</b>", use_markup=True, xalign=0)
        title.add_css_class("caption")
        title.set_margin_bottom(4)
        vbox.append(title)

        # 1. Modo dinâmico: Janela Ativa
        active_btn = Gtk.Button()
        active_btn.add_css_class("flat")
        active_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        active_icon = Gtk.Image.new_from_icon_name("starred-symbolic")
        active_box.append(active_icon)
        active_box.append(Gtk.Label(label="⚡ Janela Ativa (Dinâmica / Auto)"))
        active_btn.set_child(active_box)
        active_btn.connect("clicked", lambda _, pop=popover: self.on_select_active_window_mode(pop))
        vbox.append(active_btn)

        # 2. Janelas abertas detectadas
        try:
            from ...core.window_manager import WindowManager
            windows = WindowManager.list_windows(exclude_copilot=True)
        except Exception:
            windows = []

        if windows:
            win_lbl = Gtk.Label(label="<small><b>Janelas Abertas:</b></small>", use_markup=True, xalign=0)
            win_lbl.add_css_class("dim-label")
            win_lbl.set_margin_top(4)
            vbox.append(win_lbl)

            for w in windows[:6]:
                w_btn = Gtk.Button()
                w_btn.add_css_class("flat")
                w_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                w_icon = Gtk.Image.new_from_icon_name("window-new-symbolic")
                w_box.append(w_icon)
                w_title = Gtk.Label(label=w.display_name(30), xalign=0)
                w_title.set_ellipsize(3)
                w_box.append(w_title)
                w_btn.set_child(w_box)
                w_btn.connect("clicked", lambda _, win=w, pop=popover: self.on_select_window(win, pop))
                vbox.append(w_btn)

        sep_mon = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep_mon.set_margin_top(4)
        sep_mon.set_margin_bottom(4)
        vbox.append(sep_mon)

        mon_lbl = Gtk.Label(label="<small><b>Monitores:</b></small>", use_markup=True, xalign=0)
        mon_lbl.add_css_class("dim-label")
        vbox.append(mon_lbl)

        for m in self.ctx.fence.monitors:
            suffix = " (Principal)" if m.is_primary else " (Secundária)"
            row_btn = Gtk.Button()
            row_btn.add_css_class("flat")
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row_icon = Gtk.Image.new_from_icon_name("video-display-symbolic")
            row_box.append(row_icon)
            row_box.append(Gtk.Label(label=f"{m.name}{suffix}"))
            row_btn.set_child(row_box)
            idx = m.index
            row_btn.connect("clicked", lambda _, i=idx, pop=popover: self.on_select_fence_monitor(i, pop))
            vbox.append(row_btn)

        all_btn = Gtk.Button()
        all_btn.add_css_class("flat")
        all_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        all_box.append(Gtk.Image.new_from_icon_name("network-wired-symbolic"))
        all_box.append(Gtk.Label(label="Todas as Telas (Livre)"))
        all_btn.set_child(all_box)
        all_btn.connect("clicked", lambda _, pop=popover: self.on_select_all_monitors(pop))
        vbox.append(all_btn)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(4)
        sep.set_margin_bottom(4)
        vbox.append(sep)

        kill_btn = Gtk.Button()
        kill_btn.add_css_class("destructive-action")
        kill_btn.add_css_class("pill")
        kill_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        kill_box.append(Gtk.Image.new_from_icon_name("process-stop-symbolic"))
        kill_box.append(Gtk.Label(label="Parada de Emergência (Kill Switch)"))
        kill_btn.set_child(kill_box)
        kill_btn.connect("clicked", lambda _, pop=popover: self.on_toggle_kill_switch(pop))
        vbox.append(kill_btn)

    # ------------------------------------------------------------------
    # Ações
    # ------------------------------------------------------------------
    def _on_toggle_sidebar(self, _btn: Gtk.Button) -> None:
        self.ctx.toggle_sidebar()

    def update_provider_badge(self) -> None:
        """Reflete no badge o provedor/modelo e o consumo de tokens (item #2)."""
        config = self.ctx.config
        # Indicador de tokens consumidos na sessão (estilo Raycast) — aparece
        # sempre que houver uso acumulado, independente de estar configurado agora.
        tracker = self.ctx.engine.usage_tracker
        token_txt = ""
        if tracker is not None and tracker.session.total_tokens:
            token_txt = f" · {format_tokens(tracker.session.total_tokens)} tokens"
        if config.is_configured():
            if config.provider == "ollama":
                model_lbl = config.ollama_model or "ollama"
                if "dolphin" in model_lbl.lower():
                    prov_name = f"🐬 Dolphin ({model_lbl.split(':')[0]})"
                else:
                    prov_name = f"Ollama ({model_lbl})"
            elif config.provider == "gemini":
                prov_name = f"Gemini ({config.gemini_model})"
            elif config.provider == "workbuddy":
                prov_name = f"WorkBuddy ({getattr(config, 'workbuddy_model', 'hy4')})"
            elif config.provider == "openai":
                prov_name = f"API ({config.openai_model})"
            else:
                prov_name = "IA Ativa"
            self.status_badge.set_text(f"● {prov_name}{token_txt}")
        else:
            self.status_badge.set_text(f"○ IA não configurada{token_txt}")

    def refresh_token_usage(self) -> None:
        """Atualiza apenas o contador de tokens do badge (chamado após cada resposta)."""
        self.update_provider_badge()

    def refresh_fence_label(self) -> None:
        """Resincroniza o rótulo do monitor/janela com o estado da cerca espacial."""
        if self.ctx.fence.is_emergency_stopped:
            self.fence_lbl.set_text("BLOQUEADO")
            return
        mode = self.ctx.fence.mode
        if mode == FenceMode.ACTIVE_WINDOW:
            self.fence_lbl.set_text("⚡ Janela Ativa")
            return
        if mode == FenceMode.CHOSEN_WINDOW:
            win = self.ctx.fence.get_target_window()
            if win:
                app_name = win.app.capitalize() if win.app else "Janela"
                self.fence_lbl.set_text(f"🪟 {app_name}")
                return
            self.fence_lbl.set_text("🪟 Janela")
            return
        if mode == FenceMode.ALL_MONITORS:
            self.fence_lbl.set_text("Todas as Telas")
            return
        mon = self.ctx.fence.get_active_monitor()
        self.fence_lbl.set_text(mon.name if mon else NO_MONITOR_LABEL)

    def on_select_active_window_mode(self, popover: Gtk.Popover) -> None:
        popover.popdown()
        self.ctx.fence.set_active_window_mode()
        self.fence_lbl.set_text("⚡ Janela Ativa")
        self._sync_live_client_fence()
        self.ctx.show_toast("Foco definido para Janela Ativa dinâmica.")

    def on_select_window(self, win: Any, popover: Gtk.Popover) -> None:
        popover.popdown()
        self.ctx.fence.set_chosen_window(win)
        try:
            from ...core.window_manager import WindowManager
            win_id = getattr(win, "id", str(win))
            if win_id:
                WindowManager.focus_window(win_id)
        except Exception:
            pass
        app_name = getattr(win, "app", "Janela").capitalize()
        self.fence_lbl.set_text(f"🪟 {app_name}")
        self._sync_live_client_fence()
        display = win.display_name(30) if hasattr(win, "display_name") else str(win)
        self.ctx.show_toast(f"Foco fixado em: {display}")

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
            self.ctx.show_toast(f"Cerca espacial fixada em: {name}")

    def on_select_all_monitors(self, popover: Gtk.Popover) -> None:
        popover.popdown()
        self.ctx.fence.set_all_monitors()
        self.fence_lbl.set_text("Todas as Telas")
        self._sync_live_client_fence()
        self.ctx.show_toast("Cerca espacial expandida para todas as telas.")

    def on_toggle_kill_switch(self, popover: Gtk.Popover | None = None) -> None:
        # `popover` é opcional: a bandeja dispara isso sem popover algum.
        if popover is not None:
            popover.popdown()
        fence = self.ctx.fence
        if fence.is_emergency_stopped:
            fence.reset_emergency_stop()
            mon = fence.get_active_monitor()
            self.fence_lbl.set_text(mon.name if mon else NO_MONITOR_LABEL)
            self.ctx.show_toast("Parada de emergência desativada.")
        else:
            fence.trigger_emergency_stop()
            self.fence_lbl.set_text("BLOQUEADO")
            self.ctx.show_toast("Kill Switch ativado: automações suspensas.")
