# Decisão de design: Consolidação em Batch da memória ("Sono REM").
# Executada em segundo plano quando o computador está ocioso ou via timer noturno do systemd.
# Agrupa os logs de ações recentes (action_logs), extrai padrões recorrentes (ex: ferramentas
# mais usadas, projetos tocados), atualiza o Grafo de Conhecimento e poda logs brutos transitórios
# para manter o memory.db compacto, leve e rápido ao longo dos meses.
#
# A política de "quando rodar" mora no fim do arquivo (`decide_consolidation`) e é **pura**: um
# arquivo de estado diz quando foi a última vez, e a regra decide se já passou. O temporizador
# do app só obedece — assim a decisão é exercitável sem esperar 24 horas.

"""Consolidador noturno de memória episódica e destilação para o Grafo de Conhecimento."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
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


# ---------------------------------------------------------------------------
# Quando consolidar — a decisão, pura
# ---------------------------------------------------------------------------
#
# O consolidador existia e nunca era chamado por ninguém: `run_consolidation()` tinha 0
# referências no `src/`. Ligá-lo a um `threading.Timer` de 24 h não resolveria — o timer morre com
# o processo, então quem reinicia o app todo dia nunca consolida nada. "Diário" tem de significar
# **no máximo uma vez por dia**, e isso exige lembrar quando foi a última vez: daí o arquivo de
# estado, e daí a decisão ser uma função separada do efeito.

#: Intervalo padrão entre consolidações. Um dia é o que o desenho original pedia.
DEFAULT_INTERVAL_HOURS = 24.0

#: Dias de `action_logs` que se mantêm. Medido nesta máquina: 194 linhas em 5 dias (~97 KB) →
#: ~6,9 MB por ano sem poda, contra ~0,27 MB com o corte em 14 dias. O banco inteiro tem 7,7 MB.
DEFAULT_DAYS_TO_KEEP = 14

#: O estado fica **ao lado do banco**, não em `system_profile`. Aquele tabela é injetada no
#: contexto do agente (`agent_tools.py`, `live.py`) e uma chave de contabilidade interna vazaria
#: para dentro do prompt.
STATE_FILENAME = "consolidation_state.json"


class ConsolidationDecision(str, Enum):
    """Desfechos nomeados da pergunta "é hora de consolidar?".

    Enum e não booleano: o motivo é o que se precisa ler no log quando nada acontece, e
    "não rodou" tem causas muito diferentes — recurso desligado, acabou de rodar, ou o relógio
    andou para trás.
    """

    DISABLED = "disabled"
    NEVER_RAN = "never_ran"
    CLOCK_SKEWED = "clock_skewed"
    DUE = "due"
    NOT_YET = "not_yet"


@dataclass(frozen=True)
class ConsolidationState:
    """O que o arquivo de estado guarda. Imutável: quem grava constrói outro."""

    last_run_at: datetime | None = None
    last_logs_pruned: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_logs_pruned": self.last_logs_pruned,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> ConsolidationState:
        """Lê o estado tolerando lixo — e lixo significa "nunca rodou".

        Arquivo truncado, JSON válido mas com o campo errado, data que não parseia: tudo isso
        cai no estado vazio, e o efeito é consolidar de novo. É a escolha segura: o custo de
        rodar uma vez a mais é uma consulta; o custo de achar que já rodou é não rodar nunca.
        """
        if not isinstance(payload, dict):
            return cls()
        last_run_at: datetime | None = None
        raw = payload.get("last_run_at")
        if isinstance(raw, str) and raw.strip():
            try:
                last_run_at = datetime.fromisoformat(raw.strip())
            except ValueError:
                last_run_at = None
        pruned = payload.get("last_logs_pruned")
        return cls(
            last_run_at=last_run_at,
            last_logs_pruned=pruned if isinstance(pruned, int) and pruned >= 0 else 0,
        )


@dataclass(frozen=True)
class ConsolidationOutcome:
    """A decisão, com o contexto que a torna legível."""

    decision: ConsolidationDecision
    last_run_at: datetime | None = None
    next_due_at: datetime | None = None

    @property
    def should_run(self) -> bool:
        """Só os três desfechos que significam "há trabalho" contam.

        A checagem pelo nome, e não por `not in (NOT_YET, DISABLED)`, é deliberada: um desfecho
        novo acrescentado sem intenção de rodar não passa a rodar por descuido.
        """
        return self.decision in (
            ConsolidationDecision.NEVER_RAN,
            ConsolidationDecision.CLOCK_SKEWED,
            ConsolidationDecision.DUE,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "should_run": self.should_run,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "next_due_at": self.next_due_at.isoformat() if self.next_due_at else None,
        }


def decide_consolidation(
    state: ConsolidationState,
    *,
    now: datetime,
    enabled: bool,
    interval_hours: float = DEFAULT_INTERVAL_HOURS,
) -> ConsolidationOutcome:
    """Diz se é hora de consolidar, e por quê.

    A ordem das condições é a regra, e vai da mais fundamental para a mais circunstancial: com o
    recurso desligado, "nunca rodou" ou "o relógio andou para trás" seriam motivos enganosos.

    `interval_hours` negativo é normalizado para zero. Note que a **decisão** não muda por isso —
    o tempo decorrido nunca é menor que um intervalo negativo, então ambos os caminhos dão DUE. O
    que a normalização compra é o `next_due_at`, que entra no `to_dict()` público: sem ela, uma
    data sem sentido ficaria registrada no passado.
    """
    if not enabled:
        return ConsolidationOutcome(ConsolidationDecision.DISABLED, state.last_run_at)

    if state.last_run_at is None:
        return ConsolidationOutcome(ConsolidationDecision.NEVER_RAN)

    # Relógio para trás (correção por NTP, arquivo restaurado de backup): sem esta condição o
    # intervalo ficaria negativo para sempre e a consolidação **nunca mais** rodaria, em silêncio.
    if state.last_run_at > now:
        logger.warning(
            "Estado da consolidação no futuro (%s > %s); tratando como vencida.",
            state.last_run_at.isoformat(),
            now.isoformat(),
        )
        return ConsolidationOutcome(ConsolidationDecision.CLOCK_SKEWED, state.last_run_at)

    interval = timedelta(hours=max(0.0, interval_hours))
    next_due = state.last_run_at + interval
    if now - state.last_run_at >= interval:
        return ConsolidationOutcome(ConsolidationDecision.DUE, state.last_run_at, next_due)

    return ConsolidationOutcome(ConsolidationDecision.NOT_YET, state.last_run_at, next_due)


def consolidation_state_path(db_path: str | Path | None = None) -> Path:
    """Caminho do arquivo de estado, ao lado do banco de memória."""
    if db_path is None:
        return MemoryManager().db_path.parent / STATE_FILENAME
    return Path(db_path).parent / STATE_FILENAME


def load_consolidation_state(path: str | Path) -> ConsolidationState:
    """Lê o estado do disco. Ausente, ilegível ou corrompido ⇒ nunca rodou."""
    caminho = Path(path)
    try:
        with open(caminho, encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        return ConsolidationState()
    except (OSError, ValueError) as exc:
        logger.warning("Estado da consolidação ilegível (%s); tratando como nunca executada.", exc)
        return ConsolidationState()
    return ConsolidationState.from_dict(payload)


def save_consolidation_state(path: str | Path, state: ConsolidationState) -> None:
    """Grava o estado. Falhar aqui não pode derrubar quem chamou.

    Gravação atômica (arquivo temporário + `replace`): um desligamento no meio da escrita deixaria
    um JSON truncado, e o efeito de um estado ilegível é consolidar de novo — barato, mas evitável.
    """
    caminho = Path(path)
    temporario = caminho.with_suffix(caminho.suffix + ".tmp")
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(temporario, "w", encoding="utf-8") as fh:
            json.dump(state.to_dict(), fh, ensure_ascii=False, indent=2)
        temporario.replace(caminho)
    except OSError as exc:
        logger.debug("Falha ao gravar o estado da consolidação: %s", exc)
        # O temporário não fica para trás: a próxima gravação bem-sucedida o
        # sobrescreveria de qualquer forma, mas um órfão ao lado do banco é sujeira
        # gratuita — e quem for olhar o diretório vai querer saber de onde veio.
        try:
            temporario.unlink(missing_ok=True)
        except OSError:
            pass
