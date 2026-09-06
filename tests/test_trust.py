"""Testes unitários para o gerenciamento de confiança e sanitização de PII."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.core.trust import DocumentTrustManager, PIISanitizer, TrustLevel


class PIISanitizerTest(unittest.TestCase):
    def test_mask_cpf_and_cnpj(self):
        text = "O cliente com CPF 123.456.789-00 e empresa CNPJ 12.345.678/0001-90 assinou o contrato."
        masked = PIISanitizer.mask_text(text)
        self.assertNotIn("123.456.789-00", masked)
        self.assertNotIn("12.345.678/0001-90", masked)
        self.assertIn("[CPF_PROTEGIDO]", masked)
        self.assertIn("[CNPJ_PROTEGIDO]", masked)

    def test_mask_credit_card(self):
        text = "Pagamento aprovado no cartão 4111 2222 3333 4444 para a compra."
        masked = PIISanitizer.mask_text(text)
        self.assertNotIn("4111 2222 3333 4444", masked)
        self.assertIn("[CARTÃO_PROTEGIDO]", masked)

    def test_mask_api_keys_and_private_keys(self):
        text = (
            "Chave Gemini: AIzaSyD3x4mpleK3y_ForT3st1ngPurp0s3s123456\n"
            "Chave OpenAI: sk-proj1234567890abcdef1234567890abcdef\n"
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----"
        )
        masked = PIISanitizer.mask_text(text)
        self.assertNotIn("AIzaSyD3x4mpleK3y_ForT3st1ngPurp0s3s123456", masked)
        self.assertNotIn("sk-proj1234567890abcdef1234567890abcdef", masked)
        self.assertNotIn("MIIEowIBAAKCAQEA", masked)
        self.assertIn("[CHAVE_API_PROTEGIDA]", masked)
        self.assertIn("[CHAVE_PRIVADA_PROTEGIDA]", masked)


class DocumentTrustManagerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.docs = self.root / "Documentos"
        self.downloads = self.root / "Downloads"
        self.docs.mkdir(parents=True, exist_ok=True)
        self.downloads.mkdir(parents=True, exist_ok=True)

        self.cfg = CopilotConfig(
            trusted_directories=[str(self.docs)],
            quarantine_directories=[str(self.downloads)],
            ignored_patterns=["*.kdbx", "*.key", "*IRPF*", "*senha*"],
            mask_pii=True,
            max_file_size_mb=10,
        )
        self.mgr = DocumentTrustManager(
            config=self.cfg,
            trusted_dirs=[self.docs],
            quarantine_dirs=[self.downloads],
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_trusted_directory_evaluation(self):
        safe_file = self.docs / "relatorio_seguro.docx"
        safe_file.write_text("Conteúdo corporativo legítimo.", encoding="utf-8")

        level, reason = self.mgr.evaluate_file(safe_file)
        self.assertEqual(level, TrustLevel.TRUSTED)
        self.assertTrue(self.mgr.is_indexable(safe_file)[0])

    def test_quarantine_directory_evaluation(self):
        downloaded = self.downloads / "boleto_desconhecido.pdf"
        downloaded.write_text("Arquivo baixado via internet.", encoding="utf-8")

        level, reason = self.mgr.evaluate_file(downloaded)
        self.assertEqual(level, TrustLevel.CAUTION)
        self.assertTrue(self.mgr.is_indexable(downloaded)[0])
        self.assertIn("quarentena", reason.lower())

    def test_blocked_ignored_patterns(self):
        irpf = self.docs / "Declaracao_IRPF_2025.pdf"
        irpf.write_text("Dados fiscais sigilosos.", encoding="utf-8")

        level, reason = self.mgr.evaluate_file(irpf)
        self.assertEqual(level, TrustLevel.BLOCKED)
        self.assertFalse(self.mgr.is_indexable(irpf)[0])

        kdbx = self.docs / "senhas_mestre.kdbx"
        kdbx.write_text("Banco de senhas criptografado.", encoding="utf-8")
        level2, _ = self.mgr.evaluate_file(kdbx)
        self.assertEqual(level2, TrustLevel.BLOCKED)

    def test_copilotignore_support(self):
        ignore_file = self.docs / ".copilotignore"
        ignore_file.write_text("*.privado\nsegredo_*.txt\n", encoding="utf-8")

        secret_file = self.docs / "segredo_projeto.txt"
        secret_file.write_text("Projeto confidencial X.", encoding="utf-8")

        level, reason = self.mgr.evaluate_file(secret_file)
        self.assertEqual(level, TrustLevel.BLOCKED)
        self.assertIn(".copilotignore", reason)

    def test_sanitize_for_rag_context_caution(self):
        snippet = "O contrato do cliente CPF 123.456.789-00 vence amanhã."
        wrapped = self.mgr.sanitize_for_rag_context(
            chunk_text=snippet,
            trust_level=TrustLevel.CAUTION,
            file_name="termo.pdf",
            page_number=2,
        )
        self.assertIn("<untrusted_document_context", wrapped)
        self.assertIn("Aviso de Segurança", wrapped)
        self.assertIn("[CPF_PROTEGIDO]", wrapped)
        self.assertNotIn("123.456.789-00", wrapped)


if __name__ == "__main__":
    unittest.main()
