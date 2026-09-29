"""Testes da tela de boas-vindas — reformulação §1.3 do plano.

Cobrem as três mudanças (`docs/PROPOSTA_REFORMULACAO_GUI.md` §1.3):

1. **A estrela sai, entra o estado.** O posto mais visível da tela estava
   ocupado por uma estrela decorativa de 44px que não dizia nada sobre esta
   máquina nem sobre esta sessão.
2. **Os chips ganham hierarquia.** Quatro botões numa grade 2×2 tratavam como
   pares coisas de naturezas diferentes: "Resumir esta página" fala do que está
   na tela; "Voz ao Vivo" é um modo de interação.
3. **"Retomar" na própria tela vazia.** Quem volta ao app tinha de abrir a barra
   lateral para retomar uma conversa.

O item 2 tem uma armadilha que vale teste explícito: com contexto rico o motor
de sugestões gasta o orçamento inteiro no que está na tela, e o grupo dos modos
desapareceria justamente quando o usuário tem mais sobre o que falar. A reserva
de uma vaga tem de caber **dentro** do limite configurado — não acrescentar um
chip acima dele.
"""

import unittest
from unittest.mock import MagicMock

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402

require_gtk4()
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

try:  # descoberta como pacote (`-t .`) vs. descoberta simples
    from tests._gtk_helpers import descendants, icon_names
except ImportError:  # pragma: no cover - depende do runner
    from _gtk_helpers import descendants, icon_names

from zorin_copilot.core.config import CopilotConfig  # noqa: E402
from zorin_copilot.core.desktop.env import current_environment  # noqa: E402
from zorin_copilot.core.desktop_context import (  # noqa: E402
    AppCategory,
    ClipboardCategory,
    DesktopContext,
)
from zorin_copilot.ui.widgets.chat_stream import ChatStreamView  # noqa: E402
from zorin_copilot.ui.widgets.sidebar import (  # noqa: E402
    build_topic_identity,
    classify_topic,
)

TOPICS = [
    {
        "id": "t1",
        "title": "Como configurar o Ollama",
        "updated_at": "2026-09-21T09:10:00",
        "turn_count": 12,
    },
    {
        "id": "t2",
        "title": "Revisão do contrato de aluguel",
        "updated_at": "2026-09-20T18:00:00",
        "turn_count": 4,
    },
    {
        "id": "t3",
        "title": "Conversa de Voz sobre o quadro negro",
        "updated_at": "2026-09-19T11:30:00",
        "turn_count": 1,
    },
    {
        "id": "t4",
        "title": "Quarta conversa, fora do limite",
        "updated_at": "2026-09-18T10:00:00",
        "turn_count": 2,
    },
]


def chip_titles(group: Gtk.Box) -> list[str]:
    """Textos dos chips de um grupo rotulado, na ordem."""
    grid = group.get_last_child()
    found = []
    child = grid.get_first_child()
    while child is not None:
        found.append(child.get_child().get_last_child().get_text())
        child = child.get_next_sibling()
    return found


def make_view(topics=None, limit: int = 4):
    ctx = MagicMock()
    ctx.config = CopilotConfig()
    ctx.config.context_suggestions_limit = limit
    ctx.session.id = "nova"
    ctx.session.turns = []
    ctx.engine.memory.list_chat_topics.return_value = list(topics or [])
    return ctx, ChatStreamView(ctx)


def browser_context() -> DesktopContext:
    return DesktopContext(
        app_name="Google Chrome",
        window_title="Documentação — Docs",
        category=AppCategory.BROWSER,
        extracted_target="Documentação",
        has_active_window=True,
    )


def editor_context(*, with_git: bool = False) -> DesktopContext:
    return DesktopContext(
        app_name="Visual Studio Code",
        window_title="main.py — projeto",
        category=AppCategory.CODE_EDITOR,
        extracted_target="main.py",
        has_active_window=True,
        git_repo="projeto" if with_git else "",
        git_branch="main" if with_git else "",
        git_has_diff=with_git,
    )


