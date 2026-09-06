# Decisão de design: o fluxo de conversa é renderizado como uma pilha de cartões
# glassmorphic (usuário à direita em balão, assistente à esquerda em card). Ações
# propostas pela IA ganham uma linha executável cada, com feedback de sucesso/falha.

"""Fluxo de mensagens multi-turn e renderização das ações propostas pela IA."""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from ...ai.actions import ActionPlan, ActionType, DesktopAction
from ...core.session import ChatTurn

if TYPE_CHECKING:  # pragma: no cover - apenas para type checking
    from ..app import CopilotWindow


def format_markdown_to_markup(text: str) -> str:
    """Converte markdown comum (negrito, itálico, código, links) em GTK/Pango markup válido."""
    if not text:
        return ""
    try:
        s = html.escape(text)

        # 1. Blocos de código multilinhas: ```lang\ncode\n```
        s = re.sub(
            r"```(?:[a-zA-Z0-9_-]+)?\n?(.*?)```",
            lambda m: f"\n<tt><b>{m.group(1).strip()}</b></tt>\n",
            s,
            flags=re.DOTALL,
        )

        # 2. Código inline / caminhos de diretório: `código` -> <tt><b>código</b></tt>
        s = re.sub(r"`([^`\n]+)`", r"<tt><b>\1</b></tt>", s)

        # 3. Links markdown: [texto](url) -> <a href="url">texto</a>
        s = re.sub(
            r"\[([^\]]+)\]\((https?://[^\s\)]+)\)",
            lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>',
            s,
        )

        # 4. Negrito: **texto** ou __texto__ -> <b>texto</b>
        s = re.sub(r"\*\*([^\*\n]+)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"__([^_\n]+)__", r"<b>\1</b>", s)

        # 5. Itálico: *texto* -> <i>texto</i>
        s = re.sub(r"(?<!\*)\*([^\*\n]+)\*(?!\*)", r"<i>\1</i>", s)

        # 6. Marcadores de lista
        s = re.sub(r"(?m)^[\t ]*[-*]\s+", "  • ", s)

        return s
    except Exception:
        return html.escape(text)


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
    if action.action_type == ActionType.ORGANIZE_FILES:
        return "folder-symbolic"
    return "system-run-symbolic"


class ChatStreamView:
    """Fluxo rolável de mensagens com tela de boas-vindas e ações executáveis."""

    def __init__(self, ctx: "CopilotWindow"):
        self.ctx = ctx

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        clamp_chat = Adw.Clamp(maximum_size=820)
        self.stream_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.stream_box.set_margin_start(16)
        self.stream_box.set_margin_end(16)
        self.stream_box.set_margin_top(16)
        self.stream_box.set_margin_bottom(16)

        self.welcome_box = self._build_welcome_box()
        self.stream_box.append(self.welcome_box)

        clamp_chat.set_child(self.stream_box)
        self.scrolled.set_child(clamp_chat)

    # ------------------------------------------------------------------
    # Construção da tela de boas-vindas
    # ------------------------------------------------------------------
    def _build_welcome_box(self) -> Gtk.Box:
        welcome_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        welcome_box.set_valign(Gtk.Align.CENTER)
        welcome_box.set_halign(Gtk.Align.CENTER)
        welcome_box.set_margin_top(40)
        welcome_box.set_margin_bottom(20)

        header_welcome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header_welcome.set_halign(Gtk.Align.CENTER)

        welcome_icon = Gtk.Image.new_from_icon_name("system-help-symbolic")
        welcome_icon.set_pixel_size(44)
        welcome_icon.add_css_class("welcome-icon")
        header_welcome.append(welcome_icon)

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

        welcome_box.append(self._build_suggestions_grid())
        return welcome_box

    def _build_suggestions_grid(self) -> Gtk.Grid:
        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        grid.set_halign(Gtk.Align.CENTER)
        grid.set_margin_top(10)

        suggestions = [
            ("audio-input-microphone-symbolic", "Voz ao Vivo (Gemini Live)", "voz_ao_vivo", 0, 0),
            ("edit-cut-symbolic", "Recortar Área da Tela", "recortar_area", 1, 0),
            ("edit-paste-symbolic", "Analisar Copiado", "analisar_copiado", 0, 1),
            ("weather-clear-night-symbolic", "Alternar modo escuro", "ativar modo escuro", 1, 1),
        ]

        for icon_name, label_text, prompt_val, col, row in suggestions:
            btn = Gtk.Button()
            btn.add_css_class("card")
            btn.add_css_class("pill")
            btn.add_css_class("glass-chip")

            chip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            chip_box.set_halign(Gtk.Align.CENTER)
            chip_icon = Gtk.Image.new_from_icon_name(icon_name)
            chip_icon.set_pixel_size(16)
            chip_box.append(chip_icon)

            chip_lbl = Gtk.Label(label=label_text)
            chip_box.append(chip_lbl)
            btn.set_child(chip_box)

            def make_chip_click(p=prompt_val):
                return lambda _: self.ctx._trigger_prompt(p)

            btn.connect("clicked", make_chip_click())
            grid.attach(btn, col, row, 1, 1)

        return grid

    # ------------------------------------------------------------------
    # Ciclo de vida do fluxo
    # ------------------------------------------------------------------
    def rebuild(self) -> None:
        """Reconstrói todo o fluxo de conversa a partir dos turnos da sessão ativa."""
        while child := self.stream_box.get_first_child():
            self.stream_box.remove(child)

        if not self.ctx.session.turns:
            self.stream_box.append(self.welcome_box)
            self.welcome_box.set_visible(True)
            return

        self.welcome_box.set_visible(False)
        for i, turn in enumerate(self.ctx.session.turns):
            is_last = i == len(self.ctx.session.turns) - 1
            plan_to_use = self.ctx.current_plan if is_last else None
            self.stream_box.append(self.create_turn_widget(turn, plan=plan_to_use))

        self.scroll_to_bottom()

    def scroll_to_bottom(self) -> None:
        """Rola o fluxo rolável de mensagens até o fim com fluidez."""
        def _do_scroll():
            adj = self.scrolled.get_vadjustment()
            if adj:
                adj.set_value(adj.get_upper() - adj.get_page_size())
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_do_scroll)
        GLib.timeout_add(60, _do_scroll)

    # ------------------------------------------------------------------
    # Renderização de turnos
    # ------------------------------------------------------------------
    def create_turn_widget(
        self,
        turn: ChatTurn,
        plan: ActionPlan | None = None,
        image_bytes: bytes | None = None,
        is_pending: bool = False,
    ) -> Gtk.Widget:
        """Constrói o widget de um turno completo (pergunta do usuário + resposta do assistente)."""
        turn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        turn_box.append(self._build_user_bubble(turn, image_bytes))
        turn_box.append(self._build_assistant_card(turn, plan, is_pending))
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
    ) -> Gtk.Widget:
        ctx = self.ctx
        assistant_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        assistant_card.add_css_class("card")
        assistant_card.add_css_class("assistant-message-card")

        a_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        a_icon = Gtk.Image.new_from_icon_name("system-help-symbolic")
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
        assistant_card.append(a_hdr)

        if is_pending:
            spin_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            spin_box.set_margin_top(8)
            spin_box.set_margin_bottom(8)
            sp = Gtk.Spinner()
            sp.start()
            spin_box.append(sp)
            spin_lbl = Gtk.Label(label="Pensando...", xalign=0)
            spin_lbl.add_css_class("dim-label")
            spin_box.append(spin_lbl)
            assistant_card.append(spin_box)
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
                self.ctx.show_toast("✓ Resposta copiada!")
                GLib.timeout_add(2000, self._restore_copy_icon(btn))

        copy_b.connect("clicked", on_copy)
        return copy_b

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
            ol.set_text("✓ Conteúdo Copiado!")
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

        for act in executable:
            acts_box.append(self.create_action_row(act))

        if len(executable) > 1:
            acts_box.append(self._build_execute_all_button(plan, turn, len(executable)))

        assistant_card.append(acts_box)

    def _build_execute_all_button(self, plan: ActionPlan, turn: ChatTurn, count: int) -> Gtk.Button:
        ctx = self.ctx
        exec_all = Gtk.Button(label=f"Executar Todas as {count} Ações")
        exec_all.add_css_class("suggested-action")
        exec_all.add_css_class("pill")
        exec_all.set_halign(Gtk.Align.START)
        exec_all.set_margin_top(4)

        def on_exec_all(_):
            exec_all.set_sensitive(False)
            exec_all.set_label("Executando...")
            reports = ctx.executor.execute_plan(plan, dry_run=False)
            for r in reports:
                ctx.engine.memory.log_action(
                    prompt=turn.prompt,
                    action_type=r.action.action_type.value,
                    target=r.action.target,
                    params=r.action.params,
                    success=r.success,
                    message=r.message,
                )
            exec_all.set_label("Todas Executadas ✓")
            ctx.show_toast(f"✓ {len(reports)} ações executadas com sucesso!")

        exec_all.connect("clicked", on_exec_all)
        return exec_all

    # ------------------------------------------------------------------
    # Linhas de ação proposta
    # ------------------------------------------------------------------
    def create_action_row(self, action: DesktopAction) -> Gtk.Widget:
        """Renderiza uma linha de ação proposta com ícone semântico e botão de execução."""
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

        icon_name = (
            "camera-photo-symbolic"
            if action.action_type == ActionType.CAPTURE_SCREEN
            else get_action_icon(action)
        )
        prefix_icon = Gtk.Image.new_from_icon_name(icon_name)
        prefix_icon.set_pixel_size(20)
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
            exec_btn.connect("clicked", self._make_exec_handler(action, exec_btn))

        row.add_suffix(exec_btn)
        return row

    def _build_action_row_content(self, action: DesktopAction, badge_desc: str):
        """Monta título/subtítulo específicos por tipo de ação e o rótulo do botão."""
        if action.action_type == ActionType.FIX_COMMAND:
            cmd_show = action.params.get("command") or action.target
            row = Adw.ActionRow(
                title=f"<b>⚡ Auto-Cura: {html.escape(action.target)}</b>",
                subtitle=f"Comando: <tt><b>{html.escape(cmd_show)}</b></tt>",
            )
            row.set_use_markup(True)
            return row, "Executar Correção"

        if action.action_type == ActionType.SMART_OCR:
            preview_txt = (action.target[:42] + "...") if len(action.target) > 42 else action.target
            row = Adw.ActionRow(
                title=f"<b>\U0001f4cb Smart OCR: {html.escape(action.describe())}</b>",
                subtitle=f"Texto: {html.escape(preview_txt)}",
            )
            row.set_use_markup(True)
            return row, "Copiar Conteúdo"

        if action.action_type == ActionType.WRITE_FILE:
            dest_dir = action.params.get("directory") or "~/Documentos/Relatorios"
            row = Adw.ActionRow(
                title=f"<b>\U0001f4dd Salvar: {html.escape(action.describe())}</b>",
                subtitle=f"Destino: <tt>{html.escape(dest_dir)}</tt>",
            )
            row.set_use_markup(True)
            return row, "Salvar Arquivo"

        if action.action_type == ActionType.ORGANIZE_FILES:
            target_dir = action.params.get("directory") or "~/Downloads"
            row = Adw.ActionRow(
                title=f"<b>\U0001f4c1 Organizar: {html.escape(action.describe())}</b>",
                subtitle=f"Pasta: <tt>{html.escape(target_dir)}</tt> (Lixeira reversível)",
            )
            row.set_use_markup(True)
            return row, "Organizar Agora"

        if action.action_type == ActionType.MEDIA_CONTROL:
            row = Adw.ActionRow(
                title=f"<b>\U0001f3b5 Mídia: {html.escape(action.describe())}</b>",
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

    def _make_exec_handler(self, action: DesktopAction, btn: Gtk.Button):
        """Cria o handler de execução individual de uma ação proposta."""
        ctx = self.ctx

        def handler(_):
            btn.set_sensitive(False)
            btn.set_label("Executando...")
            single_plan = ActionPlan(
                thought=ctx.current_plan.thought if ctx.current_plan else "",
                actions=[action],
            )
            reports = ctx.executor.execute_plan(single_plan, dry_run=False)
            rep = reports[0] if reports else None
            prompt_text = ctx.entry.get_text().strip()

            if rep and rep.success:
                btn.set_label("Executado ✓")
                btn.remove_css_class("suggested-action")
                btn.add_css_class("flat")
                ctx.show_toast(f"✓ {rep.message}")
            else:
                err = rep.message if rep else "Erro"
                btn.set_label("Falha ✗")
                ctx.show_toast(f"✗ {err}")

            if rep:
                ctx.engine.memory.log_action(
                    prompt=prompt_text,
                    action_type=action.action_type.value,
                    target=action.target,
                    params=action.params,
                    success=rep.success,
                    message=rep.message,
                )

        return handler
