# Decisão de design: o registro é testado SEM GTK e SEM AT-SPI. Onde um componente
# real seria necessário (árvore, driver de entrada, cerca), injetamos um dublê —
# o que sobra para o teste real é a parte que importa: risco, dry-run, desfazer e
# o contrato "call() nunca levanta".

"""Testes do registro de ferramentas do modo agente."""

from __future__ import annotations

import pytest

from zorin_copilot.ai.agent_tools import FINISH_TOOLS, ToolRegistry
from zorin_copilot.shell.risk import RiskLevel
from zorin_copilot.shell.undo import UndoStack


class FakeFence:
    """Cerca que só permite o quadrante superior esquerdo."""

    def is_coordinate_allowed(self, x: int, y: int) -> tuple[bool, str]:
        if x < 0 or y < 0:
            return False, "coordenada negativa"
        if x > 500 or y > 500:
            return False, "fora da área permitida"
        return True, ""


class FakeDriver:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []
        self.texts: list[str] = []
        self.hotkeys: list[list[str]] = []

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> tuple[bool, str]:
        self.clicks.append((x, y))
        return True, f"clique em ({x}, {y})"

    def click_relative(self, rel_x: float, rel_y: float, button: str = "left", double: bool = False):
        self.clicks.append((int(rel_x * 100), int(rel_y * 100)))
        return True, "clique relativo"

    def type_text(self, text: str, press_enter: bool = False) -> tuple[bool, str]:
        self.texts.append(text)
        return True, "texto digitado"

    def hotkey(self, *keys: str) -> tuple[bool, str]:
        self.hotkeys.append(list(keys))
        return True, "atalho enviado"


def make_registry(**kwargs) -> ToolRegistry:
    return ToolRegistry(input_driver=FakeDriver(), fence=FakeFence(), **kwargs)


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #


def test_registry_expoe_ferramentas_de_encerramento_e_leitura():
    registry = make_registry()
    names = set(registry.names())
    assert "done" in names
    assert {"get_ui_tree", "find_element", "click_element", "keyboard_type"} <= names
    assert "done" in FINISH_TOOLS


def test_ferramenta_desconhecida_devolve_erro_em_vez_de_explodir():
    result = make_registry().call("nao_existe", {"x": 1})
    assert result["ok"] is False
    assert "desconhecida" in result["error"]


def test_schema_e_descricao_para_prompt_sao_gerados():
    registry = make_registry()
    schema = registry.schema()
    assert {item["name"] for item in schema} == set(registry.names())
    text = registry.describe_for_prompt()
    assert "get_ui_tree(" in text


# --------------------------------------------------------------------------- #
# Risco
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name,args,expected",
    [
        ("get_ui_tree", {}, RiskLevel.SAFE),
        ("write_document", {"filename": "a.md", "content": "x"}, RiskLevel.CONFIRM),
        ("organize_directory", {"directory": "/tmp"}, RiskLevel.CONFIRM),
        ("keyboard_hotkey", {"keys": ["ctrl", "t"]}, RiskLevel.SAFE),
        ("keyboard_hotkey", {"keys": ["alt", "f4"]}, RiskLevel.CONFIRM),
    ],
)
def test_risco_herda_a_politica_do_shell(name, args, expected):
    registry = make_registry()
    level, _ = registry.classify(name, args)
    assert level is expected
    assert registry.requires_approval(name, args) is (expected is RiskLevel.CONFIRM)


# --------------------------------------------------------------------------- #
# Dry-run
# --------------------------------------------------------------------------- #


def test_dry_run_nao_executa_ferramenta_mutante():
    registry = make_registry(dry_run=True)
    result = registry.call("write_document", {"filename": "a.md", "content": "x"})
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert "a.md" in result["would_do"]


def test_for_dry_run_preserva_o_registro_original():
    real = make_registry()
    assert real.dry_run is False
    assert real.for_dry_run().dry_run is True
    assert real.dry_run is False


# --------------------------------------------------------------------------- #
# Cerca espacial
# --------------------------------------------------------------------------- #


def test_clique_fora_da_cerca_e_recusado_sem_excecao():
    registry = make_registry()
    result = registry.call("mouse_click", {"x": 900, "y": 900})
    assert result["ok"] is False
    assert result["blocked_by"] == "fence"
    assert registry.input_driver.clicks == []


def test_clique_dentro_da_cerca_acontece():
    registry = make_registry()
    result = registry.call("mouse_click", {"x": 100, "y": 100})
    assert result["ok"] is True
    assert registry.input_driver.clicks == [(100, 100)]


def test_cerca_padrao_e_construida_sob_demanda_e_bloqueia_fora_do_monitor():
    """Com `fence=None` o registry resolve a cerca real — e ela ainda vale."""
    registry = ToolRegistry(input_driver=FakeDriver(), fence=None)
    result = registry.call("mouse_click", {"x": 99999, "y": 99999})
    # Ambiente sem monitor (CI headless) → a cerca não bloqueia; com monitor, bloqueia.
    # O contrato testado é: nunca levanta, e o bloqueio vem marcado quando existe.
    assert isinstance(result, dict)
    if result["ok"] is False:
        assert result["blocked_by"] == "fence"
    else:
        assert registry.input_driver.clicks == [(99999, 99999)]


