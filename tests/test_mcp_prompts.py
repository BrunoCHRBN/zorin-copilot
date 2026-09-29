"""Testes dos prompts MCP (``prompts/list`` / ``prompts/get``) e da paleta Ctrl+K.

Cobrem as quatro camadas que o Passo 3 de `docs/PROXIMOS_PASSOS.md` toca:

1. o protocolo (`MCPPrompt`, `MCPPromptArgument`);
2. o cliente (`list_prompts`, `get_prompt`) — parsing das respostas;
3. o gerenciador (agregação entre servidores e resolução por prefixo);
4. o helper puro (`mcp/prompts.py`) e a integração com a paleta de comandos.

O cliente é exercitado com um transporte em memória, não com mock de
`subprocess.Popen`: o que interessa aqui é o parsing das mensagens, e o mock de
processo adiciona uma camada de indireção que esconde o bug em vez de expô-lo.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from typing import Any
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402

require_gtk4()
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

from zorin_copilot.mcp import prompts as prompt_utils  # noqa: E402
from zorin_copilot.mcp.client import BaseMCPClient  # noqa: E402
from zorin_copilot.mcp.manager import MCPManager, MCPServerConfig  # noqa: E402
from zorin_copilot.mcp.protocol import (  # noqa: E402
    JSONRPCResponse,
    MCPPrompt,
    MCPPromptArgument,
    MCPServerState,
)

# ----------------------------------------------------------------------
# Protocolo
# ----------------------------------------------------------------------


class MCPPromptProtocolTest(unittest.TestCase):
    """`MCPPrompt` espelha `MCPTool`, com a diferença que importa: argumentos."""

    def _prompt(self, **kwargs) -> MCPPrompt:
        base = dict(name="code_review", server_name="github")
        base.update(kwargs)
        return MCPPrompt(**base)

    def test_qualified_name_is_namespaced(self):
        self.assertEqual(self._prompt().qualified_name, "mcp__github__code_review")

    def test_required_arguments_filters_optionals(self):
        prompt = self._prompt(
            arguments=[
                MCPPromptArgument("pr", required=True),
                MCPPromptArgument("tone", required=False),
                MCPPromptArgument("lang", required=True),
            ]
        )
        self.assertEqual([a.name for a in prompt.required_arguments], ["pr", "lang"])

    def test_no_arguments_means_no_required(self):
        prompt = self._prompt()
        self.assertEqual(prompt.arguments, [])
        self.assertEqual(prompt.required_arguments, [])

    def test_to_dict_is_json_ready(self):
        prompt = self._prompt(
            description="Revisa um PR",
            arguments=[MCPPromptArgument("pr", "Número do PR", True)],
        )
        payload = prompt.to_dict()
        self.assertEqual(payload["qualified_name"], "mcp__github__code_review")
        self.assertEqual(payload["description"], "Revisa um PR")
        self.assertEqual(
            payload["arguments"], [{"name": "pr", "description": "Número do PR", "required": True}]
        )

    def test_arguments_default_is_not_shared_between_instances(self):
        """`field(default_factory=list)` — sem isso, dois prompts compartilhariam a lista."""
        a = self._prompt()
        b = self._prompt()
        a.arguments.append(MCPPromptArgument("x"))
        self.assertEqual(b.arguments, [])


# ----------------------------------------------------------------------
# Cliente
# ----------------------------------------------------------------------


class _MemoryClient(BaseMCPClient):
    """Cliente sem transporte físico: responde de dicionários em memória.

    Já nasce `CONNECTED` porque `list_prompts`/`get_prompt` só chamam `start()`
    quando o estado não é esse — e aqui não há handshake real a executar.
    """

    def __init__(
        self,
        name: str,
        results: dict[str, Any] | None = None,
        errors: dict[str, dict[str, Any]] | None = None,
        timeouts: set[str] | None = None,
    ) -> None:
        super().__init__(name)
        self.results = results or {}
        self.errors = errors or {}
        self.timeouts = timeouts or set()
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.state = MCPServerState.CONNECTED

    def _do_connect(self, timeout: float) -> bool:  # pragma: no cover - não usado
        return True

    def _do_disconnect(self) -> None:  # pragma: no cover - não usado
        pass

    def _do_send_raw(self, msg_str: str, timeout: float = 20.0) -> bool:  # pragma: no cover
        return True

    def send_request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 20.0
    ) -> JSONRPCResponse | None:
        self.requests.append((method, params or {}))
        if method in self.timeouts:
            return None
        if method in self.errors:
            return JSONRPCResponse(id=1, error=self.errors[method])
        if method not in self.results:
            # Comportamento real de um servidor sem a capability `prompts`.
            return JSONRPCResponse(id=1, error={"code": -32601, "message": "Method not found"})
        return JSONRPCResponse(id=1, result=self.results[method])


class PromptClientTest(unittest.TestCase):
    """`list_prompts` e `get_prompt` no `BaseMCPClient`."""

    def test_list_prompts_parses_name_description_and_arguments(self):
        client = _MemoryClient(
            "github",
            {
                "prompts/list": {
                    "prompts": [
                        {
                            "name": "code_review",
                            "description": "Revisa um PR",
                            "arguments": [
                                {"name": "pr", "description": "Número", "required": True},
                                {"name": "tone"},
                            ],
                        }
                    ]
                }
            },
        )

        prompts = client.list_prompts()

        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0].name, "code_review")
        self.assertEqual(prompts[0].server_name, "github")
        self.assertEqual(prompts[0].description, "Revisa um PR")
        self.assertEqual([a.name for a in prompts[0].arguments], ["pr", "tone"])
        self.assertTrue(prompts[0].arguments[0].required)
        # `required` ausente no JSON precisa virar False, não None.
        self.assertFalse(prompts[0].arguments[1].required)

    def test_list_prompts_uses_the_prompts_list_method(self):
        client = _MemoryClient("srv", {"prompts/list": {"prompts": []}})
        client.list_prompts()
        self.assertIn("prompts/list", [m for m, _ in client.requests])

    def test_list_prompts_returns_empty_when_server_lacks_capability(self):
        """Servidor sem `prompts` responde erro JSON-RPC — não é falha do cliente."""
        client = _MemoryClient("no-prompts", {})
        self.assertEqual(client.list_prompts(), [])

    def test_list_prompts_returns_empty_on_timeout(self):
        client = _MemoryClient("slow", {}, timeouts={"prompts/list"})
        self.assertEqual(client.list_prompts(), [])

    def test_list_prompts_tolerates_malformed_arguments(self):
        """`arguments` pode vir ausente, `null`, string ou com itens sem nome."""
        client = _MemoryClient(
            "weird",
            {
                "prompts/list": {
                    "prompts": [
                        {"name": "a"},
                        {"name": "b", "arguments": None},
                        {"name": "c", "arguments": "not-a-list"},
                        {"name": "d", "arguments": [{"description": "sem nome"}, "lixo", 7]},
                        {"description": "sem nome de prompt"},
                        "nem é dicionário",
                    ]
                }
            },
        )

        prompts = client.list_prompts()

        self.assertEqual([p.name for p in prompts], ["a", "b", "c", "d"])
        self.assertEqual(prompts[0].arguments, [])
        self.assertEqual(prompts[1].arguments, [])
        self.assertEqual(prompts[2].arguments, [])
        self.assertEqual(prompts[3].arguments, [])

    def test_list_prompts_tolerates_non_dict_result(self):
        """Um servidor que devolve lista no lugar de objeto não pode derrubar a UI."""
        client = _MemoryClient("odd", {"prompts/list": ["inesperado"]})
        self.assertEqual(client.list_prompts(), [])

    def test_get_prompt_concatenates_messages_in_order(self):
        client = _MemoryClient(
            "github",
            {
                "prompts/get": {
                    "description": "Revisão",
                    "messages": [
                        {"role": "user", "content": {"type": "text", "text": "Primeira parte"}},
                        {"role": "user", "content": [{"type": "text", "text": "Segunda parte"}]},
                    ],
                }
            },
        )

        text = client.get_prompt("code_review", {"pr": "42"})

        self.assertEqual(text, "Primeira parte\n\nSegunda parte")

    def test_get_prompt_sends_name_and_arguments(self):
        client = _MemoryClient("github", {"prompts/get": {"messages": []}})
        client.get_prompt("code_review", {"pr": "42"})

        method, params = next(r for r in client.requests if r[0] == "prompts/get")
        self.assertEqual(params["name"], "code_review")
        self.assertEqual(params["arguments"], {"pr": "42"})

    def test_get_prompt_sends_empty_arguments_object_when_none(self):
        """A especificação exige objeto; servidores estritos rejeitam `null`."""
        client = _MemoryClient("github", {"prompts/get": {"messages": []}})
        client.get_prompt("code_review")

        _method, params = next(r for r in client.requests if r[0] == "prompts/get")
        self.assertEqual(params["arguments"], {})

    def test_get_prompt_reads_embedded_resource_text(self):
        client = _MemoryClient(
            "files",
            {
                "prompts/get": {
                    "messages": [
                        {
                            "role": "user",
                            "content": {
                                "type": "resource",
                                "resource": {"uri": "file:///tmp/a.md", "text": "Conteúdo do arquivo"},
                            },
                        }
                    ]
                }
            },
        )
        self.assertEqual(client.get_prompt("summarize"), "Conteúdo do arquivo")

    def test_get_prompt_falls_back_to_description_when_no_messages(self):
        client = _MemoryClient(
            "srv", {"prompts/get": {"description": "Só a descrição", "messages": []}}
        )
        self.assertEqual(client.get_prompt("x"), "Só a descrição")

    def test_get_prompt_reports_server_error_as_text(self):
        """A UI mostra o retorno direto: erro não pode virar exceção nem string vazia."""
        client = _MemoryClient(
            "srv",
            errors={"prompts/get": {"code": -32602, "message": "Argumento 'pr' obrigatório"}},
        )
        text = client.get_prompt("code_review")
        self.assertIn("Argumento 'pr' obrigatório", text)

    def test_get_prompt_reports_timeout_as_text(self):
        client = _MemoryClient("srv", {}, timeouts={"prompts/get"})
        text = client.get_prompt("code_review")
        self.assertIn("Timeout", text)


# ----------------------------------------------------------------------
# Gerenciador
# ----------------------------------------------------------------------


class PromptManagerTest(unittest.TestCase):
    """Agregação de prompts entre servidores e resolução por prefixo."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.mgr = MCPManager(config_path=os.path.join(tmp.name, "mcp_servers.json"))
        self.addCleanup(self.mgr.stop_file_watcher)

    def _add(self, client: _MemoryClient) -> None:
        self.mgr._clients[client.name] = client

    def _client_with(self, name: str, prompt_names: list[str]) -> _MemoryClient:
        return _MemoryClient(
            name,
            {
                "prompts/list": {
                    "prompts": [{"name": n, "description": f"Prompt {n}"} for n in prompt_names]
                }
            },
        )

    def test_aggregates_prompts_from_every_connected_server(self):
        self._add(self._client_with("alpha", ["greet"]))
        self._add(self._client_with("beta", ["summarize", "translate"]))

        prompts = self.mgr.get_all_prompts()

        self.assertEqual(
            sorted(p.qualified_name for p in prompts),
            ["mcp__alpha__greet", "mcp__beta__summarize", "mcp__beta__translate"],
        )

    def test_cache_is_reused_until_invalidated(self):
        client = self._client_with("alpha", ["greet"])
        self._add(client)

        self.mgr.get_all_prompts()
        self.mgr.get_all_prompts()

        self.assertEqual([m for m, _ in client.requests].count("prompts/list"), 1)

    def test_stop_all_clears_the_prompt_cache(self):
        self._add(self._client_with("alpha", ["greet"]))
        self.mgr.get_all_prompts()

        self.mgr.stop_all()

        self.assertEqual(self.mgr._cached_prompts, [])

    def test_get_prompt_by_qualified_name(self):
        self._add(self._client_with("alpha", ["greet"]))
        found = self.mgr.get_prompt("mcp__alpha__greet")
        self.assertIsNotNone(found)
        self.assertEqual(found.server_name, "alpha")

    def test_get_prompt_by_simple_name(self):
        self._add(self._client_with("alpha", ["greet"]))
        found = self.mgr.get_prompt("greet")
        self.assertIsNotNone(found)
        self.assertEqual(found.qualified_name, "mcp__alpha__greet")

    def test_get_prompt_unknown_name_returns_none(self):
        self._add(self._client_with("alpha", ["greet"]))
        self.assertIsNone(self.mgr.get_prompt("nao-existe"))

    def test_render_prompt_resolves_server_from_the_prefix_without_cache(self):
        """A resolução por `mcp__server__name` não depende do cache de prompts."""
        client = _MemoryClient(
            "alpha",
            {"prompts/get": {"messages": [{"role": "user", "content": {"type": "text", "text": "Olá!"}}]}},
        )
        self._add(client)

        text = self.mgr.render_prompt("mcp__alpha__greet", {"nome": "Bruno"})

        self.assertEqual(text, "Olá!")
        _method, params = next(r for r in client.requests if r[0] == "prompts/get")
        self.assertEqual(params["name"], "greet")
        self.assertEqual(params["arguments"], {"nome": "Bruno"})

    def test_render_prompt_accepts_colon_form(self):
        client = _MemoryClient(
            "alpha",
            {"prompts/get": {"messages": [{"role": "user", "content": {"type": "text", "text": "ok"}}]}},
        )
        self._add(client)
        self.assertEqual(self.mgr.render_prompt("alpha:greet"), "ok")

    def test_render_prompt_for_unknown_server_returns_readable_text(self):
        """Destino é a caixa de entrada: erro precisa ser texto, não exceção."""
        text = self.mgr.render_prompt("mcp__fantasma__greet")
        self.assertIn("fantasma", text)

    def test_status_summary_exposes_prompt_counts(self):
        self._add(self._client_with("alpha", ["greet", "bye"]))
        self.mgr._server_configs["alpha"] = MCPServerConfig(name="alpha", command="echo")

        entry = next(s for s in self.mgr.get_status_summary() if s["name"] == "alpha")

        self.assertEqual(entry["prompt_count"], 2)
        details = sorted(entry["prompt_details"], key=lambda p: p["name"])
        self.assertEqual([p["name"] for p in details], ["bye", "greet"])
        self.assertEqual(details[0]["qualified_name"], "mcp__alpha__bye")


