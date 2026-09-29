"""Gera PNGs da pílula e do popover nos dois modos (Entrega C / P1-5).

Por que existe: os defeitos desta entrega são **mentiras de interface**. Um teste pode
afirmar `assertIn("Indisponível no modo local", labels)` e ficar verde com o layout
quebrado, o chip ilegível ou a cor errada. Aqui o widget é **desenhado de fato**
(GTK4 → Gsk → textura → PNG) para inspeção visual — a mesma régua de
`tests/test_blackboard_render.py`.

Uso (precisa de display real):
    .venv/bin/python scripts/snapshot_mode_indicator.py

Saída em `docs/`:
    indicador-modo-pilula-local.png    — pílula, modo local (chip "Local")
    indicador-modo-pilula-nuvem.png    — pílula, modo nuvem (chip "Nuvem")
    indicador-modo-popover-local.png   — popover, modo local (tela indisponível, modelo local)
    indicador-modo-popover-nuvem.png   — popover, modo nuvem (controles de tela presentes)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

import gi  # noqa: E402

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, Gdk, GLib, Graphene, Gtk  # noqa: E402

Adw.init()

from zorin_copilot.ui import style  # noqa: E402
from zorin_copilot.ui.voice_pill import VoicePillWindow  # noqa: E402

DOCS = RAIZ / "docs"


def _drain(context: GLib.MainContext) -> None:
    while context.pending():
        context.iteration(False)


def _pump(predicate, timeout_ms: int = 3000) -> bool:
    """Roda o loop principal até `predicate()` ou o timeout."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        _drain(context)
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _FakeFence:
    """Cerca espacial mínima: a pílula só pergunta pela janela-alvo."""

    def get_target_window(self):
        w = MagicMock()
        w.display_name.return_value = "Workbuddy ai"
        return w


def _fake_client(mode: str, supports_video: bool, model: str) -> MagicMock:
    c = MagicMock()
    c.is_active.return_value = True
    c.is_muted.return_value = False
    c.is_video_streaming.return_value = False
    c.toggle_video_stream.return_value = False
    c.video_mode = "active_window"
    # A cerca é da nuvem: no modo local ela não existe (`local_voice.py:576`), e o PNG
    # precisa mostrar o estado real de cada modo — não o mesmo estado nos dois.
    c.fence = _FakeFence() if mode == "cloud" else None
    c.model = model
    c.mode = mode
    c.supports_video = supports_video
    return c


def _render(widget, path: Path) -> bool:
    """Rasteriza `widget` para `path`. Devolve False se o GTK não produziu nó."""
    if not _pump(lambda: widget.get_width() > 0 and widget.get_height() > 0):
        print(f"  ! {path.name}: widget sem tamanho alocado")
        return False
    width, height = widget.get_width(), widget.get_height()

    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot.new()
    paintable.snapshot(snapshot, width, height)
    node = snapshot.to_node()
    if node is None:
        print(f"  ! {path.name}: nenhum nó de render")
        return False

    native = widget.get_native()
    renderer = native.get_renderer() if native is not None else None
    if renderer is None:
        print(f"  ! {path.name}: sem Gsk.Renderer")
        return False

    texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, width, height))
    texture.save_to_png(str(path))
    print(f"  ok {path.name}  ({width}x{height})")
    return True


def _pill(mode: str, supports_video: bool, model: str) -> VoicePillWindow:
    return VoicePillWindow(
        application=None,
        live_client=_fake_client(mode, supports_video, model),
    )


def _popover_root(pill: VoicePillWindow, host: Gtk.Window):
    """Solta o conteúdo do popover e o hospeda numa janela própria para rasterizar.

    O `Gtk.Popover` desenha numa superfície própria, fora da árvore da janela — não dá
    para fotografá-lo pelo nativo da pílula. Reparentar o conteúdo é manipulação só de
    script: o código de produção não muda.
    """
    pill._on_details_clicked()
    popover = pill._details_popover
    root = popover.get_child()
    popover.set_child(None)

    # O fundo/borda vêm da classe do popover, cujo seletor exige a janela da pílula — e a
    # cor do texto depende do tema (light/dark-glass) que o setup real aplica. Sem isto o
    # render sai branco sobre branco (medido: 2 cores, 0 pixels de texto).
    root.add_css_class("pill-popover")
    root.set_halign(Gtk.Align.START)
    root.set_valign(Gtk.Align.START)
    style.setup_glass_pill_window(host)
    host.set_child(root)
    host.present()
    return root


def main() -> int:
    display = Gdk.Display.get_default()
    if display is None:
        print("sem display: nada a rasterizar")
        return 2
    style.apply_glass_theme(display)
    # Tema forçado: sem isto o texto resolve branco (tema escuro da sessão) sobre uma
    # superfície clara, e o PNG sai ilegível — medido: 2 cores, 0 pixels de texto.
    # Os docs do projeto usam fundo claro, então forçamos claro para os dois modos.
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

    ok = True

    for mode, supports_video, model, sufixo in (
        ("local", False, "gemini-2.5-flash-live", "local"),
        ("cloud", True, "gemini-3.8-live", "nuvem"),
    ):
        print(f"[{sufixo}]")

        # 1. A pílula em si — onde vive o chip de modo. Sem `application=`: uma janela
        #    de aplicação exige o sinal `startup` já emitido, e aqui não há app rodando.
        pill = _pill(mode, supports_video, model)
        pill.present()
        ok &= _render(pill.container, DOCS / f"indicador-modo-pilula-{sufixo}.png")
        pill.destroy()

        # 2. O popover — onde vivem os controles e as linhas informativas.
        pill2 = _pill(mode, supports_video, model)
        popover_host = Gtk.Window()
        popover_host.set_default_size(330, 560)
        root = _popover_root(pill2, popover_host)
        ok &= _render(root, DOCS / f"indicador-modo-popover-{sufixo}.png")
        popover_host.destroy()

    _drain(GLib.MainContext.default())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
