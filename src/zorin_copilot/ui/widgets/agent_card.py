# Decisão de design: widget do Modo Agente renderizado como um stepper visual em tempo real,
# com atualização incremental de passos, diálogo inline para aprovação de ações de risco
# (human-in-the-loop) e botão de parada de emergência (abort/kill-switch).

"""Widget visual do Modo Agente para a interface gráfica GTK4 do Zorin Copilot."""

from __future__ import annotations

import html
import json
import logging
import threading
from typing import TYPE_CHECKING, Any, Callable

from ..gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from ...ai.agent import (
    STOP_ABORTED,
    STOP_DONE,
    STOP_ERROR,
    STOP_MAX_STEPS,
    STOP_NO_PROVIDER,
    STOP_REJECTED,
    STOP_REPEATED,
    STOP_TIMEOUT,
    AgentResult,
    Step,
)
from ..markdown import format_markdown_to_markup

if TYPE_CHECKING:  # pragma: no cover
    from ..app import CopilotWindow
    from ...core.session import ChatTurn

logger = logging.getLogger(__name__)

TOOL_LABELS: dict[str, str] = {
    "launch_app": "Abrir aplicativo",
    "click_element": "Clicar em elemento",
    "find_element": "Localizar elemento na tela",
    "mouse_click": "Clique de mouse",
    "type_text": "Digitar texto",
    "keyboard_type": "Digitar texto",
    "press_hotkey": "Pressionar atalho",
    "keyboard_hotkey": "Pressionar atalho",
    "write_document": "Gravar arquivo",
    "organize_directory": "Organizar pasta",
    "capture_screen": "Capturar tela",
    "read_file": "Ler arquivo",
    "list_directory": "Listar arquivos",
    "get_ui_tree": "Inspecionar acessibilidade (AT-SPI)",
    "screen_fence_control": "Controle de cerca de tela",
    "undo_last": "Desfazer última ação",
    "capture_lesson": "Capturar conteúdo da aula",
    "web_search": "Pesquisar na web",
    "academic_search": "Pesquisa acadêmica",
    "open_document": "Abrir documento",
    "open_url": "Abrir link web",
    "done": "Concluir",
    "finish": "Concluir",
}

TOOL_ICONS: dict[str, str] = {
    "launch_app": "application-x-executable-symbolic",
    "click_element": "input-mouse-symbolic",
    "find_element": "system-search-symbolic",
    "mouse_click": "input-mouse-symbolic",
    "type_text": "input-keyboard-symbolic",
    "keyboard_type": "input-keyboard-symbolic",
    "press_hotkey": "preferences-desktop-keyboard-shortcuts-symbolic",
    "keyboard_hotkey": "preferences-desktop-keyboard-shortcuts-symbolic",
    "write_document": "document-save-symbolic",
    "organize_directory": "folder-symbolic",
    "capture_screen": "camera-photo-symbolic",
    "read_file": "document-open-symbolic",
    "list_directory": "system-file-manager-symbolic",
    "get_ui_tree": "preferences-desktop-accessibility-symbolic",
    "screen_fence_control": "security-high-symbolic",
    "undo_last": "edit-undo-symbolic",
    "capture_lesson": "accessories-dictionary-symbolic",
    "web_search": "web-browser-symbolic",
    "academic_search": "system-search-symbolic",
    "open_document": "document-open-symbolic",
    "open_url": "web-browser-symbolic",
    "done": "emblem-ok-symbolic",
    "finish": "emblem-ok-symbolic",
}


def _format_args_summary(tool: str, args: dict[str, Any]) -> str:
    """Extrai um resumo legível e enxuto dos argumentos passados para a ferramenta."""
    if not args:
        return ""
    if tool == "launch_app":
        return str(args.get("query") or args.get("app") or "")
    if tool in ("type_text", "keyboard_type"):
        text = str(args.get("text", ""))
        return f'"{text[:35]}…"' if len(text) > 35 else f'"{text}"'
    if tool == "click_element":
        return str(args.get("query") or args.get("uid") or "")
    if tool in ("write_document", "write_file"):
        filename = str(args.get("filename") or args.get("path") or "")
        return filename
    if tool == "organize_directory":
        return str(args.get("directory") or "")
    if tool in ("press_hotkey", "keyboard_hotkey"):
        return str(args.get("combo") or args.get("keys") or "")
    if tool in ("web_search", "academic_search"):
        q = str(args.get("query") or "")
        return f'"{q[:40]}…"' if len(q) > 40 else f'"{q}"'
    if tool == "open_url":
        return str(args.get("url") or "")
    if tool in ("read_file", "list_directory"):
        return str(args.get("path") or args.get("directory") or "")

    # Fallback genérico: primeiro argumento não nulo
    for k, v in args.items():
        if v:
            return f"{k}={v}"
    return ""


