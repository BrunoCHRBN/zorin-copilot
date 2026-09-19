# Decisão de design: Consolidação em Batch da memória ("Sono REM").
# Executada em segundo plano quando o computador está ocioso ou via timer noturno do systemd.
# Agrupa os logs de ações recentes (action_logs), extrai padrões recorrentes (ex: ferramentas
# mais usadas, projetos tocados), atualiza o Grafo de Conhecimento e poda logs brutos transitórios
# para manter o memory.db compacto, leve e rápido ao longo dos meses.

"""Consolidador noturno de memória episódica e destilação para o Grafo de Conhecimento."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .knowledge_graph import KnowledgeGraph
from .memory import MemoryManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConsolidationReport:
    """Relatório das operações de consolidação e poda realizadas."""

    logs_analyzed: int
    logs_pruned: int
    nodes_updated: int
    edges_updated: int
    execution_time_sec: float


class MemoryConsolidator:
    """Motor de consolidação periódica e limpeza de memória do Zorin Copilot."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            self.db_path = MemoryManager().db_path
        else:
            self.db_path = Path(db_path)

        self.graph = KnowledgeGraph(db_path=self.db_path)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def run_consolidation(self, days_to_keep: int = 14) -> ConsolidationReport:
        """Executa o ciclo de consolidação: destilação de conhecimento e poda de logs antigos."""
        start_time = time.time()
        logger.info("Iniciando ciclo de consolidação de memória...")

        logs_analyzed = 0
        logs_pruned = 0
        nodes_updated = 0
        edges_updated = 0

        # 1. Análise de padrões recentes em action_logs
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT action_type, target, success, count(*) as count
                FROM action_logs
                GROUP BY action_type, target
                ORDER BY count DESC
                """
            )
            rows = cur.fetchall()
            logs_analyzed = len(rows)

            for r in rows:
                act = r["action_type"]
                target = r["target"]
                count = r["count"]

                # Exemplo: Se abriu/editou arquivos frequentemente, registra relevância no Grafo
                if act in ("write_document", "open_document", "vscode_workspace") and target:
                    node_id = f"file:{Path(target).name}"
                    self.graph.add_node(
                        node_id=node_id,
                        label="FrequentDocument",
                        properties={"path": target, "access_count": count},
                    )
                    self.graph.add_edge(
                        source_id="user:default",
                        target_id=node_id,
                        relation="FREQUENTLY_ACCESSED",
                        properties={"count": count},
                    )
                    nodes_updated += 1
                    edges_updated += 1

            # 2. Poda de logs brutos anteriores ao limite de dias estipulado
            cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).isoformat()
            del_cur = conn.execute(
                "DELETE FROM action_logs WHERE timestamp < ?",
                (cutoff_date,),
            )
            logs_pruned = del_cur.rowcount

            # 3. Otimização do banco de dados (WAL checkpoint e vácuo incremental)
            conn.execute("PRAGMA incremental_vacuum;")

        elapsed = time.time() - start_time
        report = ConsolidationReport(
            logs_analyzed=logs_analyzed,
            logs_pruned=logs_pruned,
            nodes_updated=nodes_updated,
            edges_updated=edges_updated,
            execution_time_sec=elapsed,
        )

        logger.info(
            "Consolidação concluída em %.3fs: %d logs podados, %d nós no grafo atualizados.",
            elapsed,
            logs_pruned,
            nodes_updated,
        )
        return report
