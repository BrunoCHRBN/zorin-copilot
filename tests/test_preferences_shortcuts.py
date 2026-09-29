"""Testes da página Atalhos — chips de wake word e seletor do modelo Vosk.

Cobrem a reformulação §2.5 do plano (`docs/PROPOSTA_REFORMULACAO_GUI.md`):

- as frases de ativação deixam de ser um campo com vírgulas e passam a ser
  *chips* removíveis — a vírgula era sintaxe invisível;
- o caminho do modelo Vosk ganha um seletor nativo de pasta em vez de exigir
  digitação às cegas.

Os testes estão divididos em dois níveis: `WakePhraseChipsTest` exercita o
controle isolado, e `WakeWordPageTest` verifica que ele está de fato ligado à
configuração que o diálogo salva.
"""

import unittest
from unittest.mock import MagicMock, patch

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402

require_gtk4()
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

try:  # descoberta como pacote (`-t .`) vs. descoberta simples
    from tests._gtk_helpers import descendants, first_controller, icon_names, labels
except ImportError:  # pragma: no cover - depende do runner
    from _gtk_helpers import descendants, first_controller, icon_names, labels

from zorin_copilot.ui.preferences import PreferencesDialog, WakePhrasesRow  # noqa: E402

DEFAULT_PHRASES = ["ok copilot", "olá copilot"]


