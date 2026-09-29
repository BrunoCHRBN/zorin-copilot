"""Testes do cliente da Google Agenda (`core/google_calendar.py`) e do roteamento
da ferramenta `calendar_event` (Google como fonte de verdade, local como fallback).

Nada aqui vai à rede: o cliente aceita um `service` injetado, e a ferramenta é
exercitada com a função de API trocada por uma dobra. Testa-se a DECISÃO e o
corpo enviado à API — não a API da Google.
"""

from __future__ import annotations

import types
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from zorin_copilot.core import google_calendar as gcal


# --------------------------------------------------------------------------
# Dobra do cliente do Google (cadeia fluente `service.events().insert(...).execute()`)
# --------------------------------------------------------------------------


class _FakeExec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeEvents:
    def __init__(self, calls):
        self.calls = calls

    def insert(self, calendarId, body):
        self.calls["insert"] = {"calendarId": calendarId, "body": body}
        return _FakeExec(
            {
                "id": "evt-1",
                "summary": body.get("summary", ""),
                "start": body.get("start", {}),
                "end": body.get("end", {}),
                "description": body.get("description", ""),
                "location": body.get("location", ""),
                "htmlLink": "https://calendar.google.com/event?eid=evt-1",
                "status": "confirmed",
            }
        )

    def list(self, **kwargs):
        self.calls["list"] = kwargs
        return _FakeExec(
            {
                "items": [
                    {
                        "id": "evt-1",
                        "summary": "Alinhamento",
                        "start": {"dateTime": "2026-09-23T10:00:00-03:00"},
                        "end": {"dateTime": "2026-09-23T11:00:00-03:00"},
                        "htmlLink": "https://calendar.google.com/event?eid=evt-1",
                    }
                ]
            }
        )

    def patch(self, calendarId, eventId, body):
        self.calls["patch"] = {"calendarId": calendarId, "eventId": eventId, "body": body}
        return _FakeExec(
            {
                "id": eventId,
                "summary": body.get("summary", "Antigo"),
                "start": body.get("start", {}),
                "end": body.get("end", {}),
            }
        )

    def delete(self, calendarId, eventId):
        self.calls["delete"] = {"calendarId": calendarId, "eventId": eventId}
        return _FakeExec({})


class _FakeFreebusy:
    def __init__(self, calls):
        self.calls = calls

    def query(self, body):
        self.calls["freebusy"] = body
        return _FakeExec(
            {
                "calendars": {
                    "primary": {
                        "busy": [
                            {
                                "start": "2026-09-23T10:00:00-03:00",
                                "end": "2026-09-23T11:00:00-03:00",
                            }
                        ]
                    }
                }
            }
        )


class FakeService:
    def __init__(self):
        self.calls: dict = {}

    def events(self):
        return _FakeEvents(self.calls)

    def freebusy(self):
        return _FakeFreebusy(self.calls)


class Rfc3339Test(unittest.TestCase):
    def test_naive_datetime_gets_an_offset(self):
        # A API exige offset; um datetime ingênuo precisa ganhar o fuso local.
        text = gcal.to_rfc3339(datetime(2026, 9, 23, 10, 0))
        self.assertRegex(text, r"^2026-09-23T10:00:00[+-]\d{2}:\d{2}$")

    def test_aware_datetime_is_preserved(self):
        aware = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(gcal.to_rfc3339(aware), "2026-09-23T10:00:00+00:00")


class ResolveWindowTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 23, 14, 30)

    def test_today(self):
        start, end = gcal.resolve_window("today", now=self.now)
        self.assertEqual(start, datetime(2026, 9, 23, 0, 0))
        self.assertEqual(end, datetime(2026, 9, 24, 0, 0))

    def test_tomorrow_in_portuguese(self):
        start, end = gcal.resolve_window("amanhã", now=self.now)
        self.assertEqual(start, datetime(2026, 9, 24, 0, 0))
        self.assertEqual(end, datetime(2026, 9, 25, 0, 0))

    def test_week(self):
        start, end = gcal.resolve_window("semana", now=self.now)
        self.assertEqual(start, datetime(2026, 9, 23, 0, 0))
        self.assertEqual(end, datetime(2026, 9, 30, 0, 0))

    def test_unknown_falls_back_to_today(self):
        start, _ = gcal.resolve_window("banana", now=self.now)
        self.assertEqual(start, datetime(2026, 9, 23, 0, 0))


