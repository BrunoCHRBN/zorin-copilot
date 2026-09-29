"""Contrato do Privacy Shield por identidade de janela (Entrega E / P1-9).

Este arquivo existe por um motivo específico: o escudo de vídeo decidia por **substring
crua** (`"bb" in "hobby"`), e o teste que o cobria **repetia a mesma substring** em vez de
chamar a produção — então não podia falhar quando a decisão mudasse. Aqui a decisão é
exercida pela porta real (`PrivacyGatekeeper.evaluate_window` / `_window_is_sensitive`) e os
casos de falso positivo foram **medidos** antes de virarem teste.
"""

from __future__ import annotations

import unittest

from zorin_copilot.core.privacy_gate import PrivacyGatekeeper
from zorin_copilot.core.config import CopilotConfig


#: Janelas comuns que o matcher por substring ocultava por engano (medido).
#: Cada tupla é (app, título, substring que disparava o falso positivo).
SUBSTRING_FALSE_POSITIVES = [
    ("libreoffice-writer", "Hobby project notes.docx", "bb"),
    ("firefox", "Printer settings - Mozilla Firefox", "inter"),
    ("code", "interface.py — zorin-copilot", "inter"),
    ("nautilus", "Winter photos 2026", "inter"),
    ("gnome-calculator", "0xc6f3 + 12", "c6"),
    ("firefox", "Abbey Road - Wikipedia", "bb"),
]

#: Janelas que devem permanecer ocultas (verdadeiros positivos).
TRUE_POSITIVES = [
    ("bitwarden", "Bitwarden Password Vault"),
    ("firefox", "Banco Bradesco - Internet Banking"),
    ("google-chrome", "Nubank - Conta Digital"),
    ("keepassxc", "Minhas Senhas.kdbx"),
    ("chromium", "Navegação Anônima"),
    ("brave", "Private Browsing with Tor"),
    ("firefox", "Banco do Brasil - Extrato"),
    ("edge", "Microsoft Edge - InPrivate"),
    ("firefox", "C6 Bank - Cartão"),
    ("firefox", "Declaração IRPF 2026"),
]


class WindowDecisionTest(unittest.TestCase):
    """Decisão pura: `evaluate_window` sem listas do usuário."""

    def test_substring_false_positives_are_now_visible(self):
        """Regressão do defeito: substring dentro de outra palavra não oculta mais."""
        for app, title, culprit in SUBSTRING_FALSE_POSITIVES:
            with self.subTest(app=app, title=title):
                verdict = PrivacyGatekeeper.evaluate_window(app, title)
                self.assertFalse(
                    verdict.is_sensitive,
                    f"'{culprit}' casou dentro de outra palavra em: {app} / {title}",
                )
                self.assertTrue(verdict.allowed)

    def test_true_positives_are_still_shielded(self):
        """A correção não pode custar sensibilidade: os casos reais seguem ocultos."""
        for app, title in TRUE_POSITIVES:
            with self.subTest(app=app, title=title):
                verdict = PrivacyGatekeeper.evaluate_window(app, title)
                self.assertTrue(
                    verdict.is_sensitive,
                    f"Deveria permanecer oculto: {app} / {title}",
                )

    def test_word_boundary_is_the_mechanism(self):
        """A fronteira de palavra é o mecanismo — não um efeito colateral da lista."""
        # "inter" isolado (Banco Inter) oculta; dentro de "Printer" não.
        self.assertTrue(PrivacyGatekeeper.evaluate_window("firefox", "Banco Inter").is_sensitive)
        self.assertFalse(PrivacyGatekeeper.evaluate_window("firefox", "Printer queue").is_sensitive)
        # "BB" isolado (Banco do Brasil) oculta; dentro de "Abbey" não.
        self.assertTrue(PrivacyGatekeeper.evaluate_window("firefox", "BB Seguros").is_sensitive)
        self.assertFalse(PrivacyGatekeeper.evaluate_window("firefox", "Abbey Road").is_sensitive)


