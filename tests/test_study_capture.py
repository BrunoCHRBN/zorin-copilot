# Decisão de design: os nós falsos imitam a API do pyatspi (get_role_name, get_name,
# get_text_iface, get_child_count, get_child_at_index) — os cinco métodos que o
# extrator realmente usa. Assim o teste valida a CAMINHADA e a LIMPEZA, que é onde
# moram os bugs, sem precisar de navegador, sessão gráfica ou AVA de verdade.

"""Testes da captura de material de estudo via árvore de acessibilidade."""

from __future__ import annotations

import importlib
import os

import pytest

from zorin_copilot import cli
from zorin_copilot.core.a11y import UIElement
from zorin_copilot.core.study_capture import (
    ocr_lines,
    CaptureResult,
    LessonCapture,
    clean_lines,
    count_words,
    extract_lines,
    render_markdown,
    slugify,
)


# --------------------------------------------------------------------------- #
# Dublês
# --------------------------------------------------------------------------- #


class FakeText:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_character_count(self) -> int:
        return len(self.text)

    def get_text(self, start: int, end: int) -> str:
        return self.text[start:end]


class FakeNode:
    """Nó mínimo com a cara do pyatspi."""

    def __init__(self, role: str, name: str = "", text: str | None = None, children=None):
        self.role = role
        self.name = name
        self.text = text
        self.children = list(children or [])

    # -- API pyatspi --
    def get_role_name(self) -> str:
        return self.role

    def get_name(self) -> str:
        return self.name

    def get_text_iface(self):
        return FakeText(self.text) if self.text is not None else None

    def get_child_count(self) -> int:
        return len(self.children)

    def get_child_at_index(self, index: int):
        return self.children[index] if 0 <= index < len(self.children) else None


class FakeInspector:
    def __init__(self, tree: FakeNode, app: str = "Firefox") -> None:
        self.tree = tree
        self.app = app

    def get_ui_tree(self, app_name=None):
        return UIElement(name=self.app, role="application", raw_ref=self.tree)

    def get_focused_app(self) -> str:
        return self.app


class FakeClipboard:
    def __init__(self, text: str | None) -> None:
        self.text = text

    def get_text(self):
        return self.text


# --------------------------------------------------------------------------- #
# Árvore de exemplo: uma aula com chrome de navegador em volta
# --------------------------------------------------------------------------- #


def arvore_de_aula() -> FakeNode:
    return FakeNode(
        "application",
        "Firefox",
        children=[
            FakeNode(
                "frame",
                "Aula 3 - Contabilidade Gerencial",
                children=[
                    FakeNode(
                        "menu_bar",
                        children=[FakeNode("menu_item", text="Arquivo"), FakeNode("menu_item", text="Editar")],
                    ),
                    FakeNode(
                        "tool_bar",
                        children=[
                            FakeNode("push_button", text="Voltar"),
                            FakeNode("separator"),
                            FakeNode("entry", text="https://ava.senac.br"),
                        ],
                    ),
                    FakeNode(
                        "document_web",
                        name="Aula 3",
                        children=[
                            FakeNode("heading", text="Contabilidade Gerencial"),
                            FakeNode(
                                "paragraph",
                                text="A contabilidade gerencial é o ramo da contabilidade "
                                "voltado ao fornecimento de informações para a tomada de "
                                "decisões dentro das organizações.",
                            ),
                            FakeNode(
                                "paragraph",
                                text="Diferente da contabilidade financeira, ela não segue "
                                "necessariamente os princípios contábeis geralmente aceitos.",
                            ),
                            FakeNode("heading", text="Principais instrumentos"),
                            FakeNode("list_item", text="Custeio por absorção"),
                            FakeNode("list_item", text="Custeio variável"),
                            FakeNode("status_bar", text="Carregado"),
                        ],
                    ),
                ],
            )
        ],
    )