# ----------------------------------------------------------------------
# Helper puro
# ----------------------------------------------------------------------


class PromptHelperTest(unittest.TestCase):
    """`mcp/prompts.py` — nome de comando, busca e coleta de argumentos."""

    def _prompt(self, name="code_review", server="github", description="", arguments=None):
        return MCPPrompt(
            name=name,
            server_name=server,
            description=description,
            arguments=arguments or [],
        )

    def test_command_name_round_trips(self):
        prompt = self._prompt()
        command = prompt_utils.prompt_command_name(prompt)
        self.assertTrue(prompt_utils.is_prompt_command(command))
        self.assertEqual(prompt_utils.qualified_name_from_command(command), "mcp__github__code_review")

    def test_native_commands_are_not_prompt_commands(self):
        self.assertFalse(prompt_utils.is_prompt_command("app.new-topic"))
        self.assertFalse(prompt_utils.is_prompt_command(""))
        self.assertEqual(prompt_utils.qualified_name_from_command("app.new-topic"), "")

    def test_prettify_splits_separators(self):
        self.assertEqual(prompt_utils.prettify_prompt_name("code_review"), "Code review")
        self.assertEqual(prompt_utils.prettify_prompt_name("code-review"), "Code review")

    def test_prettify_splits_camel_case(self):
        """Sem a fronteira de camelCase, `codeReview` viraria uma palavra só."""
        self.assertEqual(prompt_utils.prettify_prompt_name("codeReview"), "Code review")

    def test_prettify_keeps_acronyms(self):
        self.assertEqual(prompt_utils.prettify_prompt_name("API_lookup"), "API lookup")

    def test_prettify_handles_empty(self):
        self.assertEqual(prompt_utils.prettify_prompt_name(""), "")

    def test_keywords_split_the_prompt_name_into_searchable_words(self):
        """Procurar "review" precisa achar `code_review`, que nunca aparece inteiro."""
        keywords = prompt_utils.prompt_keywords(self._prompt())
        self.assertIn("review", keywords)
        self.assertIn("code", keywords)
        self.assertIn("github", keywords)

    def test_keywords_include_server_name_and_drop_noise_words(self):
        keywords = prompt_utils.prompt_keywords(
            self._prompt(server="sqlite", description="Analisa as consultas do banco")
        )
        self.assertIn("sqlite", keywords)
        self.assertNotIn("do", keywords)
        self.assertNotIn("as", keywords)

    def test_keywords_are_unique(self):
        keywords = prompt_utils.prompt_keywords(
            self._prompt(name="review", description="review review review")
        )
        self.assertEqual(len(keywords), len(set(keywords)))

    def test_subtitle_falls_back_to_argument_counts(self):
        required = self._prompt(arguments=[MCPPromptArgument("a", required=True)])
        self.assertEqual(prompt_utils.prompt_subtitle(required), "1 argumento obrigatório")

        optional = self._prompt(arguments=[MCPPromptArgument("a"), MCPPromptArgument("b")])
        self.assertEqual(prompt_utils.prompt_subtitle(optional), "2 argumentos opcionais")

    def test_subtitle_never_empty(self):
        self.assertTrue(prompt_utils.prompt_subtitle(self._prompt()))

    def test_missing_required_arguments_detects_blank_values(self):
        """Caixa de texto em branco não é resposta: o servidor receberia `""`."""
        prompt = self._prompt(
            arguments=[
                MCPPromptArgument("pr", required=True),
                MCPPromptArgument("lang", required=True),
            ]
        )
        missing = prompt_utils.missing_required_arguments(prompt, {"pr": "   "})
        self.assertEqual([a.name for a in missing], ["pr", "lang"])

    def test_missing_required_arguments_is_empty_when_filled(self):
        prompt = self._prompt(arguments=[MCPPromptArgument("pr", required=True)])
        self.assertEqual(prompt_utils.missing_required_arguments(prompt, {"pr": "42"}), [])

    def test_missing_required_arguments_ignores_optionals(self):
        prompt = self._prompt(arguments=[MCPPromptArgument("tone", required=False)])
        self.assertEqual(prompt_utils.missing_required_arguments(prompt, {}), [])

    def test_clean_arguments_strips_and_drops_blanks(self):
        cleaned = prompt_utils.clean_arguments({"pr": "  42  ", "tone": "", "lang": "   "})
        self.assertEqual(cleaned, {"pr": "42"})

    def test_clean_arguments_restricts_to_declared_names(self):
        """Argumento extra faz servidor estrito rejeitar a chamada inteira."""
        prompt = self._prompt(arguments=[MCPPromptArgument("pr")])
        cleaned = prompt_utils.clean_arguments({"pr": "42", "injetado": "x"}, prompt)
        self.assertEqual(cleaned, {"pr": "42"})

    def test_clean_arguments_without_prompt_keeps_every_non_empty_key(self):
        self.assertEqual(
            prompt_utils.clean_arguments({"a": "1", "b": ""}), {"a": "1"}
        )

    def test_clean_arguments_handles_none_and_non_string_keys(self):
        self.assertEqual(prompt_utils.clean_arguments(None), {})
        self.assertEqual(prompt_utils.clean_arguments({"": "x"}), {})
        self.assertEqual(prompt_utils.clean_arguments({1: "x"}), {})

    def test_clean_arguments_ignores_none_values(self):
        self.assertEqual(prompt_utils.clean_arguments({"a": None}), {})


