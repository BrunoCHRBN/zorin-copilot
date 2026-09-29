# Decisão de design: estes testes cobrem as decisões da reformulação da GUI que
# não são óbvias pela leitura do código — a cerca espacial deixou de morar no topo,
# a sidebar passou a agrupar por data e a lixeira deixou de ser um botão fixo por
# linha. Sem cobertura, a próxima mudança de layout reverte isso em silêncio.
#
# Importante: `CopilotWindow` cria o `MemoryManager` apontando para o banco REAL
# (~/.local/share/zorin-copilot/memory.db). Construir a janela num teste, portanto,
# mexe no histórico do usuário. A base abaixo redireciona o banco para um arquivo
# temporário antes de instanciar a janela.

"""Testes de integração da reformulação da GUI (docs/PROPOSTA_REFORMULACAO_GUI.md)."""

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.core.memory import MemoryManager  # noqa: E402
from zorin_copilot.ui.app import CopilotWindow  # noqa: E402
from zorin_copilot.ui.widgets.sidebar import (  # noqa: E402
    SidebarPanel,
    group_topics_by_date,
)

_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")


class _IsolatedWindowTestCase(unittest.TestCase):
    """Constrói a janela real com um banco de memória temporário."""

    APP_ID = "org.zorin.copilot.test.reform"

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id=cls.APP_ID)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "memory.db"
        patcher = mock.patch(
            "zorin_copilot.ai.engine.MemoryManager",
            side_effect=lambda *a, **k: MemoryManager(db_path=db_path),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.win = CopilotWindow(self.app)


class HeaderAndStatusBarTest(_IsolatedWindowTestCase):
    """A cerca espacial é estado de ambiente: mora na barra de status, não no topo."""

    APP_ID = "org.zorin.copilot.test.reform.header"

    def test_fence_widget_is_hosted_by_status_bar(self):
        """O badge da cerca não é mais empacotado no header; a barra de status o hospeda."""
        fence = self.win.header.fence_menu_btn
        self.assertIsNotNone(fence)
        # Está na barra de status...
        self.assertIs(
            self.win.status_bar._fence_slot.get_first_child(),
            fence,
            "a cerca deveria estar no slot da barra de status",
        )
        # ...e não entre os filhos do header.
        header_children = []
        child = self.win.header.header.get_first_child()
        while child:
            header_children.append(child)
            child = child.get_next_sibling()
        self.assertNotIn(fence, header_children)

    def test_header_keeps_the_title(self):
        """O topo fica com comandos do assistente, sem telemetria de ambiente."""
        self.assertIsNotNone(self.win.header.window_title)
        self.assertEqual(self.win.header.window_title.get_title(), "Zorin Copilot")

    def test_tokens_are_not_in_the_top_badge(self):
        """O badge do topo identifica o cérebro; o consumo vive na barra de status."""
        from zorin_copilot.core.usage import TokenUsage

        self.win.engine.usage_tracker.record(
            TokenUsage(prompt_tokens=80, completion_tokens=20),
            provider="gemini",
            model="gemini-1.5-flash",
        )
        self.win.header.refresh_token_usage()
        self.assertNotIn("tokens", self.win.header.status_badge.get_text())

        self.win.status_bar.refresh_tokens()
        self.assertIn("100 tokens", self.win.status_bar.tokens_lbl.get_text())

    def test_zero_tokens_item_is_hidden_until_first_use(self):
        """Sem uso, o item de tokens não aparece (antes mostrava "0 tokens")."""
        self.win.status_bar.refresh_tokens()
        self.assertFalse(self.win.status_bar.tokens_item.get_visible())

    def test_fence_labels_have_no_emoji(self):
        """Rótulos de estado são texto + ícone simbólico — sem emoji (diretriz anti-slop)."""
        self.win.header.refresh_fence_label()
        self.assertIsNone(_EMOJI.search(self.win.header.fence_lbl.get_text()))


class SidebarGroupingIntegrationTest(_IsolatedWindowTestCase):
    """A listagem agrupa por data e não oferece lixeira fixa por linha."""

    APP_ID = "org.zorin.copilot.test.reform.sidebar"

    def setUp(self):
        super().setUp()
        self.sidebar = self.win.sidebar
        self.win.engine.memory.save_chat_topic("t1", "Primeira conversa", [], True)
        self.win.engine.memory.save_chat_topic("t2", "Chamada de Voz e Tela ao Vivo", [], True)

    def _rows(self) -> list:
        rows = []
        child = self.sidebar.history_listbox.get_first_child()
        while child:
            rows.append(child)
            child = child.get_next_sibling()
        return rows

    def _group_labels(self) -> list[str]:
        return [
            row.get_child().get_text()
            for row in self._rows()
            if isinstance(row.get_child(), Gtk.Label)
            and row.get_child().has_css_class("sidebar-group-label")
        ]

    def test_populate_renders_group_header_and_rows(self):
        self.sidebar.populate()
        self.assertGreaterEqual(len(self._rows()), 3, "cabeçalho de grupo + 2 conversas")
        self.assertIn("Hoje", self._group_labels())

    def test_voice_topic_gets_a_distinct_icon(self):
        """Toda chamada de voz tem o mesmo título; o ícone é o diferenciador."""
        self.sidebar.populate()
        icons = []
        for row in self._rows():
            box = row.get_child()
            if not isinstance(box, Gtk.Box):
                continue
            first = box.get_first_child()
            if isinstance(first, Gtk.Image):
                icons.append(first.get_icon_name())
        self.assertIn("audio-input-microphone-symbolic", icons)
        self.assertIn("format-justification-symbolic", icons)

    def test_delete_button_is_hidden_until_hover(self):
        """A lixeira não fica clicável o tempo todo — evita exclusão acidental."""
        self.sidebar.populate()
        hidden_buttons = []
        for row in self._rows():
            box = row.get_child()
            if not isinstance(box, Gtk.Box):
                continue
            # O último filho é o slot de largura fixa que guarda a lixeira.
            slot = box.get_last_child()
            if isinstance(slot, Gtk.Box) and slot.get_first_child() is not None:
                hidden_buttons.append(slot.get_first_child())
        self.assertTrue(hidden_buttons, "esperava encontrar lixeiras nas linhas")
        for btn in hidden_buttons:
            self.assertFalse(btn.get_visible(), "a lixeira deve começar oculta")

    def test_filtering_uses_a_single_results_group(self):
        """Com filtro, agrupar por data perde sentido — vira uma seção 'Resultados'."""
        self.sidebar.populate(filter_query="Voz")
        self.assertEqual(self._group_labels(), ["Resultados"])

    def test_grouping_helper_is_wired_into_populate(self):
        """Sanidade: o helper usado pelo populate agrupa de fato por data."""
        groups = group_topics_by_date(
            [{"id": "a", "title": "a", "updated_at": "", "turn_count": 0}]
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][0], "Anterior")


class ClearHistoryConfirmationTest(_IsolatedWindowTestCase):
    """Limpar histórico é destrutivo e não pode disparar no clique direto."""

    APP_ID = "org.zorin.copilot.test.reform.clear"

    def test_clear_history_asks_for_confirmation_first(self):
        self.win.engine.memory.save_chat_topic("t1", "Conversa", [], True)
        before = len(self.win.engine.memory.list_chat_topics())

        with mock.patch.object(SidebarPanel, "_on_confirm_clear") as confirm:
            self.win.sidebar.clear_history()
            # Sem confirmação, nada é apagado.
            confirm.assert_not_called()
            self.assertEqual(len(self.win.engine.memory.list_chat_topics()), before)

    def test_clear_history_is_a_menu_item_not_a_footer_button(self):
        """O botão de limpar vive no menu ⋮ do cabeçalho da sidebar."""
        self.assertIsInstance(self.win.sidebar.clear_history_btn, Gtk.MenuButton)
        self.assertIsNotNone(self.win.sidebar.clear_history_btn.get_popover())


if __name__ == "__main__":
    unittest.main()