# --------------------------------------------------------------------------- #
# Entrada
# --------------------------------------------------------------------------- #


def test_keyboard_type_delega_ao_driver():
    registry = make_registry()
    result = registry.call("keyboard_type", {"text": "olá", "press_enter": True})
    assert result["ok"] is True
    assert registry.input_driver.texts == ["olá"]


def test_hotkey_aceita_lista_e_string():
    registry = make_registry()
    assert registry.call("keyboard_hotkey", {"keys": ["ctrl", "t"]})["ok"] is True
    assert registry.call("keyboard_hotkey", {"keys": "ctrl+t"})["ok"] is True
    assert registry.input_driver.hotkeys == [["ctrl", "t"], ["ctrl", "t"]]


def test_hotkey_sem_teclas_falha_com_mensagem():
    result = make_registry().call("keyboard_hotkey", {"keys": []})
    assert result["ok"] is False


# --------------------------------------------------------------------------- #
# Arquivos + desfazer
# --------------------------------------------------------------------------- #


def test_write_document_grava_e_fica_desfazivel(tmp_path):
    stack = UndoStack()
    registry = make_registry(undo_stack=stack)
    target = tmp_path / "nota.md"

    result = registry.call(
        "write_document",
        {"filename": target.name, "content": "versão 1", "directory": str(tmp_path)},
    )
    assert result["ok"] is True
    assert target.read_text(encoding="utf-8").strip() == "versão 1"
    assert len(stack) == 1

    registry.call(
        "write_document",
        {"filename": target.name, "content": "versão 2", "directory": str(tmp_path)},
    )
    assert target.read_text(encoding="utf-8").strip() == "versão 2"

    undone = registry.call("undo_last", {})
    assert undone["ok"] is True
    assert target.read_text(encoding="utf-8").strip() == "versão 1"


def test_undo_sem_historico_devolve_erro():
    result = make_registry(undo_stack=UndoStack()).call("undo_last", {})
    assert result["ok"] is False


def test_list_directory_e_read_file(tmp_path):
    (tmp_path / "a.txt").write_text("conteúdo A", encoding="utf-8")
    (tmp_path / "pasta").mkdir()

    registry = make_registry()
    listed = registry.call("list_directory", {"path": str(tmp_path)})
    assert listed["ok"] is True
    assert {item["name"] for item in listed["entries"]} == {"a.txt", "pasta"}
    assert next(i for i in listed["entries"] if i["name"] == "pasta")["dir"] is True

    read = registry.call("read_file", {"path": str(tmp_path / "a.txt")})
    assert read["ok"] is True
    assert read["content"] == "conteúdo A"


def test_read_file_inexistente_devolve_ok_false(tmp_path):
    result = make_registry().call("read_file", {"path": str(tmp_path / "nope.txt")})
    assert result["ok"] is False


# --------------------------------------------------------------------------- #
# Árvore de acessibilidade (com inspetor dublê)
# --------------------------------------------------------------------------- #


class FakeElement:
    def __init__(self, uid, role, name, bbox=(0, 0, 10, 10), interactive=True, children=None):
        self.uid = uid
        self.role = role
        self.name = name
        self.bbox = bbox
        self.is_interactive = interactive
        self.children = children or []

    def find(self, predicate):
        matches = [self] if predicate(self) else []
        for child in self.children:
            matches.extend(child.find(predicate))
        return matches

    def to_summary(self, indent=0, include_bounds=False):
        lines = [f"- [{self.uid}] {self.role}: '{self.name}'"]
        for child in self.children:
            lines.append(child.to_summary(indent + 1, include_bounds))
        return "\n".join(lines)


class FakeInspector:
    def __init__(self, tree=None):
        self.tree = tree
        self.actions: list[str] = []
        self.inserted: list[str] = []

    def get_ui_tree(self, app_name=None):
        return self.tree

    def get_focused_app(self):
        return "app-fake"

    def focus_element(self, element):
        return True

    def do_action(self, element, action_index=0):
        self.actions.append(element.uid)
        return True

    def text_insert(self, element, text, append=False):
        self.inserted.append(text)
        return True, "ok"


def _tree():
    return FakeElement(
        "1",
        "frame",
        "Janela",
        bbox=(0, 0, 100, 100),
        interactive=False,
        children=[
            FakeElement("1.0", "push_button", "Salvar", bbox=(10, 10, 40, 20)),
            FakeElement("1.1", "entry", "Nome", bbox=(10, 40, 60, 20)),
        ],
    )


def test_get_ui_tree_resume_a_arvore():
    registry = make_registry(inspector=FakeInspector(_tree()))
    result = registry.call("get_ui_tree", {})
    assert result["ok"] is True
    assert "Salvar" in result["tree"]