class StateLineTest(unittest.TestCase):
    """A estrela sai; entra uma linha que orienta."""

    def setUp(self):
        self.ctx, self.view = make_view()

    def test_the_star_is_gone(self):
        self.assertNotIn("starred-symbolic", icon_names(self.view.welcome_box))

    def test_no_icon_at_all_in_the_header(self):
        """A área era ocupada por um ícone decorativo; agora é só texto."""
        header = self.view.welcome_box.get_first_child()
        self.assertEqual(icon_names(header), [])

    def test_state_line_names_the_os(self):
        expected = current_environment().distro.display_name
        self.assertIn(expected, self.view.welcome_state_label.get_text())

    def test_state_line_names_the_session(self):
        self.assertIn("Conversa nova", self.view.welcome_state_label.get_text())

    def test_state_line_names_the_desktop(self):
        """O ambiente de desktop decide quais automações funcionam."""
        env = current_environment()
        if not env.desktop:
            self.skipTest("sem desktop detectado neste ambiente")
        self.assertIn(env.desktop.capitalize(), self.view.welcome_state_label.get_text())

    def test_state_line_does_not_repeat_the_active_window(self):
        """O aplicativo ativo tem dono: o banner de contexto, logo abaixo.

        Dizer "Chrome" aqui e "No foco: Chrome" ali seria a mesma informação em
        dois lugares da mesma tela — o problema §0.1 do diagnóstico.
        """
        self.view.update_context(browser_context())
        text = self.view.welcome_state_label.get_text()
        self.assertNotIn("Chrome", text)
        self.assertIn("Google Chrome", self.view.context_banner_label.get_text())

    def test_title_survives(self):
        """A pergunta é a função da tela — não sai junto com a estrela.

        `get_text()` devolve o texto já sem o markup (a label é criada com
        `use_markup=True`), então a comparação é com a pergunta em texto puro.
        """
        labels = [
            node.get_text()
            for node in descendants(self.view.welcome_box)
            if isinstance(node, Gtk.Label)
        ]
        self.assertIn("Como posso ajudar hoje?", labels)

    def test_survives_a_broken_environment_probe(self):
        """A tela de boas-vindas não pode depender da sondagem para aparecer."""
        with unittest.mock.patch(
            "zorin_copilot.ui.widgets.chat_stream.current_environment",
            side_effect=OSError("sem /etc/os-release"),
        ):
            _, view = make_view()
        self.assertEqual(view.welcome_state_label.get_text(), "Conversa nova")


class SuggestionGroupsTest(unittest.TestCase):
    """Dois grupos rotulados, e a divisão vem dos dados que já existiam."""

    def setUp(self):
        self.ctx, self.view = make_view()

    def test_without_context_only_the_modes_group_shows(self):
        self.assertIsNone(self.view.screen_group)
        self.assertIsNotNone(self.view.modes_group)

    def test_group_labels_are_present(self):
        self.assertEqual(
            self.view.modes_group.get_first_child().get_text(), "Como interagir"
        )
        self.view.update_context(browser_context())
        self.assertEqual(
            self.view.screen_group.get_first_child().get_text(),
            "Sobre o que está na tela",
        )

    def test_context_creates_the_screen_group(self):
        self.view.update_context(browser_context())
        titles = chip_titles(self.view.screen_group)
        self.assertIn("Resumir esta página", titles)

    def test_screen_group_holds_only_contextual_chips(self):
        """O grupo é sobre o que está na tela — nada de modo de interação."""
        self.view.update_context(browser_context())
        self.assertNotIn("Voz ao Vivo (Gemini Live)", chip_titles(self.view.screen_group))
        self.assertNotIn("Recortar Área da Tela", chip_titles(self.view.screen_group))

    def test_modes_group_holds_the_interaction_modes(self):
        titles = chip_titles(self.view.modes_group)
        self.assertIn("Voz ao Vivo (Gemini Live)", titles)
        self.assertIn("Recortar Área da Tela", titles)

    def test_no_chip_disappears_when_there_is_no_context(self):
        """Regressão: pôr "Recortar Área" no grupo condicional a faria sumir.

        Ela não depende de haver janela em foco — recortar a tela funciona
        sempre. Como o grupo da tela só aparece com contexto, uma sugestão que
        não precisa de contexto não pode morar nele.
        """
        titles = chip_titles(self.view.modes_group)
        self.assertIn("Recortar Área da Tela", titles)
        self.assertIn("Analisar Copiado", titles)

    def test_contextual_chips_keep_their_marker(self):
        """O realce de "isto veio da sua tela" continua só no grupo da tela."""
        self.view.update_context(browser_context())
        for group, expected in ((self.view.screen_group, True), (self.view.modes_group, False)):
            child = group.get_last_child().get_first_child()
            while child is not None:
                self.assertEqual(child.has_css_class("context-chip"), expected)
                child = child.get_next_sibling()

    def test_total_chips_respect_the_configured_limit(self):
        """O limite é do usuário: a reserva remaneja, não acrescenta."""
        for context in (None, browser_context(), editor_context(), editor_context(with_git=True)):
            with self.subTest(context=context.app_name if context else "sem contexto"):
                _, view = make_view()
                if context is not None:
                    view.update_context(context)
                total = sum(
                    len(chip_titles(g))
                    for g in (view.screen_group, view.modes_group)
                    if g is not None
                )
                self.assertLessEqual(total, 4, f"mostrou {total} chips com limite 4")

    def test_modes_group_survives_a_greedy_context(self):
        """Com contexto rico o motor enche o orçamento; a reserva salva os modos."""
        self.view.update_context(editor_context(with_git=True))
        self.assertIsNotNone(self.view.modes_group)
        self.assertIn("Voz ao Vivo (Gemini Live)", chip_titles(self.view.modes_group))

    def test_a_smaller_limit_is_honoured(self):
        _, view = make_view(limit=2)
        view.update_context(editor_context(with_git=True))
        total = sum(
            len(chip_titles(g)) for g in (view.screen_group, view.modes_group) if g is not None
        )
        self.assertLessEqual(total, 2)

    def test_limit_of_one_does_not_reserve(self):
        """Com uma vaga só não há o que remanejar — e não pode quebrar."""
        _, view = make_view(limit=1)
        view.update_context(editor_context(with_git=True))
        total = sum(
            len(chip_titles(g)) for g in (view.screen_group, view.modes_group) if g is not None
        )
        self.assertLessEqual(total, 1)

    def test_context_awareness_off_shows_only_modes(self):
        self.ctx.config.context_awareness_enabled = False
        self.view.update_context(browser_context())
        self.assertIsNone(self.view.screen_group)

    def test_rebuild_after_context_change_keeps_groups_consistent(self):
        self.view.update_context(browser_context())
        self.assertIsNotNone(self.view.screen_group)
        self.view.update_context(None)
        self.assertIsNone(self.view.screen_group)
        self.assertIsNotNone(self.view.modes_group)


