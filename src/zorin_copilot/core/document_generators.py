"""Geração de documentos Office reais (.docx / .pptx) a partir de Markdown.

O conteúdo que o assistente produz já é Markdown; estes helpers convertem esse
Markdown em arquivos Word/PowerPoint binários e válidos, em vez de apenas
salvar o texto. Seguimos o padrão do projeto para dependências pesadas
(ver Pillow em `vision.py`/`clipboard.py`): os imports de `docx`/`pptx` ficam
dentro de `try/except` e, na falta, levantamos `MissingOfficeDependencyError`
com a instrução de instalação — o chamador decide como reportar ao usuário.
"""

from __future__ import annotations

import re

__all__ = [
    "MissingOfficeDependencyError",
    "generate_docx",
    "generate_pptx",
]


class MissingOfficeDependencyError(RuntimeError):
    """Levantado quando python-docx / python-pptx não estão instalados."""


# ---------------------------------------------------------------------------
# Parser de Markdown (subconjunto útil e robusto)
# ---------------------------------------------------------------------------

_INLINE_RE = re.compile(
    r"(\*\*(.+?)\*\*|__(.+?)__|\*(.+?)\*|_(.+?)_|`(.+?)`|\[(.+?)\]\((.+?)\))"
)


def _iter_inline(text: str):
    """Yield de tuplas (kind, *rest) para formatação inline.

    kind ∈ {'text','bold','italic','code','link'}
      - text  -> rest = [texto]
      - bold  -> rest = [texto]
      - italic-> rest = [texto]
      - code  -> rest = [texto]
      - link  -> rest = [texto_exibicao, url]
    """
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            yield ("text", text[pos : m.start()])
        if m.group(2) is not None:
            yield ("bold", m.group(2))
        elif m.group(3) is not None:
            yield ("bold", m.group(3))
        elif m.group(4) is not None:
            yield ("italic", m.group(4))
        elif m.group(5) is not None:
            yield ("italic", m.group(5))
        elif m.group(6) is not None:
            yield ("code", m.group(6))
        else:
            yield ("link", m.group(7), m.group(8))
        pos = m.end()
    if pos < len(text):
        yield ("text", text[pos:])


def _strip_inline(text: str) -> str:
    """Texto puro sem marcadores (usado em bullets do PowerPoint)."""
    return "".join(rest[0] for _, *rest in _iter_inline(text))


def _is_block_start(s: str) -> bool:
    return (
        s.startswith("```")
        or re.match(r"^#{1,6}\s", s) is not None
        or re.match(r"^[-*+]\s", s) is not None
        or re.match(r"^\d+\.\s", s) is not None
        or s.startswith(">")
        or re.match(r"^(-{3,}|\*{3,}|_{3,})$", s) is not None
    )


