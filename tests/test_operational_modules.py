"""Testes unitários para os módulos de operação: EmailManager, CalendarManager e BrowserManager."""

import os
import tempfile
import unittest

from zorin_copilot.core.browser import BrowserManager
from zorin_copilot.core.calendar import CalendarManager
from zorin_copilot.core.email import EmailManager
from zorin_copilot.core.memory import MemoryManager


class OperationalModulesTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_memory.db")
        self.memory = MemoryManager(db_path=self.db_path)
        self.email_mgr = EmailManager(memory=self.memory)
        self.cal_mgr = CalendarManager(memory=self.memory)

    def tearDown(self):
        self.temp_dir.cleanup()

    # --- EmailManager Tests ---
    def test_email_resolve_direct_email(self):
        name, email = self.email_mgr.resolve_recipient("contato@empresa.com")
        self.assertEqual(email, "contato@empresa.com")
        self.assertEqual(name, "contato")

    def test_email_resolve_from_saved_contact(self):
        self.memory.save_contact("Carlos Silva", "carlos@contabilidade.com", aliases=["contador"])
        # Resolve por apelido
        name, email = self.email_mgr.resolve_recipient("contador")
        self.assertEqual(email, "carlos@contabilidade.com")
        self.assertEqual(name, "Carlos Silva")

        # Resolve por nome parcial
        name2, email2 = self.email_mgr.resolve_recipient("Carlos")
        self.assertEqual(email2, "carlos@contabilidade.com")

    def test_email_compose_unknown_contact_returns_helpful_error(self):
        ok, msg, _ = self.email_mgr.compose("Pessoa Inexistente", "Assunto", "Corpo")
        self.assertFalse(ok)
        self.assertIn("não encontrado", msg)

    def test_email_compose_valid_contact(self):
        self.memory.save_contact("Mariana Costa", "mariana@rh.com", aliases=["rh"])
        ok, msg, data = self.email_mgr.compose("rh", "Assunto Teste", "Olá Mariana", client="gmail")
        self.assertTrue(ok)
        self.assertEqual(data["email"], "mariana@rh.com")
        self.assertEqual(data["client"], "gmail")

    # --- CalendarManager Tests ---
    def test_calendar_parse_datetime(self):
        dt_today = self.cal_mgr.parse_datetime("hoje às 16:30")
        self.assertEqual(dt_today.hour, 16)
        self.assertEqual(dt_today.minute, 30)

        dt_tomorrow = self.cal_mgr.parse_datetime("amanhã às 10h")
        self.assertEqual(dt_tomorrow.hour, 10)
        self.assertEqual(dt_tomorrow.minute, 0)

    def test_calendar_create_event_generates_ics_and_stores_db(self):
        ok, msg, data = self.cal_mgr.create_event(
            title="Alinhamento de Projeto",
            start_time_str="amanhã às 14:00",
            duration_minutes=45,
            description="Reunião de revisão do Zorin Copilot",
            location="Google Meet",
            open_in_calendar=False,
        )
        self.assertTrue(ok)
        self.assertIn("Alinhamento de Projeto", msg)
        self.assertTrue(os.path.exists(data["ics_path"]))

        # Verifica persistência no SQLite
        events = self.cal_mgr.list_events("amanhã")
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Alinhamento de Projeto")

        # Remove evento
        self.assertTrue(self.cal_mgr.delete_event(data["id"]))

    # --- BrowserManager Tests ---
    def test_browser_search_url_formatting(self):
        ok, msg, url = BrowserManager.search("Zorin OS 18 novidades", engine="google")
        self.assertIn("google.com/search?q=", url)
        self.assertIn("Zorin%20OS%2018%20novidades", url)

        ok_yt, _, url_yt = BrowserManager.search("trailer", engine="youtube")
        self.assertIn("youtube.com/results?search_query=trailer", url_yt)

        ok_gh, _, url_gh = BrowserManager.search("zorin-copilot", engine="github")
        self.assertIn("github.com/search?q=zorin-copilot", url_gh)


if __name__ == "__main__":
    unittest.main()