class ResumeSectionTest(unittest.TestCase):
    """As últimas conversas na própria tela vazia."""

    def setUp(self):
        self.ctx, self.view = make_view(TOPICS)

    def _rows(self) -> list[Gtk.ListBoxRow]:
        section = self.view.resume_slot.get_first_child()
        if section is None:
            return []
        listbox = section.get_last_child()
        rows = []
        child = listbox.get_first_child()
        while child is not None:
            rows.append(child)
            child = child.get_next_sibling()
        return rows

    def test_section_is_labelled(self):
        section = self.view.resume_slot.get_first_child()
        self.assertEqual(section.get_first_child().get_text(), "Retomar")

    def test_shows_at_most_three(self):
        self.assertEqual(len(self._rows()), 3)

    def test_asks_the_database_for_only_three(self):
        self.ctx.engine.memory.list_chat_topics.assert_called_with(limit=3)

    def test_keeps_the_order_from_the_history(self):
        titles = []
        for row in self._rows():
            info = row.get_child().get_last_child()
            titles.append(info.get_first_child().get_text())
        self.assertEqual(
            titles,
            [
                "Como configurar o Ollama",
                "Revisão do contrato de aluguel",
                "Conversa de Voz sobre o quadro negro",
            ],
        )

    def test_no_delete_button_in_the_welcome_screen(self):
        """Excluir é destrutivo e mora onde já tem confirmação: a barra lateral."""
        for row in self._rows():
            self.assertNotIn("user-trash-symbolic", icon_names(row))

    def test_reuses_the_sidebar_row_class(self):
        """Mesmo padrão de linha, sem um segundo estilo para manter."""
        for row in self._rows():
            self.assertTrue(row.has_css_class("sidebar-chat-row"))

    def test_clicking_resumes_the_conversation(self):
        row = self._rows()[0]
        listbox = self.view.resume_slot.get_first_child().get_last_child()
        listbox.emit("row-activated", row)
        self.ctx._resume_topic.assert_called_once_with("t1")

    def test_row_without_id_is_ignored(self):
        row = self._rows()[0]
        row._topic_id = None
        listbox = self.view.resume_slot.get_first_child().get_last_child()
        listbox.emit("row-activated", row)
        self.ctx._resume_topic.assert_not_called()

    def test_absent_without_history(self):
        _, view = make_view([])
        self.assertIsNone(view.resume_slot.get_first_child())

    def test_absent_when_the_history_is_unavailable(self):
        ctx, view = make_view()
        ctx.engine.memory.list_chat_topics.return_value = None
        view._refresh_resume_section()
        self.assertIsNone(view.resume_slot.get_first_child())

    def test_absent_when_the_database_raises(self):
        """Histórico quebrado é motivo para não mostrar a seção, não para cair."""
        ctx, view = make_view()
        ctx.engine.memory.list_chat_topics.side_effect = RuntimeError("banco travado")
        view._refresh_resume_section()
        self.assertIsNone(view.resume_slot.get_first_child())

    def test_refresh_picks_up_new_conversations(self):
        """A lista é reconstruída quando a tela vazia reaparece."""
        ctx, view = make_view([])
        self.assertIsNone(view.resume_slot.get_first_child())

        ctx.engine.memory.list_chat_topics.return_value = list(TOPICS)
        view.rebuild()
        self.assertEqual(len(self._rows_for(view)), 3)

    def _rows_for(self, view) -> list:
        section = view.resume_slot.get_first_child()
        listbox = section.get_last_child()
        rows = []
        child = listbox.get_first_child()
        while child is not None:
            rows.append(child)
            child = child.get_next_sibling()
        return rows

    def test_rebuild_does_not_duplicate_the_section(self):
        self.view.rebuild()
        self.view.rebuild()
        count = 0
        child = self.view.resume_slot.get_first_child()
        while child is not None:
            count += 1
            child = child.get_next_sibling()
        self.assertEqual(count, 1)

    def test_malformed_history_entries_are_skipped_by_the_scan(self):
        """Uma lista com item sem id não pode derrubar a tela."""
        _, view = make_view([{"title": "sem id nem data"}])
        row = view.resume_slot.get_first_child().get_last_child().get_first_child()
        self.assertIsNotNone(row)
        self.assertIsNone(row._topic_id)


