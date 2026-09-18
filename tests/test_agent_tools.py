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

    from zorin_copilot.core import web_search, academic_hub
    monkeypatch.setattr(web_search, "WebSearchClient", FakeClient)
    monkeypatch.setattr(academic_hub, "WebSearchClient", FakeClient)

    registry = make_registry()
    res = registry.call("academic_search", {"query": "gestao comercial varejo", "source": "scielo", "scope": "web"})
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


# --------------------------------------------------------------------------- #
# capture_lesson: ler a tela em vez de baixar o shell da SPA
# --------------------------------------------------------------------------- #


class FakeLessonCapture:
    """Captura dublê: devolve um resultado pronto e anota o que foi pedido."""

    criados: list["FakeLessonCapture"] = []

    def __init__(self, min_words: int = 120, documents_dir: str | None = None, **kwargs) -> None:
        self.min_words = min_words
        self.chamadas: list[tuple[str | None, str]] = []
        self.saves: list[dict] = []
        self.grupo = kwargs.pop("grupo", None)
        FakeLessonCapture.criados.append(self)

    def capture(self, app_name=None, strategy="auto"):
        self.chamadas.append((app_name, strategy))
        texto = self.texto()
        return capture_result(
            ok=bool(texto),
            strategy="atspi",
            text=texto,
            app=app_name or "Firefox",
            word_count=len(texto.split()),
        )

    def texto(self) -> str:
        return getattr(self, "_texto", "A contabilidade gerencial apoia decisões de preço. " * 6)

    def save(self, result, filename=None, directory=None):
        self.saves.append({"filename": filename, "directory": directory})
        return f"{directory or '/tmp/estudos'}/{filename or 'aula.md'}"


def capture_result(**kwargs) -> object:
    from zorin_copilot.core.study_capture import CaptureResult

    return CaptureResult(**kwargs)


@pytest.fixture
def captura_duble(monkeypatch):
    FakeLessonCapture.criados.clear()
    monkeypatch.setattr(
        "zorin_copilot.core.study_capture.LessonCapture", FakeLessonCapture
    )
    return FakeLessonCapture


def test_capture_lesson_esta_no_registro():
    registry = ToolRegistry()
    assert "capture_lesson" in registry.names()
    spec = registry.spec("capture_lesson")
    assert "read_web_page" in spec.description  # a dica que evita o shell da SPA


def test_capture_lesson_salva_e_devolve_caminho(captura_duble):
    registry = ToolRegistry()
    result = registry.call("capture_lesson", {"app": "Firefox", "directory": "/tmp/estudos"})

    assert result["ok"] is True
    assert result["path"].endswith("aula.md")
    assert result["word_count"] > 0
    assert captura_duble.criados[0].chamadas == [("Firefox", "auto")]


def test_capture_lesson_dry_run_nao_toca_na_tela(captura_duble):
    registry = ToolRegistry().for_dry_run()
    result = registry.call("capture_lesson", {"app": "Firefox"})

    assert result["dry_run"] is True
    assert captura_duble.criados == []  # nem instanciou: é mutação


def test_capture_lesson_sem_conteudo_devolve_erro(captura_duble, monkeypatch):
    monkeypatch.setattr(FakeLessonCapture, "texto", lambda self: "")
    result = ToolRegistry().call("capture_lesson", {})

    assert result["ok"] is False
    assert "error" in result


def test_capture_lesson_pode_nao_salvar(captura_duble):
    result = ToolRegistry().call("capture_lesson", {"save": False})

    assert result["ok"] is True
    assert result["saved"] is False
    assert "text" in result
    assert captura_duble.criados[0].saves == []


def test_capture_lesson_nao_pede_confirmacao():
    registry = ToolRegistry()
    level, _motivo = registry.classify("capture_lesson", {})
    assert level == RiskLevel.SAFE
    assert registry.requires_approval("capture_lesson", {}) is False


# --------------------------------------------------------------------------- #
# Salvaguardas de Timeout e Ferramentas Git
# --------------------------------------------------------------------------- #


