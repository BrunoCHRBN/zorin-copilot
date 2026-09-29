# Decisão de design: o fluxo de conversa é renderizado como uma pilha de cartões
# glassmorphic (usuário à direita em balão, assistente à esquerda em card). Ações
# propostas pela IA ganham uma linha executável cada, com feedback de sucesso/falha.

"""Fluxo de mensagens multi-turn e renderização das ações propostas pela IA."""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from ...ai.actions import ActionPlan, ActionType, DesktopAction
from ...core.session import ChatTurn
from ...core.desktop.env import DESKTOP_LABELS, current_environment
from ...core.desktop_context import DesktopContext
from ...core.context_suggestions import ContextSuggestion, ContextSuggestionEngine
from ...shell.action_status import ActionOutcome, ActionOutcomeRegistry
from ..markdown import format_markdown_to_markup  # noqa: F401 - reexportado por compatibilidade
from .sidebar import build_topic_identity

if TYPE_CHECKING:  # pragma: no cover - apenas para type checking
    from ..app import CopilotWindow

logger = logging.getLogger(__name__)

def get_action_icon(action: DesktopAction) -> str:
    """Retorna o ícone semântico padrão mais adequado para a ação proposta."""
    target_low = action.target.lower()
    if action.action_type == ActionType.LAUNCH_APP:
        if "terminal" in target_low:
            return "utilities-terminal-symbolic"
        if "calc" in target_low:
            return "accessories-calculator-symbolic"
        if "steam" in target_low or "jog" in target_low:
            return "applications-games-symbolic"
        if any(b in target_low for b in ("web", "browser", "firefox", "chrome", "edge", "brave")):
            return "web-browser-symbolic"
        if any(f in target_low for f in ("file", "arquiv", "pasta", "nautilus")):
            return "system-file-manager-symbolic"
        return "application-x-executable-symbolic"
    if action.action_type == ActionType.OPEN_URL:
        return "web-browser-symbolic"
    if action.action_type == ActionType.SYSTEM_CONTROL:
        return "preferences-system-symbolic"
    if action.action_type == ActionType.CLICK:
        return "input-mouse-symbolic"
    if action.action_type == ActionType.NOTIFY:
        return "dialog-information-symbolic"
    if action.action_type == ActionType.FIX_COMMAND:
        return "utilities-terminal-symbolic"
    if action.action_type == ActionType.SMART_OCR:
        return "edit-copy-symbolic"
    if action.action_type == ActionType.MEDIA_CONTROL:
        return "multimedia-player-symbolic"
    if action.action_type == ActionType.WRITE_FILE:
        return "document-save-symbolic"
    if action.action_type == ActionType.OPEN_DOCUMENT:
        return "x-office-document-symbolic"
    if action.action_type == ActionType.ORGANIZE_FILES:
        return "folder-symbolic"
    return "system-run-symbolic"

