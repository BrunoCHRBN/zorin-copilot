# Decisão de design: o **Google Calendar é a fonte de verdade**; o CalendarManager
# local (SQLite+ICS) vira cache. Por isso toda escrita vai à API e só depois
# espelha no cache — o inverso deixaria o usuário com um evento "criado" que não
# existe na agenda real, que é o defeito silencioso mais caro possível aqui.
#
# O parsing de data em linguagem natural é **importado** de `core/calendar.parse_when`
# (não recriado): duas cópias divergiriam e o mesmo "amanhã às 15h" agendaria dias
# diferentes conforme o caminho. É a mesma regra do "importe a regra, não
# reimplemente" que o projeto já aplica em outros pontos.
#
# As bibliotecas do Google NÃO são importadas aqui: quem as resolve é
# `google_auth.get_service()`, que degrada com mensagem acionável quando o extra
# `[google]` falta. Este módulo é testável com um `service` injetado.

"""Cliente da Google Agenda (Calendar API v3)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from . import google_auth
from .calendar import parse_when

logger = logging.getLogger(__name__)

DEFAULT_CALENDAR_ID = "primary"
DEFAULT_DURATION_MINUTES = 60

#: Duração mínima. Um evento de 0 minuto é rejeitado pela API e não faz sentido
#: para o usuário; 15 min é o menor bloco que ainda é "um compromisso".
MIN_DURATION_MINUTES = 15


def local_timezone():
    """Fuso local do sistema.

    O evento é criado no fuso do **usuário**, não em UTC: "às 15h" precisa
    significar 15h na parede dele, senão o compromisso aparece deslocado.
    """
    return datetime.now().astimezone().tzinfo or timezone.utc


def to_rfc3339(moment: datetime) -> str:
    """Formato que a Calendar API exige: ISO-8601 **com** offset."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=local_timezone())
    return moment.isoformat()


def resolve_start(value: Any, now: datetime | None = None) -> datetime:
    """Aceita `datetime` ou linguagem natural ('amanhã às 10h') e devolve `datetime`."""
    if isinstance(value, datetime):
        return value
    return parse_when(str(value or ""), now=now)