def test_tool_timeout_individual_retorna_payload_com_sugestao():
    import time
    from zorin_copilot.ai.agent_tools import ToolSpec

    def slow_handler(args):
        time.sleep(0.3)
        return {"ok": True, "done": True}

    registry = ToolRegistry(default_timeout=0.1)
    registry.register(
        ToolSpec(
            name="slow_tool",
            description="Ferramenta lenta de teste",
            parameters={},
            handler=slow_handler,
            timeout=0.1,
        )
    )

    res = registry.call("slow_tool", {})
    assert res["ok"] is False
    assert res["timeout"] is True
    assert "excedeu o tempo limite" in res["error"]
    assert "suggestion" in res
    assert "Não invente nem presuma" in res["suggestion"]
    registry.close()


def test_git_log_executa_e_extrai_commits():
    registry = ToolRegistry()
    res = registry.call("git_log", {"max_count": 5})
    assert res["ok"] is True
    assert "commits" in res
    assert isinstance(res["commits"], list)
    assert len(res["commits"]) > 0
    assert "summary" in res
    registry.close()


def test_git_log_caminho_invalido_retorna_sugestao():
    registry = ToolRegistry()
    res = registry.call("git_log", {"path": "/caminho/completamente/inexistente/xyz"})
    assert res["ok"] is False
    assert "não encontrado" in res["error"]
    assert "suggestion" in res
    registry.close()


def test_git_status_executa_em_repositorio():
    registry = ToolRegistry()
    res = registry.call("git_status", {})
    assert res["ok"] is True
    assert "status" in res
    assert "path" in res
    registry.close()


def test_git_tools_sao_classificadas_como_safe():
    registry = ToolRegistry()
    assert registry.classify("git_log", {})[0] == RiskLevel.SAFE
    assert registry.classify("git_status", {})[0] == RiskLevel.SAFE
    assert registry.requires_approval("git_log", {}) is False
    assert registry.requires_approval("git_status", {}) is False
    registry.close()


def test_list_open_windows_tool(monkeypatch):
    registry = ToolRegistry()
    from zorin_copilot.core.window_manager import WindowInfo, WindowManager

    fake_windows = [
        WindowInfo(id="0x1", app="chrome", title="Browser", x=0, y=0, width=1920, height=1080, is_active=True),
        WindowInfo(id="0x2", app="kitty", title="Shell", x=100, y=100, width=800, height=600, is_active=False),
    ]
    monkeypatch.setattr(WindowManager, "list_windows", lambda **kwargs: fake_windows)

    res = registry.call("list_open_windows", {})
    assert res["ok"] is True
    assert res["count"] == 2
    assert res["windows"][0]["app"] == "chrome"
    registry.close()


def test_focus_window_tool(monkeypatch):
    from zorin_copilot.core.fence import ScreenFenceManager
    from zorin_copilot.core.window_manager import WindowInfo, WindowManager

    fence = ScreenFenceManager()
    registry = ToolRegistry(fence=fence)

    target_win = WindowInfo(id="0x123", app="firefox", title="Browser", x=50, y=50, width=1200, height=900)
    focused = []

    monkeypatch.setattr(WindowManager, "find_window", lambda q: target_win if "firefox" in q else None)
    monkeypatch.setattr(WindowManager, "focus_window", lambda target: focused.append(target) or True)

    res = registry.call("focus_window", {"query": "firefox"})
    assert res["ok"] is True
    assert "focada com sucesso" in res["message"]
    assert "0x123" in focused
    assert fence.get_target_window() == target_win

    # Teste query vazia
    res_err = registry.call("focus_window", {"query": ""})
    assert res_err["ok"] is False

    registry.close()


def test_scroll_page_tool(monkeypatch):
    from zorin_copilot.shell.input_driver import VirtualInputDriver
    driver = VirtualInputDriver(simulation=True)
    registry = ToolRegistry(input_driver=driver)

    res = registry.call("scroll_page", {"direction": "down", "amount": 4})
    assert res["ok"] is True
    assert res["direction"] == "down"
    assert res["amount"] == 4

    res_up = registry.call("scroll_page", {"direction": "up", "amount": 2})
    assert res_up["ok"] is True
    assert res_up["direction"] == "up"
    assert res_up["amount"] == 2

    registry.close()


