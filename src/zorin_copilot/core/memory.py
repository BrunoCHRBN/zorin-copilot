# Decisão de design: base de conhecimento e memória persistente em SQLite local (~/.local/share/zorin-copilot/memory.db) — 100% privada, rápida (0ms em consultas locais), com aprendizado de execuções e fatos do usuário.

"""Gerenciador de memória de longo prazo e base de conhecimento do Zorin Copilot."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


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
        """Gera um resumo contextual enxuto para enriquecer o prompt da IA."""
        profile = self.get_system_profile()
        facts = self.get_all_facts()
        recent_actions = self.get_recent_actions(limit=max_actions, success_only=True)

        lines: list[str] = ["[Base de Conhecimento do Usuário e Sistema]:"]

        # Perfil
        os_info = profile.get("os_name", "Zorin OS 18")
        session = profile.get("session_type", "wayland")
        browser = profile.get("default_browser", "")
        profile_parts = [f"SO: {os_info} ({session})"]
        if browser:
            profile_parts.append(f"Navegador: {browser}")
        lines.append(f"- Ambiente: {', '.join(profile_parts)}")

        # Fatos e preferências gravados
        if facts:
            lines.append("- Fatos e preferências conhecidos do usuário:")
            for f in facts[:6]:
                lines.append(f"  • {f['content']}")

        # Ações executadas com sucesso recentemente
        if recent_actions:
            lines.append("- Ações executadas com sucesso recentemente no desktop:")
            for a in recent_actions:
                lines.append(f"  • {a['action_type']}: '{a['target']}' (a pedido de '{a['prompt']}')")

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
