"""Testes da integração de blur real com o compositor (Hyprland via hyprctl).

Tudo headless e isolado: `subprocess.run` é mockado, então nenhum comando real
é disparado. O foco é: detecção de capacidade, comandos corretos, idempotência,
remoção e os caminhos de "desligado"/"sem hyprctl".
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from zorin_copilot.core.desktop.env import Environment  # noqa: E402
from zorin_copilot.ui import hyprland_effects  # noqa: E402
from zorin_copilot.ui.hyprland_effects import HyprlandEffects  # noqa: E402


def _env(desktop: str, binaries=("hyprctl",)) -> Environment:
    return Environment(
        session_type="wayland",
        desktop=desktop,
        binaries=frozenset(binaries),
    )


def _fake_run(returncode: int = 0):
    """Substituto de subprocess.run que finge sucesso sem executar nada."""
    result = mock.MagicMock()
    result.returncode = returncode
    result.stderr = ""
    return result


class CapabilityTest(unittest.TestCase):
    def test_hyprland_com_suporte(self):
        env = _env("hyprland")
        self.assertTrue(env.supports_compositor_blur)
        self.assertTrue(env.is_hyprland)

    def test_gnome_sem_suporte(self):
        env = _env("gnome")
        self.assertFalse(env.supports_compositor_blur)

    def test_wlroots_sem_hyprctl_sem_suporte(self):
        env = _env("sway", binaries=())
        self.assertFalse(env.supports_compositor_blur)

    def test_x11_sem_suporte(self):
        env = Environment(session_type="x11", desktop="xfce", binaries=frozenset({"hyprctl"}))
        self.assertFalse(env.supports_compositor_blur)

    def test_blur_namespace_estavel(self):
        self.assertEqual(Environment.blur_namespace(), "zorin-copilot-pill")


class HyprlandEffectsTest(unittest.TestCase):
    def setUp(self):
        self.fx = HyprlandEffects()
        self.patcher_which = mock.patch.object(
            hyprland_effects.shutil, "which", return_value="/usr/bin/hyprctl"
        )
        self.patcher_which.start()
        self.patcher_run = mock.patch.object(
            hyprland_effects.subprocess, "run", return_value=_fake_run()
        )
        self.mock_run = self.patcher_run.start()
        # Simula sessão Hyprland viva.
        self._env_token = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = "test-signature"

    def tearDown(self):
        self.patcher_which.stop()
        self.patcher_run.stop()
        if self._env_token is None:
            os.environ.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
        else:
            os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = self._env_token

    def _calls(self):
        return [tuple(c.args[0]) for c in self.mock_run.call_args_list]

    def test_disponivel_com_hyprland_e_hyprctl(self):
        self.assertTrue(self.fx.is_available())

    def test_ensure_window_blur_emite_windowrule(self):
        self.fx.ensure_window_blur("io.github.bruno.ZorinCopilot")
        self.assertIn(
            ("hyprctl", "keyword", "windowrule", "blur,io.github.bruno.ZorinCopilot"),
            self._calls(),
        )
        self.assertIn(
            ("hyprctl", "keyword", "windowrule", "rounding,io.github.bruno.ZorinCopilot"),
            self._calls(),
        )

    def test_ensure_layer_blur_emite_layerrule(self):
        self.fx.ensure_layer_blur("zorin-copilot-pill")
        self.assertIn(
            ("hyprctl", "keyword", "layerrule", "blur,zorin-copilot-pill"),
            self._calls(),
        )
        self.assertIn(
            ("hyprctl", "keyword", "layerrule", "rounding,zorin-copilot-pill"),
            self._calls(),
        )

    def test_idempotencia_nao_repetir(self):
        self.fx.ensure_window_blur("io.github.bruno.ZorinCopilot")
        before = len(self._calls())
        self.fx.ensure_window_blur("io.github.bruno.ZorinCopilot")
        # Nenhuma chamada nova após a primeira aplicação.
        self.assertEqual(len(self._calls()), before)

    def test_remove_window_blur_emite_noblur(self):
        self.fx.ensure_window_blur("io.github.bruno.ZorinCopilot")
        self.mock_run.reset_mock()
        self.fx.remove_window_blur("io.github.bruno.ZorinCopilot")
        self.assertIn(
            ("hyprctl", "keyword", "windowrule", "noblur,io.github.bruno.ZorinCopilot"),
            self._calls(),
        )

    def test_apply_for_app_cobre_janela_e_pilula(self):
        self.fx.apply_for_app("io.github.bruno.ZorinCopilot")
        cmd_strs = [" ".join(c) for c in self._calls()]
        self.assertTrue(any("windowrule blur," in s for s in cmd_strs))
        self.assertTrue(any("layerrule blur,zorin-copilot-pill" in s for s in cmd_strs))

    def test_desligado_por_env_nao_faz_nada(self):
        os.environ["ZORIN_COPILOT_DISABLE_COMPOSITOR_BLUR"] = "1"
        try:
            self.fx.ensure_window_blur("io.github.bruno.ZorinCopilot")
            self.fx.ensure_layer_blur("zorin-copilot-pill")
            self.assertEqual(self._calls(), [])
        finally:
            os.environ.pop("ZORIN_COPILOT_DISABLE_COMPOSITOR_BLUR", None)

    def test_sem_hyprctl_nao_levanta(self):
        with mock.patch.object(hyprland_effects.shutil, "which", return_value=None):
            self.fx.reset_cache()
            # Sem binário e sem sessão: is_available False, chamadas viram no-op.
            os.environ.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
            self.assertFalse(self.fx.is_available())
            self.fx.ensure_window_blur("io.github.bruno.ZorinCopilot")
            self.fx.ensure_layer_blur("zorin-copilot-pill")
            self.assertEqual(self._calls(), [])


if __name__ == "__main__":
    unittest.main()