class ResolveStartTest(unittest.TestCase):
    def test_datetime_passthrough(self):
        moment = datetime(2026, 9, 23, 9, 0)
        self.assertIs(gcal.resolve_start(moment), moment)

    def test_natural_language_reuses_calendar_parser(self):
        now = datetime(2026, 9, 23, 8, 0)
        self.assertEqual(gcal.resolve_start("amanhã às 10h", now=now), datetime(2026, 9, 24, 10, 0))


class CreateEventTest(unittest.TestCase):
    def test_builds_body_and_normalizes_response(self):
        svc = FakeService()
        event = gcal.create_event(
            "Reunião",
            "amanhã às 10h",
            duration_minutes=45,
            description="pauta",
            location="sala 2",
            service=svc,
        )
        body = svc.calls["insert"]["body"]
        self.assertEqual(body["summary"], "Reunião")
        self.assertEqual(body["description"], "pauta")
        self.assertEqual(body["location"], "sala 2")
        start = datetime.fromisoformat(body["start"]["dateTime"])
        end = datetime.fromisoformat(body["end"]["dateTime"])
        self.assertEqual((end - start).total_seconds(), 45 * 60)
        # Normalização: o consumidor fala `title`, a API fala `summary`.
        self.assertEqual(event["title"], "Reunião")
        self.assertEqual(event["summary"], "Reunião")
        self.assertEqual(event["id"], "evt-1")

    def test_short_duration_is_clamped(self):
        svc = FakeService()
        gcal.create_event("X", datetime(2026, 9, 23, 10, 0), duration_minutes=1, service=svc)
        body = svc.calls["insert"]["body"]
        start = datetime.fromisoformat(body["start"]["dateTime"])
        end = datetime.fromisoformat(body["end"]["dateTime"])
        self.assertEqual((end - start).total_seconds(), gcal.MIN_DURATION_MINUTES * 60)

    def test_empty_title_raises(self):
        with self.assertRaises(ValueError):
            gcal.create_event("   ", "hoje", service=FakeService())


class ListEventsTest(unittest.TestCase):
    def test_query_params_and_normalization(self):
        svc = FakeService()
        now = datetime(2026, 9, 23, 8, 0)
        events = gcal.list_events("today", max_results=5, service=svc, now=now)
        params = svc.calls["list"]
        self.assertEqual(params["calendarId"], "primary")
        self.assertTrue(params["singleEvents"], "eventos recorrentes precisam ser expandidos")
        self.assertEqual(params["orderBy"], "startTime")
        self.assertEqual(params["maxResults"], 5)
        self.assertTrue(params["timeMin"].startswith("2026-09-23T00:00:00"))
        self.assertEqual(events[0]["title"], "Alinhamento")
        self.assertEqual(events[0]["start"], "2026-09-23T10:00:00-03:00")


class UpdateEventTest(unittest.TestCase):
    def test_only_provided_fields_are_patched(self):
        svc = FakeService()
        gcal.update_event("evt-1", title="Novo", service=svc)
        self.assertEqual(svc.calls["patch"]["body"], {"summary": "Novo"})

    def test_start_also_sets_end(self):
        svc = FakeService()
        gcal.update_event(
            "evt-1", start=datetime(2026, 9, 23, 9, 0), duration_minutes=30, service=svc
        )
        body = svc.calls["patch"]["body"]
        self.assertIn("start", body)
        self.assertIn("end", body)

    def test_no_fields_raises(self):
        with self.assertRaises(ValueError):
            gcal.update_event("evt-1", service=FakeService())


class DeleteEventTest(unittest.TestCase):
    def test_calls_api(self):
        svc = FakeService()
        self.assertTrue(gcal.delete_event("evt-1", service=svc))
        self.assertEqual(svc.calls["delete"]["eventId"], "evt-1")

    def test_empty_id_raises(self):
        with self.assertRaises(ValueError):
            gcal.delete_event("", service=FakeService())


class FreebusyTest(unittest.TestCase):
    def test_flattens_busy_slots_with_calendar_id(self):
        svc = FakeService()
        busy = gcal.freebusy("hoje", "amanhã", service=svc)
        self.assertEqual(len(busy), 1)
        self.assertEqual(busy[0]["calendar"], "primary")
        self.assertIn("start", busy[0])
        self.assertEqual(svc.calls["freebusy"]["items"], [{"id": "primary"}])


