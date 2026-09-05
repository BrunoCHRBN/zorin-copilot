# Decisão de design: base de conhecimento e memória persistente em SQLite local (~/.local/share/zorin-copilot/memory.db) — 100% privada, rápida (0ms em consultas locais), com aprendizado de execuções e fatos do usuário.

"""Gerenciador de memória de longo prazo e base de conhecimento do Zorin Copilot."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Sequence


class MemoryManager:
    """Gerencia a persistência de histórico de execuções, preferências e fatos do sistema."""

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
            dir_path = Path(base) / "zorin-copilot"
            dir_path.mkdir(parents=True, exist_ok=True)
            self.db_path = dir_path / "memory.db"
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()
        self.sync_system_profile()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. Tabela de histórico de ações executadas
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS action_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    target TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    message TEXT NOT NULL
                )
            """)

            # 2. Tabela de fatos aprendidos e preferências
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    key TEXT UNIQUE NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # 3. Tabela de perfil do sistema e hardware
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_profile (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # 4. Tabela de tópicos e sessões de chat salvas
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_topics (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    turn_count INTEGER NOT NULL,
                    is_pinned INTEGER NOT NULL DEFAULT 1,
                    turns_json TEXT NOT NULL
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_topics_updated ON chat_topics (updated_at DESC)
            """)

            # 5. Tabela de contatos do usuário (Memória Semântica e Anti-Alucinação)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    phone TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    last_contacted_at TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_contacts_name ON user_contacts (name COLLATE NOCASE)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_contacts_email ON user_contacts (email COLLATE NOCASE)
            """)
            conn.commit()

    # =========================================================================
    # Histórico de Execuções (Memória Episódica)
    # =========================================================================

    def log_action(
        self,
        prompt: str,
        action_type: str,
        target: str,
        params: dict[str, Any] | None,
        success: bool,
        message: str,
    ) -> None:
        """Registra a execução de uma ação proposta."""
        now = datetime.now().isoformat()
        params_str = json.dumps(params or {}, ensure_ascii=False)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO action_logs (timestamp, prompt, action_type, target, params_json, success, message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (now, prompt, action_type, target, params_str, 1 if success else 0, message),
            )
            conn.commit()

    def get_recent_actions(self, limit: int = 10, success_only: bool = False) -> list[dict[str, Any]]:
        """Recupera histórico recente de ações."""
        query = "SELECT * FROM action_logs"
        params: list[Any] = []
        if success_only:
            query += " WHERE success = 1"
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_action_stats(self) -> dict[str, Any]:
        """Estatísticas de execução para o painel de preferências."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total, SUM(success) as successful FROM action_logs")
            row = cursor.fetchone()
            total = row["total"] or 0
            successful = row["successful"] or 0
            return {
                "total": total,
                "successful": successful,
                "failed": total - successful,
                "success_rate": round((successful / total * 100), 1) if total > 0 else 100.0,
            }

    # =========================================================================
    # Fatos Aprendidos e Preferências (Memória Semântica)
    # =========================================================================

    def save_fact(
        self,
        key: str,
        content: str,
        category: str = "preferencia",
        source: str = "usuario",
    ) -> None:
        """Salva ou atualiza um fato aprendido ou preferência na base."""
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO knowledge_facts (category, key, content, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    content=excluded.content,
                    category=excluded.category,
                    updated_at=excluded.updated_at
                """,
                (category, key.strip().lower(), content.strip(), source, now, now),
            )
            conn.commit()

    def get_all_facts(self, category: str | None = None) -> list[dict[str, Any]]:
        """Recupera todos os fatos da base de conhecimento."""
        query = "SELECT * FROM knowledge_facts"
        params: list[Any] = []
        if category:
            query += " WHERE category = ?"
            params.append(category)
        query += " ORDER BY updated_at DESC"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(r) for r in cursor.fetchall()]

    def delete_fact(self, fact_id: int) -> bool:
        """Remove um fato da base pelo seu ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM knowledge_facts WHERE id = ?", (fact_id,))
            conn.commit()
            return cursor.rowcount > 0

    def delete_fact_by_key(self, key: str) -> bool:
        """Remove um fato pela sua chave."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM knowledge_facts WHERE key = ?", (key.strip().lower(),))
            conn.commit()
            return cursor.rowcount > 0

    # =========================================================================
    # Gestão de Contatos do Usuário (Memória Semântica & Anti-Alucinação)
    # =========================================================================

    def save_contact(
        self,
        name: str,
        email: str,
        aliases: Sequence[str] | None = None,
        phone: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        """Salva ou atualiza um contato na base local."""
        email_clean = email.strip().lower()
        if not email_clean or "@" not in email_clean:
            raise ValueError(f"Endereço de e-mail inválido: '{email}'")

        name_clean = name.strip()
        now = datetime.now().isoformat()
        aliases_list = [a.strip().lower() for a in aliases if a.strip()] if aliases else []
        aliases_json = json.dumps(aliases_list, ensure_ascii=False)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO user_contacts (name, email, aliases_json, phone, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    name=excluded.name,
                    aliases_json=excluded.aliases_json,
                    phone=excluded.phone,
                    notes=excluded.notes
                """,
                (name_clean, email_clean, aliases_json, phone.strip(), notes.strip(), now),
            )
            conn.commit()

        return {
            "name": name_clean,
            "email": email_clean,
            "aliases": aliases_list,
            "phone": phone.strip(),
            "notes": notes.strip(),
        }

    def find_contact(self, query: str) -> list[dict[str, Any]]:
        """Busca contatos por nome, e-mail ou apelidos (case-insensitive)."""
        q = query.strip().lower()
        if not q:
            return []

        all_contacts = self.list_contacts(limit=200)
        matches: list[dict[str, Any]] = []

        for c in all_contacts:
            name_lower = c["name"].lower()
            email_lower = c["email"].lower()
            aliases_lower = [a.lower() for a in c.get("aliases", [])]

            # Correspondência exata ou parcial
            if q == name_lower or q == email_lower or q in aliases_lower:
                matches.insert(0, c)
            elif q in name_lower or q in email_lower or any(q in a for a in aliases_lower):
                matches.append(c)

        return matches

    def get_contact_by_email(self, email: str) -> dict[str, Any] | None:
        """Busca exata de contato por e-mail."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_contacts WHERE email = ?", (email.strip().lower(),))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["aliases"] = json.loads(data.get("aliases_json") or "[]")
            return data

    def list_contacts(self, limit: int = 100) -> list[dict[str, Any]]:
        """Lista todos os contatos cadastrados ordenados por nome."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_contacts ORDER BY name COLLATE NOCASE ASC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            results: list[dict[str, Any]] = []
            for r in rows:
                item = dict(r)
                item["aliases"] = json.loads(item.get("aliases_json") or "[]")
                results.append(item)
            return results

    def delete_contact(self, contact_id: int) -> bool:
        """Remove um contato pelo ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_contacts WHERE id = ?", (contact_id,))
            conn.commit()
            return cursor.rowcount > 0

    def delete_contact_by_email(self, email: str) -> bool:
        """Remove um contato pelo e-mail."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_contacts WHERE email = ?", (email.strip().lower(),))
            conn.commit()
            return cursor.rowcount > 0

    def update_contact_last_used(self, email: str) -> None:
        """Registra a data e hora do último contato para priorização."""
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE user_contacts SET last_contacted_at = ? WHERE email = ?",
                (now, email.strip().lower()),
            )
            conn.commit()

    # =========================================================================
    # Perfil do Sistema (Hardware e Ambiente)
    # =========================================================================

    def sync_system_profile(self) -> dict[str, str]:
        """Sincroniza informações técnicas locais do Zorin OS."""
        info: dict[str, str] = {}

        # 1. Distro e Versão
        try:
            with open("/etc/os-release", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        info["os_name"] = line.split("=", 1)[1].strip().strip('"')
                        break
        except Exception:
            info["os_name"] = "Zorin OS"

        # 2. Sessão Wayland ou X11
        info["session_type"] = os.environ.get("XDG_SESSION_TYPE", "wayland")
        info["desktop"] = os.environ.get("XDG_CURRENT_DESKTOP", "GNOME")
        info["arch"] = platform.machine()
        info["kernel"] = platform.release()

        # 3. Navegador padrão
        try:
            from gi.repository import Gio
            app = Gio.AppInfo.get_default_for_type("text/html", True)
            if app:
                info["default_browser"] = app.get_name()
        except Exception:
            pass

        # 4. Servidor de áudio
        if shutil.which("wpctl") or shutil.which("pipewire"):
            info["audio_server"] = "PipeWire"
        elif shutil.which("pulseaudio"):
            info["audio_server"] = "PulseAudio"

        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for k, v in info.items():
                cursor.execute(
                    """
                    INSERT INTO system_profile (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (k, v, now),
                )
            conn.commit()

        return info

    def get_system_profile(self) -> dict[str, str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM system_profile")
            return {r["key"]: r["value"] for r in cursor.fetchall()}

    # =========================================================================
    # Recuperação Contextual para o Prompt da IA (RAG Leve)
    # =========================================================================

    def get_context_summary(self, max_actions: int = 4) -> str:
        """Gera um resumo contextual dinâmico com data/hora em tempo real, telemetria e perfil."""
        profile = self.get_system_profile()
        facts = self.get_all_facts()
        recent_actions = self.get_recent_actions(limit=max_actions, success_only=True)

        lines: list[str] = ["[Base de Conhecimento e Estado do Sistema]:"]

        # 1. Contexto Temporal em Tempo Real
        now = datetime.now()
        dias_semana = [
            "Segunda-feira", "Terça-feira", "Quarta-feira",
            "Quinta-feira", "Sexta-feira", "Sábado", "Domingo",
        ]
        meses = [
            "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
        ]
        dia_sem = dias_semana[now.weekday()]
        mes_nome = meses[now.month - 1]
        data_atual_str = f"{dia_sem}, {now.day:02d} de {mes_nome} de {now.year}, às {now.strftime('%H:%M')}"

        amanha = now + timedelta(days=1)
        dia_amanha = dias_semana[amanha.weekday()]
        mes_amanha = meses[amanha.month - 1]
        amanha_str = f"{dia_amanha}, {amanha.day:02d} de {mes_amanha} de {amanha.year}"

        lines.append(f"- Data e Hora Atual: {data_atual_str}")
        lines.append(f"- Amanhã será: {amanha_str}")

        # 2. Perfil do Sistema e Hardware
        os_info = profile.get("os_name", "Zorin OS 18")
        session = profile.get("session_type", "wayland")
        browser = profile.get("default_browser", "")
        profile_parts = [f"SO: {os_info} ({session})"]
        if browser:
            profile_parts.append(f"Navegador: {browser}")

        # Telemetria rápida de disco
        try:
            disk = shutil.disk_usage(os.path.expanduser("~"))
            free_gb = disk.free / (1024 ** 3)
            profile_parts.append(f"Disco livre: {free_gb:.1f} GB")
        except Exception:
            pass

        # Telemetria rápida de memória RAM
        try:
            with open("/proc/meminfo") as f:
                mem_data = dict(line.strip().split(":", 1) for line in f if ":" in line)
            avail_kb = int(mem_data.get("MemAvailable", "0 kB").split()[0])
            avail_gb = avail_kb / (1024 * 1024)
            profile_parts.append(f"RAM disponível: {avail_gb:.1f} GB")
        except Exception:
            pass

        lines.append(f"- Ambiente e Recursos: {', '.join(profile_parts)}")

        # 3. Fatos e preferências gravados
        if facts:
            lines.append("- Fatos e preferências conhecidos do usuário:")
            for f in facts[:6]:
                lines.append(f"  • {f['content']}")

        # 4. Ações executadas com sucesso recentemente
        if recent_actions:
            lines.append("- Ações executadas com sucesso recentemente no desktop:")
            for a in recent_actions:
                lines.append(f"  • {a['action_type']}: '{a['target']}' (a pedido de '{a['prompt']}')")

        # 5. Contatos salvos do usuário (Memória Semântica & Anti-Alucinação)
        contacts = self.list_contacts(limit=8)
        if contacts:
            lines.append("- Contatos salvos do usuário (utilize EXATAMENTE estes e-mails; NUNCA invente ou adivinhe outros):")
            for c in contacts:
                aliases_str = f" (apelidos: {', '.join(c['aliases'])})" if c.get("aliases") else ""
                lines.append(f"  • {c['name']} <{c['email']}>{aliases_str}")
        else:
            lines.append("- Contatos salvos: Nenhum contato cadastrado ainda. Se o usuário solicitar envio para alguém, consulte ou pergunte o e-mail.")

        return "\n".join(lines)

    # =========================================================================
    # Histórico de Tópicos e Sessões de Chat
    # =========================================================================

    def save_chat_topic(
        self,
        topic_id: str,
        title: str,
        turns: list[dict[str, Any]],
        is_pinned: bool = True,
        created_at: str | None = None,
    ) -> str:
        """Salva ou atualiza um tópico de chat no banco de dados."""
        now = datetime.now().isoformat()
        created = created_at or now
        turns_json = json.dumps(turns, ensure_ascii=False)
        turn_count = len(turns)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO chat_topics (id, title, created_at, updated_at, turn_count, is_pinned, turns_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    updated_at=excluded.updated_at,
                    turn_count=excluded.turn_count,
                    is_pinned=excluded.is_pinned,
                    turns_json=excluded.turns_json
                """,
                (topic_id, title, created, now, turn_count, 1 if is_pinned else 0, turns_json),
            )
            conn.commit()
        return topic_id

    def list_chat_topics(self, limit: int = 50) -> list[dict[str, Any]]:
        """Lista tópicos salvos ordenados pelo mais recente."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, title, created_at, updated_at, turn_count, is_pinned, turns_json
                FROM chat_topics
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()

        topics: list[dict[str, Any]] = []
        for r in rows:
            try:
                parsed_turns = json.loads(r["turns_json"])
            except Exception:
                parsed_turns = []
            preview = parsed_turns[-1]["answer"][:120] if parsed_turns else ""
            topics.append({
                "id": r["id"],
                "title": r["title"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "turn_count": r["turn_count"],
                "is_pinned": bool(r["is_pinned"]),
                "turns": parsed_turns,
                "preview": preview,
            })
        return topics

    def get_chat_topic(self, topic_id: str) -> dict[str, Any] | None:
        """Busca um tópico específico com todas as suas mensagens."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, title, created_at, updated_at, turn_count, is_pinned, turns_json
                FROM chat_topics
                WHERE id = ?
                """,
                (topic_id,),
            )
            row = cursor.fetchone()

        if not row:
            return None

        try:
            turns = json.loads(row["turns_json"])
        except Exception:
            turns = []

        return {
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "turn_count": row["turn_count"],
            "is_pinned": bool(row["is_pinned"]),
            "turns": turns,
        }

    def delete_chat_topic(self, topic_id: str) -> bool:
        """Exclui um tópico do histórico."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_topics WHERE id = ?", (topic_id,))
            conn.commit()
            return cursor.rowcount > 0

    def clear_all_chat_topics(self) -> None:
        """Remove todos os tópicos salvos."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_topics")
            conn.commit()

    def clear_all(self) -> None:
        """Limpa toda a base de memória e histórico."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM action_logs")
            cursor.execute("DELETE FROM knowledge_facts")
            cursor.execute("DELETE FROM chat_topics")
            conn.commit()
