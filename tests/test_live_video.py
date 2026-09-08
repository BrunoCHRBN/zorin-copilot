"""Testes unitários abrangentes para os aprimoramentos do Live Video:
- Focus Crop na Janela Ativa (DesktopInspector & ScreenCaptureService)
- Privacy Shield Automático (Detecção de apps/títulos sensíveis e frame protegido)
- Frame Diffing com FPS Adaptativo (Métricas de diferença visual)
- Miniatura Picture-in-Picture (PiP) e Controles de Modo no LiveVoiceWidget
- Botão de Pânico (Interrupção de emergência via Escape)
"""

from __future__ import annotations

import io
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from zorin_copilot.ai.live import GeminiLiveClient, LiveVoiceState
from zorin_copilot.core.a11y import DesktopInspector
from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.core.vision import ScreenCaptureService, compute_frame_diff

# pycairo não é dependência do app — nada em `src/` o importa. Só este teste de
# renderização precisa dele. Sem o skip, a suíte quebrava inteira no Arch, onde
# `python-gobject` não o traz: uma dependência apenas de teste não deveria
# derrubar a suíte de quem roda sem ela.
try:
    import cairo  # noqa: F401

    TEM_PYCAIRO = True
except ImportError:  # pragma: no cover
    TEM_PYCAIRO = False


class LiveVideoCoreTest(unittest.TestCase):
    """Testes para o pipeline de visão, enquadramento e escudo de privacidade."""

    def test_compute_frame_diff_identical(self):
        """Dois frames idênticos devem ter diferença zero."""
        img1 = Image.new("RGB", (640, 480), color=(100, 150, 200))
        img2 = Image.new("RGB", (640, 480), color=(100, 150, 200))
        diff = compute_frame_diff(img1, img2)
        self.assertAlmostEqual(diff, 0.0, places=4)

    def test_compute_frame_diff_opposite(self):
        """Frame todo preto vs todo branco deve resultar em diferença máxima (~1.0)."""
        black = Image.new("RGB", (640, 480), color=(0, 0, 0))
        white = Image.new("RGB", (640, 480), color=(255, 255, 255))
        diff = compute_frame_diff(black, white)
        self.assertAlmostEqual(diff, 1.0, places=2)

    def test_compute_frame_diff_small_variation(self):
        """Pequena alteração na imagem deve ter diferença mensurável porém baixa."""
        img1 = Image.new("RGB", (640, 480), color=(100, 100, 100))
        img2 = Image.new("RGB", (640, 480), color=(105, 105, 105))
        diff = compute_frame_diff(img1, img2)
        self.assertGreater(diff, 0.0)
        self.assertLess(diff, 0.05)

    def test_get_privacy_shield_image(self):
        """O escudo de privacidade deve retornar uma imagem JPEG válida e decodificável."""
        shield_bytes = ScreenCaptureService.get_privacy_shield_image()
        self.assertIsInstance(shield_bytes, bytes)
        self.assertGreater(len(shield_bytes), 500)

        # Decodifica e verifica dimensões
        img = Image.open(io.BytesIO(shield_bytes))
        self.assertEqual(img.format, "JPEG")
        self.assertEqual(img.size, (640, 360))

    def test_capture_with_crop_rect(self):
        """Verifica se o método _optimize_image respeita a tupla crop_rect (x, y, w, h)."""
        import os
        import tempfile

        full_img = Image.new("RGB", (1920, 1080), color=(50, 50, 50))
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            full_img.save(tf, format="PNG")
            tmp_path = tf.name

        try:
            crop_box = (100, 200, 600, 400)  # x=100, y=200, w=600, h=400
            cropped_bytes = ScreenCaptureService._optimize_image(tmp_path, max_size=800, crop_rect=crop_box)
            self.assertIsNotNone(cropped_bytes)

            img = Image.open(io.BytesIO(cropped_bytes))
            self.assertEqual(img.format, "JPEG")
            self.assertEqual(img.size, (600, 400))
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_get_active_window_info_fallback(self):
        """DesktopInspector deve retornar tupla segura se Atspi falhar ou não estiver disponível."""
        inspector = DesktopInspector()
        with patch.object(inspector, "_ensure_init", return_value=False):
            app_name, title, bbox = inspector.get_active_window_info()
            self.assertEqual(app_name, "")
            self.assertEqual(title, "")
            self.assertIsNone(bbox)

    def test_get_active_window_info_success(self):
        """DesktopInspector deve extrair nome, título e bbox da janela ativa."""
        mock_atspi = MagicMock()
        mock_desktop = MagicMock()
        mock_atspi.get_desktop.return_value = mock_desktop
        mock_atspi.StateType.ACTIVE = "ACTIVE"
        mock_atspi.StateType.FOCUSED = "FOCUSED"
        mock_atspi.CoordType.SCREEN = "SCREEN"

        mock_app = MagicMock()
        mock_app.get_name.return_value = "gnome-terminal"
        mock_app.get_child_count.return_value = 1

        mock_window = MagicMock()
        mock_window.get_name.return_value = "Terminal — bash"
        mock_state_set = MagicMock()
        mock_state_set.get_states.return_value = ["ACTIVE"]
        mock_window.get_state_set.return_value = mock_state_set

        mock_component = MagicMock()
        rect = MagicMock()
        rect.x = 50
        rect.y = 100
        rect.width = 800
        rect.height = 600
        mock_component.get_extents.return_value = rect
        mock_window.get_component_iface.return_value = mock_component

        mock_app.get_child_at_index.return_value = mock_window
        mock_desktop.get_child_count.return_value = 1
        mock_desktop.get_child_at_index.return_value = mock_app

        inspector = DesktopInspector(atspi_module=mock_atspi)
        inspector._initialized = True

        app, title, bbox = inspector.get_active_window_info()
        self.assertEqual(app, "gnome-terminal")
        self.assertEqual(title, "Terminal — bash")
        self.assertEqual(bbox, (50, 100, 800, 600))


