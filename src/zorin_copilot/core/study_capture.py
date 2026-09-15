# Decisão de design: capturar material de estudo lendo a ÁRVORE DE ACESSIBILIDADE
# RENDERIZADA, não o HTML. O AVA da SENAC entrega um shell de SPA dentro de iframe/SCORM:
# `WebPageReader.fetch_and_clean` baixa a URL de novo e recebe 7 palavras. O que o aluno
# vê, porém, está no AT-SPI — é a mesma árvore que um leitor de tela usaria. Ler dali:
#   • não depende de login/sessão (o navegador já está autenticado);
#   • atravessa iframe e player SCORM, porque o que importa é o que foi desenhado;
#   • não quebra nenhuma regra do ambiente — é leitura, não automação.
#
# O módulo é puro: recebe inspetor e área de transferência por injeção, então os testes
# rodam headless com uma árvore falsa. Nenhum import de pyatspi/GTK em nível de módulo.

"""Captura de material de estudo (aulas, PDFs em player, SCORM) a partir da tela."""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

#: Abaixo disso, a captura é considerada "rala": provavelmente pegamos só o chrome
#: da janela, não o conteúdo. Vale a pena tentar outro caminho antes de salvar.
DEFAULT_MIN_WORDS = 120

#: Profundidade e largura generosas: o DOM de uma aula é fundo (document > section >
#: div > div > p). Os limites do `DesktopInspector` (4 níveis, 30 filhos) servem para
#: achar controles, não para ler texto — por isso este módulo caminha a árvore por
#: conta própria.
DEFAULT_MAX_DEPTH = 18
DEFAULT_MAX_CHILDREN = 80

#: Papéis cujo texto é conteúdo. Ao encontrar texto aqui, paramos de descer:
#: evita duplicar o parágrafo inteiro como "filho" de si mesmo.
COLLECT_ROLES = {
    "paragraph",
    "heading",
    "text",
    "static",
    "list_item",
    "link",
    "label",
    "caption",
    "block_quote",
    "table_cell",
    "article",
    "description_term",
    "description_value",
}

#: Papéis de interface: nunca contêm conteúdo de aula e ainda poluem a captura
#: ("Arquivo", "Editar", "Ctrl+T"...). A subárvore inteira é descartada.
SKIP_ROLES = {
    "menu_bar",
    "menu",
    "menu_item",
    "check_menu_item",
    "radio_menu_item",
    "tool_bar",
    "status_bar",
    "scroll_bar",
    "separator",
    "combo_box",
    "button",
    "push_button",
    "toggle_button",
    "check_box",
    "radio_button",
    "spin_button",
    "slider",
    "entry",
    "password_text",
    "terminal",
    "page_tab",
    "page_tab_list",
    "progress_bar",
    "notification",
    "tooltip",
    "image",
    "icon",
    "canvas",
    "audio",
    "video",
}

#: Papéis recipiente: descemos, mas não coletamos texto (geralmente devolveria a
#: concatenação dos filhos e duplicaria tudo).
CONTAINER_HINT = {
    "application",
    "frame",
    "window",
    "dialog",
    "panel",
    "scroll_pane",
    "section",
    "filler",
    "layered_pane",
    "split_pane",
    "table",
    "table_row",
    "list",
    "html_container",
    "document_web",
    "document_frame",
    "embedded",
    "internal_frame",
}

#: No modo grosso o texto costuma estar inteiro no nó do documento (é assim que
#: vários players SCORM expõem o conteúdo). Coletar dali é o plano B.
COARSE_COLLECT = {"document_web", "document_frame", "embedded", "internal_frame", "article"}

#: Texto de um nó com mais de ~20 kB é sinal de que pegamos a página inteira de uma vez.
MAX_NODE_CHARS = 20_000

#: Estratégias aceitas por `LessonCapture.capture`.
STRATEGIES = ("auto", "atspi", "clipboard", "ocr")


@dataclass
class CaptureResult:
    """Resultado de uma captura. Serializável (CLI --json)."""

    ok: bool
    strategy: str = ""
    title: str = ""
    text: str = ""
    app: str = ""
    url: str = ""
    word_count: int = 0
    path: str = ""
    discipline: str = ""
    warnings: list[str] = field(default_factory=list)
    captured_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "strategy": self.strategy,
            "title": self.title,
            "word_count": self.word_count,
            "app": self.app,
            "url": self.url,
            "path": self.path,
            "discipline": self.discipline,
            "warnings": list(self.warnings),
            "captured_at": self.captured_at,
            "preview": self.text[:400],
        }


