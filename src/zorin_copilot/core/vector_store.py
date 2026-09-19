# Decisão de design: Armazenamento vetorial embarcado (Episodic Vector Store)
# Salva embeddings densos diretamente no memory.db como BLOBs binários (float32 array)
# com cálculo de similaridade por cosseno em O(N). Não exige servidor externo (Docker/Qdrant/Chroma)
# e garante 100% de privacidade local com zero overhead de rede.

"""Banco de vetores embarcado para memória episódica no SQLite."""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VectorSearchResult:
    """Resultado de uma busca por similaridade vetorial."""

    id: str
    text: str
    score: float  # Similaridade de cosseno [-1.0, 1.0]
    category: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0


def _pack_vector(vector: Sequence[float]) -> bytes:
    """Serializa lista de floats para bytes binários float32 (4 bytes por dimensão)."""
    return struct.pack(f"{len(vector)}f", *vector)


def _unpack_vector(raw: bytes) -> list[float]:
    """Deserializa bytes binários para lista de floats."""
    count = len(raw) // 4
    return list(struct.unpack(f"{count}f", raw))


def cosine_similarity(v1: Sequence[float], v2: Sequence[float]) -> float:
    """Calcula similaridade de cosseno pura entre dois vetores."""
    if len(v1) != len(v2) or not v1:
        return 0.0

    dot = 0.0
    norm1 = 0.0
    norm2 = 0.0

    for a, b in zip(v1, v2):
        dot += a * b
        norm1 += a * a
        norm2 += b * b

    if norm1 <= 0.0 or norm2 <= 0.0:
        return 0.0

    return dot / (math.sqrt(norm1) * math.sqrt(norm2))


class EpisodicVectorStore:
    """Gerenciador de memória vetorial para o Zorin Copilot."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from .memory import MemoryManager

            self.db_path = MemoryManager().db_path
        else:
            self.db_path = Path(db_path)

        self._init_tables()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_tables(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodic_vectors (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    vector BLOB NOT NULL,
                    dimension INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodic_cat ON episodic_vectors(category);"
            )

    def add(
        self,
        doc_id: str,
        text: str,
        vector: Sequence[float],
        category: str = "general",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insere ou atualiza um documento vetorial na memória episódica."""
        raw_bytes = _pack_vector(vector)
        meta_str = json.dumps(metadata or {}, ensure_ascii=False)
        now = time.time()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO episodic_vectors
                (id, text, vector, dimension, category, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (doc_id, text, raw_bytes, len(vector), category, now, meta_str),
            )

    def search(
        self,
        query_vector: Sequence[float],
        top_k: int = 5,
        category: str | None = None,
        min_score: float = 0.0,
    ) -> list[VectorSearchResult]:
        """Busca os documentos mais similares ao vetor de consulta."""
        if not query_vector:
            return []

        results: list[VectorSearchResult] = []
        sql = "SELECT id, text, vector, category, created_at, metadata_json FROM episodic_vectors"
        params: list[Any] = []

        if category:
            sql += " WHERE category = ?"
            params.append(category)

        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            for row in cursor.fetchall():
                doc_vec = _unpack_vector(row["vector"])
                score = cosine_similarity(query_vector, doc_vec)
                if score >= min_score:
                    meta = {}
                    try:
                        meta = json.loads(row["metadata_json"])
                    except Exception:
                        pass
                    results.append(
                        VectorSearchResult(
                            id=row["id"],
                            text=row["text"],
                            score=score,
                            category=row["category"],
                            metadata=meta,
                            created_at=row["created_at"],
                        )
                    )

        # Ordena pela maior similaridade de cosseno
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def delete(self, doc_id: str) -> bool:
        """Remove um documento vetorial pelo ID."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM episodic_vectors WHERE id = ?", (doc_id,))
            return cursor.rowcount > 0

    def count(self, category: str | None = None) -> int:
        """Retorna o número total de vetores armazenados."""
        with self._get_connection() as conn:
            if category:
                cur = conn.execute("SELECT COUNT(*) FROM episodic_vectors WHERE category = ?", (category,))
            else:
                cur = conn.execute("SELECT COUNT(*) FROM episodic_vectors")
            return int(cur.fetchone()[0])
