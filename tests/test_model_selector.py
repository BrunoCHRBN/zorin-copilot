"""Testes para o ModelSelectorDialog e integração do Agente Dolphin 3.1."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from zorin_copilot.ui.gi_versions import require_gtk4
require_gtk4()
from gi.repository import Adw, GLib, Gtk

Adw.init()

from zorin_copilot.ai.agent_router import (
    _DOLPHIN_AGENT_INSTRUCTION,
    _SYSTEM_INSTRUCTION,
    LLMPlanner,
    build_planner_prompt,
)
from zorin_copilot.ai.providers import DOLPHIN_AGENT_ADDON, OllamaProvider
from zorin_copilot.cli import build_parser, cmd_config
from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.ui.widgets.model_selector import (
    DOLPHIN_PRESET_MODEL,
    ModelSelectorDialog,
    _format_size,
)


def walk(widget):
    """Gera recursivamente todos os filhos na árvore de widgets."""
    yield widget
    child = widget.get_first_child()
    while child:
        yield from walk(child)
        child = child.get_next_sibling()


class ModelSelectorTest(unittest.TestCase):
    def setUp(self):
        self.mock_ctx = MagicMock()
        self.mock_ctx.config = CopilotConfig()
        self.mock_ctx.show_toast = MagicMock()
        self.mock_ctx._on_config_saved = MagicMock()
        self.mock_ctx._open_settings = MagicMock()

    def test_format_size(self):
        self.assertEqual(_format_size(0), "")
        self.assertEqual(_format_size(500 * 1024 * 1024), "500 MB")
        self.assertEqual(_format_size(4920757726), "4.6 GB")

    def test_dialog_instantiation_and_structure(self):
        dlg = ModelSelectorDialog(self.mock_ctx)
        self.assertIsNotNone(dlg.get_child())
        self.assertEqual(dlg.get_title(), "Selecionar Modelo / Agente")

        # Verifica presença dos grupos principais
        self.assertIsNotNone(dlg.featured_group)
        self.assertIsNotNone(dlg.ollama_group)
        self.assertIsNotNone(dlg.cloud_group)
        self.assertIsNotNone(dlg.live_group)
        self.assertIsNotNone(dlg.voices_group)
        self.assertIsNotNone(dlg.dolphin_row)
        self.assertEqual(len(dlg._live_model_rows), 3)
        self.assertEqual(len(dlg._voice_rows), 8)

    def test_dolphin_card_content(self):
        dlg = ModelSelectorDialog(self.mock_ctx)
        title = dlg.dolphin_row.get_title()
        self.assertIn("Dolphin 3.1", title)
        subtitle = dlg.dolphin_row.get_subtitle()
        self.assertIn("sem censura", subtitle.lower())

    def test_render_models_ui_populates_ollama_and_lmstudio(self):
        dlg = ModelSelectorDialog(self.mock_ctx)

        fake_ollama_models = [
            {
                "name": "dolphin3:latest",
                "size": 4920757726,
                "details": {"parameter_size": "8.0B", "quantization_level": "Q4_K_M"},
            },
            {
                "name": "qwen2.5vl:7b",
                "size": 5969245856,
                "details": {"parameter_size": "8.3B", "quantization_level": "Q4_K_M"},
            },
        ]
        fake_lmstudio_models = [
            {"id": "Qwen3.5-9B-Uncensored-HauhauCS-Aggressive"},
        ]

        dlg._render_models_ui(fake_ollama_models, "", fake_lmstudio_models)

        self.assertEqual(len(dlg._dynamic_ollama_rows), 2)
        titles = [row.get_title() for row in dlg._dynamic_ollama_rows]
        self.assertIn("dolphin3:latest", titles)
        self.assertIn("qwen2.5vl:7b", titles)

        self.assertTrue(dlg.lmstudio_group.get_visible())
        self.assertEqual(len(dlg._dynamic_lm_rows), 1)
        self.assertEqual(dlg._dynamic_lm_rows[0].get_title(), "Qwen3.5-9B-Uncensored-HauhauCS-Aggressive")

    def test_render_models_ui_handles_offline_ollama(self):
        dlg = ModelSelectorDialog(self.mock_ctx)
        dlg._render_models_ui([], "Connection refused", [])
        self.assertEqual(len(dlg._dynamic_ollama_rows), 1)
        self.assertIn("não encontrado", dlg._dynamic_ollama_rows[0].get_title().lower())

    @patch.object(CopilotConfig, "save")
    def test_select_model_updates_config_and_notifies(self, mock_save):
        dlg = ModelSelectorDialog(self.mock_ctx)
        dlg._select_model("ollama", "dolphin3:latest", "Dolphin 3.1 (Uncensored)")

        self.mock_ctx._on_config_saved.assert_called_once()
        saved_cfg = self.mock_ctx._on_config_saved.call_args[0][0]
        self.assertEqual(saved_cfg.provider, "ollama")
        self.assertEqual(saved_cfg.ollama_model, "dolphin3:latest")
        self.mock_ctx.show_toast.assert_called_once()
        self.assertIn("Dolphin 3.1", self.mock_ctx.show_toast.call_args[0][0])
        mock_save.assert_called()

    @patch.object(CopilotConfig, "save")
    def test_live_model_selection(self, mock_save):
        dlg = ModelSelectorDialog(self.mock_ctx)
        mock_live_client = MagicMock()
        self.mock_ctx.live_client = mock_live_client

        dlg._select_live_model("models/gemini-3.8-live-extended-thinking", "Gemini 3.8 Live Extended Thinking")

        self.assertEqual(dlg.config.gemini_live_model, "models/gemini-3.8-live-extended-thinking")
        mock_live_client.set_model.assert_called_once_with("models/gemini-3.8-live-extended-thinking")
        mock_save.assert_called()
        self.mock_ctx.show_toast.assert_called_once()
        self.assertIn("Gemini 3.8 Live Extended Thinking", self.mock_ctx.show_toast.call_args[0][0])
        # Confirma checkmark atualizado
        self.assertTrue(dlg._live_model_checks["models/gemini-3.8-live-extended-thinking"].get_visible())
        self.assertFalse(dlg._live_model_checks["models/gemini-3.8-live"].get_visible())

    @patch.object(CopilotConfig, "save")
    def test_live_voice_selection(self, mock_save):
        dlg = ModelSelectorDialog(self.mock_ctx)
        mock_live_client = MagicMock()
        self.mock_ctx.live_client = mock_live_client

        dlg._select_live_voice("Aoede", "Feminina · Suave")

        self.assertEqual(dlg.config.gemini_live_voice, "Aoede")
        mock_live_client.set_voice.assert_called_once_with("Aoede")
        mock_save.assert_called()
        self.mock_ctx.show_toast.assert_called_once()
        self.assertIn("Aoede", self.mock_ctx.show_toast.call_args[0][0])
        # Confirma checkmark atualizado
        self.assertTrue(dlg._voice_checks["Aoede"].get_visible())
        self.assertFalse(dlg._voice_checks["Puck"].get_visible())

    @patch("zorin_copilot.ai.voice_preview.VoicePreviewService.play_voice")
    def test_voice_preview_button_interaction(self, mock_play):
        dlg = ModelSelectorDialog(self.mock_ctx)
        self.assertIn("Puck", dlg._voice_preview_btns)
        puck_btn = dlg._voice_preview_btns["Puck"]
        self.assertIsNotNone(puck_btn)

        dlg._toggle_voice_preview("Puck")
        mock_play.assert_called_once()
        self.assertEqual(mock_play.call_args[0][0], "Puck")

    def test_load_models_async_skips_when_test_mode_active(self):
        with patch.dict(os.environ, {"ZORIN_TEST_MODE": "1"}):
            with patch("threading.Thread") as mock_thread:
                dlg = ModelSelectorDialog(self.mock_ctx)
                dlg._load_models_async()
                mock_thread.assert_not_called()


class DolphinAgentIntegrationTest(unittest.TestCase):
    def test_dolphin_addon_in_ollama_provider_chat(self):
        prov = OllamaProvider(model="dolphin3:latest")
        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "message": {"content": '{"explanation": "Olá!", "actions": []}'},
            }
            ans, actions = prov.chat("teste")
            mock_post.assert_called_once()
            call_payload = mock_post.call_args[1]["json"]
            sys_msg = call_payload["messages"][0]["content"]
            self.assertIn("DOLPHIN 3.1", sys_msg)
            self.assertIn("UNCENSORED", sys_msg)

    def test_dolphin_instruction_in_llm_planner(self):
        class FakeDolphinProvider:
            model = "dolphin3:latest"

            def complete(self, prompt, system_prompt=None, json_mode=True):
                self.last_sys_prompt = system_prompt
                return '{"final_answer": "pronto"}'

        fake_prov = FakeDolphinProvider()
        planner = LLMPlanner(fake_prov, name="dolphin")
        decision = planner.decide("executar comando", [], [])

        self.assertEqual(decision.final_answer, "pronto")
        self.assertIn("Agente Dolphin 3.1", fake_prov.last_sys_prompt)

    @patch.object(CopilotConfig, "save", autospec=True)
    def test_cli_set_agent_preset_dolphin(self, mock_save):
        parser = build_parser()
        args = parser.parse_args(["config", "--set-agent-preset", "dolphin"])
        ret = cmd_config(args)
        self.assertEqual(ret, 0)
        mock_save.assert_called_once()
        saved_cfg = mock_save.call_args[0][0]
        self.assertEqual(saved_cfg.provider, "ollama")
        self.assertEqual(saved_cfg.ollama_model, "dolphin3:latest")