def _format_observation(obs: Any, max_chars: int = 1500) -> str:
    """Formata a observação retornada pela ferramenta para visualização detalhada e limpa."""
    if obs is None:
        return ""
    if isinstance(obs, (dict, list)):
        try:
            text = json.dumps(obs, indent=2, ensure_ascii=False)
        except Exception:
            text = str(obs)
    else:
        text = str(obs)

    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n… [truncado, {len(text)} caracteres no total]"
    return text


class AgentExecutionWidget(Gtk.Box):
    """Card dinâmico que exibe o progresso passo a passo do Modo Agente."""

    def __init__(
        self,
        objective: str,
        ctx: "CopilotWindow",
        *,
        on_abort: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.objective = objective
        self.ctx = ctx
        self.on_abort = on_abort
        self.steps: list[Step] = []
        self.auto_approve_rest: bool = False
        self._active_approval_event: threading.Event | None = None
        self._active_approval_result: dict[str, bool] | None = None
        self._active_approval_tool: str = ""

        self.add_css_class("card")
        self.add_css_class("assistant-message-card")
        self.add_css_class("agent-stepper-card")

        self._build_header()
        self._build_stepper()
        self._build_approval_box()
        self._build_conclusion_box()

    def _build_header(self) -> None:
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        # Ícone do Agente
        icon = Gtk.Image.new_from_icon_name("system-run-symbolic")
        icon.set_pixel_size(18)
        header_box.append(icon)

        # Badge "Modo Agente"
        badge = Gtk.Label(label="<b>Modo Agente</b>", use_markup=True, xalign=0)
        badge.add_css_class("heading")
        header_box.append(badge)

        # Rótulo de status em tempo real
        self.status_lbl = Gtk.Label(label="● Inicializando planejamento…", xalign=0)
        self.status_lbl.add_css_class("caption")
        self.status_lbl.add_css_class("dim-label")
        self.status_lbl.set_hexpand(True)
        header_box.append(self.status_lbl)

        # Spinner de carregamento
        self.spinner = Gtk.Spinner()
        self.spinner.start()
        header_box.append(self.spinner)

        # Botão de Parada de Emergência
        self.abort_btn = Gtk.Button()
        self.abort_btn.add_css_class("destructive-action")
        self.abort_btn.add_css_class("pill")
        self.abort_btn.add_css_class("glass-pill")
        self.abort_btn.set_tooltip_text("Interromper execução do agente imediatamente")

        abort_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        abort_ic = Gtk.Image.new_from_icon_name("process-stop-symbolic")
        abort_ic.set_pixel_size(14)
        abort_content.append(abort_ic)
        abort_lbl = Gtk.Label(label="Interromper")
        abort_lbl.add_css_class("caption")
        abort_content.append(abort_lbl)
        self.abort_btn.set_child(abort_content)

        def _handle_abort() -> None:
            if self._active_approval_event and not self._active_approval_event.is_set():
                if self._active_approval_result is not None:
                    self._active_approval_result["approved"] = False
                self._active_approval_event.set()
            if self.on_abort:
                self.on_abort()

        self.abort_btn.connect("clicked", lambda _: _handle_abort())

        header_box.append(self.abort_btn)
        self.append(header_box)

        # Subtítulo com o objetivo
        obj_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        obj_lbl = Gtk.Label(label=f"<b>Objetivo:</b> {html.escape(self.objective)}", use_markup=True, xalign=0)
        obj_lbl.set_wrap(True)
        obj_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        obj_lbl.set_selectable(True)
        obj_lbl.add_css_class("caption")
        obj_row.append(obj_lbl)
        self.append(obj_row)

    def _build_stepper(self) -> None:
        self.stepper_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.stepper_container.set_margin_top(4)
        self.stepper_container.set_margin_bottom(4)

        # Cabeçalho da lista de passos
        self.steps_header = Gtk.Label(label="<b>Etapas de Execução:</b>", use_markup=True, xalign=0)
        self.steps_header.add_css_class("caption")
        self.steps_header.add_css_class("dim-label")
        self.stepper_container.append(self.steps_header)

        self.steps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.stepper_container.append(self.steps_box)

        self.append(self.stepper_container)

    def _build_approval_box(self) -> None:
        """Banner inline exibido quando uma ação sensível requer aprovação do usuário."""
        self.approval_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.approval_box.add_css_class("card")
        self.approval_box.add_css_class("agent-approval-box")
        self.approval_box.set_visible(False)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        warn_ic = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        warn_ic.set_pixel_size(18)
        top_row.append(warn_ic)

        title = Gtk.Label(label="<b>Confirmação Necessária</b>", use_markup=True, xalign=0)
        title.add_css_class("heading")
        top_row.append(title)
        self.approval_box.append(top_row)

        self.approval_msg_lbl = Gtk.Label(xalign=0)
        self.approval_msg_lbl.set_wrap(True)
        self.approval_msg_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.approval_msg_lbl.set_use_markup(True)
        self.approval_box.append(self.approval_msg_lbl)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)

        self.reject_btn = Gtk.Button(label="Recusar e Cancelar")
        self.reject_btn.add_css_class("destructive-action")
        self.reject_btn.add_css_class("pill")
        self.reject_btn.connect("clicked", self._on_reject_clicked)
        btn_row.append(self.reject_btn)

        self.approve_all_btn = Gtk.Button(label="Aprovar Todas")
        self.approve_all_btn.add_css_class("pill")
        self.approve_all_btn.add_css_class("glass-pill")
        self.approve_all_btn.set_tooltip_text("Aprova esta e todas as ações sensíveis subsequentes desta tarefa")
        self.approve_all_btn.connect("clicked", self._on_approve_all_clicked)
        btn_row.append(self.approve_all_btn)

        self.approve_btn = Gtk.Button(label="Aprovar Esta")
        self.approve_btn.add_css_class("suggested-action")
        self.approve_btn.add_css_class("pill")
        self.approve_btn.connect("clicked", self._on_approve_clicked)
        btn_row.append(self.approve_btn)

        self.approval_box.append(btn_row)
        self.append(self.approval_box)

    def _build_conclusion_box(self) -> None:
        self.conclusion_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.conclusion_box.set_visible(False)

        # Separador visual sutil
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(4)
        sep.set_margin_bottom(4)
        self.conclusion_box.append(sep)

        # Resposta final do agente
        self.answer_lbl = Gtk.Label(xalign=0)
        self.answer_lbl.set_wrap(True)
        self.answer_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.answer_lbl.set_selectable(True)
        self.answer_lbl.set_use_markup(True)
        self.conclusion_box.append(self.answer_lbl)

        # Rodapé com sumário de telemetria
        self.summary_lbl = Gtk.Label(xalign=0)
        self.summary_lbl.add_css_class("caption")
        self.summary_lbl.add_css_class("dim-label")
        self.conclusion_box.append(self.summary_lbl)

        # Ações pós-execução (ex: Desfazer alterações no sistema de arquivos)
        self.conclusion_actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.conclusion_actions_box.set_margin_top(4)

        self.undo_btn = Gtk.Button()
        self.undo_btn.add_css_class("pill")
        self.undo_btn.add_css_class("glass-pill")
        undo_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        undo_ic = Gtk.Image.new_from_icon_name("edit-undo-symbolic")
        undo_ic.set_pixel_size(14)
        undo_content.append(undo_ic)
        self.undo_btn_lbl = Gtk.Label(label="Desfazer alterações (Ctrl+Z)")
        self.undo_btn_lbl.add_css_class("caption")
        undo_content.append(self.undo_btn_lbl)
        self.undo_btn.set_child(undo_content)
        self.undo_btn.set_tooltip_text("Reverte as alterações de arquivo feitas por esta execução")
        self.undo_btn.connect("clicked", self._on_undo_clicked)
        self.undo_btn.set_visible(False)
        self.conclusion_actions_box.append(self.undo_btn)

        self.conclusion_box.append(self.conclusion_actions_box)
        self.append(self.conclusion_box)

    # ------------------------------------------------------------------
    # Handlers de Aprovação e Ações
    # ------------------------------------------------------------------

    def _on_approve_clicked(self, _btn: Gtk.Button) -> None:
        self.approval_box.set_visible(False)
        tool = self._active_approval_tool or "Ação"
        self.status_lbl.set_text(f"● Ação '{tool}' aprovada pelo usuário. Executando…")
        if self._active_approval_result is not None:
            self._active_approval_result["approved"] = True
        if self._active_approval_event is not None:
            self._active_approval_event.set()

    def _on_approve_all_clicked(self, _btn: Gtk.Button) -> None:
        self.auto_approve_rest = True
        self.approval_box.set_visible(False)
        tool = self._active_approval_tool or "Ação"
        self.status_lbl.set_text(f"● Ação '{tool}' aprovada (e autorizadas ações subsequentes). Executando…")
        if self._active_approval_result is not None:
            self._active_approval_result["approved"] = True
        if self._active_approval_event is not None:
            self._active_approval_event.set()

    def _on_reject_clicked(self, _btn: Gtk.Button) -> None:
        self.approval_box.set_visible(False)
        tool = self._active_approval_tool or "Ação"
        self.status_lbl.set_text(f"Ação '{tool}' recusada pelo usuário.")
        if self._active_approval_result is not None:
            self._active_approval_result["approved"] = False
        if self._active_approval_event is not None:
            self._active_approval_event.set()

    def _on_undo_clicked(self, _btn: Gtk.Button) -> None:
        if hasattr(self.ctx, "undo_last_action"):
            self.ctx.undo_last_action()
        self.undo_btn.set_sensitive(False)
        self.undo_btn_lbl.set_text("Alteração desfeita")

    def _update_undo_button_visibility(self, steps: list[Step]) -> None:
        """Exibe o botão de Desfazer se alguma ação mutante de arquivo foi concluída com sucesso."""
        mutating_tools = {"write_document", "write_file", "organize_directory"}
        has_undoable = any(s.tool in mutating_tools and s.ok for s in steps)
        self.undo_btn.set_visible(has_undoable)

    # ------------------------------------------------------------------
    # Atualizações em Tempo Real
    # ------------------------------------------------------------------

    def add_step(self, step: Step) -> None:
        """Adiciona uma linha de etapa concluída à lista visual com detalhes expansíveis."""
        self.steps.append(step)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("agent-step-row")

        # Ícone de sucesso / erro / alerta
        if step.ok:
            ic_name = "emblem-ok-symbolic"
            css_cls = "success"
        elif step.ok is False:
            ic_name = "window-close-symbolic"
            css_cls = "error"
        else:
            ic_name = "dialog-warning-symbolic"
            css_cls = "warning"

        ic = Gtk.Image.new_from_icon_name(ic_name)
        ic.set_pixel_size(14)
        ic.add_css_class(css_cls)
        row.append(ic)

        # Número da etapa
        idx_lbl = Gtk.Label(label=f"<b>{step.index + 1}.</b>", use_markup=True, xalign=0)
        idx_lbl.add_css_class("caption")
        row.append(idx_lbl)

        # Ícone e nome amigável da ferramenta
        tool_friendly = TOOL_LABELS.get(step.tool, step.tool)
        t_icon_name = TOOL_ICONS.get(step.tool, "system-run-symbolic")
        t_icon = Gtk.Image.new_from_icon_name(t_icon_name)
        t_icon.set_pixel_size(14)
        row.append(t_icon)

        # Resumo dos argumentos
        summary = _format_args_summary(step.tool, step.args)
        if summary:
            step_text = f"<b>{html.escape(tool_friendly)}</b> <span alpha='70%'>({html.escape(summary)})</span>"
        else:
            step_text = f"<b>{html.escape(tool_friendly)}</b>"

        lbl = Gtk.Label(label=step_text, use_markup=True, xalign=0)
        lbl.set_wrap(True)
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_hexpand(True)
        row.append(lbl)

        # Tempo decorrido
        if step.elapsed:
            time_lbl = Gtk.Label(label=f"<span size='small' alpha='60%'>{step.elapsed:.1f}s</span>", use_markup=True)
            row.append(time_lbl)

        # Observação detalhada e formatação de inspeção
        obs_text = _format_observation(step.observation)
        has_detail = bool(step.error or step.rationale or obs_text)

        if has_detail:
            revealer = Gtk.Revealer()
            revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
            revealer.set_transition_duration(150)

            expand_btn = Gtk.Button()
            expand_btn.add_css_class("flat")
            expand_btn.add_css_class("circular")
            expand_ic = Gtk.Image.new_from_icon_name("pan-down-symbolic")
            expand_ic.set_pixel_size(12)
            expand_btn.set_child(expand_ic)
            expand_btn.set_tooltip_text("Ver detalhes desta etapa")

            def toggle_detail(_b: Gtk.Button) -> None:
                rev = not revealer.get_reveal_child()
                revealer.set_reveal_child(rev)
                expand_ic.set_from_icon_name("pan-up-symbolic" if rev else "pan-down-symbolic")

            expand_btn.connect("clicked", toggle_detail)
            row.append(expand_btn)

            # Conteúdo detalhado dentro do revealer
            detail_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            detail_card.add_css_class("card")
            detail_card.add_css_class("agent-step-detail")
            detail_card.set_margin_start(28)
            detail_card.set_margin_end(6)
            detail_card.set_margin_top(2)
            detail_card.set_margin_bottom(4)

            if step.rationale:
                rat_lbl = Gtk.Label(
                    label=f"<b>Planejamento:</b> {html.escape(step.rationale)}",
                    use_markup=True,
                    xalign=0,
                )
                rat_lbl.set_wrap(True)
                rat_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                rat_lbl.add_css_class("caption")
                detail_card.append(rat_lbl)

            if step.error:
                err_lbl = Gtk.Label(
                    label=f"<b>Erro:</b> <span foreground='#e01b24'>{html.escape(step.error)}</span>",
                    use_markup=True,
                    xalign=0,
                )
                err_lbl.set_wrap(True)
                err_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                err_lbl.add_css_class("caption")
                detail_card.append(err_lbl)

            if obs_text:
                obs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                obs_title = Gtk.Label(label="<b>Observação:</b>", use_markup=True, xalign=0)
                obs_title.add_css_class("caption")
                obs_title.add_css_class("dim-label")
                obs_box.append(obs_title)

                obs_lbl = Gtk.Label(label=obs_text, xalign=0)
                obs_lbl.set_selectable(True)
                obs_lbl.set_wrap(True)
                obs_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                obs_lbl.add_css_class("monospace")
                obs_lbl.add_css_class("caption")
                obs_box.append(obs_lbl)
                detail_card.append(obs_box)

            revealer.set_child(detail_card)

            # Se a etapa falhou, revela imediatamente por conveniência
            if not step.ok and (step.error or obs_text):
                revealer.set_reveal_child(True)
                expand_ic.set_from_icon_name("pan-up-symbolic")
            else:
                revealer.set_reveal_child(False)

            step_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            step_container.append(row)
            step_container.append(revealer)
            self.steps_box.append(step_container)
        else:
            self.steps_box.append(row)

        self.status_lbl.set_text(f"● Passo {step.index + 1}: {tool_friendly} concluído")
        if hasattr(self.ctx, "chat_stream") and hasattr(self.ctx.chat_stream, "scroll_to_bottom"):
            self.ctx.chat_stream.scroll_to_bottom()

    def prompt_approval(
        self,
        step: Step,
        event: threading.Event,
        result_dict: dict[str, bool],
    ) -> None:
        """Exibe o diálogo inline solicitando autorização do usuário para ação de risco."""
        tool_friendly = TOOL_LABELS.get(step.tool, step.tool)
        if self.auto_approve_rest:
            self.status_lbl.set_text(f"● Ação '{tool_friendly}' auto-aprovada (permissão contínua).")
            result_dict["approved"] = True
            event.set()
            return

        self._active_approval_event = event
        self._active_approval_result = result_dict
        self._active_approval_tool = tool_friendly

        summary = _format_args_summary(step.tool, step.args)
        args_desc = f" ({html.escape(summary)})" if summary else ""

        msg = (
            f"O agente solicita permissão para executar a ação sensível <b>{html.escape(tool_friendly)}</b>{args_desc}.\n"
            f"<span size='small' alpha='75%'>Classificação de risco: <b>{html.escape(step.risk)}</b>.</span>"
        )
        self.approval_msg_lbl.set_markup(msg)
        self.status_lbl.set_text("Aguardando aprovação do usuário…")
        self.approval_box.set_visible(True)

        if hasattr(self.ctx, "chat_stream") and hasattr(self.ctx.chat_stream, "scroll_to_bottom"):
            self.ctx.chat_stream.scroll_to_bottom()

    def finish(self, result: AgentResult) -> None:
        """Finaliza o widget de execução exibindo a resposta e o sumário."""
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.abort_btn.set_visible(False)
        self.approval_box.set_visible(False)

        # Atualiza status
        if result.success:
            self.status_lbl.set_text("Objetivo concluído com sucesso")
            self.status_lbl.remove_css_class("dim-label")
            self.status_lbl.add_css_class("success")
        else:
            self.status_lbl.set_text(f"● Parada: {result.stop_message}")

        # Renderiza resposta final
        answer = result.final_answer or result.stop_message
        if answer:
            self.answer_lbl.set_markup(format_markdown_to_markup(answer))

        summary = (
            f"● {result.stop_message} • {len(result.steps)} passos em {result.elapsed:.1f}s"
        )
        if result.provider:
            summary += f" • Modelo: {result.provider}"
        self.summary_lbl.set_text(summary)

        # Botão de Desfazer se houver alterações de arquivos
        self._update_undo_button_visibility(result.steps)

        self.conclusion_box.set_visible(True)
        if hasattr(self.ctx, "chat_stream") and hasattr(self.ctx.chat_stream, "scroll_to_bottom"):
            self.ctx.chat_stream.scroll_to_bottom()

    # ------------------------------------------------------------------
    # Reconstrução a partir do Histórico
    # ------------------------------------------------------------------

    @classmethod
    def from_result_dict(
        cls,
        data: dict[str, Any],
        ctx: "CopilotWindow",
        turn: "ChatTurn",
    ) -> "AgentExecutionWidget":
        """Reconstrói um widget completo de execução de agente a partir de um turno gravado."""
        objective = data.get("objective", turn.prompt)
        widget = cls(objective=objective, ctx=ctx)
        widget.spinner.stop()
        widget.spinner.set_visible(False)
        widget.abort_btn.set_visible(False)
        widget.approval_box.set_visible(False)

        # Reconstrói os passos
        steps_raw = data.get("steps", [])
        for s_data in steps_raw:
            if isinstance(s_data, dict):
                step = Step(
                    index=s_data.get("index", 0),
                    tool=s_data.get("tool", ""),
                    args=s_data.get("args", {}),
                    rationale=s_data.get("rationale", ""),
                    risk=s_data.get("risk", "safe"),
                    requires_approval=s_data.get("requires_approval", False),
                    approved=s_data.get("approved"),
                    ok=s_data.get("ok"),
                    observation=s_data.get("observation"),
                    error=s_data.get("error", ""),
                    elapsed=s_data.get("elapsed", 0.0),
                )
                widget.add_step(step)

        # Reconstrói a conclusão
        success = bool(data.get("success", False))
        stop_message = data.get("stop_message") or data.get("stop_reason", "")
        if success:
            widget.status_lbl.set_text("Objetivo concluído com sucesso")
            widget.status_lbl.remove_css_class("dim-label")
            widget.status_lbl.add_css_class("success")
        else:
            widget.status_lbl.set_text(f"● Parada: {stop_message}")

        final_answer = data.get("final_answer", turn.answer)
        if final_answer:
            widget.answer_lbl.set_markup(format_markdown_to_markup(final_answer))

        elapsed = data.get("elapsed", 0.0)
        provider = data.get("provider", "")
        summary = f"● {stop_message} • {len(steps_raw)} passos em {elapsed:.1f}s"
        if provider:
            summary += f" • Modelo: {provider}"
        widget.summary_lbl.set_text(summary)

        # Botão de Desfazer se houver alterações de arquivos
        widget._update_undo_button_visibility(widget.steps)

        widget.conclusion_box.set_visible(True)
        return widget

