# Decisão de design: barra lateral estilo Gemini com recolhimento animado. A busca é
# protegida por debounce porque cada tecla disparava uma reconstrução completa da lista,
# o que degrada a resposta quando o histórico cresce.
#
# Três decisões moldam a listagem (ver docs/PROPOSTA_REFORMULACAO_GUI.md §1.2):
#   1. Agrupamento por data — "Hoje / Ontem / Esta semana / Anterior". Numa lista
#      longa, a data é o primeiro diferenciador que o olho procura.
#   2. Lixeira só no hover — antes, um ícone de exclusão permanente em cada linha
#      transformava um clique errado em perda de conversa. Fica num slot de largura
#      fixa (sem deslocar o texto ao aparecer) e só se revela quando o ponteiro
#      entra na linha. Teclado alcança a mesma ação com Delete na linha focada.
#   3. Ícone de tipo à esquerda — voz e chat são coisas diferentes; todas as
#      chamadas de voz compartilham o mesmo título, então o ícone é o que as
#      distingue de um chat de texto.

"""Barra lateral de conversas: busca, listagem agrupada, retomada e exclusão de tópicos."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Iterable

from ..gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - apenas para type checking
    from ..app import CopilotWindow

SEARCH_DEBOUNCE_MS = 80

#: Títulos com que o app grava conversas de voz (`app.py`). São genéricos de
#: propósito — toda chamada recebe o mesmo rótulo — então a linha usa o ícone de
#: tipo e o horário para diferenciá-las, nunca o título.
_VOICE_TITLE_PREFIXES = ("Chamada de Voz", "Conversa de Voz")

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

def format_clock(iso_str: str) -> str:
    """Devolve só o horário (``HH:MM``) — dentro de um grupo de data é o que
    distingue duas linhas de mesmo título (ex.: várias chamadas de voz)."""
    if not iso_str:
        return ""
    try:
        return datetime.fromisoformat(iso_str).strftime("%H:%M")
    except Exception:
        return ""

def classify_topic(topic: dict) -> str:
    """Classifica o tópico em ``"voice"`` ou ``"chat"`` para escolher o ícone."""
    title = topic.get("title") or ""
    return "voice" if title.startswith(_VOICE_TITLE_PREFIXES) else "chat"

def group_topics_by_date(
    topics: Iterable[dict], now: datetime | None = None
) -> list[tuple[str, list[dict]]]:
    """Agrupa tópicos por recência preservando a ordem de entrada.

    Devolve pares ``(rótulo, tópicos)`` na ordem canônica das seções, omitindo
    seções vazias. A ordem dentro de cada grupo é a que veio do banco (mais
    recente primeiro), então não reordenamos aqui.
    """
    now = now or datetime.now()
    buckets: dict[str, list[dict]] = {"Hoje": [], "Ontem": [], "Esta semana": [], "Anterior": []}
    for topic in topics:
        buckets[_bucket_for(topic.get("updated_at", ""), now)].append(topic)
    return [(label, items) for label, items in buckets.items() if items]

def _bucket_for(iso_str: str, now: datetime) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
    except Exception:
        return "Anterior"
    delta_days = (now.date() - dt.date()).days
    if delta_days <= 0:
        return "Hoje"
    if delta_days == 1:
        return "Ontem"
    if delta_days < 7:
        return "Esta semana"
    return "Anterior"


def build_topic_identity(ctx: "CopilotWindow", topic: dict) -> Gtk.Box:
    """Ícone + título + "horário · N mensagens": a identidade de uma conversa.

    Vive fora de `SidebarPanel` porque a mesma conversa aparece em dois lugares —
    na lista da barra lateral e na seção "Retomar" da tela de boas-vindas. Duas
    cópias divergiriam em silêncio (uma passaria a mostrar o ícone de voz e a
    outra não), que é o mesmo motivo pelo qual a deduplicação das frases de
    ativação importa `normalize_text` em vez de reimplementá-lo.

    Devolve só o conteúdo. A moldura — a linha em si, a lixeira, o menu — fica
    com quem chama, porque as duas telas oferecem coisas diferentes: a barra
    lateral gerencia (excluir), a tela de boas-vindas só inicia.
    """
    topic_id = topic.get("id")
    is_active = getattr(getattr(ctx, "session", None), "id", None) == topic_id
    is_voice = classify_topic(topic) == "voice"
    icon_name = (
        "audio-input-microphone-symbolic"
        if is_voice
        else ("starred-symbolic" if is_active else "format-justification-symbolic")
    )

    item_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

    chat_icon = Gtk.Image.new_from_icon_name(icon_name)
    chat_icon.set_pixel_size(14)
    chat_icon.set_valign(Gtk.Align.CENTER)
    item_box.append(chat_icon)

    info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    info_box.set_hexpand(True)

    clean_title = topic.get("title") or "Conversa sem título"
    title_lbl = Gtk.Label(label=clean_title, xalign=0)
    title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
    title_lbl.set_hexpand(True)
    # O título é a âncora de reconhecimento; truncado, o usuário perde a
    # referência. O tooltip devolve o texto inteiro no hover.
    title_lbl.set_tooltip_text(clean_title)
    info_box.append(title_lbl)

    clock = format_clock(topic.get("updated_at", ""))
    turns_num = topic.get("turn_count", 0)
    sub_parts = [p for p in (clock, f"{turns_num} msg{'s' if turns_num != 1 else ''}") if p]
    sub_lbl = Gtk.Label(label=" · ".join(sub_parts), xalign=0)
    sub_lbl.add_css_class("caption")
    sub_lbl.add_css_class("dim-label")
    sub_lbl.add_css_class("tabular-nums")
    info_box.append(sub_lbl)

    item_box.append(info_box)
    return item_box


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
        self.panel.add_css_class("glass-panel")
        self.panel.set_size_request(260, -1)
        self.panel.set_hexpand(False)

        self.search: Gtk.SearchEntry
        self.history_listbox: Gtk.ListBox
        # Compatibilidade: o app expõe `clear_history_btn` como propriedade. Agora
        # é o MenuButton do cabeçalho da sidebar, de onde a ação destrutiva sai
        # (não mais um botão de rodapé no caminho do usuário).
        self.clear_history_btn: Gtk.MenuButton

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

        s_top.append(self._build_overflow_menu())

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

    def _build_overflow_menu(self) -> Gtk.MenuButton:
        """Menu ⋮ do cabeçalho: abriga a ação destrutiva fora do caminho do usuário."""
        self.clear_history_btn = Gtk.MenuButton()
        self.clear_history_btn.set_icon_name("view-more-symbolic")
        self.clear_history_btn.add_css_class("flat")
        self.clear_history_btn.add_css_class("circular")
        self.clear_history_btn.add_css_class("glass-icon-btn")
        self.clear_history_btn.set_tooltip_text("Ações do histórico")

        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)

        clear_item = Gtk.Button()
        clear_item.add_css_class("flat")
        clear_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        clear_icon = Gtk.Image.new_from_icon_name("user-trash-symbolic")
        clear_icon.set_pixel_size(14)
        clear_box.append(clear_icon)
        clear_box.append(Gtk.Label(label="Limpar histórico…", xalign=0))
        clear_item.set_child(clear_box)
        clear_item.connect("clicked", self._on_clear_all_history)
        box.append(clear_item)

        popover.set_child(box)
        self.clear_history_btn.set_popover(popover)
        return self.clear_history_btn

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
        """Preenche a lista lateral com os tópicos recuperados do SQLite, agrupados por data."""
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

        # Ao filtrar, o agrupamento por data perde sentido (o usuário está
        # procurando algo específico, não navegando por recência).
        groups = [("Resultados", topics)] if filter_query else group_topics_by_date(topics)
        for label, items in groups:
            self.history_listbox.append(self._build_group_header(label))
            for topic in items:
                self.history_listbox.append(self._build_topic_row(topic))

    def _build_group_header(self, label: str) -> Gtk.ListBoxRow:
        """Cabeçalho de seção (Hoje/Ontem/...). Não é alvo de clique."""
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(False)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("sidebar-group-label")
        row.set_child(lbl)
        return row

    def _build_topic_row(self, topic: dict) -> Gtk.ListBoxRow:
        """Uma conversa: ícone de tipo, título completo (com tooltip) e horário.

        A lixeira vive num slot de largura fixa à direita: aparece no hover sem
        empurrar o texto (o slot já reserva o espaço) e fica fora do alcance de
        cliques acidentais quando o ponteiro não está sobre a linha.
        """
        row = Gtk.ListBoxRow()
        row.add_css_class("sidebar-chat-row")
        is_active = self.ctx.session.id == topic["id"]
        if is_active:
            row.add_css_class("active")

        item_box = build_topic_identity(self.ctx, topic)
        item_box.set_margin_top(4)
        item_box.set_margin_bottom(4)
        item_box.set_margin_start(4)
        item_box.set_margin_end(4)

        tid = topic["id"]

        del_slot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        del_slot.set_size_request(30, -1)
        del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        del_btn.add_css_class("flat")
        del_btn.add_css_class("circular")
        del_btn.add_css_class("glass-icon-btn")
        del_btn.set_tooltip_text("Excluir conversa")
        del_btn.set_valign(Gtk.Align.CENTER)
        del_btn.set_visible(False)
        del_btn.connect("clicked", lambda _b, t_id=tid: self.on_delete_topic(t_id))
        del_slot.append(del_btn)
        item_box.append(del_slot)

        # Revela a lixeira no hover da linha. O slot fixo evita o "pulo" do texto.
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: del_btn.set_visible(True))
        motion.connect("leave", lambda *_: del_btn.set_visible(False))
        row.add_controller(motion)

        # Teclado: com a linha focada, Delete exclui (a lixeira invisível não é
        # alcançável por Tab, então o atalho cobre quem não usa ponteiro).
        row.set_focusable(True)
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_row_key_pressed, tid)
        row.add_controller(key)

        row.set_child(item_box)
        row._topic_id = topic["id"]
        return row

    def _on_row_key_pressed(self, _ctrl, keyval: int, _code: int, _state: int, topic_id: str) -> bool:
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
            self.on_delete_topic(topic_id)
            return True
        return False

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
        self.clear_history_btn.popdown()
        self.clear_history()

    def clear_history(self) -> None:
        """Limpa todas as conversas do histórico SQLite, após confirmação.

        Ação irreversível sobre dados do usuário: pede confirmação com a
        contagem real, em vez de apagar no clique (o botão antes ficava no
        rodapé, no caminho de quem só queria rolar a lista).
        """
        # Frente B / Defeito 2: limpar o histórico cancela o agente em execução.
        self.ctx.abort_active_agent()  # transicao: limpar historico cancela o agente
        self.ctx._teardown_active_agent_ui()  # transicao: limpar historico libera a UI
        topics = self.ctx.engine.memory.list_chat_topics(limit=9999)
        count = len(topics)
        if count == 0:
            self.ctx.show_toast("Não há conversas para limpar.")
            return

        dialog = Adw.MessageDialog(
            transient_for=self.ctx,
            heading="Limpar todo o histórico?",
            body=f"{count} conversa{'s' if count != 1 else ''} será{'ão' if count != 1 else ''} "
                 "apagada(s) permanentemente. Esta ação não pode ser desfeita.",
        )
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("clear", "Apagar tudo")
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_confirm_clear)
        dialog.present()

    def _on_confirm_clear(self, _dialog, response: str) -> None:
        if response != "clear":
            return
        # O `clear_history` já abortou e liberou a UI ao abrir o diálogo; aqui só
        # resetamos o estado da conversa (Defeito 2 / Frente B).
        self.ctx.engine.memory.clear_all_chat_topics()
        self.ctx.session.reset_new()
        self.ctx.window_title.set_subtitle("Assistente Inteligente")
        self.ctx.chat_stream.rebuild()
        self.populate()
        self.ctx.show_toast("Histórico de conversas completamente limpo.")
