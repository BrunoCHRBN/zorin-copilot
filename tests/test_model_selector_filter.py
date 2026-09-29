"""Testes do seletor de modelos — reformulação §3.1 do plano.

Cobrem quatro mudanças do plano (`docs/PROPOSTA_REFORMULACAO_GUI.md` §3.1):

1. **Ordem das seções**: nuvem antes de local. O usuário típico não tem modelo
   local nenhum, e a tela abria com uma seção que para ele estava vazia ou
   quebrada.
2. **Adequação ao hardware**: cada modelo local diz se cabe na máquina, em vez
   de deixar a conta de VRAM para quem não tem como fazê-la.
3. **Vocabulário neutro**: o card Dolphin perde o emoji e a tag "Sem filtro"
   deixa de vir em destaque — é característica, não argumento de venda.
4. **Campo de filtro**: a lista cresce com o número de modelos instalados; sem
   busca, escolher vira rolagem.

O filtro merece atenção especial porque **quase passou despercebido quebrado**.
A varredura das linhas de um grupo assumia filhos diretos, mas o Libadwaita
empacota as linhas num `Gtk.ListBox` interno dentro de uma `Gtk.Box` (o mesmo
truque que `Adw.ActionRow.add_prefix()` faz com prefixos). A varredura achava um
contêiner anônimo, e a guarda "linha sem título não é escondida" transformava
esse engano em "tudo casa com tudo": o campo aceitava texto e nada acontecia,
sem erro nenhum. Os testes de `RowScanTest` travam esse caminho.
"""

import unittest
from unittest.mock import MagicMock

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402

require_gtk4()
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

try:  # descoberta como pacote (`-t .`) vs. descoberta simples
    from tests._gtk_helpers import descendants, icon_names, labels
except ImportError:  # pragma: no cover - depende do runner
    from _gtk_helpers import descendants, icon_names, labels

from zorin_copilot.core.config import CopilotConfig  # noqa: E402
from zorin_copilot.ui.widgets.model_selector import (  # noqa: E402
    ModelSelectorDialog,
    _fold,
    _rows_of,
)


def label_widgets(widget):
    """Os `Gtk.Label` abaixo de `widget` — precisamos das classes CSS deles."""
    return [node for node in descendants(widget) if isinstance(node, Gtk.Label)]


def tag_label(row, text: str):
    """O rótulo de tag com o texto dado, ou None."""
    for node in label_widgets(row):
        if node.get_text() == text:
            return node
    return None


OLLAMA_MODELS = [
    {
        "name": "dolphin3:latest",
        "size": 4920757726,
        "details": {"parameter_size": "8.0B", "quantization_level": "Q4_K_M"},
    },
    {
        "name": "qwen2.5vl:7b",
        "size": 6000000000,
        "details": {"parameter_size": "7.6B", "quantization_level": "Q4_K_M"},
    },
    {
        "name": "llama3.2:1b",
        "size": 1300000000,
        "details": {"parameter_size": "1.2B", "quantization_level": "Q8_0"},
    },
]

LMSTUDIO_MODELS = [
    {"id": "Qwen3.5-9B-Uncensored-HauhauCS-Aggressive", "size": 5500000000},
]


class DialogTestCase(unittest.TestCase):
    """Base: diálogo construído com contexto falso, sem tocar em rede."""

    def setUp(self):
        ctx = MagicMock()
        ctx.config = CopilotConfig()
        self.dlg = ModelSelectorDialog(ctx)

    def filter_with(self, query: str) -> None:
        """Digita `query` na busca e reaplica o filtro."""
        self.dlg.search_entry.set_text(query)
        self.dlg._apply_filter()

    def visible_titles(self, group: Adw.PreferencesGroup) -> list[str]:
        return [row.get_title() for row in _rows_of(group) if row.get_visible()]

    def content_rows(self, group: Adw.PreferencesGroup) -> list[Adw.PreferencesRow]:
        """Só as linhas de conteúdo — sem as de estado ("Buscando modelos…").

        A linha de carregamento é uma linha do grupo como qualquer outra, então
        aparece em `_rows_of`. Quando o teste é sobre os modelos, ela é ruído.
        """
        return [row for row in _rows_of(group) if row not in self.dlg._always_visible_rows]

    def visible_groups(self) -> list[str]:
        return [g.get_title() for g in self.dlg._filterable_groups() if g.get_visible()]