class CalendarToolRoutingTest(unittest.TestCase):
    """A ferramenta `calendar_event`: Google quando conectado, local quando não."""

    def _registry(self):
        from zorin_copilot.ai.agent_tools import ToolRegistry

        return ToolRegistry()

    def test_google_path_when_active(self):
        reg = self._registry()
        fake_event = {"id": "g1", "title": "G", "summary": "G", "start": "s", "end": "e"}
        with patch.object(type(reg), "_google_calendar_active", return_value=True):
            with patch.object(type(reg), "_mirror_calendar") as mirror:
                with patch(
                    "zorin_copilot.core.google_calendar.create_event", return_value=fake_event
                ) as mocked:
                    res = reg.call("calendar_event", {"action": "create", "title": "G"})
        self.assertTrue(res["success"])
        self.assertEqual(res["source"], "google")
        mocked.assert_called_once()
        mirror.assert_called_once()

    def test_local_path_when_not_active(self):
        reg = self._registry()
        with patch.object(type(reg), "_google_calendar_active", return_value=False):
            res = reg.call(
                "calendar_event",
                {"action": "create", "title": "Compromisso Local", "datetime_str": "amanhã às 10h"},
            )
        self.assertTrue(res["success"])
        self.assertEqual(res["source"], "local")

    def test_update_without_google_is_an_honest_error(self):
        reg = self._registry()
        with patch.object(type(reg), "_google_calendar_active", return_value=False):
            res = reg.call("calendar_event", {"action": "update", "event_id": "x", "title": "y"})
        self.assertFalse(res["success"])
        self.assertIn("Google", res["error"])


class CalendarRiskTest(unittest.TestCase):
    """Escrita na agenda pede confirmação; leitura não (decisão do Bruno, 2026-09-22)."""

    def setUp(self):
        from zorin_copilot.shell.risk import RiskPolicy

        self.policy = RiskPolicy()

    def test_writes_require_confirmation(self):
        from zorin_copilot.shell.risk import RiskLevel

        for action in ("create", "update", "delete"):
            level, _ = self.policy.classify("calendar_event", {"action": action})
            self.assertEqual(level, RiskLevel.CONFIRM, f"{action} deveria confirmar")

    def test_reads_are_safe(self):
        from zorin_copilot.shell.risk import RiskLevel

        for action in ("list", "freebusy"):
            level, _ = self.policy.classify("calendar_event", {"action": action})
            self.assertEqual(level, RiskLevel.SAFE, f"{action} não deveria confirmar")

    def test_missing_action_defaults_to_confirmation(self):
        from zorin_copilot.shell.risk import RiskLevel

        level, _ = self.policy.classify("calendar_event", {})
        self.assertEqual(level, RiskLevel.CONFIRM)


class GoogleServiceAllowlistTest(unittest.TestCase):
    """`google_enabled_services` é allowlist; lista vazia = só o portão mestre."""

    def _registry(self):
        from zorin_copilot.ai.agent_tools import ToolRegistry

        return ToolRegistry()

    def _active(self, enabled, services, connected=True, service="calendar"):
        reg = self._registry()
        cfg = types.SimpleNamespace(
            google_enabled=enabled, google_enabled_services=list(services)
        )
        with patch("zorin_copilot.core.config.CopilotConfig.load", return_value=cfg):
            with patch("zorin_copilot.core.google_auth.is_connected", return_value=connected):
                return reg._google_service_active(service)

    def test_master_switch_off_disables_everything(self):
        self.assertFalse(self._active(False, [], connected=True))

    def test_allowlist_blocks_unlisted_service(self):
        self.assertTrue(self._active(True, ["calendar"], service="calendar"))
        self.assertFalse(self._active(True, ["calendar"], service="drive"))

    def test_empty_allowlist_allows_any_connected_service(self):
        self.assertTrue(self._active(True, [], service="drive"))

    def test_disconnected_service_is_inactive(self):
        self.assertFalse(self._active(True, [], connected=False))

    def test_check_failure_is_inactive(self):
        reg = self._registry()
        with patch(
            "zorin_copilot.core.config.CopilotConfig.load", side_effect=RuntimeError("boom")
        ):
            self.assertFalse(reg._google_service_active("calendar"))


if __name__ == "__main__":
    unittest.main()
