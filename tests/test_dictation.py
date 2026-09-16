"""Testes unitários e de integração do Ditado Global (Voice Typing)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.core.desktop.shortcuts import SLOT_DICTATE, _slot_label
from zorin_copilot.core.dictation import DictationService, DictationState
from zorin_copilot.core.shortcuts import _SLOT_FLAGS, ShortcutManager
from zorin_copilot.core.shortcuts_portal import PortalShortcutManager
from zorin_copilot.ui.dictation_osd import DictationOSDWindow


class DictationStateTest(unittest.TestCase):
    def test_states_defined(self):
        self.assertEqual(DictationState.IDLE.value, "idle")
        self.assertEqual(DictationState.LISTENING.value, "listening")
        self.assertEqual(DictationState.TRANSCRIBING.value, "transcribing")
        self.assertEqual(DictationState.TYPING.value, "typing")
        self.assertEqual(DictationState.DONE.value, "done")
        self.assertEqual(DictationState.ERROR.value, "error")


class DictationServiceTest(unittest.TestCase):
    def setUp(self):
        self.config = CopilotConfig(
            dictate_shortcut_enabled=True,
            dictate_shortcut_key="<Super><Shift>d",
            dictate_silence_timeout_sec=0.8,
            gemini_api_key="",
        )
        self.mock_driver = MagicMock()
        self.mock_transcriber = MagicMock()
        self.service = DictationService(
            config=self.config,
            input_driver=self.mock_driver,
            transcriber=self.mock_transcriber,
        )

    def test_initial_state_idle(self):
        self.assertEqual(self.service.state, DictationState.IDLE)
        self.assertFalse(self.service.is_active())

    def test_is_active_for_operational_states(self):
        self.service.state = DictationState.LISTENING
        self.assertTrue(self.service.is_active())
        self.service.state = DictationState.TRANSCRIBING
        self.assertTrue(self.service.is_active())
        self.service.state = DictationState.TYPING
        self.assertTrue(self.service.is_active())
        self.service.state = DictationState.DONE
        self.assertFalse(self.service.is_active())
        self.service.state = DictationState.ERROR
        self.assertFalse(self.service.is_active())

    def test_format_text(self):
        self.assertEqual(self.service._format_text("  olá mundo  "), "Olá mundo")
        self.assertEqual(self.service._format_text("teste de ditado"), "Teste de ditado")
        self.assertEqual(self.service._format_text(""), "")
        # Pontuação verbal
        self.assertEqual(
            self.service._format_text("olá vírgula tudo bem ponto de interrogação"),
            "Olá, tudo bem?",
        )
        # Polimento de hesitações
        self.assertEqual(
            self.service._format_text("ééé vamos testar ponto"),
            "Vamos testar.",
        )

    def test_transcribe_local_success(self):
        self.mock_transcriber.transcribe_pcm.return_value = "bom dia a todos"
        result = self.service._transcribe(b"\x00" * 3200)
        self.assertEqual(result, "bom dia a todos")
        self.mock_transcriber.transcribe_pcm.assert_called_once()

    def test_transcribe_cloud_fallback_when_local_fails(self):
        self.mock_transcriber.transcribe_pcm.side_effect = RuntimeError("Whisper OOM")
        self.service.config.gemini_api_key = "fake_key"

        with patch.object(self.service, "_transcribe_cloud_gemini", return_value="texto via gemini cloud"):
            result = self.service._transcribe(b"\x00" * 3200)
            self.assertEqual(result, "texto via gemini cloud")

    def test_inject_text_with_custom_injector(self):
        custom_called = []

        def custom_injector(text: str) -> bool:
            custom_called.append(text)
            return True

        self.service.custom_text_injector = custom_injector
        self.service._inject_text("Mensagem de teste")
        self.assertEqual(custom_called, ["Mensagem de teste"])
        self.mock_driver.type_text.assert_not_called()

    def test_inject_text_with_virtual_input_driver(self):
        self.mock_driver.is_available.return_value = True
        self.mock_driver.type_text.return_value = (True, "digitado")

        self.service._inject_text("Texto para digitação")
        self.mock_driver.type_text.assert_called_once_with("Texto para digitação")

    @patch("zorin_copilot.core.dictation.ClipboardService.set_text")
    def test_inject_text_clipboard_fallback_when_driver_fails(self, mock_clipboard):
        self.mock_driver.is_available.return_value = True
        self.mock_driver.type_text.return_value = (False, "falhou")

        self.service._inject_text("Texto salvo na área de transferência")
        mock_clipboard.assert_called_once_with("Texto salvo na área de transferência")

    def test_stop_cancels_recording(self):
        self.service._is_recording = True
        self.service.stop(cancel=True)
        self.assertTrue(self.service._cancelled)
        self.assertTrue(self.service._should_stop)


class DictationOSDWindowTest(unittest.TestCase):
    def setUp(self):
        self.app = Adw.Application.new("io.github.bruno.ZorinCopilot.TestOSD", 0)
        self.cancelled = False

        def _on_cancel():
            self.cancelled = True

        self.osd = DictationOSDWindow(application=self.app, on_cancel=_on_cancel)

    def tearDown(self):
        try:
            self.osd.close_osd()
        except Exception:
            pass

    def test_osd_never_steals_focus(self):
        """Regra de ouro: OSD não pode roubar o foco do aplicativo ativo."""
        self.assertFalse(self.osd.get_focusable())
        self.assertFalse(self.osd.get_can_focus())
        self.assertFalse(self.osd.get_decorated())
        self.assertFalse(self.osd.get_resizable())

    def test_update_state_listening(self):
        self.osd.update_state(DictationState.LISTENING, "Ouvindo microfone...")
        self.assertEqual(self.osd.status_label.get_text(), "Ouvindo microfone...")
        self.assertTrue(self.osd.level_bar.get_visible())

    def test_update_state_transcribing(self):
        self.osd.update_state(DictationState.TRANSCRIBING)
        self.assertEqual(self.osd.status_label.get_text(), "Transcrevendo fala...")
        self.assertFalse(self.osd.level_bar.get_visible())

    def test_update_state_typing(self):
        self.osd.update_state(DictationState.TYPING)
        self.assertEqual(self.osd.status_label.get_text(), "Digitando no app...")
        self.assertFalse(self.osd.level_bar.get_visible())

    def test_update_state_done(self):
        self.osd.update_state(DictationState.DONE, "✓ Olá mundo")
        self.assertEqual(self.osd.status_label.get_text(), "✓ Olá mundo")
        self.assertFalse(self.osd.level_bar.get_visible())

    def test_update_state_error(self):
        self.osd.update_state(DictationState.ERROR, "Microfone mudo")
        self.assertEqual(self.osd.status_label.get_text(), "Microfone mudo")

    def test_update_audio_level(self):
        self.osd.level_bar.set_visible(True)
        self.osd.update_audio_level(0.3)
        self.assertAlmostEqual(self.osd.level_bar.get_value(), 0.6, places=2)

    def test_cancel_button_triggers_callback(self):
        self.osd.cancel_btn.emit("clicked")
        self.assertTrue(self.cancelled)


class DictationShortcutsIntegrationTest(unittest.TestCase):
    def test_slot_dictate_defined(self):
        self.assertEqual(SLOT_DICTATE, "dictate")
        self.assertEqual(_slot_label("dictate"), "Zorin Copilot - Ditado")

    def test_slot_flags_include_dictate(self):
        self.assertIn("dictate", _SLOT_FLAGS)
        self.assertEqual(_SLOT_FLAGS["dictate"], "--dictate")

    def test_portal_shortcuts_include_dictate(self):
        ids = [sid for sid, _, _ in PortalShortcutManager.SHORTCUTS]
        self.assertIn("dictate", ids)

    def test_shortcut_manager_dictate_binding(self):
        binding = ShortcutManager.get_dictate_binding()
        self.assertTrue(binding.startswith("<"))
        self.assertIn("d", binding.lower())


class DictationAppPaletteIntegrationTest(unittest.TestCase):
    def setUp(self):
        from zorin_copilot.ui.app import CopilotWindow
        self.app = Adw.Application.new("io.github.bruno.ZorinCopilot.TestPalette", 0)
        self.win = CopilotWindow(self.app)

    def tearDown(self):
        try:
            self.win.destroy()
        except Exception:
            pass

    def test_palette_has_toggle_dictation(self):
        commands = self.win.palette_commands()
        names = [cmd.name for cmd in commands]
        self.assertIn("app.toggle-dictation", names)

    def test_palette_handlers_has_toggle_dictation(self):
        handlers = self.win._palette_handlers()
        self.assertIn("app.toggle-dictation", handlers)
        self.assertEqual(handlers["app.toggle-dictation"], self.win.toggle_dictation)

    def test_insert_text_to_entry(self):
        self.win.entry.set_text("Texto prévio")
        self.win._insert_text_to_entry("adicional")
        self.assertEqual(self.win.entry.get_text(), "Texto prévio adicional")


class SpokenPunctuationAndPolishTest(unittest.TestCase):
    def test_spoken_punctuation_comprehensive(self):
        from zorin_copilot.core.dictation import parse_spoken_punctuation

        # Vírgula, ponto e vírgula, interrogação
        self.assertEqual(
            parse_spoken_punctuation("olá vírgula tudo bem ponto de interrogação"),
            "olá, tudo bem?",
        )
        # Dois pontos e nova linha
        self.assertEqual(
            parse_spoken_punctuation("veja dois pontos nova linha primeiro item ponto final"),
            "veja:\nPrimeiro item.",
        )
        # Parênteses e aspas
        self.assertEqual(
            parse_spoken_punctuation("abrir aspas zorin copilot fechar aspas"),
            '"zorin copilot"',
        )
        # Preserva expressões com 'ponto' que não sejam pontuação
        self.assertEqual(
            parse_spoken_punctuation("do meu ponto de vista este é um ponto turístico"),
            "do meu ponto de vista este é um ponto turístico",
        )

    def test_smart_polish_speech(self):
        from zorin_copilot.core.dictation import smart_polish_speech

        # Hesitações no início
        self.assertEqual(
            smart_polish_speech("ééé vamos começar"),
            "vamos começar",
        )
        self.assertEqual(
            smart_polish_speech("tipo assim preciso de ajuda"),
            "preciso de ajuda",
        )
        # Repetições consecutivas (gaguejo)
        self.assertEqual(
            smart_polish_speech("eu acho que que devemos ir para para lá"),
            "eu acho que devemos ir para lá",
        )

    @patch("shutil.which", return_value="/usr/bin/canberra-gtk-play")
    @patch("subprocess.run")
    def test_play_sound_cue_invoked(self, mock_run, mock_which):
        from zorin_copilot.core.dictation import play_sound_cue
        import time

        play_sound_cue("message-new-instant")
        for _ in range(50):
            if mock_run.called:
                break
            time.sleep(0.02)
        self.assertTrue(mock_run.called)
        args, _ = mock_run.call_args
        self.assertEqual(args[0], ["canberra-gtk-play", "-i", "message-new-instant"])


class VirtualInputDriverMultilineTest(unittest.TestCase):
    @patch("subprocess.run")
    def test_wtype_multiline_newlines(self, mock_run):
        from zorin_copilot.shell.input_driver import VirtualInputDriver

        driver = VirtualInputDriver()
        driver.wtype_bin = "/usr/bin/wtype"
        mock_run.return_value.returncode = 0

        ok, msg = driver.type_text("Primeira linha\nSegunda linha")
        self.assertTrue(ok)
        # Deve ter chamado wtype com Primeira linha, depois wtype -k Return, depois Segunda linha
        calls = [c[0][0] for c in mock_run.call_args_list]
        has_return = any("-k" in cmd and "Return" in cmd for cmd in calls)
        self.assertTrue(has_return)


if __name__ == "__main__":
    unittest.main()
