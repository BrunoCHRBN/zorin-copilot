"""Testes do cliente do Gmail (`core/google_gmail.py`) e do fluxo de envio da
ferramenta `google_gmail`.

Sem rede: o cliente aceita um `service` injetado. O ponto crítico é o ENVIO —
ele exige `reviewed=true` (etapa de revisão forçada em código) e passa pelo
portão de risco (CONFIRM).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from zorin_copilot.core import google_gmail as gmail


class _FakeExec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeMessages:
    def __init__(self, calls):
        self.calls = calls

    def send(self, userId, body):
        self.calls["send"] = {"userId": userId, "body": body}
        return _FakeExec({"id": "m1", "threadId": "t1", "labelIds": ["SENT"]})

    def list(self, **kwargs):
        self.calls["list"] = kwargs
        return _FakeExec({"messages": [{"id": "m1", "threadId": "t1"}]})

    def get(self, userId, id, format=None):
        self.calls["get"] = {"userId": userId, "id": id, "format": format}
        return _FakeExec(
            {
                "id": id,
                "threadId": "t1",
                "snippet": "trecho da mensagem",
                "labelIds": ["INBOX", "UNREAD"],
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Fatura do cartão"},
                        {"name": "From", "value": "banco@exemplo.com"},
                        {"name": "To", "value": "eu@exemplo.com"},
                        {"name": "Date", "value": "Mon, 22 Sep 2026 10:00:00 -0300"},
                    ]
                },
            }
        )

    def modify(self, userId, id, body):
        self.calls["modify"] = {"userId": userId, "id": id, "body": body}
        return _FakeExec({"id": id, "labelIds": ["INBOX"]})


class _FakeDrafts:
    def __init__(self, calls):
        self.calls = calls

    def create(self, userId, body):
        self.calls["draft_create"] = {"userId": userId, "body": body}
        return _FakeExec({"id": "d1", "message": {"id": "m9"}})


class _FakeLabels:
    def __init__(self, calls):
        self.calls = calls

    def list(self, userId):
        self.calls["labels_list"] = {"userId": userId}
        return _FakeExec({"labels": [{"id": "INBOX", "name": "Caixa de entrada", "type": "system"}]})


class FakeGmailService:
    def __init__(self):
        self.calls: dict = {}

    def users(self):
        return self

    def messages(self):
        return _FakeMessages(self.calls)

    def drafts(self):
        return _FakeDrafts(self.calls)

    def labels(self):
        return _FakeLabels(self.calls)


class BuildMessageTest(unittest.TestCase):
    def test_headers_present(self):
        raw = gmail.build_message("dest@exemplo.com", "Assunto", "Corpo do e-mail", cc="copia@exemplo.com")
        text = gmail.decode_raw(raw)
        self.assertIn("To: dest@exemplo.com", text)
        self.assertIn("Cc: copia@exemplo.com", text)
        self.assertIn("Subject: Assunto", text)
        self.assertIn("Corpo do e-mail", text)

    def test_empty_recipient_raises(self):
        with self.assertRaises(ValueError):
            gmail.build_message("   ", "Assunto", "Corpo")

    def test_accented_body_roundtrips(self):
        raw = gmail.build_message("a@b.com", "Ação", "Olá, coração")
        self.assertIsInstance(gmail.decode_raw(raw), str)


class SendMessageTest(unittest.TestCase):
    def test_send_calls_api_with_raw(self):
        svc = FakeGmailService()
        result = gmail.send_message("dest@exemplo.com", "Assunto", "Corpo", service=svc)
        self.assertEqual(svc.calls["send"]["userId"], "me")
        self.assertIn("raw", svc.calls["send"]["body"])
        self.assertEqual(result["id"], "m1")
        self.assertIn("SENT", result["labels"])

    def test_draft_does_not_send(self):
        svc = FakeGmailService()
        result = gmail.create_draft("dest@exemplo.com", "Assunto", "Corpo", service=svc)
        self.assertIn("draft_create", svc.calls)
        self.assertNotIn("send", svc.calls)
        self.assertEqual(result["message_id"], "m9")


class ListAndGetTest(unittest.TestCase):
    def test_list_params(self):
        svc = FakeGmailService()
        messages = gmail.list_messages(query="is:unread", max_results=5, service=svc)
        self.assertEqual(svc.calls["list"]["q"], "is:unread")
        self.assertEqual(svc.calls["list"]["maxResults"], 5)
        self.assertEqual(messages[0]["id"], "m1")

    def test_list_without_query_omits_q(self):
        svc = FakeGmailService()
        gmail.list_messages(service=svc)
        self.assertNotIn("q", svc.calls["list"])

    def test_get_extracts_headers(self):
        svc = FakeGmailService()
        info = gmail.get_message("m1", service=svc)
        self.assertEqual(info["subject"], "Fatura do cartão")
        self.assertEqual(info["from"], "banco@exemplo.com")
        self.assertIn("UNREAD", info["labels"])

    def test_get_empty_id_raises(self):
        with self.assertRaises(ValueError):
            gmail.get_message("", service=FakeGmailService())


class ModifyLabelsTest(unittest.TestCase):
    def test_mark_read_removes_unread(self):
        svc = FakeGmailService()
        gmail.mark_read("m1", service=svc)
        self.assertEqual(svc.calls["modify"]["body"], {"removeLabelIds": ["UNREAD"]})

    def test_mark_unread_adds_unread(self):
        svc = FakeGmailService()
        gmail.mark_unread("m1", service=svc)
        self.assertEqual(svc.calls["modify"]["body"], {"addLabelIds": ["UNREAD"]})

    def test_nothing_to_modify_raises(self):
        with self.assertRaises(ValueError):
            gmail.modify_labels("m1", service=FakeGmailService())

    def test_list_labels(self):
        svc = FakeGmailService()
        labels = gmail.list_labels(service=svc)
        self.assertEqual(labels[0]["name"], "Caixa de entrada")


class GmailToolTest(unittest.TestCase):
    def _registry(self):
        from zorin_copilot.ai.agent_tools import ToolRegistry

        return ToolRegistry()

    def test_review_works_without_gmail_connection(self):
        reg = self._registry()
        with patch(
            "zorin_copilot.core.email_review.review_draft",
            return_value=type(
                "R",
                (),
                {
                    "reviewed": True,
                    "subject": "Reunião",
                    "body": "Olá.",
                    "changes": ["acentos"],
                    "issues": [],
                    "note": "",
                },
            )(),
        ):
            res = reg.call("google_gmail", {"action": "review", "subject": "Reuniao", "body": "Ola.", "context": "formal"})
        self.assertTrue(res["success"])
        self.assertTrue(res["reviewed"])

    def test_send_without_reviewed_is_refused(self):
        reg = self._registry()
        with patch("zorin_copilot.core.google_auth.google_libs_available", return_value=True):
            with patch.object(type(reg), "_google_service_active", return_value=True):
                res = reg.call("google_gmail", {"action": "send", "to": "a@b.com", "subject": "s", "body": "b"})
        self.assertFalse(res["success"])
        self.assertIn("reviewed", res["error"])

    def test_send_with_reviewed_calls_api(self):
        reg = self._registry()
        with patch("zorin_copilot.core.google_auth.google_libs_available", return_value=True):
            with patch.object(type(reg), "_google_service_active", return_value=True):
                with patch(
                    "zorin_copilot.core.google_gmail.send_message",
                    return_value={"id": "m1", "thread_id": "t1", "labels": ["SENT"]},
                ) as mocked:
                    res = reg.call(
                        "google_gmail",
                        {"action": "send", "to": "a@b.com", "subject": "s", "body": "b", "reviewed": True},
                    )
        self.assertTrue(res["success"])
        mocked.assert_called_once()

    def test_not_connected_is_honest(self):
        reg = self._registry()
        with patch("zorin_copilot.core.google_auth.google_libs_available", return_value=True):
            with patch.object(type(reg), "_google_service_active", return_value=False):
                res = reg.call("google_gmail", {"action": "list"})
        self.assertFalse(res["success"])
        self.assertIn("conectado", res["error"])


class GmailRiskTest(unittest.TestCase):
    def setUp(self):
        from zorin_copilot.shell.risk import RiskPolicy

        self.policy = RiskPolicy()

    def test_mutations_require_confirmation(self):
        from zorin_copilot.shell.risk import RiskLevel

        for action in ("send", "draft", "mark_read", "mark_unread"):
            level, _ = self.policy.classify("google_gmail", {"action": action})
            self.assertEqual(level, RiskLevel.CONFIRM, f"{action} deveria confirmar")

    def test_reads_and_review_are_safe(self):
        from zorin_copilot.shell.risk import RiskLevel

        for action in ("list", "get", "labels", "review"):
            level, _ = self.policy.classify("google_gmail", {"action": action})
            self.assertEqual(level, RiskLevel.SAFE, f"{action} não deveria confirmar")


if __name__ == "__main__":
    unittest.main()
