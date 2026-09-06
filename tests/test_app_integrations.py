"""Testes unitários para o módulo de integrações profundas com aplicações (Spotify, Gmail, Google Drive, etc.)."""

import unittest
from unittest.mock import MagicMock, patch

from zorin_copilot.ai.actions import ActionType, DesktopAction
from zorin_copilot.ai.engine import IntentEngine
from zorin_copilot.core.app_integrations import (
    AppIntentRouter,
    GitHubIntegration,
    GmailIntegration,
    GoogleCalendarIntegration,
    GoogleDriveIntegration,
    GoogleMapsIntegration,
    SpotifyIntegration,
    YouTubeIntegration,
)
from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.core.media import MediaPlayerManager
from zorin_copilot.shell.executor import ActionExecutor


class TestAppIntegrations(unittest.TestCase):
    """Testes para os geradores de ações especializadas de aplicativos."""

    def test_spotify_play_search(self):
        act = SpotifyIntegration.play_search("Queen Bohemian Rhapsody")
        self.assertEqual(act.action_type, ActionType.MEDIA_CONTROL)
        self.assertEqual(act.params.get("action"), "search")
        self.assertEqual(act.params.get("query"), "Queen Bohemian Rhapsody")
        self.assertEqual(act.params.get("player"), "spotify")
        self.assertIn("spotify:search:", act.params.get("uri", ""))

    def test_gmail_search(self):
        act = GmailIntegration.search("boletos da claro")
        self.assertEqual(act.action_type, ActionType.OPEN_URL)
        self.assertIn("mail.google.com", act.target)
        self.assertIn("#search/boletos%20da%20claro", act.target)

    def test_gmail_compose(self):
        act = GmailIntegration.compose(
            to="suporte@zorin.com",
            subject="Dúvida de Licença",
            body="Olá equipe,",
        )
        self.assertEqual(act.action_type, ActionType.OPEN_URL)
        self.assertIn("to=suporte%40zorin.com", act.target)
        self.assertIn("su=D%C3%BAvida+de+Licen%C3%A7a", act.target)
        self.assertIn("view=cm", act.target)

    def test_gmail_views(self):
        act_unread = GmailIntegration.open_view("unread")
        self.assertIn("is%3Aunread", act_unread.target)

        act_inbox = GmailIntegration.open_view("inbox")
        self.assertIn("#inbox", act_inbox.target)

    def test_google_drive_search(self):
        act = GoogleDriveIntegration.search("relatorio financeiro 2026")
        self.assertEqual(act.action_type, ActionType.OPEN_URL)
        self.assertIn("drive.google.com/drive/search?q=", act.target)
        self.assertIn("relatorio%20financeiro%202026", act.target)

    def test_google_drive_create(self):
        act_doc = GoogleDriveIntegration.create_document("doc")
        self.assertEqual(act_doc.target, "https://docs.google.com/document/create")

        act_sheet = GoogleDriveIntegration.create_document("sheet")
        self.assertEqual(act_sheet.target, "https://sheets.google.com/create")

        act_slide = GoogleDriveIntegration.create_document("slide")
        self.assertEqual(act_slide.target, "https://slides.google.com/create")

    def test_google_drive_views(self):
        act_rec = GoogleDriveIntegration.open_view("recent")
        self.assertIn("/drive/recent", act_rec.target)

        act_shared = GoogleDriveIntegration.open_view("shared")
        self.assertIn("/drive/shared-with-me", act_shared.target)

    def test_youtube_search(self):
        act = YouTubeIntegration.search("lofi hip hop radio")
        self.assertIn("youtube.com/results?search_query=", act.target)
        self.assertIn("lofi+hip+hop+radio", act.target)

    def test_google_calendar(self):
        act_view = GoogleCalendarIntegration.view()
        self.assertIn("calendar.google.com/calendar/u/0/r", act_view.target)

        act_ev = GoogleCalendarIntegration.create_event(title="Alinhamento Semanal")
        self.assertIn("eventedit?text=Alinhamento+Semanal", act_ev.target)

    def test_google_maps(self):
        act_search = GoogleMapsIntegration.search("Restaurante Italiano")
        self.assertIn("google.com/maps/search/Restaurante+Italiano", act_search.target)

        act_route = GoogleMapsIntegration.directions("Avenida Paulista, SP")
        self.assertIn("google.com/maps/dir/?api=1&destination=Avenida+Paulista%2C+SP", act_route.target)


