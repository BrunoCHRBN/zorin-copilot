"""Testes do §2.3 — seção "Conexões" (MCP + Home Assistant).

Ver `docs/PROPOSTA_REFORMULACAO_GUI.md` §2.3. São quatro mudanças em MCP e uma
em Home Assistant:

1. **Agrupar por tipo.** Seis servidores com a mesma silhueta, sem responder à
   primeira pergunta de quem vai ligar um deles: *o que isto alcança?*
2. **O aviso de token pertence à linha.** O erro tem de estar na linha fechada e
   visível, não dissolvido no fim de um subtítulo longo.
3. **"Adicionar servidor…".** Antes, a única porta era editar o JSON à mão.
4. **Prévia de entidades.** "Testar" dizia só "conectado"; conectar não prova que
   a integração enxerga a casa.

A armadilha que mais merece teste: a classificação casa por **token inteiro**,
nunca por substring. `github` contém `git`, e casar por substring jogaria o
servidor do GitHub no grupo "Nesta máquina" — o oposto do que a categoria existe
para dizer. O mesmo vale para a URL: o esquema `https://` é transporte, não
capacidade, e sem removê-lo todo servidor remoto viraria "Web".
"""

import json
import os
import tempfile
import unittest

# Importado explicitamente: `import unittest` **não** expõe `unittest.mock` em
# Python 3.14, e o arquivo usa `unittest.mock.patch.object`. Sem esta linha o
# módulo só passa quando outro arquivo de teste já importou `unittest.mock` no
# mesmo processo — ou seja, passa na suíte completa e falha ao ser rodado
# sozinho, com `AttributeError: module 'unittest' has no attribute 'mock'`.
import unittest.mock  # noqa: F401

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402

require_gtk4()
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

try:  # pragma: no cover - depende de como a suíte é invocada
    from tests._gtk_helpers import descendants
except ImportError:  # pragma: no cover
    from _gtk_helpers import descendants

from zorin_copilot.mcp.categories import (  # noqa: E402
    CATEGORY_DESCRIPTIONS,
    CATEGORY_LABELS,
    CATEGORY_LOCAL,
    CATEGORY_ORDER,
    CATEGORY_OTHER,
    CATEGORY_SERVICE,
    CATEGORY_WEB,
    classify_mcp_server,
    group_by_category,
    is_remote,
    normalize_tokens,
    strip_scheme,
)
from zorin_copilot.mcp.manager import MCPManager  # noqa: E402
from zorin_copilot.ui.preferences import (  # noqa: E402
    HA_PREVIEW_LIMIT,
    PreferencesDialog,
    describe_entity_chip,
    select_preview_entities,
)


def server(name, command="uvx algo", **extra):
    """Item no formato de `MCPManager.get_status_summary()`."""
    item = {
        "name": name,
        "command": command,
        "transport": "stdio",
        "url": "",
        "enabled": True,
        "description": "",
        "state": "disconnected",
        "error": "",
        "tool_count": 0,
        "tool_details": [],
    }
    item.update(extra)
    return item


def expanders_in(group) -> list[Adw.ExpanderRow]:
    """Linhas de servidor dentro de um grupo.

    Conta só `Adw.ExpanderRow`: as ferramentas listadas dentro de um expansor são
    `Adw.ActionRow` e não devem entrar na conta — foi exatamente esse tipo de
    varredura ingênua que fez o filtro do seletor de modelos parecer funcionar
    sem funcionar.
    """
    return [w for w in descendants(group) if isinstance(w, Adw.ExpanderRow)]


def action_rows_in(widget) -> list[Adw.ActionRow]:
    """Linhas de ação soltas dentro de `widget`.

    Exclui tudo que vive dentro de um `Adw.ExpanderRow` — o cabeçalho dele (que é
    um `Adw.ActionRow` interno) e as linhas que ele revela. Sem isso, uma lista
    de servidores apareceria com o dobro de linhas.
    """
    inside_expander: set = set()
    for expander in (w for w in descendants(widget) if isinstance(w, Adw.ExpanderRow)):
        inside_expander.update(descendants(expander))
    return [
        w
        for w in descendants(widget)
        if isinstance(w, Adw.ActionRow) and w not in inside_expander
    ]


