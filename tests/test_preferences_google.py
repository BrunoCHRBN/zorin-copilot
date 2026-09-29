"""Testes da seção de Contas Google nas preferências (Fase 4).

Cobrem a **forma** (um grupo, uma linha por serviço, cada linha com switch e botão)
e o **estado** (o que o subtítulo e o botão dizem em cada combinação de conexão e
habilitação). O ponto que mais importa: mexer no toggle de um serviço **não pode**
apagar a permissão dos outros quando a allowlist está vazia.
"""

import unittest
from unittest.mock import patch

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402

require_gtk4()
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

try:  # descoberta como pacote (`-t .`) vs. descoberta simples
    from tests._gtk_helpers import descendants
except ImportError:  # pragma: no cover - depende do runner
    from _gtk_helpers import descendants

from zorin_copilot.core.config import CopilotConfig  # noqa: E402
from zorin_copilot.core.google_auth import SERVICE_LABELS, known_services  # noqa: E402
from zorin_copilot.ui.preferences import PreferencesDialog  # noqa: E402


def _fake_status(connected):
    return [
        {
            "service": service,
            "label": SERVICE_LABELS[service],
            "connected": service in connected,
            "has_refresh": service in connected,
            "libs_available": True,
            "scopes": [],
        }
        for service in known_services()
    ]


class GoogleSectionBase(unittest.TestCase):
    def _dialog(self, *, enabled=True, services=None, connected=(), avisos=()):
        status = patch(
            "zorin_copilot.core.google_auth.connection_status",
            return_value=_fake_status(set(connected)),
        )
        status.start()
        self.addCleanup(status.stop)

        # O aviso de expiração lê o cofre de verdade; fixá-lo mantém os testes
        # determinísticos e rápidos (e permite exercitar cada severidade).
        warnings = patch(
            "zorin_copilot.core.google_auth.refresh_expiry_warnings",
            return_value=list(avisos),
        )
        warnings.start()
        self.addCleanup(warnings.stop)

        cfg = CopilotConfig()
        cfg.google_enabled = enabled
        cfg.google_enabled_services = list(services if services is not None else [])
        with patch.object(CopilotConfig, "load", return_value=cfg):
            return PreferencesDialog(None)


class GoogleSectionFormTest(GoogleSectionBase):
    def test_group_holds_one_row_per_service(self):
        dialog = self._dialog()
        self.assertIsInstance(dialog.google_group, Adw.PreferencesGroup)
        self.assertEqual(set(dialog.google_service_rows), set(known_services()))

    def test_rows_use_the_service_labels_as_titles(self):
        dialog = self._dialog()
        for service, row in dialog.google_service_rows.items():
            self.assertEqual(row.get_title(), SERVICE_LABELS[service])

    def test_every_row_has_a_switch_and_a_button(self):
        dialog = self._dialog()
        for service, row in dialog.google_service_rows.items():
            widgets = list(descendants(row))
            self.assertTrue(any(isinstance(w, Gtk.Switch) for w in widgets), service)
            self.assertTrue(any(isinstance(w, Gtk.Button) for w in widgets), service)

    def test_master_switch_reflects_config(self):
        self.assertTrue(self._dialog(enabled=True).google_switch_row.get_active())
        self.assertFalse(self._dialog(enabled=False).google_switch_row.get_active())


class GoogleSectionStateTest(GoogleSectionBase):
    def test_enabled_but_not_connected_says_reconnect(self):
        dialog = self._dialog(enabled=True, services=["calendar"], connected=())
        self.assertEqual(
            dialog.google_service_rows["calendar"].get_subtitle(), "Reconexão necessária"
        )

    def test_not_enabled_says_not_connected(self):
        dialog = self._dialog(enabled=True, services=["calendar"], connected=())
        self.assertEqual(dialog.google_service_rows["drive"].get_subtitle(), "Não conectado")

    def test_connected_says_connected_and_button_offers_disconnect(self):
        dialog = self._dialog(enabled=True, services=["calendar"], connected=["calendar"])
        self.assertEqual(dialog.google_service_rows["calendar"].get_subtitle(), "Conectado")
        self.assertEqual(dialog.google_service_buttons["calendar"].get_label(), "Desconectar")

    def test_master_off_makes_buttons_insensitive(self):
        dialog = self._dialog(enabled=False, services=["calendar"], connected=())
        self.assertFalse(dialog.google_service_buttons["calendar"].get_sensitive())

    def test_empty_allowlist_shows_every_switch_on(self):
        dialog = self._dialog(enabled=True, services=[])
        for service, switch in dialog.google_service_switches.items():
            self.assertTrue(switch.get_active(), service)


