# Decisão de design: o diálogo de comparação é um Adw.Dialog com dois painéis lado a
# lado (estilo split view), cada um renderizando prompt + resposta (markdown) + ações
# de um turno. Dois DropDowns permitem escolher quaisquer duas respostas da conversa
# para colocar frente a frente — útil para depurar qual variante do modelo foi melhor.

"""Diálogo de comparação side-by-side de duas respostas da conversa."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango  # noqa: E402

from ..markdown import format_markdown_to_markup  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - apenas para type checking
    from ..app import CopilotWindow
    from ...core.session import ChatTurn


def _preview_text(turn: "ChatTurn") -> str:
    p = (turn.prompt or "(sem pergunta)").strip().replace("\n", " ")
    return p[:48] + ("…" if len(p) > 48 else "")


class CompareResponsesDialog(Adw.Dialog):
    """Compara duas respostas da conversa lado a lado (item #5 do backlog)."""

    def __init__(self, ctx: "CopilotWindow", anchor_turn: "ChatTurn | None" = None):
        super().__init__()
        self.ctx = ctx
        self.set_title("Comparar Respostas")
        self.set_content_width(1000)
        self.set_content_height(700)

        # Só faz sentido comparar turnos que de fato produziram uma resposta.
        self.candidates: list["ChatTurn"] = [
            t for t in ctx.session.turns if t.answer and t.answer.strip()
        ]
        self.candidates.sort(key=lambda t: t.timestamp)

        if len(self.candidates) < 2:
            self._build_empty_state()
            return

        self.toolbar = Adw.ToolbarView()

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_margin_top(8)
        header.set_margin_bottom(8)
        header.set_margin_start(14)
        header.set_margin_end(14)
        header.append(Gtk.Label(label="Comparar:"))
        self.drop_a = self._make_dropdown()
        self.drop_b = self._make_dropdown()
        header.append(self.drop_a)
        x_lbl = Gtk.Label(label="×")
        x_lbl.add_css_class("dim-label")
        header.append(x_lbl)
        header.append(self.drop_b)
        self.toolbar.add_top_bar(header)

        split = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.pane_a = self._make_pane("A")
        self.pane_b = self._make_pane("B")
        split.append(self.pane_a)
        split.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        split.append(self.pane_b)
        self.toolbar.set_content(split)
        self.set_child(self.toolbar)

        # Pré-seleção: o turno âncora (onde o usuário clicou) no painel A, e o
        # turno anterior disponível no painel B — a comparação mais provável.
        if anchor_turn in self.candidates:
            idx_a = self.candidates.index(anchor_turn)
            idx_b = (idx_a - 1) % len(self.candidates)
            if idx_b == idx_a:
                idx_b = (idx_a + 1) % len(self.candidates)
        else:
            idx_a = max(0, len(self.candidates) - 2)
            idx_b = len(self.candidates) - 1
        self.drop_a.set_selected(idx_a)
        self.drop_b.set_selected(idx_b)
        self._render(idx_a, idx_b)

    # ------------------------------------------------------------------
    # Construção
    # ------------------------------------------------------------------
    def _build_empty_state(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_top(40)
        box.set_margin_bottom(40)
        icon = Gtk.Image.new_from_icon_name("view-grid-symbolic")
        icon.set_pixel_size(48)
        icon.add_css_class("dim-label")
        box.append(icon)
        lbl = Gtk.Label(label="É preciso de ao menos duas respostas na conversa para comparar.")
        lbl.add_css_class("title-4")
        lbl.add_css_class("dim-label")
        box.append(lbl)
        self.set_child(box)

    def _make_dropdown(self) -> Gtk.DropDown:
        model = Gtk.StringList(strings=[_preview_text(t) for t in self.candidates])
        dd = Gtk.DropDown(model=model)
        dd.set_hexpand(True)
        dd.connect("notify::selected", self._on_select)
        return dd

    def _make_pane(self, tag: str) -> Gtk.ScrolledWindow:
        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp(maximum_size=520)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(14)
        box.set_margin_bottom(14)
        box.set_margin_start(16)
        box.set_margin_end(16)
        clamp.set_child(box)
        sw.set_child(clamp)
        # Etiqueta do painel (A/B) para dar contexto à comparação.
        tag_lbl = Gtk.Label(label=f"<b>Resposta {tag}</b>", use_markup=True, xalign=0)
        tag_lbl.add_css_class("heading")
        tag_lbl.set_margin_bottom(4)
        box.append(tag_lbl)
        sw._content_box = box
        return sw

    # ------------------------------------------------------------------
    # Atualização
    # ------------------------------------------------------------------
    def _on_select(self, *_args) -> None:
        self._render(self.drop_a.get_selected(), self.drop_b.get_selected())

    def _render(self, idx_a: int, idx_b: int) -> None:
        if 0 <= idx_a < len(self.candidates):
            self._fill_pane(self.pane_a, self.candidates[idx_a])
        if 0 <= idx_b < len(self.candidates):
            self._fill_pane(self.pane_b, self.candidates[idx_b])

    def _fill_pane(self, pane: Gtk.ScrolledWindow, turn: "ChatTurn") -> None:
        box: Gtk.Box = pane._content_box
        while child := box.get_first_child():
            box.remove(child)

        prompt = Gtk.Label(label=turn.prompt or "(sem pergunta)", xalign=0)
        prompt.add_css_class("body")
        prompt.set_wrap(True)
        prompt.set_selectable(True)
        box.append(prompt)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(6)
        sep.set_margin_bottom(8)
        box.append(sep)

        markup = format_markdown_to_markup(turn.answer)
        ans = Gtk.Label(xalign=0, yalign=0)
        ans.set_wrap(True)
        ans.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        ans.set_selectable(True)
        ans.set_use_markup(True)
        ans.set_markup(markup)
        box.append(ans)

        if turn.plan and turn.plan.actions:
            acts = Gtk.Label(
                label="Ações: " + ", ".join(a.describe() for a in turn.plan.actions),
                xalign=0,
            )
            acts.add_css_class("caption")
            acts.add_css_class("dim-label")
            acts.set_wrap(True)
            box.append(acts)
