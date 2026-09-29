# Decisão de design: gerenciador de calendário local com persistência SQLite e geração de arquivos .ics padrão (iCalendar).
# Compatível nativamente com o GNOME Calendar do Zorin OS, Evolution e Thunderbird via gio open evento.ics,
# permitindo consultas rápidas em linguagem natural por voz e agendamentos diretos.

"""Gerenciador de calendário e compromissos para o Zorin Copilot."""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .memory import MemoryManager

logger = logging.getLogger(__name__)

#: Horário padrão quando a frase não traz hora ("amanhã" ⇒ 10:00). É o default
#: histórico; mantido para não mudar o que o usuário já conhece.
DEFAULT_EVENT_HOUR = 10


def parse_when(date_str: str, now: datetime | None = None) -> datetime:
    """Converte linguagem natural em data/hora. PURO — sem banco, sem rede.

    Suporta "hoje", "amanhã", "depois de amanhã", horários ("15:30", "15h") e
    ISO. Vive no **nível do módulo** (não só como método) porque o cliente da
    Google Agenda reusa esta MESMA regra: duas cópias divergiriam em silêncio e
    o mesmo "amanhã às 15h" agendaria dias diferentes conforme o caminho.

    Uma correção sobre a versão anterior, com defeito **observável**:
    - **"depois de amanhã" era inalcançável.** O `elif` vinha depois de um
      `if "amanhã" in ...`, e "depois de amanhã" contém "amanhã" — então a frase
      dava +1 dia em vez de +2. Agora a checagem mais específica vem primeiro.

    Nota de honestidade: eu **supus** que o ISO com "T" maiúsculo também estava
    quebrado (a string é baixada para minúsculas). Medido, é falso — o `strptime`
    do Python é *case-insensitive* para literais, então `%Y-%m-%dT%H:%M` casa com
    "2026-09-23t15:30". Não "corrigi" o que não estava quebrado; o `strptime`
    segue rodando sobre `clean`, como antes.
    """
    clean = (date_str or "").strip().lower()
    now = now or datetime.now()

    # Extrai horário se presente (ex: 15:30 ou 15h)
    hour = DEFAULT_EVENT_HOUR
    minute = 0
    time_match = re.search(r"(\d{1,2})[:h](\d{2})?", clean)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2)) if time_match.group(2) else 0

    target_date = now
    if "depois de amanhã" in clean or "depois de amanha" in clean:
        target_date = now + timedelta(days=2)
    elif "amanhã" in clean or "amanha" in clean:
        target_date = now + timedelta(days=1)
    else:
        # Tenta parsing ISO/formatado. `clean` serve: o `strptime` é
        # case-insensitive para literais, então o "T" do ISO casa mesmo em baixa.
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(clean, fmt)
                if ":" in clean:
                    return parsed
                target_date = parsed
                break
            except ValueError:
                pass

    return target_date.replace(hour=hour, minute=minute, second=0, microsecond=0)


