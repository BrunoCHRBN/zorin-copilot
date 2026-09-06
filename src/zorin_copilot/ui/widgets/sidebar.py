# Decisão de design: barra lateral estilo Gemini com recolhimento animado. A busca é
# protegida por debounce porque cada tecla disparava uma reconstrução completa da lista,
# o que degrada a resposta quando o histórico cresce.

"""Barra lateral de conversas: busca, listagem, retomada e exclusão de tópicos."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - apenas para type checking
    from ..app import CopilotWindow

SEARCH_DEBOUNCE_MS = 80


def format_relative_timestamp(iso_str: str) -> str:
    """Formata timestamp ISO de forma amigável para exibição no histórico de tópicos."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        now = datetime.now()
        time_part = dt.strftime("%H:%M")
        if dt.date() == now.date():
            return f"Hoje às {time_part}"
        elif (now.date() - dt.date()).days == 1:
            return f"Ontem às {time_part}"
        elif dt.year == now.year:
            return dt.strftime("%d/%m") + f" às {time_part}"
        else:
            return dt.strftime("%d/%m/%Y")
    except Exception:
        return iso_str[:16].replace("T", " ")


class SidebarPanel:
    """Painel lateral de histórico de conversas com busca filtrada."""

    def __init__(self, ctx: "CopilotWindow"):
        self.ctx = ctx
        self._search_debounce_timer: int | None = None

        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_RIGHT)
        self.revealer.set_transition_duration(200)
        self.revealer.set_reveal_child(True)

        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.panel.add_css_class("sidebar-panel")
        self.panel.set_size_request(260, -1)
        self.panel.set_hexpand(False)

        self.search: Gtk.SearchEntry
        self.history_listbox: Gtk.ListBox
        self.clear_history_btn: Gtk.Button

        self._build_panel()

        sidebar_wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        sidebar_wrap.set_hexpand(False)
        sidebar_wrap.append(self.panel)
        sidebar_wrap.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.revealer.set_child(sidebar_wrap)
        self.revealer.set_hexpand(False)

    # ------------------------------------------------------------------
    # Construção
    # ------------------------------------------------------------------
    def _build_panel(self) -> None:
        s_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        s_title = Gtk.Label(label="<b>Conversas</b>", use_markup=True, xalign=0)
        s_title.add_css_class("heading")
        s_title.set_hexpand(True)
        s_top.append(s_title)

        s_close_btn = Gtk.Button.new_from_icon_name("pan-start-symbolic")
        s_close_btn.set_tooltip_text("Recolher barra lateral (Ctrl+H)")
        s_close_btn.add_css_class("flat")
        s_close_btn.add_css_class("circular")
        s_close_btn.add_css_class("glass-icon-btn")
        s_close_btn.connect("clicked", self.toggle)
        s_top.append(s_close_btn)
        self.panel.append(s_top)

        self.panel.append(self._build_new_conversation_button())
        self.panel.append(self._build_search_entry())

        self.history_scrolled = Gtk.ScrolledWindow()
        self.history_scrolled.set_vexpand(True)
        self.history_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.history_listbox = Gtk.ListBox()
        self.history_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.history_listbox.connect("row-activated", self._on_row_activated)
        self.history_scrolled.set_child(self.history_listbox)
        self.panel.append(self.history_scrolled)

        self.panel.append(self._build_clear_button())

    def _build_new_conversation_button(self) -> Gtk.Button:
        sidebar_new_btn = Gtk.Button()
        sidebar_new_btn.add_css_class("card")
        sidebar_new_btn.add_css_class("pill")
        sidebar_new_btn.add_css_class("glass-card")
        sidebar_new_btn.set_tooltip_text("Iniciar nova conversa limpa (Ctrl+N)")
        sidebar_new_btn.connect("clicked", lambda _: self.ctx._on_new_topic())

        s_new_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        s_new_box.set_margin_start(10)
        s_new_box.set_margin_end(10)
        s_new_box.set_margin_top(6)
        s_new_box.set_margin_bottom(6)
        s_new_icon = Gtk.Image.new_from_icon_name("list-add-symbolic")
        s_new_icon.set_pixel_size(16)
        s_new_box.append(s_new_icon)
        s_new_lbl = Gtk.Label(label="<b>Nova conversa</b>", use_markup=True, xalign=0)
        s_new_box.append(s_new_lbl)
        sidebar_new_btn.set_child(s_new_box)
        return sidebar_new_btn

    def _build_search_entry(self) -> Gtk.SearchEntry:
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Pesquisar conversas...")
        self.search.connect("search-changed", self._on_search_changed)
        return self.search

    def _build_clear_button(self) -> Gtk.Button:
        self.clear_history_btn = Gtk.Button()
        self.clear_history_btn.add_css_class("flat")
        self.clear_history_btn.add_css_class("destructive-action")
        self.clear_history_btn.set_tooltip_text("Limpar todas as conversas gravadas")
        self.clear_history_btn.connect("clicked", self._on_clear_all_history)
        cl_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cl_box.set_halign(Gtk.Align.CENTER)
        cl_icon = Gtk.Image.new_from_icon_name("user-trash-symbolic")
        cl_icon.set_pixel_size(14)
        cl_box.append(cl_icon)
        cl_lbl = Gtk.Label(label="Limpar Histórico")
        cl_lbl.add_css_class("caption")
        cl_box.append(cl_lbl)
        self.clear_history_btn.set_child(cl_box)
        return self.clear_history_btn

    # ------------------------------------------------------------------
    # Estado e listagem
    # ------------------------------------------------------------------
    def toggle(self, _btn: Gtk.Button | None = None) -> None:
        """Alterna a visibilidade do painel lateral."""
        is_revealed = self.revealer.get_reveal_child()
        self.revealer.set_reveal_child(not is_revealed)
        if not is_revealed:
            self.populate(filter_query=self.search.get_text().strip())

    @property
    def is_revealed(self) -> bool:
        return self.revealer.get_reveal_child()

    def populate(self, filter_query: str = "") -> None:
        """Preenche a lista lateral com os tópicos recuperados do SQLite."""
        while row := self.history_listbox.get_first_child():
            self.history_listbox.remove(row)

        topics = self.ctx.engine.memory.list_chat_topics(limit=60)
        if filter_query:
            q = filter_query.lower().strip()
            topics = [
                t for t in topics
                if q in (t.get("title") or "").lower() or q in (t.get("preview") or "").lower()
            ]

        if not topics:
            self._render_empty_state(bool(filter_query))
            return

        self.clear_history_btn.set_visible(True)

        for topic in topics:
            row = Gtk.ListBoxRow()
            row.add_css_class("sidebar-chat-row")
            is_active = self.ctx.session.id == topic["id"]
            if is_active:
                row.add_css_class("active")

            item_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            item_box.set_margin_top(4)
            item_box.set_margin_bottom(4)
            item_box.set_margin_start(4)
            item_box.set_margin_end(4)

            # Indicador de conversa ativa (usa ícone de destaque, não de presença)
            chat_icon = Gtk.Image.new_from_icon_name(
                "starred-symbolic" if is_active else "format-justification-symbolic"
            )
            chat_icon.set_pixel_size(14)
            item_box.append(chat_icon)

            info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            info_box.set_hexpand(True)

            clean_title = topic["title"] or "Conversa Sem Título"
            title_lbl = Gtk.Label(label=clean_title, xalign=0)
            title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            title_lbl.set_max_width_chars(24)
            info_box.append(title_lbl)

            time_str = format_relative_timestamp(topic.get("updated_at", ""))
            turns_num = topic.get("turn_count", 0)
            sub_str = f"{time_str} • {turns_num} msg{'s' if turns_num != 1 else ''}"
            sub_lbl = Gtk.Label(label=sub_str, xalign=0)
            sub_lbl.add_css_class("caption")
            sub_lbl.add_css_class("dim-label")
            info_box.append(sub_lbl)

            item_box.append(info_box)

            del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.add_css_class("circular")
            del_btn.add_css_class("glass-icon-btn")
            del_btn.set_tooltip_text("Excluir conversa")
            del_btn.set_valign(Gtk.Align.CENTER)
            tid = topic["id"]
            del_btn.connect("clicked", lambda _b, t_id=tid: self.on_delete_topic(t_id))
            item_box.append(del_btn)

            row.set_child(item_box)
            row._topic_id = topic["id"]
            self.history_listbox.append(row)

    def _render_empty_state(self, has_filter: bool) -> None:
        """Mostra o estado vazio quando não há conversas (ou nenhuma casa com o filtro)."""
        empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        empty_box.set_valign(Gtk.Align.CENTER)
        empty_box.set_margin_top(30)
        empty_box.set_margin_bottom(30)

        empty_icon = Gtk.Image.new_from_icon_name("document-open-recent-symbolic")
        empty_icon.set_pixel_size(28)
        empty_icon.add_css_class("dim-label")
        empty_box.append(empty_icon)

        message = "Nenhuma conversa encontrada" if has_filter else "Nenhuma conversa salva ainda"
        empty_lbl = Gtk.Label(
            label=f"<span size='small' alpha='70%'>{message}</span>",
            use_markup=True,
            justify=Gtk.Justification.CENTER,
        )
        empty_box.append(empty_lbl)
        self.history_listbox.append(empty_box)
        self.clear_history_btn.set_visible(False)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------
    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        """Filtra a lista com debounce para evitar reconstruções a cada tecla."""
        if self._search_debounce_timer:
            GLib.source_remove(self._search_debounce_timer)
            self._search_debounce_timer = None

        query = entry.get_text().strip()

        def run_filter():
            self._search_debounce_timer = None
            self.populate(filter_query=query)
            return GLib.SOURCE_REMOVE

        self._search_debounce_timer = GLib.timeout_add(SEARCH_DEBOUNCE_MS, run_filter)

    def _on_row_activated(self, _listbox, row) -> None:
        topic_id = getattr(row, "_topic_id", None)
        if not topic_id:
            return
        self.ctx._resume_topic(topic_id)

    def on_delete_topic(self, topic_id: str) -> None:
        """Exclui uma conversa do histórico SQLite."""
        self.ctx.engine.memory.delete_chat_topic(topic_id)
        if self.ctx.session.id == topic_id:
            self.ctx._on_new_topic()
        else:
            self.populate(filter_query=self.search.get_text().strip())
        self.ctx.show_toast("Conversa excluída.")

    def _on_clear_all_history(self, _btn: Gtk.Button) -> None:
        self.clear_history()

    def clear_history(self) -> None:
        """Limpa todas as conversas do histórico SQLite."""
        self.ctx.engine.memory.clear_all_chat_topics()
        self.ctx.session.reset_new()
        self.ctx.window_title.set_subtitle("Assistente Inteligente")
        self.ctx.chat_stream.rebuild()
        self.populate()
        self.ctx.show_toast("Histórico de conversas completamente limpo.")
