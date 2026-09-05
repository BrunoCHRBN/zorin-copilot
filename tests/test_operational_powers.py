"""Testes de integração para os novos poderes operacionais do Zorin Copilot.

Cobre controle de mídia (Spotify/MPRIS2), escrita de arquivos/relatórios
e organização de diretórios via texto e voz.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from zorin_copilot.ai.actions import ActionPlan, ActionType, DesktopAction
from zorin_copilot.ai.engine import IntentEngine
from zorin_copilot.ai.providers import BaseLLMProvider
from zorin_copilot.shell.executor import ActionExecutor


class OperationalPowersTest(unittest.TestCase):
    def setUp(self):
        self.engine = IntentEngine()
        self.executor = ActionExecutor()
        self.test_dir = tempfile.mkdtemp(prefix="copilot_test_op_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_engine_fast_path_media_pause(self):
        plan = self.engine.parse("pausa a musica")
        self.assertFalse(plan.is_empty)
        act = plan.actions[0]
        self.assertEqual(act.action_type, ActionType.MEDIA_CONTROL)
        self.assertEqual(act.params.get("action"), "pause")

    def test_engine_fast_path_media_play(self):
        plan = self.engine.parse("play na musica")
        self.assertFalse(plan.is_empty)
        act = plan.actions[0]
        self.assertEqual(act.action_type, ActionType.MEDIA_CONTROL)
        self.assertEqual(act.params.get("action"), "play")

    def test_engine_fast_path_media_next(self):
        plan = self.engine.parse("proxima musica")
        self.assertFalse(plan.is_empty)
        act = plan.actions[0]
        self.assertEqual(act.action_type, ActionType.MEDIA_CONTROL)
        self.assertEqual(act.params.get("action"), "next")

    def test_engine_fast_path_media_status(self):
        with patch("zorin_copilot.core.media.MediaPlayerManager.get_track_info") as mock_info:
            from zorin_copilot.core.media import TrackInfo
            mock_info.return_value = TrackInfo(
                title="Blinding Lights",
                artist="The Weeknd",
                playback_status="Playing",
                player_name="spotify",
            )
            plan = self.engine.parse("qual musica esta tocando?")
            self.assertFalse(plan.is_empty)
            act = plan.actions[0]
            self.assertEqual(act.action_type, ActionType.MEDIA_CONTROL)
            self.assertIn("Blinding Lights", plan.thought)

    def test_engine_fast_path_organize_downloads(self):
        plan = self.engine.parse("organizar pasta de downloads")
        self.assertFalse(plan.is_empty)
        act = plan.actions[0]
        self.assertEqual(act.action_type, ActionType.ORGANIZE_FILES)
        self.assertEqual(act.params.get("directory"), "~/Downloads")

    @patch("zorin_copilot.core.media.MediaPlayerManager.control")
    def test_executor_media_control(self, mock_control):
        mock_control.return_value = (True, "Música pausada no Spotify.")
        act = DesktopAction(ActionType.MEDIA_CONTROL, "pause", {"action": "pause", "player": "spotify"})
        plan = ActionPlan(thought="Pausar", actions=[act])
        reports = self.executor.execute_plan(plan)
        self.assertEqual(len(reports), 1)
        self.assertTrue(reports[0].success)
        self.assertIn("Spotify", reports[0].message)

    def test_executor_write_file(self):
        act = DesktopAction(
            ActionType.WRITE_FILE,
            "relatorio_mercado.md",
            {
                "filename": "relatorio_mercado.md",
                "content": "# Resumo de Mercado\n\nCotação e análises.",
                "directory": self.test_dir,
            },
        )
        plan = ActionPlan(thought="Salvar arquivo", actions=[act])
        reports = self.executor.execute_plan(plan)
        self.assertEqual(len(reports), 1)
        self.assertTrue(reports[0].success)
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "relatorio_mercado.md")))

    def test_executor_organize_files(self):
        f = os.path.join(self.test_dir, "documento.pdf")
        with open(f, "w") as fp:
            fp.write("pdf test")

        act = DesktopAction(
            ActionType.ORGANIZE_FILES,
            self.test_dir,
            {"directory": self.test_dir, "dry_run": False},
        )
        plan = ActionPlan(thought="Organizar", actions=[act])
        reports = self.executor.execute_plan(plan)
        self.assertEqual(len(reports), 1)
        self.assertTrue(reports[0].success)
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "Documentos", "documento.pdf")))

    def test_parse_response_payload_operational_actions(self):
        raw_json = """{
            "explanation": "Pesquisei e organizei as informações solicitadas.",
            "actions": [
                {
                    "type": "media_control",
                    "target": "pause",
                    "description": "Pausar música no Spotify",
                    "params": {"action": "pause", "player": "spotify"}
                },
                {
                    "type": "write_file",
                    "target": "relatorio_ai.md",
                    "description": "Salvar relatório",
                    "params": {"filename": "relatorio_ai.md", "content": "# Conteúdo"}
                },
                {
                    "type": "organize_files",
                    "target": "~/Downloads",
                    "description": "Organizar downloads",
                    "params": {"directory": "~/Downloads"}
                }
            ]
        }"""
        explanation, actions = BaseLLMProvider.parse_response_payload(raw_json)
        self.assertEqual(len(actions), 3)
        self.assertEqual(actions[0].action_type, ActionType.MEDIA_CONTROL)
        self.assertEqual(actions[1].action_type, ActionType.WRITE_FILE)
        self.assertEqual(actions[2].action_type, ActionType.ORGANIZE_FILES)


if __name__ == "__main__":
    unittest.main()