class WakePhraseChipsTest(unittest.TestCase):
    """O controle isolado: a lista é um conjunto de objetos, não uma string."""

    def setUp(self):
        self.row = WakePhrasesRow()

    # -- utilidades --------------------------------------------------------
    def _chip_count(self) -> int:
        count = 0
        child = self.row._flow.get_first_child()
        while child is not None:
            count += 1
            child = child.get_next_sibling()
        return count

    def _first_chip(self) -> Gtk.Widget:
        return self.row._flow.get_first_child().get_child()

    def _chip_remove_button(self, chip: Gtk.Widget) -> Gtk.Button:
        for node in descendants(chip):
            if isinstance(node, Gtk.Button):
                return node
        raise AssertionError("o chip não tem botão de remover")

    # -- estado ------------------------------------------------------------
    def test_set_phrases_fills_list_and_chips(self):
        self.row.set_phrases(["um", "dois", "três"])

        self.assertEqual(self.row.get_phrases(), ["um", "dois", "três"])
        self.assertEqual(self._chip_count(), 3, "um chip por frase")

    def test_refresh_rebuilds_instead_of_stacking(self):
        """Repopular é idempotente — chips são reconstruídos, não empilhados."""
        self.row.set_phrases(["um", "dois"])
        self.row.set_phrases(["um", "dois"])

        self.assertEqual(self._chip_count(), 2)

    def test_get_phrases_returns_a_copy(self):
        """Mexer na lista devolvida não pode alterar o controle por baixo."""
        self.row.set_phrases(["um"])

        self.row.get_phrases().append("intruso")

        self.assertEqual(self.row.get_phrases(), ["um"])

    def test_empty_state_explains_the_consequence(self):
        """Sem frases o Copilot não acorda — a tela precisa dizer isso."""
        self.row.set_phrases([])

        self.assertEqual(self._chip_count(), 0)
        self.assertTrue(self.row._empty_label.get_visible())
        self.assertIn("não acordará", self.row._empty_label.get_text())

        self.row.set_phrases(["um"])
        self.assertFalse(self.row._empty_label.get_visible())

    # -- validação ---------------------------------------------------------
    def test_duplicate_is_rejected_in_its_normalized_form(self):
        """`OK, Copilot!` e `ok copilot` são a MESMA frase para o motor.

        O Vosk casa por substring sobre o texto normalizado (minúsculas, sem
        pontuação), então aceitar as duas criaria duas entradas de comportamento
        idêntico. A normalização vem do próprio motor, não de uma cópia local.
        """
        self.row.set_phrases(["ok copilot"])

        self.assertFalse(self.row.add_phrase("OK, Copilot!"))
        self.assertFalse(self.row.add_phrase("ok   copilot"))
        self.assertEqual(self.row.get_phrases(), ["ok copilot"])

    def test_internal_whitespace_is_collapsed(self):
        self.assertTrue(self.row.add_phrase("  ei   assistente  "))

        self.assertEqual(self.row.get_phrases(), ["ei assistente"])

    def test_casing_is_preserved_for_display(self):
        """Normalizar é para comparar, não para exibir: a caixa digitada fica."""
        self.row.add_phrase("Ei Assistente")

        self.assertEqual(self.row.get_phrases(), ["Ei Assistente"])

    def test_empty_phrase_is_rejected(self):
        self.assertFalse(self.row.add_phrase(""))
        self.assertFalse(self.row.add_phrase("     "))
        self.assertEqual(self.row.get_phrases(), [])

    def test_phrase_without_letters_is_rejected(self):
        """`!!!` normaliza para vazio e casaria com qualquer transcrição."""
        self.assertFalse(self.row.add_phrase("!!!"))

        self.assertEqual(self.row.get_phrases(), [])

    def test_accents_are_kept(self):
        """Português depende de acento; a normalização não pode comê-lo."""
        self.row.add_phrase("olá copilot")

        self.assertEqual(self.row.get_phrases(), ["olá copilot"])

    # -- remoção -----------------------------------------------------------
    def test_remove_takes_the_phrase_out_of_list_and_screen(self):
        self.row.set_phrases(["um", "dois"])

        self.assertTrue(self.row.remove_phrase("um"))

        self.assertEqual(self.row.get_phrases(), ["dois"])
        self.assertEqual(self._chip_count(), 1)

    def test_removing_an_unknown_phrase_is_a_no_op(self):
        self.row.set_phrases(["um"])

        self.assertFalse(self.row.remove_phrase("nada"))
        self.assertEqual(self.row.get_phrases(), ["um"])

    # -- forma dos chips ---------------------------------------------------
    def test_chip_hides_the_remove_button_until_hover(self):
        """Ação destrutiva não fica à vista convidando ao clique errado."""
        self.row.set_phrases(["um"])
        chip = self._first_chip()
        button = self._chip_remove_button(chip)

        self.assertFalse(button.get_visible(), "lixeira deveria começar escondida")

        motion = first_controller(chip, Gtk.EventControllerMotion)
        self.assertIsNotNone(motion, "o chip precisa de um controlador de hover")
        motion.emit("enter", 0.0, 0.0)
        self.assertTrue(button.get_visible(), "hover deveria revelar a lixeira")
        motion.emit("leave")
        self.assertFalse(button.get_visible())

    def test_chip_remove_button_uses_a_symbolic_icon(self):
        self.row.set_phrases(["um"])
        button = self._chip_remove_button(self._first_chip())

        self.assertEqual(button.get_icon_name(), "window-close-symbolic")

    def test_chip_leads_with_the_text_not_a_glyph(self):
        """Decisão de desenho: o chip não tem ícone decorativo.

        O glifo seria idêntico em todos os chips da mesma lista e não
        distinguiria nada — repetir N vezes o mesmo símbolo é decoração, não
        informação. O único ícone do chip é o `window-close-symbolic` do botão
        de remover, que fica no fim. Este teste existe para que "melhorar" o
        chip com um ícone exija uma decisão consciente.
        """
        self.row.set_phrases(["um"])

        self.assertIsInstance(self._first_chip().get_first_child(), Gtk.Label)

    def test_chip_label_shows_the_phrase(self):
        self.row.set_phrases(["ok copilot"])

        self.assertIn("ok copilot", labels(self._first_chip()))

    # -- entrada -----------------------------------------------------------
    def test_enter_adds_the_phrase_and_clears_the_field(self):
        self.row._entry.set_text("ei copilot")

        self.row._entry.emit("activate")

        self.assertEqual(self.row.get_phrases(), ["ei copilot"])
        self.assertEqual(self.row._entry.get_text(), "")

    def test_a_rejected_phrase_stays_in_the_field(self):
        """Apagar o que a pessoa digitou sem explicação é pior que deixar corrigir."""
        self.row.set_phrases(["ok copilot"])
        self.row._entry.set_text("OK, Copilot!")

        self.row._entry.emit("activate")

        self.assertEqual(self.row._entry.get_text(), "OK, Copilot!")
        self.assertEqual(self.row.get_phrases(), ["ok copilot"])

    def test_row_is_not_activatable(self):
        """A linha contém um campo de texto: o clique vai para o campo."""
        self.assertFalse(self.row.get_activatable())


