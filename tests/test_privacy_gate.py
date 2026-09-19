"""Testes unitários para o Zero-Knowledge Privacy Gatekeeper."""

from __future__ import annotations

import unittest
from zorin_copilot.core.privacy_gate import PrivacyGatekeeper, PrivacyCheckResult


class TestPrivacyGatekeeper(unittest.TestCase):
    def test_block_sensitive_apps(self):
        blocked_apps = [
            "Bitwarden",
            "bitwarden-desktop",
            "1Password",
            "keepassxc",
            "org.keepassxc.KeePassXC",
            "polkit-gnome-authentication-agent-1",
            "pinentry-gtk-2",
        ]
        for app in blocked_apps:
            result = PrivacyGatekeeper.should_ingest_window(app_name=app, window_title="Main Window")
            self.assertFalse(result.allowed, f"Deveria ter bloqueado o app: {app}")
            self.assertTrue(result.is_sensitive)

    def test_block_sensitive_window_titles(self):
        blocked_titles = [
            "Firefox - Navegação anônima",
            "Google Chrome - Incognito",
            "Banco do Brasil - Extrato",
            "Nubank - Conta",
            "KeePassXC - Senha Mestra",
            "Microsoft Edge - InPrivate",
        ]
        for title in blocked_titles:
            result = PrivacyGatekeeper.should_ingest_window(app_name="firefox", window_title=title)
            self.assertFalse(result.allowed, f"Deveria ter bloqueado o título: {title}")
            self.assertTrue(result.is_sensitive)

    def test_allow_safe_apps_and_windows(self):
        safe_cases = [
            ("code", "main.py - zorin-copilot - Visual Studio Code"),
            ("firefox", "Documentação do Hyprland - Mozilla Firefox"),
            ("kitty", "bruno@PC-do-BRUNO: ~/zorin-copilot"),
            ("libreoffice-writer", "TCC_Gestao_Comercial.docx - LibreOffice Writer"),
        ]
        for app, title in safe_cases:
            result = PrivacyGatekeeper.should_ingest_window(app_name=app, window_title=title)
            self.assertTrue(result.allowed, f"Não deveria ter bloqueado: {app} / {title}")
            self.assertFalse(result.is_sensitive)

    def test_sanitize_secrets(self):
        # 1. Chave privada
        private_key = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA0Y3...\n"
            "-----END RSA PRIVATE KEY-----"
        )
        self.assertEqual(PrivacyGatekeeper.sanitize_text(private_key), "<REDACTED_PRIVATE_KEY>")

        # 2. Chave de API OpenAI / Gemini
        text_with_keys = "API key: sk_live_abcdef123456789012345678 e AIzaSyD9xYZ1234567890123456789012345678"
        sanitized = PrivacyGatekeeper.sanitize_text(text_with_keys)
        self.assertNotIn("sk_live_", sanitized)
        self.assertNotIn("AIzaSyD", sanitized)
        self.assertIn("<REDACTED_API_KEY>", sanitized)
        self.assertIn("<REDACTED_GEMINI_KEY>", sanitized)

        # 3. Cartão de crédito
        card_text = "Número do cartão: 4532 1234 5678 9012 para faturamento"
        sanitized_card = PrivacyGatekeeper.sanitize_text(card_text)
        self.assertNotIn("4532 1234 5678 9012", sanitized_card)
        self.assertIn("<REDACTED_CARD_NUMBER>", sanitized_card)

    def test_filter_event_stream(self):
        raw_events = [
            {"app": "code", "title": "engine.py", "text": "def test(): pass"},
            {"app": "bitwarden", "title": "Bitwarden Vault", "text": "my_master_password"},
            {"app": "firefox", "title": "Nubank", "text": "saldo 500"},
            {"app": "terminal", "title": "bash", "text": "export TOKEN=sk_live_123456789012345678901234"},
        ]
        filtered = PrivacyGatekeeper.filter_event_stream(raw_events)
        self.assertEqual(len(filtered), 2)
        self.assertEqual(filtered[0]["app"], "code")
        self.assertEqual(filtered[1]["app"], "terminal")
        self.assertIn("<REDACTED_API_KEY>", filtered[1]["text"])


if __name__ == "__main__":
    unittest.main()
