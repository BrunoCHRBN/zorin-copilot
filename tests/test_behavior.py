"""Testes unitários para a humanização e comportamento inteligente do Zorin Copilot."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from zorin_copilot.ai.actions import ActionType
from zorin_copilot.ai.engine import IntentEngine
from zorin_copilot.ai.live import GeminiLiveClient
from zorin_copilot.ai.providers import SYSTEM_PROMPT, BaseLLMProvider
from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.core.media import TrackInfo


class BehaviorAndSituationalAwarenessTest(unittest.TestCase):
    """Testes para a consciência situacional e diretrizes de diálogo humano."""

    def setUp(self):
        self.config = CopilotConfig(gemini_api_key="fake-key")
        self.mock_inspector = MagicMock()
        self.engine = IntentEngine(inspector=self.mock_inspector, config=self.config)

    def test_system_prompt_persona_guidelines(self):
        """Verifica se o SYSTEM_PROMPT incorpora as diretrizes de parceiro de desktop."""
        self.assertIn("parceiro de desktop", SYSTEM_PROMPT.lower())
        self.assertIn("tom camaleônico", SYSTEM_PROMPT.lower())
        self.assertIn("micro-hooks", SYSTEM_PROMPT.lower())
        self.assertIn("memória orgânica", SYSTEM_PROMPT.lower())
        self.assertIn("open_document", SYSTEM_PROMPT)

    def test_type_map_open_document(self):
        """Garante que a ação open_document é mapeada para ActionType.OPEN_DOCUMENT."""
        json_payload = """
        {
            "explanation": "Abrindo o relatório para você!",
            "actions": [
                {
                    "type": "open_document",
                    "target": "/home/bruno/Documentos/relatorio.pdf",
                    "params": {"page_number": 2},
                    "description": "Abrir relatório na página 2"
                }
            ]
        }
        """
        explanation, actions = BaseLLMProvider.parse_response_payload(json_payload)
        self.assertIn("Abrindo o relatório", explanation)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, ActionType.OPEN_DOCUMENT)
        self.assertEqual(actions[0].target, "/home/bruno/Documentos/relatorio.pdf")

    def test_situational_context_formatting(self):
        """Verifica a montagem do bloco de contexto situacional com janela, horário e música."""
        self.mock_inspector.get_active_window_info.return_value = (
            "code",
            "main.py — zorin-copilot",
            (0, 0, 1920, 1080),
        )

        mock_track = TrackInfo(
            title="Instant Crush",
            artist="Daft Punk",
            playback_status="Playing",
            player_name="org.mpris.MediaPlayer2.spotify",
        )

        with patch("zorin_copilot.ai.engine.MediaPlayerManager.get_track_info", return_value=mock_track):
            context = self.engine._get_situational_context()

            self.assertIn("[Contexto Situacional do Desktop]", context)
            self.assertIn("code ('main.py — zorin-copilot')", context)
            self.assertIn("Horário:", context)
            self.assertIn("Instant Crush", context)
            self.assertIn("Daft Punk", context)
            self.assertIn("Spotify", context)

    def test_situational_context_graceful_fallback(self):
        """Se ocorrer falha em qualquer inspeção, retorna string limpa sem exceções."""
        self.mock_inspector.get_active_window_info.side_effect = Exception("DBus timeout")
        with patch("zorin_copilot.ai.engine.MediaPlayerManager.get_track_info", side_effect=Exception("No media")):
            context = self.engine._get_situational_context()
            # Pelo menos o horário deve ser capturado
            self.assertIn("Horário:", context)

    def test_learn_fact_friendly_response(self):
        """Verifica se o aprendizado de fatos tem resposta acolhedora e concisa."""
        plan = self.engine.parse("lembre-se que meu café favorito é espresso duplo")
        self.assertIn("Anotado!", plan.thought)
        self.assertIn("espresso duplo", plan.thought)
        self.assertNotIn("Guardei na minha base de conhecimento", plan.thought)

    def test_calendar_queries_deterministic(self):
        """Garante precisão matemática e temporal sem alucinações para perguntas de calendário."""
        # Quantos domingos faltam para o natal
        plan_domingos = self.engine.parse("quantos domingos faltam para o natal")
        self.assertIn("Natal", plan_domingos.thought)
        self.assertIn("domingos", plan_domingos.thought)
        self.assertIn("25 de dezembro", plan_domingos.thought.lower())
        self.assertNotIn("2029", plan_domingos.thought)

        # Quantos dias faltam para o natal
        plan_dias = self.engine.parse("quantos dias faltam para o natal")
        self.assertIn("dias", plan_dias.thought)
        self.assertIn("25 de dezembro", plan_dias.thought.lower())

        # Próximo feriado
        plan_feriado = self.engine.parse("qual o próximo feriado")
        self.assertIn("próximo feriado", plan_feriado.thought.lower())

        # Que dia cai o natal
        plan_quando = self.engine.parse("quando é o natal")
        self.assertIn("25 de dezembro", plan_quando.thought.lower())

        # Ano novo
        plan_ano_novo = self.engine.parse("quantos dias faltam para o ano novo")
        self.assertIn("Ano Novo", plan_ano_novo.thought)
        self.assertIn("janeiro", plan_ano_novo.thought.lower())

    def test_math_queries_deterministic(self):
        """Garante cálculo matemático exato e seguro sem invocar LLM."""
        plan_mult = self.engine.parse("quanto é 25 * 4")
        self.assertIn("100", plan_mult.thought)

        plan_pct = self.engine.parse("calcule 15% de 800")
        self.assertIn("120", plan_pct.thought)

        plan_sqrt = self.engine.parse("raiz quadrada de 144")
        self.assertIn("12", plan_sqrt.thought)

        plan_div = self.engine.parse("quanto é 120 dividido por 3")
        self.assertIn("40", plan_div.thought)

    def test_temporal_fidelity_in_prompt_and_context(self):
        """Verifica se as diretrizes de fidelidade temporal estão presentes no SYSTEM_PROMPT e no contexto."""
        self.assertIn("fidelidade temporal e de calendário", SYSTEM_PROMPT.lower())
        context = self.engine._get_situational_context()
        self.assertIn("Data e Horário:", context)


if __name__ == "__main__":
    unittest.main()
