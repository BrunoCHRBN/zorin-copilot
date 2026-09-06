# Decisão de design: renderer de Markdown em Python puro que emite markup do Pango.
# A alternativa (WebView) exigiria webkit2gtk e quebraria o visual nativo do app; a
# alternativa "usar a lib markdown" adicionaria uma dependência de empacotamento a um
# app distribuído como .deb. Aqui o custo fica em ~250 linhas e o resultado continua
# sendo um Gtk.Label, com seleção de texto, tema e quebra de linha nativos.
#
# Regra de segurança: o texto cru é escapado ANTES de qualquer tag ser inserida. Saída
# de modelo de linguagem nunca pode injetar markup do Pango.

"""Conversor de Markdown para markup do Pango (GTK 4)."""

from __future__ import annotations

import html
import re

# Marcadores de placeholder: bytes de controle ausentes do texto já normalizado.
_PH = "\x00"

_FENCE_RE = re.compile(r"^(?:```|~~~)\s*([A-Za-z0-9_+#-]*)\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_UL_ITEM_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_OL_ITEM_RE = re.compile(r"^(\s*)(\d{1,9})[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^\s{0,3}>\s?(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$")

_RULE = "─" * 24
_QUOTE_BAR = "┃ "

_HEADING_SIZES = {1: "x-large", 2: "large"}

# Espaços de indentação por nível de lista (Markdown usa 2, mas 4 é comum).
_INDENT_UNIT = 2


def format_markdown_to_markup(text: str) -> str:
    """Converte Markdown em markup do Pango.

    Suporta: títulos ATX, blocos de código cercados, citações, listas ordenadas e
    não ordenadas com aninhamento por indentação, regras horizontais, tabelas no
    estilo GFM e inline (negrito, itálico, riscado, código, links, imagens).

    Nunca propaga exceção: em caso de erro devolve o texto escapado, porque um
    markup inválido faria o Gtk.Label falhar ao renderizar a resposta inteira.
    """
    if not text:
        return ""
    try:
        return _render_document(text)
    except Exception:  # pragma: no cover - rede de segurança
        return html.escape(text)


# ----------------------------------------------------------------------
# Normalização
# ----------------------------------------------------------------------
def _normalize(text: str) -> list[str]:
    """Remove caracteres de controle, expande tabulações e devolve as linhas."""
    src = text.replace("\r\n", "\n").replace("\r", "\n")
    # Preserva apenas \n; descarta o resto do lixo de controle (inclui \x00).
    src = "".join(ch for ch in src if ch == "\n" or ord(ch) >= 32)
    return src.replace("\t", "    ").split("\n")


# ----------------------------------------------------------------------
# Blocos
# ----------------------------------------------------------------------
def _render_document(text: str) -> str:
    lines = _normalize(text)
    blocks = _parse_blocks(lines)
    rendered = [_render_block(kind, payload) for kind, payload in blocks]
    return "\n\n".join(part for part in rendered if part)


def _parse_blocks(lines: list[str]) -> list[tuple[str, object]]:
    """Agrupa as linhas em blocos lógicos (código, título, lista, parágrafo...)."""
    blocks: list[tuple[str, object]] = []
    i = 0
    total = len(lines)

    while i < total:
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        if _HR_RE.match(line):
            blocks.append(("hr", None))
            i += 1
            continue

        fence = _FENCE_RE.match(line)
        if fence:
            body: list[str] = []
            i += 1
            while i < total and not _FENCE_RE.match(lines[i]):
                body.append(lines[i])
                i += 1
            i += 1  # consome a cerca de fechamento (ou passa do fim)
            blocks.append(("code", (fence.group(1), body)))
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            blocks.append(("heading", (len(heading.group(1)), heading.group(2))))
            i += 1
            continue

        if "|" in line and i + 1 < total and _TABLE_SEP_RE.match(lines[i + 1]):
            # A linha seguinte é o separador: define o alinhamento de cada coluna.
            aligns = _table_alignments(lines[i + 1], len(_split_table_row(line)))
            i += 2
            rows: list[list[str]] = []
            while i < total and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            blocks.append(("table", (_split_table_row(line), aligns, rows)))
            continue

        if _QUOTE_RE.match(line):
            inner: list[str] = []
            while i < total:
                quoted = _QUOTE_RE.match(lines[i])
                if quoted:
                    inner.append(quoted.group(1))
                    i += 1
                elif lines[i].strip() and inner:
                    # Continuação lazy de uma citação.
                    inner.append(lines[i])
                    i += 1
                else:
                    break
            blocks.append(("quote", inner))
            continue

        if _UL_ITEM_RE.match(line) or _OL_ITEM_RE.match(line):
            items: list[tuple[int, int | None, str]] = []
            while i < total:
                raw = lines[i]
                if not raw.strip():
                    # Linha em branco encerra a lista, a menos que venha mais item.
                    nxt = i + 1
                    while nxt < total and not lines[nxt].strip():
                        nxt += 1
                    if nxt < total and (
                        _UL_ITEM_RE.match(lines[nxt]) or _OL_ITEM_RE.match(lines[nxt])
                    ):
                        i = nxt
                        continue
                    break

                ol = _OL_ITEM_RE.match(raw)
                ul = _UL_ITEM_RE.match(raw)
                if ol:
                    items.append((len(ol.group(1)), int(ol.group(2)), ol.group(3)))
                    i += 1
                    continue
                if ul:
                    items.append((len(ul.group(1)), None, ul.group(2)))
                    i += 1
                    continue
                if raw.startswith((" ", "  ")) and items:
                    # Linha de continuação do item anterior.
                    indent, num, prev = items[-1]
                    items[-1] = (indent, num, f"{prev} {raw.strip()}")
                    i += 1
                    continue
                break

            blocks.append(("list", items))
            continue

        paragraph: list[str] = []
        while i < total:
            cur = lines[i]
            if (
                not cur.strip()
                or _HEADING_RE.match(cur)
                or _HR_RE.match(cur)
                or _FENCE_RE.match(cur)
                or _QUOTE_RE.match(cur)
                or _UL_ITEM_RE.match(cur)
                or _OL_ITEM_RE.match(cur)
            ):
                break
            paragraph.append(cur)
            i += 1
        if paragraph:
            blocks.append(("para", paragraph))

    return blocks


def _render_block(kind: str, payload: object) -> str:
    if kind == "hr":
        return _RULE

    if kind == "code":
        _lang, body = payload  # type: ignore[misc]
        if not body:
            return ""
        return f"<tt>{html.escape(chr(10).join(body), quote=False)}</tt>"

    if kind == "heading":
        level, raw = payload  # type: ignore[misc]
        inner = _inline(raw)
        size = _HEADING_SIZES.get(level)
        if size:
            return f'<span size="{size}"><b>{inner}</b></span>'
        return f"<b>{inner}</b>"

    if kind == "quote":
        inner_lines = payload  # type: ignore[misc]
        # Cada linha visual recebe a barra, senão só a primeira fica marcada.
        rendered = _render_document("\n".join(inner_lines)).split("\n")
        return "\n".join(f"{_QUOTE_BAR}{ln}" for ln in rendered)

    if kind == "list":
        return _render_list(payload)  # type: ignore[arg-type]

    if kind == "table":
        header, aligns, rows = payload  # type: ignore[misc]
        return _render_table(header, aligns, rows)

    if kind == "para":
        lines: list[str] = payload  # type: ignore[misc]
        # Quebras simples do autor são preservadas: em chat elas quase sempre são
        # intencionais (endereços, passos curtos, versos).
        return "\n".join(_inline(ln) for ln in lines)

    return ""


def _render_list(items: list[tuple[int, int | None, str]]) -> str:
    out: list[str] = []
    for indent, number, text in items:
        level = max(0, indent // _INDENT_UNIT)
        pad = "  " * level
        bullet = f"{number}." if number is not None else "•"
        out.append(f"{pad}{bullet} {_inline(text)}")
    return "\n".join(out)


# ----------------------------------------------------------------------
# Tabelas (estilo GFM renderizadas em monoespaçado alinhado)
# ----------------------------------------------------------------------
def _split_table_row(line: str) -> list[str]:
    trimmed = line.strip()
    if trimmed.startswith("|"):
        trimmed = trimmed[1:]
    if trimmed.endswith("|"):
        trimmed = trimmed[:-1]
    return [cell.strip() for cell in trimmed.split("|")]


def _table_alignments(separator: str, ncols: int) -> list[str]:
    """Lê a linha separadora (``|---|---:|``) para saber a direção de cada coluna."""
    cells = [c.strip() for c in separator.strip().strip("|").split("|")]
    aligns: list[str] = []
    for cell in cells:
        left = cell.startswith(":")
        right = cell.endswith(":")
        if left and right:
            aligns.append("center")
        elif right:
            aligns.append("right")
        else:
            aligns.append("left")
    return (aligns + ["left"] * ncols)[:ncols]


def _render_table(header: list[str], aligns: list[str], rows: list[list[str]]) -> str:
    body = rows
    ncols = max([len(header), *[len(r) for r in body]] or [0])
    if ncols == 0:
        return ""

    def pad(row: list[str]) -> list[str]:
        return row + [""] * (ncols - len(row))

    header = pad(header)
    body = [pad(r) for r in body]
    aligns = (aligns + ["left"] * ncols)[:ncols]

    widths = [
        max([len(header[c])] + [len(r[c]) for r in body] or [0]) for c in range(ncols)
    ]

    def fmt(cells: list[str]) -> str:
        parts = []
        for c, cell in enumerate(cells):
            text = _inline(cell)
            # Largura calculada sobre o texto puro: markup não conta para alinhamento.
            plain = re.sub(r"<[^>]+>", "", text)
            gap = max(0, widths[c] - len(plain))
            if aligns[c] == "right":
                parts.append(" " * gap + text)
            elif aligns[c] == "center":
                left = gap // 2
                parts.append(" " * left + text + " " * (gap - left))
            else:
                parts.append(text + " " * gap)
        return " │ ".join(parts)

    lines = [fmt(header), "─" * (sum(widths) + 3 * (ncols - 1))]
    lines.extend(fmt(r) for r in body)
    return f"<tt>{html.escape(chr(10).join(lines), quote=False)}</tt>"


# ----------------------------------------------------------------------
# Inline
# ----------------------------------------------------------------------
def _inline(text: str) -> str:
    """Aplica formatação inline sobre o texto já escapado."""
    s = html.escape(text, quote=False)
    store: list[str] = []

    def stash(rendered: str) -> str:
        store.append(rendered)
        return f"{_PH}{len(store) - 1}{_PH}"

    # 1. Código inline — protegido primeiro para não sofrer formatação depois.
    s = re.sub(r"(`{1,2})([^`\n]+?)\1", lambda m: stash(f"<tt><b>{m.group(2).strip()}</b></tt>"), s)

    # 2. Imagens: não há como renderizar em Gtk.Label, então viram rótulo.
    s = re.sub(
        r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)",
        lambda m: stash(f"[imagem: {m.group(1)}]" if m.group(1) else "[imagem]"),
        s,
    )

    # 3. Links — já vão para o store para que o autolink não os envolva de novo.
    #    O texto chega escapado; reescapar aqui geraria "&amp;amp;", então só as
    #    aspas (que quote=False preservou) precisam de tratamento.
    def link_sub(m: re.Match[str]) -> str:
        label, url = m.group(1), m.group(2).replace('"', "&quot;")
        return stash(f'<a href="{url}">{label}</a>')

    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", link_sub, s)

    # 4. URL solta. Seguro agora: os hrefs do passo 3 estão dentro de placeholders.
    s = re.sub(
        r"(?<![\w/])https?://[^\s<>\[\]()]+",
        lambda m: stash(f'<a href="{m.group(0)}">{m.group(0)}</a>'),
        s,
    )

    # 5. Negrito, itálico e riscado sobre o que sobrou.
    s = re.sub(r"\*\*([^\s*][^*]*?[^\s*]|[^\s*])\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w])__([^_]+)__(?![\w])", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*([^\s*][^*]*?[^\s*]|[^\s*])\*(?![\w*])", r"<i>\1</i>", s)
    # Sublinhado só no meio de palavras não conta (evita quebrar snake_case).
    s = re.sub(r"(?<![\w])_([^_\n]+)_(?![\w])", r"<i>\1</i>", s)
    s = re.sub(r"~~([^~\n]+)~~", r"<s>\1</s>", s)

    # 6. Restaura em ordem reversa: conteúdo interno tem índice menor que o externo.
    for idx in range(len(store) - 1, -1, -1):
        s = s.replace(f"{_PH}{idx}{_PH}", store[idx])

    # Rede de segurança: nenhum placeholder deve sobrar.
    return re.sub(rf"{_PH}\d+{_PH}", "", s)