class SharedTopicIdentityTest(unittest.TestCase):
    """A linha da conversa é uma só, usada em duas telas."""

    def setUp(self):
        self.ctx = MagicMock()
        self.ctx.session.id = "outra"

    def test_both_screens_agree_on_the_icon(self):
        """O motivo de existir um construtor compartilhado."""
        for topic in TOPICS:
            with self.subTest(topic=topic["title"]):
                identity = build_topic_identity(self.ctx, topic)
                self.assertEqual(identity.get_first_child().get_icon_name(), _expected_icon(topic))

    def test_voice_conversations_get_the_microphone(self):
        topic = {"id": "v", "title": "Conversa de Voz sobre matemática", "updated_at": "", "turn_count": 1}
        self.assertEqual(classify_topic(topic), "voice")
        identity = build_topic_identity(self.ctx, topic)
        self.assertEqual(
            identity.get_first_child().get_icon_name(), "audio-input-microphone-symbolic"
        )

    def test_active_conversation_is_marked(self):
        topic = {"id": "aberta", "title": "Aberta agora", "updated_at": "", "turn_count": 3}
        self.ctx.session.id = "aberta"
        identity = build_topic_identity(self.ctx, topic)
        self.assertEqual(identity.get_first_child().get_icon_name(), "starred-symbolic")

    def test_identity_carries_title_and_subtitle(self):
        identity = build_topic_identity(self.ctx, TOPICS[0])
        info = identity.get_last_child()
        self.assertEqual(info.get_first_child().get_text(), "Como configurar o Ollama")
        self.assertIn("12 msgs", info.get_last_child().get_text())

    def test_singular_message_count(self):
        identity = build_topic_identity(self.ctx, TOPICS[2])
        self.assertIn("1 msg", identity.get_last_child().get_last_child().get_text())

    def test_untitled_conversation_gets_a_placeholder(self):
        identity = build_topic_identity(self.ctx, {"id": "x", "title": "", "updated_at": ""})
        self.assertEqual(
            identity.get_last_child().get_first_child().get_text(), "Conversa sem título"
        )

    def test_identity_does_not_include_the_delete_affordance(self):
        """A moldura fica com quem chama: a sidebar gerencia, a boas-vindas inicia."""
        identity = build_topic_identity(self.ctx, TOPICS[0])
        self.assertNotIn("user-trash-symbolic", icon_names(identity))

    def test_welcome_rows_have_no_keyboard_delete(self):
        """A tela de partida não apaga nada, nem por atalho."""
        _, view = make_view(TOPICS)
        section = view.resume_slot.get_first_child()
        row = section.get_last_child().get_first_child()
        self.assertEqual(
            [c for c in row.observe_controllers() if isinstance(c, Gtk.EventControllerKey)],
            [],
        )


def _expected_icon(topic: dict) -> str:
    if classify_topic(topic) == "voice":
        return "audio-input-microphone-symbolic"
    return "format-justification-symbolic"


class ClipboardContextTest(unittest.TestCase):
    """O outro caminho de contexto: erro copiado."""

    def test_traceback_becomes_a_screen_suggestion(self):
        ctx, view = make_view()
        view.update_context(
            DesktopContext(
                app_name="kitty",
                category=AppCategory.TERMINAL,
                has_active_window=True,
                clipboard_category=ClipboardCategory.ERROR_TRACEBACK,
                clipboard_preview="Traceback (most recent call last):",
                clipboard_text='Traceback (most recent call last):\n  File "x.py"',
            )
        )
        self.assertIn("Diagnosticar erro copiado", chip_titles(view.screen_group))


if __name__ == "__main__":
    unittest.main()