class LessonCapture:
    """Extrai o conteúdo de uma aula a partir da tela (AT-SPI) ou da área de transferência."""

    def __init__(
        self,
        inspector: Any | None = None,
        clipboard: Any | None = None,
        ocr: Any | None = None,
        *,
        min_words: int = DEFAULT_MIN_WORDS,
        documents_dir: str | None = None,
    ) -> None:
        self._inspector = inspector
        self._clipboard = clipboard
        self._ocr = ocr
        self.min_words = max(0, int(min_words))
        self.documents_dir = documents_dir or os.path.expanduser("~/Documentos/Estudos")

    # -- componentes -------------------------------------------------------- #

    @property
    def inspector(self) -> Any | None:
        if self._inspector is None:
            try:
                from .a11y import DesktopInspector

                self._inspector = DesktopInspector()
            except Exception as exc:
                logger.debug("Inspetor indisponível: %s", exc)
                return None
        return self._inspector

    @property
    def clipboard(self) -> Any | None:
        if self._clipboard is None:
            try:
                from .clipboard import ClipboardService

                self._clipboard = ClipboardService
            except Exception as exc:
                logger.debug("Área de transferência indisponível: %s", exc)
                return None
        return self._clipboard

    @property
    def ocr(self) -> Any | None:
        """Serviço de OCR espacial (grounding visual) — terceira via quando a árvore é cega."""
        if self._ocr is None:
            try:
                from .ui_grounding import UIGroundingService

                self._ocr = UIGroundingService
            except Exception as exc:
                logger.debug("OCR indisponível: %s", exc)
                return None
        return self._ocr

    # -- captura ------------------------------------------------------------ #

    def capture(
        self,
        app_name: str | None = None,
        strategy: str = "auto",
        discipline: str | None = None,
    ) -> CaptureResult:
        """Captura o conteúdo visível. `strategy`: auto | atspi | clipboard | ocr.

        A ordem do `auto` é por custo e qualidade: a árvore de acessibilidade é
        instantânea e limpa; a área de transferência depende de o aluno selecionar;
        o OCR lê pixels, então é mais lento e sujo — entra só quando os dois
        primeiros ficam ralos.
        """
        strategy = (strategy or "auto").strip().lower()
        if strategy not in STRATEGIES:
            strategy = "auto"
        warnings: list[str] = []
        app = app_name or (self.inspector.get_focused_app() if self.inspector else "") or ""
        url = self._active_url()

        atspi_result: CaptureResult | None = None
        clipboard_result: CaptureResult | None = None
        ocr_result: CaptureResult | None = None

        if strategy in ("auto", "atspi"):
            atspi_result = self._capture_atspi(app, url)
            if not atspi_result.ok:
                warnings.append(atspi_result.warnings[0] if atspi_result.warnings else "AT-SPI sem conteúdo.")

        if strategy in ("auto", "clipboard"):
            if strategy == "clipboard" or (atspi_result is not None and atspi_result.word_count < self.min_words):
                clipboard_result = self._capture_clipboard(app, url)
                # Só reclamamos do clipboard se ele era a única esperança: quando o
                # AT-SPI já devolveu algo, o aviso que serve ao usuário é "rala".
                if not clipboard_result.ok and (atspi_result is None or not atspi_result.ok):
                    warnings.append("Área de transferência vazia ou sem texto.")

        if strategy in ("auto", "ocr"):
            so_far = self._best(atspi_result, clipboard_result)
            if strategy == "ocr" or so_far is None or so_far.word_count < self.min_words:
                ocr_result = self._capture_ocr(app, url)
                if not ocr_result.ok and (so_far is None or not so_far.ok):
                    warnings.append(ocr_result.warnings[0] if ocr_result.warnings else "OCR sem texto.")

        if strategy == "atspi":
            return self._with_discipline(
                atspi_result or self._empty(app, url, "AT-SPI não disponível."), discipline
            )
        if strategy == "clipboard":
            return self._with_discipline(
                clipboard_result or self._empty(app, url, "Área de transferência não disponível."), discipline
            )
        if strategy == "ocr":
            return self._with_discipline(ocr_result or self._empty(app, url, "OCR não disponível."), discipline)

        # auto: fica com o que tiver mais conteúdo — e avisa quando estiver ralo.
        best = self._best(atspi_result, clipboard_result, ocr_result)
        if best is None:
            return self._empty(app, url, "Nenhuma estratégia de captura disponível.")
        best.discipline = (discipline or "").strip()
        best.warnings.extend(warnings)
        if best.word_count < self.min_words:
            best.warnings.append(
                f"Captura rala ({best.word_count} palavras, mínimo {self.min_words}): "
                "provavelmente pegamos só o chrome da janela. "
                "Tente selecionar o texto da aula com Ctrl+A, Ctrl+C e repetir com --strategy clipboard; "
                "se o player não expuser a árvore de acessibilidade, --strategy ocr lê a tela por pixels."
            )
        return best

    @staticmethod
    def _with_discipline(result: CaptureResult, discipline: str | None) -> CaptureResult:
        result.discipline = (discipline or "").strip()
        return result

    def _best(self, *results: CaptureResult | None) -> CaptureResult | None:
        candidates = [r for r in results if r is not None and r.ok]
        if not candidates:
            candidates = [r for r in results if r is not None]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.word_count)

    def _capture_atspi(self, app: str, url: str) -> CaptureResult:
        inspector = self.inspector
        if inspector is None:
            return self._empty(app, url, "AT-SPI indisponível (barramento de acessibilidade fora do ar).")
        try:
            tree = inspector.get_ui_tree(app or None)
        except Exception as exc:
            return self._empty(app, url, f"Falha ao inspecionar: {exc}")
        if tree is None:
            return self._empty(app, url, f"Não foi possível inspecionar '{app or 'app em foco'}'.")

        lines = extract_lines(tree.raw_ref if getattr(tree, "raw_ref", None) is not None else tree)
        if not lines:
            # Modo grosso: alguns players expõem o texto inteiro só no nó do documento.
            lines = extract_lines(
                tree.raw_ref if getattr(tree, "raw_ref", None) is not None else tree,
                coarse=True,
            )
        text = "\n\n".join(lines)
        result = CaptureResult(
            ok=bool(lines),
            strategy="atspi",
            title=self._title_for(app, lines, url),
            text=text,
            app=app,
            url=url,
            word_count=count_words(text),
            captured_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        if not lines:
            result.warnings.append("Árvore de acessibilidade sem texto de conteúdo (player pode não expor AT-SPI).")
        return result

    def _capture_clipboard(self, app: str, url: str) -> CaptureResult:
        service = self.clipboard
        if service is None:
            return self._empty(app, url, "Área de transferência indisponível.")
        try:
            raw = service.get_text() or ""
        except Exception as exc:
            return self._empty(app, url, f"Falha ao ler a área de transferência: {exc}")
        lines = clean_lines(raw.splitlines())
        text = "\n\n".join(lines)
        return CaptureResult(
            ok=bool(lines),
            strategy="clipboard",
            title=self._title_for(app, lines, url),
            text=text,
            app=app,
            url=url,
            word_count=count_words(text),
            captured_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

    def _capture_ocr(self, app: str, url: str) -> CaptureResult:
        """Lê a tela por pixels — a saída para players que não expõem a árvore.

        Exige `tesseract` (com o pacote de português) instalado; sem ele o
        serviço devolve lista vazia e a captura apenas avisa, não quebra.
        """
        service = self.ocr
        if service is None:
            return self._empty(app, url, "OCR indisponível (módulo de grounding visual não carregou).")
        try:
            elements = service.scan_screen(fence=None)
        except Exception as exc:
            return self._empty(app, url, f"Falha ao varrer a tela: {exc}")
        if not elements:
            return self._empty(
                app,
                url,
                "OCR não encontrou texto na tela (verifique se o tesseract e o pacote 'por' estão instalados).",
            )

        lines = clean_lines(ocr_lines(elements))
        text = "\n\n".join(lines)
        return CaptureResult(
            ok=bool(lines),
            strategy="ocr",
            title=self._title_for(app, lines, url),
            text=text,
            app=app,
            url=url,
            word_count=count_words(text),
            captured_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

    def _title_for(self, app: str, lines: Sequence[str], url: str) -> str:
        for line in lines[:5]:
            if 0 < len(line) <= 120 and count_words(line) <= 14:
                return line.strip().rstrip(":")
        if url:
            return url
        return app or "Material capturado"

    def _active_url(self) -> str:
        """Melhor esforço: o AVA raramente expõe a URL, mas quando expõe é ouro."""
        try:
            from .browser import WebPageReader

            tabs = WebPageReader.get_active_browser_tabs()
            for entry in tabs:
                title = (entry or {}).get("active_tab", "")
                if title and re.search(r"https?://", title):
                    return re.search(r"https?://[^\s]+", title).group(0)
        except Exception:
            pass
        return ""

    def _empty(self, app: str, url: str, reason: str) -> CaptureResult:
        return CaptureResult(
            ok=False,
            app=app,
            url=url,
            warnings=[reason],
            captured_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

    # -- persistência -------------------------------------------------------- #

    def save(self, result: CaptureResult, filename: str | None = None, directory: str | None = None) -> str:
        """Grava a captura em Markdown e devolve o caminho (sem sobrescrever).

        Com disciplina, o material vai para `<documentos>/<disciplina>/` — é o que
        mantém Contabilidade separada de Marketing sem o aluno pensar em pastas.
        """
        base = os.path.expanduser(directory) if directory else self.documents_dir
        if not directory and (result.discipline or "").strip():
            base = os.path.join(base, slugify(result.discipline) or "")
        target_dir = os.path.expanduser(base)
        os.makedirs(target_dir, exist_ok=True)

        name = (filename or "").strip() or f"{slugify(result.title) or 'material'}.md"
        if not name.endswith(".md"):
            name += ".md"
        path = os.path.join(target_dir, name)
        if os.path.exists(path):
            stem, ext = os.path.splitext(name)
            counter = 2
            while os.path.exists(os.path.join(target_dir, f"{stem}-{counter}{ext}")):
                counter += 1
            path = os.path.join(target_dir, f"{stem}-{counter}{ext}")

        with open(path, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(result))
        result.path = path
        return path


# --------------------------------------------------------------------------- #
# Caminhada da árvore
# --------------------------------------------------------------------------- #


def extract_lines(
    root: Any,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_children: int = DEFAULT_MAX_CHILDREN,
    coarse: bool = False,
) -> list[str]:
    """Extrai linhas de conteúdo de um nó (Atspi cru ou `UIElement`).

    `coarse=True` aceita o texto de nós recipientes — é o plano B quando o player
    só expõe o texto no nó do documento.
    """
    collected: list[str] = []
    _walk(root, collected, 0, max_depth, max_children, coarse)
    return clean_lines(collected)


def _walk(
    node: Any,
    out: list[str],
    depth: int,
    max_depth: int,
    max_children: int,
    coarse: bool,
) -> None:
    if node is None or depth > max_depth:
        return

    role = _role_of(node)
    if role in SKIP_ROLES:
        return

    text = _text_of(node)
    if coarse:
        wants_text = role in COLLECT_ROLES or role in COARSE_COLLECT or role not in CONTAINER_HINT
    else:
        wants_text = role in COLLECT_ROLES
    if wants_text and text:
        out.append(text)
        return  # achou conteúdo: não precisa dos filhos

    for child in _children_of(node, max_children):
        _walk(child, out, depth + 1, max_depth, max_children, coarse)


def _role_of(node: Any) -> str:
    getter = getattr(node, "get_role_name", None)
    if callable(getter):
        try:
            return (getter() or "").strip().lower()
        except Exception:
            return ""
    return str(getattr(node, "role", "") or "").strip().lower()


def _children_of(node: Any, max_children: int) -> list[Any]:
    counter = getattr(node, "get_child_count", None)
    getter = getattr(node, "get_child_at_index", None)
    if callable(counter) and callable(getter):
        try:
            total = int(counter())
        except Exception:
            return []
        children = []
        for index in range(min(total, max_children)):
            try:
                child = getter(index)
            except Exception:
                continue
            if child is not None:
                children.append(child)
        return children
    return list(getattr(node, "children", []) or [])[:max_children]


def ocr_lines(elements: Sequence[Any]) -> list[str]:
    """Converte elementos de OCR em linhas de leitura.

    O `parse_tesseract_tsv` devolve palavras *e* frases (agrupamento da linha
    inteira). Usar os dois duplicaria tudo, então ficamos com as frases e só
    aproveitamos as palavras que sobraram sozinhas na linha — é o que preserva
    títulos curtos e listas de um item.
    """
    phrases: list[Any] = []
    words: list[Any] = []
    for element in elements or []:
        (phrases if getattr(element, "is_phrase", False) else words).append(element)

    kept: list[Any] = list(phrases)
    for word in words:
        if not _inside_any(word, phrases):
            kept.append(word)

    # Mesma limpeza de `extract_lines`: ruído de interface (só números, CSS
    # vazado) e repetições consecutivas saem aqui, não no chamador.
    return clean_lines(element.text for element in _read_order(kept))


def _inside_any(word: Any, phrases: Sequence[Any]) -> bool:
    """Uma palavra já pertence a uma frase se o centro dela cai dentro do retângulo."""
    wx = getattr(word, "x", 0) or 0
    wy = getattr(word, "y", 0) or 0
    for phrase in phrases:
        box = getattr(phrase, "bbox", None) or ()
        if len(box) != 4:
            continue
        px, py, width, height = box
        if px <= wx <= px + width and py <= wy <= py + height:
            return True
    return False


def _read_order(elements: Sequence[Any]) -> list[Any]:
    """Ordem de leitura: agrupa por linha (com tolerância da altura) e, dentro dela, da esquerda para a direita."""
    heights = sorted(
        (getattr(e, "bbox", None) or (0, 0, 0, 0))[3]
        for e in elements
        if (getattr(e, "bbox", None) or (0, 0, 0, 0))[3] > 0
    )
    tolerance = heights[len(heights) // 2] if heights else 12
    tolerance = max(6, int(tolerance))
    return sorted(elements, key=lambda e: (round((getattr(e, "y", 0) or 0) / tolerance), getattr(e, "x", 0) or 0))


def _text_of(node: Any) -> str:
    """Texto de um nó: primeiro a interface Text, depois o nome acessível."""
    text_iface = None
    getter = getattr(node, "get_text_iface", None)
    if callable(getter):
        try:
            text_iface = getter()
        except Exception:
            text_iface = None
    if text_iface is not None:
        try:
            count = int(text_iface.get_character_count())
            if 0 < count <= MAX_NODE_CHARS:
                value = text_iface.get_text(0, count)
                if value and value.strip():
                    return " ".join(value.split())
        except Exception:
            pass

    name_getter = getattr(node, "get_name", None)
    if callable(name_getter):
        try:
            name = name_getter() or ""
        except Exception:
            name = ""
    else:
        name = getattr(node, "name", "") or ""
    return " ".join(str(name).split())


# --------------------------------------------------------------------------- #
# Limpeza e formatação
# --------------------------------------------------------------------------- #


def count_words(text: str) -> int:
    return len((text or "").split())


def clean_lines(lines: Iterable[str]) -> list[str]:
    """Remove vazios, ruído de interface e duplicatas consecutivas."""
    cleaned: list[str] = []
    for raw in lines:
        line = " ".join(str(raw or "").split())
        if not line:
            continue
        if _is_noise(line):
            continue
        if cleaned and cleaned[-1] == line:
            continue  # repetição típica de player SCORM
        cleaned.append(line)
    return cleaned


def _is_noise(line: str) -> bool:
    """Linha que claramente não é conteúdo de aula."""
    if len(line) < 3:
        return True
    if not re.search(r"[^\W\d_]", line, flags=re.UNICODE):
        return True  # só números/pontuação
    if len(line) > 2000:
        return True  # CSS/JSON vazado para a árvore
    return False


def slugify(text: str, max_len: int = 60) -> str:
    """Slug seguro para nome de arquivo, preservando acentos do português."""
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"\s+", "-", value.strip().lower())
    value = re.sub(r"[^a-z0-9\-_à-ÿ]", "", value)
    value = re.sub(r"-+", "-", value)  # "aula - 3" vira "aula-3", não "aula---3"
    return value[:max_len].strip("-")


def render_markdown(result: CaptureResult) -> str:
    """Markdown final, com metadados no topo — é o que o RAG indexa depois."""
    header = [
        f"# {result.title or 'Material capturado'}",
        "",
        f"- Capturado em: {result.captured_at or datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- Origem: {result.app or 'desconhecida'} ({result.strategy or 'n/d'})",
        f"- Palavras: {result.word_count}",
    ]
    if result.discipline:
        header.append(f"- Disciplina: {result.discipline}")
    if result.url:
        header.append(f"- URL: {result.url}")
    for warning in result.warnings:
        header.append(f"- Aviso: {warning}")
    header += ["", "---", "", result.text.strip(), ""]
    return "\n".join(header)