class UserListTest(unittest.TestCase):
    """Allowlist e denylist do usuário, com a precedência documentada."""

    def test_allowlist_releases_a_false_positive_title(self):
        """O consentimento do usuário libera uma janela cujo título casaria um padrão."""
        verdict = PrivacyGatekeeper.evaluate_window(
            "firefox", "Itaú - Internet Banking", allowlist=["firefox"]
        )
        self.assertTrue(verdict.allowed)
        self.assertFalse(verdict.is_sensitive)

    def test_allowlist_matches_exactly_not_by_substring(self):
        """A allowlist é identidade exata: 'firefox' não libera 'firefox-nightly'."""
        verdict = PrivacyGatekeeper.evaluate_window(
            "firefox-nightly", "Nubank - Conta", allowlist=["firefox"]
        )
        self.assertTrue(verdict.is_sensitive)

    def test_allowlist_normalizes_case_and_whitespace(self):
        """A comparação ignora caixa e espaços nas pontas.

        O título é **sensível de propósito** (`Itaú`/`Extrato`): se fosse um título neutro,
        a asserção passaria mesmo com a normalização removida — o teste não carregaria peso.
        """
        verdict = PrivacyGatekeeper.evaluate_window(
            "LibreOffice-Writer", "Itaú - Extrato", allowlist=["  libreoffice-writer  "]
        )
        self.assertTrue(
            verdict.allowed,
            "Sem normalização de caixa/espaços, a allowlist deixa de casar — e a janela volta a ser ocultada.",
        )
        self.assertFalse(verdict.is_sensitive)

    def test_denylist_shields_an_otherwise_normal_window(self):
        """A denylist acrescenta janelas a ocultar."""
        verdict = PrivacyGatekeeper.evaluate_window(
            "my-notes-app", "Notas", denylist=["my-notes-app"]
        )
        self.assertTrue(verdict.is_sensitive)

    def test_denylist_beats_allowlist(self):
        """Negação explícita vence uma entrada de allowlist possivelmente esquecida."""
        verdict = PrivacyGatekeeper.evaluate_window(
            "firefox", "Qualquer coisa", allowlist=["firefox"], denylist=["firefox"]
        )
        self.assertTrue(
            verdict.is_sensitive,
            "A denylist precisa vencer a allowlist — senão uma entrada velha revela a janela.",
        )

    def test_empty_or_none_lists_are_safe(self):
        """Listas ausentes/vazias não quebram nem liberam indevidamente."""
        for allow, deny in [(None, None), ([], []), (None, []), ([""], ["  "])]:
            with self.subTest(allow=allow, deny=deny):
                self.assertTrue(
                    PrivacyGatekeeper.evaluate_window("bitwarden", "Vault", allow, deny).is_sensitive
                )
                self.assertFalse(
                    PrivacyGatekeeper.evaluate_window("code", "main.py", allow, deny).is_sensitive
                )


class HardBlockTest(unittest.TestCase):
    """Cofres de credenciais e prompts de autenticação: a allowlist NÃO revela."""

    def test_password_managers_ignore_the_allowlist(self):
        """Expor uma senha na tela é irreversível — não há opt-in."""
        for app in [
            "bitwarden",
            "1password",
            "keepassxc",
            "org.keepassxc.KeePassXC",
            "seahorse",
            "pinentry-gtk-2",
            "gcr-prompter",
            "gnome-keyring-prompt",
            "polkit-gnome-authentication-agent-1",
        ]:
            with self.subTest(app=app):
                verdict = PrivacyGatekeeper.evaluate_window(
                    app, "Main Window", allowlist=[app]
                )
                self.assertTrue(
                    verdict.is_sensitive,
                    f"A allowlist não pode revelar o cofre: {app}",
                )

    def test_hard_block_beats_the_denylist_too(self):
        """O cofre é ocultado pela identidade, não por estar na denylist."""
        verdict = PrivacyGatekeeper.evaluate_window(
            "bitwarden", "Vault", allowlist=["bitwarden"], denylist=[]
        )
        self.assertTrue(verdict.is_sensitive)
        self.assertIn("credenciais", verdict.reason.lower())


