"""Testes unitários para o layout estilo Gemini (barra lateral, fluxo de diálogo e barra inferior)."""

import os
import sys
import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.ai.actions import ActionPlan, ActionType, DesktopAction
from zorin_copilot.core.session import ChatTurn
from zorin_copilot.ui.app import CopilotWindow


class GeminiUILayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.gemini_ui")

    def setUp(self):
        self.win = CopilotWindow(self.app)

    def test_window_initial_state_and_size(self):
        """Verifica se a janela inicializa com tamanho amplo e título correto."""
        self.assertEqual(self.win.get_title(), "Zorin Copilot")
        width, height = self.win.get_default_size()
        self.assertGreaterEqual(width, 800)
        self.assertGreaterEqual(height, 550)

    def test_sidebar_elements_and_toggle(self):
        """Verifica a presença da barra lateral estilo Gemini e seu recolhimento/expansão."""
        self.assertIsNotNone(self.win.sidebar_revealer)
        self.assertTrue(self.win.sidebar_revealer.get_reveal_child())
        self.assertIsNotNone(self.win.sidebar_search)
        self.assertIsNotNone(self.win.history_listbox)
        self.assertIsNotNone(self.win.sidebar_toggle_btn)

        # Alterna para recolher
        self.win.toggle_sidebar()
        self.assertFalse(self.win.sidebar_revealer.get_reveal_child())

        # Alterna para reabrir
        self.win.toggle_sidebar()
        self.assertTrue(self.win.sidebar_revealer.get_reveal_child())

    def test_bottom_prompt_bar_elements(self):
        """Verifica se a barra de entrada está posicionada na parte inferior com todos os botões."""
        self.assertIsNotNone(self.win.prompt_bar_box)
        self.assertIsNotNone(self.win.entry)
        self.assertIsNotNone(self.win.vision_btn)
        self.assertIsNotNone(self.win.clipboard_btn)
        self.assertIsNotNone(self.win.bottom_voice_btn)
        self.assertIsNotNone(self.win.submit_btn)
        self.assertIsNotNone(self.win.app_preview_revealer)
        self.assertIsNotNone(self.win.vision_preview_box)

    def test_multi_turn_stream_rendering(self):
        """Verifica se os turnos são adicionados em sequência no chat_stream_box preservando o diálogo."""
        self.win._rebuild_chat_stream()
        self.assertTrue(self.win.welcome_box.get_visible())

        # Adiciona 2 turnos
        self.win.session.record_turn("Como verificar o IP?", "Use o comando `ip a` ou `hostname -I`.")
        self.win.session.record_turn("E para verificar portas abertas?", "Use `ss -tulpn` no terminal.")
        self.assertEqual(self.win.session.turn_count, 2)

        self.win._rebuild_chat_stream()
        self.assertFalse(self.win.welcome_box.get_visible())

        first_child = self.win.chat_stream_box.get_first_child()
        self.assertIsNotNone(first_child)

    def test_create_turn_widget_with_actions(self):
        """Garante que a resposta do assistente renderiza ações propostas executáveis."""
        turn = ChatTurn(prompt="abrir nautilus", answer="Abrindo o gerenciador de arquivos...")
        plan = ActionPlan(
            thought="Abrindo o gerenciador de arquivos...",
            actions=[DesktopAction(ActionType.LAUNCH_APP, "nautilus")],
        )
        widget = self.win._create_turn_widget(turn, plan=plan)
        self.assertIsNotNone(widget)

    def test_resume_historical_topic_into_stream(self):
        """Verifica se carregar uma conversa salva restaura o diálogo completo no fluxo."""
        temp_id = "test_gemini_topic_1"
        turns = [
            {"prompt": "P1", "answer": "R1", "timestamp": 1.0},
            {"prompt": "P2", "answer": "R2", "timestamp": 2.0},
            {"prompt": "P3", "answer": "R3", "timestamp": 3.0},
        ]
        self.win.engine.memory.save_chat_topic(temp_id, "Tópico de Teste Multiturn", turns, is_pinned=True)

        self.win._resume_topic(temp_id)
        self.assertEqual(self.win.session.id, temp_id)
        self.assertEqual(self.win.session.turn_count, 3)
        self.assertFalse(self.win.welcome_box.get_visible())

        self.win.engine.memory.delete_chat_topic(temp_id)

    def test_new_topic_resets_stream(self):
        """Verifica se Nova Conversa (Ctrl+N) reseta o fluxo e restaura a tela de boas-vindas."""
        self.win.session.record_turn("Pergunta teste", "Resposta teste")
        self.win._rebuild_chat_stream()
        self.assertFalse(self.win.welcome_box.get_visible())

        self.win._on_new_topic()
        self.assertEqual(self.win.session.turn_count, 0)
        self.assertEqual(self.win.entry.get_text(), "")
        self.assertTrue(self.win.welcome_box.get_visible())

    def test_sidebar_search_filter(self):
        """Verifica se o filtro de busca da barra lateral funciona corretamente."""
        self.win.engine.memory.save_chat_topic("s1", "Desenvolvimento em Python", [], is_pinned=True)
        self.win.engine.memory.save_chat_topic("s2", "Instalação do Docker", [], is_pinned=True)

        self.win._populate_sidebar_history(filter_query="Python")
        self.assertIsNotNone(self.win.history_listbox.get_first_child())

    def test_fence_header_selector(self):
        """Verifica se o botão de cerca espacial no HeaderBar está presente e permite alternar telas."""
        self.assertIsNotNone(self.win.fence_menu_btn)
        self.assertIsNotNone(self.win.fence_lbl)
        self.assertIn("AOC", self.win.fence_lbl.get_text())

        # Alterna para monitor secundário
        popover = self.win.fence_menu_btn.get_popover()
        self.win._on_select_fence_monitor(1, popover)
        self.assertIn("VIE", self.win.fence_lbl.get_text())

        # Alterna para todas as telas
        self.win._on_select_all_monitors(popover)
        self.assertEqual(self.win.fence_lbl.get_text(), "Todas as Telas")

        # Testa Kill Switch
        self.win._on_toggle_kill_switch(popover)
        self.assertEqual(self.win.fence_lbl.get_text(), "🛑 BLOQUEADO")
        self.assertTrue(self.win.fence.is_emergency_stopped)

        # Destrava Kill Switch
        self.win._on_toggle_kill_switch(popover)
        self.assertFalse(self.win.fence.is_emergency_stopped)


if __name__ == "__main__":
    unittest.main()
