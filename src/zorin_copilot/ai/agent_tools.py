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

import concurrent.futures
import copy
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from ..core.board_outcome import message_for, outcome_from_payload
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
DEFAULT_TOOL_TIMEOUT = 25.0

#: Sentinel que marca "já tentamos construir e não deu" — evita repetir o import
#: falho a cada passo do loop.
_UNAVAILABLE = object()

#: Famílias especializadas de ferramentas built-in. Quando um objetivo é informado
#: a `schema(objective=...)`, o núcleo essencial (UI, janelas, arquivos, web,
#: scratchpad, subagentes, `done`) entra sempre, e estas famílias só entram quando
#: o objetivo/contexto cita o domínio correspondente ou a ferramenta já está em
#: `active_tools`. Sem `objective` (`schema()`), 100% do catálogo é retornado.
BUILTIN_DOMAIN_GROUPS: dict[str, frozenset[str]] = {
    "network": frozenset({
        "network_inspect",
        "lan_devices_scan",
        "network_discover_lan",
        "network_identify_device",
        "network_watch_lan",
        "network_establish_baseline",
        "network_block_device",
        "local_services_list",
        "network_wake_on_lan",
        "network_release_port",
        "network_confirm_port",
        "network_probe_service",
        "network_dns_benchmark",
        "network_reconnect",
        "network_audit_exposure",
        "network_throughput",
        "network_diagnose_path",
        "network_ssl_check",
        "network_mdns_services",
    }),
    "study": frozenset({
        "capture_lesson",
        "study_generate_deck",
        "study_summarize",
        "study_export_anki",
        "study_show_board",
        "academic_search",
    }),
    "dev_git_docker": frozenset({
        "git_log",
        "git_status",
        "git_diff",
        "search_code",
        "vscode_workspace",
        "docker_list_containers",
        "docker_container_logs",
        "docker_container_action",
        "system_processes",
    }),
    "smart_home": frozenset({
        "smart_home_control",
        "smart_home_status",
        "smart_home_automate",
        "smart_home_events",
    }),
    "pim_google": frozenset({
        "contact_lookup",
        "contact_save",
        "email_compose",
        "calendar_event",
        "google_drive",
        "google_gmail",
    }),
    "system_media": frozenset({
        "media_control",
        "system_control",
        "system_hardware_health",
        "system_journal_errors",
    }),
}

ALL_SPECIALIZED_BUILTIN_TOOLS: frozenset[str] = frozenset().union(*BUILTIN_DOMAIN_GROUPS.values())

BUILTIN_DOMAIN_TRIGGERS: dict[str, tuple[str, ...]] = {
    "network": (
        "rede", "network", "lan", "wi-fi", "wifi", "ip", "porta", "port", "dns",
        "ssl", "tls", "ping", "latência", "latencia", "banda", "velocidade",
        "throughput", "roteador", "dispositivo", "mac", "arp", "mdns", "wake",
        "wol", "firewall", "conexão", "conexao", "servidor", "socket", "http",
    ),
    "study": (
        "estud", "aula", "anki", "flashcard", "baralho", "deck", "resum",
        "quadro", "lousa", "disciplina", "acadêmic", "academic", "scielo",
        "ibge", "artigo", "tcc", "prova", "exercício", "exercicio", "equação",
        "equacao", "matemática", "matematica", "cálculo", "calculo", "ava", "scorm",
    ),
    "dev_git_docker": (
        "git", "commit", "branch", "diff", "repositór", "repositor", "código",
        "codigo", "code", "vscode", "patch", "função", "funcao", "classe",
        "docker", "container", "conteiner", "processo", "pid", "cpu", "ram",
        "memória", "memoria", "porta", "servidor", "dev", "script", "python",
        "bug", "erro", "log",
    ),
    "smart_home": (
        "casa", "home assistant", "luz", "lâmpada", "lampada", "ar-condicionado",
        "clima", "temperatura", "sensor", "interruptor", "tomada", "cena",
        "quarto", "sala", "cozinha", "iot", "smart", "ligar a ", "apagar a ", "acender",
    ),
    "pim_google": (
        "email", "e-mail", "gmail", "mensagem", "contato", "agenda", "calendário",
        "calendario", "evento", "reunião", "reuniao", "compromisso", "drive",
        "nuvem", "google",
    ),
    "system_media": (
        "música", "musica", "áudio", "audio", "volume", "tocar", "pausar",
        "play", "pause", "spotify", "mídia", "midia", "tema", "escuro",
        "claro", "dark", "brilho", "energia", "bateria", "hardware",
        "temperatura", "disco", "saúde", "saude", "journal", "systemd",
        "erro do sistema", "falha",
    ),
}


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
    #: Tempo limite individual de execução em segundos (None usa default_timeout do registro).
    timeout: float | None = None