class CalendarManager:
    """Gerencia eventos de calendário e compromissos no desktop Zorin OS."""

    def __init__(self, memory: MemoryManager | None = None):
        self.memory = memory or MemoryManager()
        self.db_path = self.memory.db_path
        self._init_calendar_db()

        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        self.ics_dir = Path(base) / "zorin-copilot" / "calendar"
        self.ics_dir.mkdir(parents=True, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_calendar_db(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS calendar_events (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    start_datetime TEXT NOT NULL,
                    end_datetime TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    ics_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_calendar_start ON calendar_events (start_datetime ASC)
                """
            )
            conn.commit()

    def parse_datetime(self, date_str: str) -> datetime:
        """Compatibilidade: a regra mora em `parse_when` (módulo), fonte única."""
        return parse_when(date_str)

    def create_event(
        self,
        title: str,
        start_time_str: str,
        duration_minutes: int = 60,
        description: str = "",
        location: str = "",
        open_in_calendar: bool = True,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Cria um compromisso no calendário, gera o arquivo .ics e opcionalmente abre no GNOME Calendar."""
        if not title.strip():
            return False, "Título do compromisso não informado.", {}

        try:
            start_dt = self.parse_datetime(start_time_str)
        except Exception:
            start_dt = datetime.now() + timedelta(hours=1)

        end_dt = start_dt + timedelta(minutes=max(15, duration_minutes))
        event_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()

        # Gera arquivo .ics iCalendar
        ics_filename = f"evento_{event_id}.ics"
        ics_file = self.ics_dir / ics_filename
        ics_content = self._generate_ics(
            event_id=event_id,
            title=title,
            start_dt=start_dt,
            end_dt=end_dt,
            description=description,
            location=location,
        )
        ics_file.write_text(ics_content, encoding="utf-8")

        # Salva no banco local
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO calendar_events (id, title, start_datetime, end_datetime, description, location, ics_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    title.strip(),
                    start_dt.isoformat(),
                    end_dt.isoformat(),
                    description.strip(),
                    location.strip(),
                    str(ics_file),
                    now,
                ),
            )
            conn.commit()

        # Abre no GNOME Calendar se solicitado
        if open_in_calendar:
            self._open_ics(str(ics_file))

        msg = (
            f"📅 Compromisso '{title}' agendado para "
            f"{start_dt.strftime('%d/%m às %H:%M')} (Duração: {duration_minutes} min)."
        )
        event_data = {
            "id": event_id,
            "title": title,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "ics_path": str(ics_file),
        }
        return True, msg, event_data

    def list_events(self, day_filter: str = "today") -> list[dict[str, Any]]:
        """Lista compromissos de hoje ou próximos."""
        now = datetime.now()
        start_limit = now.replace(hour=0, minute=0, second=0).isoformat()
        end_limit = (now + timedelta(days=7)).replace(hour=23, minute=59, second=59).isoformat()

        if "amanhã" in day_filter.lower():
            start_limit = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0).isoformat()
            end_limit = (now + timedelta(days=1)).replace(hour=23, minute=59, second=59).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM calendar_events
                WHERE start_datetime >= ? AND start_datetime <= ?
                ORDER BY start_datetime ASC
                """,
                (start_limit, end_limit),
            )
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def delete_event(self, event_id: str) -> bool:
        """Remove um evento pelo ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))
            conn.commit()
            return cursor.rowcount > 0

    def mirror_events(self, events: list[dict[str, Any]]) -> int:
        """Espelha eventos vindos da **fonte de verdade** (Google) no cache local.

        O Google é a autoridade; esta cópia existe para consulta offline e para
        não perder o histórico se a rede cair — nunca para decidir. `INSERT OR
        REPLACE` pela chave `id`: rodar duas vezes não duplica.

        Aceita tanto os nomes da API (`summary`) quanto os internos (`title`),
        porque o normalizador do cliente devolve os dois. Evento sem id ou sem
        título/início é **pulado** (não é erro): um registro parcial no cache é
        pior que ausência — o usuário veria um compromisso sem nome.
        """
        gravados = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for event in events:
                event_id = str(event.get("id") or "").strip()
                title = str(event.get("title") or event.get("summary") or "").strip()
                start = str(event.get("start") or "").strip()
                if not event_id or not title or not start:
                    continue
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO calendar_events
                        (id, title, start_datetime, end_datetime, description, location, ics_path, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        title,
                        start,
                        str(event.get("end") or "").strip(),
                        str(event.get("description") or "").strip(),
                        str(event.get("location") or "").strip(),
                        str(event.get("ics_path") or ""),
                        str(event.get("created_at") or datetime.now().isoformat()),
                    ),
                )
                gravados += 1
            conn.commit()
        return gravados

    def _generate_ics(
        self,
        event_id: str,
        title: str,
        start_dt: datetime,
        end_dt: datetime,
        description: str,
        location: str,
    ) -> str:
        """Gera conteúdo iCalendar padrão RFC 5545."""
        fmt = "%Y%m%dT%H%M%SZ"
        # Converte para UTC para o arquivo .ics
        from datetime import timezone
        utc_start = start_dt.strftime(fmt)
        utc_end = end_dt.strftime(fmt)
        utc_now = datetime.now(timezone.utc).strftime(fmt)

        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Zorin Copilot//Calendario//PT",
            "CALSCALE:GREGORIAN",
            "BEGIN:VEVENT",
            f"UID:copilot-{event_id}@zorin-copilot",
            f"DTSTAMP:{utc_now}",
            f"DTSTART:{utc_start}",
            f"DTEND:{utc_end}",
            f"SUMMARY:{title}",
        ]
        if description:
            lines.append(f"DESCRIPTION:{description}")
        if location:
            lines.append(f"LOCATION:{location}")
        lines.extend(["STATUS:CONFIRMED", "END:VEVENT", "END:VCALENDAR"])
        return "\r\n".join(lines) + "\r\n"

    def _open_ics(self, ics_path: str) -> None:
        """Abre o arquivo .ics no aplicativo de calendário padrão do Zorin OS."""
        for opener in ("gio", "xdg-open"):
            if shutil.which(opener):
                try:
                    subprocess.Popen(
                        [opener, "open" if opener == "gio" else "", ics_path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return
                except Exception:
                    pass
