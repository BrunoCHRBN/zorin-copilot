# Decisão de design: o registro de ferramentas é a ÚNICA fronteira entre o loop do
# agente (puro, headless) e o desktop real (AT-SPI, ydotool, Gio, filesystem).
# Três regras sustentam o arquivo:
#
#   1. Nenhuma exceção escapa de `call()` — o loop precisa de uma observação sempre,
#      mesmo quando o AT-SPI não existe ou o app não abriu.
#   2. Nenhum import de GTK/Gio/pyatspi em nível de módulo. Componentes pesados são
#      resolvidos sob demanda (ver `_component`), então `import agent_tools` funciona
#      num ambiente sem `gi` — é o que mantém os testes do loop rodando sem xvfb.
#   3. Nenhuma lista de risco duplicada. O que é perigoso decide `shell/risk.py`;
#      aqui só consultamos.
#
# Os nomes das ferramentas repetem os de `ai/live.py` (get_ui_tree, click_element,
# keyboard_type, keyboard_hotkey, write_document, organize_directory...) de propósito:
# a política de risco é indexada por nome, e a Fase 4 vai fazer o live client
# consumir este mesmo registro em vez de manter o dispatch duplicado.

"""Registro de ferramentas usado pelo modo agente (uso autônomo supervisionado)."""

from __future__ import annotations

import copy
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..shell.risk import RiskLevel, RiskPolicy, is_blocked_app

logger = logging.getLogger(__name__)

#: Ferramentas que encerram o loop com sucesso. O `AgentLoop` as intercepta antes
#: de chegar ao registro — servem como "o objetivo foi cumprido, eis a resposta".
FINISH_TOOLS = {"done", "finish"}

#: A árvore de acessibilidade de um app grande passa de 100 kB. Enviar tudo ao modelo
#: local derruba a qualidade e estoura o contexto; cortamos com aviso explícito de
#: truncamento para o modelo saber que precisa refinar a busca.
MAX_TREE_CHARS = 6000
MAX_LIST_ENTRIES = 50
MAX_READ_CHARS = 4000

#: Sentinel que marca "já tentamos construir e não deu" — evita repetir o import
#: falho a cada passo do loop.
_UNAVAILABLE = object()


# --------------------------------------------------------------------------- #
# Especificação
# --------------------------------------------------------------------------- #