def inner_rows_of(expander: Adw.ExpanderRow) -> list[Adw.ActionRow]:
    """As linhas que um expansor revela.

    `Adw.ExpanderRow` **não** expõe `get_rows()` — esse método existe no
    `Adw.PreferencesGroup`. E varrer por `Adw.ActionRow` também não serve: o
    próprio cabeçalho do expansor é um `Adw.ActionRow` interno, dentro de um
    `Gtk.ListBox` que fica **fora** do `Gtk.Revealer`. Contá-lo daria uma linha a
    mais em toda contagem. As linhas de verdade vivem dentro do `Revealer`.
    """
    rows: list[Adw.ActionRow] = []
    for revealer in (w for w in descendants(expander) if isinstance(w, Gtk.Revealer)):
        rows.extend(w for w in descendants(revealer) if isinstance(w, Adw.ActionRow))
    return rows


def visible_switches_in(widget) -> list[Gtk.Switch]:
    """Switches **visíveis** dentro de `widget`.

    `Adw.ExpanderRow` carrega um switch interno (a propriedade
    `show-enable-switch`) que vive na árvore mesmo desligado. Sem filtrar por
    visibilidade, um expansor com o nosso switch apareceria com **dois** — e o
    teste passaria a medir o switch errado.
    """
    return [
        w
        for w in descendants(widget)
        if isinstance(w, Gtk.Switch) and w.get_visible()
    ]


class FakeMcpManager:
    """Manager de mentira: a UI só precisa do resumo e do liga/desliga."""

    def __init__(self, summary):
        self.summary = summary
        self.toggled: list[tuple[str, bool]] = []

    def get_status_summary(self):
        return list(self.summary)

    def set_server_enabled(self, name, enabled):
        self.toggled.append((name, enabled))
        return True