class ShouldIngestUnchangedTest(unittest.TestCase):
    """O caminho da RNN (`should_ingest_window`) mantém o contrato anterior."""

    def test_should_ingest_window_ignores_user_lists(self):
        """`should_ingest_window` é o matcher de padrões puro — sem política de listas."""
        # Mesmo que o usuário quisesse allowlistar, este caminho não a consulta.
        self.assertFalse(PrivacyGatekeeper.should_ingest_window("firefox", "Nubank").allowed)
        self.assertTrue(PrivacyGatekeeper.should_ingest_window("code", "main.py").allowed)

    def test_evaluate_window_falls_back_to_should_ingest_window(self):
        """Sem listas, `evaluate_window` concorda com o matcher de padrões."""
        cases = [("code", "main.py"), ("bitwarden", "Vault"), ("firefox", "Nubank")]
        for app, title in cases:
            with self.subTest(app=app):
                self.assertEqual(
                    PrivacyGatekeeper.evaluate_window(app, title).is_sensitive,
                    not PrivacyGatekeeper.should_ingest_window(app, title).allowed,
                )


class ClientWiringTest(unittest.TestCase):
    """A fiação do cliente: `_window_is_sensitive` lê as listas do config."""

    def _client(self, **cfg_kwargs):
        from zorin_copilot.ai.live import GeminiLiveClient

        return GeminiLiveClient(config=CopilotConfig(gemini_api_key="x", **cfg_kwargs))

    def test_client_shields_a_sensitive_window(self):
        client = self._client()
        self.assertTrue(client._window_is_sensitive("bitwarden", "Vault"))
        self.assertFalse(client._window_is_sensitive("code", "main.py"))

    def test_client_reads_the_allowlist_from_config(self):
        """O cliente precisa ler a allowlist do config — e o título é sensível de propósito.

        Com um título neutro, `_window_is_sensitive` devolveria False mesmo se o cliente
        ignorasse a allowlist por completo, e o teste não provaria nada.
        """
        client = self._client(privacy_shield_allowlist=["libreoffice-writer"])
        self.assertFalse(
            client._window_is_sensitive("libreoffice-writer", "Itaú - Extrato"),
            "A allowlist do config não foi consultada — a janela continua oculta.",
        )
        # E a allowlist não vaza para outras janelas.
        self.assertTrue(client._window_is_sensitive("firefox", "Nubank - Conta"))

    def test_client_reads_the_denylist_from_config(self):
        client = self._client(privacy_shield_denylist=["my-secret-app"])
        self.assertTrue(client._window_is_sensitive("my-secret-app", "Notas"))

    def test_client_survives_a_fixture_without_the_lists(self):
        """Fixtures que montam por `__new__` não passam pelo `__init__` — não pode explodir."""
        from zorin_copilot.ai.live import GeminiLiveClient

        bare = GeminiLiveClient.__new__(GeminiLiveClient)
        self.assertTrue(bare._window_is_sensitive("bitwarden", "Vault"))
        self.assertFalse(bare._window_is_sensitive("code", "main.py"))


class VideoLoopUsesTheGateTest(unittest.TestCase):
    """O laço de vídeo decide **pela porta do gate**, não por substring.

    Mutação que isto mata: trocar `is_sensitive = self._window_is_sensitive(...)` pela
    substring crua dentro de `_video_stream_worker`. Nenhum teste de unidade da decisão
    pega isso — o que muda é a **CHAMADA**, não a função. É a lacuna clássica de fiação.
    """

    def test_video_loop_asks_the_gate_for_the_active_window(self):
        from unittest import mock

        from zorin_copilot.ai.live import GeminiLiveClient
        from zorin_copilot.core.vision import ScreenCaptureService

        client = GeminiLiveClient(config=CopilotConfig(gemini_api_key="x"))
        client._is_video_streaming = True
        client._is_running = True
        client._ws = mock.MagicMock()          # precisa ser truthy para entrar no corpo
        client._loop = mock.MagicMock()
        client._loop.is_running.return_value = True

        client.inspector = mock.MagicMock()

        def active_window():
            client._is_running = False          # encerra o laço após uma volta
            return ("firefox", "Documentação", (0, 0, 100, 100))

        client.inspector.get_active_window_info.side_effect = active_window

        spy = mock.MagicMock(return_value=False)
        client._window_is_sensitive = spy

        with mock.patch.object(ScreenCaptureService, "capture", return_value=(False, None, None)):
            client._video_stream_worker(fps=10.0)

        spy.assert_called_once_with("firefox", "Documentação")


if __name__ == "__main__":
    unittest.main()