class RowScanTest(DialogTestCase):
    """`_rows_of` tem de achar as linhas apesar do empacotamento do Libadwaita."""

    def test_scan_finds_rows_despite_the_internal_listbox(self):
        """O grupo não expõe as linhas como filhos diretos — a varredura desce."""
        children = []
        child = self.dlg.featured_group.get_first_child()
        while child is not None:
            children.append(child)
            child = child.get_next_sibling()

        # Filhos diretos são contêineres anônimos, nenhum deles uma linha.
        self.assertTrue(children)
        self.assertFalse(
            any(isinstance(c, Adw.PreferencesRow) for c in children),
            "se isto falhar, o Libadwaita mudou e a varredura recursiva pode ser revista",
        )

        rows = _rows_of(self.dlg.featured_group)
        self.assertEqual([r.get_title() for r in rows], ["Dolphin 3.1 (Llama 3.1 8B)"])

    def test_scan_returns_only_preference_rows(self):
        for group in self.dlg._filterable_groups():
            for row in _rows_of(group):
                self.assertIsInstance(row, Adw.PreferencesRow)

    def test_scan_reflects_removal(self):
        """Linha removida do grupo sai da varredura — sem lista paralela a manter."""
        self.dlg._render_models_ui(OLLAMA_MODELS, "", [])
        row = self.content_rows(self.dlg.ollama_group)[0]
        self.dlg.ollama_group.remove(row)
        self.assertNotIn(row, _rows_of(self.dlg.ollama_group))

    def test_scan_preserves_creation_order(self):
        self.dlg._render_models_ui(OLLAMA_MODELS, "", [])
        titles = [r.get_title() for r in self.content_rows(self.dlg.ollama_group)]
        self.assertEqual(titles, ["dolphin3:latest", "qwen2.5vl:7b", "llama3.2:1b"])


class FoldTest(unittest.TestCase):
    """A normalização do filtro preserva o que é conteúdo."""

    def test_strips_accents(self):
        self.assertEqual(_fold("Visão"), "visao")

    def test_lowercases(self):
        self.assertEqual(_fold("QWEN"), "qwen")

    def test_keeps_punctuation(self):
        """Ao contrário do `normalize_text` do wake word, a pontuação fica."""
        self.assertEqual(_fold("qwen2.5vl:7b"), "qwen2.5vl:7b")
        self.assertEqual(_fold("Q8_0"), "q8_0")

    def test_empty_text(self):
        self.assertEqual(_fold(""), "")