class McpCategoryTest(unittest.TestCase):
    """A classificação, isolada de qualquer widget."""

    def test_github_is_a_service_not_a_local_server(self):
        """`github` contém `git`; o casamento tem de ser por token inteiro."""
        self.assertEqual(
            classify_mcp_server(server("github", "npx server-github")),
            CATEGORY_SERVICE,
        )
        self.assertEqual(
            classify_mcp_server(server("git", "uvx mcp-server-git")),
            CATEGORY_LOCAL,
        )

    def test_partial_token_does_not_match(self):
        """`digit` contém `git` — e ainda assim não é Git."""
        self.assertEqual(classify_mcp_server(server("digit")), CATEGORY_OTHER)

    def test_local_servers(self):
        for name in ("git", "sqlite", "filesystem", "fs", "memory"):
            with self.subTest(name=name):
                self.assertEqual(classify_mcp_server(server(name)), CATEGORY_LOCAL)

    def test_web_servers(self):
        for name in ("fetch", "duckduckgo", "brave-search", "puppeteer"):
            with self.subTest(name=name):
                self.assertEqual(classify_mcp_server(server(name)), CATEGORY_WEB)

    def test_service_servers(self):
        for name in ("github", "gitlab", "sentry", "slack", "notion"):
            with self.subTest(name=name):
                self.assertEqual(classify_mcp_server(server(name)), CATEGORY_SERVICE)

    def test_command_and_args_are_used_as_well_as_the_name(self):
        """O nome pode ser genérico e o comando dizer tudo."""
        item = server("servidor-1", "uvx mcp-server-sqlite --db-path /x.db")
        self.assertEqual(classify_mcp_server(item), CATEGORY_LOCAL)

    def test_url_scheme_does_not_make_a_server_web(self):
        """O bug encontrado na implementação: `https` é transporte, não capacidade.

        Sem remover o esquema antes de tokenizar, todo servidor remoto casava com
        o token `https` e ia parar em "Web" mesmo sem buscar nada na internet.
        """
        item = server("servidor-remoto", "", transport="sse", url="https://casa.local/mcp")
        self.assertEqual(classify_mcp_server(item), CATEGORY_SERVICE)

    def test_url_host_still_counts_as_a_hint(self):
        item = server("mcp-1", "", transport="sse", url="https://api.github.com/mcp")
        self.assertEqual(classify_mcp_server(item), CATEGORY_SERVICE)

    def test_remote_with_unknown_name_falls_back_to_service(self):
        item = server("xyz", "", transport="sse", url="https://exemplo.com/sse")
        self.assertEqual(classify_mcp_server(item), CATEGORY_SERVICE)

    def test_unknown_local_server_falls_to_other(self):
        """Sem pista nenhuma, a resposta honesta é "não sei", não um palpite."""
        self.assertEqual(classify_mcp_server(server("meu-servidor")), CATEGORY_OTHER)

    def test_description_is_not_used_as_evidence(self):
        """A descrição é prosa escrita à mão — classificar por ela é adivinhar.

        Um servidor local cuja descrição fala em "pesquisas na web" continua
        local: o que ele executa é que decide.
        """
        item = server("sqlite", "uvx mcp-server-sqlite")
        item["description"] = "Pesquisas na web sobre mercado e concorrência"
        self.assertEqual(classify_mcp_server(item), CATEGORY_LOCAL)

    def test_malformed_items_never_raise(self):
        for bad in (None, {}, "texto", 42, [], {"name": None, "command": None}):
            with self.subTest(bad=bad):
                self.assertEqual(classify_mcp_server(bad), CATEGORY_OTHER)

    def test_precedence_prefers_the_wider_reach(self):
        """Quando casa em mais de um conjunto, vence o alcance mais amplo.

        É o que importa para decidir se confia: um servidor que pode tocar tanto
        a internet quanto os arquivos locais é anunciado pelo que alcança mais
        longe.
        """
        item = server("filesystem-web", "npx server-filesystem-fetch")
        self.assertEqual(classify_mcp_server(item), CATEGORY_WEB)

    def test_normalize_tokens_splits_and_lowercases(self):
        self.assertEqual(
            normalize_tokens("mcp-server-git"), {"mcp", "server", "git"}
        )
        self.assertEqual(normalize_tokens(["A", "b-c"]), {"a", "b", "c"})
        self.assertEqual(normalize_tokens(None, "", 7), {"7"})

    def test_strip_scheme(self):
        self.assertEqual(strip_scheme("https://x/y"), "x/y")
        self.assertEqual(strip_scheme("sse://x"), "x")
        self.assertEqual(strip_scheme(None), "")
        self.assertEqual(strip_scheme("sem-esquema"), "sem-esquema")

    def test_is_remote(self):
        self.assertTrue(is_remote({"transport": "sse"}))
        self.assertTrue(is_remote({"url": "https://x", "command": ""}))
        self.assertFalse(is_remote({"transport": "stdio", "command": "uvx"}))
        self.assertFalse(is_remote({"url": "https://x", "command": "uvx"}))
        self.assertFalse(is_remote({}))

    def test_every_category_has_a_label_and_a_description(self):
        for category in CATEGORY_ORDER:
            with self.subTest(category=category):
                self.assertTrue(CATEGORY_LABELS.get(category))
                self.assertTrue(CATEGORY_DESCRIPTIONS.get(category))

    def test_labels_do_not_promise_more_than_they_know(self):
        """A descrição de "Outros" admite a ignorância em vez de disfarçá-la."""
        self.assertIn("não dizem", CATEGORY_DESCRIPTIONS[CATEGORY_OTHER])


