"""Testes unitários para o módulo de voz local contínua (Whisper + Piper TTS) e atalhos de voz."""

import unittest
from unittest.mock import MagicMock, patch

from zorin_copilot.ai.actions import ActionPlan, ActionType, DesktopAction
from zorin_copilot.ai.live import LiveVoiceState
from zorin_copilot.ai.local_voice import (
    LocalLiveVoiceClient,
    LocalVoiceSynthesizer,
    LocalVoiceTranscriber,
    build_spoken_response,
    clean_text_for_speech,
)
from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.core.desktop import shortcuts as desktop_shortcuts
from zorin_copilot.core.shortcuts import ShortcutManager


class TestCleanTextForSpeech(unittest.TestCase):
    """Testes para o sanitizador de texto pré-TTS."""

    def test_clean_empty_or_none(self):
        self.assertEqual(clean_text_for_speech(""), "")
        self.assertEqual(clean_text_for_speech("   "), "")

    def test_clean_markdown_formatting(self):
        raw = "Aqui está um **texto em negrito** e outro *em itálico* com `código inline`."
        expected = "Aqui está um texto em negrito e outro em itálico com código inline."
        self.assertEqual(clean_text_for_speech(raw), expected)

    def test_clean_code_blocks(self):
        raw = "Execute o comando a seguir:\n```bash\nsudo apt update\nsudo apt upgrade\n```\nIsso atualizará o sistema."
        cleaned = clean_text_for_speech(raw)
        self.assertNotIn("```", cleaned)
        self.assertNotIn("sudo apt", cleaned)
        self.assertIn("código de terminal executado no sistema", cleaned)

    def test_clean_copilot_badges_and_emojis(self):
        raw = "⚡ *[Modo Local]* 🎙️ Olá Bruno! [⚡ minicpm-v] Como posso ajudar?"
        cleaned = clean_text_for_speech(raw)
        self.assertNotIn("⚡", cleaned)
        self.assertNotIn("Modo Local", cleaned)
        self.assertNotIn("minicpm-v", cleaned)
        self.assertNotIn("🎙️", cleaned)
        self.assertIn("Olá Bruno! Como posso ajudar?", cleaned)

    def test_clean_urls_and_headers(self):
        raw = "### Título da Seção\nVisite https://zorin.com para mais detalhes."
        cleaned = clean_text_for_speech(raw)
        self.assertNotIn("###", cleaned)
        self.assertNotIn("https://zorin.com", cleaned)
        self.assertIn("o site indicado", cleaned)

    def test_clean_citations(self):
        raw = "Aqui está o resultado: [1] Compre com frete grátis [2] e aproveite [fonte: duckduckgo]."
        cleaned = clean_text_for_speech(raw)
        self.assertNotIn("[1]", cleaned)
        self.assertNotIn("[2]", cleaned)
        self.assertNotIn("[fonte: duckduckgo]", cleaned)
        self.assertEqual(cleaned, "Aqui está o resultado: Compre com frete grátis e aproveite .")


class TestBuildSpokenResponse(unittest.TestCase):
    """Testes para a geração de confirmações de voz concisas (anti-prolixidade)."""

    def test_open_url_with_verbose_search_snippets(self):
        raw_thought = (
            "Aqui está o resultado da pesquisa no Mercado Livre: "
            "[1] Compre produtos com Frete Grátis no mesmo dia no MercadoLivreBrasil. "
            "Encontre milhares de marcas e produtos no precinho. Lojas oficiais. Categorias. "
            "Ofertas do dia. Histórico. Moda. Vender. Contato."
        )
        action = DesktopAction(
            action_type=ActionType.OPEN_URL,
            target="https://www.mercadolivre.com.br",
            description="Abrir fonte: Mercado Livre Brasil",
        )
        spoken = build_spoken_response(raw_thought, executable_actions=[action])
        self.assertEqual(
            spoken,
            "Aqui está o resultado da pesquisa no Mercado Livre. Abri a página no seu navegador.",
        )
        self.assertNotIn("[1]", spoken)
        self.assertNotIn("Lojas oficiais", spoken)

    def test_open_url_already_mentioning_open(self):
        thought = "Abri a página do Mercado Livre no navegador para você ver as opções."
        action = DesktopAction(
            action_type=ActionType.OPEN_URL,
            target="https://www.mercadolivre.com.br",
        )
        spoken = build_spoken_response(thought, executable_actions=[action])
        self.assertEqual(
            spoken,
            "Abri a página do Mercado Livre no navegador para você ver as opções.",
        )

    def test_launch_app_speech(self):
        action = DesktopAction(
            action_type=ActionType.LAUNCH_APP,
            target="gnome-calculator",
            description="Abrir Calculadora",
        )
        spoken = build_spoken_response("", executable_actions=[action])
        self.assertEqual(spoken, "Abri gnome-calculator para você.")

    def test_informational_response_limits_sentences(self):
        long_info = (
            "A física quântica estuda os fenômenos em escala atômica e subatômica. "
            "Ela descreve o comportamento de fótons e elétrons com precisão matemática. "
            "Essa teoria foi desenvolvida ao longo do século vinte por físicos renomados. "
            "Diversas tecnologias modernas dependem diretamente desses princípios fundamentais."
        )
        spoken = build_spoken_response(long_info, executable_actions=[], max_sentences=2, max_chars=220)
        self.assertEqual(
            spoken,
            "A física quântica estuda os fenômenos em escala atômica e subatômica. Ela descreve o comportamento de fótons e elétrons com precisão matemática.",
        )
        self.assertNotIn("século vinte", spoken)


