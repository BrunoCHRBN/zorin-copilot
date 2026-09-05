"""Testes para o gerenciador de tópicos e sessões (TopicSession)."""

import unittest
from zorin_copilot.core.session import TopicSession


class TopicSessionTest(unittest.TestCase):
    def setUp(self):
        self.session = TopicSession(max_history_turns=4)

    def test_default_unpinned_state(self):
        """Por padrão a sessão é limpa e não fixada."""
        self.assertFalse(self.session.is_pinned)
        self.assertEqual(self.session.turn_count, 0)
        self.assertEqual(self.session.get_history_for_llm(), [])

    def test_pin_records_last_unpinned_turn(self):
        """Ao fixar logo após uma resposta isolada, ela passa a compor o contexto."""
        self.session.record_turn("Quem criou o Linux?", "Linus Torvalds criou o Linux em 1991.")
        self.assertFalse(self.session.is_pinned)
        self.assertEqual(self.session.turn_count, 0)

        # Fixa o tópico
        self.session.pin()
        self.assertTrue(self.session.is_pinned)
        self.assertEqual(self.session.turn_count, 1)

        history = self.session.get_history_for_llm()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "Quem criou o Linux?")
        self.assertEqual(history[1]["role"], "assistant")

    def test_followup_turns_when_pinned(self):
        """Quando fixado, novas mensagens acumulam contexto ordenado."""
        self.session.pin()
        self.session.record_turn("Qual a versão do Zorin?", "Zorin OS 18 Core.")
        self.session.record_turn("Como atualizo?", "Execute sudo apt update && sudo apt upgrade.")

        self.assertEqual(self.session.turn_count, 2)
        history = self.session.get_history_for_llm()
        self.assertEqual(len(history), 4)
        self.assertEqual(history[2]["content"], "Como atualizo?")

    def test_unpin_clears_history(self):
        """Desafixar limpa totalmente o histórico para novas chamadas isoladas."""
        self.session.pin()
        self.session.record_turn("Pergunta 1", "Resposta 1")
        self.assertEqual(self.session.turn_count, 1)

        self.session.unpin()
        self.assertFalse(self.session.is_pinned)
        self.assertEqual(self.session.turn_count, 0)
        self.assertEqual(self.session.get_history_for_llm(), [])

    def test_toggle_pin(self):
        """Alterna estado de fixação."""
        self.assertTrue(self.session.toggle_pin())
        self.assertTrue(self.session.is_pinned)
        self.assertFalse(self.session.toggle_pin())
        self.assertFalse(self.session.is_pinned)

    def test_max_history_turns_limit(self):
        """Garante que o histórico não cresce infinitamente excedendo max_history_turns."""
        self.session.pin()
        for i in range(10):
            self.session.record_turn(f"P{i}", f"R{i}")

        self.assertEqual(self.session.turn_count, 4)
        history = self.session.get_history_for_llm()
        self.assertEqual(history[0]["content"], "P6")
        self.assertEqual(history[-2]["content"], "P9")

    def test_auto_persist_mode(self):
        """No modo auto_persist (estilo Gemini), cada pergunta já é mantida e preserva contexto."""
        auto_session = TopicSession(max_history_turns=5, auto_persist=True)
        self.assertTrue(auto_session.is_pinned)
        self.assertTrue(auto_session.auto_persist)

        auto_session.record_turn("Pesquise preços de monitores", "Encontrei 3 modelos recomendados.")
        self.assertEqual(auto_session.turn_count, 1)
        self.assertEqual(auto_session.title, "Preços de monitores")

        # Segunda pergunta mantém contexto sem precisar fixar manualmente
        auto_session.record_turn("Qual deles tem menor latência?", "O monitor LG tem 1ms de resposta.")
        self.assertEqual(auto_session.turn_count, 2)
        llm_history = auto_session.get_history_for_llm()
        self.assertEqual(len(llm_history), 4)

    def test_semantic_title_derivation(self):
        """Verifica a limpeza de prefixos conversacionais para títulos limpos."""
        s = TopicSession(auto_persist=True)
        self.assertEqual(s._derive_title("como posso organizar minha pasta de downloads"), "Minha pasta de downloads")
        self.assertEqual(s._derive_title("por favor abra o terminal do zorin"), "Terminal do zorin")
        self.assertEqual(s._derive_title("pesquise sobre inteligência artificial"), "Inteligência artificial")


if __name__ == "__main__":
    unittest.main()
