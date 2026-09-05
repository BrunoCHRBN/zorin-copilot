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
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
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
        """Converte strings em datas (suporta termos como 'hoje', 'amanhã', horários e ISO)."""
        clean = date_str.strip().lower()
        now = datetime.now()

        # Extrai horário se presente (ex: 15:30 ou 15h)
        hour = 10
        minute = 0
        time_match = re.search(r"(\d{1,2})[:h](\d{2})?", clean)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2)) if time_match.group(2) else 0

        target_date = now
        if "amanhã" in clean or "amanha" in clean:
            target_date = now + timedelta(days=1)
        elif "depois de amanhã" in clean:
            target_date = now + timedelta(days=2)
        else:
            # Tenta parsing ISO
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