class TestLocalVoiceSynthesizer(unittest.TestCase):
    """Testes para o sintetizador Piper TTS local."""

    def test_empty_text_returns_false(self):
        synth = LocalVoiceSynthesizer()
        done_called = False

        def on_done():
            nonlocal done_called
            done_called = True

        res = synth.speak("", on_done=on_done)
        self.assertFalse(res)
        self.assertTrue(done_called)

    @patch("shutil.which")
    @patch("subprocess.Popen")
    def test_speak_successful_stream(self, mock_popen, mock_which):
        mock_which.return_value = "/usr/bin/pw-play"
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_popen.return_value = mock_proc

        synth = LocalVoiceSynthesizer()
        synth._voice = MagicMock()
        mock_chunk = MagicMock()
        mock_chunk.audio_int16_bytes = b"fake_audio_pcm"
        synth._voice.synthesize.return_value = [mock_chunk]

        res = synth.speak("Texto para sintetizar")
        self.assertTrue(res)
        mock_proc.stdin.write.assert_called_with(b"fake_audio_pcm")
        mock_proc.stdin.close.assert_called()

    def test_interrupt_stops_playback(self):
        synth = LocalVoiceSynthesizer()
        mock_proc = MagicMock()
        synth._playback_proc = mock_proc
        synth._is_speaking = True

        synth.interrupt()
        self.assertFalse(synth.is_speaking)
        self.assertTrue(synth._stop_event.is_set())
        mock_proc.terminate.assert_called()


class TestLocalVoiceTranscriber(unittest.TestCase):
    """Testes para o transcritor faster-whisper local."""

    def test_empty_audio_returns_empty_string(self):
        transcriber = LocalVoiceTranscriber()
        self.assertEqual(transcriber.transcribe_pcm(b""), "")
        self.assertEqual(transcriber.transcribe_pcm(b"123"), "")

    def test_transcribe_pcm_formats_text(self):
        transcriber = LocalVoiceTranscriber()
        transcriber._model = MagicMock()

        mock_seg1 = MagicMock()
        mock_seg1.text = " Abrir o navegador "
        mock_seg2 = MagicMock()
        mock_seg2.text = " e pesquisar "
        transcriber._model.transcribe.return_value = ([mock_seg1, mock_seg2], MagicMock())

        # Cria buffer int16 simulado
        fake_pcm = b"\x00\x00" * 2000
        text = transcriber.transcribe_pcm(fake_pcm)
        self.assertEqual(text, "Abrir o navegador e pesquisar")

    def test_transcribe_pcm_filters_phantom_hallucinations(self):
        """Garante que frases fantasmas do Whisper (como 'Muito obrigado.') são descartadas."""
        transcriber = LocalVoiceTranscriber()
        transcriber._model = MagicMock()

        for phantom in ["Muito obrigado.", "Muito obrigado!", "Inscreva-se no canal", "Obrigado por assistir ao vídeo"]:
            mock_seg = MagicMock()
            mock_seg.text = phantom
            mock_seg.no_speech_prob = 0.0
            mock_seg.avg_logprob = -0.2
            transcriber._model.transcribe.return_value = ([mock_seg], MagicMock())

            fake_pcm = b"\x00\x00" * 2000
            result = transcriber.transcribe_pcm(fake_pcm)
            self.assertEqual(result, "", f"Falhou em descartar a alucinação '{phantom}'")