class McpGroupingTest(unittest.TestCase):
    """`group_by_category`: ordem, conteúdo e grupos vazios."""

    def test_groups_follow_the_display_order(self):
        items = [
            server("github", "npx server-github"),
            server("fetch"),
            server("sqlite"),
        ]
        grouped = group_by_category(items)
        self.assertEqual([c for c, _ in grouped], [CATEGORY_LOCAL, CATEGORY_WEB, CATEGORY_SERVICE])

    def test_empty_categories_are_omitted(self):
        grouped = group_by_category([server("sqlite")])
        self.assertEqual([c for c, _ in grouped], [CATEGORY_LOCAL])

    def test_input_order_is_preserved_inside_a_group(self):
        items = [server("git"), server("filesystem"), server("sqlite")]
        _, rows = group_by_category(items)[0]
        self.assertEqual([r["name"] for r in rows], ["git", "filesystem", "sqlite"])

    def test_no_items_gives_no_groups(self):
        self.assertEqual(group_by_category([]), [])

    def test_real_world_configuration_lands_where_expected(self):
        """A configuração que existe nesta máquina, classificada."""
        items = [
            server("git", "uvx mcp-server-git"),
            server("fetch", "uvx mcp-server-fetch"),
            server("filesystem", "npx server-filesystem /home"),
            server("duckduckgo", "uvx duckduckgo-mcp-server"),
            server("sqlite", "uvx mcp-server-sqlite"),
            server("github", "npx @modelcontextprotocol/server-github"),
        ]
        grouped = dict(group_by_category(items))
        self.assertEqual(
            [r["name"] for r in grouped[CATEGORY_LOCAL]],
            ["git", "filesystem", "sqlite"],
        )
        self.assertEqual([r["name"] for r in grouped[CATEGORY_WEB]], ["fetch", "duckduckgo"])
        self.assertEqual([r["name"] for r in grouped[CATEGORY_SERVICE]], ["github"])


class McpPageGroupingUiTest(unittest.TestCase):
    """Os grupos como o usuário os vê."""

    def setUp(self):
        self.dlg = PreferencesDialog(None)

    def _apply(self, summary):
        self.dlg._mcp_manager = FakeMcpManager(summary)
        self.dlg._populate_mcp_servers()

    def test_one_group_per_category_with_its_label(self):
        self._apply([
            server("sqlite"),
            server("fetch"),
            server("github", "npx server-github"),
        ])
        titles = [g.get_title() for g in self.dlg._mcp_groups]
        self.assertEqual(
            titles,
            [CATEGORY_LABELS[CATEGORY_LOCAL], CATEGORY_LABELS[CATEGORY_WEB], CATEGORY_LABELS[CATEGORY_SERVICE]],
        )

    def test_groups_carry_the_category_description(self):
        self._apply([server("sqlite")])
        self.assertEqual(
            self.dlg._mcp_groups[0].get_description(),
            CATEGORY_DESCRIPTIONS[CATEGORY_LOCAL],
        )

    def test_servers_are_split_across_their_groups(self):
        self._apply([
            server("sqlite"),
            server("fetch"),
            server("github", "npx server-github"),
        ])
        per_group = [len(expanders_in(g)) for g in self.dlg._mcp_groups]
        self.assertEqual(per_group, [1, 1, 1])

    def test_a_group_only_appears_if_it_has_servers(self):
        self._apply([server("sqlite"), server("filesystem")])
        self.assertEqual(len(self.dlg._mcp_groups), 1)
        self.assertEqual(self.dlg._mcp_groups[0].get_title(), CATEGORY_LABELS[CATEGORY_LOCAL])

    def test_reloading_does_not_duplicate_groups(self):
        """Recarregar é comum (o arquivo é observado); não pode acumular."""
        self._apply([server("sqlite"), server("fetch")])
        first = len(self.dlg._mcp_groups)
        self._apply([server("sqlite"), server("fetch")])
        self.assertEqual(len(self.dlg._mcp_groups), first)
        self.assertEqual(len(self.dlg._mcp_rows), 2)

    def test_empty_summary_shows_a_single_group_with_guidance(self):
        self._apply([])
        self.assertEqual(len(self.dlg._mcp_groups), 1)
        self.assertEqual(self.dlg._mcp_groups[0].get_title(), "Servidores Configurados")
        titles = [r.get_title() for r in action_rows_in(self.dlg._mcp_groups[0])]
        self.assertIn("Nenhum servidor MCP configurado", titles)

    def test_switching_from_servers_to_empty_clears_the_old_groups(self):
        self._apply([server("sqlite"), server("fetch")])
        self._apply([])
        self.assertEqual(len(self.dlg._mcp_groups), 1)
        self.assertEqual(self.dlg._mcp_groups[0].get_title(), "Servidores Configurados")

    def test_the_add_server_door_exists(self):
        """O JSON deixa de ser a única porta."""
        rows = action_rows_in(self.dlg._mcp_page)
        titles = [r.get_title() for r in rows]
        self.assertIn("Adicionar servidor…", titles)

    def test_the_json_door_is_still_there(self):
        rows = action_rows_in(self.dlg._mcp_page)
        titles = [r.get_title() for r in rows]
        self.assertIn("Arquivo de Configuração (JSON)", titles)

    def test_add_row_comes_before_the_json_row(self):
        """A ordem diz qual é a porta principal para quem não é dev."""
        titles = [r.get_title() for r in action_rows_in(self.dlg._mcp_page)]
        self.assertLess(
            titles.index("Adicionar servidor…"),
            titles.index("Arquivo de Configuração (JSON)"),
        )