# ----------------------------------------------------------------------
# Paleta de comandos
# ----------------------------------------------------------------------


def _pump_until(predicate, timeout: float = 3.0) -> bool:
    """Drena o `GMainContext` até `predicate()` virar verdadeiro ou estourar o tempo.

    Necessário porque o texto do prompt chega por `GLib.idle_add` a partir de uma
    thread; sem drenar o contexto o `idle` nunca roda e o teste falharia por
    motivo errado.
    """
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while context.iteration(False):
            pass
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _content_root(widget):
    """Raiz navegável de `widget`.

    Armadilha: `Adw.Dialog` **não** é um contêiner comum — `get_first_child()`
    devolve ``None`` e o conteúdo só sai de `get_child()`. Varrer a árvore a
    partir do diálogo sem esta correção não encontra nada, o que faria o teste
    passar por engano se ele afirmasse apenas "não achei".
    """
    if isinstance(widget, Adw.Dialog):
        return widget.get_child()
    return widget


def _find(widget, predicate) -> list:
    """Todos os descendentes de `widget` (sem incluí-lo) que satisfazem `predicate`."""
    root = _content_root(widget)
    if root is None:
        return []

    found = []
    child = root.get_first_child()
    while child is not None:
        if predicate(child):
            found.append(child)
        found.extend(_find(child, predicate))
        child = child.get_next_sibling()
    return found