def _split_table_row(row: str) -> list[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [c.strip() for c in row.split("|")]


def _parse_markdown_blocks(md: str) -> list[dict]:
    """Converte Markdown em lista de blocos tipados.

    Cada bloco: {'type': ..., ...}
      heading -> {'type','level':int,'text'}
      paragraph/quote/code/hr -> {'type','text'}
      ulist/olist -> {'type','items':[str,...]}
      table -> {'type','rows':[[str,...],...]}
    """
    lines = md.replace("\r\n", "\n").split("\n")
    blocks: list[dict] = []
    i, n = 0, len(lines)

    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue

        # bloco de código (fence)
        if stripped.startswith("```"):
            buf = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # consome fence de fechamento
            blocks.append({"type": "code", "text": "\n".join(buf)})
            continue

        # título
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            blocks.append(
                {"type": "heading", "level": len(m.group(1)), "text": m.group(2).strip()}
            )
            i += 1
            continue

        # tabela (linha com '|' seguida de linha separadora)
        if "|" in stripped and i + 1 < n and re.match(r"^\s*\|?[\s:-]+\|", lines[i + 1]):
            rows = [_split_table_row(stripped)]
            i += 1  # pula a linha de separação
            while i < n and lines[i].strip() and "|" in lines[i].strip():
                rows.append(_split_table_row(lines[i].strip()))
                i += 1
            blocks.append({"type": "table", "rows": rows})
            continue

        # citação
        if stripped.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            blocks.append({"type": "quote", "text": " ".join(buf)})
            continue

        # regra horizontal
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            blocks.append({"type": "hr"})
            i += 1
            continue

        # lista não-ordenada
        if re.match(r"^[-*+]\s+", stripped):
            items = []
            while i < n and re.match(r"^[-*+]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*+]\s+", "", lines[i].strip()))
                i += 1
            blocks.append({"type": "ulist", "items": items})
            continue

        # lista ordenada
        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < n and re.match(r"^\d+\.\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
            blocks.append({"type": "olist", "items": items})
            continue

        # parágrafo (acumula até linha em branco ou início de outro bloco)
        buf = [stripped]
        i += 1
        while i < n and lines[i].strip() and not _is_block_start(lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        blocks.append({"type": "paragraph", "text": " ".join(buf)})

    return blocks


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def generate_docx(path: str, markdown: str) -> None:
    """Escreve um arquivo .docx real em `path` a partir de `markdown`."""
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor
    except ImportError as exc:  # pragma: no cover - dependência opcional
        raise MissingOfficeDependencyError(
            "python-docx não está instalado. Instale com: "
            "pip install zorin-copilot[office]  (ou 'pip install python-docx')"
        ) from exc

    doc = Document()
    for block in _parse_markdown_blocks(markdown):
        _render_docx_block(doc, block)
    doc.save(path)


def _render_docx_block(doc, block: dict) -> None:
    from docx.shared import Pt, RGBColor  # import local (docx opcional)

    t = block["type"]
    if t == "heading":
        level = min(max(block["level"], 1), 9)
        doc.add_heading(_strip_inline(block["text"]), level=level)
    elif t == "paragraph":
        p = doc.add_paragraph()
        _add_inline_runs(p, block["text"])
    elif t == "ulist":
        for item in block["items"]:
            p = doc.add_paragraph(style="List Bullet")
            _add_inline_runs(p, item)
    elif t == "olist":
        for item in block["items"]:
            p = doc.add_paragraph(style="List Number")
            _add_inline_runs(p, item)
    elif t == "code":
        p = doc.add_paragraph()
        run = p.add_run(block["text"])
        run.font.name = "Courier New"
        run.font.size = Pt(9)
    elif t == "quote":
        p = doc.add_paragraph()
        run = p.add_run(_strip_inline(block["text"]))
        run.italic = True
    elif t == "hr":
        doc.add_page_break()
    elif t == "table":
        rows = block["rows"]
        if not rows:
            return
        cols = max(len(r) for r in rows)
        table = doc.add_table(rows=len(rows), cols=cols)
        table.style = "Light Grid Accent 1"
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                if c < cols:
                    _add_inline_runs(table.cell(r, c).paragraphs[0], cell)


def _add_inline_runs(paragraph, text: str) -> None:
    from docx.shared import RGBColor  # import local (docx opcional)

    for kind, *rest in _iter_inline(text):
        if kind == "text":
            paragraph.add_run(rest[0])
        elif kind == "bold":
            run = paragraph.add_run(rest[0])
            run.bold = True
        elif kind == "italic":
            run = paragraph.add_run(rest[0])
            run.italic = True
        elif kind == "code":
            run = paragraph.add_run(rest[0])
            run.font.name = "Courier New"
        elif kind == "link":
            run = paragraph.add_run(rest[0])
            run.font.color.rgb = RGBColor(0x1A, 0x5F, 0xB4)
            run.underline = True


# ---------------------------------------------------------------------------
# PPTX
# ---------------------------------------------------------------------------


def generate_pptx(path: str, markdown: str) -> None:
    """Escreve um arquivo .pptx real em `path`.

    Slides são separados por uma linha '---' isolada. O primeiro cabeçalho
    (#, ##, ...) de cada slide vira o título; listas, parágrafos e citações
    viram bullets no layout "Título e Conteúdo".
    """
    try:
        from pptx import Presentation
    except ImportError as exc:  # pragma: no cover - dependência opcional
        raise MissingOfficeDependencyError(
            "python-pptx não está instalado. Instale com: "
            "pip install zorin-copilot[office]  (ou 'pip install python-pptx')"
        ) from exc

    prs = Presentation()
    for slide_md in _split_slides(markdown):
        if not slide_md.strip():
            continue
        slide = prs.slides.add_slide(prs.slide_layouts[1])  # Title and Content
        _render_pptx_slide(slide, slide_md)
    prs.save(path)


def _split_slides(md: str) -> list[str]:
    slides: list[str] = []
    cur: list[str] = []
    for line in md.replace("\r\n", "\n").split("\n"):
        if re.match(r"^\s*---\s*$", line):
            slides.append("\n".join(cur))
            cur = []
        else:
            cur.append(line)
    slides.append("\n".join(cur))
    return slides


def _render_pptx_slide(slide, slide_md: str) -> None:
    blocks = _parse_markdown_blocks(slide_md)
    title_set = False
    body: list[str] = []

    for block in blocks:
        t = block["type"]
        if t == "heading" and not title_set:
            if slide.shapes.title is not None:
                slide.shapes.title.text = _strip_inline(block["text"])
            title_set = True
            continue
        if t in ("paragraph", "quote", "code"):
            body.append(_strip_inline(block["text"]))
        elif t in ("ulist", "olist"):
            body.extend(_strip_inline(it) for it in block["items"])
        elif t == "heading":  # cabeçalhos extras viram bullets
            body.append(_strip_inline(block["text"]))
        elif t == "table":
            for row in block["rows"][1:]:  # ignora a linha de separação
                if row:
                    body.append(_strip_inline(row[0]))

    # fallback de título: primeiro item de corpo
    if not title_set and body and slide.shapes.title is not None:
        slide.shapes.title.text = body.pop(0)

    try:
        placeholder = slide.placeholders[1]  # corpo (Title and Content)
        tf = placeholder.text_frame
        tf.clear()
        first = True
        for item in body:
            p = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            p.text = item
            p.level = 0
    except (KeyError, IndexError):
        pass