class McpErrorRowTest(unittest.TestCase):
    """O aviso de token pertence à linha que ele descreve."""

    def setUp(self):
        self.dlg = PreferencesDialog(None)
        self.mgr = FakeMcpManager([])

    def _row(self, **overrides):
        item = server("github", "npx @modelcontextprotocol/server-github")
        item.update(
            {
                "description": "Repositórios, issues e pull requests",
                "state": "error",
                "error": "requer token em GITHUB_PERSONAL_ACCESS_TOKEN",
            }
        )
        item.update(overrides)
        return self.dlg._build_mcp_server_row(item, self.mgr)

    def test_errored_row_is_marked_as_error(self):
        self.assertIn("error", self._row().get_css_classes())

    def test_healthy_row_is_not_marked_as_error(self):
        row = self._row(state="connected", error="", tool_count=12)
        self.assertNotIn("error", row.get_css_classes())

    def test_the_warning_leads_the_collapsed_line(self):
        """O subtítulo tem uma linha; o aviso não pode ser o que é cortado."""
        subtitle = self._row().get_subtitle()
        self.assertIn("GITHUB_PERSONAL_ACCESS_TOKEN", subtitle)
        self.assertTrue(
            subtitle.startswith("Erro:"),
            f"o aviso deveria abrir o subtítulo, veio: {subtitle!r}",
        )

    def test_the_description_survives_inside_the_expander(self):
        """O erro tomou o lugar da descrição — ela não pode simplesmente sumir."""
        row = self._row()
        texts = " ".join(
            (r.get_title() or "") + " " + (r.get_subtitle() or "")
            for r in inner_rows_of(row)
        )
        self.assertIn("Repositórios, issues e pull requests", texts)

    def test_connected_row_reports_the_tool_count(self):
        row = self._row(state="connected", error="", tool_count=12)
        self.assertIn("12 ferramenta(s)", row.get_subtitle())

    def test_error_row_lists_no_tools(self):
        """Sem ferramentas, o expansor mostra a descrição e o motivo — nada mais."""
        self.assertEqual(len(inner_rows_of(self._row())), 2)

    def test_row_title_is_the_server_name(self):
        self.assertEqual(self._row().get_title(), "github")

    def test_switch_toggles_the_server(self):
        row = self._row()
        # Premissa explícita: se um dia o switch interno do expansor for ligado,
        # o filtro por visibilidade deixa de distinguir e o teste avisa.
        self.assertFalse(row.get_show_enable_switch())
        switches = visible_switches_in(row)
        self.assertEqual(len(switches), 1)
        switches[0].set_active(False)
        self.assertEqual(self.mgr.toggled, [("github", False)])