def _button_labelled(root, text: str):
    """Primeiro `Gtk.Button` com o rótulo pedido — evita pegar o botão de voltar."""
    for widget in _find(root, lambda w: isinstance(w, Gtk.Button)):
        if widget.get_label() == text:
            return widget
    return None


class PromptPaletteTest(unittest.TestCase):
    """Prompts MCP aparecem no Ctrl+K e preenchem a entrada em vez de agir."""

    @classmethod
    def setUpClass(cls):
        cls.Adw = Adw

    def setUp(self):
        from zorin_copilot.ui.app import CopilotWindow

        self.app = self.Adw.Application(application_id="org.zorin.copilot.test.mcp-prompts")
        self.win = CopilotWindow(self.app)
        self.addCleanup(self._destroy)
        self.mgr = self.win.mcp_manager

    def _destroy(self):
        from tests._sandbox import destroy_leftover_toplevels

        destroy_leftover_toplevels()

    def _prime(self, prompts: list[MCPPrompt]) -> None:
        """Coloca prompts no cache do gerenciador sem falar com servidor nenhum."""
        self.mgr._cached_prompts = list(prompts)

    def _prompt(self, name="code_review", server="github", **kwargs) -> MCPPrompt:
        return MCPPrompt(name=name, server_name=server, **kwargs)

    def _commands_by_name(self) -> dict[str, Any]:
        return {c.name: c for c in self.win.palette_commands()}

    def test_prompt_appears_as_a_palette_command(self):
        self._prime([self._prompt(description="Revisa um PR")])

        commands = self._commands_by_name()

        self.assertIn("mcp-prompt::mcp__github__code_review", commands)
        command = commands["mcp-prompt::mcp__github__code_review"]
        self.assertEqual(command.title, "Code review")
        self.assertEqual(command.subtitle, "Revisa um PR")

    def test_prompt_commands_are_absent_when_mcp_is_disabled(self):
        self._prime([self._prompt()])
        self.win.config.mcp_enabled = False

        names = self._commands_by_name()

        self.assertNotIn("mcp-prompt::mcp__github__code_review", names)

    def test_no_prompts_means_no_extra_commands(self):
        self._prime([])
        names = self._commands_by_name()
        self.assertFalse([n for n in names if prompt_utils.is_prompt_command(n)])

    def test_prompt_command_is_not_routed_through_the_static_handlers(self):
        """Regressão: sem roteamento por prefixo, escolher o prompt não fazia nada.

        `run_palette_command` procura o nome no dicionário de handlers, que só tem
        comandos nativos. Um nome dinâmico cairia no `logger.warning` e a paleta
        fecharia em silêncio.
        """
        self._prime([self._prompt()])
        routed: list[str] = []
        self.win._run_mcp_prompt = routed.append

        self.win.run_palette_command(
            self._commands_by_name()["mcp-prompt::mcp__github__code_review"]
        )

        self.assertEqual(routed, ["mcp__github__code_review"])

    def test_prompts_are_absent_from_the_static_handler_map(self):
        """O invariante que `test_command_palette` precisa respeitar.

        Com prompts presentes, os comandos nativos continuam todos cobertos pelo
        mapa estático, e os de prompt deliberadamente não estão — é o roteamento
        por prefixo que os atende. Sem esta distinção explícita, o teste de
        "todo comando tem handler" acusaria defeito onde há desenho.
        """
        self._prime([self._prompt(), self._prompt(name="resumir", server="beta")])

        commands = self.win.palette_commands()
        handlers = set(self.win._palette_handlers())
        prompt_names = {c.name for c in commands if prompt_utils.is_prompt_command(c.name)}
        native_names = {
            c.name for c in commands if not prompt_utils.is_prompt_command(c.name)
        }

        self.assertEqual(len(prompt_names), 2)
        self.assertEqual(prompt_names & handlers, set())
        self.assertEqual(native_names - handlers, set())

    def test_prompt_without_arguments_fills_the_entry(self):
        self._prime([self._prompt()])
        self.mgr.render_prompt = lambda *a, **k: "TEXTO DO PROMPT"

        self.win._run_mcp_prompt("mcp__github__code_review")

        self.assertTrue(_pump_until(lambda: "TEXTO DO PROMPT" in self.win.entry.get_text()))
        self.assertEqual(self.win.entry.get_text(), "TEXTO DO PROMPT")

    def test_prompt_text_is_appended_to_existing_entry_content(self):
        self._prime([self._prompt()])
        self.mgr.render_prompt = lambda *a, **k: "NOVO"
        self.win.entry.set_text("já escrito")

        self.win._run_mcp_prompt("mcp__github__code_review")

        self.assertTrue(_pump_until(lambda: "NOVO" in self.win.entry.get_text()))
        self.assertEqual(self.win.entry.get_text(), "já escrito NOVO")

    def test_empty_server_response_shows_a_toast_instead_of_blank_entry(self):
        self._prime([self._prompt()])
        self.mgr.render_prompt = lambda *a, **k: ""
        toasts: list[str] = []
        self.win.show_toast = toasts.append

        self.win._run_mcp_prompt("mcp__github__code_review")

        self.assertTrue(_pump_until(lambda: bool(toasts)))
        self.assertEqual(self.win.entry.get_text(), "")

    def test_prompt_that_disappeared_shows_a_toast(self):
        """O servidor pode cair entre abrir a paleta e escolher o comando."""
        self._prime([])
        toasts: list[str] = []
        self.win.show_toast = toasts.append

        self.win._run_mcp_prompt("mcp__github__code_review")

        self.assertEqual(len(toasts), 1)

    def test_arguments_dialog_collects_values_and_renders(self):
        prompt = self._prompt(
            arguments=[
                MCPPromptArgument("pr", "Número do PR", required=True),
                MCPPromptArgument("tone", "Tom", required=False),
            ]
        )
        self._prime([prompt])

        captured: dict[str, Any] = {}
        self.mgr.render_prompt = lambda name, args=None: captured.update(
            {"name": name, "args": args}
        ) or "pronto"

        presented: list[Any] = []
        # `present` é suprimido (o diálogo nunca é mapeado) e `close` também:
        # fechar um diálogo não apresentado gera um CRITICAL do Libadwaita que
        # polui a saída sem indicar defeito algum.
        with patch.object(Adw.Dialog, "present", lambda dlg, parent: presented.append(dlg)), \
                patch.object(Adw.Dialog, "close", lambda dlg: None):
            self.win._open_prompt_arguments_dialog(prompt, prompt.qualified_name)

        self.assertEqual(len(presented), 1)
        dialog = presented[0]

        rows = _find(dialog, lambda w: isinstance(w, Adw.EntryRow))
        self.assertEqual(len(rows), 2)

        by_title = {row.get_title(): row for row in rows}
        by_title["pr"].set_text("  42  ")
        by_title["tone"].set_text("")

        button = _button_labelled(dialog, "Inserir")
        self.assertIsNotNone(button)
        button.emit("clicked")

        self.assertTrue(_pump_until(lambda: "args" in captured))
        self.assertEqual(captured["name"], "mcp__github__code_review")
        # `tone` vazio é descartado; `pr` perde os espaços das pontas.
        self.assertEqual(captured["args"], {"pr": "42"})

    def test_arguments_dialog_marks_required_rows(self):
        prompt = self._prompt(
            arguments=[
                MCPPromptArgument("pr", required=True),
                MCPPromptArgument("tone", required=False),
            ]
        )
        self._prime([prompt])

        presented: list[Any] = []
        with patch.object(Adw.Dialog, "present", lambda dlg, parent: presented.append(dlg)), \
                patch.object(Adw.Dialog, "close", lambda dlg: None):
            self.win._open_prompt_arguments_dialog(prompt, prompt.qualified_name)

        rows = _find(presented[0], lambda w: isinstance(w, Adw.EntryRow))
        badged = [
            row
            for row in rows
            if _find(row, lambda w: isinstance(w, Gtk.Label) and w.get_text() == "obrigatório")
        ]

        self.assertEqual(len(badged), 1)
        self.assertEqual(badged[0].get_title(), "pr")

    def test_confirming_with_missing_required_argument_does_not_call_the_server(self):
        """Sem o gate, o servidor devolveria erro depois de o usuário já ter confirmado."""
        prompt = self._prompt(arguments=[MCPPromptArgument("pr", required=True)])
        self._prime([prompt])

        called: list[Any] = []
        self.mgr.render_prompt = lambda *a, **k: called.append(a) or "nunca"
        toasts: list[str] = []
        self.win.show_toast = toasts.append

        with patch.object(Adw.Dialog, "present", lambda dlg, parent: None):
            self.win._open_prompt_arguments_dialog(prompt, prompt.qualified_name)

        self.win._render_mcp_prompt(prompt.qualified_name, {})

        self.assertEqual(called, [])
        self.assertEqual(len(toasts), 1)
        self.assertIn("pr", toasts[0])

    def test_dialog_is_only_opened_when_the_prompt_has_arguments(self):
        """Pedir confirmação para um prompt sem parâmetros é um passo sem decisão."""
        self._prime([self._prompt()])
        self.mgr.render_prompt = lambda *a, **k: "sem argumentos"

        opened: list[Any] = []
        self.win._open_prompt_arguments_dialog = lambda *a, **k: opened.append(a)

        self.win._run_mcp_prompt("mcp__github__code_review")

        self.assertEqual(opened, [])
        self.assertTrue(_pump_until(lambda: self.win.entry.get_text() == "sem argumentos"))


if __name__ == "__main__":
    unittest.main()
