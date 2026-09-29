"""Nível de autonomia: normalização, teto em "low", promoção em "high" e persistência (B2).

Cobre o item 5 do `docs/ANEXO_AUTONOMIA_PREREQUISITOS.md` — o knob `autonomy_level` — e o núcleo
do item 6 (zonas de confiança) que dá sentido ao valor "high".
"""

import json
import os
import sys
import tempfile
import unittest

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402

require_gtk4()
from gi.repository import Adw  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.core.config import CopilotConfig  # noqa: E402
from zorin_copilot.shell.risk import (  # noqa: E402
    AUTONOMY_LEVELS,
    DEFAULT_AUTONOMY_LEVEL,
    RiskLevel,
    RiskPolicy,
    normalize_autonomy_level,
)
from zorin_copilot.ui.preferences import PreferencesDialog  # noqa: E402


class NormalizeAutonomyTest(unittest.TestCase):
    def test_valores_validos_passam(self):
        for v in AUTONOMY_LEVELS:
            self.assertEqual(normalize_autonomy_level(v), v)

    def test_normaliza_caixa_e_espaco(self):
        self.assertEqual(normalize_autonomy_level("  HIGH "), "high")
        self.assertEqual(normalize_autonomy_level("Low"), "low")

    def test_valor_invalido_cai_em_low(self):
        # Fail-closed pelo lado que se VÊ: um valor sem sentido nunca concede MAIS autonomia.
        for v in ("yolo", "", None, 3, "standard2", "máximo"):
            self.assertEqual(normalize_autonomy_level(v), "low")

    def test_default_do_dataclass_eh_standard(self):
        self.assertEqual(CopilotConfig().autonomy_level, "standard")
        self.assertEqual(DEFAULT_AUTONOMY_LEVEL, "standard")
        self.assertIn(DEFAULT_AUTONOMY_LEVEL, AUTONOMY_LEVELS)


class ConfigLoadNormalizationTest(unittest.TestCase):
    def _load_with(self, payload: dict) -> CopilotConfig:
        prev = os.environ.get("XDG_CONFIG_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                d = os.path.join(tmp, "zorin-copilot")
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                return CopilotConfig.load()
            finally:
                if prev is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = prev

    def test_load_normaliza_valor_invalido(self):
        self.assertEqual(self._load_with({"autonomy_level": "yolo"}).autonomy_level, "low")

    def test_load_preserva_valor_valido(self):
        self.assertEqual(self._load_with({"autonomy_level": "high"}).autonomy_level, "high")

    def test_load_sem_o_campo_usa_o_default(self):
        self.assertEqual(self._load_with({}).autonomy_level, "standard")


class RiskPolicyAutonomyTest(unittest.TestCase):
    def _auto_policy(self, level: str, trusted=None) -> RiskPolicy:
        p = RiskPolicy(autonomy_level=level, trusted_dirs=trusted)
        p.register_risk("ferramenta_x", RiskLevel.AUTO, "x")
        return p

    def test_low_rebaixa_auto_para_confirm(self):
        self.assertIs(
            self._auto_policy("low").classify("ferramenta_x", {})[0], RiskLevel.CONFIRM
        )

    def test_standard_honra_auto(self):
        self.assertIs(
            self._auto_policy("standard").classify("ferramenta_x", {})[0], RiskLevel.AUTO
        )

    def test_high_promove_escrita_em_zona_de_confianca(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = RiskPolicy(autonomy_level="high", trusted_dirs=[tmp])
            alvo = os.path.join(tmp, "sub", "nota.md")
            self.assertIs(p.classify("write_file", {"file_path": alvo})[0], RiskLevel.AUTO)
            # Fora da zona continua CONFIRM.
            self.assertIs(
                p.classify("write_file", {"file_path": "/etc/hosts"})[0], RiskLevel.CONFIRM
            )

    def test_standard_nao_promove_mesmo_em_zona(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = RiskPolicy(autonomy_level="standard", trusted_dirs=[tmp])
            self.assertIs(
                p.classify("write_file", {"file_path": os.path.join(tmp, "a.md")})[0],
                RiskLevel.CONFIRM,
            )

    def test_high_sem_zonas_nao_promove(self):
        p = RiskPolicy(autonomy_level="high")
        self.assertIs(p.classify("write_file", {"file_path": "/tmp/x"})[0], RiskLevel.CONFIRM)

    def test_set_trusted_dirs_reconfigura(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = RiskPolicy(autonomy_level="high")
            self.assertIs(p.classify("write_file", {"file_path": os.path.join(tmp, "a")})[0], RiskLevel.CONFIRM)
            p.set_trusted_dirs([tmp])
            self.assertIs(p.classify("write_file", {"file_path": os.path.join(tmp, "a")})[0], RiskLevel.AUTO)

    def test_valor_invalido_no_construtor_vira_low(self):
        p = self._auto_policy("garbage")
        self.assertEqual(p.autonomy_level, "low")
        self.assertIs(p.classify("ferramenta_x", {})[0], RiskLevel.CONFIRM)


class AutonomyPreferencesTest(unittest.TestCase):
    def test_combo_tem_as_tres_opcoes_na_ordem(self):
        dlg = PreferencesDialog(None)
        self.assertEqual([c for c, _ in dlg.autonomy_options], list(AUTONOMY_LEVELS))

    def test_subtitulo_diz_o_que_acontece(self):
        dlg = PreferencesDialog(None)
        sub = dlg.autonomy_level_row.get_subtitle()
        self.assertTrue(sub)
        self.assertIn("confirmação", sub)
        self.assertNotIn(dlg.autonomy_level_row.get_title().lower(), sub.lower())

    def test_carrega_o_nivel_da_config(self):
        dlg = PreferencesDialog(None)
        dlg.config.autonomy_level = "high"
        dlg._load_values()
        self.assertEqual(dlg.autonomy_level_row.get_selected(), 2)
        dlg.config.autonomy_level = "low"
        dlg._load_values()
        self.assertEqual(dlg.autonomy_level_row.get_selected(), 0)

    def test_coletor_preserva_o_nivel(self):
        # B2: sem a reatribuição em `_collect_current_config`, isto voltaria ao
        # default ("standard") — o defeito que já apagou o `system_prompt`.
        dlg = PreferencesDialog(None)
        dlg.autonomy_level_row.set_selected(2)  # high
        self.assertEqual(dlg._collect_current_config().autonomy_level, "high")
        dlg.autonomy_level_row.set_selected(0)  # low
        self.assertEqual(dlg._collect_current_config().autonomy_level, "low")


if __name__ == "__main__":
    unittest.main()
