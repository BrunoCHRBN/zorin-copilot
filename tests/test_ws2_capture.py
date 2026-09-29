"""Testes WS2/B1-B2: resolução de fonte de captura filtrada (NS+AGC) e enrich_capture_cmd.

Estes testes NÃO dependem de PipeWire real: `subprocess.run` e `shutil.which` são
mockados para exercitar os caminhos de sucesso, falha e cache de forma determinística.
O caminho real de criação do módulo (graph RNNoise/compander) continua marcado como
'validar em hardware real' no plano Entrega A (R5).
"""

import subprocess
import unittest
from unittest.mock import patch

from zorin_copilot.core.audio.capture import (
    enrich_capture_cmd,
    get_processed_capture_source,
    reset_cache,
)


def _fake_run_factory(fail_load: bool = False):
    """Simula `subprocess.run` para `pw-dump` e `pw-cli load-module`.

    `fail_load=True` reproduz o defeito real: `pw-cli load-module` retorna código 0
    mesmo quando falha, jogando "Could not load module" para o stderr. A detecção
    por returncode apenas produziria falso positivo.
    """

    def _run(args, *a, **k):
        cmd = list(args) if args else []
        if "load-module" in cmd:
            if fail_load:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="", stderr="Error: Could not load module"
                )
            return subprocess.CompletedProcess(
                cmd, 0, stdout="loaded module id: 99", stderr=""
            )
        if cmd[:2] == ["pw-dump", "Node"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return _run


class CaptureCmdEnrichTest(unittest.TestCase):
    def test_pw_record_inserts_target_before_dash(self):
        cmd = ["pw-record", "--raw", "--rate", "16000", "-"]
        out = enrich_capture_cmd(cmd, "mysrc")
        self.assertEqual(
            out, ["pw-record", "--raw", "--rate", "16000", "--target", "mysrc", "-"]
        )

    def test_arecord_inserts_device_before_dash(self):
        cmd = ["arecord", "-r", "16000", "-"]
        out = enrich_capture_cmd(cmd, "hw:1,0")
        self.assertEqual(out, ["arecord", "-r", "16000", "-D", "hw:1,0", "-"])

    def test_no_target_returns_unchanged(self):
        cmd = ["pw-record", "--raw", "-"]
        self.assertEqual(enrich_capture_cmd(cmd, None), cmd)
        self.assertEqual(enrich_capture_cmd(cmd, ""), cmd)

    def test_no_trailing_dash_returns_unchanged(self):
        cmd = ["pw-record", "--raw", "x"]
        self.assertEqual(enrich_capture_cmd(cmd, "s"), cmd)

    def test_does_not_mutate_input(self):
        cmd = ["pw-record", "--raw", "-"]
        _ = enrich_capture_cmd(cmd, "s")
        self.assertEqual(cmd, ["pw-record", "--raw", "-"])


class CaptureSourceResolutionTest(unittest.TestCase):
    def setUp(self):
        reset_cache()

    def tearDown(self):
        reset_cache()

    @patch("zorin_copilot.core.audio.capture.shutil.which", return_value=None)
    def test_no_pipewire_returns_none(self, _):
        self.assertIsNone(get_processed_capture_source())

    @patch("zorin_copilot.core.audio.capture.shutil.which", return_value="/usr/bin/pw-cli")
    @patch("zorin_copilot.core.audio.capture.subprocess.run")
    def test_pw_load_failure_false_positive_returns_none(self, mock_run, _):
        # REGRESSÃO WS2/B1: pw-cli load-module retorna 0 mas falha (erro no stderr).
        # A resolução deve retornar None (mic padrão), não um source inexistente.
        mock_run.side_effect = _fake_run_factory(fail_load=True)
        self.assertIsNone(get_processed_capture_source())

    @patch("zorin_copilot.core.audio.capture.shutil.which", return_value="/usr/bin/pw-cli")
    @patch("zorin_copilot.core.audio.capture.subprocess.run")
    def test_pw_load_success_returns_source(self, mock_run, _):
        mock_run.side_effect = _fake_run_factory(fail_load=False)
        self.assertEqual(get_processed_capture_source(), "zorin-copilot-mic-ns-agc")

    @patch("zorin_copilot.core.audio.capture.shutil.which", return_value="/usr/bin/pw-cli")
    @patch("zorin_copilot.core.audio.capture.subprocess.run")
    def test_idempotent_cache(self, mock_run, _):
        mock_run.side_effect = _fake_run_factory(fail_load=False)
        first = get_processed_capture_source()
        second = get_processed_capture_source()  # deve vir do cache, sem novo subprocess
        self.assertEqual(first, second)
        # 1x pw-dump (find existing) + 1x load-module; a 2ª chamada usa cache.
        self.assertEqual(mock_run.call_count, 2)

    @patch("zorin_copilot.core.audio.capture.shutil.which", return_value="/usr/bin/pw-cli")
    def test_ns_agc_disabled_returns_none(self, _):
        # Sem NS nem AGC, nem tenta criar o filtro (mantém mic padrão).
        self.assertIsNone(get_processed_capture_source(enable_ns=False, enable_agc=False))


if __name__ == "__main__":
    unittest.main()
