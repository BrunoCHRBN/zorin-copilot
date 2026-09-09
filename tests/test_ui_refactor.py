"""Testes de regressão da refatoração da UI em componentes (Sprint 1).

Cobrem os bugs descobertos durante a extração de `ui/app.py` para `ui/widgets/`:
  1. `TopicSession.record_turn` não devolvia o turno, impedindo a renderização da resposta.
  2. `Esc` escondia a janela mesmo com um popover aberto.
  3. `zorin_copilot.ui` não era descoberto pelo empacotamento (falta de `__init__.py`).
"""

import os
import sys
import time
import unittest

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from unittest.mock import patch  # noqa: E402

from zorin_copilot.ai.actions import ActionPlan, ActionType, DesktopAction  # noqa: E402
from zorin_copilot.core.session import ChatTurn, TopicSession  # noqa: E402
from zorin_copilot.core.shortcuts import APP_SHORTCUTS  # noqa: E402
from zorin_copilot.ui.app import CopilotWindow  # noqa: E402
from zorin_copilot.ui.widgets.chat_stream import TypingIndicator  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_loop_until(predicate, timeout_ms=2000):
    """Bombeia o contexto principal até `predicate` ser verdadeiro ou estourar o prazo.

    Antes isto criava um `GLib.MainLoop` aninhado com dois timers — um para checar
    e outro para encerrar. Em runner de CI carregado isso deu falso negativo: o
    prazo vencia antes de o timer de debounce (80 ms) ser despachado, e o teste
    falhava mesmo estando tudo certo. Bomber o contexto direto evita o loop
    aninhado e devolve o controle assim que a condição aparece.
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    context = GLib.MainContext.default()

    while time.monotonic() < deadline:
        _drain_ready(context)
        if predicate():
            return True
        time.sleep(0.005)

    # Última chance: o prazo pode ter vencido no mesmo instante em que a
    # condição ficou pronta.
    _drain_ready(context)
    return bool(predicate())


def _drain_ready(context, max_iterations: int = 100) -> None:
    """Despacha o que já está pronto, com teto de iterações.

    O laço precisa de teto: `MainContext.pending()` responde "existe source
    registrado", não "existe algo despachável". Uma conexão D-Bus ociosa mantém
    `pending()` verdadeiro indefinidamente e `iteration(False)` não a consome —
    o `while context.pending()` original travava a suíte inteira.
    """
    for _ in range(max_iterations):
        if not context.pending():
            return
        # iteration(False) não bloqueia: só despacha o que já está pronto.
        # Devolve False quando não havia nada pronto — aí também paramos.
        if not context.iteration(False):
            return


class SessionTurnRegressionTest(unittest.TestCase):
    """`record_turn` deve devolver o turno criado para permitir a renderização."""

    def test_record_turn_returns_turn(self):
        session = TopicSession(auto_persist=True)
        turn = session.record_turn("Qual é o meu IP?", "Use `ip a`.")
        self.assertIsInstance(turn, ChatTurn)
        self.assertEqual(turn.prompt, "Qual é o meu IP?")
        self.assertEqual(turn.answer, "Use `ip a`.")

    def test_record_turn_returns_turn_when_not_persisted(self):
        """Mesmo sem auto-persistência o turno precisa ser devolvido."""
        session = TopicSession(auto_persist=False)
        turn = session.record_turn("Pergunta", "Resposta")
        self.assertIsInstance(turn, ChatTurn)
        self.assertEqual(turn.prompt, "Pergunta")


class WindowCompositionTest(unittest.TestCase):
    """A janela deve compor os componentes extraídos mantendo a API pública."""

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.refactor")

    def setUp(self):
        self.win = CopilotWindow(self.app)

    def test_components_are_mounted(self):
        for attr in ("header", "sidebar", "chat_stream", "prompt_bar", "vision"):
            self.assertTrue(hasattr(self.win, attr), f"componente ausente: {attr}")

    def test_legacy_api_preserved(self):
        """Atributos usados por código legado e testes antigos continuam acessíveis."""
        for attr in (
            "sidebar_revealer", "sidebar_search", "history_listbox", "sidebar_toggle_btn",
            "chat_stream_box", "welcome_box", "prompt_bar_box", "entry",
            "vision_btn", "clipboard_btn", "bottom_voice_btn", "submit_btn",
            "app_preview_revealer", "vision_preview_box",
            "fence_menu_btn", "fence_lbl", "window_title",
        ):
            self.assertIsNotNone(getattr(self.win, attr, None), f"atributo ausente: {attr}")

    def test_submit_renders_assistant_response(self):
        """Regressão: a resposta da IA precisa aparecer no fluxo (turno não pode ser None)."""
        plan = ActionPlan(
            thought="Abrindo a calculadora.",
            actions=[DesktopAction(ActionType.LAUNCH_APP, "calc")],
        )
        with patch.object(self.win.engine, "parse", return_value=plan):
            self.win.entry.set_text("abrir calculadora")
            self.win._on_submit(self.win.entry)

        # Praço largo pelo mesmo motivo do teste de debounce: em runner
        # compartilhado o worker da IA pode demorar a ser despachado.
        self.assertTrue(
            run_loop_until(lambda: not self.win._is_busy, timeout_ms=10_000),
            "o envio não liberou a interface dentro do prazo",
        )
        self.assertEqual(self.win.session.turn_count, 1)

        children = []
        child = self.win.chat_stream_box.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        # Balão do usuário + cartão de resposta do assistente
        self.assertGreaterEqual(len(children), 2)

    def test_escape_closes_popover_before_hiding_window(self):
        """Regressão: Esc deve fechar popovers antes de esconder a janela."""
        self.win.set_visible(True)
        popover = self.win.vision_btn.get_popover()
        popover.popup()
        self.assertTrue(popover.get_visible())

        self.win._handle_escape()

        self.assertFalse(popover.get_visible())
        self.assertTrue(self.win.get_visible())

    def test_escape_hides_window_when_nothing_open(self):
        self.win.set_visible(True)
        self.win.entry.set_text("")
        self.win.sidebar_search.set_text("")
        self.win.live_client = None

        self.win._handle_escape()
        self.assertFalse(self.win.get_visible())

    def test_sidebar_search_uses_debounce(self):
        """A busca da sidebar não deve reconstruir a lista a cada tecla."""
        self.win.engine.memory.save_chat_topic("dbg1", "Receita de bolo", [], is_pinned=True)
        self.win.engine.memory.save_chat_topic("dbg2", "Configurar VPN", [], is_pinned=True)

        populated = []
        original = self.win.sidebar.populate

        def spy(*args, **kwargs):
            populated.append(1)
            return original(*args, **kwargs)

        self.win.sidebar.populate = spy
        entry = self.win.sidebar_search
        entry.set_text("VPN")
        # Imediatamente após digitar nenhuma reconstrução deve ter ocorrido
        self.assertEqual(len(populated), 0)

        # Espera o debounce (80 ms) disparar. O prazo é bem maior que o
        # necessário de propósito: runner de CI compartilhado atrasa timers.
        self.assertTrue(
            run_loop_until(lambda: len(populated) > 0, timeout_ms=10_000),
            "a busca com debounce não reconstruiu a lista dentro do prazo",
        )
        self.assertGreaterEqual(len(populated), 1)

        self.win.sidebar.populate = original
        self.win.engine.memory.delete_chat_topic("dbg1")
        self.win.engine.memory.delete_chat_topic("dbg2")


class AppShortcutsTest(unittest.TestCase):
    """Os atalhos internos devem vir do registro declarativo, não de keyvals soltos."""

    def test_registry_is_declared(self):
        # Subconjunto, não igualdade: adicionar atalho novo não deve quebrar isto.
        core = {"app.quit", "app.toggle-live-voice", "app.toggle-sidebar", "app.new-topic", "app.toggle-pin"}
        names = {s.name for s in APP_SHORTCUTS}
        self.assertTrue(core.issubset(names), f"faltando: {core - names}")

        # Nomes únicos: dois atalhos com o mesmo nome se sobrescreveriam.
        self.assertEqual(len(names), len(APP_SHORTCUTS))

        for shortcut in APP_SHORTCUTS:
            self.assertTrue(shortcut.accelerator.startswith("<Control>"))
            self.assertTrue(shortcut.description)

    def test_window_installs_shortcut_controller(self):
        app = Adw.Application(application_id="org.zorin.copilot.test.shortcuts")
        win = CopilotWindow(app)
        controllers = [
            c for c in win.observe_controllers()
            if isinstance(c, Gtk.ShortcutController)
        ]
        self.assertTrue(controllers, "nenhum Gtk.ShortcutController instalado")

        controller = win.app_shortcut_controller
        self.assertEqual(controller.get_n_items(), len(APP_SHORTCUTS))

        installed = set()
        for i in range(controller.get_n_items()):
            trigger = controller.get_item(i).get_trigger()
            if trigger is not None:
                installed.add(trigger.to_string())

        for shortcut in APP_SHORTCUTS:
            self.assertIn(shortcut.accelerator, installed)


class PackagingTest(unittest.TestCase):
    """O pacote `zorin_copilot.ui` precisa ser descoberto pelo setuptools."""

    def test_ui_package_is_discoverable(self):
        try:
            from setuptools import find_packages
        except ImportError:  # pragma: no cover - setuptools sempre presente
            self.skipTest("setuptools indisponível")

        packages = find_packages(where=os.path.join(ROOT, "src"))
        self.assertIn("zorin_copilot.ui", packages)
        self.assertIn("zorin_copilot.ui.widgets", packages)

    def test_init_files_exist(self):
        for rel in (
            "src/zorin_copilot/ui/__init__.py",
            "src/zorin_copilot/ui/widgets/__init__.py",
        ):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, rel)), f"ausente: {rel}")


class StatusBarTest(unittest.TestCase):
    """Barra de status inferior (item #9) e contador de tokens no badge (item #2)."""

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.statusbar")

    def setUp(self):
        self.win = CopilotWindow(self.app)

    def test_status_bar_mounted(self):
        """A barra de status deve estar montada como bottom bar da ToolbarView."""
        from zorin_copilot.ui.widgets.status_bar import StatusBarWidget

        self.assertIsInstance(self.win.status_bar, StatusBarWidget)
        # O widget da barra foi efetivamente adicionado à ToolbarView.
        self.assertIsNotNone(self.win.status_bar.container.get_parent())

    def test_engine_tracker_wired_to_provider(self):
        """O engine cria o tracker e o liga ao provedor de LLM (item #11)."""
        from zorin_copilot.core.usage import TokenUsageTracker

        self.assertIsInstance(self.win.engine.usage_tracker, TokenUsageTracker)
        self.assertIs(
            self.win.engine.llm_provider.usage_tracker,
            self.win.engine.usage_tracker,
        )

    def test_token_counter_updates_after_usage(self):
        """Após uso de tokens, o badge e a barra de status refletem o consumo."""
        from zorin_copilot.core.usage import TokenUsage

        self.win.engine.usage_tracker.record(
            TokenUsage(prompt_tokens=120, completion_tokens=30),
            provider="gemini",
            model="gemini-1.5-flash",
        )

        self.win.status_bar.refresh_tokens()
        self.assertIn("150 tokens", self.win.status_bar.tokens_lbl.get_text())

        self.win.header.refresh_token_usage()
        self.assertIn("tokens", self.win.header.status_badge.get_text())

    def test_status_bar_refreshes_model_and_rag(self):
        """A barra deve exibir modelo ativo e contagem do indexador RAG sem quebrar."""
        self.win.status_bar.refresh_model()
        self.win.status_bar.refresh_rag()
        self.assertIsInstance(self.win.status_bar.model_lbl.get_text(), str)
        self.assertIsInstance(self.win.status_bar.rag_lbl.get_text(), str)


class TimerLeakRegressionTest(unittest.TestCase):
    """Regressão: fontes periódicas não podem vazar nem passar fome no idle.

    O `GMainContext` só despacha uma fonte quando não existe nenhuma de
    prioridade mais alta pronta. `GLib.idle_add` roda em
    `G_PRIORITY_DEFAULT_IDLE` (200) — e é por ele que o app entrega a resposta
    da IA à interface. Basta sobrar uma fonte periódica em
    `G_PRIORITY_DEFAULT` (0), ou um frame clock do GDK (100), para que nenhum
    idle rode nunca mais.

    Foi exatamente isso que aconteceu: os ticks da barra de status e do
    indicador de "digitando" vazavam por janela, e depois de algumas centenas
    de janelas acumuladas a suíte travava a janela em "ocupada" para sempre —
    um teste que passava isolado e falhava só no fim da suíte.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.leak")

    def test_status_bar_tick_segue_a_visibilidade_da_janela(self):
        """O tick de telemetria só existe enquanto a janela está visível.

        Regressão: era criado no `__init__` e nunca removido de fato — o
        `destroy` do GTK4 sequer emite o sinal para janela nunca apresentada.
        """
        win = CopilotWindow(self.app)
        self.assertEqual(win.status_bar._timer_id, 0, "janela oculta não deve ticar")
        win.status_bar._on_map()
        self.assertNotEqual(win.status_bar._timer_id, 0)
        win.status_bar._on_unmap()
        self.assertEqual(win.status_bar._timer_id, 0)
        win.destroy()

    def test_indicador_de_digitacao_nao_tem_timer_enquanto_invisivel(self):
        """Sem `map` não há timer — era aí que as fontes periódicas vazavam."""
        self.assertEqual(TypingIndicator()._timer, 0)

    def test_idle_continua_despachando_com_muitas_janelas_vivas(self):
        """Trinta janelas abertas não podem impedir o despacho de um idle."""
        janelas = [CopilotWindow(self.app) for _ in range(30)]
        try:
            marcador = []

            def marca():
                marcador.append(True)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(marca)
            self.assertTrue(
                run_loop_until(lambda: bool(marcador), timeout_ms=3000),
                "GLib.idle_add deixou de ser despachado: o GMainContext está "
                "passando fome por causa de fontes de prioridade mais alta",
            )
        finally:
            for janela in janelas:
                janela.destroy()


if __name__ == "__main__":
    unittest.main()