@pytest.fixture(autouse=True)
def sem_url(monkeypatch):
    """Detecção de URL é best-effort e fala com AT-SPI: fora do escopo aqui."""
    monkeypatch.setattr(LessonCapture, "_active_url", lambda self: "")


# --------------------------------------------------------------------------- #
# Extração
# --------------------------------------------------------------------------- #


def test_extrai_conteudo_e_ignora_chrome_do_navegador():
    lines = extract_lines(arvore_de_aula())

    assert "Contabilidade Gerencial" in lines
    assert any("contabilidade gerencial é o ramo" in line for line in lines)
    assert "Custeio por absorção" in lines
    # chrome da janela fica de fora
    assert "Arquivo" not in lines
    assert "Editar" not in lines
    assert "Voltar" not in lines
    assert "Carregado" not in lines
    assert "https://ava.senac.br" not in lines


def test_ordem_do_conteudo_e_preservada():
    lines = extract_lines(arvore_de_aula())
    assert lines.index("Contabilidade Gerencial") < lines.index("Principais instrumentos")
    assert lines.index("Custeio por absorção") < lines.index("Custeio variável")


def test_no_com_texto_nao_tem_filhos_repetidos():
    """Se o nó já entregou texto, não descemos: evita parágrafo duplicado."""
    tree = FakeNode("paragraph", text="pai", children=[FakeNode("text", text="filho")])
    assert extract_lines(tree) == ["pai"]


def test_modo_grosso_pega_o_texto_no_no_do_documento():
    tree = FakeNode(
        "application",
        children=[
            FakeNode("document_web", text="Todo o conteúdo da aula está neste único nó.")
        ],
    )
    assert extract_lines(tree) == []  # granular: document_web é só recipiente
    assert extract_lines(tree, coarse=True) == ["Todo o conteúdo da aula está neste único nó."]


def test_arvore_vazia_devolve_lista_vazia():
    assert extract_lines(None) == []


def test_limpeza_remove_vazios_ruido_e_repeticoes():
    cleaned = clean_lines(["", "  ", "ok", "42", "Repetido", "Repetido", "Conteúdo real"])
    assert cleaned == ["Repetido", "Conteúdo real"]


def test_texto_longo_de_css_vazado_e_descartado():
    assert clean_lines(["a" * 2500]) == []


# --------------------------------------------------------------------------- #
# Utilitários
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("Aula 3 - Contabilidade Gerencial", "aula-3-contabilidade-gerencial"),
        ("Introdução à Gestão Financeira", "introducao-a-gestao-financeira"),
        ("  ", ""),
    ],
)
def test_slugify(texto, esperado):
    assert slugify(texto) == esperado


def test_count_words():
    assert count_words("uma duas três") == 3
    assert count_words("") == 0


# --------------------------------------------------------------------------- #
# Captura
# --------------------------------------------------------------------------- #


def test_captura_atspi_completa():
    capture = LessonCapture(
        inspector=FakeInspector(arvore_de_aula()),
        clipboard=FakeClipboard(None),
        min_words=20,  # a aula de exemplo tem ~45 palavras: captura saudável
    )
    result = capture.capture()

    assert result.ok is True
    assert result.strategy == "atspi"
    assert result.app == "Firefox"
    assert result.title == "Contabilidade Gerencial"
    assert result.word_count > 20
    assert result.warnings == []


def test_captura_rala_avisa_e_minimo_e_configuravel():
    capture = LessonCapture(
        inspector=FakeInspector(arvore_de_aula()),
        clipboard=FakeClipboard(None),
        min_words=9999,
    )
    result = capture.capture()
    assert result.ok is True
    assert any("rala" in aviso for aviso in result.warnings)
    assert "Ctrl+A" in result.warnings[-1]


def test_auto_cai_para_area_de_transferencia_quando_o_atspi_e_ralo():
    capture = LessonCapture(
        inspector=FakeInspector(FakeNode("application")),
        clipboard=FakeClipboard("Texto colado da aula.\n" + "palavra " * 200),
        min_words=50,
    )
    result = capture.capture()

    assert result.strategy == "clipboard"
    assert result.ok is True
    assert "Texto colado da aula." in result.text


