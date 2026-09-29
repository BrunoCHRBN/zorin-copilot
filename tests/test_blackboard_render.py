"""Render de verdade do Quadro Negro: GTK4 -> Gsk -> textura -> pixels.

Diferente de `test_board_nav.py` (lógica pura de seleção) e de
`test_study_show_board.py` (contrato da ferramenta no registro), aqui o widget é
**desenhado de fato** por um display real: a `Gtk.DrawingArea` é mapeada, o GTK
invoca o `draw_func`, o `Gtk.Snapshot` vira nó de render, o `Gsk.Renderer`
rasteriza e o PNG resultante é inspecionado pixel a pixel.

Esse caminho é o único que pega a classe de defeito que motivou este teste: no
GTK4 o `draw_func` de uma `Gtk.DrawingArea` recebe um `cairo_t`, **não** um
`Gtk.Snapshot`. Assinado no modelo antigo (snapshot), o GTK passa um argumento a
mais, engole o `TypeError`, nada é pintado e a área sai preta — **sem levantar
exceção em Python**. Um teste que só chamasse `_draw()` à mão passaria feliz e
não veria o bug; só olhando os pixels ele aparece.

Requer servidor gráfico (X11, Wayland ou `xvfb-run`). Sem display, os testes são
pulados em vez de falharem.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import gi  # noqa: E402

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, Gdk, GLib, Graphene, Gtk  # noqa: E402

try:
    Adw.init()
    _TOOLKIT_OK = True
except Exception:  # pragma: no cover - só acontece sem servidor gráfico
    _TOOLKIT_OK = False

try:
    from PIL import Image
    _PIL_OK = True
except Exception:  # pragma: no cover - Pillow é dependência de teste
    _PIL_OK = False

if _TOOLKIT_OK:
    from zorin_copilot.ai.math_solver import build_quadratic_sample, try_solve  # noqa: E402
    from zorin_copilot.ui.widgets.blackboard import BlackboardWidget  # noqa: E402

#: Paleta do widget (ver `ui/widgets/blackboard.py`). O render tem de produzi-la.
BG = (238, 240, 243)         # #EEF0F3 - fundo do quadro
ROOT_FILL = (238, 237, 254)  # #EEEDFE - nó do enunciado
METH_FILL = (225, 245, 238)  # #E1F5EE - nós de método
STEP_FILL = (255, 255, 255)  # #FFFFFF - nós de passo
VIEW_FILL = (250, 238, 218)  # #FAEEDA - nós de ponto de vista

#: Geometria do layout (`BlackboardWidget._layout`), em coordenadas do grafo.
ROOT_CENTER = (480.0, 60.0)
ROOT_HALF = (150.0, 30.0)  # meia largura, meia altura

#: Tamanho **exigido** da área de desenho (ver `setUp`), em px.
#:
#: A janela do teste é **ladrilhada pelo compositor**: `set_default_size` é só um
#: pedido. Num espaço de trabalho cheio o Hyprland concede um ladrilho pequeno —
#: medido: janela 961x210, desenho 641x160. Nesse tamanho o `_layout` **não cabe**
#: (enunciado em y=30..90, métodos em y=168..232, passos a partir de y=289) e os
#: testes falhavam por *recorte*, não por defeito de desenho: o enunciado era
#: pintado e os métodos simplesmente ficavam fora da tela.
#:
#: O compositor honra o **mínimo**, não o tamanho pedido. Com `set_size_request`
#: a janela sai exatamente 1000x640 — o mesmo `set_default_size` que o app usa —
#: e o desenho 680x590. Qualquer WM respeita mínimo de janela, então isto não é
#: específico do Hyprland.
#:
#: A largura de 680 tem margem dos dois lados, e é escolhida de propósito: passa
#: da borda direita do enunciado (x=630) e fica **antes** da coluna do ponto de
#: vista (x=715..925). É essa ausência que dá sentido a
#: `test_viewpoint_node_is_rasterized_after_highlight`, que existe justamente
#: para verificar que `highlight` traz para a vista um nó que estava fora dela.
MIN_CANVAS_W = 680
MIN_CANVAS_H = 590

HAS_RENDER = bool(
    _TOOLKIT_OK
    and _PIL_OK
    and hasattr(Gtk, "WidgetPaintable")
    and hasattr(Gtk, "Snapshot")
    and Gdk.Display.get_default() is not None
)


def _drain(context: GLib.MainContext, budget: int = 500) -> None:
    """Despacha o que já está pronto no contexto, com teto de iterações."""
    for _ in range(budget):
        if not context.pending():
            return
        context.iteration(False)


def _pump(predicate, timeout_ms: int = 3000) -> bool:
    """Bombeia o contexto principal até `predicate` ficar verdadeiro.

    Bombeia direto em vez de criar um `GLib.MainLoop` aninhado: num runner
    carregado o laço aninhado vence o prazo antes de o frame ser despachado e
    produz falso negativo (mesma lição de `test_ui_refactor.run_loop_until`).
    """
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        _drain(context)
        if predicate():
            return True
        time.sleep(0.005)
    _drain(context)
    return bool(predicate())


def _histogram(image) -> dict:
    """Histograma {(r, g, b): pixels} usando `getcolors` (API estável do Pillow)."""
    return {color: count for count, color in image.getcolors(maxcolors=1 << 24)}


def _collect_labels(widget) -> list[str]:
    """Todos os textos de `Gtk.Label` na subárvore de `widget`."""
    found: list[str] = []
    if isinstance(widget, Gtk.Label):
        found.append(widget.get_text())
    child = widget.get_first_child()
    while child is not None:
        found.extend(_collect_labels(child))
        child = child.get_next_sibling()
    return found


@unittest.skipUnless(HAS_RENDER, "requer display real + Gtk.WidgetPaintable + Pillow")
class BlackboardRenderTest(unittest.TestCase):
    """Desenha o Quadro Negro num display real e confere o que saiu nos pixels."""

    def setUp(self):
        self.graph = build_quadratic_sample()
        self.win = Gtk.Window()
        self.win.set_default_size(1000, 640)
        self.bb = BlackboardWidget()
        self.bb.load_solution(self.graph)
        # O tamanho tem de ser **exigido**, não pedido: `set_default_size` acima
        # é ignorado por um compositor que ladrilha (ver `MIN_CANVAS_W`). Isto
        # vira o tamanho mínimo da janela, e aí o `_layout` cabe inteiro.
        self.bb.drawing.set_size_request(MIN_CANVAS_W, MIN_CANVAS_H)
        self.win.set_child(self.bb)
        self.win.present()

        ready = _pump(
            lambda: self.bb.drawing.get_width() >= MIN_CANVAS_W
            and self.bb.drawing.get_height() >= MIN_CANVAS_H
            and self.bb.get_native() is not None,
            timeout_ms=4000,
        )
        if not ready:  # pragma: no cover - ambiente sem compositor utilizável
            self.win.destroy()
            self.skipTest("o display não alocou o quadro num tamanho utilizável")
        self._wait_for_stable_canvas()

    def _wait_for_stable_canvas(self, timeout_ms: int = 1500) -> None:
        """Espera o tamanho alocado da área parar de mudar.

        O compositor redimensiona a janela de forma assíncrona: o `_pump` acima
        libera assim que o tamanho **mínimo** é atingido, mas um resize pode
        ainda estar a caminho. Se rasterizarmos nesse intervalo, o
        `Gtk.WidgetPaintable` fotografa o widget num tamanho e o
        `render_texture` recorta noutro — o conteúdo sai escalonado e os pontos
        de amostragem caem fora do nó. Era essa a falha de ~10 %: o pixel do
        interior do enunciado saía na cor de **fundo**, `(238, 240, 243)` em vez
        de `(238, 237, 254)`, em ponto que a geometria lógica garante estar
        dentro do nó. Exigir três leituras iguais seguidas elimina a corrida sem
        afrouxar asserção nenhuma: a geometria continua conferida com exatidão.
        """
        context = GLib.MainContext.default()
        deadline = time.monotonic() + timeout_ms / 1000.0
        previous = None
        repeats = 0
        while time.monotonic() < deadline:
            _drain(context)
            current = (self.bb.drawing.get_width(), self.bb.drawing.get_height())
            if current == previous and current[0] > 0 and current[1] > 0:
                repeats += 1
                if repeats >= 3:
                    return
            else:
                repeats = 0
            previous = current
            time.sleep(0.01)

    def tearDown(self):
        try:
            self.win.destroy()
        except Exception:  # pragma: no cover - janela já desmontada
            pass
        _drain(GLib.MainContext.default())

    # --------------------------------------------------------------- apoio
    def _snapshot_node(self, width: int, height: int, timeout_ms: int = 2500):
        """Nó de render da área de desenho, esperando um frame novo se preciso.

        `Gtk.WidgetPaintable` devolve `None` enquanto o widget está "sujo" (com
        `queue_draw` pendente): o GTK só entrega o nó depois de processar um
        frame. Por isso esperamos o frame chegar em vez de fotografar um estado
        velho — ou, pior, um vazio.
        """
        context = GLib.MainContext.default()
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            paintable = Gtk.WidgetPaintable.new(self.bb.drawing)
            snapshot = Gtk.Snapshot.new()
            paintable.snapshot(snapshot, width, height)
            node = snapshot.to_node()
            if node is not None:
                return node
            if time.monotonic() >= deadline:
                return None
            _drain(context)
            time.sleep(0.005)

    def _render(self):
        """Rasteriza a área de desenho atual e devolve (imagem PIL, largura, altura)."""
        width = self.bb.drawing.get_width()
        height = self.bb.drawing.get_height()
        self.assertGreater(width, 0, "área de desenho sem largura alocada")
        self.assertGreater(height, 0, "área de desenho sem altura alocada")

        self.bb.drawing.queue_draw()
        node = self._snapshot_node(width, height)
        self.assertIsNotNone(
            node, "o GTK não produziu nó de render (nada foi desenhado)"
        )

        native = self.bb.get_native()
        self.assertIsNotNone(native, "widget sem nativo: nada a rasterizar")
        renderer = native.get_renderer()
        self.assertIsNotNone(renderer, "o nativo não expôs um Gsk.Renderer")

        texture = renderer.render_texture(
            node, Graphene.Rect().init(0, 0, width, height)
        )
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            texture.save_to_png(path)
            image = Image.open(path).convert("RGB")
            image.load()  # materializa antes de apagar o arquivo
        finally:
            os.unlink(path)
        return image, width, height

    # -------------------------------------------------------------- testes
    def test_canvas_is_not_blank(self):
        """O quadro não pode sair preto: prova que o `draw_func` realmente rodou."""
        image, width, height = self._render()
        self.assertEqual(image.size, (width, height))

        hist = _histogram(image)
        dominant = max(hist, key=hist.get)
        self.assertNotEqual(
            dominant, (0, 0, 0),
            "o quadro saiu totalmente preto: o draw_func falhou silenciosamente",
        )
        self.assertEqual(dominant, BG, "o fundo do quadro não é o esperado")
        self.assertGreater(
            hist[BG] / float(width * height), 0.30,
            "o fundo não domina a área: o desenho pode ter sido abortado no meio",
        )

    def test_enunciado_metodos_e_passos_are_rasterized(self):
        """Enunciado, métodos e passos aparecem pintados com suas cores."""
        image, _, _ = self._render()
        hist = _histogram(image)

        for name, color in (
            ("enunciado", ROOT_FILL),
            ("método", METH_FILL),
            ("passo", STEP_FILL),
        ):
            self.assertIn(color, hist, f"nó de {name} não foi desenhado (cor {color})")
            self.assertGreater(
                hist[color], 400,
                f"área pintada de {name} pequena demais ({hist[color]} px)",
            )

    def test_viewpoint_node_is_rasterized_after_highlight(self):
        """O ponto de vista (数形结合) fica fora da vista inicial: centralizá-lo o pinta."""
        # O layout tem 960 de largura e o ramo do ponto de vista fica em x=820,
        # fora da janela. `highlight` o traz para o centro — é o mesmo caminho da
        # voz ("abre o quadro e mostra o ponto de vista geométrico").
        self.assertTrue(self.bb.highlight("geométrico"))
        self.assertEqual(self.bb.selected["kind"], "viewpoint")

        image, _, _ = self._render()
        hist = _histogram(image)
        self.assertGreater(
            hist.get(VIEW_FILL, 0), 400,
            "o nó do ponto de vista não foi desenhado nem depois de centralizado",
        )

    def test_root_node_is_painted_at_its_layout_position(self):
        """O nó do enunciado sai exatamente na geometria calculada por `_layout`."""
        image, _, _ = self._render()

        cx, cy = ROOT_CENTER
        half_w, half_h = ROOT_HALF
        # Dentro da caixa, longe do texto centralizado: 10 px à direita da borda
        # esquerda, na altura do meio.
        inside = (int(cx - half_w + 10), int(cy))
        self.assertEqual(
            image.getpixel(inside), ROOT_FILL,
            f"o interior do nó do enunciado não está pintado em {inside}",
        )
        # Logo à esquerda da borda o quadro volta a ser fundo — a fronteira do
        # nó está onde o layout diz que está.
        outside = (int(cx - half_w - 6), int(cy))
        self.assertEqual(
            image.getpixel(outside), BG,
            f"há tinta fora do nó do enunciado em {outside}",
        )

    def test_background_shows_where_no_node_exists(self):
        """Canto inferior esquerdo: fora de qualquer nó, tem de ser o fundo."""
        image, _, height = self._render()
        self.assertEqual(image.getpixel((5, height - 5)), BG)

    def test_highlight_recenters_the_selected_node(self):
        """`highlight` seleciona e centraliza: o nó alvo passa a ocupar o meio."""
        before = (self.bb.tx, self.bb.ty)

        self.assertTrue(self.bb.highlight("fatoração"))
        self.assertEqual(self.bb.selected["label"], "Fatoração")
        self.assertNotEqual(
            (self.bb.tx, self.bb.ty), before, "a vista não foi recentralizada"
        )

        image, width, height = self._render()
        # A caixa do método foi levada ao centro; o preenchimento de método tem
        # de dominar a região central.
        crop = image.crop((width // 2 - 100, height // 2 - 25,
                           width // 2 + 100, height // 2 + 25))
        hist = _histogram(crop)
        self.assertGreater(
            hist.get(METH_FILL, 0), 0.40 * crop.width * crop.height,
            "o nó destacado não foi desenhado no centro da vista",
        )

    def test_zoom_updates_scale_label_and_pixels(self):
        """A roda do mouse muda a escala, o rótulo de zoom e o desenho."""
        base, _, _ = self._render()
        self.assertEqual(self.bb.scale, 1.0)

        self.bb._on_scroll(None, 0.0, 1.0)  # dy > 0 -> afasta
        self.assertAlmostEqual(self.bb.scale, 0.9, places=5)
        self.assertEqual(self.bb.zoom_lbl.get_text(), "90%")

        zoomed, _, _ = self._render()
        self.assertNotEqual(
            _histogram(base), _histogram(zoomed),
            "o desenho não mudou depois do zoom",
        )

    def test_dimmed_branch_renders_through_alpha_group(self):
        """Apagar uma aba passa pelo grupo com alpha do cairo sem quebrar o desenho."""
        base, _, _ = self._render()
        before = _histogram(base)

        branches = [n for n in self.bb.nodes if n["type"] == "branch"]
        fator = next(n for n in branches if n["label"] == "Fatoração")
        self.bb._make_tab_cb(fator)(None)

        dimmed = [n for n in self.bb.nodes if n.get("_dim")]
        self.assertTrue(dimmed, "nenhum nó foi apagado ao trocar de aba")

        image, _, _ = self._render()
        after = _histogram(image)
        self.assertLess(
            after.get(METH_FILL, 0), before.get(METH_FILL, 0),
            "o método apagado continuou com a cor cheia: o alpha não foi aplicado",
        )

    def test_rigor_mode_panel_lists_chinese_tags(self):
        """O interruptor de rigor chinês repovoa o painel com 已知/求/答 e 步骤分."""
        self.bb.rigor_switch.set_active(True)
        self.assertTrue(self.bb.rigor)

        labels = _collect_labels(self.bb.panel)
        for tag in ("已知", "求", "答"):
            self.assertIn(tag, labels, f"a etiqueta {tag} não apareceu no painel")
        self.assertTrue(
            any("步骤分" in text for text in labels),
            "a pontuação por passo (步骤分) não apareceu no painel",
        )

    def test_renders_a_freshly_solved_graph(self):
        """Um grafo vindo do solver real também é desenhado (não só o exemplo fixo)."""
        graph = try_solve("x^2 - 4x + 3 = 0")
        self.assertIsNotNone(graph, "o solver não resolveu a quadrática")
        self.bb.load_solution(graph)
        self.assertEqual(self.bb.graph.problem_statement, graph.problem_statement)

        image, _, _ = self._render()
        hist = _histogram(image)
        self.assertGreater(hist.get(ROOT_FILL, 0), 400)
        self.assertGreater(hist.get(METH_FILL, 0), 400)

    def test_play_registers_and_advances_the_animation(self):
        """O botão Reproduzir registra o timer e a seleção avança sozinha."""
        self.bb._play()
        self.assertIsNotNone(self.bb._play_src, "a animação não registrou timer")
        try:
            # Bombear até a condição que a asserção seguinte exige, não até a
            # PRIMEIRA mudança. Antes o predicado era `selected is not root_node`,
            # que fica verdadeiro já no primeiro quadro da animação — e aí a
            # asserção seguinte podia pegar a seleção ainda num "branch":
            # `AssertionError: 'branch' != 'step'`. Não é lentidão da máquina, é
            # corrida entre o predicado do `_pump` e a asserção: os dois têm de
            # falar do mesmo estado.
            self.assertTrue(
                _pump(
                    lambda: self.bb.selected is not None
                    and self.bb.selected["type"] == "step",
                    timeout_ms=3000,
                ),
                "a reprodução não avançou a seleção até um passo",
            )
            self.assertEqual(self.bb.selected["type"], "step")
        finally:
            if self.bb._play_src is not None:
                GLib.source_remove(self.bb._play_src)
                self.bb._play_src = None

    def test_show_dialog_opens_a_window_with_the_focus_applied(self):
        """`show_dialog` cria a janela, carrega o grafo e aplica o foco inicial."""
        widget = BlackboardWidget.show_dialog(
            None, build_quadratic_sample(), focus="passo 2"
        )
        try:
            self.assertIsNotNone(widget._host_window)
            self.assertEqual(widget.selected["label"], "Encontrar os fatores")
            self.assertTrue(
                _pump(lambda: widget.drawing.get_width() > 0, timeout_ms=3000),
                "a janela do quadro não alocou a área de desenho",
            )
        finally:
            try:
                widget._host_window.destroy()
            except Exception:  # pragma: no cover - janela já desmontada
                pass
            _drain(GLib.MainContext.default())


if __name__ == "__main__":
    unittest.main()