class FilterTest(DialogTestCase):
    """O filtro mostra o que casa e esconde o que não casa."""

    def setUp(self):
        super().setUp()
        self.dlg._render_models_ui(OLLAMA_MODELS, "", LMSTUDIO_MODELS)

    def test_empty_query_shows_everything(self):
        self.filter_with("")
        self.assertEqual(
            self.visible_titles(self.dlg.ollama_group),
            ["dolphin3:latest", "qwen2.5vl:7b", "llama3.2:1b"],
        )
        self.assertEqual(self.visible_titles(self.dlg.featured_group), ["Dolphin 3.1 (Llama 3.1 8B)"])

    def test_matching_hides_the_rest(self):
        self.filter_with("qwen")
        self.assertEqual(self.visible_titles(self.dlg.ollama_group), ["qwen2.5vl:7b"])
        self.assertEqual(self.visible_titles(self.dlg.featured_group), [])

    def test_groups_without_matches_are_hidden(self):
        """Seção sem linha é pior que a rolagem original."""
        self.filter_with("qwen")
        self.assertNotIn("Agentes em Nuvem", self.visible_groups())
        self.assertNotIn("Voz do Assistente de IA", self.visible_groups())

    def test_query_matching_nothing_hides_every_group(self):
        self.filter_with("zzzz")
        self.assertEqual(self.visible_groups(), [])

    def test_matches_quantization_in_specs(self):
        """Especificação técnica é pesquisável: "q8_0" acha o llama3.2."""
        self.filter_with("q8_0")
        self.assertEqual(self.visible_titles(self.dlg.ollama_group), ["llama3.2:1b"])

    def test_matches_tag_without_the_accent(self):
        """"visao" acha a tag "Visão" — a busca não exige acento."""
        self.filter_with("visao")
        self.assertEqual(self.visible_titles(self.dlg.ollama_group), ["qwen2.5vl:7b"])

    def test_abbreviated_query_finds_the_model_id(self):
        """Mesma busca difusa do Ctrl+K: "q25vl" acha "qwen2.5vl:7b"."""
        self.filter_with("q25vl")
        self.assertEqual(self.visible_titles(self.dlg.ollama_group), ["qwen2.5vl:7b"])

    def test_prose_in_a_subtitle_does_not_drag_the_row_in(self):
        """O ponto que separa este filtro do painel de comandos.

        "gemini" é subsequência de "Agente local sem censura nem recusas
        corporativas", então uma busca difusa sobre o texto inteiro traria o
        card do Dolphin junto com o Gemini. Ele não deve entrar: o acerto fraco
        em prosa longa não conta.
        """
        self.filter_with("gemini")
        self.assertEqual(self.visible_titles(self.dlg.featured_group), [])
        self.assertNotIn("Agente em Destaque", self.visible_groups())

    def test_exact_substring_in_a_subtitle_still_matches(self):
        """Em troca, acerto literal em prosa continua valendo."""
        self.filter_with("workbuddy")
        self.assertEqual(
            self.visible_titles(self.dlg.cloud_group),
            ["WorkBuddy AI (hy4-preview)"],
        )

    def test_filter_survives_a_rerender(self):
        """As linhas do Ollama são recriadas a cada busca em background."""
        self.filter_with("qwen")
        self.dlg._render_models_ui(OLLAMA_MODELS, "", LMSTUDIO_MODELS)
        self.assertEqual(self.visible_titles(self.dlg.ollama_group), ["qwen2.5vl:7b"])

    def test_clearing_the_query_restores_every_row(self):
        self.filter_with("qwen")
        self.filter_with("")
        self.assertEqual(len(self.visible_titles(self.dlg.ollama_group)), 3)
        self.assertEqual(len(self.visible_groups()), 6)


class StatusRowTest(DialogTestCase):
    """Linhas de diagnóstico não são conteúdo a filtrar."""

    def test_error_row_survives_a_query_that_matches_nothing(self):
        self.dlg._render_models_ui([], "connection refused", [])
        self.filter_with("zzzz")
        self.assertEqual(
            self.visible_titles(self.dlg.ollama_group),
            ["Ollama não encontrado"],
        )
        self.assertIn("Modelos Locais (Ollama)", self.visible_groups())

    def test_loading_row_holds_the_group_open_while_visible(self):
        """Enquanto a busca corre, a seção precisa aparecer para dizer isso."""
        self.dlg.ollama_loading_row.set_visible(True)
        self.filter_with("zzzz")
        self.assertIn("Modelos Locais (Ollama)", self.visible_groups())

    def test_hidden_loading_row_does_not_hold_the_group_open(self):
        """Terminada a busca, a linha sai da árvore visível e o grupo com ela.

        A linha continua *na árvore*, apenas oculta; contá-la assim mesmo
        manteria o grupo aberto e vazio para qualquer busca.
        """
        self.dlg.ollama_loading_row.set_visible(False)
        self.filter_with("")
        self.assertNotIn("Modelos Locais (Ollama)", self.visible_groups())

    def test_test_mode_leaves_no_empty_section(self):
        """Sem busca real (modo de teste), o grupo do Ollama não fica sozinho."""
        self.assertFalse(self.dlg.ollama_group.get_visible())
        self.assertNotIn("Modelos Locais (Ollama)", self.visible_groups())