class LiveVideoClientTest(unittest.TestCase):
    """Testes para o comportamento do GeminiLiveClient com vídeo inteligente."""

    def setUp(self):
        self.config = CopilotConfig(gemini_api_key="fake-key-live")
        self.client = GeminiLiveClient(config=self.config)

    def test_default_video_mode(self):
        """O modo padrão de vídeo deve ser 'active_window' conforme decisão de usabilidade."""
        self.assertEqual(self.client.video_mode, "active_window")

    def test_set_video_mode(self):
        """set_video_mode deve alternar entre active_window e fullscreen e rejeitar inválidos."""
        self.client.set_video_mode("fullscreen")
        self.assertEqual(self.client.video_mode, "fullscreen")

        self.client.set_video_mode("active_window")
        self.assertEqual(self.client.video_mode, "active_window")

        # Modo inválido é ignorado
        self.client.set_video_mode("modo_invalido_xyz")
        self.assertEqual(self.client.video_mode, "active_window")

    def test_panic_stop_video(self):
        """panic_stop_video deve cortar a transmissão de vídeo imediatamente."""
        self.client._is_running = True
        self.client.start_video_stream(fps=1.0)
        self.assertTrue(self.client.is_video_streaming())

        stopped = self.client.panic_stop_video()
        self.assertTrue(stopped)
        self.assertFalse(self.client.is_video_streaming())

    def test_sensitive_window_detection(self):
        """Verifica se palavras-chave bancárias e de segurança disparam o Privacy Shield."""
        # Palavras sensíveis padrão
        sensitive_cases = [
            ("bitwarden", "Bitwarden Password Vault"),
            ("firefox", "Banco Bradesco - Internet Banking"),
            ("google-chrome", "Nubank - Conta Digital"),
            ("keepassxc", "Minhas Senhas.kdbx"),
            ("chromium", "Navegação Anônima"),
            ("brave", "Private Browsing with Tor"),
        ]

        for app, title in sensitive_cases:
            combined = f"{app} {title}".lower()
            is_sensitive = any(kw in combined for kw in self.client.sensitive_keywords)
            self.assertTrue(is_sensitive, f"Falha ao detectar contexto sensível para: {app} / {title}")

        # Janela normal não deve disparar
        normal_cases = [
            ("code", "main.py — zorin-copilot"),
            ("gnome-terminal", "terminal - bash"),
            ("nautilus", "Downloads"),
        ]
        for app, title in normal_cases:
            combined = f"{app} {title}".lower()
            is_sensitive = any(kw in combined for kw in self.client.sensitive_keywords)
            self.assertFalse(is_sensitive, f"Falso positivo para: {app} / {title}")