class TestLocalLiveVoiceClient(unittest.TestCase):
    """Testes para o cliente de voz contínua local."""

    def setUp(self):
        self.config = CopilotConfig(provider="ollama")
        self.mock_executor = MagicMock()
        self.mock_engine = MagicMock()
        self.client = LocalLiveVoiceClient(
            config=self.config,
            executor=self.mock_executor,
            engine=self.mock_engine,
        )

    def test_initial_state(self):
        self.assertEqual(self.client.state, LiveVoiceState.DISCONNECTED)
        self.assertFalse(self.client.is_active())
        self.assertFalse(self.client.is_video_streaming())
        self.assertFalse(self.client.is_muted())

        self.client._is_running = True
        self.client.state = LiveVoiceState.LISTENING
        self.assertTrue(self.client.is_active())

    def test_toggle_mute(self):
        is_muted = self.client.toggle_mute()
        self.assertTrue(is_muted)
        self.assertTrue(self.client.is_muted())

        is_muted = self.client.toggle_mute()
        self.assertFalse(is_muted)
        self.assertFalse(self.client.is_muted())

    def test_interrupt_while_speaking(self):
        self.client.state = LiveVoiceState.SPEAKING
        self.client.synthesizer = MagicMock()
        self.client.interrupt()
        self.client.synthesizer.interrupt.assert_called_once()
        self.assertEqual(self.client.state, LiveVoiceState.LISTENING)

    def test_process_utterance_executes_actions_and_speaks(self):
        self.client.transcriber = MagicMock()
        self.client.transcriber.transcribe_pcm.return_value = "abra as configurações de som"

        self.client.synthesizer = MagicMock()

        mock_action = DesktopAction(
            action_type=ActionType.LAUNCH_APP,
            target="gnome-control-center sound",
            description="Abrir configurações de som",
        )
        plan = ActionPlan(
            thought="Abrindo as configurações de som do sistema.",
            actions=[mock_action],
        )
        self.mock_engine.parse.return_value = plan
        mock_result = MagicMock()
        mock_result.success = True
        self.mock_executor.execute.return_value = mock_result

        transcripts = []
        self.client.on_transcript = lambda role, text: transcripts.append((role, text))
        tool_executed = []
        self.client.on_tool_executed = lambda name, desc, ok: tool_executed.append((name, desc, ok))

        # Dispara processamento
        self.client._process_utterance(b"fake_pcm_data")

        self.mock_engine.parse.assert_called_with("abra as configurações de som")
        self.mock_executor.execute.assert_called_with(mock_action)
        self.client.synthesizer.speak.assert_called_with("Abrindo as configurações de som do sistema.")

        self.assertEqual(transcripts, [
            ("user", "abra as configurações de som"),
            ("agent", "Abrindo as configurações de som do sistema."),
        ])
        self.assertEqual(len(tool_executed), 1)
        self.assertEqual(tool_executed[0][0], ActionType.LAUNCH_APP.value)

        summary = self.client.get_session_summary()
        self.assertTrue(summary["has_activity"])
        self.assertEqual(len(summary["actions_executed"]), 1)
        self.assertEqual(len(summary["transcripts"]), 2)

    def test_process_utterance_with_open_url_and_verbose_search_results(self):
        """Garante que ao abrir uma página web da busca, o sintetizador vocalize confirmação executiva e não leia o site inteiro."""
        self.client.transcriber = MagicMock()
        self.client.transcriber.transcribe_pcm.return_value = "pesquise computador no mercado livre"

        self.client.synthesizer = MagicMock()

        raw_thought = (
            "Aqui está o resultado da pesquisa no Mercado Livre: "
            "[1] Compre produtos com Frete Grátis no mesmo dia no MercadoLivreBrasil. "
            "Encontre milhares de marcas e produtos no precinho. Lojas oficiais. Categorias. "
            "Ofertas do dia. Histórico. Moda. Vender. Contato."
        )
        mock_action = DesktopAction(
            action_type=ActionType.OPEN_URL,
            target="https://www.mercadolivre.com.br",
            description="Abrir fonte: Mercado Livre Brasil",
        )
        plan = ActionPlan(
            thought=raw_thought,
            actions=[mock_action],
        )
        self.mock_engine.parse.return_value = plan
        mock_result = MagicMock()
        mock_result.success = True
        self.mock_executor.execute.return_value = mock_result

        transcripts = []
        self.client.on_transcript = lambda role, text: transcripts.append((role, text))

        # Dispara processamento
        self.client._process_utterance(b"fake_pcm_data")

        # Verifica se o executor abriu a URL
        self.mock_executor.execute.assert_called_with(mock_action)

        # Garante que o sintetizador vocalizou a frase executiva curta e NÃO leu o conteúdo cru
        expected_spoken = "Aqui está o resultado da pesquisa no Mercado Livre. Abri a página no seu navegador."
        self.client.synthesizer.speak.assert_called_with(expected_spoken)

        # Garante que o transcript também foi higienizado e não contém os menus de rodapé
        agent_transcripts = [t[1] for t in transcripts if t[0] == "agent"]
        self.assertEqual(len(agent_transcripts), 1)
        self.assertNotIn("[1]", agent_transcripts[0])
        self.assertNotIn("Lojas oficiais", agent_transcripts[0])
        self.assertIn("Mercado Livre", agent_transcripts[0])