def test_click_and_type_tool():
    driver = FakeDriver()
    fence = FakeFence()
    registry = ToolRegistry(input_driver=driver, fence=fence)

    # 1. Clique e digitação válidos dentro da cerca
    res = registry.call("click_and_type", {"x": 200, "y": 150, "text": "pesquisa rápida", "press_enter": True})
    assert res["ok"] is True
    assert driver.clicks == [(200, 150)]
    assert "pesquisa rápida" in driver.texts

    # 2. Com clear_first
    driver.clicks.clear()
    driver.hotkeys.clear()
    res_clear = registry.call(
        "click_and_type",
        {"x": 100, "y": 80, "text": "novo termo", "clear_first": True, "press_enter": False},
    )
    assert res_clear["ok"] is True
    assert driver.clicks == [(100, 80)]
    assert ["ctrl", "a"] in driver.hotkeys
    assert ["backspace"] in driver.hotkeys

    # 3. Fora da cerca espacial -> bloqueio seguro
    driver.clicks.clear()
    res_blocked = registry.call("click_and_type", {"x": 800, "y": 900, "text": "inseguro"})
    assert res_blocked["ok"] is False
    assert res_blocked["blocked_by"] == "fence"
    assert driver.clicks == []

    registry.close()


def test_vscode_workspace_patch_code_tool(tmp_path, monkeypatch):
    from zorin_copilot.core.vscode import VSCodeManager

    demo_file = tmp_path / "app.py"
    demo_file.write_text("def hello():\n    return 'old'\n", encoding="utf-8")

    registry = ToolRegistry()
    monkeypatch.setattr(VSCodeManager, "open_file", lambda *a, **kw: {"success": True})

    # 1. Execução de patch_code
    res = registry.call(
        "vscode_workspace",
        {
            "action": "patch_code",
            "file_path": str(demo_file),
            "target_code": "return 'old'",
            "replacement_code": "return 'updated'",
        },
    )
    assert res["ok"] is True
    assert res["success"] is True
    assert "return 'updated'" in demo_file.read_text(encoding="utf-8")

    # 2. Dry-run de patch_code não modifica arquivo
    dry_registry = registry.for_dry_run()
    dry_res = dry_registry.call(
        "vscode_workspace",
        {
            "action": "patch_code",
            "file_path": str(demo_file),
            "target_code": "return 'updated'",
            "replacement_code": "return 'dry'",
        },
    )
    assert dry_res["ok"] is True
    assert dry_res.get("dry_run") is True
    assert "return 'updated'" in demo_file.read_text(encoding="utf-8")

    registry.close()


def test_tool_registry_unified_tools():
    registry = ToolRegistry()

    # 1. run_command seguro
    res_cmd = registry.call("run_command", {"command": "echo 'hello unified'"})
    assert res_cmd["ok"] is True
    assert "hello unified" in res_cmd["output"]

    # 2. run_command com dry_run
    dry_reg = registry.for_dry_run()
    res_dry = dry_reg.call("run_command", {"command": "echo 'test'"})
    assert res_dry["ok"] is True
    assert res_dry.get("dry_run") is True

    # 3. git_diff
    res_diff = registry.call("git_diff", {"path": "."})
    assert res_diff["ok"] is True
    assert "diff" in res_diff

    # 4. window_management
    res_win = registry.call("window_management", {"action": "tile_right"})
    assert res_win["ok"] is True

    # 5. system_control
    res_sys = registry.call("system_control", {"action": "dark_mode"})
    assert "ok" in res_sys

    # 6. memory_remember
    res_mem = registry.call("memory_remember", {"fact": "usuário prefere tema escuro"})
    assert res_mem["ok"] is True

    # 7. contact_save & contact_lookup
    res_save = registry.call("contact_save", {"name": "Carlos Dev", "email": "carlos@zorin.org"})
    assert res_save["ok"] is True
    res_find = registry.call("contact_lookup", {"query": "Carlos"})
    assert res_find["ok"] is True
    assert len(res_find["contacts"]) >= 1

    registry.close()