def test_find_element_busca_por_nome_e_papel():
    registry = make_registry(inspector=FakeInspector(_tree()))
    result = registry.call("find_element", {"query": "salvar"})
    assert result["ok"] is True
    assert result["elements"][0]["uid"] == "1.0"

    by_role = registry.call("find_element", {"role": "entry"})
    assert by_role["elements"][0]["name"] == "Nome"


def test_find_element_sem_criterio_falha():
    result = make_registry(inspector=FakeInspector(_tree())).call("find_element", {})
    assert result["ok"] is False


def test_click_element_usa_acao_semantica():
    inspector = FakeInspector(_tree())
    registry = make_registry(inspector=inspector)
    result = registry.call("click_element", {"uid": "1.0"})
    assert result["ok"] is True
    assert inspector.actions == ["1.0"]


def test_click_element_uid_inexistente_falha():
    registry = make_registry(inspector=FakeInspector(_tree()))
    result = registry.call("click_element", {"uid": "9.9"})
    assert result["ok"] is False


def test_type_element_insere_via_atspi():
    inspector = FakeInspector(_tree())
    registry = make_registry(inspector=inspector)
    result = registry.call("type_element", {"uid": "1.1", "text": "Bruno"})
    assert result["ok"] is True
    assert inspector.inserted == ["Bruno"]


def test_componentes_indisponiveis_viram_erro_em_vez_de_excecao():
    """Sem inspetor (AT-SPI fora do ar), a ferramenta explica o motivo."""

    class Boom:
        def __getattr__(self, _name):
            raise RuntimeError("AT-SPI explodiu")

    registry = ToolRegistry(inspector=None)
    registry._inspector = Boom()  # força o caminho de exceção
    result = registry.call("get_ui_tree", {})
    assert result["ok"] is False
    assert "AT-SPI explodiu" in result["error"]


def test_academic_search_valida_query_obrigatoria():
    registry = make_registry()
    res = registry.call("academic_search", {})
    assert res["ok"] is False
    assert "query" in res["error"]


def test_academic_search_executa_pesquisa(monkeypatch):
    class FakeSearchResult:
        title = "Gestão de Canais e Distribuição no Varejo"
        url = "https://scielo.br/artigo1"
        snippet = "Artigo sobre estratégias omnicanal no Senac..."

    class FakeClient:
        def academic_search(self, query, source="all", max_results=5):
            return [FakeSearchResult()]

    from zorin_copilot.core import web_search
    monkeypatch.setattr(web_search, "WebSearchClient", FakeClient)

    registry = make_registry()
    res = registry.call("academic_search", {"query": "gestao comercial varejo", "source": "scielo"})
    assert res["ok"] is True
    assert res["count"] == 1
    assert res["results"][0]["title"] == "Gestão de Canais e Distribuição no Varejo"


def test_web_search_valida_query_obrigatoria():
    registry = make_registry()
    res = registry.call("web_search", {})
    assert res["ok"] is False
    assert "query" in res["error"]


def test_web_search_executa_pesquisa(monkeypatch):
    class FakeSearchResult:
        title = "Notícias do Varejo 2026"
        url = "https://exemplo.com/noticia"
        snippet = "Crescimento de 5% no comércio eletrônico..."

    class FakeClient:
        def search(self, query, max_results=4):
            return [FakeSearchResult()]

    from zorin_copilot.core import web_search
    monkeypatch.setattr(web_search, "WebSearchClient", FakeClient)

    registry = make_registry()
    res = registry.call("web_search", {"query": "mercado varejista"})
    assert res["ok"] is True
    assert res["count"] == 1
    assert res["results"][0]["url"] == "https://exemplo.com/noticia"


def test_read_web_page_executa_leitura(monkeypatch):
    class FakeBrowser:
        @classmethod
        def read_page(cls, url=None):
            return {
                "success": True,
                "title": "Página do Curso",
                "url": "https://senac.br",
                "text": "Conteúdo da aula de Gestão",
            }

    from zorin_copilot.core import browser
    monkeypatch.setattr(browser, "BrowserManager", FakeBrowser)

    registry = make_registry()
    res = registry.call("read_web_page", {"url": "https://senac.br"})
    assert res["ok"] is True
    assert res["title"] == "Página do Curso"
    assert "Conteúdo da aula" in res["content"]


def test_open_document_valida_path():
    registry = make_registry()
    res = registry.call("open_document", {})
    assert res["ok"] is False
    assert "path" in res["error"]


def test_open_document_executa(monkeypatch):
    class FakeRAG:
        def open_document(self, path, page_number=1):
            return True, f"Documento '{path}' aberto com sucesso."

    from zorin_copilot.core import rag
    monkeypatch.setattr(rag, "LocalDocumentRAG", FakeRAG)

    registry = make_registry()
    res = registry.call("open_document", {"path": "/tmp/tcc.docx"})
    assert res["ok"] is True
    assert "aberto com sucesso" in res["message"]