class WakeWordPageTest(unittest.TestCase):
    """O controle está de fato ligado à configuração que o diálogo salva."""

    def setUp(self):
        self.dlg = PreferencesDialog(None)

    def test_saved_phrases_reach_the_chips(self):
        self.assertEqual(self.dlg.wake_phrases_row.get_phrases(), DEFAULT_PHRASES)

    def test_reloading_config_replaces_the_chips(self):
        self.dlg.config.wake_phrases = ["ei assistente"]

        self.dlg._load_values()

        self.assertEqual(self.dlg.wake_phrases_row.get_phrases(), ["ei assistente"])

    def test_chip_edits_reach_the_saved_config(self):
        self.dlg.wake_phrases_row.add_phrase("ei assistente")

        saved = self.dlg._collect_current_config()

        self.assertEqual(saved.wake_phrases, DEFAULT_PHRASES + ["ei assistente"])

    def test_removing_a_phrase_reaches_the_saved_config(self):
        self.dlg.wake_phrases_row.remove_phrase("olá copilot")

        saved = self.dlg._collect_current_config()

        self.assertEqual(saved.wake_phrases, ["ok copilot"])

    def test_clearing_every_phrase_falls_back_to_the_defaults(self):
        """Lista vazia desligaria a wake word em silêncio; o padrão é o freio."""
        self.dlg.wake_phrases_row.set_phrases([])

        saved = self.dlg._collect_current_config()

        self.assertEqual(saved.wake_phrases, DEFAULT_PHRASES)

    def test_comma_separated_field_is_gone(self):
        """Regressão: o campo com vírgulas não pode voltar.

        A varredura é no grupo inteiro, e não só na linha, porque a queixa
        original era justamente o campo escondendo a sintaxe num texto corrido.
        """
        self.assertIsInstance(self.dlg.wake_phrases_row, WakePhrasesRow)
        self.assertNotIsInstance(self.dlg.wake_phrases_row, Adw.EntryRow)

        texts = labels(self.dlg._wake_group)
        self.assertIn("Frases de Ativação", texts)
        for text in texts:
            self.assertNotIn("vírgula", text.lower(), f"rótulo reintroduziu a vírgula: {text!r}")

    def test_wake_page_has_no_stray_entry_row_for_phrases(self):
        """Nenhuma `Adw.EntryRow` do grupo pode ser a lista de frases."""
        entry_rows = [
            node for node in descendants(self.dlg._wake_group) if isinstance(node, Adw.EntryRow)
        ]
        self.assertNotIn(self.dlg.wake_phrases_row, entry_rows)

    def test_model_row_offers_a_native_picker(self):
        self.assertEqual(self.dlg.wake_model_row.get_title(), "Pasta do Modelo Vosk")

        icons = icon_names(self.dlg.wake_model_row)
        self.assertEqual(
            icons.count("folder-open-symbolic"),
            1,
            "a linha do modelo precisa de exatamente um botão de selecionar pasta",
        )
        for name in icons:
            self.assertTrue(
                name.endswith("-symbolic"),
                f"ícone {name!r} não é simbólico do Adwaita",
            )

    def test_model_picker_chooses_a_folder_not_a_file(self):
        """O modelo do Vosk é uma PASTA (am/, conf/, graph/…).

        Escolher um arquivo produziria um caminho que o `vosk.Model()` recusa em
        runtime, sem o usuário entender por quê. Este teste fixa a escolha —
        divergência deliberada do texto do plano, que falava em "selecionar
        arquivo".
        """
        with patch.object(Gtk, "FileDialog") as file_dialog:
            self.dlg._on_choose_wake_model(MagicMock())

        file_dialog.return_value.select_folder.assert_called_once()
        file_dialog.return_value.open.assert_not_called()

    def test_picker_writes_the_chosen_folder_into_the_field(self):
        dialog = MagicMock()
        folder = MagicMock()
        folder.get_path.return_value = "/home/alguem/modelos/vosk-pt"
        dialog.select_folder_finish.return_value = folder

        self.dlg._on_wake_model_chosen(dialog, None)

        self.assertEqual(
            self.dlg.wake_model_row.get_text(), "/home/alguem/modelos/vosk-pt"
        )
        self.assertEqual(
            self.dlg._collect_current_config().wake_word_model_path,
            "/home/alguem/modelos/vosk-pt",
        )

    def test_cancelled_picker_leaves_the_field_untouched(self):
        self.dlg.wake_model_row.set_text("/modelo/antigo")

        dialog = MagicMock()
        dialog.select_folder_finish.return_value = None

        self.dlg._on_wake_model_chosen(dialog, None)

        self.assertEqual(self.dlg.wake_model_row.get_text(), "/modelo/antigo")


if __name__ == "__main__":
    unittest.main()