class GoogleServiceToggleTest(GoogleSectionBase):
    def test_turning_one_off_keeps_the_others_on(self):
        """O ponto crítico: lista vazia = sem restrição, não "tudo desligado".

        Sem materializar a allowlist antes de desligar, o usuário perderia os
        outros dois serviços sem ter pedido.
        """
        dialog = self._dialog(enabled=True, services=[])
        dialog.google_service_switches["drive"].set_active(False)
        self.assertEqual(
            sorted(dialog.config.google_enabled_services),
            sorted(s for s in known_services() if s != "drive"),
        )

    def test_turning_one_on_adds_it_to_the_allowlist(self):
        dialog = self._dialog(enabled=True, services=["calendar"])
        dialog.google_service_switches["drive"].set_active(True)
        self.assertIn("drive", dialog.config.google_enabled_services)

    def test_collect_follows_the_switches(self):
        dialog = self._dialog(enabled=True, services=["calendar"])
        dialog.google_service_switches["drive"].set_active(True)
        collected = dialog._collect_current_config()
        self.assertTrue(collected.google_enabled)
        self.assertEqual(sorted(collected.google_enabled_services), ["calendar", "drive"])

    def test_master_switch_feeds_collect(self):
        dialog = self._dialog(enabled=True, services=[])
        dialog.google_switch_row.set_active(False)
        self.assertFalse(dialog._collect_current_config().google_enabled)

    def test_handlers_are_inert_while_loading(self):
        """O guarda existe para os `set_active` do carregamento não escreverem.

        Exercitado direto: sem o guarda, mexer no controle com a bandeira ligada
        ainda dispara o handler e altera a config.
        """
        dialog = self._dialog(enabled=True, services=[])
        dialog._loading_google = True
        try:
            dialog.google_service_switches["drive"].set_active(False)
            dialog.google_switch_row.set_active(False)
        finally:
            dialog._loading_google = False
        self.assertEqual(dialog.config.google_enabled_services, [])
        self.assertTrue(dialog.config.google_enabled)

    def test_collect_follows_the_control_not_a_stale_config(self):
        """Sob o guarda, controle e config divergem — o coletor segue o CONTROLE.

        É a convenção da tela inteira: quem manda é o widget. Ler a config aqui
        faria um valor velho vencer o que o usuário acabou de ver na tela.
        """
        dialog = self._dialog(enabled=True, services=[])
        dialog._loading_google = True
        try:
            dialog.google_switch_row.set_active(False)
        finally:
            dialog._loading_google = False
        self.assertFalse(dialog._collect_current_config().google_enabled)


class GoogleExpiryNoticeTest(GoogleSectionBase):
    """O aviso de expiração do refresh token na tela."""

    @staticmethod
    def _aviso(severity="unknown", **extra):
        aviso = {
            "service": "calendar",
            "label": "Google Agenda",
            "age_days": None,
            "days_left": None,
            "severity": severity,
            "message": "mensagem de teste",
        }
        aviso.update(extra)
        return aviso

    def test_row_is_hidden_when_there_is_nothing_to_say(self):
        dialog = self._dialog()
        self.assertFalse(dialog.google_warning_row.get_visible())

    def test_unknown_age_shows_a_question_not_silence(self):
        dialog = self._dialog(avisos=[self._aviso("unknown")])
        row = dialog.google_warning_row
        self.assertTrue(row.get_visible(), "idade desconhecida não pode virar silêncio")
        self.assertIn("desconhecida", row.get_title())
        self.assertEqual(dialog.google_warning_icon.get_icon_name(), "dialog-question-symbolic")

    def test_expired_authorization_uses_the_warning_icon(self):
        dialog = self._dialog(avisos=[self._aviso("warning", age_days=8.0, days_left=-1.0)])
        self.assertEqual(dialog.google_warning_row.get_title(), "Autorização expirada")
        self.assertEqual(dialog.google_warning_icon.get_icon_name(), "dialog-warning-symbolic")

    def test_upcoming_expiry_uses_the_info_icon(self):
        dialog = self._dialog(avisos=[self._aviso("info", age_days=6.0, days_left=1.0)])
        self.assertEqual(dialog.google_warning_row.get_title(), "Expiração da autorização")
        self.assertEqual(dialog.google_warning_icon.get_icon_name(), "dialog-information-symbolic")

    def test_several_services_are_summarized(self):
        avisos = [
            self._aviso("info", age_days=6.0, days_left=1.0),
            self._aviso("info", service="drive", age_days=6.0, days_left=1.0),
        ]
        dialog = self._dialog(avisos=avisos)
        self.assertIn("mais 1 serviço", dialog.google_warning_row.get_subtitle())


class GoogleConnectButtonTest(GoogleSectionBase):
    def test_click_when_connected_disconnects(self):
        dialog = self._dialog(enabled=True, services=["calendar"], connected=["calendar"])
        with patch("zorin_copilot.core.google_auth.is_connected", return_value=True):
            with patch("zorin_copilot.core.google_auth.disconnect", return_value=True) as disc:
                dialog._on_google_connect_clicked(
                    dialog.google_service_buttons["calendar"], "calendar"
                )
        disc.assert_called_once_with("calendar")

    def test_click_when_not_connected_starts_the_flow(self):
        dialog = self._dialog(enabled=True, services=["calendar"], connected=())
        button = dialog.google_service_buttons["calendar"]

        class _SyncThread:
            """Roda o alvo na hora: o teste não pode depender do agendador."""

            def __init__(self, target=None, daemon=None, name=None):
                self._target = target
                self.name = name

            def start(self):
                self._target()

        with patch("zorin_copilot.core.google_auth.is_connected", return_value=False):
            with patch(
                "zorin_copilot.core.google_auth.run_connect_flow",
                return_value={"label": "Google Agenda"},
            ) as flow:
                with patch("zorin_copilot.ui.preferences.threading.Thread", _SyncThread):
                    dialog._on_google_connect_clicked(button, "calendar")

        flow.assert_called_once_with("calendar")
        self.assertEqual(button.get_label(), "Aguardando…")


if __name__ == "__main__":
    unittest.main()