class TypingIndicator(Gtk.Box):
    """Indicador de "digitando…" estilo iMessage: três pontos que pulsam em onda.

    Usado na bolha pendente do assistente enquanto a IA processa. A animação roda
    num ``GLib.timeout`` que só existe enquanto o widget está visível (map):
    criá-lo no ``__init__`` deixava uma fonte periódica viva mesmo para
    indicadores que nunca chegaram a ser exibidos.
    """

    def __init__(self, n_dots: int = 3):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.add_css_class("typing-indicator")
        self.dots: list[Gtk.Widget] = []
        for _ in range(n_dots):
            dot = Gtk.Box()
            dot.add_css_class("typing-dot")
            dot.set_opacity(0.3)
            self.append(dot)
            self.dots.append(dot)
        self._step = 0
        self._timer = 0
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.connect("unrealize", self._on_unmap)

    def _start(self) -> None:
        if not self._timer:
            # G_PRIORITY_LOW de propósito: uma animação cosmética não deve
            # competir com a entrega da resposta da IA, que chega por
            # GLib.idle_add. Ver "Inanição de prioridade" em
            # docs/ENDEAVOUROS_PORT.md.
            self._timer = GLib.timeout_add(300, self._tick, priority=GLib.PRIORITY_LOW)

    def _stop(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _on_map(self, *_args) -> None:
        self._start()

    def _on_unmap(self, *_args) -> None:
        self._stop()

    def _tick(self) -> bool:
        if not self.get_mapped():
            self._stop()
            return GLib.SOURCE_REMOVE
        self._step = (self._step + 1) % (len(self.dots) + 1)
        for i, dot in enumerate(self.dots):
            # Onda da esquerda p/ direita, com uma pausa (step == n_dots) sem brilho.
            dot.set_opacity(1.0 if self._step == i else 0.3)
        return GLib.SOURCE_CONTINUE

@dataclass
class ActionRowHandle:
    """Trio (linha, botão, chave) de uma ação proposta, para atualização após executar."""

    row: "Adw.ActionRow"
    button: "Gtk.Button"
    action: DesktopAction
    key: str | None

class ChatStreamView:
    """Fluxo rolável de mensagens com tela de boas-vindas e ações executáveis."""

    def __init__(self, ctx: "CopilotWindow"):
        self.ctx = ctx

        self.outcomes = ActionOutcomeRegistry()

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        clamp_chat = Adw.Clamp(maximum_size=820)
        self.stream_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.stream_box.set_margin_start(16)
        self.stream_box.set_margin_end(16)
        self.stream_box.set_margin_top(16)
        self.stream_box.set_margin_bottom(16)

        self.current_context: DesktopContext | None = None
        self.current_suggestions: list[ContextSuggestion] = []

        self.welcome_box = self._build_welcome_box()
        self.stream_box.append(self.welcome_box)

        clamp_chat.set_child(self.stream_box)
        self.scrolled.set_child(clamp_chat)

    # ------------------------------------------------------------------
    # Construção da tela de boas-vindas
    # ------------------------------------------------------------------
    def _welcome_state_text(self) -> str:
        """A linha de estado: o que é esta conversa e onde ela roda.

        Mantém as três partes que o plano pediu, mas a terceira mudou de
        conteúdo. O plano sugeria o aplicativo ativo ("Chrome ativo"); esse fato
        já tem dono — o banner de contexto logo abaixo, que mostra "No foco:
        Chrome: <título>" e se atualiza sozinho. Repeti-lo aqui seria dizer a
        mesma coisa em dois lugares da mesma tela, que é o problema §0.1.

        No lugar dele vai o **ambiente de desktop**. Para este aplicativo isso
        não é enfeite: é o que decide quais automações funcionam — `hyprctl` no
        Hyprland, `gsettings` no GNOME. É a informação que ajusta a expectativa
        de quem vai pedir uma tarefa.
        """
        parts = ["Conversa nova"]

        try:
            env = current_environment()
        except Exception as exc:  # noqa: BLE001
            # A sondagem é leitura de arquivos e de PATH, mas roda no caminho de
            # abertura da janela: se falhar, a tela de boas-vindas ainda tem de
            # aparecer. Fica só "Conversa nova".
            logger.debug("Não foi possível descrever o ambiente: %s", exc)
            return parts[0]

        parts.append(env.distro.display_name)

        desktop = DESKTOP_LABELS.get(
            env.desktop, env.desktop.capitalize() if env.desktop else ""
        )
        if desktop:
            parts.append(desktop)

        return " · ".join(parts)

    def _build_welcome_box(self) -> Gtk.Box:
        welcome_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        welcome_box.set_valign(Gtk.Align.CENTER)
        welcome_box.set_halign(Gtk.Align.CENTER)
        welcome_box.set_margin_top(40)
        welcome_box.set_margin_bottom(20)

        header_welcome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header_welcome.set_halign(Gtk.Align.CENTER)

        # O posto mais visível da tela estava ocupado por uma estrela decorativa
        # (`starred-symbolic`, 44px) que não dizia nada sobre esta máquina nem
        # sobre esta sessão. A mesma área passa a dar orientação: o que é a
        # conversa e onde ela roda. Custa uma linha e responde à primeira
        # pergunta de quem abre a janela.
        self.welcome_state_label = Gtk.Label()
        self.welcome_state_label.add_css_class("caption")
        self.welcome_state_label.add_css_class("dim-label")
        self.welcome_state_label.set_label(self._welcome_state_text())
        header_welcome.append(self.welcome_state_label)

        welcome_title = Gtk.Label(label="<b>Como posso ajudar hoje?</b>", use_markup=True)
        welcome_title.add_css_class("title-2")
        welcome_title.add_css_class("welcome-title")
        header_welcome.append(welcome_title)

        welcome_desc = Gtk.Label(
            label="Peça tarefas no desktop, consulte seus projetos ou converse por voz"
        )
        welcome_desc.add_css_class("caption")
        welcome_desc.add_css_class("welcome-subtitle")
        header_welcome.append(welcome_desc)
        welcome_box.append(header_welcome)

        # Banner de Contexto Ativo (Fase 3: Context Awareness)
        self.context_banner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.context_banner_box.set_halign(Gtk.Align.CENTER)
        self.context_banner_box.add_css_class("active-context-pill")
        self.context_banner_box.set_visible(False)

        self.context_banner_icon = Gtk.Image.new_from_icon_name("window-restore-symbolic")
        self.context_banner_icon.set_pixel_size(14)
        self.context_banner_box.append(self.context_banner_icon)

        self.context_banner_label = Gtk.Label()
        self.context_banner_label.set_use_markup(True)
        self.context_banner_box.append(self.context_banner_label)
        welcome_box.append(self.context_banner_box)

        # Dois grupos com rótulo, em vez de uma grade 2×2 única. Antes, "Resumir
        # esta página" (que fala do que está na tela) e "Voz ao Vivo" (que é um
        # modo de interação) apareciam como pares, sem nada dizendo que são
        # coisas de naturezas diferentes.
        self.suggestions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.suggestions_box.set_halign(Gtk.Align.CENTER)
        self.suggestions_box.set_margin_top(10)
        self._populate_suggestions()
        welcome_box.append(self.suggestions_box)

        # Slot próprio para o "Retomar": a lista vem do histórico, que muda
        # enquanto o app está aberto, então ela é reconstruída à parte — não faz
        # sentido recriar a tela de boas-vindas inteira só porque uma conversa
        # foi criada ou excluída.
        self.resume_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._refresh_resume_section()
        welcome_box.append(self.resume_slot)

        return welcome_box

    def _suggestion_budget(self) -> int:
        """Quantas sugestões o motor pode devolver, segundo a configuração."""
        limit_val = getattr(getattr(self.ctx, "config", None), "context_suggestions_limit", 4)
        try:
            return int(limit_val)
        except (ValueError, TypeError):
            return 4

    def _split_suggestions(
        self, suggestions: list[ContextSuggestion], limit: int
    ) -> tuple[list[ContextSuggestion], list[ContextSuggestion]]:
        """Divide as sugestões em (o que está na tela, como interagir).

        A divisão usa `is_contextual`, que o motor já preenche: é `True` para o
        que foi derivado da janela em foco ou da área de transferência e `False`
        para os modos globais. Nenhum metadado novo — o que separa os dois grupos
        já estava nos dados.

        Uma vaga é reservada para os modos. Com contexto rico o motor gasta o
        orçamento inteiro no que está na tela, e o grupo "Como interagir"
        desapareceria justamente quando o usuário tem mais sobre o que falar.
        A reserva acontece **dentro** do orçamento configurado: uma sugestão da
        tela sai para o modo entrar, em vez de acrescentar um chip acima do
        limite que o usuário escolheu. O descarte é o preço de garantir que a
        tela sempre ofereça por onde começar — e nada se perde de verdade: voz e
        recorte continuam na barra de prompt e no cabeçalho.
        """
        on_screen = [s for s in suggestions if s.is_contextual]
        modes = [s for s in suggestions if not s.is_contextual]

        if not modes and limit > 1:
            modes = [
                s
                for s in ContextSuggestionEngine.get_suggestions(None, limit=1)
                if not s.is_contextual
            ]
            if modes:
                on_screen = on_screen[: limit - len(modes)]

        return on_screen, modes

    def _populate_suggestions(self) -> None:
        while child := self.suggestions_box.get_first_child():
            self.suggestions_box.remove(child)

        limit = self._suggestion_budget()

        enabled_val = getattr(getattr(self.ctx, "config", None), "context_awareness_enabled", True)
        enabled = bool(enabled_val) if not type(enabled_val).__name__.startswith("MagicMock") else True

        if enabled and self.current_context:
            suggestions = ContextSuggestionEngine.get_suggestions(self.current_context, limit=limit)
        else:
            suggestions = ContextSuggestionEngine.get_suggestions(None, limit=limit)

        self.current_suggestions = suggestions

        on_screen, modes = self._split_suggestions(suggestions, limit)

        # O grupo da tela só existe quando há tela sobre o que falar; o dos modos
        # é o piso da tela vazia — sempre há como começar.
        self.screen_group = self._build_suggestion_group(
            "Sobre o que está na tela", on_screen, contextual=True
        )
        self.modes_group = self._build_suggestion_group(
            "Como interagir", modes, contextual=False
        )
        for group in (self.screen_group, self.modes_group):
            if group is not None:
                self.suggestions_box.append(group)

    def _build_suggestion_group(
        self,
        title: str,
        suggestions: list[ContextSuggestion],
        *,
        contextual: bool,
    ) -> Gtk.Box | None:
        """Um grupo rotulado de chips. `None` quando não há o que mostrar."""
        if not suggestions:
            return None

        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        label = Gtk.Label(label=title, xalign=0)
        # `caption` + `dim-label`, e não o tratamento do `.sidebar-group-label`
        # (maiúsculas com letter-spacing): lá os rótulos são uma palavra
        # ("Hoje", "Ontem") e o caixa-alta funciona como marcador; aqui são
        # frases inteiras, e caixa-alta com espaçamento largo em frase longa
        # atrapalha a leitura — além de ser o padrão que o sistema visual do
        # projeto rejeita.
        label.add_css_class("caption")
        label.add_css_class("dim-label")
        group.append(label)

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        grid.set_halign(Gtk.Align.CENTER)
        group.append(grid)

        for idx, sug in enumerate(suggestions):
            grid.attach(self._build_suggestion_chip(sug, contextual), idx % 2, idx // 2, 1, 1)

        return group

    def _build_suggestion_chip(self, sug: ContextSuggestion, contextual: bool) -> Gtk.Button:
        """Um chip acionável da tela de boas-vindas."""
        btn = Gtk.Button()
        btn.add_css_class("card")
        btn.add_css_class("pill")
        btn.add_css_class("glass-chip")
        if contextual:
            btn.add_css_class("context-chip")
        if sug.description:
            btn.set_tooltip_text(sug.description)

        chip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        chip_box.set_halign(Gtk.Align.CENTER)
        chip_icon = Gtk.Image.new_from_icon_name(sug.icon)
        chip_icon.set_pixel_size(16)
        chip_box.append(chip_icon)

        chip_lbl = Gtk.Label(label=sug.title)
        chip_box.append(chip_lbl)
        btn.set_child(chip_box)

        def make_chip_click(s=sug):
            trigger_fn = getattr(self.ctx, "_trigger_suggestion", None)
            if callable(trigger_fn):
                return lambda _: trigger_fn(s)
            return lambda _: self.ctx._trigger_prompt(s.prompt_template)

        btn.connect("clicked", make_chip_click())
        return btn

    # ------------------------------------------------------------------
    # Retomar
    # ------------------------------------------------------------------
    RESUME_LIMIT = 3

    def _recent_topics(self) -> list[dict]:
        """As últimas conversas do histórico, no máximo `RESUME_LIMIT`.

        Devolve `[]` em vez de levantar quando a memória não responde: a tela de
        boas-vindas é a primeira coisa que aparece, e não pode depender do banco
        para existir. Um histórico indisponível é um motivo para não mostrar a
        seção, não para a janela abrir vazia.
        """
        memory = getattr(getattr(self.ctx, "engine", None), "memory", None)
        if memory is None:
            return []
        try:
            topics = memory.list_chat_topics(limit=self.RESUME_LIMIT)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Não foi possível listar conversas recentes: %s", exc)
            return []
        if not isinstance(topics, (list, tuple)):
            return []
        return list(topics[: self.RESUME_LIMIT])

    def _build_resume_section(self) -> Gtk.Box | None:
        """As últimas conversas, na própria tela vazia.

        Sem isto, quem volta ao app tinha de abrir a barra lateral para retomar:
        a tela de boas-vindas só oferecia começar do zero. Reusa a identidade
        visual da linha da barra lateral (`build_topic_identity`) **e a mesma
        classe de linha**, então o hover e o estado ativo vêm de graça e não há
        um segundo padrão de "linha de conversa" para manter.

        Não leva lixeira de propósito: excluir é ação destrutiva e mora onde já
        tem confirmação e contexto — a barra lateral. Uma tela de partida não é
        lugar de apagar coisas.
        """
        topics = self._recent_topics()
        if not topics:
            return None

        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        label = Gtk.Label(label="Retomar", xalign=0)
        label.add_css_class("caption")
        label.add_css_class("dim-label")
        section.append(label)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.connect("row-activated", self._on_resume_activated)
        # Largura fixa para as três linhas terem a mesma medida e serem
        # varridas como coluna. É mais larga que os 260px da barra lateral
        # porque aqui há espaço sobrando e o mesmo conteúdo (título + horário)
        # não deve encolher só por estar em outra tela.
        listbox.set_size_request(380, -1)
        listbox.set_halign(Gtk.Align.CENTER)

        for topic in topics:
            listbox.append(self._build_resume_row(topic))

        section.append(listbox)
        return section

    def _build_resume_row(self, topic: dict) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.add_css_class("sidebar-chat-row")
        row.set_child(build_topic_identity(self.ctx, topic))
        row._topic_id = topic.get("id")
        return row

    def _on_resume_activated(self, _listbox, row) -> None:
        topic_id = getattr(row, "_topic_id", None)
        if topic_id:
            self.ctx._resume_topic(topic_id)

    def _refresh_resume_section(self) -> None:
        """Reconstrói a seção "Retomar" a partir do histórico atual."""
        while child := self.resume_slot.get_first_child():
            self.resume_slot.remove(child)
        section = self._build_resume_section()
        if section is not None:
            self.resume_slot.append(section)

    def update_context(self, context: DesktopContext | None) -> None:
        """Atualiza dinamicamente as sugestões e o banner de contexto ativo."""
        self.current_context = context
        enabled = getattr(getattr(self.ctx, "config", None), "context_awareness_enabled", True)

        if enabled and context and context.is_contextual:
            if context.has_active_window:
                app_disp = context.app_name or "Aplicativo"
                target_disp = f": <b>{html.escape(context.extracted_target)}</b>" if context.extracted_target else ""
                icon_map = {
                    "code_editor": "applications-development-symbolic",
                    "browser": "web-browser-symbolic",
                    "terminal": "utilities-terminal-symbolic",
                    "document": "x-office-document-symbolic",
                    "file_manager": "system-file-manager-symbolic",
                    "communication": "chat-symbolic",
                    "media": "multimedia-volume-control-symbolic",
                }
                icon_name = icon_map.get(getattr(context.category, "value", ""), "window-restore-symbolic")
                self.context_banner_icon.set_from_icon_name(icon_name)
                self.context_banner_label.set_markup(f"No foco: {html.escape(app_disp)}{target_disp}")
                self.context_banner_box.set_visible(True)
            elif getattr(context.clipboard_category, "value", "") == "error_traceback":
                self.context_banner_icon.set_from_icon_name("dialog-error-symbolic")
                self.context_banner_label.set_markup("Área de transferência: <b>Traceback de erro detectado</b>")
                self.context_banner_box.set_visible(True)
            elif getattr(context.clipboard_category, "value", "") == "url":
                self.context_banner_icon.set_from_icon_name("web-browser-symbolic")
                self.context_banner_label.set_markup("Área de transferência: <b>Link copiado</b>")
                self.context_banner_box.set_visible(True)
            else:
                self.context_banner_box.set_visible(False)
        else:
            self.context_banner_box.set_visible(False)

        self._populate_suggestions()

    # ------------------------------------------------------------------
    # Ciclo de vida do fluxo
    # ------------------------------------------------------------------
    def rebuild(self) -> None:
        """Reconstrói todo o fluxo de conversa a partir dos turnos da sessão ativa."""
        while child := self.stream_box.get_first_child():
            self.stream_box.remove(child)

        if not self.ctx.session.turns:
            # A lista "Retomar" é lida do histórico, que mudou desde a última vez
            # que esta tela apareceu (uma conversa foi criada, outra excluída).
            # Refazê-la aqui mantém a tela honesta sem recriar o widget inteiro.
            self._refresh_resume_section()
            self.stream_box.append(self.welcome_box)
            self.welcome_box.set_visible(True)
            return

        self.welcome_box.set_visible(False)
        # Contagem de respostas computada UMA vez por rebuild (evita O(n²) — antes
        # cada create_turn_widget recontava todos os turnos).
        total_answers = sum(1 for t in self.ctx.session.turns if t.answer and t.answer.strip())
        for turn in self.ctx.session.turns:
            # O plano vem do turno, não de `ctx.current_plan`: antes só o último
            # turno recebia plano e os botões de ação das respostas anteriores
            # desapareciam a cada reconstrução do fluxo.
            self.stream_box.append(
                self.create_turn_widget(turn, plan=turn.plan, n_answers=total_answers)
            )

        self.scroll_to_bottom()

    def scroll_to_bottom(self) -> None:
        """Rola o fluxo rolável de mensagens até o fim com fluidez.

        Um único agendamento (idle) basta; o timeout de 60 ms em duplicata só
        causava trabalho redundante sob stream contínuo.
        """
        def _do_scroll():
            adj = self.scrolled.get_vadjustment()
            if adj:
                adj.set_value(adj.get_upper() - adj.get_page_size())
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_do_scroll)

    # ------------------------------------------------------------------
    # Renderização de turnos
    # ------------------------------------------------------------------
    def create_turn_widget(
        self,
        turn: ChatTurn,
        plan: ActionPlan | None = None,
        image_bytes: bytes | None = None,
        is_pending: bool = False,
        n_answers: int | None = None,
    ) -> Gtk.Widget:
        """Constrói o widget de um turno completo (pergunta do usuário + resposta do assistente)."""
        turn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        turn_box.append(self._build_user_bubble(turn, image_bytes))
        if getattr(turn, "agent_result", None):
            from .agent_card import AgentExecutionWidget

            turn_box.append(AgentExecutionWidget.from_result_dict(turn.agent_result, self.ctx, turn))
        else:
            turn_box.append(self._build_assistant_card(turn, plan, is_pending, n_answers))
        return turn_box

    def _build_user_bubble(self, turn: ChatTurn, image_bytes: bytes | None) -> Gtk.Widget:
        user_wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        user_wrap.set_halign(Gtk.Align.END)
        user_wrap.set_hexpand(True)

        user_bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        user_bubble.add_css_class("user-chat-bubble")

        u_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        u_icon = Gtk.Image.new_from_icon_name("avatar-default-symbolic")
        u_icon.set_pixel_size(14)
        u_hdr.append(u_icon)

        u_name = Gtk.Label(label="<b>Você</b>", use_markup=True, xalign=0)
        u_name.add_css_class("caption")
        u_hdr.append(u_name)
        user_bubble.append(u_hdr)

        if image_bytes:
            try:
                pic = Gtk.Picture()
                pic.set_can_shrink(True)
                pic.set_content_fit(Gtk.ContentFit.CONTAIN)
                pic.set_size_request(-1, 120)
                pic.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(image_bytes)))
                user_bubble.append(pic)
            except Exception:
                pass

        prompt_lbl = Gtk.Label(label=turn.prompt or "...", xalign=0)
        prompt_lbl.set_wrap(True)
        prompt_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        prompt_lbl.set_selectable(True)
        user_bubble.append(prompt_lbl)

        user_wrap.append(user_bubble)
        return user_wrap

    def _build_assistant_card(
        self,
        turn: ChatTurn,
        plan: ActionPlan | None,
        is_pending: bool,
        n_answers: int | None = None,
    ) -> Gtk.Widget:
        ctx = self.ctx
        assistant_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        assistant_card.add_css_class("card")
        assistant_card.add_css_class("assistant-message-card")

        a_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        a_icon = Gtk.Image.new_from_icon_name("starred-symbolic")
        a_icon.set_pixel_size(18)
        a_hdr.append(a_icon)

        a_name = Gtk.Label(label="<b>Zorin Copilot</b>", use_markup=True, xalign=0)
        a_name.add_css_class("heading")
        a_hdr.append(a_name)

        prov_str = ctx.config.gemini_model if ctx.config.provider == "gemini" else ctx.config.provider
        a_badge = Gtk.Label(label=f"● {prov_str}", xalign=0)
        a_badge.add_css_class("caption")
        a_badge.add_css_class("dim-label")
        a_badge.set_hexpand(True)
        a_hdr.append(a_badge)

        if not is_pending:
            a_hdr.append(self._build_copy_button(turn))
            # Só faz sentido comparar quando há ao menos duas respostas na conversa.
            # Calculado uma vez por rebuild (passado via parâmetro) para não virar O(n²).
            if n_answers is None:
                n_answers = sum(1 for t in ctx.session.turns if t.answer and t.answer.strip())
            if n_answers >= 2:
                a_hdr.append(self._build_compare_button(turn))
        assistant_card.append(a_hdr)

        if is_pending:
            typing_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            typing_box.set_margin_top(8)
            typing_box.set_margin_bottom(8)
            typing_box.append(TypingIndicator())
            typing_lbl = Gtk.Label(label="digitando…", xalign=0)
            typing_lbl.add_css_class("dim-label")
            typing_box.append(typing_lbl)
            assistant_card.append(typing_box)
            return assistant_card

        markup = format_markdown_to_markup(turn.answer)
        ans_lbl = Gtk.Label(xalign=0, yalign=0)
        ans_lbl.set_wrap(True)
        ans_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        ans_lbl.set_selectable(True)
        ans_lbl.set_use_markup(True)
        ans_lbl.set_markup(markup)
        ans_lbl.set_margin_top(2)
        ans_lbl.set_margin_bottom(4)
        assistant_card.append(ans_lbl)

        ctx.answer_label = ans_lbl
        ctx._raw_answer_text = turn.answer

        # Modo de estudos: quando o turno carrega um SolutionGraph (resposta de
        # matemática), oferece abrir o raciocínio passo a passo no Quadro Negro.
        # Também aparece quando só há um `solution_problem` (matemática que a
        # álgebra pura não cobre): aí o grafo é resolvido sob demanda no clique.
        solution = getattr(turn, "solution", None)
        problem = getattr(turn, "solution_problem", "")
        if solution is not None or problem:
            assistant_card.append(self._build_blackboard_button(turn))

        if plan and plan.actions:
            self._append_actions(assistant_card, plan, turn)

        ocr_text = (plan.extracted_text if plan else None) or getattr(ctx, "_current_ocr_text", None)
        if ocr_text:
            assistant_card.append(self._build_ocr_button(ocr_text))

        return assistant_card

    def _build_copy_button(self, turn: ChatTurn) -> Gtk.Button:
        copy_b = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        copy_b.set_tooltip_text("Copiar Resposta")
        copy_b.add_css_class("flat")
        copy_b.add_css_class("circular")
        copy_b.add_css_class("glass-icon-btn")
        txt_to_copy = turn.answer

        def on_copy(_b, t=txt_to_copy, btn=copy_b):
            disp = Gdk.Display.get_default()
            if disp:
                disp.get_clipboard().set(t)
                btn.set_icon_name("emblem-ok-symbolic")
                self.ctx.show_toast("Resposta copiada para a área de transferência")
                GLib.timeout_add(2000, self._restore_copy_icon(btn))

        copy_b.connect("clicked", on_copy)
        return copy_b

    def _build_compare_button(self, turn: ChatTurn) -> Gtk.Button:
        cmp_b = Gtk.Button(label="Comparar")
        cmp_b.set_tooltip_text("Comparar esta resposta com outra da conversa")
        cmp_b.add_css_class("flat")
        cmp_b.add_css_class("pill")
        cmp_b.add_css_class("glass-pill")
        cmp_b.connect("clicked", lambda _: self.ctx._open_compare(turn))
        return cmp_b

    def _build_blackboard_button(self, turn: ChatTurn) -> Gtk.Button:
        """Botão que abre o raciocínio estruturado no Quadro Negro Digital.

        Só é criado quando o turno carrega um ``SolutionGraph`` OU um
        ``solution_problem`` (matemática que a álgebra pura não cobre). Com grafo,
        abre direto; só com o problema, delega à janela a resolução sob demanda
        (model-backed). Mesma linguagem visual dos chips/pílulas do projeto.
        """
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.add_css_class("pill")
        btn.add_css_class("glass-pill")
        btn.set_halign(Gtk.Align.START)
        btn.set_margin_top(4)
        btn.set_tooltip_text("Abrir o raciocínio passo a passo no Quadro Negro")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon = Gtk.Image.new_from_icon_name("view-grid-symbolic")
        icon.set_pixel_size(14)
        box.append(icon)

        label = Gtk.Label(label="Ver no Quadro Negro")
        label.add_css_class("caption")
        box.append(label)
        btn.set_child(box)

        def on_open(_b, t=turn):
            solution = getattr(t, "solution", None)
            if solution is not None:
                opener = getattr(self.ctx, "show_blackboard", None)
                if callable(opener):
                    opener(solution)
                return
            lazy = getattr(self.ctx, "show_blackboard_for_problem", None)
            if callable(lazy):
                lazy(getattr(t, "solution_problem", ""))

        btn.connect("clicked", on_open)
        return btn

    @staticmethod
    def _restore_copy_icon(btn: Gtk.Button):
        """Devolve o ícone original do botão de cópia após o feedback."""
        def restore():
            btn.set_icon_name("edit-copy-symbolic")
            return GLib.SOURCE_REMOVE
        return restore

    def _build_ocr_button(self, ocr_text: str) -> Gtk.Button:
        ocr_btn = Gtk.Button()
        ocr_btn.add_css_class("flat")
        ocr_btn.add_css_class("pill")
        ocr_btn.add_css_class("glass-pill")
        ocr_btn.set_halign(Gtk.Align.START)
        ocr_btn.set_margin_top(4)

        ocr_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        ocr_ic = Gtk.Image.new_from_icon_name("edit-copy-symbolic")
        ocr_ic.set_pixel_size(14)
        ocr_box.append(ocr_ic)
        ocr_lbl = Gtk.Label(label="Copiar Texto/Código Extraído")
        ocr_lbl.add_css_class("caption")
        ocr_box.append(ocr_lbl)
        ocr_btn.set_child(ocr_box)

        def on_copy_ocr(_b, ot=ocr_text, ol=ocr_lbl):
            from ...core.clipboard import ClipboardService
            ClipboardService.set_text(ot)
            ol.set_text("Conteúdo copiado!")
            self.ctx.show_toast("Texto copiado para a área de transferência!")
            GLib.timeout_add(2000, self._restore_ocr_label(ol))

        ocr_btn.connect("clicked", on_copy_ocr)
        return ocr_btn

    @staticmethod
    def _restore_ocr_label(label: Gtk.Label):
        def restore():
            label.set_text("Copiar Texto/Código Extraído")
            return GLib.SOURCE_REMOVE
        return restore

    def _append_actions(self, assistant_card: Gtk.Box, plan: ActionPlan, turn: ChatTurn) -> None:
        executable = [a for a in plan.actions if a.action_type != ActionType.ANSWER]
        if not executable:
            return

        acts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        acts_box.set_margin_top(8)

        acts_title = Gtk.Label(
            label=f"<b>Ações Propostas ({len(executable)}):</b>", use_markup=True, xalign=0
        )
        acts_title.add_css_class("caption")
        acts_title.add_css_class("dim-label")
        acts_box.append(acts_title)

        handles = [self._create_action_handle(act, turn, i) for i, act in enumerate(executable)]
        for handle in handles:
            acts_box.append(handle.row)

        if len(executable) > 1:
            acts_box.append(self._build_execute_all_button(plan, turn, handles))

        assistant_card.append(acts_box)

    def _build_execute_all_button(
        self, plan: ActionPlan, turn: ChatTurn, handles: list[ActionRowHandle]
    ) -> Gtk.Button:
        """Botão único para executar o plano inteiro, com contagem honesta de falhas."""
        ctx = self.ctx
        total = len(handles)
        exec_all = Gtk.Button(label=f"Executar Todas as {total} Ações")
        exec_all.add_css_class("suggested-action")
        exec_all.add_css_class("pill")
        exec_all.set_halign(Gtk.Align.START)
        exec_all.set_margin_top(4)

        def on_exec_all(_):
            exec_all.set_sensitive(False)
            exec_all.set_label("Executando...")
            reports = ctx.execute_plan_with_undo(plan)

            ok = 0
            failed = 0
            for report, handle in zip(reports, handles):
                outcome = ActionOutcome.from_report(report)
                if handle.key:
                    self.outcomes.record(handle.key, outcome)
                self._apply_outcome_to_row(handle.row, handle.button, outcome)
                ctx.engine.memory.log_action(
                    prompt=turn.prompt,
                    action_type=report.action.action_type.value,
                    target=report.action.target,
                    params=report.action.params,
                    success=report.success,
                    message=report.message,
                )
                if report.success:
                    ok += 1
                else:
                    failed += 1

            skipped = total - len(reports)
            exec_all.remove_css_class("suggested-action")
            if failed:
                exec_all.add_css_class("destructive-action")
                exec_all.set_label(f"{ok} ok • {failed} falharam")
                ctx.show_toast(f"{ok} ação(ões) executada(s), {failed} falharam.")
            else:
                exec_all.add_css_class("flat")
                suffix = f" ({skipped} interrompidas)" if skipped else ""
                exec_all.set_label(f"Todas executadas ({ok}){suffix}")
                ctx.show_toast(f"{ok} ações executadas com sucesso!")

        exec_all.connect("clicked", on_exec_all)
        return exec_all

    # ------------------------------------------------------------------
    # Linhas de ação proposta
    # ------------------------------------------------------------------
    def create_action_row(
        self,
        action: DesktopAction,
        turn: ChatTurn | None = None,
        index: int = 0,
    ) -> Gtk.Widget:
        """Renderiza uma linha de ação proposta com ícone semântico e botão de execução."""
        return self._create_action_handle(action, turn, index).row

    def _create_action_handle(
        self,
        action: DesktopAction,
        turn: ChatTurn | None,
        index: int,
    ) -> ActionRowHandle:
        """Monta a linha e devolve também o botão e a chave, para atualização posterior.

        Se a ação já foi executada — nesta sessão ou em uma reconstrução do fluxo —
        a linha nasce direto no estado final, com a mensagem de sucesso ou de falha
        visível. Uma falha não pode simplesmente desaparecer do histórico.
        """
        badge_desc = {
            ActionType.LAUNCH_APP: "abrir aplicativo",
            ActionType.OPEN_URL: "abrir link web",
            ActionType.SYSTEM_CONTROL: "configuração do sistema",
            ActionType.CLICK: "interação acessível",
            ActionType.NOTIFY: "notificação",
            ActionType.CAPTURE_SCREEN: "visão da tela",
            ActionType.FIX_COMMAND: "auto-cura do sistema",
            ActionType.SMART_OCR: "smart ocr",
            ActionType.MEDIA_CONTROL: "controle de mídia",
            ActionType.WRITE_FILE: "salvar documento",
            ActionType.ORGANIZE_FILES: "organização de arquivos",
        }.get(action.action_type, action.action_type.value)

        row, exec_label = self._build_action_row_content(action, badge_desc)
        row.add_css_class("card")
        row.add_css_class("glass-row")

        key = (
            ActionOutcomeRegistry.make_key(turn.id, index, action)
            if turn is not None
            else None
        )
        outcome = self.outcomes.get(key) if key else None

        if outcome is not None:
            icon_name = "emblem-ok-symbolic" if outcome.success else "dialog-error-symbolic"
        elif action.action_type == ActionType.CAPTURE_SCREEN:
            icon_name = "camera-photo-symbolic"
        else:
            icon_name = get_action_icon(action)

        prefix_icon = Gtk.Image.new_from_icon_name(icon_name)
        prefix_icon.set_pixel_size(20)
        if outcome is not None:
            prefix_icon.add_css_class("success" if outcome.success else "error")
        row.add_prefix(prefix_icon)

        exec_btn = Gtk.Button(label=exec_label)
        exec_btn.add_css_class("suggested-action")
        exec_btn.add_css_class("pill")
        exec_btn.set_valign(Gtk.Align.CENTER)

        if action.action_type == ActionType.CAPTURE_SCREEN:
            is_area_target = action.target == "area"
            exec_btn.connect(
                "clicked",
                lambda _, a=is_area_target: self.ctx._start_screen_capture(interactive=a),
            )
        else:
            exec_btn.connect(
                "clicked",
                self._make_exec_handler(action, exec_btn, turn=turn, index=index, row=row),
            )

        if outcome is not None:
            self._apply_outcome_to_row(row, exec_btn, outcome)

        row.add_suffix(exec_btn)
        return ActionRowHandle(row=row, button=exec_btn, action=action, key=key)

    def _apply_outcome_to_row(
        self, row: Adw.ActionRow, btn: Gtk.Button, outcome: ActionOutcome
    ) -> None:
        """Pinta a linha com o resultado da execução e revela a mensagem recebida."""
        btn.remove_css_class("suggested-action")
        btn.remove_css_class("destructive-action")
        row.remove_css_class("action-failed")
        if outcome.undone:
            # Executou e voltou atrás: não é sucesso pendente nem erro.
            btn.add_css_class("flat")
            btn.set_label("Desfeito ↩")
            btn.set_sensitive(False)
            row.add_css_class("action-done")
            row.remove_css_class("action-failed")
        elif outcome.success:
            btn.add_css_class("flat")
            btn.set_label("Executado ✓")
            row.add_css_class("action-done")
        else:
            # Falha continua clicável: o botão vira "Tentar novamente".
            btn.add_css_class("destructive-action")
            btn.set_label("Tentar novamente")
            btn.set_sensitive(True)
            row.add_css_class("action-failed")

        # Guarda o subtítulo original para que novas tentativas não empilhem linhas.
        base = getattr(row, "_zc_base_subtitle", None)
        if base is None:
            base = (row.get_subtitle() or "").rstrip()
            row._zc_base_subtitle = base

        detail = outcome.message if not row.get_use_markup() else html.escape(outcome.message)
        prefix = "• "
        row.set_subtitle(f"{base}\n{prefix}{detail}" if base else f"{prefix}{detail}")
        row.set_subtitle_lines(3)

    def _build_action_row_content(self, action: DesktopAction, badge_desc: str):
        """Monta título/subtítulo específicos por tipo de ação e o rótulo do botão."""
        if action.action_type == ActionType.FIX_COMMAND:
            cmd_show = action.params.get("command") or action.target
            row = Adw.ActionRow(
                title=f"<b>Auto-recuperação: {html.escape(action.target)}</b>",
                subtitle=f"Comando: <tt><b>{html.escape(cmd_show)}</b></tt>",
            )
            row.set_use_markup(True)
            return row, "Executar Correção"

        if action.action_type == ActionType.SMART_OCR:
            preview_txt = (action.target[:42] + "...") if len(action.target) > 42 else action.target
            row = Adw.ActionRow(
                title=f"<b>Smart OCR: {html.escape(action.describe())}</b>",
                subtitle=f"Texto: {html.escape(preview_txt)}",
            )
            row.set_use_markup(True)
            return row, "Copiar Conteúdo"

        if action.action_type == ActionType.WRITE_FILE:
            dest_dir = action.params.get("directory") or "~/Documentos/Relatorios"
            row = Adw.ActionRow(
                title=f"<b>Salvar: {html.escape(action.describe())}</b>",
                subtitle=f"Destino: <tt>{html.escape(dest_dir)}</tt>",
            )
            row.set_use_markup(True)
            return row, "Salvar Arquivo"

        if action.action_type == ActionType.ORGANIZE_FILES:
            target_dir = action.params.get("directory") or "~/Downloads"
            row = Adw.ActionRow(
                title=f"<b>Organizar: {html.escape(action.describe())}</b>",
                subtitle=f"Pasta: <tt>{html.escape(target_dir)}</tt> (Lixeira reversível)",
            )
            row.set_use_markup(True)
            return row, "Organizar Agora"

        if action.action_type == ActionType.MEDIA_CONTROL:
            row = Adw.ActionRow(
                title=f"<b>Mídia: {html.escape(action.describe())}</b>",
                subtitle="Spotify / Reprodutor ativo MPRIS2",
            )
            row.set_use_markup(True)
            return row, "Controlar"

        row = Adw.ActionRow(
            title=action.describe(),
            subtitle=f"Tipo: {badge_desc}",
        )
        exec_label = (
            "Recortar Agora"
            if (action.action_type == ActionType.CAPTURE_SCREEN and action.target == "area")
            else "Executar"
        )
        return row, exec_label

    def _make_exec_handler(
        self,
        action: DesktopAction,
        btn: Gtk.Button,
        turn: ChatTurn | None = None,
        index: int = 0,
        row: Adw.ActionRow | None = None,
    ):
        """Cria o handler de execução individual de uma ação proposta.

        O resultado fica registrado em ``self.outcomes`` e visível na própria linha,
        então a falha sobrevive tanto ao toast quanto a uma reconstrução do fluxo.
        """
        ctx = self.ctx
        key = (
            ActionOutcomeRegistry.make_key(turn.id, index, action)
            if turn is not None
            else None
        )

        def handler(_):
            btn.set_sensitive(False)
            btn.set_label("Executando...")
            single_plan = ActionPlan(
                thought=ctx.current_plan.thought if ctx.current_plan else "",
                actions=[action],
            )
            # `on_undone` repinta só esta linha: reconstruir o fluxo inteiro
            # faria as ações de turnos antigos desaparecerem.
            def repaint(ok: bool, message: str) -> None:
                undone_outcome = ActionOutcome(success=ok, message=message, undone=True)
                if key:
                    self.outcomes.record(key, undone_outcome)
                if row is not None:
                    self._apply_outcome_to_row(row, btn, undone_outcome)

            reports = ctx.execute_plan_with_undo(
                single_plan, ui_key=key, on_undone=repaint
            )
            rep = reports[0] if reports else None

            if rep is None:
                outcome = ActionOutcome(success=False, message="O executor não retornou resultado.")
            else:
                outcome = ActionOutcome.from_report(rep)

            if key:
                self.outcomes.record(key, outcome)

            if row is not None:
                self._apply_outcome_to_row(row, btn, outcome)

            ctx.show_toast(outcome.message)

            ctx.engine.memory.log_action(
                prompt=(turn.prompt if turn else "") or ctx.entry.get_text().strip(),
                action_type=action.action_type.value,
                target=action.target,
                params=action.params,
                success=outcome.success,
                message=outcome.message,
            )

        return handler