class _RecordingBackend(desktop_shortcuts.ShortcutBackend):
    """Backend falso que só anota as chamadas — usado para testar o despacho."""

    name = "gravador-teste"

    def __init__(self):
        super().__init__(env=desktop_shortcuts.current_environment())
        self.registered: list[tuple[str, str, str]] = []
        self.unregistered: list[str] = []

    def is_supported(self):
        return True

    def register(self, slot, accelerator, command):
        self.registered.append((slot, accelerator, command))
        return desktop_shortcuts.ShortcutResult(True, "ok")

    def unregister(self, slot):
        self.unregistered.append(slot)
        return desktop_shortcuts.ShortcutResult(True, "ok")

    def is_registered(self, slot):
        return any(entry[0] == slot for entry in self.registered)


class TestVoiceShortcuts(unittest.TestCase):
    """O ShortcutManager precisa despachar para o backend do ambiente.

    Antes esses testes fixavam o caminho do GNOME (`_register_binding` com path
    de media-keys), o que amarrava a API a um único ambiente. Agora verificamos
    o contrato: slot certo, acelerador certo e flag certa no comando.
    """

    def setUp(self):
        self.backend = _RecordingBackend()
        self._patcher = patch.object(desktop_shortcuts, "select_backend", return_value=self.backend)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_register_voice_despacha_slot_e_flag_corretos(self):
        self.assertTrue(ShortcutManager.register_voice("<Super><Shift>v"))
        slot, accelerator, command = self.backend.registered[0]
        self.assertEqual(slot, "voice")
        self.assertEqual(accelerator, "<Super><Shift>v")
        self.assertTrue(command.endswith("--voice"))

    def test_register_hud_usa_flag_toggle(self):
        ShortcutManager.register("<Super>c")
        slot, _, command = self.backend.registered[0]
        self.assertEqual(slot, "hud")
        self.assertTrue(command.endswith("--toggle"))

    def test_register_crop_usa_flag_crop(self):
        ShortcutManager.register_crop("<Super><Shift>s")
        slot, _, command = self.backend.registered[0]
        self.assertEqual(slot, "crop")
        self.assertTrue(command.endswith("--crop"))

    def test_is_voice_registered_consulta_o_backend(self):
        self.assertFalse(ShortcutManager.is_voice_registered())
        ShortcutManager.register_voice()
        self.assertTrue(ShortcutManager.is_voice_registered())

    def test_unregister_voice_remove_o_slot(self):
        ShortcutManager.register_voice()
        self.assertTrue(ShortcutManager.unregister_voice())
        self.assertEqual(self.backend.unregistered, ["voice"])

    def test_backend_com_falha_devolve_falso_e_explica(self):
        with patch.object(
            desktop_shortcuts,
            "select_backend",
            return_value=desktop_shortcuts.NullShortcutBackend(),
        ):
            self.assertFalse(ShortcutManager.register_voice())
            self.assertIn("Nenhum backend", ShortcutManager.last_message)


if __name__ == "__main__":
    unittest.main()
