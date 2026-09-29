"""Testes da página Privacidade — zonas de confiança editáveis e exemplos humanos.

Cobrem a reformulação §2.4 do plano (`docs/PROPOSTA_REFORMULACAO_GUI.md`):

- as pastas de cada zona deixam de ser texto puro e passam a ser linhas com
  remoção e um seletor de diretório — o que é listado é editável no mesmo lugar;
- cada toggle técnico ganha **um exemplo humano** no subtítulo, em vez de uma
  paráfrase do próprio nome.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402

require_gtk4()
from gi.repository import Adw  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

try:  # descoberta como pacote (`-t .`) vs. descoberta simples
    from tests._gtk_helpers import icon_names as _icon_names
except ImportError:  # pragma: no cover - depende do runner
    from _gtk_helpers import icon_names as _icon_names

from zorin_copilot.ui.preferences import PreferencesDialog  # noqa: E402


class TrustZonesTest(unittest.TestCase):
    """A lista de pastas precisa ser editável, não só legível."""

    def setUp(self):
        self.dlg = PreferencesDialog(None)

    def _set_zone(self, zone: str, paths: list[str]) -> None:
        attr = PreferencesDialog._ZONE_ATTRS[zone]
        setattr(self.dlg.config, attr, list(paths))
        group = self.dlg._trusted_group if zone == "trusted" else self.dlg._quarantine_group
        self.dlg._populate_zone_rows(group, zone)

    def _rows(self, zone: str) -> list:
        return list(getattr(self.dlg, f"_zone_rows_{zone}", []))

    def _dir_rows(self, zone: str) -> list:
        """Só as linhas de pasta — a última linha é sempre o 'Adicionar'."""
        return self._rows(zone)[:-1]

    def test_empty_zone_still_offers_the_add_row(self):
        """Zona vazia não pode virar um beco: o caminho de adicionar continua lá."""
        self._set_zone("trusted", [])

        rows = self._rows("trusted")
        self.assertEqual(len(rows), 1, "deveria sobrar apenas a linha de adicionar")
        self.assertIn("Adicionar", rows[0].get_title())

    def test_rows_mirror_configured_paths(self):
        self._set_zone("trusted", ["/tmp/alfa", "/tmp/beta"])

        titles = [row.get_title() for row in self._dir_rows("trusted")]
        self.assertEqual(titles, ["/tmp/alfa", "/tmp/beta"])

    def test_repopulate_does_not_duplicate_rows(self):
        """Repopular é idempotente — a lista é reconstruída, não empilhada."""
        self._set_zone("trusted", ["/tmp/alfa"])
        self._set_zone("trusted", ["/tmp/alfa"])

        self.assertEqual(len(self._dir_rows("trusted")), 1)

    def test_remove_takes_the_path_out_of_the_config(self):
        """O ponto da reformulação: remover da tela muda a configuração."""
        self._set_zone("trusted", ["/tmp/alfa", "/tmp/beta"])

        self.dlg._remove_zone_path(self.dlg._trusted_group, "trusted", "/tmp/alfa")

        self.assertEqual(self.dlg.config.trusted_directories, ["/tmp/beta"])
        self.assertEqual(
            [row.get_title() for row in self._dir_rows("trusted")], ["/tmp/beta"]
        )

    def test_remove_unknown_path_is_a_no_op(self):
        self._set_zone("quarantine", ["/tmp/baixados"])

        self.dlg._remove_zone_path(self.dlg._quarantine_group, "quarantine", "/tmp/inexistente")

        self.assertEqual(self.dlg.config.quarantine_directories, ["/tmp/baixados"])

    def test_removal_reaches_the_saved_config(self):
        """Remover na UI precisa sobreviver ao `_collect_current_config`."""
        self._set_zone("trusted", ["/tmp/alfa", "/tmp/beta"])
        self.dlg._remove_zone_path(self.dlg._trusted_group, "trusted", "/tmp/alfa")

        saved = self.dlg._collect_current_config()

        self.assertEqual(saved.trusted_directories, ["/tmp/beta"])

    def test_adding_a_folder_updates_the_config(self):
        self._set_zone("trusted", [])

        dialog = MagicMock()
        folder = MagicMock()
        folder.get_path.return_value = "/tmp/nova"
        dialog.select_folder_finish.return_value = folder

        self.dlg._on_zone_folder_chosen(dialog, None, (self.dlg._trusted_group, "trusted"))

        self.assertEqual(self.dlg.config.trusted_directories, ["/tmp/nova"])
        self.assertEqual([row.get_title() for row in self._dir_rows("trusted")], ["/tmp/nova"])

    def test_adding_the_same_folder_twice_is_ignored(self):
        self._set_zone("trusted", ["/tmp/nova"])

        dialog = MagicMock()
        folder = MagicMock()
        folder.get_path.return_value = "/tmp/nova"
        dialog.select_folder_finish.return_value = folder

        self.dlg._on_zone_folder_chosen(dialog, None, (self.dlg._trusted_group, "trusted"))

        self.assertEqual(self.dlg.config.trusted_directories, ["/tmp/nova"])

    def test_cancelled_folder_choice_changes_nothing(self):
        self._set_zone("trusted", ["/tmp/alfa"])

        dialog = MagicMock()
        dialog.select_folder_finish.return_value = None

        self.dlg._on_zone_folder_chosen(dialog, None, (self.dlg._trusted_group, "trusted"))

        self.assertEqual(self.dlg.config.trusted_directories, ["/tmp/alfa"])

    def test_zones_do_not_bleed_into_each_other(self):
        self._set_zone("trusted", ["/tmp/confiavel"])
        self._set_zone("quarantine", ["/tmp/cautela"])

        self.dlg._remove_zone_path(self.dlg._trusted_group, "trusted", "/tmp/confiavel")

        self.assertEqual(self.dlg.config.trusted_directories, [])
        self.assertEqual(self.dlg.config.quarantine_directories, ["/tmp/cautela"])


class HumanExamplesTest(unittest.TestCase):
    """Os toggles técnicos precisam da tradução em uma linha."""

    def setUp(self):
        self.dlg = PreferencesDialog(None)

    def test_pii_toggle_has_a_concrete_example(self):
        subtitle = self.dlg.mask_pii_switch_row.get_subtitle()
        self.assertTrue(subtitle)
        self.assertIn("CPF", subtitle)

    def test_rag_toggle_explains_the_benefit_in_plain_words(self):
        subtitle = self.dlg.rag_local_only_switch_row.get_subtitle()
        self.assertTrue(subtitle)
        self.assertIn("nunca toca a nuvem", subtitle)

    def test_auto_execute_toggle_says_what_actually_happens(self):
        subtitle = self.dlg.auto_execute_switch_row.get_subtitle()
        self.assertTrue(subtitle)
        self.assertIn("sem pedir confirmação", subtitle)

    def test_examples_are_not_just_the_title_restated(self):
        """Um exemplo que só repete o título não ajuda ninguém."""
        for row in (
            self.dlg.mask_pii_switch_row,
            self.dlg.rag_local_only_switch_row,
            self.dlg.auto_execute_switch_row,
        ):
            title = row.get_title().lower()
            subtitle = row.get_subtitle().lower()
            self.assertNotEqual(title, subtitle, f"subtítulo repete o título em {title!r}")
            self.assertNotIn(title, subtitle, f"subtítulo contém o título inteiro em {title!r}")


class PrivacyPageStructureTest(unittest.TestCase):
    """Contrato mínimo da página depois da reformulação."""

    def test_zone_groups_exist_and_are_titled(self):
        dlg = PreferencesDialog(None)
        self.assertTrue(dlg._trusted_group.get_title())
        self.assertTrue(dlg._quarantine_group.get_title())

    def test_add_row_uses_a_symbolic_icon_not_an_emoji(self):
        """Diretriz anti-slop: ícone simbólico do Adwaita, nunca emoji.

        O `Adw.ActionRow` empacota prefixos numa caixa interna, então a busca
        desce a árvore de widgets em vez de olhar só os irmãos diretos.
        """
        dlg = PreferencesDialog(None)
        row = dlg._build_add_folder_row(dlg._trusted_group, "trusted")

        icon_names = _icon_names(row)
        self.assertTrue(icon_names, "a linha de adicionar deveria ter ícone")
        for name in icon_names:
            self.assertTrue(
                name.endswith("-symbolic"),
                f"ícone {name!r} não é simbólico do Adwaita",
            )


if __name__ == "__main__":
    unittest.main()