def resolve_window(day_filter: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Janela `[time_min, time_max)` para listagem.

    `hoje`/`today` = o dia inteiro; `amanhã`/`tomorrow` = o dia seguinte;
    `semana`/`week` = os próximos 7 dias. Filtro desconhecido cai em **hoje** —
    o caso visível (o usuário vê o que pediu por padrão), não um erro.
    """
    now = now or datetime.now()
    day = (day_filter or "today").strip().lower()
    if day in ("tomorrow", "amanhã", "amanha"):
        start = now + timedelta(days=1)
        end = start + timedelta(days=1)
    elif day in ("week", "semana", "próximos 7 dias", "proximos 7 dias", "7d"):
        start = now
        end = now + timedelta(days=7)
    else:
        start = now
        end = now + timedelta(days=1)
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = end.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, end


def _service(service: Any = None) -> Any:
    return service if service is not None else google_auth.get_service("calendar")


def _event_to_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """Normaliza um evento da API para o formato interno (o mesmo do cache).

    Devolve `title` **e** `summary` de propósito: o cache e a UI falam `title`,
    a API fala `summary`, e ter os dois evita um `.get()` divergente em cada
    consumidor.
    """
    start = raw.get("start") or {}
    end = raw.get("end") or {}
    return {
        "id": raw.get("id", ""),
        "title": raw.get("summary", ""),
        "summary": raw.get("summary", ""),
        "start": start.get("dateTime") or start.get("date") or "",
        "end": end.get("dateTime") or end.get("date") or "",
        "description": raw.get("description", ""),
        "location": raw.get("location", ""),
        "html_link": raw.get("htmlLink", ""),
        "status": raw.get("status", ""),
    }


def create_event(
    title: str,
    start: Any,
    duration_minutes: int = DEFAULT_DURATION_MINUTES,
    description: str = "",
    location: str = "",
    calendar_id: str = DEFAULT_CALENDAR_ID,
    service: Any = None,
) -> dict[str, Any]:
    """Cria um evento e devolve o normalizado. Título vazio é erro de uso."""
    title = str(title or "").strip()
    if not title:
        raise ValueError("Título do compromisso é obrigatório.")

    start_dt = resolve_start(start)
    duration = max(MIN_DURATION_MINUTES, int(duration_minutes or DEFAULT_DURATION_MINUTES))
    end_dt = start_dt + timedelta(minutes=duration)

    body: dict[str, Any] = {
        "summary": title,
        "start": {"dateTime": to_rfc3339(start_dt)},
        "end": {"dateTime": to_rfc3339(end_dt)},
    }
    if description:
        body["description"] = str(description)
    if location:
        body["location"] = str(location)

    raw = _service(service).events().insert(calendarId=calendar_id, body=body).execute()
    return _event_to_dict(raw)


def list_events(
    day_filter: str = "today",
    max_results: int = 25,
    calendar_id: str = DEFAULT_CALENDAR_ID,
    service: Any = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Lista eventos na janela pedida, em ordem cronológica."""
    start, end = resolve_window(day_filter, now=now)
    result = (
        _service(service)
        .events()
        .list(
            calendarId=calendar_id,
            timeMin=to_rfc3339(start),
            timeMax=to_rfc3339(end),
            singleEvents=True,
            orderBy="startTime",
            maxResults=int(max_results or 25),
        )
        .execute()
    )
    return [_event_to_dict(item) for item in result.get("items", [])]


def update_event(
    event_id: str,
    *,
    title: str | None = None,
    start: Any = None,
    duration_minutes: int | None = None,
    description: str | None = None,
    location: str | None = None,
    calendar_id: str = DEFAULT_CALENDAR_ID,
    service: Any = None,
) -> dict[str, Any]:
    """Altera só o que foi passado (`patch`, não `update`).

    `None` significa "não mexe" — é a diferença que impede um campo vazio de
    apagar a descrição existente.
    """
    svc = _service(service)
    patch: dict[str, Any] = {}
    if title is not None:
        patch["summary"] = str(title)
    if description is not None:
        patch["description"] = str(description)
    if location is not None:
        patch["location"] = str(location)
    if start is not None:
        start_dt = resolve_start(start)
        duration = max(
            MIN_DURATION_MINUTES, int(duration_minutes or DEFAULT_DURATION_MINUTES)
        )
        patch["start"] = {"dateTime": to_rfc3339(start_dt)}
        patch["end"] = {"dateTime": to_rfc3339(start_dt + timedelta(minutes=duration))}

    if not patch:
        raise ValueError("Nada a atualizar: informe ao menos um campo.")

    raw = (
        svc.events()
        .patch(calendarId=calendar_id, eventId=event_id, body=patch)
        .execute()
    )
    return _event_to_dict(raw)


def delete_event(
    event_id: str,
    calendar_id: str = DEFAULT_CALENDAR_ID,
    service: Any = None,
) -> bool:
    """Remove um evento. Levanta se a API recusar — não finge sucesso."""
    event_id = str(event_id or "").strip()
    if not event_id:
        raise ValueError("ID do evento é obrigatório.")
    _service(service).events().delete(calendarId=calendar_id, eventId=event_id).execute()
    return True


def freebusy(
    time_min: Any,
    time_max: Any,
    calendar_ids: list[str] | None = None,
    service: Any = None,
) -> list[dict[str, Any]]:
    """Intervalos ocupados, achatados numa lista.

    A API devolve por calendário (`calendars[<id>].busy[]`); aqui vira uma lista
    plana com o calendário em cada item — mais fácil de raciocinar e de exibir.
    """
    start = resolve_start(time_min)
    end = resolve_start(time_max)
    ids = list(calendar_ids or [DEFAULT_CALENDAR_ID])
    body = {
        "timeMin": to_rfc3339(start),
        "timeMax": to_rfc3339(end),
        "items": [{"id": cal_id} for cal_id in ids],
    }
    result = _service(service).freebusy().query(body=body).execute()

    busy: list[dict[str, Any]] = []
    for cal_id, data in (result.get("calendars") or {}).items():
        for slot in (data or {}).get("busy", []) or []:
            busy.append(
                {
                    "calendar": cal_id,
                    "start": slot.get("start", ""),
                    "end": slot.get("end", ""),
                }
            )
    return busy