class LiveVoiceWidgetUITest(unittest.TestCase):
    """Testes unitários para os componentes de UI do LiveVoiceWidget."""

    def test_widget_mode_toggle(self):
        """Testa a alternância do modo de janela/tela no widget."""
        from zorin_copilot.ui.live_view import LiveVoiceWidget

        mock_client = MagicMock()
        mock_client.video_mode = "active_window"
        mock_client.video_streaming = False

        widget = LiveVoiceWidget(live_client=mock_client)
        widget._on_toggle_mode(MagicMock())

        mock_client.set_video_mode.assert_called_with("fullscreen")

    def test_panic_key_controller(self):
        """Testa o acionamento do botão de pânico via tecla Escape."""
        import gi
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk
        from zorin_copilot.ui.live_view import LiveVoiceWidget

        mock_client = MagicMock()
        mock_client.video_streaming = True

        widget = LiveVoiceWidget(live_client=mock_client)
        handled = widget._on_key_pressed(MagicMock(), Gdk.KEY_Escape, 0, MagicMock())

        self.assertTrue(handled)
        mock_client.panic_stop_video.assert_called_once()

    def test_visualizer_styles_and_cycling(self):
        """Verifica se os 4 estilos visuais alternam corretamente e mantêm persistência."""
        from zorin_copilot.ui.live_view import LiveVoiceWidget

        mock_client = MagicMock()
        widget = LiveVoiceWidget(live_client=mock_client)
        widget.visualizer_style = "waves"
        self.assertEqual(widget._get_style_label(), "Ondas Fluidas (Siri)")

        widget._cycle_visualizer_style()
        self.assertEqual(widget.visualizer_style, "bars")
        self.assertEqual(widget._get_style_label(), "Barras de Equalizador")

        widget._cycle_visualizer_style()
        self.assertEqual(widget.visualizer_style, "matrix")
        self.assertEqual(widget._get_style_label(), "Matriz de Pontos (Grid)")

        widget._cycle_visualizer_style()
        self.assertEqual(widget.visualizer_style, "orb")
        self.assertEqual(widget._get_style_label(), "Orbe Pulsante (Clássico)")

        widget._cycle_visualizer_style()
        self.assertEqual(widget.visualizer_style, "waves")

    @unittest.skipUnless(TEM_PYCAIRO, "pycairo não instalado (python-cairo)")
    def test_visualizer_cairo_rendering(self):
        """Garante que todos os 4 estilos de visualizador renderizam no Cairo sem exceções."""
        import cairo
        from zorin_copilot.ui.live_view import LiveVoiceWidget

        mock_client = MagicMock()
        mock_client.state = LiveVoiceState.LISTENING
        widget = LiveVoiceWidget(live_client=mock_client)

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 280, 105)
        cr = cairo.Context(surface)

        # Testa renderização de cada estilo
        for style in ["waves", "bars", "matrix", "orb"]:
            widget.visualizer_style = style
            widget._draw_audio_visualizer(widget.drawing_area, cr, 280, 105)

    def test_live_widget_callbacks(self):
        """Testa se os callbacks de transcrição, execução de ferramenta e erro atualizam a UI sem exceções."""
        from zorin_copilot.ui.live_view import LiveVoiceWidget

        mock_client = MagicMock()
        widget = LiveVoiceWidget(live_client=mock_client)

        # Transcrição de usuário e assistente
        widget._ui_on_transcript("user", "que dia é hoje?")
        self.assertIn("que dia é hoje?", widget.subtitle_lbl.get_text())

        widget._ui_on_transcript("assistant", "Hoje é domingo.")
        self.assertIn("Hoje é domingo.", widget.subtitle_lbl.get_text())

        # Execução de ferramentas
        widget._ui_on_tool_executed("launch_app", "Abrindo terminal", True)
        self.assertIn("Abrindo terminal", widget.subtitle_lbl.get_text())

        # Mensagem de erro
        widget._ui_on_error("Microfone desconectado")
        self.assertIn("Microfone desconectado", widget.subtitle_lbl.get_text())


if __name__ == "__main__":
    unittest.main()