class EmptyGroupTest(DialogTestCase):
    """Grupo sem linha nenhuma nunca aparece, nem com busca vazia."""

    def test_lmstudio_hidden_when_no_models(self):
        self.dlg._render_models_ui(OLLAMA_MODELS, "", [])
        self.filter_with("")
        self.assertNotIn("LM Studio / Servidor Local Compatível", self.visible_groups())

    def test_lmstudio_hidden_when_items_carry_no_id(self):
        """Servidor responde, mas nenhum item é aproveitável: nada a mostrar.

        O render marcava o grupo como visível antes de acrescentar as linhas;
        quando nenhuma linha sobrevivia, a marcação deixava um cabeçalho de
        seção sozinho na tela.
        """
        self.dlg._render_models_ui(OLLAMA_MODELS, "", [{"id": "", "size": 0}])
        self.filter_with("")
        self.assertNotIn("LM Studio / Servidor Local Compatível", self.visible_groups())

    def test_lmstudio_appears_with_models(self):
        self.dlg._render_models_ui(OLLAMA_MODELS, "", LMSTUDIO_MODELS)
        self.filter_with("")
        self.assertIn("LM Studio / Servidor Local Compatível", self.visible_groups())


class SectionOrderTest(DialogTestCase):
    """Nuvem antes de local: a ordem segue quem tem o quê."""

    def test_cloud_comes_before_local(self):
        order = [g.get_title() for g in self.dlg._filterable_groups()]
        self.assertLess(order.index("Agentes em Nuvem"), order.index("Modelos Locais (Ollama)"))
        self.assertLess(
            order.index("Modelos Locais (Ollama)"),
            order.index("LM Studio / Servidor Local Compatível"),
        )

    def test_cloud_is_first_in_the_page(self):
        """Não basta estar na lista do filtro — tem de ser o primeiro na tela."""
        first = self.dlg._filterable_groups()[0]
        self.assertEqual(first.get_title(), "Agentes em Nuvem")

    def test_featured_comes_second(self):
        self.assertEqual(
            self.dlg._filterable_groups()[1].get_title(),
            "Agente em Destaque",
        )


class HardwareFitLineTest(DialogTestCase):
    """A conta de VRAM é do programa, não do usuário."""

    def test_local_rows_state_whether_they_fit(self):
        self.dlg._render_models_ui(OLLAMA_MODELS, "", [])
        for row in self.content_rows(self.dlg.ollama_group):
            text = " ".join(labels(row))
            self.assertIn("GB", text, f"{row.get_title()} não diz quanto ocupa")
            self.assertTrue(
                any(
                    marker in text
                    for marker in ("Cabe", "Aperta", "não cabe", "RAM", "VRAM", "sem dado")
                ),
                f"{row.get_title()} não dá veredito: {text!r}",
            )

    def test_row_keeps_specs_on_one_line(self):
        """Especificações e veredito convivem em duas linhas, não em prosa."""
        self.dlg._render_models_ui(OLLAMA_MODELS, "", [])
        row = self.content_rows(self.dlg.ollama_group)[0]
        subtitle = row.get_subtitle()
        self.assertIn("8.0B", subtitle)
        self.assertIn("Q4_K_M", subtitle)
        self.assertLessEqual(subtitle.count("\n"), 1)


