# Decisão de design: Grafo de Conhecimento Semântico embarcado no SQLite.
# Modela nós (Usuário, Disciplinas, Projetos, Documentos, Dispositivos IoT) e arestas direcionadas
# (CURSA, TRABALHA_EM, LOCALIZADO_EM, DEPENDE_DE) para raciocínio relacional determinístico.
# Opera localmente sem dependência de bancos pesados externos.

"""Motor de Grafo de Conhecimento Semântico para o Zorin Copilot."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphNode:
    """Nó do Grafo de Conhecimento representando uma entidade."""

    id: str
    label: str  # ex: "User", "Course", "Project", "Device", "File"
    properties: dict[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0


@dataclass(frozen=True)
class GraphEdge:
    """Aresta direcionada representando a relação entre duas entidades."""

    id: int
    source_id: str
    target_id: str
    relation: str  # ex: "STUDIES", "CONTROLS", "WRITING", "DEPENDS_ON"
    properties: dict[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0


class KnowledgeGraph:
    """Grafo de propriedades e relações semânticas persistido no SQLite."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from .memory import MemoryManager

            self.db_path = MemoryManager().db_path
        else:
            self.db_path = Path(db_path)

        self._init_graph_tables()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_graph_tables(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    properties_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_label ON graph_nodes(label);"
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    properties_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(source_id, target_id, relation),
                    FOREIGN KEY(source_id) REFERENCES graph_nodes(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_id) REFERENCES graph_nodes(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_source ON graph_edges(source_id, relation);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_target ON graph_edges(target_id, relation);"
            )

    def add_node(
        self,
        node_id: str,
        label: str,
        properties: dict[str, Any] | None = None,
    ) -> GraphNode:
        """Cria ou atualiza um nó no grafo."""
        now = time.time()
        props_str = json.dumps(properties or {}, ensure_ascii=False)

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO graph_nodes (id, label, properties_json, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (node_id, label, props_str, now),
            )
        return GraphNode(id=node_id, label=label, properties=properties or {}, updated_at=now)

    def get_node(self, node_id: str) -> GraphNode | None:
        """Recupera um nó pelo ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT id, label, properties_json, updated_at FROM graph_nodes WHERE id = ?",
                (node_id,),
            ).fetchone()
            if not row:
                return None
            try:
                props = json.loads(row["properties_json"])
            except Exception:
                props = {}
            return GraphNode(
                id=row["id"],
                label=row["label"],
                properties=props,
                updated_at=row["updated_at"],
            )

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        relation: str,
        properties: dict[str, Any] | None = None,
    ) -> bool:
        """Cria ou atualiza uma aresta direcionada entre dois nós."""
        now = time.time()
        props_str = json.dumps(properties or {}, ensure_ascii=False)

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR REPLACE INTO graph_edges (source_id, target_id, relation, properties_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (source_id, target_id, relation.upper(), props_str, now),
            )
            return cursor.rowcount > 0

    def get_neighbors(
        self,
        node_id: str,
        direction: str = "out",  # "out", "in", "both"
        relation: str | None = None,
    ) -> list[tuple[GraphEdge, GraphNode]]:
        """Busca nós vizinhos conectados ao nó informado."""
        results: list[tuple[GraphEdge, GraphNode]] = []
        rel_upper = relation.upper() if relation else None

        with self._get_connection() as conn:
            # 1. Arestas de saída: node -> target
            if direction in ("out", "both"):
                query = """
                    SELECT e.id as edge_id, e.source_id, e.target_id, e.relation, e.properties_json as edge_props, e.updated_at as edge_ts,
                           n.id as node_id, n.label as node_label, n.properties_json as node_props, n.updated_at as node_ts
                    FROM graph_edges e
                    JOIN graph_nodes n ON e.target_id = n.id
                    WHERE e.source_id = ?
                """
                params: list[Any] = [node_id]
                if rel_upper:
                    query += " AND e.relation = ?"
                    params.append(rel_upper)

                for r in conn.execute(query, params).fetchall():
                    edge = GraphEdge(
                        id=r["edge_id"],
                        source_id=r["source_id"],
                        target_id=r["target_id"],
                        relation=r["relation"],
                        properties=json.loads(r["edge_props"]) if r["edge_props"] else {},
                        updated_at=r["edge_ts"],
                    )
                    node = GraphNode(
                        id=r["node_id"],
                        label=r["node_label"],
                        properties=json.loads(r["node_props"]) if r["node_props"] else {},
                        updated_at=r["node_ts"],
                    )
                    results.append((edge, node))

            # 2. Arestas de entrada: source -> node
            if direction in ("in", "both"):
                query = """
                    SELECT e.id as edge_id, e.source_id, e.target_id, e.relation, e.properties_json as edge_props, e.updated_at as edge_ts,
                           n.id as node_id, n.label as node_label, n.properties_json as node_props, n.updated_at as node_ts
                    FROM graph_edges e
                    JOIN graph_nodes n ON e.source_id = n.id
                    WHERE e.target_id = ?
                """
                params = [node_id]
                if rel_upper:
                    query += " AND e.relation = ?"
                    params.append(rel_upper)

                for r in conn.execute(query, params).fetchall():
                    edge = GraphEdge(
                        id=r["edge_id"],
                        source_id=r["source_id"],
                        target_id=r["target_id"],
                        relation=r["relation"],
                        properties=json.loads(r["edge_props"]) if r["edge_props"] else {},
                        updated_at=r["edge_ts"],
                    )
                    node = GraphNode(
                        id=r["node_id"],
                        label=r["node_label"],
                        properties=json.loads(r["node_props"]) if r["node_props"] else {},
                        updated_at=r["node_ts"],
                    )
                    results.append((edge, node))

        return results

    def find_nodes(self, label: str | None = None) -> list[GraphNode]:
        """Localiza nós por rótulo (ex: 'Course', 'Project', 'Device')."""
        nodes: list[GraphNode] = []
        with self._get_connection() as conn:
            if label:
                cur = conn.execute("SELECT id, label, properties_json, updated_at FROM graph_nodes WHERE label = ?", (label,))
            else:
                cur = conn.execute("SELECT id, label, properties_json, updated_at FROM graph_nodes")

            for r in cur.fetchall():
                nodes.append(
                    GraphNode(
                        id=r["id"],
                        label=r["label"],
                        properties=json.loads(r["properties_json"]) if r["properties_json"] else {},
                        updated_at=r["updated_at"],
                    )
                )
        return nodes

    def delete_node(self, node_id: str) -> bool:
        """Remove um nó e suas arestas associadas."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM graph_edges WHERE source_id = ? OR target_id = ?", (node_id, node_id))
            cur = conn.execute("DELETE FROM graph_nodes WHERE id = ?", (node_id,))
            return cur.rowcount > 0
