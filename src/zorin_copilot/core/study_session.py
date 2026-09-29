# Decisão de design: este módulo é o "cérebro" único que fecha a fragmentação de
# estado apontada em `docs/ANALISE_APRIMORAMENTO_RACIOCINIO.md` §3.1/§6.4 — hoje
# chat, voz e vídeo seguem fluxos separados e não compartilham o problema em curso.
#
# É **puro e testável sem display/rede** (mesma filosofia de `core/export.py` /
# `core/attachments.py`): stdlib + dataclasses apenas. Não importa GTK, AT-SPI nem
# `ai.math_solver` no topo do arquivo — o grafo do quadro negro é guardado como
# objeto opaco e reconstruído via import tardio em `get_blackboard()`, evitando
# ciclo de import (core -> ai) e mantendo o módulo leve.
#
# Concorrência: chat (GTK main loop), voz/vídeo (threads do LiveClient) e o loop do
# agente rodam em threads diferentes. A sessão é estado mutável compartilhado, então
# TODO acesso passa por `RLock`. O pub/sub emite fora do lock para não travar se um
# callback re-entrar na sessão; quem mexe na UI faz o marshaling (GLib.idle_add) no
# próprio callback.
#
# Versionamento: `to_dict` sempre inclui `version`; `from_dict`/`load` rodam
# `_migrate`, então upgrades de esquema não corrompem estado salvo.

"""Estado compartilhado de estudo entre modalidades (chat/voz/vídeo/agente)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Eventos publicados (ver docs/PLANO_ASSISTENTE_ESTUDOS.md §2).
STUDY_EVENT_PROBLEM_SET = "problem_set"
STUDY_EVENT_STEP_HIGHLIGHTED = "step_highlighted"
STUDY_EVENT_METHOD_SELECTED = "method_selected"
STUDY_EVENT_BLACKBOARD_EXPANDED = "blackboard_expanded"
STUDY_EVENT_VERIFICATION_DONE = "verification_done"


# --------------------------------------------------------------------------- #
# Migração de esquema (versionado) — registra um passo por versão de origem.
# --------------------------------------------------------------------------- #
def _migrate_from_1(data: dict[str, Any]) -> dict[str, Any]:
    """Placeholder: migração da v1 para v2 (ainda não há campos novos).

    Mantido para demonstrar o padrão — quando a v2 adicionar um campo, basta
    preencher o default aqui e incrementar ``SCHEMA_VERSION``.
    """
    return data


_MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
    1: _migrate_from_1,
}


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Normaliza um dict cru para o esquema atual, rodando as migrações em ordem."""
    version = int(raw.get("version", 1))
    if version == SCHEMA_VERSION:
        return raw
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"study_session: schema_version={version} maior que o suportado "
            f"({SCHEMA_VERSION}); atualize o Zorin Copilot."
        )
    cur: dict[str, Any] = dict(raw)
    for v in range(version, SCHEMA_VERSION):
        step = _MIGRATIONS.get(v)
        if step is None:
            raise ValueError(f"study_session: sem migração definida de v{v} -> v{v + 1}")
        cur = step(cur)
    cur["version"] = SCHEMA_VERSION
    return cur


