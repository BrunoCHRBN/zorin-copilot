# Decisão de design: o Quadro Negro é a superfície visual do SolutionGraph. Em vez de
# HTML/WebView (o app recusa WebView no chat — ver UI_ANALYSIS.md), ele desenha o grafo
# node-link com GTK4 DrawingArea + Cairo, dentro do paradigma nativo do Zorin Copilot.
# O widget consome o mesmo dataclass que o chat e a voz usam, então o raciocínio
# estruturado aparece idêntico nas três modalidades. Metro math usa core/math_render
# (SVG via matplotlib, com fallback de texto).

"""Quadro Negro Digital — widget GTK4 que desenha o SolutionGraph (modo de estudos)."""

from __future__ import annotations

import os
import tempfile
from typing import TYPE_CHECKING, Any, Optional

from ..gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from ...ai.math_solver import SolutionGraph  # noqa: E402
from ...core.board_nav import find_node  # noqa: E402
from ...core.math_render import has_math_renderer, render_math_to_svg  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - apenas para type checking
    from ..app import CopilotWindow


def _hex(c: str) -> tuple[float, float, float]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


# paleta (espelha o protótipo HTML / o sistema de cores do app)
_ROOT_FILL, _ROOT_STROKE = "#EEEDFE", "#534AB7"
_METH_FILL, _METH_STROKE = "#E1F5EE", "#0F6E56"
_VIEW_FILL, _VIEW_STROKE = "#FAEEDA", "#BA7517"
_STEP_FILL, _STEP_STROKE = "#FFFFFF", "#C7CCD4"
_INK = _hex("#1D1F23")
_MUTED = _hex("#6B7280")