def test_auto_escolhe_o_caminho_com_mais_conteudo():
    capture = LessonCapture(
        inspector=FakeInspector(arvore_de_aula()),
        clipboard=FakeClipboard("pouco texto"),
        min_words=1,
    )
    result = capture.capture()
    assert result.strategy == "atspi"  # a aula tem bem mais palavras que o clipboard


def test_estrategia_clipboard_forcada_ignora_atspi():
    capture = LessonCapture(
        inspector=FakeInspector(arvore_de_aula()), clipboard=FakeClipboard("só do clipboard")
    )
    result = capture.capture(strategy="clipboard")
    assert result.strategy == "clipboard"
    assert result.text == "só do clipboard"


def test_sem_conteudo_em_lugar_nenhum_devolve_ok_false_com_dica():
    capture = LessonCapture(
        inspector=FakeInspector(FakeNode("application")), clipboard=FakeClipboard("")
    )
    result = capture.capture()
    assert result.ok is False
    assert result.warnings


def test_inspetor_indisponivel_nao_levanta():
    class SemInspetor:
        def get_ui_tree(self, app=None):
            raise RuntimeError("AT-SPI fora do ar")

        def get_focused_app(self):
            return ""

    capture = LessonCapture(inspector=SemInspetor(), clipboard=FakeClipboard(None))
    result = capture.capture(strategy="atspi")
    assert result.ok is False
    assert "Falha ao inspecionar" in result.warnings[0]


def test_url_detectada_vai_para_o_resultado(monkeypatch):
    monkeypatch.setattr(LessonCapture, "_active_url", lambda self: "https://ava.senac.br/aula3")
    capture = LessonCapture(inspector=FakeInspector(arvore_de_aula()), clipboard=FakeClipboard(None))
    assert capture.capture().url == "https://ava.senac.br/aula3"


# --------------------------------------------------------------------------- #
# Persistência
# --------------------------------------------------------------------------- #


def test_save_escreve_markdown_com_metadados(tmp_path):
    capture = LessonCapture(
        inspector=FakeInspector(arvore_de_aula()),
        clipboard=FakeClipboard(None),
        documents_dir=str(tmp_path),
    )
    result = capture.capture()
    path = capture.save(result)

    content = open(path, encoding="utf-8").read()
    assert path.endswith(".md")
    assert "# Contabilidade Gerencial" in content
    assert "- Origem: Firefox (atspi)" in content
    assert "contabilidade gerencial é o ramo" in content
    assert result.path == path


def test_save_nao_sobrescreve_arquivo_existente(tmp_path):
    capture = LessonCapture(
        inspector=FakeInspector(arvore_de_aula()),
        clipboard=FakeClipboard(None),
        documents_dir=str(tmp_path),
    )
    first = capture.capture()
    capture.save(first)
    second = capture.capture()
    second_path = capture.save(second)

    assert second_path != first.path
    assert os.path.basename(second_path).endswith("-2.md")


def test_render_markdown_inclui_avisos_e_url():
    result = CaptureResult(
        ok=True,
        strategy="clipboard",
        title="Aula 1",
        text="corpo",
        app="Chrome",
        url="https://x/y",
        warnings=["captura rala"],
        captured_at="2026-09-15 02:40",
    )
    md = render_markdown(result)
    assert "- URL: https://x/y" in md
    assert "- Aviso: captura rala" in md
    assert md.endswith("corpo\n")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


class StubCapture(LessonCapture):
    """Captura de roteiro fixo para testar a fiação do comando."""

    def _capture_atspi(self, app, url):
        return CaptureResult(
            ok=True,
            strategy="atspi",
            title="Aula 3",
            text="conteúdo " * 300,
            app="Firefox",
            word_count=300,
            captured_at="2026-09-15 02:40",
        )

    def _capture_clipboard(self, app, url):
        return CaptureResult(ok=False, app=app)