class NeutralVocabularyTest(DialogTestCase):
    """§3.1: emoji em rótulo e apelo de "sem filtro" em destaque, não."""

    def test_dolphin_title_has_no_emoji(self):
        title = _rows_of(self.dlg.featured_group)[0].get_title()
        self.assertEqual(title, "Dolphin 3.1 (Llama 3.1 8B)")
        self.assertTrue(title.isascii(), f"título com caractere não-ASCII: {title!r}")

    def test_dolphin_keeps_the_symbolic_icon(self):
        """O ícone simbólico do Adwaita é o que substitui o emoji."""
        row = _rows_of(self.dlg.featured_group)[0]
        self.assertIn("system-run-symbolic", icon_names(row))

    def test_uncensored_tag_is_present_but_not_highlighted(self):
        """A tag existe; o que muda é ela não vir em destaque.

        "Sem filtro" é característica de treino, não vantagem — anunciá-la com
        a mesma ênfase das tags de caso de uso sugeria um argumento de venda.
        """
        row = _rows_of(self.dlg.featured_group)[0]
        tag = tag_label(row, "Sem filtro")
        self.assertIsNotNone(tag, "a tag saiu do card; era para ficar, só sem destaque")
        self.assertTrue(tag.has_css_class("dim-label"))
        self.assertFalse(tag.has_css_class("accent"))

    def test_use_case_tags_come_first(self):
        """Quem ajuda a escolher vem antes de quem apenas descreve."""
        row = _rows_of(self.dlg.featured_group)[0]
        texts = [node.get_text() for node in label_widgets(row)]
        self.assertLess(texts.index("Autônomo"), texts.index("Sem filtro"))

    def test_lmstudio_uncensored_tag_is_dim(self):
        self.dlg._render_models_ui([], "", LMSTUDIO_MODELS)
        row = _rows_of(self.dlg.lmstudio_group)[0]
        tag = tag_label(row, "Sem filtro")
        self.assertIsNotNone(tag)
        self.assertTrue(tag.has_css_class("dim-label"))


class FilterBarTest(DialogTestCase):
    """A barra de busca fica no topo e não rola junto."""

    def test_search_entry_exists_and_is_wired(self):
        self.assertTrue(hasattr(self.dlg, "search_entry"))
        self.assertIsInstance(self.dlg.search_entry, Gtk.SearchEntry)

    def test_placeholder_says_what_it_searches(self):
        """Vozes também são filtráveis — o rótulo precisa dizer isso."""
        text = self.dlg.search_entry.get_placeholder_text()
        self.assertIn("modelos", text.lower())
        self.assertIn("vozes", text.lower())

    def test_bar_is_a_top_bar_not_inside_the_scroll(self):
        """Rolagem cresce, busca não: o campo tem de ficar fora do que rola.

        `Adw.ToolbarView` não expõe leitura das barras (só `add_top_bar`), então
        a asserção é sobre a fronteira que importa: o campo está na janela e
        **não** está dentro da área rolável.
        """
        toolbar = self.dlg.get_child()
        self.assertIsInstance(toolbar, Adw.ToolbarView)

        self.assertIn(self.dlg.search_entry, descendants(toolbar))

        content = toolbar.get_content()
        self.assertIsNotNone(content)
        self.assertNotIn(self.dlg.search_entry, descendants(content))

    def test_typing_filters_without_a_button(self):
        """`search-changed` já dispara o filtro — não há botão "aplicar"."""
        self.dlg._render_models_ui(OLLAMA_MODELS, "", [])
        self.dlg.search_entry.set_text("qwen")
        self.dlg.search_entry.emit("search-changed")
        self.assertEqual(self.visible_titles(self.dlg.ollama_group), ["qwen2.5vl:7b"])

    def test_voices_are_filterable_too(self):
        self.filter_with("puck")
        self.assertIn("Voz do Assistente de IA", self.visible_groups())


if __name__ == "__main__":
    unittest.main()