class McpAddServerTest(unittest.TestCase):
    """O formulário e a gravação no arquivo."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "mcp_servers.json")
        # `add_server(..., enabled=True)` chama `start_server`, que sobe um
        # subprocesso de verdade. No sandbox o cache do `uv` é frio, então o
        # `uvx` baixaria o pacote do PyPI e o servidor ficaria vivo até o fim da
        # suíte — foi assim que `test_submits_and_writes`, que passa
        # "mcp-server-git" como argumento, pendurou a execução completa. Esta
        # classe testa gravação e validação de configuração, não ciclo de vida de
        # processo: nenhum subprocesso deve nascer aqui.
        patcher = unittest.mock.patch.object(MCPManager, "start_server", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mgr = MCPManager(config_path=self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def _config(self):
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_adds_and_persists(self):
        ok, message = self.mgr.add_server("meu", "uvx", args=["coisa"])
        self.assertTrue(ok, message)
        self.assertIn("meu", self._config()["mcpServers"])
        self.assertEqual(self._config()["mcpServers"]["meu"]["command"], "uvx")
        self.assertEqual(self._config()["mcpServers"]["meu"]["args"], ["coisa"])

    def test_the_new_server_shows_up_in_the_summary(self):
        self.mgr.add_server("meu", "uvx")
        names = [item["name"] for item in self.mgr.get_status_summary()]
        self.assertIn("meu", names)

    def test_rejects_an_empty_name(self):
        ok, message = self.mgr.add_server("   ", "uvx")
        self.assertFalse(ok)
        self.assertIn("nome", message.lower())

    def test_rejects_an_empty_command(self):
        ok, message = self.mgr.add_server("meu", "  ")
        self.assertFalse(ok)
        self.assertIn("comando", message.lower())

    def test_rejects_a_name_that_would_break_the_json(self):
        for bad in ('a"b', "a{b}", "a/b", "a b"):
            with self.subTest(bad=bad):
                ok, _ = self.mgr.add_server(bad, "uvx")
                self.assertFalse(ok)

    def test_rejects_a_duplicate_name(self):
        self.mgr.add_server("meu", "uvx")
        ok, message = self.mgr.add_server("meu", "outro")
        self.assertFalse(ok)
        self.assertIn("Já existe", message)

    def test_does_not_clobber_servers_added_outside_the_app(self):
        """`save_config` grava o dicionário inteiro.

        Um servidor acrescentado no arquivo desde o último `load_config` seria
        apagado em silêncio pela nossa gravação — por isso `add_server` relê
        antes de escrever.
        """
        self.mgr.add_server("primeiro", "uvx")
        data = self._config()
        data["mcpServers"]["externo"] = {"command": "uvx", "args": [], "enabled": True}
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

        self.mgr.add_server("segundo", "uvx")
        names = set(self._config()["mcpServers"])
        self.assertIn("externo", names)
        self.assertIn("segundo", names)

    def test_remove_server(self):
        self.mgr.add_server("meu", "uvx")
        ok, _ = self.mgr.remove_server("meu")
        self.assertTrue(ok)
        self.assertNotIn("meu", self._config()["mcpServers"])

    def test_remove_unknown_server_reports_it(self):
        ok, message = self.mgr.remove_server("fantasma")
        self.assertFalse(ok)
        self.assertIn("fantasma", message)


class McpAddServerFormTest(unittest.TestCase):
    """`_submit_new_mcp_server` — a regra do formulário, sem construir a janela."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "mcp_servers.json")
        # Mesma razão de `McpAddServerTest`: o formulário grava com
        # `enabled=True`, o que dispara `start_server`. Aqui o teste é sobre a
        # regra do formulário, não sobre o processo subir.
        patcher = unittest.mock.patch.object(MCPManager, "start_server", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dlg = PreferencesDialog(None)
        self.dlg._mcp_manager = MCPManager(config_path=self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_submits_and_writes(self):
        ok, message = self.dlg._submit_new_mcp_server("meu", "uvx", "mcp-server-git")
        self.assertTrue(ok, message)
        with open(self.path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["mcpServers"]["meu"]["args"], ["mcp-server-git"])

    def test_splits_arguments_respecting_quotes(self):
        """Um caminho com espaço tem de virar UM argumento."""
        self.dlg._submit_new_mcp_server(
            "meu", "uvx", 'server --db-path "/home/eu/Meus Dados/x.db"'
        )
        with open(self.path, encoding="utf-8") as fh:
            args = json.load(fh)["mcpServers"]["meu"]["args"]
        self.assertEqual(args, ["server", "--db-path", "/home/eu/Meus Dados/x.db"])

    def test_unbalanced_quotes_are_reported_not_raised(self):
        ok, message = self.dlg._submit_new_mcp_server("meu", "uvx", 'a "b')
        self.assertFalse(ok)
        self.assertIn("Argumentos inválidos", message)

    def test_empty_arguments_are_allowed(self):
        ok, message = self.dlg._submit_new_mcp_server("meu", "uvx", "   ")
        self.assertTrue(ok, message)

    def test_invalid_name_is_reported(self):
        ok, message = self.dlg._submit_new_mcp_server("nome com espaço", "uvx")
        self.assertFalse(ok)
        self.assertTrue(message)


class HaEntityPreviewTest(unittest.TestCase):
    """A amostra de entidades do Home Assistant."""

    def test_actionable_domains_come_first(self):
        states = [
            {"entity_id": "sensor.temp", "state": "23"},
            {"entity_id": "light.cozinha", "state": "on"},
            {"entity_id": "switch.portao", "state": "off"},
        ]
        picked = select_preview_entities(states, limit=2)
        self.assertEqual(
            [s["entity_id"] for s in picked], ["light.cozinha", "switch.portao"]
        )

    def test_respects_the_limit(self):
        states = [{"entity_id": f"light.l{i}", "state": "on"} for i in range(20)]
        self.assertEqual(len(select_preview_entities(states)), HA_PREVIEW_LIMIT)

    def test_others_fill_the_rest(self):
        states = [
            {"entity_id": "light.a", "state": "on"},
            {"entity_id": "sensor.b", "state": "1"},
        ]
        self.assertEqual(len(select_preview_entities(states, limit=2)), 2)

    def test_malformed_input_never_raises(self):
        for bad in (None, {}, "x", 42, [None, "y", 3]):
            with self.subTest(bad=bad):
                self.assertEqual(select_preview_entities(bad), [])

    def test_chip_text_uses_the_friendly_name_and_translates_the_state(self):
        self.assertEqual(
            describe_entity_chip(
                {"entity_id": "light.c", "state": "on", "attributes": {"friendly_name": "Cozinha"}}
            ),
            "Cozinha · ligado",
        )
        self.assertEqual(
            describe_entity_chip(
                {"entity_id": "switch.p", "state": "off", "attributes": {"friendly_name": "Portão"}}
            ),
            "Portão · desligado",
        )

    def test_chip_falls_back_to_the_entity_id(self):
        self.assertEqual(
            describe_entity_chip({"entity_id": "light.c", "state": "on"}), "light.c · ligado"
        )

    def test_unknown_state_passes_through_unchanged(self):
        """Traduzir só o que se conhece é melhor que inventar tradução."""
        self.assertEqual(
            describe_entity_chip({"entity_id": "sensor.t", "state": "23.5"}), "sensor.t · 23.5"
        )

    def test_chip_survives_a_malformed_entity(self):
        for bad in (None, {}, "x", 7):
            with self.subTest(bad=bad):
                self.assertTrue(describe_entity_chip(bad))


class HaPreviewUiTest(unittest.TestCase):
    """O grupo de prévia: aparece com entidades, some sem elas."""

    def setUp(self):
        self.dlg = PreferencesDialog(None)

    def test_hidden_before_any_test(self):
        self.assertFalse(self.dlg.ha_preview_group.get_visible())

    def test_shown_after_a_successful_test(self):
        self.dlg._render_ha_entities([{"entity_id": "light.c", "state": "on"}])
        self.assertTrue(self.dlg.ha_preview_group.get_visible())

    def test_hidden_again_when_nothing_is_discovered(self):
        self.dlg._render_ha_entities([{"entity_id": "light.c", "state": "on"}])
        self.dlg._render_ha_entities([])
        self.assertFalse(self.dlg.ha_preview_group.get_visible())

    def test_rendering_twice_does_not_stack_rows(self):
        self.dlg._render_ha_entities([{"entity_id": "light.c", "state": "on"}])
        self.dlg._render_ha_entities([{"entity_id": "light.s", "state": "off"}])
        self.assertEqual(len(self.dlg._ha_preview_rows), 1)

    def test_chips_are_not_clickable(self):
        """É leitura, não controle: um chip clicável prometeria ação inexistente."""
        self.dlg._render_ha_entities([{"entity_id": "light.c", "state": "on"}])
        chips = [
            w
            for w in descendants(self.dlg._ha_preview_rows[0])
            if isinstance(w, Gtk.Label) and "entity-chip" in w.get_css_classes()
        ]
        self.assertEqual(len(chips), 1)
        self.assertEqual(chips[0].get_text(), "light.c · ligado")

    def test_chip_carries_the_entity_id_in_the_tooltip(self):
        """O nome amigável é o que se lê; o id é o que se digita no campo acima."""
        self.dlg._render_ha_entities(
            [{"entity_id": "light.c", "state": "on", "attributes": {"friendly_name": "Cozinha"}}]
        )
        chips = [
            w
            for w in descendants(self.dlg._ha_preview_rows[0])
            if isinstance(w, Gtk.Label) and "entity-chip" in w.get_css_classes()
        ]
        self.assertEqual(chips[0].get_tooltip_text(), "light.c")

    def test_at_most_the_limit_is_rendered(self):
        states = [{"entity_id": f"light.l{i}", "state": "on"} for i in range(20)]
        self.dlg._render_ha_entities(states)
        chips = [
            w
            for w in descendants(self.dlg._ha_preview_rows[0])
            if isinstance(w, Gtk.Label) and "entity-chip" in w.get_css_classes()
        ]
        self.assertEqual(len(chips), HA_PREVIEW_LIMIT)


class EntityChipStyleTest(unittest.TestCase):
    """A classe do chip precisa de uma regra na folha.

    Lição já paga duas vezes neste projeto: o `.model-filter-bar` e o
    `.welcome-icon` ficaram no código depois que o widget que os usava saiu, e uma
    classe sem regra é uma mentira silenciosa — o widget parece estilizado e não
    está. O inverso também vale: se a regra sumir, o chip perde a forma sem que
    nenhum teste de comportamento perceba.
    """

    def test_the_stylesheet_defines_the_chip(self):
        from zorin_copilot.ui.style import GLASS_CSS

        self.assertIn(".entity-chip", GLASS_CSS)

    def test_the_chip_has_no_hover_state(self):
        """É leitura, não controle: não pode reagir ao ponteiro."""
        from zorin_copilot.ui.style import GLASS_CSS

        self.assertNotIn(".entity-chip:hover", GLASS_CSS)

    def test_the_chip_gets_a_background_in_both_themes(self):
        """Sem fundo, o chip não se distingue de um rótulo comum."""
        from zorin_copilot.ui.style import GLASS_CSS

        self.assertIn("window.light-glass .entity-chip", GLASS_CSS)
        self.assertIn("window.dark-glass .entity-chip", GLASS_CSS)


if __name__ == "__main__":
    unittest.main()