class StubVazio(LessonCapture):
    def _capture_atspi(self, app, url):
        return CaptureResult(ok=False, app=app, warnings=["nada na tela"])

    def _capture_clipboard(self, app, url):
        return CaptureResult(ok=False, app=app)


def test_cli_study_capture_salva_e_mostra_caminho(monkeypatch, capsys, tmp_path):
    module = importlib.import_module("zorin_copilot.core.study_capture")
    monkeypatch.setattr(module, "LessonCapture", StubCapture)

    code = cli.main(["study", "capture", "--out", str(tmp_path / "aula3.md")])
    out = capsys.readouterr().out

    assert code == 0
    assert "300 palavras" in out
    assert "Salvo em:" in out
    assert (tmp_path / "aula3.md").exists()
    assert "rag index" in out


def test_cli_study_capture_sem_conteudo_sai_com_um(monkeypatch, capsys):
    module = importlib.import_module("zorin_copilot.core.study_capture")
    monkeypatch.setattr(module, "LessonCapture", StubVazio)

    code = cli.main(["study", "capture", "--no-save"])
    out = capsys.readouterr().out

    assert code == 1
    assert "Dica" in out


def test_cli_study_capture_json(monkeypatch, capsys):
    import json

    module = importlib.import_module("zorin_copilot.core.study_capture")
    monkeypatch.setattr(module, "LessonCapture", StubCapture)

    code = cli.main(["study", "capture", "--json", "--no-save"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["strategy"] == "atspi"
    assert payload["word_count"] == 300


def test_parser_do_study_capture():
    args = cli.build_parser().parse_args(
        ["study", "capture", "--app", "Firefox", "--strategy", "clipboard", "--min-words", "50"]
    )
    assert args.command == "study"
    assert args.study_action == "capture"
    assert args.app == "Firefox"
    assert args.strategy == "clipboard"
    assert args.min_words == 50


# --------------------------------------------------------------------------- #
# OCR: terceira via, para quando o player não expõe a árvore
# --------------------------------------------------------------------------- #


class FakeVisualElement:
    """Elemento com a cara do `ui_grounding.VisualElement`."""

    def __init__(self, text: str, x: int, y: int, width: int = 200, height: int = 20, is_phrase: bool = True):
        self.text = text
        self.bbox = (x, y, width, height)
        self.x = x + width // 2
        self.y = y + height // 2
        self.confidence = 92.0
        self.is_phrase = is_phrase


class FakeOCR:
    """Serviço de OCR dublê: devolve o que mandarmos (ou nada)."""

    def __init__(self, elements=None) -> None:
        self.elements = elements if elements is not None else []
        self.chamadas = 0

    def scan_screen(self, fence=None):
        self.chamadas += 1
        return list(self.elements)


def elementos_de_tela() -> list[FakeVisualElement]:
    """Duas linhas de aula + palavras soltas, fora de ordem de leitura."""
    return [
        FakeVisualElement("Custeio variável", 40, 200, 300, 20),  # linha 2 (topo)
        FakeVisualElement("Contabilidade Gerencial", 40, 100, 400, 24),  # linha 1
        FakeVisualElement("Contabilidade", 40, 100, 120, 24, is_phrase=False),  # dentro da frase
        FakeVisualElement("Gerencial", 170, 100, 100, 24, is_phrase=False),  # dentro da frase
        FakeVisualElement("Margem de contribuição", 40, 300, 320, 20),
        FakeVisualElement("2024", 600, 300, 60, 20, is_phrase=False),  # sozinha, mas só número
    ]


def test_ocr_lines_descarta_palavra_contida_em_frase():
    linhas = ocr_lines(elementos_de_tela())
    # "Contabilidade" e "Gerencial" já estão na frase; não podem reaparecer.
    assert linhas == ["Contabilidade Gerencial", "Custeio variável", "Margem de contribuição"]


def test_ocr_lines_ordena_por_posicao_de_leitura():
    fora_de_ordem = [
        FakeVisualElement("terceira", 40, 300),
        FakeVisualElement("primeira", 40, 100),
        FakeVisualElement("segunda", 40, 200),
    ]
    assert ocr_lines(fora_de_ordem) == ["primeira", "segunda", "terceira"]


def test_estrategia_ocr_captura_texto_da_tela():
    capture = LessonCapture(
        inspector=None,
        clipboard=FakeClipboard(""),
        ocr=FakeOCR(elementos_de_tela()),
        min_words=3,
    )
    result = capture.capture("Firefox", strategy="ocr")

    assert result.ok is True
    assert result.strategy == "ocr"
    assert result.word_count == 7
    assert "Contabilidade Gerencial" in result.text


def test_ocr_sem_tesseract_avisa_em_vez_de_quebrar():
    capture = LessonCapture(inspector=None, clipboard=FakeClipboard(""), ocr=FakeOCR([]))
    result = capture.capture("Firefox", strategy="ocr")

    assert result.ok is False
    assert any("tesseract" in aviso for aviso in result.warnings)


def test_auto_so_chama_ocr_quando_as_outras_ficam_ralas():
    arvore = FakeNode("application", children=[FakeNode("paragraph", text="Só chrome da janela.")])
    ocr = FakeOCR(elementos_de_tela())
    capture = LessonCapture(
        inspector=FakeInspector(arvore),
        clipboard=FakeClipboard(""),
        ocr=ocr,
        min_words=120,
    )
    result = capture.capture("Firefox")

    assert ocr.chamadas == 1  # AT-SPI devolveu pouco -> OCR entra
    assert result.strategy == "ocr"  # e é o melhor resultado
    assert result.word_count == 7


def test_auto_nao_gasta_ocr_quando_a_arvore_ja_basta():
    ocr = FakeOCR(elementos_de_tela())
    capture = LessonCapture(
        inspector=FakeInspector(arvore_de_aula()),
        clipboard=FakeClipboard(""),
        ocr=ocr,
        min_words=5,
    )
    result = capture.capture("Firefox")

    assert ocr.chamadas == 0  # árvore resolveu: nada de OCR
    assert result.strategy == "atspi"


def test_cli_aceita_strategy_ocr():
    args = cli.build_parser().parse_args(["study", "capture", "--strategy", "ocr"])
    assert args.strategy == "ocr"


# --------------------------------------------------------------------------- #
# A2: organização por disciplina
# --------------------------------------------------------------------------- #


def test_captura_com_disciplina_vai_para_subpasta(tmp_path):
    capture = LessonCapture(
        inspector=FakeInspector(arvore_de_aula()),
        clipboard=FakeClipboard(""),
        ocr=FakeOCR([]),
        min_words=5,
        documents_dir=str(tmp_path),
    )
    result = capture.capture("Firefox", discipline="Contabilidade Gerencial")
    path = capture.save(result)

    assert result.discipline == "Contabilidade Gerencial"
    assert os.path.basename(os.path.dirname(path)) == "contabilidade-gerencial"
    assert os.path.exists(path)


def test_sem_disciplina_fica_na_raiz(tmp_path):
    capture = LessonCapture(
        inspector=FakeInspector(arvore_de_aula()),
        clipboard=FakeClipboard(""),
        documents_dir=str(tmp_path),
    )
    result = capture.capture("Firefox")
    path = capture.save(result)

    assert result.discipline == ""
    assert os.path.dirname(path) == str(tmp_path)


def test_metadados_registram_a_disciplina():
    result = CaptureResult(ok=True, title="Aula 3", text="corpo", word_count=1, discipline="Marketing")
    markdown = render_markdown(result)

    assert "- Disciplina: Marketing" in markdown