@dataclass
class StudySession:
    """Estado de uma sessão de estudo, compartilhado entre modalidades.

    Thread-safe: todos os acessos/mutações passam por ``_lock``. O pub/sub
    (``subscribe`` + ``publish``) notifica inscritos de forma segura; o callback
    recebe ``(evento, payload_dict)`` e é responsável por fazer marshaling para a
    thread de UI se necessário.
    """

    problem_statement: str = ""
    givens: str = ""          # 已知
    unknown: str = ""         # 求
    answer: str = ""          # 答
    discipline: str = "Matemática"
    rigor_mode: str = "BR"    # "BR" padrão | "CN" rigoroso
    modality: str = "chat"    # "chat" | "voice" | "video"
    blackboard: Any | None = None  # SolutionGraph (objeto ou dict serializado)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._subs: list[Callable[[str, dict[str, Any]], None]] = []

    # -- pub/sub ------------------------------------------------------------- #
    def subscribe(self, cb: Callable[[str, dict[str, Any]], None]) -> None:
        """Inscreve ``cb(evento, payload)``. Idempotente por identidade."""
        with self._lock:
            if cb not in self._subs:
                self._subs.append(cb)

    def unsubscribe(self, cb: Callable[[str, dict[str, Any]], None]) -> None:
        with self._lock:
            if cb in self._subs:
                self._subs.remove(cb)

    def publish(self, event: str, **payload: Any) -> None:
        """Notifica os inscritos (fora do lock, para evitar deadlock)."""
        with self._lock:
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(event, payload)
            except Exception:  # pragma: no cover - resiliência do subscriber
                logger.exception("StudySession subscriber falhou para %s", event)

    # -- mutações (cada uma emite seu evento) ------------------------------- #
    def set_problem(
        self,
        *,
        problem_statement: str,
        givens: str = "",
        unknown: str = "",
        answer: str = "",
        blackboard: Any | None = None,
        modality: str | None = None,
    ) -> None:
        with self._lock:
            self.problem_statement = problem_statement
            self.givens = givens
            self.unknown = unknown
            self.answer = answer
            self.blackboard = blackboard
            if modality is not None:
                self.modality = modality
            self.updated_at = time.time()
        self.publish(
            STUDY_EVENT_PROBLEM_SET,
            problem_statement=problem_statement,
            givens=givens,
            unknown=unknown,
            answer=answer,
        )

    def set_modality(self, modality: str) -> None:
        with self._lock:
            if self.modality == modality:
                return
            self.modality = modality
            self.updated_at = time.time()
        self.publish(STUDY_EVENT_PROBLEM_SET, modality=modality)  # re-publica contexto

    def highlight_step(self, node_id: str) -> None:
        with self._lock:
            self.updated_at = time.time()
        self.publish(STUDY_EVENT_STEP_HIGHLIGHTED, node_id=node_id)

    def select_method(self, method_id: str) -> None:
        with self._lock:
            self.updated_at = time.time()
        self.publish(STUDY_EVENT_METHOD_SELECTED, method_id=method_id)

    def expand_blackboard(self) -> None:
        with self._lock:
            self.updated_at = time.time()
        self.publish(STUDY_EVENT_BLACKBOARD_EXPANDED)

    def mark_verification(self, passed: bool) -> None:
        with self._lock:
            self.updated_at = time.time()
        self.publish(STUDY_EVENT_VERIFICATION_DONE, passed=passed)

    # -- acesso concorrente ao quadro --------------------------------------- #
    def get_blackboard(self) -> Any | None:
        """Devolve o SolutionGraph reconstruído (ou None).

        Import tardio de ``ai.math_solver`` para manter este módulo puro (sem
        dependência de ``core -> ai`` no import). Se ``blackboard`` já for um
        objeto com ``methods``, é devolvido direto.
        """
        with self._lock:
            b = self.blackboard
        if b is None or hasattr(b, "methods"):
            return b
        if isinstance(b, dict):
            try:
                from ..ai.math_solver import solution_graph_from_dict

                return solution_graph_from_dict(b)
            except Exception:  # pragma: no cover - depende de ambiente
                logger.debug("não foi possível reconstruir o SolutionGraph do blackboard")
                return b
        return b

    # -- serialização versionada ------------------------------------------- #
    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            blackboard = self.blackboard
            if hasattr(blackboard, "to_dict"):
                blackboard = blackboard.to_dict()
            return {
                "version": SCHEMA_VERSION,
                "problem_statement": self.problem_statement,
                "givens": self.givens,
                "unknown": self.unknown,
                "answer": self.answer,
                "discipline": self.discipline,
                "rigor_mode": self.rigor_mode,
                "modality": self.modality,
                "blackboard": blackboard,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StudySession":
        data = _migrate(data)
        return cls(
            problem_statement=data.get("problem_statement", ""),
            givens=data.get("givens", ""),
            unknown=data.get("unknown", ""),
            answer=data.get("answer", ""),
            discipline=data.get("discipline", "Matemática"),
            rigor_mode=data.get("rigor_mode", "BR"),
            modality=data.get("modality", "chat"),
            blackboard=data.get("blackboard"),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
        )

    # -- persistência (atômica via write-temp + replace) -------------------- #
    def save(self, path: str | os.PathLike[str]) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "StudySession":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)