def _param(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """Monta o schema no formato já aceito pelos provedores (estilo Gemini)."""
    return {"type": "OBJECT", "properties": properties, "required": required or []}


def _str(desc: str) -> dict[str, str]:
    return {"type": "STRING", "description": desc}


def _num(desc: str) -> dict[str, str]:
    return {"type": "NUMBER", "description": desc}


def _int(desc: str) -> dict[str, str]:
    return {"type": "INTEGER", "description": desc}


def _bool(desc: str) -> dict[str, str]:
    return {"type": "BOOLEAN", "description": desc}


GEMINI_ALLOWED_SCHEMA_KEYS = frozenset({
    "type", "format", "description", "nullable", "enum", "properties", "required", "items"
})

GEMINI_TYPE_MAPPING = {
    "string": "STRING",
    "str": "STRING",
    "integer": "INTEGER",
    "int": "INTEGER",
    "number": "NUMBER",
    "float": "NUMBER",
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    "array": "ARRAY",
    "list": "ARRAY",
    "object": "OBJECT",
    "dict": "OBJECT",
}


def sanitize_gemini_schema(raw_schema: Any) -> dict[str, Any]:
    """Sanitiza recursivamente um JSON Schema para a especificação estrita do Gemini Schema (Protobuf).

    Remove chaves OpenAPI/JSON Schema proibidas pelo Gemini (e.g. exclusiveMaximum,
    exclusiveMinimum, minimum, maximum, title, default, additionalProperties, $schema, etc.),
    converte uniões anyOf/oneOf em tipos anuláveis e garante conformidade total com
    o Gemini Live WebSocket API (prevenindo erro 1007 de invalid payload).
    """
    if not isinstance(raw_schema, dict):
        return {"type": "STRING"}

    cleaned: dict[str, Any] = {}

    # 1. Trata anyOf / oneOf (padrão Pydantic/OpenAPI para Optional / Nullable)
    if "anyOf" in raw_schema or "oneOf" in raw_schema:
        union_variants = raw_schema.get("anyOf") or raw_schema.get("oneOf") or []
        is_nullable = False
        primary_variant = None
        for v in union_variants:
            if isinstance(v, dict):
                if v.get("type") in ("null", "NULL") or (v.get("const") is None and "const" in v):
                    is_nullable = True
                elif not primary_variant:
                    primary_variant = v

        merged: dict[str, Any] = {}
        if primary_variant and isinstance(primary_variant, dict):
            merged.update(primary_variant)
        if "description" in raw_schema and "description" not in merged:
            merged["description"] = raw_schema["description"]
        if is_nullable:
            merged["nullable"] = True
        raw_schema = merged

    # 2. Normaliza type
    raw_type = raw_schema.get("type")
    if isinstance(raw_type, list):
        is_nullable = "null" in raw_type
        non_null_types = [t for t in raw_type if t != "null"]
        base_t = non_null_types[0] if non_null_types else "string"
        cleaned["type"] = GEMINI_TYPE_MAPPING.get(base_t.lower(), "STRING")
        if is_nullable:
            cleaned["nullable"] = True
    elif isinstance(raw_type, str):
        cleaned["type"] = GEMINI_TYPE_MAPPING.get(raw_type.lower(), raw_type.upper())
    else:
        if "properties" in raw_schema:
            cleaned["type"] = "OBJECT"
        elif "items" in raw_schema:
            cleaned["type"] = "ARRAY"
        else:
            cleaned["type"] = "STRING"

    # 3. Description (com enriquecimento semântico de default se houver)
    desc = raw_schema.get("description")
    title = raw_schema.get("title")
    default_val = raw_schema.get("default")
    extra_hints: list[str] = []
    if default_val is not None and default_val != "":
        extra_hints.append(f"padrão: {default_val}")

    desc_str = str(desc or "").strip()
    if not desc_str and title:
        desc_str = str(title).strip()
    if extra_hints and desc_str and not any(h in desc_str for h in extra_hints):
        desc_str = f"{desc_str} ({'; '.join(extra_hints)})"

    if desc_str:
        cleaned["description"] = desc_str

    # 4. Format
    if "format" in raw_schema and isinstance(raw_schema["format"], str):
        cleaned["format"] = raw_schema["format"]

    # 5. Nullable
    if raw_schema.get("nullable") is True or cleaned.get("nullable") is True:
        cleaned["nullable"] = True

    # 6. Enum
    if "enum" in raw_schema and isinstance(raw_schema["enum"], (list, tuple)):
        cleaned["enum"] = [str(e) for e in raw_schema["enum"]]

    # 7. Properties & Required (para OBJECT)
    if cleaned["type"] == "OBJECT":
        raw_props = raw_schema.get("properties")
        cleaned_props: dict[str, Any] = {}
        if isinstance(raw_props, dict):
            for p_name, p_schema in raw_props.items():
                if isinstance(p_name, str):
                    cleaned_props[p_name] = sanitize_gemini_schema(p_schema)
        cleaned["properties"] = cleaned_props

        raw_req = raw_schema.get("required")
        if isinstance(raw_req, (list, tuple)):
            cleaned["required"] = [str(r) for r in raw_req if str(r) in cleaned_props]
        else:
            cleaned["required"] = []

    # 8. Items (para ARRAY)
    elif cleaned["type"] == "ARRAY":
        raw_items = raw_schema.get("items")
        if isinstance(raw_items, dict):
            cleaned["items"] = sanitize_gemini_schema(raw_items)
        else:
            cleaned["items"] = {"type": "STRING"}

    return cleaned


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
        default_timeout: float = DEFAULT_TOOL_TIMEOUT,
        memory: Any | None = None,
        board_opener: Any | None = None,
        subagent_runner: Any | None = None,
        scratchpad: dict[str, str] | None = None,
        plan_milestones: list[dict[str, str]] | None = None,
    ) -> None:
        self._inspector = inspector
        self._input_driver = input_driver
        self._undo_stack = undo_stack
        self._fence = fence
        self._memory = memory
        self._scratchpad: dict[str, str] = dict(scratchpad) if scratchpad is not None else {}
        self._plan_milestones: list[dict[str, str]] = [dict(m) for m in plan_milestones] if plan_milestones else []
        # Gancho de UI para o Quadro Negro: callable `(graph, focus) -> dict`.
        # O registro é agnóstico de GTK; a UI injeta o abridor (pode ser definido
        # depois, via `registry.board_opener = ...`). Sem ele, a ferramenta
        # `study_show_board` devolve uma mensagem graciosa em vez de quebrar.
        self.board_opener = board_opener
        # Gancho de subagentes: callable `(especialista, entrada) -> dict`.
        # Mesmo contrato do `board_opener`: o registro não conhece planejador nem
        # provedor, então quem sabe montar um pipeline é a camada que já tem o
        # roteador (a UI) ou o chamador (CLI). O progresso é problema de quem
        # monta o pipeline — o registro não sabe o que é uma janela. Sem o
        # gancho, `run_subagent` devolve mensagem graciosa em vez de quebrar.
        self.subagent_runner = subagent_runner
        self.policy = policy or RiskPolicy()
        self.dry_run = dry_run
        self.max_tree_chars = max_tree_chars
        self.mcp_manager = mcp_manager
        self.default_timeout = float(default_timeout)
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
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

    def restricted(self, names: Iterable[str]) -> "ToolRegistry":
        """Cópia com apenas as ferramentas de `names`. O original não é tocado.

        É o que dá substância a "ferramentas restritas" num subagente: o
        registro restrito é o único que o planejador do subagente enxerga, então
        a restrição é do **schema** que o modelo recebe, não uma checagem depois
        do fato que o modelo pode contornar.

        `copy.copy` + troca de `_specs` (e não mutação do dicionário herdado):
        `copy.copy` é rasa e compartilha o **mesmo** dicionário `_specs`, então
        filtrar no lugar apagaria ferramentas do registro original.
        """
        keep = {str(name) for name in names}
        clone = copy.copy(self)
        # O Scratchpad (`_scratchpad`) é compartilhado intencionalmente entre pai e
        # subagente; já os marcos do plano (`_plan_milestones`) são isolados por estágio.
        clone._plan_milestones = []
        clone._specs = {
            name: spec for name, spec in self._specs.items() if name in keep
        }
        return clone

    def _get_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="agent-tool"
            )
        return self._executor

    def close(self) -> None:
        """Libera a pool de threads do executor."""
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

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

    @property
    def memory(self) -> Any | None:
        """Gerenciador de memória persistente."""

        def build() -> Any:
            from ..core.memory import MemoryManager

            return MemoryManager()

        return self._component("_memory", build)

    @property
    def network_inspector(self) -> Any | None:
        """Inspetor de infraestrutura de redes."""

        def build() -> Any:
            from ..core.network_inspector import NetworkInspector

            return NetworkInspector()

        return self._component("_network_inspector", build)

    @property
    def scratchpad(self) -> dict[str, str]:
        """Bloco de notas de trabalho (Working Memory) da sessão atual."""
        return self._scratchpad

    @property
    def plan_milestones(self) -> list[dict[str, str]]:
        """Checklist de marcos (sub-objetivos) da demanda multipassos atual."""
        return self._plan_milestones


    # -- descrição para o modelo ------------------------------------------- #

    def schema(
        self,
        objective: str | None = None,
        session_context: Sequence[dict[str, str]] | None = None,
        active_tools: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Lista de ferramentas no formato esperado pelos provedores, com filtro inteligente de relevância."""
        specs = self._filter_relevant_specs(objective, session_context, active_tools)
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            }
            for spec in specs
        ]

    def _filter_relevant_specs(
        self,
        objective: str | None = None,
        session_context: Sequence[dict[str, str]] | None = None,
        active_tools: set[str] | None = None,
    ) -> list[ToolSpec]:
        """Filtra ferramentas (MCP e grupos built-in especializados) para reduzir a sobrecarga de tokens."""
        all_specs = list(self._specs.values())
        if not objective:
            return all_specs

        query_text = objective.lower()
        if session_context:
            recent_turns = session_context[-3:]
            context_text = " ".join(t.get("content", "") for t in recent_turns).lower()
            query_text = f"{query_text} {context_text}"

        mcp_triggers = {
            "github": ("git", "commit", "repositór", "repositor", "branch", "pull request", "pr", "push", "diff", "sha", "github", "gh", "issue", "release", "clone"),
            "git": ("git", "commit", "repositór", "repositor", "branch", "push", "diff", "sha", "log", "clone"),
            "sqlite": ("sqlite", "sql", "banco", "tabela", "database", "comercial", "query", "select ", "insert "),
            "duckduckgo": ("pesquis", "busca", "google", "web", "site", "internet", "notícia", "noticia", "artigo", "duckduckgo", "url", "http"),
            "fetch": ("pesquis", "busca", "web", "site", "url", "http", "página", "pagina", "download", "ler site"),
            "filesystem": ("arquivo", "pasta", "diretório", "diretorio", "ficheiro", "documento", "file", "folder", "path", "caminho", "ler", "salvar", "gravar", "projeto", "study-hub", "estudo"),
        }

        matched_servers = set()
        for server, kws in mcp_triggers.items():
            if any(kw in query_text for kw in kws):
                matched_servers.add(server)

        matched_builtin_tools: set[str] = set()
        for domain, kws in BUILTIN_DOMAIN_TRIGGERS.items():
            if any(kw in query_text for kw in kws):
                matched_builtin_tools.update(BUILTIN_DOMAIN_GROUPS.get(domain, ()))

        if active_tools:
            for tool_name in active_tools:
                if tool_name.startswith("mcp__"):
                    parts = tool_name.split("__")
                    if len(parts) >= 2:
                        matched_servers.add(parts[1])
                else:
                    matched_builtin_tools.add(tool_name)
                    for domain, domain_tools in BUILTIN_DOMAIN_GROUPS.items():
                        if tool_name in domain_tools:
                            matched_builtin_tools.update(domain_tools)

        if not matched_servers:
            matched_servers = {"filesystem", "duckduckgo"}

        selected: list[ToolSpec] = []
        for spec in all_specs:
            if spec.name.startswith("mcp__"):
                parts = spec.name.split("__")
                if len(parts) >= 2 and parts[1] in matched_servers:
                    selected.append(spec)
            elif spec.name in ALL_SPECIALIZED_BUILTIN_TOOLS:
                if spec.name in matched_builtin_tools:
                    selected.append(spec)
            else:
                # Ferramenta do núcleo essencial (Core) ou customizada injetada no registro
                selected.append(spec)

        return selected or all_specs

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
        """True só para ``CONFIRM``. ``AUTO`` executa sem aprovação (mas avisa depois)."""
        level, _ = self.classify(name, args)
        return level == RiskLevel.CONFIRM

    def notify_after(self, name: str, args: dict[str, Any] | None = None) -> bool:
        """True para ``AUTO``: executa sem perguntar, mas o passo pede aviso posterior.

        Sinal **distinto** de ``requires_approval`` de propósito: reaproveitá-lo faria
        o cartão voltar a pedir clique para uma ação que já executou.
        """
        level, _ = self.classify(name, args)
        return level == RiskLevel.AUTO

    def is_safe_read_only(self, name: str, args: dict[str, Any] | None = None) -> bool:
        """True se a ferramenta existe, não é mutante, não é de encerramento e tem risco SAFE."""
        clean_name = (name or "").strip()
        if not clean_name or clean_name in FINISH_TOOLS:
            return False
        spec = self._specs.get(clean_name)
        if spec is None or spec.mutating:
            return False
        level, _ = self.classify(clean_name, args)
        return level is RiskLevel.SAFE

    # -- execução ----------------------------------------------------------- #

    def call_batch(
        self,
        calls: Sequence[tuple[str, dict[str, Any]]],
        contract: Any | None = None,
    ) -> dict[str, Any]:
        """Executa um lote de ferramentas SAFE de leitura em paralelo no ThreadPoolExecutor."""
        if not calls:
            return {"ok": False, "batch": True, "count": 0, "results": [], "error": "Lote vazio."}

        started = time.monotonic()
        executor = self._get_executor()
        futures = [
            executor.submit(self.call, tool_name, dict(tool_args or {}), contract)
            for tool_name, tool_args in calls
        ]
        results: list[dict[str, Any]] = []
        all_ok = True
        errors: list[str] = []
        for (tool_name, _), fut in zip(calls, futures):
            try:
                res = fut.result(timeout=self.default_timeout + 2.0)
            except Exception as exc:
                res = {"ok": False, "tool": tool_name, "error": f"{type(exc).__name__}: {exc}"}
            results.append(res)
            if not res.get("ok"):
                all_ok = False
                err_msg = str(res.get("error") or res.get("message") or "falha")
                errors.append(f"{tool_name}: {err_msg}")

        return {
            "ok": all_ok,
            "batch": True,
            "count": len(results),
            "tools": [name for name, _ in calls],
            "results": results,
            "error": "; ".join(errors) if errors else "",
            "elapsed": round(time.monotonic() - started, 3),
        }

    def call(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        contract: Any | None = None,
    ) -> dict[str, Any]:
        """Executa uma ferramenta e devolve SEMPRE um dicionário serializável.

        `contract` é opaco aqui (ignorado): é o gancho para registros validadores
        por composição (ex.: ValidatingToolRegistry) conferirem pré/pós-condições
        do passo. Mantê-lo como parâmetro mantém a assinatura compatível.

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
        timeout = spec.timeout if spec.timeout is not None else self.default_timeout
        handler = spec.handler
        if (
            isinstance(getattr(handler, "__self__", None), ToolRegistry)
            and getattr(handler, "__self__", None) is not self
        ):
            handler = getattr(self, getattr(handler, "__name__", ""), handler)

        try:
            if timeout and timeout > 0:
                future = self._get_executor().submit(handler, args)
                result = future.result(timeout=timeout)
            else:
                result = handler(args)
        except concurrent.futures.TimeoutError:
            logger.warning("Ferramenta '%s' excedeu o timeout de %.1fs", name, timeout)
            result = {
                "ok": False,
                "timeout": True,
                "error": f"A ferramenta '{name}' excedeu o tempo limite individual de {timeout:.1f}s.",
                "suggestion": (
                    f"O recurso solicitado pela ferramenta '{name}' existe e o sistema está operacional, "
                    "mas a operação demorou mais que o esperado. Não invente nem presuma o resultado; "
                    "tente novamente com escopo reduzido ou parâmetros mais específicos."
                ),
            }
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

        self.mutate(
            ToolSpec(
                name="click_and_type",
                description=(
                    "Clica em um elemento ou coordenada para focar e digita o texto solicitado. "
                    "Ação atômica recomendada para preencher campos em navegadores, Spotify e aplicativos."
                ),
                parameters=_param(
                    {
                        "x": _num("Coordenada horizontal (fração [0,1], escala 0-1000 ou pixels)."),
                        "y": _num("Coordenada vertical (fração [0,1], escala 0-1000 ou pixels)."),
                        "text": _str("Texto a ser digitado no campo focado."),
                        "press_enter": _bool("Pressiona Enter após digitar o texto (padrão false)."),
                        "clear_first": _bool("Limpa o campo selecionando tudo e apagando antes de digitar (padrão false)."),
                        "is_relative": _bool("Se true, x e y são fração da tela ou escala 0-1000 (padrão true)."),
                    },
                    required=["x", "y", "text"],
                ),
                handler=self._tool_click_and_type,
            )
        )

        self.register(
            ToolSpec(
                name="find_on_screen",
                description=(
                    "Localiza visualmente textos, botões ou controles na tela via OCR espacial e VLM multimodal. "
                    "Devolve coordenadas absolutas (x, y), caixa delimitadora (bbox) e fonte ('ocr' ou 'vision_model')."
                ),
                parameters=_param(
                    {
                        "query": _str("Texto, rótulo ou botão procurado na tela."),
                        "mode": _str("Modo de busca: 'auto' (OCR + fallback VLM), 'ocr' (apenas OCR) ou 'vlm' (apenas VLM)."),
                    },
                    required=["query"],
                ),
                handler=self._tool_find_on_screen,
            )
        )

        self.register(
            ToolSpec(
                name="locate_element_visual",
                description=(
                    "Localiza visualmente qualquer elemento, botão, ícone, controle de canvas, vídeo player ou área gráfica "
                    "na tela via modelo VLM local (Vision-Language Model). Essencial para interfaces sem acessibilidade AT-SPI."
                ),
                parameters=_param(
                    {
                        "query": _str("Descrição visual em linguagem natural do elemento (ex: 'botão de play', 'ícone de engrenagem', 'fechar banner')."),
                        "mode": _str("Modo de busca: 'auto' (padrão), 'vlm' (forçar VLM) ou 'ocr'."),
                    },
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
                        "prefer_vlm": _bool("Se true, prioriza localização por VLM visual direto (padrão false)."),
                    },
                    required=["query"],
                ),
                handler=self._tool_click_on_screen,
            )
        )

        self.register(
            ToolSpec(
                name="click_visual_element",
                description=(
                    "Localiza visualmente um controle gráfico, ícone, botão em canvas ou interface opaca via VLM local e executa o clique com o Cursor Fantasma."
                ),
                parameters=_param(
                    {
                        "query": _str("Descrição visual do elemento a ser clicado (ex: 'ícone de play', 'botão avançar')."),
                        "button": _str("Botão do mouse: left (padrão), right ou middle."),
                        "double": _bool("Clique duplo (padrão false)."),
                        "prefer_vlm": _bool("Priorizar modelo VLM sobre OCR (padrão true para elementos visuais)."),
                    },
                    required=["query"],
                ),
                handler=self._tool_click_visual_element,
            )
        )

        self.mutate(
            ToolSpec(
                name="scroll_page",
                description=(
                    "Rola a página ou janela ativa para baixo ('down') ou para cima ('up'). "
                    "Use isto para revelar botões, filtros, textos ou produtos que estejam abaixo ou acima do corte visível da tela."
                ),
                parameters=_param(
                    {
                        "direction": _str("Direção do scroll: 'down' (baixo, padrão) ou 'up' (cima)."),
                        "amount": _int("Quantidade de passos de scroll (padrão 3)."),
                    },
                ),
                handler=self._tool_scroll_page,
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
                name="list_open_windows",
                description=(
                    "Lista todas as janelas abertas atualmente no desktop com seus nomes de aplicativo, títulos e coordenadas. "
                    "Útil para inspecionar o que está aberto e decidir qual janela focar ou interagir."
                ),
                parameters=_param({}),
                handler=self._tool_list_open_windows,
            )
        )

        self.mutate(
            ToolSpec(
                name="focus_window",
                description=(
                    "Muda o foco do sistema operacional para uma janela aberta específica (pelo nome do app, trecho do título ou endereço). "
                    "Se a cerca espacial estiver em modo janela, também redireciona a cerca e o OCR para ela."
                ),
                parameters=_param(
                    {"query": _str("Nome do aplicativo, trecho do título ou endereço hexadecimal da janela.")},
                    required=["query"],
                ),
                handler=self._tool_focus_window,
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
                        "multi_page": _bool("Se true, captura múltiplos slides/páginas navegando pelo player SCORM/AVA (padrão false)."),
                        "max_pages": _num("Máximo de páginas a capturar sequencialmente (padrão 5, máx 20)."),
                        "advance_by": _str("Estratégia de avanço de slide: 'auto' (VLM/AT-SPI/hotkey), 'vlm', 'atspi' ou 'hotkey'."),
                    }
                ),
                handler=self._tool_capture_lesson,
            )
        )

        self.register(
            ToolSpec(
                name="study_generate_deck",
                description=(
                    "Gera flashcards com agendamento de repetição espaçada (SM-2) a partir de um arquivo de estudo Markdown capturado (.md), "
                    "salvando o baralho e opcionalmente exportando diretamente um pacote pronto para o Anki (.apkg)."
                ),
                parameters=_param(
                    {
                        "source": _str("Caminho do arquivo .md de estudo (ex: ~/Documentos/Estudos/aula.md)."),
                        "max_cards": _num("Máximo de flashcards a gerar (padrão 10)."),
                        "discipline": _str("Nome da disciplina acadêmica (opcional)."),
                        "export_apkg": _bool("Se true, compila e gera arquivo .apkg para o Anki (padrão true)."),
                        "out_apkg": _str("Caminho de saída opcional do arquivo .apkg."),
                    },
                    required=["source"],
                ),
                handler=self._tool_study_generate_deck,
            )
        )

        self.register(
            ToolSpec(
                name="study_summarize",
                description=(
                    "Gera um resumo executivo com afirmações completas em tópicos e um glossário de termos técnicos a partir de um material de estudo (.md), "
                    "salvando o arquivo estruturado de revisão."
                ),
                parameters=_param(
                    {
                        "source": _str("Caminho do arquivo .md de estudo."),
                        "max_topics": _num("Máximo de tópicos do resumo (padrão 8)."),
                        "max_terms": _num("Máximo de termos técnicos no glossário (padrão 12)."),
                        "discipline": _str("Nome da disciplina acadêmica (opcional)."),
                        "save": _bool("Se true, grava o arquivo de resumo ao lado do original (padrão true)."),
                    },
                    required=["source"],
                ),
                handler=self._tool_study_summarize,
            )
        )

        self.register(
            ToolSpec(
                name="study_export_anki",
                description=(
                    "Exporta um baralho existente no sistema para o formato nativo do Anki (.apkg ou .tsv) pronto para importação no desktop ou mobile."
                ),
                parameters=_param(
                    {
                        "deck_id": _str("ID ou identificador do baralho."),
                        "format": _str("Formato de exportação: 'apkg' ou 'tsv' (padrão 'apkg')."),
                        "output_path": _str("Caminho de destino do arquivo exportado (opcional)."),
                    },
                    required=["deck_id"],
                ),
                handler=self._tool_study_export_anki,
            )
        )

        self.register(
            ToolSpec(
                name="study_show_board",
                description=(
                    "Abre o Quadro Negro Digital com o raciocínio passo a passo de um problema de "
                    "matemática (equação quadrática ou sistema linear 2x2) e pode destacar um nó "
                    "específico. Use quando o usuário pedir para 'ver no quadro', 'abrir o quadro "
                    "negro', 'mostrar o passo a passo' ou destacar algo ('mostre a fatoração', "
                    "'destaque o passo 2'). O resultado traz `opened` (o quadro já está visível) e "
                    "`pending` (a resolução ainda está sendo gerada). Só diga ao usuário que o "
                    "quadro abriu quando `opened` for verdadeiro; se `ok` for falso, explique o "
                    "motivo que vem em `error` em vez de afirmar que abriu."
                ),
                parameters=_param(
                    {
                        "problem": _str(
                            "Problema a resolver e desenhar (ex: 'x^2 - 5x + 6 = 0' ou "
                            "'x + y = 10; x - y = 2'). Se omitido, reusa a última solução da conversa."
                        ),
                        "focus": _str(
                            "Nó a destacar: nome do método ('Bhaskara', 'Fatoração', 'Substituição'), "
                            "'enunciado' ou 'passo N' (ex: 'passo 2'). Opcional."
                        ),
                    },
                    required=[],
                ),
                handler=self._tool_study_show_board,
            )
        )

        # O subagente roda com contexto próprio e registro restrito; o pai vê
        # UM passo. `timeout=0` é deliberado: com o teto padrão de 25 s o
        # pipeline morreria no meio do primeiro estágio. O orçamento de um
        # subagente é dele (`SubagentSpec.max_steps`/`max_seconds`), não deste
        # registro. `timeout=0` faz o `call` rodar em linha, sem executor.
        #
        # Os nomes dos especialistas aqui são texto: `agent_tools` não pode
        # importar `subagents` no topo (é o `subagents` que importa daqui), e um
        # import local dentro do handler só adiaria o problema. A defasagem é
        # pega por teste (`test_descricao_da_ferramenta_cita_todo_especialista`).
        self.register(
            ToolSpec(
                name="run_subagent",
                description=(
                    "Delega uma tarefa LONGA e de raciocínio em várias etapas a um subagente "
                    "especializado. O subagente roda com contexto isolado (não vê esta conversa), "
                    "memória de trabalho (Scratchpad) compartilhada, ferramentas restritas ao seu "
                    "domínio e orçamento próprio de passos — use-o quando a tarefa exigiria muitos "
                    "passos aqui e poluiria o contexto principal. "
                    "Especialistas: 'researcher' (pesquisa com fontes e verificação), "
                    "'refactorer' (refatoração de código com leitura, edição e testes), "
                    "'a11y_auditor' (auditoria de acessibilidade de uma janela aberta) e "
                    "'window_analyst' (extração e cruzamento de dados entre múltiplas janelas). "
                    "Devolve o resultado consolidado do subagente; ele não conversa com o usuário."
                ),
                parameters=_param(
                    {
                        "specialist": _str(
                            "Nome do especialista: 'researcher', 'refactorer', 'a11y_auditor' ou 'window_analyst'."
                        ),
                        "input": _str(
                            "O que o subagente deve fazer, em detalhe. É a ÚNICA coisa que ele "
                            "recebe: inclua o alvo (tópico, caminho, janela) e o formato do "
                            "resultado esperado."
                        ),
                        "target_window": _str(
                            "Opcional: nome do aplicativo ou título da janela à qual o subagente deve dedicar seu foco."
                        ),
                        "scratchpad_key": _str(
                            "Opcional: chave do Scratchpad onde o relatório consolidado do subagente será gravado."
                        ),
                    },
                    required=["specialist", "input"],
                ),
                handler=self._tool_run_subagent,
                timeout=0,
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
                        "scope": _str("Escopo de busca: 'auto' (padrão), 'local' (trabalhos/anotações) ou 'web' (artigos científicos)."),
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
                name="git_log",
                description=(
                    "Consulta o histórico de commits do repositório Git local. "
                    "Devolve hashes abreviados, autor, data relativa e mensagens de commit."
                ),
                parameters=_param(
                    {
                        "path": _str("Caminho do repositório ou pasta/arquivo específico (padrão '.')."),
                        "max_count": _num("Número máximo de commits a listar (padrão 10, máximo 50)."),
                        "revision_range": _str("Faixa de revisão ou branch (ex.: 'HEAD', 'main..feature')."),
                        "file_path": _str("Filtrar apenas commits que alteraram este arquivo ou subdiretório."),
                    },
                    required=[],
                ),
                handler=self._tool_git_log,
                timeout=12.0,
            )
        )

        self.register(
            ToolSpec(
                name="git_status",
                description=(
                    "Inspeciona o estado atual do repositório Git local (branch atual, "
                    "arquivos modificados, staged e untracked)."
                ),
                parameters=_param(
                    {
                        "path": _str("Caminho do repositório Git (padrão '.')."),
                    },
                    required=[],
                ),
                handler=self._tool_git_status,
                timeout=8.0,
            )
        )

        self.register(
            ToolSpec(
                name="search_code",
                description=(
                    "Busca de alta performance por padrões de texto ou expressões regulares em arquivos de código ou diretórios usando ripgrep (ou fallback nativo), retornando arquivos, linhas e contexto sem estourar o orçamento de passos."
                ),
                parameters=_param(
                    {
                        "pattern": _str("Texto ou expressão regular a ser pesquisada."),
                        "path": _str("Caminho do diretório ou arquivo base para a busca (padrão '.')."),
                        "file_glob": _str("Filtro opcional de arquivos por extensão/glob (ex: '*.py', '*.ts', '*.json')."),
                        "case_sensitive": _bool("Se True, diferencia maiúsculas de minúsculas (padrão False)."),
                        "is_regex": _bool("Se True, interpreta o padrão como expressão regular; se False, busca literal (padrão True)."),
                        "max_results": _num("Máximo de ocorrências retornadas (padrão 30, máx 100)."),
                        "context_lines": _num("Número de linhas de contexto antes e depois da ocorrência (padrão 1, máx 5)."),
                    },
                    required=["pattern"],
                ),
                handler=self._tool_search_code,
                timeout=10.0,
            )
        )

        self.register(
            ToolSpec(
                name="system_processes",
                description=(
                    "Inspeciona processos em execução no sistema, consumo de CPU e memória (%CPU, %MEM), PIDs e comandos ativos (útil para verificar servidores, bancos de dados e serviços em segundo plano)."
                ),
                parameters=_param(
                    {
                        "filter": _str("Filtro opcional por nome do processo, comando, argumentos ou PID (ex: 'python', 'node', 'ollama', 'docker')."),
                        "sort_by": _str("Critério de ordenação dos processos: 'cpu', 'memory' ou 'pid' (padrão 'cpu')."),
                        "limit": _num("Número máximo de processos a retornar (padrão 20, máx 50)."),
                    },
                    required=[],
                ),
                handler=self._tool_system_processes,
                timeout=8.0,
            )
        )

        self.register(
            ToolSpec(
                name="media_control",
                description=(
                    "Controla tocadores de música e reprodutores de mídia como Spotify, VLC e navegadores. "
                    "Permite dar play, pausar, avançar, retroceder, ou buscar e reproduzir uma música/artista específico "
                    "(ex: 'Bohemian Rhapsody', 'Queen')."
                ),
                parameters=_param(
                    {
                        "action": _str("Ação de mídia: play, pause, play_pause, next, previous, get_status, search ou play_song."),
                        "query": _str("Nome da música, artista, banda ou playlist para buscar e tocar (ex: 'Bohemian Rhapsody', 'Queen', 'Daft Punk')."),
                        "player": _str("Nome opcional do reprodutor (padrão 'spotify')."),
                    },
                    required=["action"],
                ),
                handler=self._tool_media_control,
            )
        )

        self.mutate(
            ToolSpec(
                name="vscode_workspace",
                description=(
                    "Interage diretamente com o Visual Studio Code (VS Code) para fluxos completos de desenvolvimento: "
                    "detecta o projeto/workspace atualmente aberto, abre pastas no editor, cria projetos estruturados "
                    "(FastAPI, Flask, Node/Express, React, Python, Web) com .gitignore e .vscode/settings.json, "
                    "cria e atualiza arquivos de código abrindo-os diretamente em abas no editor na linha exata, "
                    "e lê a estrutura de arquivos do projeto com proteção de segredos (.env)."
                ),
                parameters=_param(
                    {
                        "action": {
                            "type": "STRING",
                            "enum": [
                                "get_active_project",
                                "open_workspace",
                                "create_project",
                                "write_code",
                                "patch_code",
                                "read_file",
                                "get_structure",
                                "open_file",
                            ],
                            "description": "Ação de desenvolvimento a realizar com o VS Code.",
                        },
                        "project_name": _str("Nome do novo projeto ou pasta (para 'create_project')."),
                        "folder_path": _str("Caminho da pasta a abrir como workspace (para 'open_workspace')."),
                        "file_path": _str("Caminho relativo ou absoluto do arquivo de código (para 'write_code', 'patch_code', 'read_file', 'open_file')."),
                        "code_content": _str("Código-fonte completo a ser gravado no arquivo (para 'write_code')."),
                        "target_code": _str("Trecho exato de código a ser substituído no arquivo (para 'patch_code')."),
                        "replacement_code": _str("Novo trecho de código que substituirá o target_code (para 'patch_code')."),
                        "template": {
                            "type": "STRING",
                            "enum": ["python", "fastapi", "flask", "node", "express", "react", "web", "empty"],
                            "description": "Template do projeto (padrão 'python' ou 'fastapi').",
                        },
                        "line_number": _int("Linha do arquivo para focar o cursor no editor (padrão 1)."),
                    },
                    required=["action"],
                ),
                handler=self._tool_vscode_workspace,
            )
        )

        self.mutate(
            ToolSpec(
                name="smart_home_control",
                description=(
                    "Controla dispositivos inteligentes da casa via Home Assistant (lâmpadas Avant Neo, "
                    "luzes, ar-condicionado, tomadas e interruptores). Permite ligar, desligar, ajustar brilho (1..100%), "
                    "temperatura de cor em Kelvin (2700K quente / 4000K neutro / 6500K frio), cores RGB e temperatura do ar."
                ),
                parameters=_param(
                    {
                        "action": {
                            "type": "STRING",
                            "description": "Ação a executar: 'turn_on', 'turn_off', 'toggle', 'set_temperature' ou 'set_hvac_mode'.",
                        },
                        "device_type": {
                            "type": "STRING",
                            "enum": ["light", "climate", "switch"],
                            "description": "Tipo de aparelho: 'light' (lâmpada), 'climate' (ar-condicionado) ou 'switch' (tomada/interruptor). Padrão 'light'.",
                        },
                        "entity": _str("Nome ou ID do aparelho (ex.: 'quarto', 'escritorio', 'avant neo'). Opcional se houver apenas um do tipo."),
                        "brightness": _int("Brilho da lâmpada de 1 a 100%."),
                        "color_temp": _str("Temperatura de cor em Kelvin ('2700', '4000', '6500') ou descrição ('quente', 'frio', 'neutro')."),
                        "color": _str("Nome da cor em português ('azul', 'vermelho', 'verde', 'amarelo', 'roxo', 'laranja', 'rosa')."),
                        "temperature": {
                            "type": "NUMBER",
                            "description": "Temperatura desejada em graus Celsius para o ar-condicionado.",
                        },
                        "hvac_mode": _str("Modo de climatização ('cool', 'heat', 'fan_only', 'off')."),
                    },
                    required=["action"],
                ),
                handler=self._tool_smart_home_control,
            )
        )

        self.register(
            ToolSpec(
                name="smart_home_status",
                description="Consulta o estado atual dos dispositivos inteligentes da casa (se as lâmpadas estão acesas, ar ligado, temperaturas, etc.).",
                parameters=_param(
                    {
                        "query": _str("Nome do aparelho ou cômodo específico a consultar (opcional)."),
                        "device_type": {
                            "type": "STRING",
                            "enum": ["light", "climate", "switch", "scene", "script", "automation", "all"],
                            "description": "Filtro por tipo de aparelho.",
                        },
                    }
                ),
                handler=self._tool_smart_home_status,
            )
        )

        self.mutate(
            ToolSpec(
                name="smart_home_automate",
                description=(
                    "Aciona cenas, scripts e automações do Home Assistant — o atalho de uma frase para "
                    "vários aparelhos (ex.: 'boa noite' apaga tudo). Use 'trigger_scene' para cenas, "
                    "'run_script' para scripts, 'trigger_automation' para automações e as ações 'list_*' "
                    "para descobrir os nomes disponíveis antes de acionar."
                ),
                parameters=_param(
                    {
                        "action": {
                            "type": "STRING",
                            "enum": [
                                "trigger_scene",
                                "run_script",
                                "trigger_automation",
                                "list_scenes",
                                "list_scripts",
                                "list_automations",
                            ],
                            "description": "Ação a executar sobre cenas/scripts/automações.",
                        },
                        "name": _str("Nome ou ID da cena/script/automação (ex.: 'boa noite', 'cinema'). Opcional se houver apenas um."),
                    },
                    required=["action"],
                ),
                handler=self._tool_smart_home_automate,
            )
        )

        self.register(
            ToolSpec(
                name="smart_home_events",
                description=(
                    "Lista as mudanças de estado mais recentes da casa captadas em tempo real "
                    "(ex.: quem ligou a luz, quando o ar mudou de modo). Responde 'o que acabou de acontecer?'."
                ),
                parameters=_param(
                    {
                        "limit": _int("Quantidade de eventos a devolver (padrão 10)."),
                        "domain": _str("Filtro opcional por domínio (ex.: 'light', 'switch', 'binary_sensor')."),
                    }
                ),
                handler=self._tool_smart_home_events,
            )
        )

        self.register(
            ToolSpec(
                name="system_control",
                description="Controla configurações do sistema operacional (volume, modo escuro/claro, mudo, tela de bloqueio).",
                parameters=_param(
                    {
                        "action": {
                            "type": "STRING",
                            "enum": ["volume_set", "volume_up", "volume_down", "mute", "dark_mode", "light_mode", "lock"],
                            "description": "Ação de controle do sistema.",
                        },
                        "value": _str("Valor opcional (ex: '80' para volume_set)."),
                    },
                    required=["action"],
                ),
                mutating=True,
                handler=self._tool_system_control,
            )
        )

        self.register(
            ToolSpec(
                name="get_system_info",
                description="Obtém métricas do computador em tempo real: uso de CPU, memória RAM, bateria e versão do Zorin OS.",
                parameters=_param({}),
                handler=self._tool_get_system_info,
            )
        )

        self.register(
            ToolSpec(
                name="screen_fence_control",
                description="Controla a cerca de segurança espacial e qual monitor físico está autorizado para receber cliques e automações.",
                parameters=_param(
                    {
                        "monitor": _str("Identificador do monitor ou modo ('principal', 'secundaria', 'all')."),
                    },
                    required=["monitor"],
                ),
                handler=self._tool_screen_fence_control,
            )
        )

        self.register(
            ToolSpec(
                name="move_window_to_monitor",
                description="Move ou arrasta uma janela específica ou ativa de um monitor para outro.",
                parameters=_param(
                    {
                        "target_monitor": _str("Monitor de destino ('principal', 'secundario', 'outro', '0', '1')."),
                        "window": _str("Nome ou título da janela ('current' para a ativa)."),
                    },
                    required=["target_monitor"],
                ),
                mutating=True,
                handler=self._tool_move_window_to_monitor,
            )
        )

        self.register(
            ToolSpec(
                name="window_management",
                description="Gerencia janelas abertas no desktop: alternar foco, minimizar, maximizar, restaurar, fechar ou posicionar lado a lado.",
                parameters=_param(
                    {
                        "action": {
                            "type": "STRING",
                            "enum": ["focus", "minimize", "maximize", "restore", "close", "tile_left", "tile_right"],
                            "description": "Ação desejada na janela.",
                        },
                        "window": _str("Nome, título ou identificador da janela alvo ('current' para a janela ativa)."),
                    },
                    required=["action"],
                ),
                mutating=True,
                handler=self._tool_window_management,
            )
        )

        self.register(
            ToolSpec(
                name="run_command",
                description="Executa um comando no terminal Linux (Bash) e retorna saída, erros e código de retorno. Comandos destrutivos exigem confirmação.",
                parameters=_param(
                    {
                        "command": _str("Comando de terminal bash a ser executado."),
                        "cwd": _str("Diretório de trabalho opcional."),
                        "timeout": _num("Tempo limite em segundos (padrão 15s)."),
                    },
                    required=["command"],
                ),
                mutating=True,
                handler=self._tool_run_command,
            )
        )

        self.register(
            ToolSpec(
                name="git_diff",
                description="Exibe as diferenças não commitadas (ou preparadas com --staged) no repositório Git.",
                parameters=_param(
                    {
                        "path": _str("Caminho do diretório do repositório (padrão '.')."),
                        "staged": _bool("Se true, exibe o diff das alterações preparadas (--cached/staged)."),
                        "file_path": _str("Filtrar por arquivo específico."),
                    },
                    required=[],
                ),
                handler=self._tool_git_diff,
            )
        )

        self.register(
            ToolSpec(
                name="contact_lookup",
                description="Consulta os contatos salvos pelo nome, apelido ou e-mail na memória permanente do usuário.",
                parameters=_param(
                    {"query": _str("Nome, apelido ou termo de busca do contato.")},
                    required=["query"],
                ),
                handler=self._tool_contact_lookup,
            )
        )

        self.register(
            ToolSpec(
                name="contact_save",
                description="Salva um novo contato ou atualiza informações na memória permanente do usuário.",
                parameters=_param(
                    {
                        "name": _str("Nome completo do contato."),
                        "email": _str("Endereço de e-mail."),
                        "aliases": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Apelidos ou funções."},
                        "notes": _str("Anotações adicionais."),
                    },
                    required=["name", "email"],
                ),
                mutating=True,
                handler=self._tool_contact_save,
            )
        )

        self.register(
            ToolSpec(
                name="memory_remember",
                description="Memoriza um fato ou preferência dita pelo usuário para disponibilização imediata e futura.",
                parameters=_param(
                    {
                        "fact": _str("O fato ou preferência a memorizar."),
                        "category": _str("Categoria opcional (ex: 'preferencia', 'projeto')."),
                    },
                    required=["fact"],
                ),
                mutating=True,
                handler=self._tool_memory_remember,
            )
        )

        self.register(
            ToolSpec(
                name="email_compose",
                description="Prepara e abre o cliente de e-mail (Thunderbird ou Webmail) com destinatário, assunto e corpo prontos.",
                parameters=_param(
                    {
                        "recipient": _str("Endereço de e-mail de destino."),
                        "subject": _str("Assunto do e-mail."),
                        "body": _str("Corpo da mensagem."),
                        "client": _str("Cliente de e-mail ('auto', 'gmail', 'native')."),
                    },
                    required=["recipient"],
                ),
                mutating=True,
                handler=self._tool_email_compose,
            )
        )

        self.register(
            ToolSpec(
                name="calendar_event",
                description=(
                    "Gerencia compromissos na agenda. Com o Google Agenda conectado, ele é a "
                    "fonte de verdade (criar, listar, atualizar, remover, consultar horários "
                    "livres/ocupados); sem conexão, usa o calendário local do Zorin OS."
                ),
                parameters=_param(
                    {
                        "action": {
                            "type": "STRING",
                            "enum": ["create", "list", "update", "delete", "freebusy"],
                            "description": "Ação de calendário a realizar.",
                        },
                        "title": _str("Título do compromisso (para 'create'/'update')."),
                        "datetime_str": _str(
                            "Data e hora (ex: 'amanhã às 10h'). Em 'freebusy', é o início da janela."
                        ),
                        "datetime_end": _str("Fim da janela (só em 'freebusy'; padrão: amanhã)."),
                        "duration_minutes": _int("Duração em minutos (padrão 60)."),
                        "description": _str("Descrição do evento."),
                        "location": _str("Local ou link."),
                        "event_id": _str("ID do evento (para 'update'/'delete')."),
                    },
                    required=["action"],
                ),
                mutating=True,
                handler=self._tool_calendar_event,
            )
        )

        self.register(
            ToolSpec(
                name="google_drive",
                description=(
                    "Gerencia arquivos no Google Drive (listar, buscar, enviar, baixar, criar "
                    "pasta, mover, renomear, mandar para a lixeira, apagar definitivamente). "
                    "Escopo mínimo `drive.file`: "
                    "só os arquivos que o Copilot criou ou que você abriu com ele aparecem — o "
                    "resto do Drive fica invisível. Exige o Drive conectado."
                ),
                parameters=_param(
                    {
                        "action": {
                            "type": "STRING",
                            "enum": [
                                "list",
                                "search",
                                "get",
                                "upload",
                                "download",
                                "mkdir",
                                "move",
                                "rename",
                                "trash",
                                "delete",
                            ],
                            "description": "Ação no Drive. 'trash' é recuperável; 'delete' é PERMANENTE.",
                        },
                        "query": _str("Termo de busca ('search') ou consulta `q` crua ('list')."),
                        "path": _str("Caminho local (origem em 'upload', destino em 'download')."),
                        "name": _str("Nome do arquivo no Drive ('upload') ou da pasta ('mkdir')."),
                        "new_name": _str("Novo nome (para 'rename')."),
                        "file_id": _str("ID do arquivo (download/get/move/rename/trash)."),
                        "folder_id": _str("ID da pasta (destino de 'move', pai em 'upload'/'mkdir')."),
                        "max_results": _int("Máximo de itens em 'list'/'search' (padrão 25)."),
                    },
                    required=["action"],
                ),
                mutating=True,
                handler=self._tool_google_drive,
            )
        )

        self.register(
            ToolSpec(
                name="google_gmail",
                description=(
                    "Gerencia o Gmail. Fluxo de ENVIO obrigatório: (1) 'review' para corrigir "
                    "gramática e ortografia conforme o 'context' que o usuário fornecer; "
                    "(2) mostre o texto revisado ao usuário e peça confirmação; (3) 'send' com "
                    "reviewed=true. Sem reviewed=true o envio é recusado. Também lista/lê "
                    "mensagens, cria rascunhos e marca lida/não lida. Exige o Gmail conectado."
                ),
                parameters=_param(
                    {
                        "action": {
                            "type": "STRING",
                            "enum": [
                                "list",
                                "get",
                                "labels",
                                "draft",
                                "review",
                                "send",
                                "mark_read",
                                "mark_unread",
                            ],
                            "description": "Ação no Gmail.",
                        },
                        "to": _str("Destinatário (para 'send'/'draft')."),
                        "cc": _str("Cópia, opcional (para 'send'/'draft')."),
                        "subject": _str("Assunto (para 'send'/'draft'/'review')."),
                        "body": _str("Corpo da mensagem (para 'send'/'draft'/'review')."),
                        "context": _str(
                            "Contexto para a revisão: tom, destinatário e propósito "
                            "(ex.: 'formal, para meu professor do SENAC')."
                        ),
                        "reviewed": _bool(
                            "Só 'send' com reviewed=true é aceito: confirma que o rascunho "
                            "passou por 'review' e o usuário aprovou o texto."
                        ),
                        "query": _str("Busca do Gmail (ex.: 'is:unread', 'from:banco')."),
                        "message_id": _str("ID da mensagem (get/mark_read/mark_unread)."),
                        "max_results": _int("Máximo de mensagens em 'list' (padrão 25)."),
                    },
                    required=["action"],
                ),
                mutating=True,
                handler=self._tool_google_gmail,
            )
        )

        self.register(
            ToolSpec(
                name="browser_search",
                description="Abre o navegador padrão com uma pesquisa direcionada no Google, YouTube, GitHub, Maps ou Wikipedia.",
                parameters=_param(
                    {
                        "query": _str("Termo de pesquisa."),
                        "engine": _str("Motor de busca ('google', 'youtube', 'github', 'wikipedia')."),
                    },
                    required=["query"],
                ),
                mutating=True,
                handler=self._tool_browser_search,
            )
        )

        self.register(
            ToolSpec(
                name="search_documents",
                description="Pesquisa na base local de documentos do usuário (PDFs, anotações, contratos) usando busca semântica.",
                parameters=_param(
                    {
                        "query": _str("Termo de busca ou pergunta sobre os documentos."),
                        "limit": _int("Máximo de trechos a retornar (padrão 4)."),
                    },
                    required=["query"],
                ),
                handler=self._tool_search_documents,
            )
        )

        self.register(
            ToolSpec(
                name="read_document_page",
                description="Lê o conteúdo textual de uma página específica de um documento local.",
                parameters=_param(
                    {
                        "file_path": _str("Caminho absoluto do arquivo."),
                        "page_number": _int("Número da página (padrão 1)."),
                    },
                    required=["file_path"],
                ),
                handler=self._tool_read_document_page,
            )
        )

        self.register(
            ToolSpec(
                name="open_document_file",
                description="Abre um documento local no visualizador nativo do desktop (Evince na página exata se PDF).",
                parameters=_param(
                    {
                        "file_path": _str("Caminho do arquivo local a ser aberto."),
                        "page_number": _int("Página específica para abrir (padrão 1)."),
                    },
                    required=["file_path"],
                ),
                mutating=True,
                handler=self._tool_open_document_file,
            )
        )

        self.register(
            ToolSpec(
                name="read_open_webpage",
                description="Lê o conteúdo da página ou artigo atualmente aberto no navegador ou de uma URL informada.",
                parameters=_param(
                    {"url": _str("URL específica opcional para leitura.")},
                    required=[],
                ),
                handler=self._tool_read_open_webpage,
            )
        )

        self.register(
            ToolSpec(
                name="deep_web_search",
                description=(
                    "Realiza pesquisa aprofundada na web, navegando e consolidando múltiplos "
                    "artigos com fontes. O resultado traz `synthesis`: \"llm\" quando o relatório "
                    "foi escrito por um modelo, \"offline\" quando é uma compilação de trechos "
                    "montada pelo próprio app. Em \"offline\" (ou quando `ok` for falso), "
                    "apresente o relatório como compilação de fontes e diga quantas páginas "
                    "foram lidas por inteiro (`pages_read` de `pages_total`) — não o apresente "
                    "como análise própria nem esconda que parte das fontes só teve o resumo lido."
                ),
                parameters=_param(
                    {"query": _str("Tema ou pergunta a ser pesquisada profundamente.")},
                    required=["query"],
                ),
                handler=self._tool_deep_web_search,
            )
        )

        self.register(
            ToolSpec(
                name="network_inspect",
                description=(
                    "Inspeciona a conectividade e integridade da rede do desktop: interface ativa, "
                    "endereço IP local, gateway padrão, servidores DNS, sinal Wi-Fi (se aplicável) "
                    "e latência de ping em tempo real."
                ),
                parameters=_param(
                    {"target_host": _str("Host para testar conectividade (padrão 1.1.1.1).")},
                    required=[],
                ),
                handler=self._tool_network_inspect,
            )
        )

        self.register(
            ToolSpec(
                name="lan_devices_scan",
                description=(
                    "Descobre e lista os aparelhos conectados na sub-rede local (LAN), identificando "
                    "endereço IP, fabricante da placa de rede (OUI), hostname e tipo de dispositivo "
                    "(roteador, smartphone, TV, IoT, computador)."
                ),
                parameters=_param(
                    {"active_only": _bool("Filtrar apenas aparelhos online/acessíveis recentemente.")},
                    required=[],
                ),
                handler=self._tool_lan_devices_scan,
            )
        )

        self.register(
            ToolSpec(
                name="network_discover_lan",
                description=(
                    "Descoberta ATIVA de aparelhos na sub-rede local (LAN) via varredura nmap "
                    "(host discovery). Ao contrário de 'lan_devices_scan' (que só reflete a tabela "
                    "ARP do kernel), encontra dispositivos que o computador ainda não contatou — "
                    "útil para detectar aparelhos desconhecidos. Read-only; não abre portas."
                ),
                parameters=_param(
                    {
                        "cidr": _str(
                            "Sub-rede a varrer no formato CIDR (ex.: '192.168.5.0/24'). "
                            "Omitir para usar a sub-rede local do host."
                        ),
                    },
                    required=[],
                ),
                handler=self._tool_network_discover_lan,
            )
        )

        self.register(
            ToolSpec(
                name="network_identify_device",
                description=(
                    "Identifica (fingerprint) um dispositivo específico na LAN pelo IP: sistema "
                    "operacional (nmap -O), fabricante (MAC/OUI) e serviços/portas abertas (nmap "
                    "-sV). Read-only. Útil para saber o que é um aparelho não reconhecido."
                ),
                parameters=_param(
                    {"ip": _str("Endereço IP do dispositivo a identificar (ex.: '192.168.5.12').")},
                    required=["ip"],
                ),
                handler=self._tool_network_identify_device,
            )
        )

        self.register(
            ToolSpec(
                name="network_watch_lan",
                description=(
                    "Monitora a LAN contra um baseline persistido de dispositivos conhecidos: varre "
                    "a sub-rede (nmap -sn) e alerta apenas os MACs que NÃO estavam no baseline. Na "
                    "primeira execução (baseline vazio) semeia silenciosamente os dispositivos "
                    "atuais como conhecidos, sem disparar alerta. Read-only na rede; escreve só o "
                    "estado local do assistente. NUNCA bloqueia nada — apenas notifica. O MAC é "
                    "mascarado (preserva o fabricante) na resposta."
                ),
                parameters=_param(
                    {
                        "cidr": _str(
                            "Sub-rede a monitorar no formato CIDR (ex.: '192.168.5.0/24'). "
                            "Omitir para usar a sub-rede local do host."
                        ),
                    },
                    required=[],
                ),
                handler=self._tool_network_watch_lan,
            )
        )

        self.register(
            ToolSpec(
                name="network_establish_baseline",
                description=(
                    "Estabelece (ou reestabelece) o baseline de dispositivos conhecidos a partir de "
                    "uma varredura atual da LAN: tudo o que está na rede agora vira 'conhecido' "
                    "(e confiável). Use quando o usuário quiser dizer 'verifiquei minha rede, confie "
                    "nestes'. Read-only na rede; escreve só o estado local. Não bloqueia nada."
                ),
                parameters=_param(
                    {
                        "cidr": _str(
                            "Sub-rede a varrer no formato CIDR (ex.: '192.168.5.0/24'). "
                            "Omitir para usar a sub-rede local do host."
                        ),
                    },
                    required=[],
                ),
                handler=self._tool_network_establish_baseline,
            )
        )

        self.register(
            ToolSpec(
                name="network_block_device",
                description=(
                    "Contenção defensiva de um dispositivo na LAN (o equivalente defensivo a 'atacar'): "
                    "solicita BLOQUEAR ou LIBERAR um aparelho não reconhecido no roteador. Importante: "
                    "o host NÃO derruba o tráfego localmente — o bloqueio real é delegado ao roteador "
                    "via um backend configurado (script que fala com o roteador/Home Assistant). Sem "
                    "backend configurado, NÃO executa nada e orienta o usuário. Requer confirmação "
                    "(CONFIRM) antes de qualquer ação. Prefira informar o 'ip' (o MAC real é resolvido "
                    "localmente no inventário); 'mac' completo só se você já o tiver. O MAC é mascarado "
                    "na resposta."
                ),
                parameters=_param(
                    {
                        "ip": _str(
                            "IP do dispositivo a conter (recomendado; o MAC real é resolvido no "
                            "inventário local)."
                        ),
                        "mac": _str(
                            "MAC completo do dispositivo (opcional; use apenas se já souber o MAC real)."
                        ),
                        "action": _str(
                            "Ação desejada: 'block' (bloquear) ou 'allow' (liberar/desbloquear). "
                            "Padrão: 'block'."
                        ),
                    },
                    required=[],
                ),
                handler=self._tool_network_block_device,
                mutating=True,
            )
        )

        self.register(
            ToolSpec(
                name="local_services_list",
                description=(
                    "Lista portas de rede abertas e serviços em escuta no desktop (TCP/UDP), "
                    "identificando número da porta, nome do processo e PID."
                ),
                parameters=_param(
                    {"protocol": _str("Filtrar por protocolo ('tcp', 'udp' ou omitir para ambos).")},
                    required=[],
                ),
                handler=self._tool_local_services_list,
            )
        )

        self.register(
            ToolSpec(
                name="network_wake_on_lan",
                description=(
                    "Envia pacote Wake-on-LAN (Magic Packet) para acordar ou ligar um computador "
                    "ou servidor na rede local a partir do seu endereço MAC."
                ),
                parameters=_param(
                    {
                        "mac": _str("Endereço MAC do dispositivo alvo."),
                        "broadcast_ip": _str("IP de broadcast (padrão 255.255.255.255)."),
                    },
                    required=["mac"],
                ),
                handler=self._tool_network_wake_on_lan,
                mutating=True,
            )
        )

        self.register(
            ToolSpec(
                name="network_release_port",
                description=(
                    "Encerra com segurança o processo local que está ocupando uma porta de rede "
                    "(TCP/UDP) para liberá-la. Possui proteção contra processos vitais do sistema."
                ),
                parameters=_param(
                    {
                        "port": _int("Número da porta a ser liberada (1-65535)."),
                        "force": _bool("Se true, envia SIGKILL caso o processo recuse SIGTERM."),
                    },
                    required=["port"],
                ),
                handler=self._tool_network_release_port,
                mutating=True,
            )
        )

        self.register(
            ToolSpec(
                name="network_confirm_port",
                description=(
                    "Verifica (somente leitura) se uma porta de rede está livre ou ainda ocupada, "
                    "confirmando se o processo que escutava nela foi de fato encerrado. "
                    "Suporta IPv4 e IPv6."
                ),
                parameters=_param(
                    {
                        "port": _int("Número da porta a verificar (1-65535)."),
                        "protocol": _str("Protocolo ('tcp' ou 'udp'; padrão 'tcp')."),
                    },
                    required=["port"],
                ),
                handler=self._tool_network_confirm_port,
                mutating=False,
            )
        )

        self.register(
            ToolSpec(
                name="network_probe_service",
                description=(
                    "Testa conectividade direta (handshake TCP) com um serviço ou porta em um dispositivo "
                    "da rede local ou internet (ex: Home Assistant na 8123, SSH na 22)."
                ),
                parameters=_param(
                    {
                        "host": _str("Endereço IP ou hostname do serviço alvo."),
                        "port": _int("Porta de rede do serviço."),
                    },
                    required=["host", "port"],
                ),
                handler=self._tool_network_probe_service,
                mutating=False,
            )
        )

        self.register(
            ToolSpec(
                name="network_dns_benchmark",
                description=(
                    "Compara o tempo de resposta (latência em ms) entre o servidor DNS atual do sistema "
                    "e provedores de alto desempenho (Cloudflare 1.1.1.1, Google 8.8.8.8, Quad9 9.9.9.9)."
                ),
                parameters=_param(
                    {"domain": _str("Domínio opcional para teste de resolução.")},
                    required=[],
                ),
                handler=self._tool_network_dns_benchmark,
                mutating=False,
            )
        )

        self.register(
            ToolSpec(
                name="network_reconnect",
                description=(
                    "Reconecta ou reaplica a conexão de rede ativa usando NetworkManager Polkit para "
                    "recuperar conectividade em caso de travamento."
                ),
                parameters=_param(
                    {"interface": _str("Nome da interface opcional (ex: enp4s0, wlan0).")},
                    required=[],
                ),
                handler=self._tool_network_reconnect,
                mutating=True,
            )
        )

        self.register(
            ToolSpec(
                name="network_audit_exposure",
                description=(
                    "Audita todas as portas em escuta classificando-as entre Loopback Seguro (127.0.0.1) "
                    "e Exposição na LAN (0.0.0.0 / ::), alertando sobre servidores dev ou bancos sem proteção."
                ),
                parameters=_param({}, required=[]),
                handler=self._tool_network_audit_exposure,
                mutating=False,
            )
        )

        self.register(
            ToolSpec(
                name="network_throughput",
                description=(
                    "Mede a taxa instantânea de download e upload (KB/s e MB/s) na interface de rede "
                    "lendo estatísticas de kernel sem overhead de CPU."
                ),
                parameters=_param(
                    {
                        "interface": _str("Nome opcional da interface (padrão: rota default)."),
                        "sample_seconds": _num("Janela de amostragem em segundos (padrão: 0.4s)."),
                    },
                    required=[],
                ),
                handler=self._tool_network_throughput,
                mutating=False,
            )
        )

        self.register(
            ToolSpec(
                name="network_diagnose_path",
                description=(
                    "Executa diagnóstico de conexão em 3 etapas (Enlace local -> Roteador gateway -> "
                    "Internet externa e DNS) apontando com precisão onde ocorreu a queda."
                ),
                parameters=_param(
                    {"target": _str("Host externo de destino para teste (padrão: 1.1.1.1).")},
                    required=[],
                ),
                handler=self._tool_network_diagnose_path,
                mutating=False,
            )
        )

        self.register(
            ToolSpec(
                name="network_ssl_check",
                description=(
                    "Inspeciona o certificado SSL/TLS de um domínio ou IP (porta 443), extraindo validade, "
                    "dias restantes, emissor e versão TLS (com proteção anti-SYN-blackhole de 2.5s)."
                ),
                parameters=_param(
                    {
                        "host": _str("Nome de domínio ou endereço IP a verificar (ex: cloudflare.com)."),
                        "port": _int("Porta HTTPS (padrão: 443)."),
                    },
                    required=["host"],
                ),
                handler=self._tool_network_ssl_check,
                mutating=False,
            )
        )

        self.register(
            ToolSpec(
                name="network_mdns_services",
                description=(
                    "Descobre passivamente serviços e dispositivos anunciados na LAN via mDNS / Avahi "
                    "(Home Assistant, impressoras de rede, receptores Google Cast)."
                ),
                parameters=_param(
                    {"timeout_sec": _num("Tempo de escuta em segundos (padrão: 2.0s).")},
                    required=[],
                ),
                handler=self._tool_network_mdns_services,
                mutating=False,
            )
        )

        self.register(
            ToolSpec(
                name="docker_list_containers",
                description=(
                    "Lista containers Docker locais com status operacional, saúde, portas e consumo de recursos."
                ),
                parameters=_param(
                    {"all": _bool("Se True, inclui containers parados. Padrão True.")},
                    required=[],
                ),
                handler=self._tool_docker_list_containers,
                mutating=False,
            )
        )

        self.register(
            ToolSpec(
                name="docker_container_logs",
                description=(
                    "Obtém logs destilados de um container Docker, eliminando ruído repetitivo, "
                    "destacando erros críticos e ofuscando segredos/tokens sensíveis."
                ),
                parameters=_param(
                    {
                        "container": _str("Nome ou ID do container Docker."),
                        "tail": _int("Quantidade de linhas recentes a analisar (padrão 50)."),
                        "filter_errors_only": _bool("Se True, retorna exclusivamente mensagens de erro."),
                    },
                    required=["container"],
                ),
                handler=self._tool_docker_container_logs,
                mutating=False,
            )
        )

        self.register(
            ToolSpec(
                name="docker_container_action",
                description=(
                    "Executa ação de ciclo de vida em um container Docker ('restart' ou 'stop') "
                    "com verificação de estado pós-ação (RiskLevel.CONFIRM)."
                ),
                parameters=_param(
                    {
                        "container": _str("Nome ou ID do container Docker."),
                        "action": _str("Ação a executar: 'restart' ou 'stop'."),
                    },
                    required=["container", "action"],
                ),
                handler=self._tool_docker_container_action,
                mutating=True,
            )
        )

        self.register(
            ToolSpec(
                name="system_hardware_health",
                description=(
                    "Inspeciona a saúde física do hardware: temperaturas de CPU/GPU/NVMe (lm_sensors), "
                    "pressão de memória PSI/zRAM e status de desgaste de SSD via S.M.A.R.T. (com proteção de spin-down)."
                ),
                parameters=_param({}, required=[]),
                handler=self._tool_system_hardware_health,
                mutating=False,
            )
        )

        self.register(
            ToolSpec(
                name="system_journal_errors",
                description=(
                    "Extrai os últimos erros críticos (priority err..emerg) do journalctl do systemd "
                    "com agrupamento heurístico por serviço e deduplicação de mensagens em loop."
                ),
                parameters=_param(
                    {"lines": _int("Número de registros do diário a inspecionar (padrão 40).")},
                    required=[],
                ),
                handler=self._tool_system_journal_errors,
                mutating=False,
            )
        )

        self.register(
            ToolSpec(
                name="scratchpad_write",
                description=(
                    "Grava ou atualiza uma anotação na memória de trabalho (Scratchpad) da sessão atual. "
                    "Use para guardar dados, trechos, números ou URLs extraídos de uma janela ou pesquisa "
                    "antes de alternar para outro aplicativo, evitando truncamento de histórico."
                ),
                parameters=_param(
                    {
                        "key": _str("Identificador da nota (ex: 'dados_pesquisa', 'tabela_custos')."),
                        "content": _str("Conteúdo textual ou estruturado a salvar."),
                        "append": _bool("Se True, concatena ao conteúdo já existente na chave (padrão False)."),
                    },
                    required=["key", "content"],
                ),
                handler=self._tool_scratchpad_write,
                mutating=False,
            )
        )

        self.register(
            ToolSpec(
                name="scratchpad_read",
                description=(
                    "Lê notas salvas na memória de trabalho (Scratchpad) da sessão atual "
                    "(uma chave específica ou todo o bloco de notas)."
                ),
                parameters=_param(
                    {"key": _str("Chave opcional a consultar. Omita para listar todas as notas salvas.")},
                    required=[],
                ),
                handler=self._tool_scratchpad_read,
                mutating=False,
            )
        )

        self.register(
            ToolSpec(
                name="scratchpad_clear",
                description=(
                    "Remove uma chave específica ou limpa toda a memória de trabalho (Scratchpad) da sessão."
                ),
                parameters=_param(
                    {"key": _str("Chave opcional a remover. Omita para limpar todo o Scratchpad.")},
                    required=[],
                ),
                handler=self._tool_scratchpad_clear,
                mutating=False,
            )
        )

        self.register(
            ToolSpec(
                name="inspect_window",
                description=(
                    "Localiza uma janela aberta pelo nome do app ou título, opcionalmente foca nela "
                    "e extrai imediatamente sua árvore de acessibilidade (AT-SPI) e geometria em um único passo."
                ),
                parameters=_param(
                    {
                        "query": _str("Nome do aplicativo, trecho do título ou ID da janela."),
                        "focus": _bool("Se True, foca na janela antes de inspecionar (padrão True)."),
                        "max_depth": _num("Profundidade máxima da árvore AT-SPI (padrão 4)."),
                        "include_bounds": _bool("Se True, inclui coordenadas (x, y, w, h) de cada elemento."),
                    },
                    required=["query"],
                ),
                handler=self._tool_inspect_window,
                mutating=False,
            )
        )

        self.register(
            ToolSpec(
                name="plan_update",
                description=(
                    "Cria ou atualiza o checklist de marcos (sub-objetivos) de uma tarefa multipassos "
                    "e marca etapas concluídas ou em andamento."
                ),
                parameters=_param(
                    {
                        "steps": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"},
                            "description": "Lista ordenada de marcos/etapas do plano (ao criar ou redefinir o plano).",
                        },
                        "completed_step": _int("Índice 1-based da etapa que acabou de ser concluída."),
                        "current_step": _int("Índice 1-based da etapa atualmente em execução."),
                    },
                    required=[],
                ),
                handler=self._tool_plan_update,
                mutating=False,
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

        # 1. Escala relativa explícita [0.0..1.0 ou 0..1000] ou fração [0.0, 1.0] sem flag
        is_rel = args.get("is_relative")
        is_norm = is_rel is True or (is_rel is not False and 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0)
        if is_norm:
            norm_x = x / 1000.0 if x > 1.0 else x
            norm_y = y / 1000.0 if y > 1.0 else y
            ok, message = driver.click_relative(
                norm_x,
                norm_y,
                button=str(args.get("button") or "left"),
                double=bool(args.get("double")),
            )
            return {"ok": bool(ok), "message": message}

        fence = getattr(driver, "fence", None) or getattr(self, "fence", None)
        active_m = fence.get_active_monitor() if (fence and hasattr(fence, "get_active_monitor")) else None
        # 2. Pixel relativo ao monitor ativo (com offset espacial se o monitor começar em x > 0)
        if active_m and getattr(active_m, "x", 0) > 0:
            contains_fn = getattr(active_m, "contains", None)
            if contains_fn and not contains_fn(int(x), int(y)):
                w = getattr(active_m, "width", 0)
                h = getattr(active_m, "height", 0)
                if 0 <= x <= w and 0 <= y <= h:
                    x = active_m.x + int(x)
                    y = getattr(active_m, "y", 0) + int(y)

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

    def _tool_click_and_type(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            x = float(args.get("x"))
            y = float(args.get("y"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "x e y precisam ser números."}
        text = str(args.get("text") or "")
        if not text:
            return {"ok": False, "error": "`text` é obrigatório."}

        driver = self.input_driver
        if driver is None:
            return {"ok": False, "error": "Driver de entrada indisponível (ydotool/uinput ausentes)."}

        is_rel = args.get("is_relative")
        is_norm = is_rel is True or (is_rel is not False and 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0)

        if is_norm:
            norm_x = x / 1000.0 if x > 1.0 else x
            norm_y = y / 1000.0 if y > 1.0 else y
            click_ok, click_msg = driver.click_relative(norm_x, norm_y, button="left")
        else:
            abs_x, abs_y = int(x), int(y)
            allowed, reason = self.check_coordinate(abs_x, abs_y)
            if not allowed:
                return {
                    "ok": False,
                    "error": f"Clique bloqueado pela cerca digital: {reason}",
                    "blocked_by": "fence",
                }
            click_ok, click_msg = driver.click(abs_x, abs_y, button="left")

        if not click_ok:
            return {"ok": False, "error": f"Falha ao focar o campo: {click_msg}"}

        time.sleep(0.08)

        if bool(args.get("clear_first")):
            driver.hotkey("ctrl", "a")
            time.sleep(0.02)
            driver.hotkey("backspace")
            time.sleep(0.02)

        type_ok, type_msg = driver.type_text(text, press_enter=bool(args.get("press_enter")))
        return {"ok": bool(type_ok), "message": f"Campo focado e texto digitado: {type_msg}"}

    def _tool_find_on_screen(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "`query` é obrigatório."}
        mode = str(args.get("mode") or "auto").lower()
        threshold = float(args.get("confidence_threshold", 0.65))
        try:
            from ..core.ui_grounding import UIGroundingService

            # 1. Se modo permitir OCR, busca texto/rótulos rápidos
            candidates = []
            if mode != "vlm":
                candidates = UIGroundingService.find_elements(query, fence=self._fence, threshold=threshold)
                if candidates:
                    return {
                        "ok": True,
                        "count": len(candidates),
                        "source": "ocr",
                        "best_match": candidates[0][0].to_dict(),
                        "score": round(candidates[0][1], 2),
                        "all_matches": [c[0].to_dict() for c in candidates[:5]],
                    }

            # 2. Se OCR não localizou ou modo é 'vlm', aciona Grounding VLM
            if mode in ("auto", "vlm"):
                vlm_el = UIGroundingService.ground_visual_element(query, fence=self._fence)
                if vlm_el:
                    return {
                        "ok": True,
                        "count": 1,
                        "source": "vision_model",
                        "best_match": vlm_el.to_dict(),
                        "score": round(vlm_el.confidence / 100.0, 2),
                        "all_matches": [vlm_el.to_dict()],
                    }

            return {"ok": False, "error": f"Nenhum elemento correspondente a '{query}' na tela."}
        except Exception as exc:
            return {"ok": False, "error": f"Falha na localização visual: {exc}"}

    def _tool_click_on_screen(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "`query` é obrigatório."}
        button = str(args.get("button") or "left")
        double = bool(args.get("double", False))
        prefer_vlm = bool(args.get("prefer_vlm", False))
        driver = self.input_driver
        if driver is None:
            return {"ok": False, "error": "Driver de entrada indisponível."}
        try:
            from ..core.ui_grounding import UIGroundingService
            ok, msg, coords = UIGroundingService.click_visual_element(
                query,
                button=button,
                double=double,
                driver=driver,
                fence=self._fence,
                prefer_vlm=prefer_vlm,
            )
            return {
                "ok": ok,
                "message": msg,
                "coords": coords,
                "query": query,
            }
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao clicar visualmente: {exc}"}

    def _tool_click_visual_element(self, args: dict[str, Any]) -> dict[str, Any]:
        # Para elementos descritos visualmente, prioriza VLM por padrão se não especificado o contrário
        if "prefer_vlm" not in args:
            args = dict(args)
            args["prefer_vlm"] = True
        return self._tool_click_on_screen(args)

    def _tool_scroll_page(self, args: dict[str, Any]) -> dict[str, Any]:
        direction = str(args.get("direction") or "down").lower().strip()
        amount = int(args.get("amount") or 3)
        driver = self.input_driver
        if driver is None:
            return {"ok": False, "error": "Driver de entrada indisponível."}
        try:
            cx, cy = None, None
            if self._fence:
                bounds = self._fence.get_effective_bounds()
                if bounds:
                    cx = bounds[0] + bounds[2] // 2
                    cy = bounds[1] + bounds[3] // 2
            lbl = f"Rolando ({'para baixo' if direction == 'down' else 'para cima'})"
            ok, msg = driver.scroll(x=cx, y=cy, direction=direction, amount=amount, label=lbl)
            return {"ok": ok, "message": msg, "direction": direction, "amount": amount}
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao rolar página: {exc}"}

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
                self._focus_app_window(query)
                return {"ok": True, "message": f"'{query}' iniciado via '{binary}'.", "binary": binary}
            except Exception as exc:
                return {"ok": False, "error": f"Falha ao iniciar '{binary}': {exc}"}

        ok, launch_message = AppManager.launch(app)
        if ok:
            self._focus_app_window(query)
        return {"ok": bool(ok), "message": launch_message, "app": app.get_name() if hasattr(app, "get_name") else query}

    def _tool_list_open_windows(self, _args: dict[str, Any]) -> dict[str, Any]:
        try:
            from ..core.window_manager import WindowManager

            windows = WindowManager.list_windows(exclude_copilot=True)
            entries = [
                {
                    "id": w.id,
                    "app": w.app,
                    "title": w.title,
                    "bounds": {"x": w.x, "y": w.y, "width": w.width, "height": w.height},
                    "is_active": w.is_active,
                    "display_name": w.display_name(),
                }
                for w in windows
            ]
            return {"ok": True, "count": len(entries), "windows": entries}
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao listar janelas abertas: {exc}"}

    def _tool_focus_window(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "`query` é obrigatório."}
        try:
            from ..core.window_manager import WindowManager

            win = WindowManager.find_window(query)
            target_id = win.id if win else query
            ok = WindowManager.focus_window(target_id)
            if win and self._fence is not None and hasattr(self._fence, "set_chosen_window"):
                self._fence.set_chosen_window(win)
            if ok:
                name = win.display_name() if win else query
                return {"ok": True, "message": f"Janela '{name}' focada com sucesso.", "window": name}
            return {"ok": False, "error": f"Não foi possível focar a janela '{query}'. Verifique se está aberta."}
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao focar janela: {exc}"}

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

        multi_page = bool(args.get("multi_page", False))
        max_pages = int(args.get("max_pages") or 5)
        advance_by = str(args.get("advance_by") or "auto").strip().lower()

        try:
            if multi_page and hasattr(capture, "capture_multipage"):
                result = capture.capture_multipage(
                    str(args.get("app") or "").strip() or None,
                    strategy=strategy,
                    max_pages=max_pages,
                    advance_by=advance_by,
                )
            else:
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
            "multi_page": multi_page,
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

    def _tool_study_generate_deck(self, args: dict[str, Any]) -> dict[str, Any]:
        """Gera flashcards com repetição espaçada SM-2 a partir de arquivo de estudo (.md) e opcionalmente exporta .apkg."""
        source = str(args.get("source") or "").strip()
        if not source:
            return {"ok": False, "error": "`source` é obrigatório."}

        source = os.path.expanduser(source)
        if not os.path.exists(source):
            return {"ok": False, "error": f"Arquivo não encontrado: {source}"}

        try:
            from ..core.study_cards import (
                DeckStore,
                StudyAI,
                generate_cards,
                merge_cards,
                new_deck,
                read_discipline,
                read_source,
            )

            discipline = (args.get("discipline") or "").strip() or read_discipline(source) or "Estudos"
            title, body = read_source(source)
            raw_max = args.get("max_cards")
            try:
                max_cards = int(raw_max) if raw_max not in (None, "") else 10
            except (TypeError, ValueError):
                max_cards = 10

            ai = StudyAI(mode="auto")
            cards, warnings = generate_cards(
                body,
                ai,
                title=title or os.path.basename(source),
                discipline=discipline,
                n_cards=max_cards,
            )

            if not cards:
                return {
                    "ok": False,
                    "error": "Nenhum card gerado a partir do material.",
                    "warnings": list(warnings),
                }

            store = DeckStore()
            deck = new_deck(
                title or os.path.basename(source),
                cards,
                source=source,
                discipline=discipline,
                provider=ai.used,
            )
            existing = store.load(deck.id)
            if existing is not None and existing.cards:
                deck.cards = merge_cards(existing.cards, cards)
                deck.created_at = existing.created_at

            deck_path = store.save(deck)

            export_apkg = args.get("export_apkg") is not False
            apkg_path = None
            if export_apkg:
                out_apkg = str(args.get("out_apkg") or "").strip() or None
                apkg_path = store.export_apkg(deck.id, out_apkg)

            return {
                "ok": True,
                "deck_id": deck.id,
                "title": deck.title,
                "discipline": deck.discipline,
                "cards_count": len(deck.cards),
                "deck_path": deck_path,
                "apkg_path": apkg_path,
                "provider": ai.used,
                "warnings": list(warnings),
            }
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao gerar deck de estudo: {exc}"}

    def _tool_study_summarize(self, args: dict[str, Any]) -> dict[str, Any]:
        """Gera resumo estruturado e glossário de termos a partir de material Markdown."""
        source = str(args.get("source") or "").strip()
        if not source:
            return {"ok": False, "error": "`source` é obrigatório."}

        source = os.path.expanduser(source)
        if not os.path.exists(source):
            return {"ok": False, "error": f"Arquivo não encontrado: {source}"}

        try:
            from ..core.study_cards import read_discipline, read_source
            from ..core.study_summary import build_default_ai, generate_summary, save as save_summary

            discipline = (args.get("discipline") or "").strip() or read_discipline(source) or "Estudos"
            title, body = read_source(source)
            raw_topics = args.get("max_topics")
            raw_terms = args.get("max_terms")
            try:
                max_topics = int(raw_topics) if raw_topics not in (None, "") else 8
            except (TypeError, ValueError):
                max_topics = 8
            try:
                max_terms = int(raw_terms) if raw_terms not in (None, "") else 12
            except (TypeError, ValueError):
                max_terms = 12

            ai = build_default_ai("auto")
            summary, warnings = generate_summary(
                body,
                ai,
                title=title or os.path.basename(source),
                discipline=discipline,
                max_topics=max_topics,
                max_terms=max_terms,
            )
            summary.source = source

            if not summary.topics and not summary.glossary:
                return {
                    "ok": False,
                    "error": "Nenhum conteúdo aproveitável encontrado para resumir.",
                    "warnings": list(warnings),
                }

            saved_path = None
            if args.get("save") is not False:
                target = os.path.join(
                    os.path.dirname(source), f"resumo-{os.path.basename(source)}"
                )
                saved_path = save_summary(summary, target)

            return {
                "ok": True,
                "title": summary.title,
                "discipline": summary.discipline,
                "topics_count": len(summary.topics),
                "glossary_count": len(summary.glossary),
                "topics": summary.topics,
                "glossary": summary.glossary,
                "path": saved_path,
                "provider": summary.provider or ai.used,
                "warnings": list(warnings),
            }
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao gerar resumo: {exc}"}

    def _tool_study_export_anki(self, args: dict[str, Any]) -> dict[str, Any]:
        """Exporta um baralho existente para o formato nativo do Anki (.apkg ou .tsv)."""
        deck_id = str(args.get("deck_id") or "").strip()
        if not deck_id:
            return {"ok": False, "error": "`deck_id` é obrigatório."}

        fmt = str(args.get("format") or "apkg").strip().lower()
        out_path = str(args.get("output_path") or "").strip() or None

        try:
            from ..core.study_cards import DeckStore

            store = DeckStore()
            if fmt == "tsv":
                exported_path = store.export_tsv(deck_id, out_path)
            else:
                exported_path = store.export_apkg(deck_id, out_path)

            return {
                "ok": True,
                "deck_id": deck_id,
                "format": fmt,
                "output_path": exported_path,
            }
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao exportar baralho para Anki: {exc}"}

    def _tool_study_show_board(self, args: dict[str, Any]) -> dict[str, Any]:
        """Abre o Quadro Negro com o raciocínio estruturado de um problema.

        A resolução é pura (`math_solver.try_solve`) e roda aqui; a abertura do
        quadro é delegada ao gancho `board_opener` (a UI injeta). Sem gancho —
        CLI headless, teste — devolve uma mensagem graciosa em vez de quebrar.
        """
        problem = str(args.get("problem") or "").strip()
        focus = str(args.get("focus") or args.get("method") or "").strip()

        graph = None
        if problem:
            try:
                from .math_solver import try_solve

                graph = try_solve(problem)
            except Exception as exc:  # pragma: no cover - defensivo
                return {"ok": False, "error": f"Falha ao resolver o problema: {exc}"}

        opener = getattr(self, "board_opener", None)
        if opener is None:
            return {
                "ok": False,
                "error": (
                    "O Quadro Negro não está disponível nesta interface. "
                    "Diga ao usuário para abrir pela resposta no chat."
                ),
            }
        try:
            # Contrato do gancho: (problema, grafo, foco). O grafo pode ser None
            # (álgebra pura não casou); a UI decide escalar para o modelo.
            result = opener(problem, graph, focus)
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao abrir o Quadro Negro: {exc}"}

        # O desfecho vem do gancho, não de uma presunção: `ok: True` fixo aqui
        # dizia ao modelo que o quadro tinha aberto mesmo quando nada abria (sem
        # provedor de IA configurado, por exemplo), e ele então repetia isso ao
        # usuário com confiança.
        outcome = outcome_from_payload(result)
        payload: dict[str, Any] = {
            "ok": outcome.accepted,
            "opened": outcome.opened,
            "pending": outcome.pending,
            "status": outcome.status,
            "has_graph": graph is not None,
            "message": outcome.message or message_for(outcome.status, focus),
        }
        if not outcome.accepted:
            payload["error"] = outcome.detail or outcome.message
        return payload

    def _tool_run_subagent(self, args: dict[str, Any]) -> dict[str, Any]:
        """Delega a tarefa a um subagente especializado (gancho `subagent_runner`).

        O registro não sabe montar um pipeline — não conhece provedor nem
        roteador. Ele valida o pedido contra o **catálogo** (que é puro e
        importável de qualquer lugar) e repassa ao gancho injetado pela camada
        que tem as peças. Sem gancho — CLI headless, teste — devolve uma
        mensagem graciosa em vez de quebrar.
        """
        specialist = str(args.get("specialist") or args.get("name") or "").strip().lower()
        data = str(args.get("input") or args.get("data") or args.get("task") or "").strip()

        from .subagents import CATALOG, catalog_names

        if not specialist:
            return {
                "ok": False,
                "error": (
                    "Informe o especialista. Disponíveis: " + ", ".join(catalog_names()) + "."
                ),
            }
        if specialist not in CATALOG:
            return {
                "ok": False,
                "error": (
                    f"Especialista desconhecido: '{specialist}'. "
                    "Disponíveis: " + ", ".join(catalog_names()) + "."
                ),
            }
        if not data:
            return {
                "ok": False,
                "error": "Informe a tarefa (`input`) que o subagente deve executar.",
            }

        runner = getattr(self, "subagent_runner", None)
        if runner is None:
            return {
                "ok": False,
                "error": (
                    "Subagentes não estão disponíveis nesta interface. "
                    "Execute a tarefa você mesmo, passo a passo."
                ),
            }

        target_window = str(args.get("target_window") or "").strip()
        scratchpad_key = str(args.get("scratchpad_key") or "").strip()

        try:
            import inspect

            sig = inspect.signature(runner)
            params = sig.parameters
            accepts_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
            call_kwargs: dict[str, Any] = {}
            if target_window and ("target_window" in params or accepts_kwargs):
                call_kwargs["target_window"] = target_window
            if scratchpad_key and ("scratchpad_key" in params or accepts_kwargs):
                call_kwargs["scratchpad_key"] = scratchpad_key
            result = runner(specialist, data, **call_kwargs)
        except Exception as exc:  # nenhum gancho pode derrubar o loop
            logger.exception("Subagente '%s' falhou", specialist)
            return {"ok": False, "error": f"Falha ao executar o subagente: {exc}"}

        if not isinstance(result, dict):
            # Sem relatório legível não há como afirmar sucesso — mesma regra do
            # `board_opener`: não saber o desfecho não é o mesmo que ter dado certo.
            return {
                "ok": False,
                "error": "O subagente não devolveu um relatório legível.",
            }
        # O subagente devolve um relatório; o loop precisa dele como observação
        # legível, não como um dict aninhado que o modelo teria de decifrar.
        if "ok" not in result:
            # Gancho que não informa o desfecho não é promovido a sucesso. O
            # `PipelineResult` real sempre traz `ok`, então isto só vale para um
            # gancho de outra implementação — e presumir ali é como o defeito nasce.
            result["ok"] = False
            result.setdefault("error", "O subagente não informou se concluiu a tarefa.")
        result.setdefault("specialist", specialist)
        if target_window:
            result.setdefault("target_window", target_window)
        if result.get("ok"):
            report_text = str(result.get("report") or result.get("output") or "").strip()
            if report_text:
                self._scratchpad[f"subagent_{specialist}"] = report_text
                if scratchpad_key:
                    self._scratchpad[scratchpad_key] = report_text
                result.setdefault("scratchpad_key", scratchpad_key or f"subagent_{specialist}")
        return result

    def _tool_academic_search(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "`query` é obrigatório."}
        source = str(args.get("source") or "all").strip()
        scope = str(args.get("scope") or "auto").strip()
        limit = int(args.get("limit") or 5)
        try:
            from ..core.academic_hub import AcademicHub

            hub = AcademicHub()
            docs = hub.search(query, scope=scope, source=source, limit=limit)
            entries = [
                {
                    "title": d.title,
                    "source": d.source,
                    "year": d.year,
                    "authors": d.authors,
                    "abstract": d.abstract,
                    "abnt_citation": d.abnt_citation,
                    "file_path_or_url": d.file_path_or_url,
                    "is_local": d.is_local,
                    "page_number": d.page_number,
                }
                for d in docs
            ]
            return {
                "ok": True,
                "count": len(entries),
                "scope": scope,
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

    def _focus_browser_window(self) -> None:
        """Tenta trazer a janela do navegador para o foco (Wayland/X11)."""
        time.sleep(0.3)
        try:
            from ..core.window_manager import WindowManager

            for b in ("Google-chrome", "chrome", "firefox", "brave", "chromium"):
                win = WindowManager.find_window(b)
                if win and WindowManager.focus_window(win.id):
                    if self._fence is not None and hasattr(self._fence, "set_chosen_window"):
                        self._fence.set_chosen_window(win)
                    return
        except Exception:
            pass
        if shutil.which("hyprctl"):
            try:
                for cls_name in ("class:Google-chrome", "class:firefox", "class:Brave-browser", "class:chromium"):
                    proc = subprocess.run(
                        ["hyprctl", "dispatch", "focuswindow", cls_name],
                        capture_output=True,
                        text=True,
                        timeout=0.4,
                        check=False,
                    )
                    if proc.returncode == 0:
                        break
            except Exception as exc:
                logger.debug("hyprctl focuswindow browser falhou: %s", exc)

    def _focus_app_window(self, query: str) -> None:
        """Tenta focar a janela do aplicativo recém-aberto no Wayland/X11."""
        if not query:
            return
        try:
            from ..core.window_manager import WindowManager

            win = WindowManager.find_window(query)
            if win and WindowManager.focus_window(win.id):
                if self._fence is not None and hasattr(self._fence, "set_chosen_window"):
                    self._fence.set_chosen_window(win)
                return
        except Exception:
            pass
        if shutil.which("hyprctl"):
            try:
                clean = re.sub(r"[^\w\-]", "", query)
                if clean:
                    subprocess.run(
                        ["hyprctl", "dispatch", "focuswindow", f"class:{clean}"],
                        capture_output=True,
                        text=True,
                        timeout=0.4,
                        check=False,
                    )
            except Exception:
                pass

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
                self._focus_browser_window()
                return {"ok": True, "message": f"URL '{url}' aberta no navegador.", "url": url}
        except Exception:
            pass
        try:
            import subprocess

            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._focus_browser_window()
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

    def _tool_git_log(self, args: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(args.get("path") or ".").strip()
        target_path = os.path.abspath(os.path.expanduser(raw_path))
        if not os.path.exists(target_path):
            return {
                "ok": False,
                "error": f"Caminho não encontrado: '{raw_path}'",
                "suggestion": "Verifique o caminho do diretório ou arquivo do repositório.",
            }

        work_dir = target_path if os.path.isdir(target_path) else os.path.dirname(target_path)

        try:
            max_count = int(args.get("max_count") or 10)
        except (ValueError, TypeError):
            max_count = 10
        max_count = max(1, min(max_count, 50))

        cmd = ["git", "-c", "safe.directory=*", "log", f"-n{max_count}", "--pretty=format:%h %ad | %s (%an)", "--date=short"]

        rev_range = (args.get("revision_range") or "").strip()
        if rev_range:
            cmd.append(rev_range)

        file_filter = (args.get("file_path") or "").strip()
        if not file_filter and os.path.isfile(target_path):
            file_filter = os.path.basename(target_path)

        if file_filter:
            cmd.extend(["--", file_filter])

        try:
            proc = subprocess.run(
                cmd,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=8.0,
                check=False,
            )
        except FileNotFoundError:
            return {
                "ok": False,
                "error": "O executável 'git' não está instalado ou disponível no PATH do sistema.",
                "suggestion": "Instale o pacote git no sistema usando o gerenciador de pacotes.",
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "timeout": True,
                "error": f"A consulta de git log excedeu o limite de 8.0s em '{work_dir}'.",
                "suggestion": (
                    "O repositório Git existe e está acessível, mas a consulta ao histórico demorou muito. "
                    "Tente reduzir 'max_count' para 5 ou especificar um arquivo em 'file_path'."
                ),
            }

        if proc.returncode != 0:
            err_msg = proc.stderr.strip() or f"git log encerrou com código {proc.returncode}"
            return {
                "ok": False,
                "success": False,
                "error": err_msg,
                "message": f"Erro no Git: {err_msg}",
                "suggestion": (
                    "Verifique se o caminho especificado pertence a um repositório Git válido "
                    "e se a branch ou revisão existe."
                ),
            }

        output = proc.stdout.strip()
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        repo_name = os.path.basename(work_dir)
        msg = f"Encontrados {len(lines)} commits em '{repo_name}':\n{output}" if lines else f"Nenhum commit encontrado em '{repo_name}'."
        return {
            "ok": True,
            "success": True,
            "path": work_dir,
            "count": len(lines),
            "commits": lines,
            "summary": output or "(Nenhum commit encontrado para os critérios fornecidos)",
            "message": msg,
        }

    def _tool_git_status(self, args: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(args.get("path") or ".").strip()
        target_path = os.path.abspath(os.path.expanduser(raw_path))
        if not os.path.exists(target_path):
            return {
                "ok": False,
                "success": False,
                "error": f"Caminho não encontrado: '{raw_path}'",
                "message": f"Caminho não encontrado: '{raw_path}'",
                "suggestion": "Verifique o caminho do diretório do repositório.",
            }

        work_dir = target_path if os.path.isdir(target_path) else os.path.dirname(target_path)

        cmd = ["git", "-c", "safe.directory=*", "status", "--short", "--branch"]
        try:
            proc = subprocess.run(
                cmd,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
        except FileNotFoundError:
            return {
                "ok": False,
                "success": False,
                "error": "O executável 'git' não está instalado ou disponível no PATH do sistema.",
                "message": "Git não está instalado no sistema.",
                "suggestion": "Instale o pacote git no sistema usando o gerenciador de pacotes.",
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "success": False,
                "timeout": True,
                "error": f"A consulta de git status excedeu o limite de 5.0s em '{work_dir}'.",
                "message": "Tempo limite de 5s excedido no git status.",
                "suggestion": (
                    "O repositório Git é extenso ou há concorrência de I/O. "
                    "Tente novamente em instantes."
                ),
            }

        if proc.returncode != 0:
            err_msg = proc.stderr.strip() or f"git status encerrou com código {proc.returncode}"
            return {
                "ok": False,
                "success": False,
                "error": err_msg,
                "message": f"Erro no Git: {err_msg}",
                "suggestion": "Verifique se o caminho pertence a um repositório Git válido.",
            }

        output = proc.stdout.strip()
        clean_status = output or "## (working tree clean, sem alterações pendentes)"
        repo_name = os.path.basename(work_dir)
        msg = f"Status Git em '{repo_name}':\n{clean_status}"
        return {
            "ok": True,
            "success": True,
            "path": work_dir,
            "status": clean_status,
            "message": msg,
        }

    def _tool_search_code(self, args: dict[str, Any]) -> dict[str, Any]:
        pattern = str(args.get("pattern") or "").strip()
        if not pattern:
            return {
                "ok": False,
                "success": False,
                "error": "O parâmetro 'pattern' é obrigatório para a busca de código.",
                "message": "Parâmetro 'pattern' não fornecido.",
                "suggestion": "Especifique o termo ou expressão regular que deseja buscar.",
            }

        path = str(args.get("path") or ".").strip()
        file_glob = str(args.get("file_glob") or "").strip()
        case_sensitive = bool(args.get("case_sensitive", False))
        is_regex = bool(args.get("is_regex", True))
        max_results = int(args.get("max_results") or 30)
        context_lines = int(args.get("context_lines") or 1)

        try:
            from ..core.system_inspect import CodeSearchEngine
            engine = CodeSearchEngine.get_default()
            res = engine.search(
                pattern=pattern,
                path=path,
                file_glob=file_glob,
                case_sensitive=case_sensitive,
                is_regex=is_regex,
                max_results=max_results,
                context_lines=context_lines,
            )
            return res.to_dict()
        except FileNotFoundError as fnf:
            return {
                "ok": False,
                "success": False,
                "error": str(fnf),
                "message": str(fnf),
                "suggestion": "Verifique se o caminho do diretório ou arquivo existe.",
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "success": False,
                "timeout": True,
                "error": f"A busca de código por '{pattern}' excedeu o limite de tempo em '{path}'.",
                "message": f"Timeout na busca por '{pattern}'.",
                "suggestion": "Especifique um subdiretório mais restrito ou use 'file_glob' (ex: '*.py').",
            }
        except Exception as exc:
            return {
                "ok": False,
                "success": False,
                "error": f"Falha na busca de código: {exc}",
                "message": f"Erro na busca de código: {exc}",
                "suggestion": "Revise os parâmetros da busca e a sintaxe da expressão regular.",
            }

    def _tool_system_processes(self, args: dict[str, Any]) -> dict[str, Any]:
        filter_query = str(args.get("filter") or "").strip()
        sort_by = str(args.get("sort_by") or "cpu").strip().lower()
        if sort_by not in ("cpu", "memory", "pid"):
            sort_by = "cpu"
        limit = int(args.get("limit") or 20)

        try:
            from ..core.system_inspect import ProcessInspector
            inspector = ProcessInspector.get_default()
            procs = inspector.list_processes(
                filter_query=filter_query,
                sort_by=sort_by,
                limit=limit,
            )
            table_summary = inspector.format_table(procs)
            return {
                "ok": True,
                "success": True,
                "count": len(procs),
                "filter": filter_query,
                "sort_by": sort_by,
                "processes": [p.to_dict() for p in procs],
                "summary": table_summary,
                "message": f"Listados {len(procs)} processos no sistema.",
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "success": False,
                "timeout": True,
                "error": "A listagem de processos excedeu o limite de tempo.",
                "message": "Timeout ao inspecionar processos.",
                "suggestion": "Tente novamente ou aplique um filtro específico.",
            }
        except Exception as exc:
            return {
                "ok": False,
                "success": False,
                "error": f"Falha ao listar processos: {exc}",
                "message": f"Erro ao listar processos: {exc}",
            }

    def _tool_media_control(self, args: dict[str, Any]) -> dict[str, Any]:
        from ..core.media import MediaPlayerManager
        act = str(args.get("action", "play_pause"))
        player = args.get("player")
        query = args.get("query")
        ok, msg = MediaPlayerManager.control(act, player_name=player, query=query)
        return {"ok": ok, "message": msg}

    def _tool_vscode_workspace(self, args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action", "")).strip().lower()
        if self.dry_run and action in ("create_project", "write_code", "patch_code"):
            return {
                "ok": True,
                "dry_run": True,
                "tool": "vscode_workspace",
                "would_do": f"Executaria ação '{action}' no VS Code com argumentos: {args}",
            }

        try:
            from ..core.vscode import VSCodeManager
            from pathlib import Path

            if action == "get_active_project":
                ws = VSCodeManager.get_active_workspace()
                if ws:
                    return {
                        "ok": True,
                        "success": True,
                        "workspace_path": str(ws),
                        "project_name": ws.name,
                        "message": f"Projeto ativo no VS Code: '{ws.name}' ({ws}).",
                    }
                return {
                    "ok": False,
                    "success": False,
                    "error": "Nenhum workspace ativo encontrado no VS Code.",
                    "message": "Nenhum workspace ativo encontrado no VS Code.",
                }

            elif action == "open_workspace":
                path = args.get("folder_path") or args.get("project_name", "")
                res = VSCodeManager.open_workspace(path)
                res["ok"] = bool(res.get("success", False))
                return res

            elif action == "create_project":
                pname = args.get("project_name", "novo_projeto")
                tmpl = args.get("template", "python")
                bdir = args.get("folder_path")
                res = VSCodeManager.create_project(pname, template=tmpl, base_dir=bdir)
                res["ok"] = bool(res.get("success", False))
                return res

            elif action == "write_code":
                fpath = args.get("file_path", "")
                content = args.get("code_content", "")
                line = int(args.get("line_number", 1))
                if fpath:
                    try:
                        self._push_file_snapshot(fpath)
                    except Exception:
                        pass
                res = VSCodeManager.write_code_file(fpath, content, line=line)
                res["ok"] = bool(res.get("success", False))
                return res

            elif action == "patch_code":
                fpath = args.get("file_path", "")
                target = args.get("target_code", "")
                replacement = args.get("replacement_code", "")
                if fpath:
                    try:
                        self._push_file_snapshot(fpath)
                    except Exception:
                        pass
                res = VSCodeManager.patch_code_file(fpath, target, replacement)
                res["ok"] = bool(res.get("success", False))
                return res

            elif action == "read_file":
                fpath = args.get("file_path", "")
                res = VSCodeManager.read_code_file(fpath)
                res["ok"] = bool(res.get("success", False))
                return res

            elif action == "get_structure":
                wpath = args.get("folder_path")
                res = VSCodeManager.read_workspace_structure(Path(wpath) if wpath else None)
                res["ok"] = bool(res.get("success", False))
                return res

            elif action == "open_file":
                fpath = args.get("file_path", "")
                line = int(args.get("line_number", 1))
                res = VSCodeManager.open_file(fpath, line=line)
                res["ok"] = bool(res.get("success", False))
                return res

            return {
                "ok": False,
                "success": False,
                "error": f"Ação de desenvolvimento desconhecida: '{action}'",
                "message": f"Ação de desenvolvimento desconhecida: '{action}'",
            }
        except Exception as exc:
            logger.exception("Falha ao executar vscode_workspace: %s", exc)
            return {
                "ok": False,
                "success": False,
                "error": f"Erro interno ao operar VS Code: {exc}",
                "message": f"Erro interno ao operar VS Code: {exc}",
            }

    def _tool_smart_home_control(self, args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "turn_on").strip().lower()
        dev_type = str(args.get("device_type") or "").strip().lower()
        entity = str(args.get("entity") or "").strip()

        if self.dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "tool": "smart_home_control",
                "would_do": f"Executaria ação '{action}' no dispositivo '{entity or 'padrão'}' ({dev_type or 'light'}).",
            }

        try:
            from ..core.home_assistant import HomeAssistantManager

            ha = HomeAssistantManager.get_default()

            is_light = dev_type == "light" or any(w in entity.lower() for w in ("luz", "lampada", "lâmpada", "iluminação", "avant", "led"))
            is_climate = dev_type == "climate" or any(w in entity.lower() for w in ("ar", "clima", "temperatura", "ar condicionado"))

            if is_light or (not dev_type and not is_climate):
                res = ha.control_light(
                    entity=entity,
                    action=action,
                    brightness=args.get("brightness"),
                    color_temp=args.get("color_temp"),
                    color=args.get("color"),
                )
            elif is_climate:
                res = ha.control_climate(
                    entity=entity,
                    action=action,
                    temperature=args.get("temperature"),
                    hvac_mode=args.get("hvac_mode"),
                )
            else:
                res = ha.control_switch(entity=entity, action=action)

            res["ok"] = bool(res.get("success", False))
            return res
        except Exception as exc:
            logger.exception("Falha ao controlar casa inteligente: %s", exc)
            return {"ok": False, "error": f"Falha ao comunicar com Home Assistant: {exc}"}

    def _tool_smart_home_status(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            from ..core.home_assistant import HomeAssistantManager

            ha = HomeAssistantManager.get_default()
            query = str(args.get("query") or "").strip()
            dev_type = args.get("device_type")
            domain = None if dev_type in (None, "all", "") else dev_type
            res = ha.get_status(entity_or_query=query, domain=domain)
            res["ok"] = bool(res.get("success", False))
            return res
        except Exception as exc:
            logger.exception("Falha ao consultar estado da casa inteligente: %s", exc)
            return {"ok": False, "error": f"Falha ao consultar Home Assistant: {exc}"}

    def _tool_smart_home_automate(self, args: dict[str, Any]) -> dict[str, Any]:
        """Aciona cenas/scripts/automações ou lista os disponíveis."""
        action = str(args.get("action") or "").strip().lower()
        name = str(args.get("name") or "").strip()
        try:
            from ..core.home_assistant import HomeAssistantManager

            ha = HomeAssistantManager.get_default()
            bloqueio = ha.unavailable()
            if bloqueio:
                bloqueio["ok"] = False
                return bloqueio

            if action == "list_scenes":
                items = ha.list_scenes()
                return self._format_automation_list(items, "cena", "cenas")
            if action == "list_scripts":
                items = ha.list_scripts()
                return self._format_automation_list(items, "script", "scripts")
            if action == "list_automations":
                items = ha.list_automations()
                return self._format_automation_list(items, "automação", "automações")

            if action == "trigger_scene":
                res = ha.trigger_scene(scene=name)
            elif action == "run_script":
                res = ha.run_script(script=name)
            elif action == "trigger_automation":
                res = ha.trigger_automation(automation=name)
            else:
                return {
                    "ok": False,
                    "error": (
                        f"Ação '{action}' inválida. Use trigger_scene, run_script, trigger_automation "
                        "ou list_scenes/list_scripts/list_automations."
                    ),
                }
            res["ok"] = bool(res.get("success", False))
            return res
        except Exception as exc:
            logger.exception("Falha ao acionar cena/script/automação: %s", exc)
            return {"ok": False, "error": f"Falha ao acionar automação no Home Assistant: {exc}"}

    @staticmethod
    def _format_automation_list(items: list[dict[str, Any]], singular: str, plural: str) -> dict[str, Any]:
        if not items:
            return {"ok": True, "success": True, "count": 0, "items": [], "message": f"Nenhuma {singular} configurada no Home Assistant."}
        names = [f"{it.get('friendly_name')} ({it.get('entity_id')})" for it in items]
        return {
            "ok": True,
            "success": True,
            "count": len(items),
            "items": items,
            "message": f"{len(items)} {plural} disponíveis: " + "; ".join(names) + ".",
        }

    def _tool_smart_home_events(self, args: dict[str, Any]) -> dict[str, Any]:
        """Lista as mudanças de estado recentes captadas em tempo real."""
        try:
            from ..core.home_assistant import HomeAssistantManager

            ha = HomeAssistantManager.get_default()
            bloqueio = ha.unavailable()
            if bloqueio:
                bloqueio["ok"] = False
                return bloqueio

            limit = int(args.get("limit") or 10)
            domain = str(args.get("domain") or "").strip() or None
            events = ha.recent_events(limit=limit)
            if domain:
                events = [e for e in events if e.get("domain") == domain]

            if not events:
                status = ha.realtime_status()
                hint = (
                    " O canal de eventos em tempo real não está ativo — habilite-o para começar a captar mudanças."
                    if not status.get("running")
                    else " Nenhuma mudança de estado registrada ainda."
                )
                return {"ok": True, "success": True, "count": 0, "events": [], "message": hint.strip()}

            lines = []
            for e in events:
                lines.append(
                    f"{e.get('friendly_name')}: {e.get('old_state')} → {e.get('state')}"
                )
            return {
                "ok": True,
                "success": True,
                "count": len(events),
                "events": events,
                "message": "Últimas mudanças de estado:\n• " + "\n• ".join(lines),
            }
        except Exception as exc:
            logger.exception("Falha ao listar eventos da casa inteligente: %s", exc)
            return {"ok": False, "error": f"Falha ao consultar eventos do Home Assistant: {exc}"}

    def _tool_system_control(self, args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "").strip().lower()
        value = str(args.get("value") or "").strip()
        from ..shell.system import SystemController

        if action in ("dark_mode", "modo_escuro"):
            ok, msg = SystemController.set_color_scheme(True)
            return {"ok": ok, "success": ok, "message": msg}
        elif action in ("light_mode", "modo_claro"):
            ok, msg = SystemController.set_color_scheme(False)
            return {"ok": ok, "success": ok, "message": msg}
        elif action in ("mute", "mudo"):
            ok, msg = SystemController.adjust_volume("mute")
            return {"ok": ok, "success": ok, "message": msg}
        elif action in ("volume_up", "aumentar_volume"):
            ok, msg = SystemController.adjust_volume("up")
            return {"ok": ok, "success": ok, "message": msg}
        elif action in ("volume_down", "diminuir_volume"):
            ok, msg = SystemController.adjust_volume("down")
            return {"ok": ok, "success": ok, "message": msg}
        elif action in ("volume_set", "definir_volume"):
            import shutil
            wpctl = shutil.which("wpctl")
            val_num = "".join(c for c in value if c.isdigit())
            if wpctl and val_num:
                target_fraction = f"{float(val_num) / 100.0:.2f}"
                subprocess.run([wpctl, "set-volume", "@DEFAULT_AUDIO_SINK@", target_fraction], check=False)
                return {"ok": True, "success": True, "message": f"Volume definido para {val_num}%."}
            ok, msg = SystemController.adjust_volume("up")
            return {"ok": ok, "success": ok, "message": f"Volume ajustado para {value or 'padrão'}."}
        elif action in ("lock", "bloquear"):
            ok, msg = SystemController.lock_session()
            return {"ok": ok, "success": ok, "message": msg}
        return {"ok": False, "success": False, "error": f"Ação de controle do sistema desconhecida: '{action}'"}

    def _tool_get_system_info(self, args: dict[str, Any]) -> dict[str, Any]:
        profile = self.memory.get_system_profile() if self.memory else {}
        import psutil
        vm = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.05)
        os_name = profile.get("os_name", "Zorin OS")
        ram_used = round((vm.total - vm.available) / (1024 ** 3), 1)
        ram_total = round(vm.total / (1024 ** 3), 1)
        battery = profile.get("battery_status", "AC Conectado")
        msg = f"Sistema: {os_name} | CPU: {cpu}% | RAM: {ram_used}GB de {ram_total}GB | Bateria: {battery}"
        return {
            "ok": True,
            "success": True,
            "os": os_name,
            "cpu_usage_percent": cpu,
            "ram_used_gb": ram_used,
            "ram_total_gb": ram_total,
            "battery": battery,
            "message": msg,
        }

    def _tool_screen_fence_control(self, args: dict[str, Any]) -> dict[str, Any]:
        monitor = str(args.get("monitor") or "principal").strip()
        fence = self.fence
        if fence is None:
            return {"ok": False, "success": False, "error": "Cerca de tela indisponível no ambiente."}
        ok = fence.set_active_monitor(monitor)
        m = fence.get_active_monitor()
        name_str = m.name if m else monitor
        msg = f"Cerca de tela definida para o monitor '{name_str}'." if ok else f"Monitor '{monitor}' não localizado."
        return {"ok": ok, "success": ok, "monitor": name_str, "message": msg}

    def _tool_move_window_to_monitor(self, args: dict[str, Any]) -> dict[str, Any]:
        target_mon = str(args.get("target_monitor") or "outro").strip()
        window_query = str(args.get("window") or "current").strip()
        from ..core.window_manager import WindowManager
        res = WindowManager.move_windows(window_query, target_mon, drag_visual=True)
        if isinstance(res, dict):
            res.setdefault("ok", res.get("success", True))
        return res

    def _tool_window_management(self, args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "focus").strip().lower()
        win_query = str(args.get("window") or "current").strip()
        from ..core.window_manager import WindowManager

        target_win = None
        if win_query in ("current", "active", "ativa", "atual", ""):
            target_win = WindowManager.get_active_or_last_window()
        else:
            target_win = WindowManager.find_window(win_query)

        if action == "focus":
            if not target_win:
                return {"ok": False, "success": False, "error": f"Janela '{win_query}' não encontrada para focar."}
            ok = WindowManager.focus_window(target_win.id)
            msg = f"Foco alterado para '{target_win.display_name()}'." if ok else f"Falha ao focar janela '{win_query}'."
            return {"ok": ok, "success": ok, "message": msg, "window": target_win.display_name()}

        import shutil
        if action == "close":
            if shutil.which("hyprctl") and target_win:
                proc = subprocess.run(["hyprctl", "dispatch", "closewindow", f"address:{target_win.id}"], capture_output=True, check=False)
                ok = proc.returncode == 0
                return {"ok": ok, "success": ok, "message": f"Janela '{target_win.display_name()}' fechada." if ok else "Falha ao fechar janela."}
            elif shutil.which("wmctrl") and target_win:
                subprocess.run(["wmctrl", "-c", target_win.title or target_win.app], capture_output=True, check=False)
                return {"ok": True, "success": True, "message": f"Comando de fechar enviado para '{target_win.display_name()}'."}
            elif shutil.which("xdotool") and target_win:
                subprocess.run(["xdotool", "windowclose", target_win.id], capture_output=True, check=False)
                return {"ok": True, "success": True, "message": f"Janela '{target_win.display_name()}' fechada via xdotool."}
            return {"ok": False, "success": False, "error": "Compositor sem suporte para fechar janela programaticamente."}

        if action in ("maximize", "restore", "minimize"):
            if shutil.which("hyprctl"):
                if action == "maximize":
                    subprocess.run(["hyprctl", "dispatch", "fullscreen", "1"], capture_output=True, check=False)
                elif action == "restore":
                    subprocess.run(["hyprctl", "dispatch", "fullscreen", "0"], capture_output=True, check=False)
                elif action == "minimize" and target_win:
                    subprocess.run(["hyprctl", "dispatch", "movetoworkspacesilent", f"special:minimized,address:{target_win.id}"], capture_output=True, check=False)
                return {"ok": True, "success": True, "message": f"Ação '{action}' aplicada na janela."}
            elif shutil.which("wmctrl") and target_win:
                flag = "add" if action == "maximize" else "remove"
                subprocess.run(["wmctrl", "-r", target_win.title or target_win.app, "-b", f"{flag},maximized_vert,maximized_horz"], capture_output=True, check=False)
                return {"ok": True, "success": True, "message": f"Ação '{action}' aplicada via wmctrl."}
            return {"ok": True, "success": True, "message": f"Ação '{action}' solicitada."}

        if action in ("tile_left", "tile_right"):
            if shutil.which("hyprctl"):
                dir_key = "l" if action == "tile_left" else "r"
                subprocess.run(["hyprctl", "dispatch", "movewindow", dir_key], capture_output=True, check=False)
                return {"ok": True, "success": True, "message": f"Janela posicionada para {'esquerda' if dir_key == 'l' else 'direita'}."}
            return {"ok": True, "success": True, "message": f"Ação '{action}' solicitada."}

        return {"ok": False, "success": False, "error": f"Ação '{action}' não suportada para gerenciamento de janelas."}

    def _tool_run_command(self, args: dict[str, Any]) -> dict[str, Any]:
        command = str(args.get("command") or "").strip()
        if not command:
            return {"ok": False, "success": False, "error": "Nenhum comando fornecido."}

        raw_cwd = args.get("cwd")
        work_dir = os.path.abspath(os.path.expanduser(raw_cwd)) if raw_cwd else None
        if work_dir and not os.path.isdir(work_dir):
            return {
                "ok": False,
                "success": False,
                "error": f"Diretório de trabalho não encontrado: '{raw_cwd}'",
            }

        try:
            timeout = float(args.get("timeout") or 15.0)
        except (ValueError, TypeError):
            timeout = 15.0
        timeout = max(1.0, min(timeout, 60.0))

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            output = proc.stdout.strip()
            err = proc.stderr.strip()
            is_ok = proc.returncode == 0
            msg = output if is_ok else (err or f"Comando encerrou com código {proc.returncode}")
            return {
                "ok": is_ok,
                "success": is_ok,
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "output": output or err,
                "message": msg or f"Comando executado (código {proc.returncode}).",
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "success": False,
                "timeout": True,
                "error": f"O comando excedeu o limite de {timeout:.1f}s.",
                "message": f"Tempo limite de {timeout:.1f}s excedido.",
            }
        except Exception as exc:
            return {
                "ok": False,
                "success": False,
                "error": f"Falha ao executar comando: {exc}",
                "message": f"Erro de execução: {exc}",
            }

    def _tool_git_diff(self, args: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(args.get("path") or ".").strip()
        target_path = os.path.abspath(os.path.expanduser(raw_path))
        if not os.path.exists(target_path):
            return {
                "ok": False,
                "success": False,
                "error": f"Caminho não encontrado: '{raw_path}'",
                "message": f"Caminho não encontrado: '{raw_path}'",
            }
        work_dir = target_path if os.path.isdir(target_path) else os.path.dirname(target_path)
        cmd = ["git", "-c", "safe.directory=*", "diff"]
        if args.get("staged"):
            cmd.append("--cached")
        fpath = (args.get("file_path") or "").strip()
        if fpath:
            cmd.extend(["--", fpath])
        try:
            proc = subprocess.run(
                cmd,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=8.0,
                check=False,
            )
        except Exception as exc:
            return {"ok": False, "success": False, "error": str(exc), "message": f"Erro: {exc}"}
        if proc.returncode != 0:
            err = proc.stderr.strip() or f"git diff retornou {proc.returncode}"
            return {"ok": False, "success": False, "error": err, "message": err}
        out = proc.stdout.strip()
        return {
            "ok": True,
            "success": True,
            "path": work_dir,
            "diff": out or "(Nenhuma diferença detectada)",
            "message": out[:1000] if out else "Nenhuma alteração detectada.",
        }

    def _tool_contact_lookup(self, args: dict[str, Any]) -> dict[str, Any]:
        q = str(args.get("query") or "").strip()
        if not self.memory:
            return {"ok": False, "success": False, "error": "Gerenciador de memória indisponível."}
        contacts = self.memory.find_contact(q)
        formatted = [
            {"name": c["name"], "email": c["email"], "aliases": c.get("aliases", []), "notes": c.get("notes", "")}
            for c in contacts
        ]
        if formatted:
            return {
                "ok": True,
                "success": True,
                "contacts": formatted,
                "message": f"{len(formatted)} contato(s) localizado(s).",
            }
        return {
            "ok": False,
            "success": False,
            "contacts": [],
            "message": f"Nenhum contato encontrado para '{q}'.",
        }

    def _tool_contact_save(self, args: dict[str, Any]) -> dict[str, Any]:
        c_name = str(args.get("name") or "").strip()
        c_email = str(args.get("email") or "").strip()
        c_aliases = args.get("aliases") or []
        c_notes = str(args.get("notes") or "").strip()
        if not self.memory:
            return {"ok": False, "success": False, "error": "Gerenciador de memória indisponível."}
        saved = self.memory.save_contact(name=c_name, email=c_email, aliases=c_aliases, notes=c_notes)
        return {
            "ok": True,
            "success": True,
            "contact": saved,
            "message": f"Contato '{c_name}' <{c_email}> salvo com sucesso.",
        }

    def _tool_memory_remember(self, args: dict[str, Any]) -> dict[str, Any]:
        fact = str(args.get("fact") or "").strip()
        if not fact:
            return {"ok": False, "success": False, "error": "Nenhum fato informado para memorizar."}
        category = str(args.get("category") or "").strip() or "preferencia"
        if not self.memory:
            return {"ok": False, "success": False, "error": "Gerenciador de memória indisponível."}
        key = self.memory.fact_key(fact)
        self.memory.save_fact(key, fact, category=category, source="tool_registry")
        return {
            "ok": True,
            "success": True,
            "message": f"Fato memorizado com sucesso: '{fact}'.",
        }

    def _tool_email_compose(self, args: dict[str, Any]) -> dict[str, Any]:
        from ..core.email import EmailManager
        recip = str(args.get("recipient") or "").strip()
        subj = str(args.get("subject") or "").strip()
        body = str(args.get("body") or "").strip()
        client = str(args.get("client") or "auto").strip()
        mgr = EmailManager(memory=self.memory)
        ok, msg, data = mgr.compose(recip, subject=subj, body=body, client=client)
        return {"ok": ok, "success": ok, "message": msg, "details": data}

    def _google_service_active(self, service: str) -> bool:
        """O serviço Google está habilitado E conectado?

        A regra de "habilitado" é **importada** de `google_auth.service_enabled` —
        a mesma que a tela de preferências usa. Uma cópia aqui divergiria em
        silêncio e o agente recusaria um serviço que a UI mostra como ligado.

        Conservador de propósito: qualquer falha devolve `False`. Para a agenda
        isso significa cair no calendário local (que funciona sempre) em vez de
        estourar no meio de um comando de voz.
        """
        try:
            from ..core import google_auth
            from ..core.config import CopilotConfig

            cfg = CopilotConfig.load()
            if not google_auth.service_enabled(
                service,
                bool(getattr(cfg, "google_enabled", False)),
                getattr(cfg, "google_enabled_services", None),
            ):
                return False
            return google_auth.is_connected(service)
        except Exception as exc:
            logger.debug("Checagem do Google (%s) falhou: %s", service, exc)
            return False

    def _google_calendar_active(self) -> bool:
        """Atalho para a agenda (legibilidade e os testes existentes)."""
        return self._google_service_active("calendar")

    def _mirror_calendar(self, events: list[dict[str, Any]]) -> None:
        """Espelha no cache local. Falha do cache NUNCA derruba a operação real."""
        try:
            from ..core.calendar import CalendarManager

            CalendarManager(memory=self.memory).mirror_events(events)
        except Exception as exc:
            logger.debug("Falha ao espelhar eventos no cache local: %s", exc)

    def _tool_calendar_event(self, args: dict[str, Any]) -> dict[str, Any]:
        """Gerencia a agenda. Com o Google conectado, ele é a **fonte de verdade**."""
        act = str(args.get("action") or "create").strip().lower()
        if self._google_calendar_active():
            return self._calendar_event_google(act, args)
        return self._calendar_event_local(act, args)

    def _calendar_event_google(self, act: str, args: dict[str, Any]) -> dict[str, Any]:
        from ..core import google_auth
        from ..core import google_calendar as gcal

        def _int_or(value: Any, default: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        try:
            if act == "create":
                event = gcal.create_event(
                    title=str(args.get("title") or "").strip(),
                    start=str(args.get("datetime_str") or "amanhã às 10h").strip(),
                    duration_minutes=_int_or(args.get("duration_minutes"), 60),
                    description=str(args.get("description") or "").strip(),
                    location=str(args.get("location") or "").strip(),
                )
                self._mirror_calendar([event])
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "message": f"Compromisso '{event['title']}' criado na Google Agenda.",
                    "event": event,
                }
            if act == "list":
                events = gcal.list_events(str(args.get("datetime_str") or "today").strip())
                self._mirror_calendar(events)
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "events": events,
                    "count": len(events),
                    "message": f"{len(events)} compromisso(s) na Google Agenda.",
                }
            if act == "update":
                kwargs: dict[str, Any] = {}
                if args.get("title") is not None:
                    kwargs["title"] = str(args.get("title"))
                if args.get("datetime_str") is not None:
                    kwargs["start"] = str(args.get("datetime_str"))
                    kwargs["duration_minutes"] = _int_or(args.get("duration_minutes"), 60)
                if args.get("description") is not None:
                    kwargs["description"] = str(args.get("description"))
                if args.get("location") is not None:
                    kwargs["location"] = str(args.get("location"))
                event = gcal.update_event(str(args.get("event_id") or "").strip(), **kwargs)
                self._mirror_calendar([event])
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "event": event,
                    "message": f"Compromisso '{event['title']}' atualizado na Google Agenda.",
                }
            if act == "delete":
                event_id = str(args.get("event_id") or "").strip()
                gcal.delete_event(event_id)
                try:
                    from ..core.calendar import CalendarManager

                    CalendarManager(memory=self.memory).delete_event(event_id)
                except Exception as exc:
                    logger.debug("Falha ao remover do cache local: %s", exc)
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "message": f"Compromisso {event_id} removido da Google Agenda.",
                }
            if act == "freebusy":
                busy = gcal.freebusy(
                    str(args.get("datetime_str") or "hoje").strip(),
                    str(args.get("datetime_end") or "amanhã").strip(),
                )
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "busy": busy,
                    "count": len(busy),
                    "message": f"{len(busy)} intervalo(s) ocupado(s) na Google Agenda.",
                }
            return {"ok": False, "success": False, "error": f"Ação de calendário desconhecida: '{act}'"}
        except google_auth.GoogleAuthError as exc:
            return {"ok": False, "success": False, "error": str(exc)}

    def _calendar_event_local(self, act: str, args: dict[str, Any]) -> dict[str, Any]:
        from ..core.calendar import CalendarManager

        mgr = CalendarManager(memory=self.memory)
        if act == "create":
            title = str(args.get("title") or "").strip()
            dt_str = str(args.get("datetime_str") or "amanhã às 10h").strip()
            try:
                duration = int(args.get("duration_minutes") or 60)
            except (ValueError, TypeError):
                duration = 60
            description = str(args.get("description") or "").strip()
            location = str(args.get("location") or "").strip()
            ok, msg, data = mgr.create_event(
                title, dt_str, duration_minutes=duration, description=description, location=location
            )
            return {"ok": ok, "success": ok, "source": "local", "message": msg, "event": data}
        if act == "list":
            day = str(args.get("datetime_str") or "today").strip()
            events = mgr.list_events(day)
            return {
                "ok": True,
                "success": True,
                "source": "local",
                "events": events,
                "count": len(events),
                "message": f"{len(events)} compromisso(s) encontrado(s).",
            }
        if act == "delete":
            event_id = str(args.get("event_id") or "").strip()
            ok = mgr.delete_event(event_id)
            msg = f"Compromisso {event_id} removido." if ok else "Evento não encontrado."
            return {"ok": ok, "success": ok, "source": "local", "message": msg}
        if act in ("update", "freebusy"):
            # Honestidade: o calendário local não tem esses dados. Melhor dizer
            # por que não dá do que devolver uma lista vazia que parece sucesso.
            return {
                "ok": False,
                "success": False,
                "source": "local",
                "error": (
                    f"'{act}' exige o Google Agenda conectado (o calendário local não "
                    "guarda eventos da nuvem). Conecte com: "
                    "zorin-copilot-cli google connect calendar"
                ),
            }
        return {"ok": False, "success": False, "error": f"Ação de calendário desconhecida: '{act}'"}

    def _tool_google_drive(self, args: dict[str, Any]) -> dict[str, Any]:
        """Gerencia arquivos no Google Drive. Exige o serviço conectado.

        Não há fallback local: o Drive é uma nuvem, e fingir um caminho local
        seria mentir para o modelo. Sem conexão, o erro é explícito e acionável.
        """
        from ..core import google_auth
        from ..core import google_drive as gdrive

        act = str(args.get("action") or "list").strip().lower()

        if not google_auth.google_libs_available():
            return {
                "ok": False,
                "success": False,
                "error": (
                    "As bibliotecas do Google não estão instaladas. "
                    "Instale com: pip install 'zorin-copilot[google]'"
                ),
            }
        if not self._google_service_active("drive"):
            return {
                "ok": False,
                "success": False,
                "error": (
                    "O Google Drive não está conectado. "
                    "Conecte com: zorin-copilot-cli google connect drive"
                ),
            }

        def _limit() -> int:
            try:
                return max(1, int(args.get("max_results") or 25))
            except (TypeError, ValueError):
                return 25

        try:
            if act in ("list", "search"):
                if act == "search":
                    files = gdrive.search(str(args.get("query") or ""), max_results=_limit())
                else:
                    files = gdrive.list_files(
                        query=(args.get("query") or None), max_results=_limit()
                    )
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "files": files,
                    "count": len(files),
                    "message": f"{len(files)} arquivo(s) no Google Drive.",
                }
            if act == "get":
                info = gdrive.get_file(str(args.get("file_id") or ""))
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "file": info,
                    "message": f"Arquivo '{info['name']}'.",
                }
            if act == "upload":
                info = gdrive.upload(
                    str(args.get("path") or ""),
                    name=(args.get("name") or None),
                    folder_id=(args.get("folder_id") or None),
                )
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "file": info,
                    "message": f"Arquivo '{info['name']}' enviado ao Drive.",
                }
            if act == "download":
                dest = gdrive.download(
                    str(args.get("file_id") or ""), str(args.get("path") or "")
                )
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "path": dest,
                    "message": f"Arquivo baixado para {dest}.",
                }
            if act == "mkdir":
                info = gdrive.create_folder(
                    str(args.get("name") or ""), parent_id=(args.get("folder_id") or None)
                )
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "file": info,
                    "message": f"Pasta '{info['name']}' criada no Drive.",
                }
            if act == "move":
                info = gdrive.move(
                    str(args.get("file_id") or ""), str(args.get("folder_id") or "")
                )
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "file": info,
                    "message": f"'{info['name']}' movido no Drive.",
                }
            if act == "rename":
                info = gdrive.rename(
                    str(args.get("file_id") or ""), str(args.get("new_name") or "")
                )
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "file": info,
                    "message": f"Arquivo renomeado para '{info['name']}'.",
                }
            if act == "trash":
                file_id = str(args.get("file_id") or "").strip()
                gdrive.trash(file_id)
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "message": f"Arquivo {file_id} movido para a lixeira do Drive.",
                }
            if act == "delete":
                file_id = str(args.get("file_id") or "").strip()
                gdrive.delete_permanently(file_id)
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "message": f"Arquivo {file_id} apagado DEFINITIVAMENTE do Drive.",
                }
            return {"ok": False, "success": False, "error": f"Ação de Drive desconhecida: '{act}'"}
        except google_auth.GoogleAuthError as exc:
            return {"ok": False, "success": False, "error": str(exc)}

    def _tool_google_gmail(self, args: dict[str, Any]) -> dict[str, Any]:
        """Gerencia o Gmail. Envio exige revisão prévia e confirmação do usuário.

        A ordem exigida é: `review` → mostrar ao usuário → ele confirma → `send`
        com `reviewed=true`. O `reviewed=true` é **exigido em código**, não só
        pedido no texto da ferramenta: sem ele, o envio é recusado.
        """
        from ..core import email_review
        from ..core import google_auth
        from ..core import google_gmail as gmail

        act = str(args.get("action") or "list").strip().lower()

        # `review` não fala com o Gmail — só com o provedor de IA. Por isso vem
        # ANTES da checagem de conexão: revisar não depende do Gmail conectado.
        if act == "review":
            result = email_review.review_draft(
                str(args.get("subject") or ""),
                str(args.get("body") or ""),
                context=str(args.get("context") or ""),
            )
            return {
                "ok": True,
                "success": True,
                "reviewed": result.reviewed,
                "subject": result.subject,
                "body": result.body,
                "changes": result.changes,
                "issues": result.issues,
                "note": result.note,
                "message": (
                    f"Revisão concluída: {len(result.changes)} correção(ões) sugerida(s)."
                    if result.reviewed
                    else result.note
                ),
            }

        if not google_auth.google_libs_available():
            return {
                "ok": False,
                "success": False,
                "error": (
                    "As bibliotecas do Google não estão instaladas. "
                    "Instale com: pip install 'zorin-copilot[google]'"
                ),
            }
        if not self._google_service_active("gmail"):
            return {
                "ok": False,
                "success": False,
                "error": (
                    "O Gmail não está conectado. "
                    "Conecte com: zorin-copilot-cli google connect gmail"
                ),
            }

        def _limit() -> int:
            try:
                return max(1, int(args.get("max_results") or 25))
            except (TypeError, ValueError):
                return 25

        try:
            if act == "list":
                messages = gmail.list_messages(
                    query=(args.get("query") or None), max_results=_limit()
                )
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "messages": messages,
                    "count": len(messages),
                    "message": f"{len(messages)} mensagem(ns) encontrada(s).",
                }
            if act == "get":
                info = gmail.get_message(str(args.get("message_id") or ""))
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "message_info": info,
                    "message": f"Assunto: '{info['subject']}' — de {info['from']}.",
                }
            if act == "labels":
                labels = gmail.list_labels()
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "labels": labels,
                    "count": len(labels),
                    "message": f"{len(labels)} rótulo(s).",
                }
            if act == "draft":
                draft = gmail.create_draft(
                    str(args.get("to") or ""),
                    str(args.get("subject") or ""),
                    str(args.get("body") or ""),
                    cc=(args.get("cc") or None),
                )
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "draft": draft,
                    "message": "Rascunho criado no Gmail (não enviado).",
                }
            if act == "send":
                # Etapa de revisão exigida em CÓDIGO, não só no texto da ferramenta.
                if not args.get("reviewed"):
                    return {
                        "ok": False,
                        "success": False,
                        "error": (
                            "Envio bloqueado: revise o rascunho primeiro "
                            "(action='review' com o 'context'), mostre o texto ao usuário, "
                            "e só depois de ele confirmar chame 'send' com reviewed=true."
                        ),
                    }
                sent = gmail.send_message(
                    str(args.get("to") or ""),
                    str(args.get("subject") or ""),
                    str(args.get("body") or ""),
                    cc=(args.get("cc") or None),
                )
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "sent": sent,
                    "message": f"E-mail enviado para {args.get('to')}.",
                }
            if act in ("mark_read", "mark_unread"):
                message_id = str(args.get("message_id") or "").strip()
                if act == "mark_read":
                    result = gmail.mark_read(message_id)
                    text = "marcada como lida"
                else:
                    result = gmail.mark_unread(message_id)
                    text = "marcada como não lida"
                return {
                    "ok": True,
                    "success": True,
                    "source": "google",
                    "labels": result.get("labels", []),
                    "message": f"Mensagem {message_id} {text}.",
                }
            return {"ok": False, "success": False, "error": f"Ação de Gmail desconhecida: '{act}'"}
        except google_auth.GoogleAuthError as exc:
            return {"ok": False, "success": False, "error": str(exc)}

    def _tool_browser_search(self, args: dict[str, Any]) -> dict[str, Any]:
        from ..core.browser import BrowserManager
        q = str(args.get("query") or "").strip()
        eng = str(args.get("engine") or "google").strip()
        ok, msg, url = BrowserManager.search(q, engine=eng)
        return {"ok": ok, "success": ok, "message": msg, "url": url}

    def _tool_search_documents(self, args: dict[str, Any]) -> dict[str, Any]:
        from ..core.rag import LocalDocumentRAG
        q = str(args.get("query") or "").strip()
        try:
            lim = int(args.get("limit") or 4)
        except (ValueError, TypeError):
            lim = 4
        rag = LocalDocumentRAG(memory=self.memory)
        results = rag.search(q, limit=lim)
        formatted = [r.to_dict() for r in results]
        if formatted:
            citations = "\n".join([r.format_citation() for r in results])
            return {
                "ok": True,
                "success": True,
                "count": len(formatted),
                "results": formatted,
                "citations": citations,
                "message": f"{len(formatted)} trecho(s) relevante(s) encontrado(s) nos seus documentos.",
            }
        return {
            "ok": False,
            "success": False,
            "count": 0,
            "results": [],
            "message": f"Nenhum documento encontrado para a busca '{q}'.",
        }

    def _tool_read_document_page(self, args: dict[str, Any]) -> dict[str, Any]:
        from ..core.rag import LocalDocumentRAG
        fpath = str(args.get("file_path") or "").strip()
        try:
            pnum = int(args.get("page_number") or 1)
        except (ValueError, TypeError):
            pnum = 1
        rag = LocalDocumentRAG(memory=self.memory)
        page_text = rag.read_document_page(fpath, page_number=pnum)
        if page_text:
            return {
                "ok": True,
                "success": True,
                "page_number": pnum,
                "file_path": fpath,
                "content": page_text,
                "message": f"Página {pnum} lida com sucesso ({len(page_text)} caracteres).",
            }
        return {
            "ok": False,
            "success": False,
            "error": f"Não foi possível ler a página {pnum} do arquivo '{fpath}'.",
            "message": f"Não foi possível ler a página {pnum} do arquivo '{fpath}'.",
        }

    def _tool_open_document_file(self, args: dict[str, Any]) -> dict[str, Any]:
        from ..core.rag import LocalDocumentRAG
        fpath = str(args.get("file_path") or "").strip()
        try:
            pnum = int(args.get("page_number") or 1)
        except (ValueError, TypeError):
            pnum = 1
        rag = LocalDocumentRAG(memory=self.memory)
        ok, msg = rag.open_document(fpath, page_number=pnum)
        return {"ok": ok, "success": ok, "message": msg}

    def _tool_read_open_webpage(self, args: dict[str, Any]) -> dict[str, Any]:
        from ..core.browser import BrowserManager
        url_param = str(args.get("url") or "").strip() or None
        res = BrowserManager.read_page(url_param)
        is_ok = bool(res.get("success"))
        return {
            "ok": is_ok,
            "success": is_ok,
            "title": res.get("title", ""),
            "url": res.get("url", ""),
            "content": res.get("text", "")[:4000],
            "message": f"Conteúdo da página '{res.get('title', '')}' lido com sucesso." if is_ok else res.get("text", "Falha ao ler página web."),
        }

    def _tool_deep_web_search(self, args: dict[str, Any]) -> dict[str, Any]:
        from ..core.web_search import DeepWebResearcher, WebSearchClient
        q = str(args.get("query") or "").strip()
        researcher = DeepWebResearcher(WebSearchClient())
        res = researcher.deep_search(q)
        is_ok = bool(res.get("success"))
        # `synthesis` diz de onde veio o relatório: "llm" (escrito pelo modelo) ou
        # "offline" (compilação de trechos feita pelo app). Sem este campo o modelo
        # apresentava a compilação como se fosse análise própria. Esta rota não passa
        # provedor, então hoje é sempre "offline" — mas o campo não pode depender disso.
        synthesis = str(res.get("synthesis") or "")
        pages_read = int(res.get("pages_read") or 0)
        pages_total = int(res.get("pages_total") or 0)
        payload: dict[str, Any] = {
            "ok": is_ok,
            "success": is_ok,
            "query": q,
            "summary": res.get("summary", ""),
            "sources": res.get("sources", []),
            "report": res.get("report", ""),
            "synthesis": synthesis,
            "pages_read": pages_read,
            "pages_total": pages_total,
        }
        if not is_ok:
            payload["message"] = res.get("summary", "Falha na pesquisa profunda.")
            payload["error"] = res.get("error") or res.get("summary") or "Falha na pesquisa profunda."
            return payload
        if synthesis == "offline":
            payload["message"] = (
                f"Pesquisa profunda sobre '{q}' concluída SEM síntese por IA: "
                f"{pages_read} de {pages_total} fontes lidas por inteiro. O relatório é uma "
                f"compilação de trechos das fontes — apresente-o como tal, não como análise própria."
            )
        else:
            payload["message"] = f"Pesquisa profunda sobre '{q}' concluída."
        return payload

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

    def _tool_network_inspect(self, args: dict[str, Any]) -> dict[str, Any]:
        target = str(args.get("target_host") or "1.1.1.1").strip()
        try:
            from ..core.network_inspector import NetworkInspector

            report = NetworkInspector.get_health_report(target_internet=target)
            return {
                "ok": True,
                "primary_interface": report.primary_interface,
                "primary_type": report.primary_type,
                "local_ip": report.local_ip,
                "gateway_ip": report.gateway_ip,
                "gateway_ping_ms": report.gateway_ping_ms,
                "internet_ping_ms": report.internet_ping_ms,
                "dns_servers": report.dns_servers,
                "packet_loss_percent": report.packet_loss_percent,
                "wifi": {
                    "ssid": report.wifi.ssid,
                    "signal_percent": report.wifi.signal_percent,
                    "frequency_ghz": report.wifi.frequency_ghz,
                    "rate_mbps": report.wifi.rate_mbps,
                }
                if report.wifi
                else None,
            }
        except Exception as exc:
            logger.warning("Falha ao inspecionar rede: %s", exc)
            return {"ok": False, "error": f"Falha na inspeção de rede: {exc}"}

    def _tool_lan_devices_scan(self, args: dict[str, Any]) -> dict[str, Any]:
        active_only = bool(args.get("active_only", True))
        try:
            from ..core.network_inspector import NetworkInspector

            neighbors = NetworkInspector.get_neighbors()
            if active_only:
                neighbors = [n for n in neighbors if n.is_online]
            safe_summary = NetworkInspector.to_safe_summary(neighbors=neighbors)
            return {
                "ok": True,
                "device_count": len(neighbors),
                "devices": safe_summary.get("lan_devices", []),
            }
        except Exception as exc:
            logger.warning("Falha ao escanear LAN: %s", exc)
            return {"ok": False, "error": f"Falha ao escanear dispositivos na LAN: {exc}"}

    def _tool_network_discover_lan(self, args: dict[str, Any]) -> dict[str, Any]:
        cidr = str(args.get("cidr") or "").strip()
        try:
            from ..core.network_inspector import NetworkInspector

            if not cidr:
                cidr = NetworkInspector.local_subnet_cidr() or "192.168.1.0/24"
            devices = NetworkInspector.scan_subnet(cidr)
            safe_summary = NetworkInspector.to_safe_summary(neighbors=devices)
            return {
                "ok": True,
                "cidr": cidr,
                "device_count": len(devices),
                "devices": safe_summary.get("lan_devices", []),
            }
        except Exception as exc:
            logger.warning("Falha na descoberta ativa de LAN: %s", exc)
            return {"ok": False, "error": f"Falha na descoberta ativa da LAN: {exc}"}

    def _tool_network_identify_device(self, args: dict[str, Any]) -> dict[str, Any]:
        ip = str(args.get("ip") or "").strip()
        if not ip:
            return {"ok": False, "error": "O parâmetro 'ip' é obrigatório."}
        try:
            from ..core.network_inspector import NetworkInspector

            result = NetworkInspector.identify_device(ip)
            # Não expõe o MAC cru: mascara antes de devolver ao modelo.
            if result.get("mac"):
                result["mac_masked"] = NetworkInspector.mask_mac(result["mac"])
                result.pop("mac", None)
            return result
        except Exception as exc:
            logger.warning("Falha ao identificar dispositivo: %s", exc)
            return {"ok": False, "error": f"Falha ao identificar o dispositivo: {exc}"}

    def _tool_network_watch_lan(self, args: dict[str, Any]) -> dict[str, Any]:
        cidr = str(args.get("cidr") or "").strip() or None
        try:
            from ..core.network_inspector import NetworkInspector

            result = NetworkInspector.monitor_lan(cidr=cidr)
            # O monitor_lan já mascara os MACs nos device lists; garantia extra de
            # que nenhum MAC cru escapa ao modelo.
            return result
        except Exception as exc:
            logger.warning("Falha ao monitorar a LAN: %s", exc)
            return {"ok": False, "error": f"Falha ao monitorar a LAN: {exc}"}

    def _tool_network_establish_baseline(self, args: dict[str, Any]) -> dict[str, Any]:
        cidr = str(args.get("cidr") or "").strip() or None
        try:
            from ..core.network_inspector import NetworkInspector

            result = NetworkInspector.establish_baseline(cidr=cidr)
            return result
        except Exception as exc:
            logger.warning("Falha ao estabelecer baseline: %s", exc)
            return {"ok": False, "error": f"Falha ao estabelecer o baseline: {exc}"}

    def _tool_network_block_device(self, args: dict[str, Any]) -> dict[str, Any]:
        ip = str(args.get("ip") or "").strip() or None
        mac = str(args.get("mac") or "").strip() or None
        action = str(args.get("action") or "block").strip() or "block"
        try:
            from ..core.network_actions import NetworkBlocker
            from ..core.network_inspector import DeviceInventory

            blocker = NetworkBlocker()
            # Inventário local para resolver IP -> MAC real (privado; não exposto ao modelo).
            inventory = DeviceInventory()
            result = blocker.request_block(
                mac=mac, ip=ip, action=action, inventory=inventory
            )
            # O blocker já mascara o MAC na resposta; reforço de que nenhum MAC cru escapa.
            return result
        except Exception as exc:
            logger.warning("Falha ao conter dispositivo: %s", exc)
            return {"ok": False, "error": f"Falha ao conter o dispositivo: {exc}"}

    def _tool_local_services_list(self, args: dict[str, Any]) -> dict[str, Any]:
        proto = str(args.get("protocol") or "all").strip().lower()
        try:
            from ..core.network_inspector import NetworkInspector

            ports = NetworkInspector.get_listening_ports()
            if proto in ("tcp", "udp"):
                ports = [p for p in ports if p.protocol == proto]
            return {
                "ok": True,
                "count": len(ports),
                "ports": [
                    {
                        "protocol": p.protocol,
                        "port": p.port,
                        "ip_binding": p.ip_binding,
                        "process_name": p.process_name,
                        "pid": p.pid,
                    }
                    for p in ports
                ],
            }
        except Exception as exc:
            logger.warning("Falha ao listar serviços locais: %s", exc)
            return {"ok": False, "error": f"Falha ao inspecionar serviços locais: {exc}"}

    def _tool_network_wake_on_lan(self, args: dict[str, Any]) -> dict[str, Any]:
        mac = str(args.get("mac") or "").strip()
        bcast = str(args.get("broadcast_ip") or "255.255.255.255").strip()
        if not mac:
            return {"ok": False, "error": "Argumento 'mac' é obrigatório."}
        try:
            from ..core.network_actions import wake_on_lan

            ok, msg = wake_on_lan(mac, broadcast_ip=bcast)
            return {"ok": ok, "message": msg, "target_mac": mac}
        except Exception as exc:
            logger.warning("Falha em network_wake_on_lan: %s", exc)
            return {"ok": False, "error": f"Erro ao executar Wake-on-LAN: {exc}"}

    def _tool_network_release_port(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            port = int(args.get("port", 0))
        except (ValueError, TypeError):
            return {"ok": False, "error": "Argumento 'port' deve ser um número inteiro válido."}
        force = bool(args.get("force", False))
        try:
            from ..core.network_actions import release_local_port
            from ..core.network_inspector import NetworkInspector
            from ..core.port_termination import confirm_port_freed

            # Descobre protocolo/interface antes de encerrar, para confirmar corretamente.
            protocol = "tcp"
            ip_binding: str | None = None
            try:
                for lp in NetworkInspector.get_listening_ports():
                    if lp.port == port:
                        protocol = lp.protocol or "tcp"
                        ip_binding = lp.ip_binding
                        break
            except Exception:
                pass

            ok, msg, meta = release_local_port(port, force=force)

            # S6: confirma se a porta foi de fato liberada (somente leitura).
            report = confirm_port_freed(
                port,
                protocol,
                expected_pid=(meta or {}).get("pid"),
                expected_process=(meta or {}).get("process"),
                ip_binding=ip_binding,
            )
            return {
                "ok": ok,
                "message": msg,
                "freed": report.freed,
                "confirmation": report.summary,
                "details": meta,
            }
        except Exception as exc:
            logger.warning("Falha em network_release_port: %s", exc)
            return {"ok": False, "error": f"Erro ao liberar porta: {exc}"}

    def _tool_network_confirm_port(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            port = int(args.get("port", 0))
        except (ValueError, TypeError):
            return {"ok": False, "error": "Argumento 'port' deve ser um número inteiro válido."}
        protocol = str(args.get("protocol") or "tcp").strip().lower()
        if protocol not in ("tcp", "udp"):
            protocol = "tcp"
        try:
            from ..core.port_termination import confirm_port_freed

            report = confirm_port_freed(port, protocol)
            return {
                "ok": True,
                "freed": report.freed,
                "summary": report.summary,
                "pid_state": report.pid_state.value,
                "port_state": report.port_state.value,
                "occupying_pid": report.occupying_pid,
                "next_steps": report.next_steps,
            }
        except Exception as exc:
            logger.warning("Falha em network_confirm_port: %s", exc)
            return {"ok": False, "error": f"Erro ao verificar porta: {exc}"}

    def _tool_network_probe_service(self, args: dict[str, Any]) -> dict[str, Any]:
        host = str(args.get("host") or "").strip()
        try:
            port = int(args.get("port", 0))
        except (ValueError, TypeError):
            return {"ok": False, "error": "Argumento 'port' inválido."}
        if not host:
            return {"ok": False, "error": "Argumento 'host' é obrigatório."}
        try:
            from ..core.network_actions import probe_service

            return probe_service(host, port)
        except Exception as exc:
            logger.warning("Falha em network_probe_service: %s", exc)
            return {"ok": False, "error": f"Erro ao sondar serviço: {exc}"}

    def _tool_network_dns_benchmark(self, args: dict[str, Any]) -> dict[str, Any]:
        dom = str(args.get("domain") or "").strip()
        domains = [dom] if dom else None
        try:
            from ..core.network_actions import benchmark_dns

            results = benchmark_dns(domains=domains)
            return {"ok": True, "results": results}
        except Exception as exc:
            logger.warning("Falha em network_dns_benchmark: %s", exc)
            return {"ok": False, "error": f"Erro ao testar DNS: {exc}"}

    def _tool_network_reconnect(self, args: dict[str, Any]) -> dict[str, Any]:
        iface = str(args.get("interface") or "").strip() or None
        try:
            from ..core.network_actions import reconnect_interface

            ok, msg = reconnect_interface(iface)
            return {"ok": ok, "message": msg}
        except Exception as exc:
            logger.warning("Falha em network_reconnect: %s", exc)
            return {"ok": False, "error": f"Erro ao reconectar interface: {exc}"}

    def _tool_network_audit_exposure(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            from ..core.network_actions import audit_attack_surface

            report = audit_attack_surface()
            return {"ok": True, "report": report}
        except Exception as exc:
            logger.warning("Falha em network_audit_exposure: %s", exc)
            return {"ok": False, "error": f"Erro ao auditar portas: {exc}"}

    def _tool_network_throughput(self, args: dict[str, Any]) -> dict[str, Any]:
        iface = str(args.get("interface") or "").strip() or None
        window = float(args.get("sample_seconds") or 0.4)
        try:
            from ..core.network_actions import measure_throughput

            res = measure_throughput(interface_name=iface, sample_seconds=window)
            return {"ok": True, "throughput": res}
        except Exception as exc:
            logger.warning("Falha em network_throughput: %s", exc)
            return {"ok": False, "error": f"Erro ao medir throughput: {exc}"}

    def _tool_network_diagnose_path(self, args: dict[str, Any]) -> dict[str, Any]:
        target = str(args.get("target") or "1.1.1.1").strip()
        try:
            from ..core.network_actions import diagnose_connection_path

            res = diagnose_connection_path(target_host=target)
            return {"ok": True, "diagnosis": res}
        except Exception as exc:
            logger.warning("Falha em network_diagnose_path: %s", exc)
            return {"ok": False, "error": f"Erro no diagnóstico de conexão: {exc}"}

    def _tool_network_ssl_check(self, args: dict[str, Any]) -> dict[str, Any]:
        host = str(args.get("host") or "").strip()
        if not host:
            return {"ok": False, "error": "Argumento 'host' é obrigatório."}
        port = int(args.get("port") or 443)
        try:
            from ..core.network_actions import check_ssl_certificate

            res = check_ssl_certificate(host, port=port)
            return {"ok": res.get("ok", False), "certificate": res}
        except Exception as exc:
            logger.warning("Falha em network_ssl_check: %s", exc)
            return {"ok": False, "error": f"Erro ao checar SSL: {exc}"}

    def _tool_network_mdns_services(self, args: dict[str, Any]) -> dict[str, Any]:
        timeout = float(args.get("timeout_sec") or 2.0)
        try:
            from ..core.network_actions import discover_mdns_services

            services = discover_mdns_services(timeout_sec=timeout)
            return {"ok": True, "count": len(services), "services": services}
        except Exception as exc:
            logger.warning("Falha em network_mdns_services: %s", exc)
            return {"ok": False, "error": f"Erro na descoberta mDNS: {exc}"}

    def _tool_docker_list_containers(self, args: dict[str, Any]) -> dict[str, Any]:
        all_c = bool(args.get("all", True))
        try:
            from ..core.docker_manager import DockerManager

            dm = DockerManager()
            if not dm.is_available():
                return {"ok": False, "error": "Docker não está disponível ou o socket está inacessível."}
            containers = [c.to_dict() for c in dm.list_containers(all_containers=all_c)]
            return {"ok": True, "count": len(containers), "containers": containers}
        except Exception as exc:
            logger.warning("Falha em docker_list_containers: %s", exc)
            return {"ok": False, "error": f"Erro ao listar containers: {exc}"}

    def _tool_docker_container_logs(self, args: dict[str, Any]) -> dict[str, Any]:
        container = str(args.get("container") or "").strip()
        if not container:
            return {"ok": False, "error": "Argumento 'container' é obrigatório."}
        tail = int(args.get("tail") or 50)
        filter_err = bool(args.get("filter_errors_only", False))
        try:
            from ..core.docker_manager import DockerManager

            dm = DockerManager()
            report = dm.get_container_logs(container, tail=tail, filter_errors_only=filter_err)
            return {"ok": True, "report": report}
        except Exception as exc:
            logger.warning("Falha em docker_container_logs: %s", exc)
            return {"ok": False, "error": f"Erro ao extrair logs: {exc}"}

    def _tool_docker_container_action(self, args: dict[str, Any]) -> dict[str, Any]:
        container = str(args.get("container") or "").strip()
        action = str(args.get("action") or "").strip().lower()
        if not container:
            return {"ok": False, "error": "Argumento 'container' é obrigatório."}
        if action not in ("restart", "stop"):
            return {"ok": False, "error": "Ação inválida. Use 'restart' ou 'stop'."}
        try:
            from ..core.docker_manager import DockerManager

            dm = DockerManager()
            if action == "restart":
                result = dm.restart_container(container)
            else:
                result = dm.stop_container(container)
            return {"ok": result.get("success", False), "result": result}
        except Exception as exc:
            logger.warning("Falha em docker_container_action: %s", exc)
            return {"ok": False, "error": f"Erro ao executar ação no container: {exc}"}

    def _tool_system_hardware_health(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            from ..core.system_health import SystemHealthInspector

            report = SystemHealthInspector.get_complete_report()
            return {"ok": True, "report": report}
        except Exception as exc:
            logger.warning("Falha em system_hardware_health: %s", exc)
            return {"ok": False, "error": f"Erro ao inspecionar saúde do hardware: {exc}"}

    def _tool_system_journal_errors(self, args: dict[str, Any]) -> dict[str, Any]:
        lines = int(args.get("lines") or 40)
        try:
            from ..core.system_health import SystemHealthInspector

            incidents = SystemHealthInspector.get_journal_incidents(lines=lines)
            return {
                "ok": True,
                "count": len(incidents),
                "incidents": [
                    {
                        "unit": inc.unit,
                        "message": inc.message,
                        "priority": inc.priority,
                        "repeats": inc.repeat_count,
                        "timestamp": inc.timestamp,
                    }
                    for inc in incidents
                ],
            }
        except Exception as exc:
            logger.warning("Falha em system_journal_errors: %s", exc)
            return {"ok": False, "error": f"Erro ao extrair incidentes do journal: {exc}"}

    def _tool_scratchpad_write(self, args: dict[str, Any]) -> dict[str, Any]:
        key = str(args.get("key") or "").strip()
        content = str(args.get("content") or "")
        append = bool(args.get("append", False))
        if not key:
            return {"ok": False, "error": "Argumento 'key' é obrigatório."}
        if append and key in self._scratchpad and self._scratchpad[key]:
            self._scratchpad[key] = f"{self._scratchpad[key]}\n{content}"
        else:
            self._scratchpad[key] = content
        return {
            "ok": True,
            "key": key,
            "length": len(self._scratchpad[key]),
            "keys_count": len(self._scratchpad),
            "message": f"Nota '{key}' registrada no Scratchpad ({len(self._scratchpad[key])} caracteres).",
        }

    def _tool_scratchpad_read(self, args: dict[str, Any]) -> dict[str, Any]:
        key = str(args.get("key") or "").strip()
        if key:
            if key not in self._scratchpad:
                return {
                    "ok": False,
                    "error": f"Chave '{key}' não encontrada no Scratchpad.",
                    "available_keys": list(self._scratchpad.keys()),
                }
            return {"ok": True, "key": key, "content": self._scratchpad[key]}
        return {
            "ok": True,
            "count": len(self._scratchpad),
            "notes": dict(self._scratchpad),
        }

    def _tool_scratchpad_clear(self, args: dict[str, Any]) -> dict[str, Any]:
        key = str(args.get("key") or "").strip()
        if key:
            existed = key in self._scratchpad
            self._scratchpad.pop(key, None)
            return {"ok": True, "cleared_key": key, "existed": existed, "remaining": len(self._scratchpad)}
        count = len(self._scratchpad)
        self._scratchpad.clear()
        return {"ok": True, "cleared_count": count, "remaining": 0}

    def _tool_inspect_window(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "Argumento 'query' é obrigatório."}
        should_focus = bool(args.get("focus", True))
        include_bounds = bool(args.get("include_bounds", False))

        win_meta: dict[str, Any] | None = None
        target_app = query
        try:
            from ..core.window_manager import WindowManager

            win = WindowManager.find_window(query)
            if win:
                target_app = win.app or query
                win_meta = {
                    "id": win.id,
                    "app": win.app,
                    "title": win.title,
                    "workspace": win.workspace,
                    "bounds": {"x": win.x, "y": win.y, "width": win.width, "height": win.height},
                    "display_name": win.display_name(),
                }
                if should_focus:
                    WindowManager.focus_window(win.id or win.app)
                    if self._fence is not None and hasattr(self._fence, "set_chosen_window"):
                        self._fence.set_chosen_window(win)
                    time.sleep(0.08)
        except Exception as exc:
            logger.debug("Falha ao buscar/focar janela em inspect_window: %s", exc)

        tree, error = self._tree_for(target_app)
        if error:
            if win_meta is not None:
                return {
                    "ok": True,
                    "window": win_meta,
                    "tree": "",
                    "tree_available": False,
                    "warning": error,
                }
            return {"ok": False, "error": error}

        try:
            summary = tree.to_summary(include_bounds=include_bounds)
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao resumir árvore da janela: {exc}"}

        truncated = len(summary) > self.max_tree_chars
        return {
            "ok": True,
            "window": win_meta,
            "app": target_app,
            "tree": summary[: self.max_tree_chars],
            "tree_available": True,
            "truncated": truncated,
        }

    def _tool_plan_update(self, args: dict[str, Any]) -> dict[str, Any]:
        import re

        raw_steps = args.get("steps")
        if isinstance(raw_steps, str) and raw_steps.strip():
            raw_steps = [s.strip() for s in re.split(r"[\n;]+", raw_steps) if s.strip()]

        if isinstance(raw_steps, list) and raw_steps:
            new_milestones: list[dict[str, str]] = []
            for idx, item in enumerate(raw_steps):
                if isinstance(item, dict):
                    title = str(item.get("title") or item.get("step") or "").strip()
                    status = str(item.get("status") or "pending").strip().lower()
                else:
                    title = re.sub(r"^\s*\d+[\.\)\-]\s*", "", str(item or "").strip())
                    status = "in_progress" if idx == 0 else "pending"
                if title:
                    if status not in ("pending", "in_progress", "done", "blocked"):
                        status = "pending"
                    new_milestones.append({"title": title, "status": status})
            if new_milestones:
                self._plan_milestones.clear()
                self._plan_milestones.extend(new_milestones)

        completed_step = args.get("completed_step")
        if completed_step is not None and self._plan_milestones:
            try:
                c_idx = int(completed_step) - 1
                if 0 <= c_idx < len(self._plan_milestones):
                    for i in range(c_idx + 1):
                        self._plan_milestones[i]["status"] = "done"
                    if c_idx + 1 < len(self._plan_milestones) and self._plan_milestones[c_idx + 1]["status"] == "pending":
                        self._plan_milestones[c_idx + 1]["status"] = "in_progress"
            except (TypeError, ValueError):
                pass

        current_step = args.get("current_step")
        if current_step is not None and self._plan_milestones:
            try:
                cur_idx = int(current_step) - 1
                if 0 <= cur_idx < len(self._plan_milestones):
                    for i, m in enumerate(self._plan_milestones):
                        if i < cur_idx and m["status"] == "in_progress":
                            m["status"] = "done"
                        elif i == cur_idx:
                            m["status"] = "in_progress"
            except (TypeError, ValueError):
                pass

        if not self._plan_milestones:
            return {"ok": False, "error": "Nenhuma etapa informada em 'steps' e nenhum plano ativo na sessão."}

        done_count = sum(1 for m in self._plan_milestones if m.get("status") == "done")
        return {
            "ok": True,
            "total": len(self._plan_milestones),
            "completed": done_count,
            "milestones": [dict(m) for m in self._plan_milestones],
        }



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
