"""Testes da revisão de e-mail (`core/email_review.py`).

O ponto crítico aqui é a **honestidade**: quando não há provedor (ou ele falha), o
resultado precisa dizer `reviewed=False` — nunca devolver o texto como se tivesse
sido corrigido. Um teste que só verifica o caminho feliz deixaria passar
exatamente a falha mais cara deste módulo.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from zorin_copilot.core import email_review as review


class FakeProvider:
    """Dobra do provedor: `is_configured()` + `generate_with_tool(prompt, schema)`."""

    def __init__(self, response, configured=True, raises=None):
        self.response = response
        self._configured = configured
        self.raises = raises
        self.prompts: list[str] = []

    def is_configured(self):
        return self._configured

    def generate_with_tool(self, prompt, schema, system_prompt=None):
        self.prompts.append(prompt)
        if self.raises:
            raise self.raises
        return self.response


class LintDraftTest(unittest.TestCase):
    def test_clean_draft_has_no_issues(self):
        self.assertEqual(review.lint_draft("Reunião amanhã", "Olá,\n\nSegue o resumo."), [])

    def test_empty_subject(self):
        self.assertIn("Assunto vazio.", review.lint_draft("   ", "corpo"))

    def test_empty_body(self):
        self.assertIn("Corpo vazio.", review.lint_draft("assunto", "  \n "))

    def test_double_spaces(self):
        self.assertIn("Espaços duplicados no corpo.", review.lint_draft("a", "Olá,  tudo bem?"))

    def test_space_before_punctuation(self):
        self.assertIn(
            "Espaço antes de pontuação (ex.: 'texto ,' ).", review.lint_draft("a", "Olá , tudo bem")
        )

    def test_trailing_whitespace(self):
        issues = review.lint_draft("a", "primeira linha   \nsegunda")
        self.assertTrue(any("espaço em branco no fim" in i for i in issues))

    def test_long_subject(self):
        issues = review.lint_draft("x" * 90, "corpo")
        self.assertTrue(any("acima de" in i for i in issues))


class ReviewDraftTest(unittest.TestCase):
    def test_applies_provider_corrections(self):
        prov = FakeProvider(
            {
                "subject": "Reunião amanhã",
                "body": "Olá, tudo bem?",
                "changes": ["Corrigi a acentuação de 'Reuniao' e 'Ola'."],
            }
        )
        result = review.review_draft("Reuniao amanha", "Ola, tudo bem?", context="formal", provider=prov)
        self.assertTrue(result.reviewed)
        self.assertEqual(result.subject, "Reunião amanhã")
        self.assertEqual(result.body, "Olá, tudo bem?")
        self.assertEqual(len(result.changes), 1)
        self.assertTrue(result.changed)
        self.assertIn("formal", prov.prompts[0], "o contexto do usuário precisa ir no prompt")

    def test_unconfigured_provider_is_honest(self):
        prov = FakeProvider({}, configured=False)
        result = review.review_draft("assunto", "corpo", provider=prov)
        self.assertFalse(result.reviewed)
        self.assertEqual(result.subject, "assunto")
        self.assertEqual(result.body, "corpo")
        self.assertIn("NÃO foi revisado", result.note)

    def test_provider_error_is_honest(self):
        prov = FakeProvider(None, raises=RuntimeError("boom"))
        result = review.review_draft("assunto", "corpo", provider=prov)
        self.assertFalse(result.reviewed)
        self.assertEqual(result.body, "corpo")
        self.assertIn("NÃO foi revisado", result.note)

    def test_non_dict_response_is_honest(self):
        prov = FakeProvider(["não é dict"])
        result = review.review_draft("assunto", "corpo", provider=prov)
        self.assertFalse(result.reviewed)
        self.assertIn("formato inesperado", result.note)

    def test_no_provider_at_all_is_honest(self):
        with patch.object(review, "_default_provider", return_value=None):
            result = review.review_draft("assunto", "corpo")
        self.assertFalse(result.reviewed)
        self.assertIn("NÃO foi revisado", result.note)

    def test_deterministic_issues_are_reported_alongside_review(self):
        prov = FakeProvider({"subject": "", "body": "", "changes": []})
        result = review.review_draft("", "", provider=prov)
        self.assertIn("Assunto vazio.", result.issues)
        self.assertIn("Corpo vazio.", result.issues)

    def test_changed_is_false_when_no_changes(self):
        prov = FakeProvider({"subject": "ok", "body": "ok", "changes": []})
        result = review.review_draft("ok", "ok", provider=prov)
        self.assertTrue(result.reviewed)
        self.assertFalse(result.changed)


if __name__ == "__main__":
    unittest.main()