@dataclass
class ToolSpec:
    """Uma ferramenta exposta ao modelo."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    #: Ações que mexem no estado do desktop. Em dry-run viram observação sintética.
    mutating: bool = False


def _param(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """Monta o schema no formato já aceito pelos provedores (estilo Gemini)."""
    return {"type": "OBJECT", "properties": properties, "required": required or []}


def _str(desc: str) -> dict[str, str]:
    return {"type": "STRING", "description": desc}


def _num(desc: str) -> dict[str, str]:
    return {"type": "NUMBER", "description": desc}


def _bool(desc: str) -> dict[str, str]:
    return {"type": "BOOLEAN", "description": desc}


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #


class ToolRegistry:
    """Conjunto nomeado de ferramentas com classificação de risco embutida.

    Todos os componentes de desktop são opcionais e injetáveis: passar `None`
    significa "resolva sob demanda quando algum tool precisar". É o que permite
    testar o loop com um registro de mentira e, ao mesmo tempo, usar o registro
    real dentro da UI sem reescrever nada.
    """

    def __init__(
        self,
        *,
        inspector: Any | None = None,
        input_driver: Any | None = None,
        undo_stack: Any | None = None,
        fence: Any | None = None,
        policy: RiskPolicy | None = None,
        dry_run: bool = False,
        max_tree_chars: int = MAX_TREE_CHARS,
        mcp_manager: Any | None = None,
    ) -> None:
        self._inspector = inspector
        self._input_driver = input_driver
        self._undo_stack = undo_stack
        self._fence = fence
        self.policy = policy or RiskPolicy()
        self.dry_run = dry_run
        self.max_tree_chars = max_tree_chars
        self.mcp_manager = mcp_manager
        self._specs: dict[str, ToolSpec] = {}
        self._register_builtins()
        if self.mcp_manager:
            self.load_mcp_tools(self.mcp_manager)

    def load_mcp_tools(self, manager: Any) -> int:
        """Carrega ferramentas de servidores MCP para o registro."""
        try:
            from ..mcp.adapter import register_mcp_tools_in_registry
            return register_mcp_tools_in_registry(self, manager)
        except Exception as exc:
            logger.debug("Falha ao registrar ferramentas MCP no registro: %s", exc)
            return 0

    # -- ciclo de vida ----------------------------------------------------- #

    def register(self, spec: ToolSpec) -> None:
        self._specs[spec.name] = spec

    def names(self) -> list[str]:
        return list(self._specs)

    def spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def mutate(self, spec: ToolSpec) -> None:
        """Atalho para registrar declarando mutação (atalho legível nos builtins)."""
        spec.mutating = True
        self.register(spec)

    def for_dry_run(self) -> "ToolRegistry":
        """Cópia que descreve o que faria sem tocar no desktop.

        `copy.copy` é intencional: compartilha handlers e componentes (nada é
        executado de todo jeito) e só troca a flag.
        """
        clone = copy.copy(self)
        clone.dry_run = True
        return clone

    # -- resolução de componentes (lazy, sem gi em nível de módulo) --------- #

    def _component(self, attr: str, factory: Callable[[], Any]) -> Any:
        current = getattr(self, attr)
        if current is None:
            try:
                setattr(self, attr, factory())
            except Exception as exc:  # ambiente sem AT-SPI/ydotool é comum em CI
                logger.debug("Componente %s indisponível: %s", attr, exc)
                setattr(self, attr, _UNAVAILABLE)
        value = getattr(self, attr)
        return None if value is _UNAVAILABLE else value

    @property
    def inspector(self) -> Any | None:
        def build() -> Any:
            from ..core.a11y import DesktopInspector

            return DesktopInspector()

        return self._component("_inspector", build)

    @property
    def input_driver(self) -> Any | None:
        def build() -> Any:
            from ..shell.input_driver import VirtualInputDriver

            return VirtualInputDriver()

        return self._component("_input_driver", build)

    @property
    def undo_stack(self) -> Any | None:
        def build() -> Any:
            from ..shell.undo import UndoStack

            return UndoStack()

        return self._component("_undo_stack", build)

    @property
    def fence(self) -> Any | None:
        """Cerca digital. Sem cerca configurada, coordenadas passam (ambiente headless)."""

        def build() -> Any:
            from ..core.fence import ScreenFenceManager

            return ScreenFenceManager()

        return self._component("_fence", build)

    # -- descrição para o modelo ------------------------------------------- #

    def schema(self) -> list[dict[str, Any]]:
        """Lista de ferramentas no formato esperado pelos provedores."""
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            }
            for spec in self._specs.values()
        ]

    def describe_for_prompt(self) -> str:
        """Descrição compacta para modelos locais sem suporte a function calling.

        Qwen 2.5 7B lida melhor com uma lista textual do que com JSON Schema
        embutido no prompt — e o parser do planner tolera as duas coisas.
        """
        lines: list[str] = []
        for spec in self._specs.values():
            props = spec.parameters.get("properties", {}) or {}
            required = spec.parameters.get("required", []) or []
            args_desc = ", ".join(
                f"{nome}{'*' if nome in required else ''}" for nome in props
            )
            lines.append(f"- {spec.name}({args_desc}): {spec.description}")
        return "\n".join(lines)

    # -- risco -------------------------------------------------------------- #

    def classify(self, name: str, args: dict[str, Any] | None = None) -> tuple[RiskLevel, str]:
        """Risco de uma chamada. Sempre delega a `shell/risk.py`."""
        return self.policy.classify(name, args)

    def requires_approval(self, name: str, args: dict[str, Any] | None = None) -> bool:
        level, _ = self.classify(name, args)
        return level == RiskLevel.CONFIRM

    # -- execução ----------------------------------------------------------- #

    def call(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Executa uma ferramenta e devolve SEMPRE um dicionário serializável.

        Nunca levanta: falha de componente, ferramenta inexistente e argumento
        inválido voltam como `{"ok": False, "error": ...}` para o loop decidir.
        """
        args = dict(args or {})
        spec = self._specs.get(name)

        if spec is None:
            known = ", ".join(sorted(self._specs))
            return {"ok": False, "error": f"Ferramenta desconhecida: '{name}'. Disponíveis: {known}"}

        if self.dry_run and spec.mutating:
            return {
                "ok": True,
                "dry_run": True,
                "tool": name,
                "would_do": _summarize_args(args),
            }

        started = time.monotonic()
        try:
            result = spec.handler(args)
        except Exception as exc:  # nenhum tool pode derrubar o loop
            logger.exception("Ferramenta '%s' falhou", name)
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        result.setdefault("ok", True)
        result["tool"] = name
        result["elapsed"] = round(time.monotonic() - started, 3)
        return result

    # -- cerca -------------------------------------------------------------- #

    def check_coordinate(self, x: int, y: int) -> tuple[bool, str]:
        """Consulta a cerca espacial. Sem cerca ativa, libera (headless/CI)."""
        fence = self.fence
        if fence is None:
            return True, ""
        try:
            allowed, reason = fence.is_coordinate_allowed(int(x), int(y))
        except Exception as exc:
            logger.debug("Falha ao consultar a cerca: %s", exc)
            return True, ""
        return bool(allowed), reason or ""

    # ------------------------------------------------------------------ #
    # Ferramentas
    # ------------------------------------------------------------------ #

    def _register_builtins(self) -> None:
        self.register(
            ToolSpec(
                name="get_ui_tree",
                description=(
                    "Lê a árvore de acessibilidade do aplicativo em foco (ou de `app`). "
                    "É a forma de ENXERGAR a tela sem screenshot: traz papel, nome, uid "
                    "e geometria de cada controle."
                ),
                parameters=_param(
                    {
                        "app": _str("Nome do aplicativo alvo. Omita para usar o app em foco."),
                        "max_depth": _num("Profundidade máxima da árvore (padrão 4)."),
                        "include_bounds": _bool("Incluir geometria (x, y, largura, altura) de cada elemento."),
                    }
                ),
                handler=self._tool_get_ui_tree,
            )
        )

        self.register(
            ToolSpec(
                name="find_element",
                description=(
                    "Procura controles na árvore de acessibilidade por nome e/ou papel. "
                    "Devolve uid, papel, nome e geometria. Prefira isto a clicar por coordenada."
                ),
                parameters=_param(
                    {
                        "query": _str("Trecho do nome/rótulo do controle (busca parcial, sem case)."),
                        "role": _str("Papel AT-SPI desejado (ex.: push_button, entry, link)."),
                        "app": _str("Nome do aplicativo alvo. Omita para usar o app em foco."),
                        "limit": _num("Máximo de resultados (padrão 10)."),
                    },
                    required=[],
                ),
                handler=self._tool_find_element,
            )
        )

        self.register(
            ToolSpec(
                name="locate_element",
                description=(
                    "Dado um ponto da tela, devolve o elemento ali presente (fusão visão+a11y). "
                    "x/y são relativos [0,1] por padrão; use is_relative=false para pixels."
                ),
                parameters=_param(
                    {
                        "x": _num("Coordenada horizontal."),
                        "y": _num("Coordenada vertical."),
                        "is_relative": _bool("Se true (padrão), x/y são fração da tela."),
                        "app": _str("Nome do aplicativo alvo (opcional)."),
                    },
                    required=["x", "y"],
                ),
                handler=self._tool_locate_element,
            )
        )

        self.mutate(
            ToolSpec(
                name="click_element",
                description=(
                    "Clica num controle pelo uid (vindo de find_element/locate_element). "
                    "Usa a ação semântica do AT-SPI e cai para clique por coordenada se falhar."
                ),
                parameters=_param(
                    {
                        "uid": _str("UID do elemento."),
                        "app": _str("Nome do aplicativo alvo (opcional)."),
                    },
                    required=["uid"],
                ),
                handler=self._tool_click_element,
            )
        )

        self.mutate(
            ToolSpec(
                name="type_element",
                description=(
                    "Digita texto num campo editável identificado por uid. "
                    "Insere via AT-SPI (não depende de uinput) e faz fallback para teclado virtual."
                ),
                parameters=_param(
                    {
                        "uid": _str("UID do campo de texto."),
                        "text": _str("Texto a digitar."),
                        "press_enter": _bool("Enviar Enter ao final (padrão false)."),
                        "app": _str("Nome do aplicativo alvo (opcional)."),
                    },
                    required=["uid", "text"],
                ),
                handler=self._tool_type_element,
            )
        )

        self.mutate(
            ToolSpec(
                name="keyboard_type",
                description="Digita texto no elemento que tem o foco do teclado.",
                parameters=_param(
                    {
                        "text": _str("Texto a digitar."),
                        "press_enter": _bool("Enviar Enter ao final (padrão false)."),
                    },
                    required=["text"],
                ),
                handler=self._tool_keyboard_type,
            )
        )

        self.mutate(
            ToolSpec(
                name="keyboard_hotkey",
                description=(
                    "Envia um atalho de teclado (ex.: ['ctrl','t'], ['alt','Tab']). "
                    "Atalhos que fecham aplicativos exigem confirmação."
                ),
                parameters=_param(
                    {"keys": {"type": "ARRAY", "description": "Lista de teclas na ordem do atalho."}},
                    required=["keys"],
                ),
                handler=self._tool_keyboard_hotkey,
            )
        )

        self.mutate(
            ToolSpec(
                name="mouse_click",
                description=(
                    "Clica numa coordenada da tela. Só use se não houver uid confiável. "
                    "Coordenadas fora da cerca digital são recusadas."
                ),
                parameters=_param(
                    {
                        "x": _num("Coordenada horizontal (ou fração [0,1] se is_relative)."),
                        "y": _num("Coordenada vertical (ou fração [0,1] se is_relative)."),
                        "is_relative": _bool("Se true, x/y são fração da tela."),
                        "button": _str("Botão do mouse: left (padrão), right ou middle."),
                        "double": _bool("Clique duplo (padrão false)."),
                    },
                    required=["x", "y"],
                ),
                handler=self._tool_mouse_click,
            )
        )

        self.register(
            ToolSpec(
                name="find_on_screen",
                description=(
                    "Localiza visualmente textos, botões ou controles na tela via OCR e visão computacional. "
                    "Devolve coordenadas absolutas (x, y), caixa delimitadora (bbox) e o texto encontrado."
                ),
                parameters=_param(
                    {"query": _str("Texto, rótulo ou botão procurado na tela.")},
                    required=["query"],
                ),
                handler=self._tool_find_on_screen,
            )
        )

        self.mutate(
            ToolSpec(
                name="click_on_screen",
                description=(
                    "Localiza visualmente um texto ou botão na tela e clica nele diretamente, "
                    "animando o Cursor Fantasma até o elemento. Use isto quando o elemento não tiver UID "
                    "ou quando o AT-SPI falhar."
                ),
                parameters=_param(
                    {
                        "query": _str("Texto ou rótulo do botão a clicar."),
                        "button": _str("Botão do mouse: left (padrão), right ou middle."),
                        "double": _bool("Clique duplo (padrão false)."),
                    },
                    required=["query"],
                ),
                handler=self._tool_click_on_screen,
            )
        )

        self.register(
            ToolSpec(
                name="read_screen_text",
                description=(
                    "Extrai todo o texto visível na tela em formato estruturado com suas posições espaciais (mapa visual de controles)."
                ),
                parameters=_param({}),
                handler=self._tool_read_screen_text,
            )
        )

        self.mutate(
            ToolSpec(
                name="launch_app",
                description="Abre um aplicativo pelo nome (busca no menu e, se falhar, no $PATH).",
                parameters=_param(
                    {"query": _str("Nome do aplicativo (ex.: 'firefox', 'calculadora').")},
                    required=["query"],
                ),
                handler=self._tool_launch_app,
            )
        )

        self.register(
            ToolSpec(
                name="list_directory",
                description="Lista arquivos e pastas de um diretório.",
                parameters=_param(
                    {
                        "path": _str("Caminho (padrão: ~/Downloads)."),
                        "limit": _num("Máximo de entradas (padrão 50)."),
                    }
                ),
                handler=self._tool_list_directory,
            )
        )

        self.register(
            ToolSpec(
                name="read_file",
                description="Lê o conteúdo de um arquivo de texto (com limite de caracteres).",
                parameters=_param(
                    {
                        "path": _str("Caminho do arquivo."),
                        "max_chars": _num("Limite de caracteres (padrão 4000)."),
                    },
                    required=["path"],
                ),
                handler=self._tool_read_file,
            )
        )

        self.mutate(
            ToolSpec(
                name="write_document",
                description=(
                    "Cria ou sobrescreve um arquivo (.md, .txt, .docx, .pptx). "
                    "Gera snapshot para desfazer. Exige confirmação."
                ),
                parameters=_param(
                    {
                        "filename": _str("Nome do arquivo (com extensão)."),
                        "content": _str("Conteúdo (Markdown para .docx/.pptx)."),
                        "directory": _str("Pasta de destino (padrão ~/Documentos/Relatorios)."),
                    },
                    required=["filename", "content"],
                ),
                handler=self._tool_write_document,
            )
        )

        self.mutate(
            ToolSpec(
                name="organize_directory",
                description=(
                    "Organiza os arquivos avulsos de uma pasta em subpastas por tipo. "
                    "Reversível. Exige confirmação."
                ),
                parameters=_param(
                    {"directory": _str("Pasta a organizar (padrão ~/Downloads).")}
                ),
                handler=self._tool_organize_directory,
            )
        )

        self.register(
            ToolSpec(
                name="capture_screen",
                description=(
                    "Tira um screenshot da tela e salva em arquivo temporário. "
                    "Devolve o caminho — útil quando a árvore de acessibilidade não basta."
                ),
                parameters=_param(
                    {"interactive": _bool("Se true, abre o seletor de área (padrão false).")}
                ),
                handler=self._tool_capture_screen,
            )
        )

        # Mutação, mas de baixo risco: grava um .md NOVO em ~/Documentos/Estudos e
        # nunca sobrescreve (o arquivo existente vira "-2", "-3"). Por isso não
        # entra na lista de ferramentas que pedem confirmação — pedir "posso salvar
        # sua aula?" a cada captura transformaria o estudo numa fila de perguntas.
        self.mutate(
            ToolSpec(
                name="capture_lesson",
                description=(
                    "Captura o conteúdo de uma aula ou material VISÍVEL NA TELA (AVA, player SCORM, PDF) "
                    "lendo a tela, não o HTML. Use em vez de `read_web_page` sempre que a página for uma "
                    "SPA que devolve só o shell — o AVA devolve 24 palavras por HTTP e o texto inteiro "
                    "na tela. Salva em Markdown e devolve o caminho."
                ),
                parameters=_param(
                    {
                        "app": _str("Aplicativo alvo (padrão: o app em foco)."),
                        "strategy": _str(
                            "Como ler: 'auto' (AT-SPI, depois área de transferência, depois OCR), "
                            "'atspi', 'clipboard' ou 'ocr'."
                        ),
                        "filename": _str("Nome do arquivo .md (opcional; derivado do título se vazio)."),
                        "directory": _str("Pasta de destino (padrão: ~/Documentos/Estudos)."),
                        "min_words": _num("Abaixo disso a captura é considerada rala (padrão 120)."),
                        "save": _bool("Gravar em arquivo (padrão true)."),
                    }
                ),
                handler=self._tool_capture_lesson,
            )
        )

        self.mutate(
            ToolSpec(
                name="undo_last",
                description="Desfaz a última ação reversível (escrita ou organização de arquivos).",
                parameters=_param({}),
                handler=self._tool_undo_last,
            )
        )

        self.register(
            ToolSpec(
                name="academic_search",
                description=(
                    "Pesquisa em bases científicas e órgãos oficiais (SciELO, IBGE, Sebrae, IPEA, "
                    "Google Acadêmico, CAPES, HBR) para TCC, artigos e Projetos Integradores (PI) de Gestão Comercial."
                ),
                parameters=_param(
                    {
                        "query": _str("Tema, conceitos ou termos-chave acadêmicos/estatísticos a pesquisar."),
                        "source": _str("Fonte alvo opcional: 'all' (padrão), 'scielo', 'ibge', 'sebrae', 'ipea', 'scholar', 'internacional'."),
                        "limit": _num("Máximo de artigos/resultados (padrão 5)."),
                    },
                    required=["query"],
                ),
                handler=self._tool_academic_search,
            )
        )

        self.register(
            ToolSpec(
                name="web_search",
                description="Pesquisa na internet por fatos atualizados, notícias, cotações e documentações.",
                parameters=_param(
                    {
                        "query": _str("Termo de pesquisa na web."),
                        "limit": _num("Máximo de resultados (padrão 4)."),
                    },
                    required=["query"],
                ),
                handler=self._tool_web_search,
            )
        )

        self.register(
            ToolSpec(
                name="read_web_page",
                description="Lê e extrai o conteúdo de texto limpo de uma página web ou artigo a partir da URL.",
                parameters=_param(
                    {
                        "url": _str("URL da página a ser lida (ou omita para ler a página aberta no navegador)."),
                        "max_chars": _num("Limite de caracteres (padrão 6000)."),
                    }
                ),
                handler=self._tool_read_web_page,
            )
        )

        self.mutate(
            ToolSpec(
                name="open_url",
                description="Abre uma página web ou link no navegador padrão.",
                parameters=_param(
                    {"url": _str("URL a abrir no navegador.")},
                    required=["url"],
                ),
                handler=self._tool_open_url,
            )
        )

        self.mutate(
            ToolSpec(
                name="open_document",
                description="Abre um arquivo (.docx, .pdf, .ods, .xlsx) no LibreOffice ou visualizador do sistema.",
                parameters=_param(
                    {
                        "path": _str("Caminho do arquivo (ex: ~/Documentos/Gestao_Comercial/TCC_Artigos/Introducao_TCC.docx)."),
                        "page_number": _num("Página para abrir (padrão 1)."),
                    },
                    required=["path"],
                ),
                handler=self._tool_open_document,
            )
        )

        self.register(
            ToolSpec(
                name="done",
                description=(
                    "Encerra a tarefa. Chame quando o objetivo estiver cumprido (ou quando "
                    "faltar algo que só o usuário pode fazer), passando a resposta final."
                ),
                parameters=_param({"answer": _str("Resposta final para o usuário.")}),
                handler=self._tool_done,
            )
        )

    # -- leitura ------------------------------------------------------------ #

    def _tree_for(self, app: str | None) -> tuple[Any | None, str | None]:
        inspector = self.inspector
        if inspector is None:
            return None, "Árvore de acessibilidade indisponível (AT-SPI não inicializou)."
        try:
            tree = inspector.get_ui_tree(app or None)
        except Exception as exc:
            return None, f"Falha ao ler a árvore: {exc}"
        if tree is None:
            return None, f"Não foi possível inspecionar '{app or 'aplicativo em foco'}'."
        return tree, None

    def _tool_get_ui_tree(self, args: dict[str, Any]) -> dict[str, Any]:
        app = (args.get("app") or "").strip() or None
        depth = int(args.get("max_depth") or 4)
        tree, error = self._tree_for(app)
        if error:
            return {"ok": False, "error": error}
        try:
            summary = tree.to_summary(include_bounds=bool(args.get("include_bounds")))
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao resumir a árvore: {exc}"}
        truncated = len(summary) > self.max_tree_chars
        return {
            "ok": True,
            "app": app or (self.inspector.get_focused_app() if self.inspector else None),
            "tree": summary[: self.max_tree_chars],
            "truncated": truncated,
            "hint": "Árvore cortada: use find_element com um nome mais específico."
            if truncated
            else "",
        }

    def _tool_find_element(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip().lower()
        role = str(args.get("role") or "").strip().lower()
        if not query and not role:
            return {"ok": False, "error": "Informe `query` e/ou `role`."}
        limit = int(args.get("limit") or 10)
        tree, error = self._tree_for((args.get("app") or "").strip() or None)
        if error:
            return {"ok": False, "error": error}

        def match(el: Any) -> bool:
            name = (getattr(el, "name", "") or "").lower()
            role_ok = not role or role in (getattr(el, "role", "") or "").lower()
            query_ok = not query or query in name
            return role_ok and query_ok

        try:
            found = tree.find(match)
        except Exception as exc:
            return {"ok": False, "error": f"Falha na busca: {exc}"}

        # Controles interativos primeiro: é quase sempre o que o modelo quer clicar.
        found.sort(key=lambda el: (not bool(getattr(el, "is_interactive", False)),))
        return {
            "ok": bool(found),
            "count": len(found),
            "elements": [_element_dict(el) for el in found[:limit]],
            "error": "" if found else "Nenhum elemento corresponde à busca.",
        }

    def _tool_locate_element(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            x = float(args.get("x"))
            y = float(args.get("y"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "x e y precisam ser números."}
        is_relative = bool(args.get("is_relative", True))
        tree, error = self._tree_for((args.get("app") or "").strip() or None)
        if error:
            return {"ok": False, "error": error}

        abs_x, abs_y = self._to_absolute(x, y, is_relative)
        if abs_x is None:
            return {"ok": False, "error": "Não foi possível converter a coordenada relativa."}
        try:
            from ..core.a11y import DesktopInspector

            element = DesktopInspector.element_at_point(tree, int(abs_x), int(abs_y))
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao localizar: {exc}"}
        if element is None:
            return {"ok": False, "error": f"Nenhum elemento em ({int(abs_x)}, {int(abs_y)})."}
        return {"ok": True, "element": _element_dict(element), "x": int(abs_x), "y": int(abs_y)}

    # -- escrita/input ------------------------------------------------------ #

    def _tool_click_element(self, args: dict[str, Any]) -> dict[str, Any]:
        uid = str(args.get("uid") or "").strip()
        if not uid:
            return {"ok": False, "error": "Informe o `uid` do elemento."}
        tree, error = self._tree_for((args.get("app") or "").strip() or None)
        if error:
            return {"ok": False, "error": error}

        from ..core.a11y import DesktopInspector

        element = DesktopInspector.find_element_by_uid(tree, uid)
        if element is None:
            return {"ok": False, "error": f"UID '{uid}' não existe mais. Recarregue a árvore."}

        inspector = self.inspector
        cx, cy = _center(element)
        if cx is not None and not is_blocked_app(getattr(inspector, "get_focused_app", lambda: None)()):
            allowed, reason = self.check_coordinate(cx, cy)
            if not allowed:
                return {"ok": False, "error": f"Clique bloqueado pela cerca digital: {reason}", "blocked_by": "fence"}

        if cx is not None and cy is not None:
            try:
                from ..ui.ghost_cursor import GhostCursorOverlay
                GhostCursorOverlay.get_default().click_at(cx, cy, label=f"Clicando em '{element.name}'")
            except Exception as exc:
                logger.debug("Ghost cursor indisponível no _tool_click_element: %s", exc)

        if inspector is not None:
            try:
                inspector.focus_element(element)
                if inspector.do_action(element):
                    return {"ok": True, "message": f"Clicado em '{element.name}' via AT-SPI.", "uid": uid}
            except Exception as exc:
                logger.debug("Ação semântica falhou para %s: %s", uid, exc)

        return self._click_point(cx, cy, element_name=getattr(element, "name", uid))

    def _tool_type_element(self, args: dict[str, Any]) -> dict[str, Any]:
        uid = str(args.get("uid") or "").strip()
        text = str(args.get("text") or "")
        if not uid or not text:
            return {"ok": False, "error": "`uid` e `text` são obrigatórios."}
        tree, error = self._tree_for((args.get("app") or "").strip() or None)
        if error:
            return {"ok": False, "error": error}

        from ..core.a11y import DesktopInspector

        element = DesktopInspector.find_element_by_uid(tree, uid)
        if element is None:
            return {"ok": False, "error": f"UID '{uid}' não existe mais."}

        cx, cy = _center(element)
        if cx is not None and cy is not None:
            try:
                from ..ui.ghost_cursor import GhostCursorOverlay
                GhostCursorOverlay.get_default().type_at(cx, cy, text=text, label=f"Digitando em '{element.name}'")
            except Exception as exc:
                logger.debug("Ghost cursor indisponível no _tool_type_element: %s", exc)

        inspector = self.inspector
        if inspector is not None:
            try:
                inspector.focus_element(element)
                ok, message = inspector.text_insert(element, text)
                if ok:
                    if args.get("press_enter") and self.input_driver is not None:
                        self.input_driver.hotkey("enter")
                    return {"ok": True, "message": f"Texto inserido em '{element.name}'."}
                logger.debug("text_insert recusou (%s); caindo para teclado virtual", message)
            except Exception as exc:
                logger.debug("text_insert falhou: %s", exc)

        cx, cy = _center(element)
        return self._type_after_click(cx, cy, text, bool(args.get("press_enter")), element.name)

    def _tool_keyboard_type(self, args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("text") or "")
        if not text:
            return {"ok": False, "error": "`text` é obrigatório."}
        driver = self.input_driver
        if driver is None:
            return {"ok": False, "error": "Driver de entrada indisponível (ydotool/uinput ausentes)."}
        ok, message = driver.type_text(text, press_enter=bool(args.get("press_enter")))
        return {"ok": bool(ok), "message": message}

    def _tool_keyboard_hotkey(self, args: dict[str, Any]) -> dict[str, Any]:
        keys = args.get("keys") or []
        if isinstance(keys, str):
            keys = [part for part in keys.replace("-", "+").split("+") if part]
        if not keys:
            return {"ok": False, "error": "`keys` precisa ser uma lista não vazia."}
        driver = self.input_driver
        if driver is None:
            return {"ok": False, "error": "Driver de entrada indisponível (ydotool/uinput ausentes)."}
        ok, message = driver.hotkey(*[str(k) for k in keys])
        return {"ok": bool(ok), "message": message, "keys": list(keys)}

    def _tool_mouse_click(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            x = float(args.get("x"))
            y = float(args.get("y"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "x e y precisam ser números."}
        is_relative = bool(args.get("is_relative", False))
        driver = self.input_driver
        if driver is None:
            return {"ok": False, "error": "Driver de entrada indisponível (ydotool/uinput ausentes)."}

        if is_relative:
            ok, message = driver.click_relative(
                x,
                y,
                button=str(args.get("button") or "left"),
                double=bool(args.get("double")),
            )
            return {"ok": bool(ok), "message": message}

        abs_x, abs_y = int(x), int(y)
        allowed, reason = self.check_coordinate(abs_x, abs_y)
        if not allowed:
            return {
                "ok": False,
                "error": f"Clique bloqueado pela cerca digital: {reason}",
                "blocked_by": "fence",
                "x": abs_x,
                "y": abs_y,
            }
        ok, message = driver.click(
            abs_x,
            abs_y,
            button=str(args.get("button") or "left"),
            double=bool(args.get("double")),
        )
        return {"ok": bool(ok), "message": message, "x": abs_x, "y": abs_y}

    def _tool_find_on_screen(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "`query` é obrigatório."}
        try:
            from ..core.ui_grounding import UIGroundingService
            candidates = UIGroundingService.find_elements(query, fence=self._fence)
            if not candidates:
                return {"ok": False, "error": f"Nenhum elemento correspondente a '{query}' na tela."}
            return {
                "ok": True,
                "count": len(candidates),
                "best_match": candidates[0][0].to_dict(),
                "score": round(candidates[0][1], 2),
                "all_matches": [c[0].to_dict() for c in candidates[:5]],
            }
        except Exception as exc:
            return {"ok": False, "error": f"Falha na localização visual: {exc}"}

    def _tool_click_on_screen(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "`query` é obrigatório."}
        button = str(args.get("button") or "left")
        double = bool(args.get("double", False))
        driver = self.input_driver
        if driver is None:
            return {"ok": False, "error": "Driver de entrada indisponível."}
        try:
            from ..core.ui_grounding import UIGroundingService
            ok, msg, coords = UIGroundingService.click_visual_element(
                query, button=button, double=double, driver=driver, fence=self._fence
            )
            return {
                "ok": ok,
                "message": msg,
                "coords": coords,
                "query": query,
            }
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao clicar visualmente: {exc}"}

    def _tool_read_screen_text(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            from ..core.ui_grounding import UIGroundingService
            elements = UIGroundingService.scan_screen(fence=self._fence)
            return {
                "ok": True,
                "count": len(elements),
                "elements": [el.to_dict() for el in elements[:40]],
            }
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao ler texto da tela: {exc}"}

    def _tool_launch_app(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "`query` é obrigatório."}
        if is_blocked_app(query):
            return {"ok": False, "error": f"Aplicativo bloqueado por política: '{query}'.", "blocked_by": "policy"}
        try:
            from ..core.apps import AppManager
        except Exception as exc:
            return {"ok": False, "error": f"Gestão de aplicativos indisponível: {exc}"}

        app, message = AppManager.find_app(query)
        if app is None:
            binary = AppManager.find_binary(query)
            if not binary:
                return {"ok": False, "error": f"Aplicativo '{query}' não encontrado. {message}"}
            try:
                import subprocess

                subprocess.Popen([binary], start_new_session=True)  # noqa: S603 - launch pedido pelo usuário
                return {"ok": True, "message": f"'{query}' iniciado via '{binary}'.", "binary": binary}
            except Exception as exc:
                return {"ok": False, "error": f"Falha ao iniciar '{binary}': {exc}"}

        ok, launch_message = AppManager.launch(app)
        return {"ok": bool(ok), "message": launch_message, "app": app.get_name() if hasattr(app, "get_name") else query}

    # -- arquivos ----------------------------------------------------------- #

    def _tool_list_directory(self, args: dict[str, Any]) -> dict[str, Any]:
        path = os.path.expanduser(str(args.get("path") or "~/Downloads").strip())
        limit = int(args.get("limit") or MAX_LIST_ENTRIES)
        if not os.path.isdir(path):
            return {"ok": False, "error": f"'{path}' não é uma pasta."}
        try:
            entries = sorted(os.listdir(path))[:limit]
        except OSError as exc:
            return {"ok": False, "error": f"Falha ao listar '{path}': {exc}"}
        items = []
        for name in entries:
            full = os.path.join(path, name)
            items.append({"name": name, "dir": os.path.isdir(full), "size": _safe_size(full)})
        return {"ok": True, "path": path, "count": len(items), "entries": items}

    def _tool_read_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path") or "").strip()
        if not path:
            return {"ok": False, "error": "`path` é obrigatório."}
        max_chars = int(args.get("max_chars") or MAX_READ_CHARS)
        try:
            from ..core.files import FileManager

            ok, content = FileManager.read_document(path, max_chars=max_chars)
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao ler: {exc}"}
        return {"ok": bool(ok), "path": path, "content": content, "error": "" if ok else content}

    def _tool_write_document(self, args: dict[str, Any]) -> dict[str, Any]:
        filename = str(args.get("filename") or "").strip()
        content = str(args.get("content") or "")
        if not filename:
            return {"ok": False, "error": "`filename` é obrigatório."}
        try:
            from ..core.files import FileManager
        except Exception as exc:
            return {"ok": False, "error": f"Gestão de arquivos indisponível: {exc}"}

        target = FileManager.resolve_target_path(filename, args.get("directory"))
        self._push_file_snapshot(target)

        ok, message, path = FileManager.write_document(filename, content, args.get("directory"))
        return {"ok": bool(ok), "message": message, "path": path, "undoable": self._has_undo()}

    def _tool_organize_directory(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            from ..core.files import FileManager
        except Exception as exc:
            return {"ok": False, "error": f"Gestão de arquivos indisponível: {exc}"}

        directory = (args.get("directory") or "~/Downloads").strip()
        moves: list[tuple[str, str]] = []
        ok, message, stats = FileManager.organize_directory(directory, dry_run=False, moves=moves)
        if ok and moves:
            self._push_organize_snapshot(moves)
        return {"ok": bool(ok), "message": message, "stats": stats, "undoable": bool(moves)}

    def _tool_undo_last(self, args: dict[str, Any]) -> dict[str, Any]:
        stack = self.undo_stack
        if stack is None or len(stack) == 0:
            return {"ok": False, "error": "Nada para desfazer."}
        ok, message, entry = stack.undo()
        return {"ok": bool(ok), "message": message, "label": getattr(entry, "label", "")}

    # -- visão -------------------------------------------------------------- #

    def _tool_capture_screen(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            from ..core.vision import ScreenCaptureService
        except Exception as exc:
            return {"ok": False, "error": f"Captura de tela indisponível: {exc}"}
        ok, data, message = ScreenCaptureService.capture(interactive=bool(args.get("interactive")))
        if not ok or not data:
            return {"ok": False, "error": message or "Captura vazia."}
        try:
            handle, path = tempfile.mkstemp(prefix="zorin-agent-", suffix=".jpg")
            with os.fdopen(handle, "wb") as out:
                out.write(data)
        except OSError as exc:
            return {"ok": False, "error": f"Falha ao salvar o screenshot: {exc}"}
        return {"ok": True, "path": path, "bytes": len(data), "message": message}

    def _tool_capture_lesson(self, args: dict[str, Any]) -> dict[str, Any]:
        """Lê o material renderizado na tela — o caminho certo para SPA/SCORM."""
        try:
            from ..core.study_capture import LessonCapture
        except Exception as exc:
            return {"ok": False, "error": f"Captura de aula indisponível: {exc}"}

        raw_min = args.get("min_words")
        try:
            min_words = int(raw_min) if raw_min not in (None, "") else 120
        except (TypeError, ValueError):
            min_words = 120

        strategy = str(args.get("strategy") or "auto").strip().lower()
        capture = LessonCapture(min_words=min_words)

        try:
            result = capture.capture(str(args.get("app") or "").strip() or None, strategy=strategy)
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao capturar a tela: {exc}"}

        payload: dict[str, Any] = {
            "strategy": result.strategy,
            "title": result.title,
            "word_count": result.word_count,
            "app": result.app,
            "warnings": list(result.warnings),
            "preview": result.text[:400],
        }
        if not result.ok:
            payload["error"] = result.warnings[0] if result.warnings else "Nada capturado na tela."
            return payload | {"ok": False}

        if args.get("save") is False:
            return payload | {"ok": True, "saved": False, "text": result.text}

        try:
            path = capture.save(
                result,
                filename=str(args.get("filename") or "").strip() or None,
                directory=str(args.get("directory") or "").strip() or None,
            )
        except OSError as exc:
            return payload | {"ok": False, "error": f"Falha ao salvar o material: {exc}"}
        return payload | {"ok": True, "path": path, "saved": True}

    def _tool_academic_search(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "`query` é obrigatório."}
        source = str(args.get("source") or "all").strip()
        limit = int(args.get("limit") or 5)
        try:
            from ..core.web_search import WebSearchClient

            client = WebSearchClient()
            results = client.academic_search(query, source=source, max_results=limit)
            entries = [
                {"title": r.title, "url": r.url, "snippet": r.snippet}
                for r in results
            ]
            return {
                "ok": True,
                "count": len(entries),
                "source": source,
                "query": query,
                "results": entries,
            }
        except Exception as exc:
            return {"ok": False, "error": f"Falha na pesquisa acadêmica: {exc}"}

    def _tool_web_search(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "`query` é obrigatório."}
        limit = int(args.get("limit") or 4)
        try:
            from ..core.web_search import WebSearchClient

            client = WebSearchClient()
            results = client.search(query, max_results=limit)
            entries = [
                {"title": r.title, "url": r.url, "snippet": r.snippet}
                for r in results
            ]
            return {
                "ok": True,
                "count": len(entries),
                "query": query,
                "results": entries,
            }
        except Exception as exc:
            return {"ok": False, "error": f"Falha na pesquisa web: {exc}"}

    def _tool_read_web_page(self, args: dict[str, Any]) -> dict[str, Any]:
        url = str(args.get("url") or "").strip() or None
        max_chars = int(args.get("max_chars") or 6000)
        try:
            from ..core.browser import BrowserManager

            res = BrowserManager.read_page(url=url)
            if not res.get("success"):
                return {"ok": False, "error": res.get("text") or "Falha ao ler página web."}
            text = str(res.get("text") or "")[:max_chars]
            return {
                "ok": True,
                "title": res.get("title") or "Página Web",
                "url": res.get("url") or url or "",
                "length": len(text),
                "content": text,
            }
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao ler página web: {exc}"}

    def _tool_open_url(self, args: dict[str, Any]) -> dict[str, Any]:
        url = str(args.get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "`url` é obrigatório."}
        if not url.startswith(("http://", "https://", "mailto:")):
            url = f"https://{url}"
        try:
            from gi.repository import Gio

            ok = Gio.AppInfo.launch_default_for_uri(url, None)
            if ok:
                return {"ok": True, "message": f"URL '{url}' aberta no navegador.", "url": url}
        except Exception:
            pass
        try:
            import subprocess

            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True, "message": f"URL '{url}' aberta com xdg-open.", "url": url}
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao abrir URL '{url}': {exc}"}

    def _tool_open_document(self, args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path") or "").strip()
        if not path:
            return {"ok": False, "error": "`path` é obrigatório."}
        page = int(args.get("page_number") or 1)
        try:
            from ..core.rag import LocalDocumentRAG

            rag = LocalDocumentRAG()
            ok, msg = rag.open_document(path, page_number=page)
            return {"ok": bool(ok), "message": msg, "path": path}
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao abrir documento: {exc}"}

    def _tool_done(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "answer": str(args.get("answer") or ""), "finished": True}

    # -- desfazer (snapshots) ----------------------------------------------- #

    def _has_undo(self) -> bool:
        stack = self.undo_stack
        return stack is not None and len(stack) > 0

    def _push_file_snapshot(self, path: str) -> None:
        """Fotografa o arquivo ANTES da escrita. Reusa o executor para não duplicar regras."""
        stack = self.undo_stack
        if stack is None:
            return
        try:
            from ..shell.executor import make_file_revert, snapshot_file
        except Exception as exc:  # executor puxa Gio; sem ele, escrevemos sem rede
            logger.debug("Snapshot de '%s' desativado: %s", path, exc)
            return
        snapshot = snapshot_file(path, append=False)
        if snapshot is None:
            return
        stack.push(
            label=f"Escrita de {os.path.basename(path)}",
            revert=make_file_revert(path, snapshot),
            action_type="write_document",
        )

    def _push_organize_snapshot(self, moves: list[tuple[str, str]]) -> None:
        stack = self.undo_stack
        if stack is None:
            return
        try:
            from ..shell.executor import make_organize_revert
        except Exception as exc:
            logger.debug("Desfazer de organização desativado: %s", exc)
            return
        stack.push(
            label=f"Organização de {len(moves)} arquivo(s)",
            revert=make_organize_revert(moves),
            action_type="organize_directory",
        )

    # -- helpers ------------------------------------------------------------ #

    def _to_absolute(self, x: float, y: float, is_relative: bool) -> tuple[float | None, float | None]:
        if not is_relative:
            return x, y
        fence = self.fence
        if fence is not None:
            try:
                point = fence.convert_relative_point(x, y)
                if point:
                    return float(point[0]), float(point[1])
            except Exception as exc:
                logger.debug("convert_relative_point falhou: %s", exc)
        monitor = fence.get_active_monitor() if fence is not None else None
        if monitor is not None:
            return monitor.relative_to_absolute(x, y)
        return None, None

    def _click_point(self, x: int | None, y: int | None, element_name: str = "") -> dict[str, Any]:
        if x is None or y is None:
            return {"ok": False, "error": "Elemento sem geometria e sem ação semântica disponível."}
        allowed, reason = self.check_coordinate(x, y)
        if not allowed:
            return {"ok": False, "error": f"Clique bloqueado pela cerca digital: {reason}", "blocked_by": "fence"}
        driver = self.input_driver
        if driver is None:
            return {"ok": False, "error": "Driver de entrada indisponível (ydotool/uinput ausentes)."}
        ok, message = driver.click(x, y)
        return {"ok": bool(ok), "message": message, "x": x, "y": y, "element": element_name}

    def _type_after_click(
        self,
        x: int | None,
        y: int | None,
        text: str,
        press_enter: bool,
        element_name: str,
    ) -> dict[str, Any]:
        clicked = self._click_point(x, y, element_name)
        if not clicked.get("ok"):
            return clicked
        driver = self.input_driver
        if driver is None:
            return {"ok": False, "error": "Driver de entrada indisponível (ydotool/uinput ausentes)."}
        ok, message = driver.type_text(text, press_enter=press_enter)
        return {"ok": bool(ok), "message": message, "element": element_name, "via": "teclado virtual"}


# --------------------------------------------------------------------------- #
# Helpers de módulo
# --------------------------------------------------------------------------- #


def _element_dict(element: Any) -> dict[str, Any]:
    bbox = getattr(element, "bbox", (0, 0, 0, 0)) or (0, 0, 0, 0)
    return {
        "uid": getattr(element, "uid", ""),
        "role": getattr(element, "role", ""),
        "name": getattr(element, "name", ""),
        "bbox": list(bbox),
        "interactive": bool(getattr(element, "is_interactive", False)),
    }


def _center(element: Any) -> tuple[int | None, int | None]:
    bbox = getattr(element, "bbox", None)
    if not bbox or len(bbox) != 4 or bbox[2] <= 0 or bbox[3] <= 0:
        return None, None
    return int(bbox[0] + bbox[2] / 2), int(bbox[1] + bbox[3] / 2)


def _safe_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _summarize_args(args: dict[str, Any], limit: int = 120) -> str:
    parts: list[str] = []
    for key, value in args.items():
        text = str(value)
        if len(text) > limit:
            text = text[:limit] + "…"
        parts.append(f"{key}={text}")
    return ", ".join(parts)
