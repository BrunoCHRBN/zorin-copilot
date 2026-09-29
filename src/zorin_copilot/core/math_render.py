"""math_render — tipografia de fórmulas para o Quadro Negro, sem WebView.

O app recusa WebView no chat (ver UI_ANALYSIS.md), então o Quadro Negro tipografa
LaTeX usando o backend SVG do matplotlib quando ele está presente. Se não estiver
(instalável via pip, mas ausente no .deb mínimo), o renderizador devolve `None` e o
widget exibe o LaTeX cru em monoespaço — degradação graciosa, nunca quebra.
"""

from __future__ import annotations

import io

try:  # matplotlib é opcional no runtime mínimo
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_svg import FigureCanvasSVG
    _HAS_MPL = True
except Exception:  # pragma: no cover - depende do ambiente
    _HAS_MPL = False


def has_math_renderer() -> bool:
    return _HAS_MPL


def render_math_to_svg(tex: str, *, fontsize: float = 14.0, color: str = "#1d1f23",
                       dpi: int = 150) -> "str | None":
    """Renderiza `$...$` (ou LaTeX cru) em SVG. Retorna None se indisponível/erro."""
    if not _HAS_MPL or not tex:
        return None
    try:
        fig = plt.Figure()
        fig.patch.set_alpha(0.0)
        canvas = FigureCanvasSVG(fig)
        text = fig.text(0.5, 0.5, f"${tex}$", fontsize=fontsize, color=color,
                         ha="center", va="center")
        renderer = canvas.get_renderer()
        bbox = text.get_window_extent(renderer)
        w = max(bbox.width / dpi, 0.5)
        h = max(bbox.height / dpi, 0.5)
        fig.set_size_inches(w, h)
        text.set_position((0.5, 0.5))
        canvas.draw()
        buf = io.StringIO()
        canvas.print_svg(buf)
        return buf.getvalue()
    except Exception:  # pragma: no cover - defesa contra LaTeX inválido
        return None
