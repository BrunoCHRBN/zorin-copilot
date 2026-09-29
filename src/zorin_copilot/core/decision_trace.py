"""Rastro de decisão (decision trace) — observabilidade do raciocínio.

Módulo puro (só stdlib), sem GTK/rede. Registra, de forma thread-safe, cada passo
decisório de uma cadeia de raciocínio para que seja possível reconstruir *por que*
o assistente tomou uma decisão (lacuna de §6.2 da análise de raciocínio).

O trace é transportado por uma ``contextvars.ContextVar``: basta chamar
``set_trace(t)`` no início de uma operação de raciocínio (na thread que a executa)
e todos os módulos downstream gravam no trace ativo, sem precisar passar o objeto
por dezenas de parâmetros. Quando não há trace ativo, ``record()`` é no-op
(custo negligenciável no caminho puramente determinístico).

Cada entrada (span) carrega: instante monotonic, nome do passo, tipo e um payload
arbitrário (dict). O trace inteiro vira JSON para telemetria/persistência.
"""

from __future__ import annotations

import contextvars
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

_TRACE_VERSION = 1


@dataclass
class TraceSpan:
    ts: float
    step: str
    kind: str
    data: dict = field(default_factory=dict)
    span: str = ""

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "span": self.span,
            "step": self.step,
            "kind": self.kind,
            "data": self.data,
        }


class DecisionTrace:
    """Coleção ordenada e thread-safe de passos decisórios de uma cadeia."""

    def __init__(self, trace_id: Optional[str] = None, root: str = "reasoning"):
        self.trace_id = trace_id or uuid.uuid4().hex
        self.root = root
        self._lock = threading.RLock()
        self._entries: list[TraceSpan] = []
        self.created_at = time.monotonic()

    def record(self, step: str, kind: str, span: str = "", **data: Any) -> None:
        entry = TraceSpan(
            ts=time.monotonic(),
            step=step,
            kind=kind,
            data=dict(data),
            span=span,
        )
        with self._lock:
            self._entries.append(entry)

    def as_dict(self) -> dict:
        with self._lock:
            entries = [e.to_dict() for e in self._entries]
        return {
            "version": _TRACE_VERSION,
            "trace_id": self.trace_id,
            "root": self.root,
            "entries": entries,
        }

    def to_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "DecisionTrace":
        """Reconstrói um trace persistido (versão atual suportada apenas)."""
        if d.get("version") != _TRACE_VERSION:
            raise ValueError(f"DecisionTrace versão {d.get('version')} não suportada")
        t = cls(trace_id=d.get("trace_id"), root=d.get("root", "reasoning"))
        with t._lock:
            for e in d.get("entries", []):
                t._entries.append(TraceSpan(
                    ts=e.get("ts", 0.0),
                    step=e.get("step", ""),
                    kind=e.get("kind", ""),
                    data=dict(e.get("data") or {}),
                    span=e.get("span", ""),
                ))
        return t

    def steps(self) -> list[str]:
        """Nomes dos passos na ordem em que foram gravados (para asserções de roteamento)."""
        with self._lock:
            return [e.step for e in self._entries]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


_active_trace: "contextvars.ContextVar[Optional[DecisionTrace]]" = \
    contextvars.ContextVar("zorin_decision_trace", default=None)


def set_trace(trace: Optional[DecisionTrace]) -> "contextvars.Token":
    """Torna ``trace`` o trace ativo nesta contexto; devolve o token para reset."""
    return _active_trace.set(trace)


def reset_trace(token: "contextvars.Token") -> None:
    """Restaura o trace ativo ao valor anterior (use no ``finally``)."""
    _active_trace.reset(token)


def get_trace() -> Optional[DecisionTrace]:
    return _active_trace.get()


def record(step: str, kind: str, span: str = "", **data: Any) -> None:
    """Atalho: grava no trace ativo. No-op se nenhum trace estiver ativo."""
    t = _active_trace.get()
    if t is not None:
        t.record(step, kind, span=span, **data)