class BlackboardWidget(Gtk.Box):
    """Desenha enunciado -> métodos -> passos + ponto de vista, com pan/zoom,
    seleção de nó e modo rigor chinês (已知/求/解/答 + 步骤分)."""

    __gtype_name__ = "BlackboardWidget"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self.graph: Optional[SolutionGraph] = None
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.tx, self.ty, self.scale = 0.0, 0.0, 1.0
        self.selected: Optional[dict] = None
        self.rigor = False
        self._drag_start = (0.0, 0.0)
        self._play_src: Optional[int] = None
        # Janela que hospeda este quadro (preenchida por `show_dialog`). A
        # permite reusar a mesma janela em comandos de voz sucessivos, em vez de
        # abrir uma nova a cada "abre o quadro".
        self._host_window: Optional[Gtk.Window] = None

        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.toolbar.set_margin_top(8)
        self.toolbar.set_margin_bottom(8)
        self.toolbar.set_margin_start(8)
        self.toolbar.set_margin_end(8)

        fit = Gtk.Button(label="Ajustar")
        fit.connect("clicked", lambda *_: self._fit())
        self.toolbar.append(fit)

        self.zoom_lbl = Gtk.Label(label="100%")
        self.toolbar.append(self.zoom_lbl)

        play = Gtk.Button(label="▶ Reproduzir")
        play.connect("clicked", lambda *_: self._play())
        self.toolbar.append(play)

        self.tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.toolbar.append(self.tabs)

        self.rigor_switch = Gtk.Switch()
        self.rigor_switch.connect("notify::active", self._on_rigor)
        rigor_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        rigor_box.append(Gtk.Label(label="Rigor chinês"))
        rigor_box.append(self.rigor_switch)
        self.toolbar.append(rigor_box)
        self.append(self.toolbar)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        body.set_hexpand(True)
        body.set_vexpand(True)

        self.drawing = Gtk.DrawingArea()
        self.drawing.set_hexpand(True)
        self.drawing.set_vexpand(True)
        self.drawing.set_draw_func(self._draw, None)
        body.append(self.drawing)

        self.side = Gtk.ScrolledWindow()
        self.side.set_size_request(320, -1)
        self.side.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.panel.set_margin_top(10)
        self.panel.set_margin_bottom(10)
        self.panel.set_margin_start(10)
        self.panel.set_margin_end(10)
        self.side.set_child(self.panel)
        body.append(self.side)

        self.append(body)

    def _connect_signals(self) -> None:
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        self.drawing.add_controller(drag)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._on_scroll)
        self.drawing.add_controller(scroll)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        self.drawing.add_controller(click)

    # ------------------------------------------------------------ carga
    def load_solution(self, graph: SolutionGraph) -> None:
        self.graph = graph
        self._layout()
        self._rebuild_tabs()
        self._select(self.root_node)
        self._fit()
        self.drawing.queue_draw()

    # ---------------------------------------------------------- layout
    def _layout(self) -> None:
        g = self.graph
        W = 960.0
        self.root_node = {"id": "root", "type": "root", "label": "Enunciado",
                          "sub": g.problem_statement, "x": W / 2, "y": 60,
                          "w": 300, "h": 60, "ref": g}
        branches = []
        blist = g.branches_for_layout()
        n = len(blist)
        for i, b in enumerate(blist):
            x = 140 + i * ((W - 280) / max(n - 1, 1))
            branches.append({"id": b["id"], "type": "branch", "kind": b["kind"],
                             "label": b["name"], "sub": b.get("summary", ""),
                             "x": x, "y": 200, "w": 210, "h": 64, "ref": b})
        steps = []
        for br in branches:
            for j, s in enumerate(br["ref"].get("steps", [])):
                steps.append({"id": s["id"], "type": "step", "kind": br["kind"],
                              "label": s["title"], "sub": s.get("detail", ""),
                              "x": br["x"], "y": 320 + j * 86, "w": 240, "h": 62,
                              "ref": s, "branchId": br["id"]})
        self.nodes = [self.root_node, *branches, *steps]
        self.edges = []
        for br in branches:
            self.edges.append({"from": self.root_node, "to": br})
        for s in steps:
            br = next(b for b in branches if b["id"] == s["branchId"])
            idx = [x["id"] for x in br["ref"].get("steps", [])].index(s["id"])
            prev = br if idx == 0 else next(
                x for x in steps if x["branchId"] == br["id"]
                and [y["id"] for y in br["ref"].get("steps", [])][idx - 1] == x["id"])
            self.edges.append({"from": prev, "to": s})

    def _rebuild_tabs(self) -> None:
        while (child := self.tabs.get_first_child()) is not None:
            self.tabs.remove(child)

        def _all(_btn: Gtk.Button) -> None:
            self._clear_dim()
            self._select(self.root_node)

        all_t = Gtk.Button(label="Todos")
        all_t.connect("clicked", _all)
        self.tabs.append(all_t)
        for br in [n for n in self.nodes if n["type"] == "branch"]:
            t = Gtk.Button(label=br["label"])
            t.connect("clicked", self._make_tab_cb(br))
            self.tabs.append(t)

    def _make_tab_cb(self, br: dict):
        def _cb(_btn: Gtk.Button) -> None:
            self._clear_dim()
            self._select(br)
            for n in self.nodes:
                if n is not br and n.get("branchId") != br["id"] and n["type"] != "root":
                    n["_dim"] = True
            self.drawing.queue_draw()
        return _cb

    def _clear_dim(self) -> None:
        for n in self.nodes:
            n.pop("_dim", None)

    # ----------------------------------------------------------- draw
    def _draw(
        self,
        area: Gtk.DrawingArea,
        cr: Any,
        width: int,
        height: int,
        data: Any = None,
    ) -> None:
        # `Gtk.DrawingArea.set_draw_func` no GTK4 entrega um `cairo_t` já pronto
        # (`GtkDrawingAreaDrawFunc = (area, cr, width, height, user_data)`), e
        # não um `Gtk.Snapshot` — o modelo snapshot pertence ao vfunc `snapshot`
        # de um widget. Assinar `(area, snapshot, width, height)` fazia o GTK
        # passar 5 argumentos para uma função de 4 e o desenho inteiro morria com
        # `TypeError`, deixando a área preta. O `data` (o `None` de
        # `set_draw_func`) é o 5º argumento e precisa constar da assinatura.
        ctx = cr
        ctx.save()
        ctx.translate(self.tx, self.ty)
        ctx.scale(self.scale, self.scale)
        ctx.set_source_rgb(*_hex("#EEF0F3"))
        ctx.paint()
        ctx.set_source_rgb(*_hex("#9AA0A8"))
        ctx.set_line_width(1.4 / self.scale)
        for e in self.edges:
            ctx.move_to(e["from"]["x"], e["from"]["y"] + e["from"]["h"] / 2)
            ctx.line_to(e["to"]["x"], e["to"]["y"] - e["to"]["h"] / 2)
            ctx.stroke()
        for n in self.nodes:
            self._draw_node(ctx, n)
        ctx.restore()

    def _draw_node(self, ctx: Any, n: dict) -> None:
        if n.get("_dim"):
            ctx.push_group()
        fill, stroke = self._colors(n)
        x, y, w, h = n["x"] - n["w"] / 2, n["y"] - n["h"] / 2, n["w"], n["h"]
        ctx.set_source_rgb(*_hex(fill))
        self._round_rect(ctx, x, y, w, h, 10)
        ctx.fill()
        ctx.set_source_rgb(*_hex(stroke))
        ctx.set_line_width((2.4 if self.selected is n else 1.2) / self.scale)
        self._round_rect(ctx, x, y, w, h, 10)
        ctx.stroke()
        ctx.set_source_rgb(*_INK)
        ctx.select_font_face("Sans", 0, 0)
        ctx.set_font_size(13 / self.scale)
        tw = ctx.text_extents(n["label"]).width
        ctx.move_to(n["x"] - tw / 2, n["y"] - (4 if n.get("sub") else -4))
        ctx.show_text(n["label"])
        if n.get("sub"):
            ctx.set_source_rgb(*_MUTED)
            ctx.set_font_size(11 / self.scale)
            sub = n["sub"].replace("$", "")[:46]
            sw = ctx.text_extents(sub).width
            ctx.move_to(n["x"] - sw / 2, n["y"] + 12 / self.scale)
            ctx.show_text(sub)
        if n.get("_dim"):
            ctx.pop_group_to_source()
            ctx.paint_with_alpha(0.3)

    @staticmethod
    def _round_rect(ctx: Any, x: float, y: float, w: float, h: float, r: float) -> None:
        ctx.new_path()
        ctx.arc(x + r, y + r, r, 3.14159265, 3.14159265 * 1.5)
        ctx.arc(x + w - r, y + r, r, 3.14159265 * 1.5, 0)
        ctx.arc(x + w - r, y + h - r, r, 0, 3.14159265 / 2)
        ctx.arc(x + r, y + h - r, r, 3.14159265 / 2, 3.14159265)
        ctx.close_path()

    @staticmethod
    def _colors(n: dict) -> tuple[str, str]:
        if n["type"] == "root":
            return _ROOT_FILL, _ROOT_STROKE
        if n["kind"] == "viewpoint":
            return _VIEW_FILL, _VIEW_STROKE
        if n["type"] == "branch":
            return _METH_FILL, _METH_STROKE
        return _STEP_FILL, _STEP_STROKE

    # --------------------------------------------------------- eventos
    def _on_scroll(self, ctrl: Any, dx: float, dy: float) -> bool:
        factor = 1.1 if dy < 0 else 0.9
        self.scale = max(0.3, min(3.0, self.scale * factor))
        self.zoom_lbl.set_text(f"{int(self.scale * 100)}%")
        self.drawing.queue_draw()
        return True

    def _on_drag_begin(self, gesture: Any, start_x: float, start_y: float) -> None:
        self._drag_start = (self.tx, self.ty)

    def _on_drag_update(self, gesture: Any, off_x: float, off_y: float) -> None:
        self.tx = self._drag_start[0] + off_x
        self.ty = self._drag_start[1] + off_y
        self.drawing.queue_draw()

    def _on_click(self, gesture: Any, n_press: int, x: float, y: float) -> None:
        gx = (x - self.tx) / self.scale
        gy = (y - self.ty) / self.scale
        node = self._node_at(gx, gy)
        if node:
            self._clear_dim()
            self._select(node)

    def _node_at(self, gx: float, gy: float) -> Optional[dict]:
        for n in self.nodes:
            if (n["x"] - n["w"] / 2) <= gx <= (n["x"] + n["w"] / 2) and \
               (n["y"] - n["h"] / 2) <= gy <= (n["y"] + n["h"] / 2):
                return n
        return None

    def _fit(self) -> None:
        self.tx, self.ty, self.scale = 0.0, 0.0, 1.0
        self.zoom_lbl.set_text("100%")
        self.drawing.queue_draw()

    def _on_rigor(self, switch: Gtk.Switch, *a: Any) -> None:
        self.rigor = switch.get_active()
        self._rebuild_panel(self.selected)

    # -------------------------------------------------------- seleção
    def _select(self, node: dict) -> None:
        self.selected = node
        self._rebuild_panel(node)
        self.drawing.queue_draw()

    def highlight(self, query: str) -> bool:
        """Seleciona e centraliza o nó que casa com `query` (voz/agente).

        Devolve ``True`` quando encontrou algo. Consulta vazia volta ao
        enunciado. Toda a decisão de qual nó escolher é do `core.board_nav`
        (puro, testável sem display); aqui só se aplica o resultado.
        """
        node = find_node(self.nodes, query)
        if node is None:
            return False
        self._clear_dim()
        self._select(node)
        self._center_on(node)
        self.drawing.queue_draw()
        return True

    def _center_on(self, node: dict) -> None:
        """Centraliza a vista no nó (com fallback de tamanho antes do 1º desenho)."""
        w = self.drawing.get_width() or 1000
        h = self.drawing.get_height() or 640
        self.tx = w / 2 - node["x"] * self.scale
        self.ty = h / 2 - node["y"] * self.scale
        self.zoom_lbl.set_text(f"{int(self.scale * 100)}%")

    def _rebuild_panel(self, node: Optional[dict]) -> None:
        while (c := self.panel.get_first_child()) is not None:
            self.panel.remove(c)
        if node is None or self.graph is None:
            return
        if node["type"] == "root":
            self._append_card("Enunciado", self.graph.problem_statement, latex=True)
        elif node["type"] == "branch":
            b = node["ref"]
            self._append_card(b["name"], b.get("summary", ""))
            for s in b.get("steps", []):
                self._step_card(s)
        else:
            self._step_card(node["ref"])
        if self.rigor:
            self._append_rigor()

    def _append_card(self, title: str, body: str, latex: bool = False) -> None:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.set_margin_bottom(8)
        card.append(Gtk.Label(label=title, xalign=0))
        lbl = self._math_label(body) if latex else Gtk.Label(label=body, xalign=0, wrap=True)
        lbl.set_wrap(True)
        card.append(lbl)
        self.panel.append(card)

    def _step_card(self, s: dict) -> None:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.set_margin_bottom(8)
        card.append(Gtk.Label(label=s["title"], xalign=0))
        card.append(Gtk.Label(label=s.get("detail", ""), xalign=0, wrap=True))
        if s.get("latex"):
            card.append(self._math_label(s["latex"]))
        if s.get("justification"):
            j = Gtk.Label(label=f"Por quê: {s['justification']}", xalign=0, wrap=True)
            j.add_css_class("dim-label")
            card.append(j)
        self.panel.append(card)

    def _append_rigor(self) -> None:
        g = self.graph
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(6)
        sep.set_margin_bottom(6)
        self.panel.append(sep)
        self.panel.append(Gtk.Label(label="Modo rigor chinês (已知/求/解/答)", xalign=0))
        for tag, val in (("已知", g.givens), ("求", g.unknown), ("答", g.answer)):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(Gtk.Label(label=tag, xalign=0))
            row.append(self._math_label(val))
            self.panel.append(row)
        self.panel.append(Gtk.Label(label="Pontuação por passo (步骤分)", xalign=0))
        for it in g.scoring.items:
            self.panel.append(Gtk.Label(label=f"{it['step']} — {it['points']} pt", xalign=0))
        self.panel.append(Gtk.Label(label=f"Total: {g.scoring.total} pontos", xalign=0))

    def _math_label(self, tex: str) -> Gtk.Widget:
        """Rótulo com a fórmula: SVG (matplotlib) quando disponível, senão texto cru."""
        svg = render_math_to_svg(tex) if has_math_renderer() else None
        if svg:
            try:
                fd, path = tempfile.mkstemp(suffix=".svg")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(svg)
                pic = Gtk.Picture.new_for_filename(path)
                pic.set_size_request(-1, 28)
                return pic
            except Exception:
                pass
        return Gtk.Label(label=tex.replace("$", ""), selectable=True, wrap=True)

    # ----------------------------------------------------------- play
    def _play(self) -> None:
        if self._play_src is not None:
            GLib.source_remove(self._play_src)
            self._play_src = None
        seq = [self.root_node] + [n for n in self.nodes if n["type"] == "step"]
        idx = 0

        def tick() -> bool:
            nonlocal idx
            if idx >= len(seq):
                self._select(self.root_node)
                self._play_src = None
                return False
            self._clear_dim()
            self._select(seq[idx])
            idx += 1
            return True

        self._play_src = GLib.timeout_add(1100, tick)

    # ----------------------------------------------------- utilidade
    @classmethod
    def show_dialog(
        cls,
        parent: Optional[Gtk.Window],
        graph: SolutionGraph,
        focus: str = "",
    ) -> "BlackboardWidget":
        """Cria uma janela independente exibindo o quadro para `graph`.

        `focus` (opcional) destaca um nó logo na abertura — é o caminho do
        comando de voz "abre o quadro e mostra a fatoração".
        """
        win = Adw.Window() if Adw else Gtk.Window()
        win.set_default_size(1000, 640)
        if parent is not None:
            win.set_transient_for(parent)
        widget = cls()
        widget.load_solution(graph)
        widget._host_window = win
        if focus:
            widget.highlight(focus)
        win.set_content(widget)
        win.present()
        return widget