class TestAppIntentRouter(unittest.TestCase):
    """Testes para o roteador de intenções rápidas em aplicações."""

    def setUp(self):
        self.engine = IntentEngine(config=CopilotConfig())

    def test_router_spotify_play(self):
        plan = self.engine.parse("toque bohemian rhapsody no spotify")
        self.assertFalse(plan.is_empty)
        act = plan.actions[0]
        self.assertEqual(act.action_type, ActionType.MEDIA_CONTROL)
        self.assertEqual(act.params.get("action"), "search")
        self.assertEqual(act.params.get("query"), "bohemian rhapsody")
        self.assertIn("bohemian rhapsody", plan.thought.lower())

    def test_router_gmail_search(self):
        plan = self.engine.parse("pesquise no gmail por boletos")
        self.assertFalse(plan.is_empty)
        act = plan.actions[0]
        self.assertEqual(act.action_type, ActionType.OPEN_URL)
        self.assertIn("mail.google.com", act.target)
        self.assertIn("boletos", act.target)

    def test_router_gmail_compose(self):
        plan = self.engine.parse("escreva um email no gmail para financeiro@empresa.com com assunto Fatura")
        self.assertFalse(plan.is_empty)
        act = plan.actions[0]
        self.assertEqual(act.action_type, ActionType.OPEN_URL)
        self.assertIn("financeiro%40empresa.com", act.target)
        self.assertIn("Fatura", act.target)

    def test_router_drive_search(self):
        plan = self.engine.parse("procure no drive por projeto final")
        self.assertFalse(plan.is_empty)
        act = plan.actions[0]
        self.assertEqual(act.action_type, ActionType.OPEN_URL)
        self.assertIn("drive.google.com", act.target)
        self.assertIn("projeto%20final", act.target)

    def test_router_drive_create_sheet(self):
        plan = self.engine.parse("crie uma nova planilha no google sheets")
        self.assertFalse(plan.is_empty)
        act = plan.actions[0]
        self.assertEqual(act.action_type, ActionType.OPEN_URL)
        self.assertEqual(act.target, "https://sheets.google.com/create")

    def test_router_youtube_search(self):
        plan = self.engine.parse("procure no youtube por trailer gta 6")
        self.assertFalse(plan.is_empty)
        act = plan.actions[0]
        self.assertEqual(act.action_type, ActionType.OPEN_URL)
        self.assertIn("youtube.com", act.target)
        self.assertIn("trailer+gta+6", act.target)


class TestExecutorAppInteractions(unittest.TestCase):
    """Testes para a execução de ações de controle de app e texto."""

    def setUp(self):
        self.executor = ActionExecutor()

    @patch("zorin_copilot.core.media.MediaPlayerManager.play_search")
    def test_executor_media_search(self, mock_search):
        mock_search.return_value = (True, "Buscando Queen no Spotify.")
        act = DesktopAction(
            action_type=ActionType.MEDIA_CONTROL,
            target="search",
            params={"action": "search", "query": "Queen", "player": "spotify"},
        )
        rep = self.executor.execute(act)
        self.assertTrue(rep.success)
        mock_search.assert_called_with("Queen", player_name="spotify")

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_executor_type_text_wtype(self, mock_run, mock_which):
        mock_which.side_effect = lambda b: "/usr/bin/wtype" if b == "wtype" else None
        act = DesktopAction(
            action_type=ActionType.TYPE_TEXT,
            target="campo de pesquisa",
            params={"text": "teste de digitação"},
        )
        rep = self.executor.execute(act)
        self.assertTrue(rep.success)
        mock_run.assert_called_once()
        self.assertIn("wtype", rep.message)


if __name__ == "__main__":
    unittest.main()
