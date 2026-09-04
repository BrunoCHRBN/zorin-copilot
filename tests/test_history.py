"""Testes unitários para o Histórico de Tópicos Acessíveis e Separação de Sessões."""

import tempfile
import unittest
from pathlib import Path

from zorin_copilot.core.memory import MemoryManager
from zorin_copilot.core.session import ChatTurn, TopicSession


class TopicHistoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_history.db"
        self.mem = MemoryManager(db_path=self.db_path)
        self.session = TopicSession(max_history_turns=6)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_isolated_chat_not_saved_by_default(self):
        """Um 'chat de agora' (não fixado) não é gravado no histórico de tópicos."""
        self.session.record_turn("Qual a versão do kernel?", "Linux 6.8.")
        self.assertFalse(self.session.is_pinned)
        self.assertEqual(len(self.mem.list_chat_topics()), 0)

    def test_pin_and_save_topic(self):
        """Ao fixar, o tópico é salvo e pode ser listado no histórico."""
        self.session.pin()
        self.session.record_turn("Como criar container Docker?", "Use docker run -d ...")
        self.assertEqual(self.session.title, "Como criar container Docker?")

        self.mem.save_chat_topic(
            topic_id=self.session.id,
            title=self.session.title,
            turns=[t.to_dict() for t in self.session.turns],
            is_pinned=True,
        )

        topics = self.mem.list_chat_topics()
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["id"], self.session.id)
        self.assertEqual(topics[0]["title"], "Como criar container Docker?")
        self.assertEqual(topics[0]["turn_count"], 1)

    def test_resume_historical_topic(self):
        """Retomar um tópico salvo restaura o contexto para o LLM continuar o raciocínio."""
        # Cria e salva tópico no banco
        self.mem.save_chat_topic(
            topic_id="hist_123",
            title="Desenvolvimento Web no Linux",
            turns=[
                {"prompt": "Instalar Node.js", "answer": "Use nvm install --lts", "timestamp": 100.0},
                {"prompt": "E o Yarn?", "answer": "npm install -g yarn", "timestamp": 101.0},
            ],
            is_pinned=True,
        )

        # Recupera do banco
        topic_data = self.mem.get_chat_topic("hist_123")
        self.assertIsNotNone(topic_data)

        # Carrega em nova sessão
        resumed_session = TopicSession.from_dict(topic_data)
        self.assertEqual(resumed_session.id, "hist_123")
        self.assertEqual(resumed_session.title, "Desenvolvimento Web no Linux")
        self.assertTrue(resumed_session.is_pinned)
        self.assertEqual(resumed_session.turn_count, 2)

        # Verifica histórico formatado para LLM
        history = resumed_session.get_history_for_llm()
        self.assertEqual(len(history), 4)
        self.assertEqual(history[0]["content"], "Instalar Node.js")
        self.assertEqual(history[1]["content"], "Use nvm install --lts")
        self.assertEqual(history[2]["content"], "E o Yarn?")
        self.assertEqual(history[3]["content"], "npm install -g yarn")

        # Continua o raciocínio com nova pergunta
        resumed_session.record_turn("Qual comando cria um projeto Next.js?", "npx create-next-app@latest")
        self.assertEqual(resumed_session.turn_count, 3)

    def test_delete_and_clear_history(self):
        """Exclusão de tópico individual e limpeza completa."""
        self.mem.save_chat_topic("t1", "Tópico 1", [], True)
        self.mem.save_chat_topic("t2", "Tópico 2", [], True)
        self.assertEqual(len(self.mem.list_chat_topics()), 2)

        # Exclui um
        self.assertTrue(self.mem.delete_chat_topic("t1"))
        topics = self.mem.list_chat_topics()
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["id"], "t2")

        # Limpa tudo
        self.mem.clear_all_chat_topics()
        self.assertEqual(len(self.mem.list_chat_topics()), 0)

    def test_reset_new_starts_clean_chat(self):
        """reset_new() cria nova sessão vazia e desvinculada para o chat de agora."""
        self.session.pin()
        self.session.record_turn("P1", "R1")
        old_id = self.session.id

        self.session.reset_new()
        self.assertNotEqual(self.session.id, old_id)
        self.assertFalse(self.session.is_pinned)
        self.assertEqual(self.session.title, "")
        self.assertEqual(self.session.turn_count, 0)
        self.assertEqual(self.session.get_history_for_llm(), [])


if __name__ == "__main__":
    unittest.main()
