# Decisão de design: Auditoria Contínua de Portas (Watcher) — S8
# Observa um conjunto de portas e detecta TRANSIÇÕES de estado usando
# confirm_port_freed() (port_termination.py) como fonte de verdade.
# UI-agnóstico e sem dependências externas: pode ser dirigido por thread,
# GLib.timeout_add (GTK) ou QTimer. Segue infra-safety-standard — é
# SOMENTE LEITURA, nunca encerra processos.
# Exporta snapshot no formato de texto do Prometheus para Pushgateway/exporter.

"""Auditoria contínua de portas: watcher de transições + export Prometheus."""

from __future__ import annotations

import enum
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .port_termination import PortTerminationReport, confirm_port_freed

logger = logging.getLogger(__name__)


class PortAuditEventKind(str, enum.Enum):
    BECAME_OCCUPIED = "became_occupied"  # estava livre, agora ocupada (alerta)
    BECAME_FREE = "became_free"          # estava ocupada, agora livre


@dataclass(frozen=True)
class PortSpec:
    """Especificação de uma porta a auditar."""

    port: int
    protocol: str = "tcp"
    ip_binding: str | None = None
    process_name: str | None = None
    label: str = ""

    @property
    def key(self) -> tuple[int, str]:
        return (self.port, self.protocol)


@dataclass(frozen=True)
class PortAuditEvent:
    """Transição de estado detectada numa porta."""

    spec: PortSpec
    kind: PortAuditEventKind
    freed: bool
    summary: str
    timestamp: float = field(default_factory=time.time)


#: Assinatura do verificador injetável (default: confirm_port_freed).
CheckFn = Callable[..., PortTerminationReport]


class PortAuditWatcher:
    """Observa portas e emite eventos APENAS quando o estado muda.

    O `checker` é injetável para permitir testes determinísticos sem tocar em
    portas reais. O default é `confirm_port_freed` (leitura real).
    """

    def __init__(
        self,
        specs: Iterable[PortSpec],
        checker: CheckFn = confirm_port_freed,
        interval: float = 5.0,
    ) -> None:
        self._specs = list(specs)
        self._checker = checker
        self._interval = max(0.5, interval)
        self._last: dict[tuple[int, str], bool] = {}
        self._stop = threading.Event()

    @property
    def specs(self) -> list[PortSpec]:
        return list(self._specs)

    def _check(self, spec: PortSpec) -> PortTerminationReport:
        return self._checker(
            spec.port,
            spec.protocol,
            expected_process=spec.process_name,
            ip_binding=spec.ip_binding,
        )

    def poll(self) -> list[PortAuditEvent]:
        """Verifica todas as portas e retorna apenas as transições detectadas.

        A primeira verificação de cada porta apenas estabelece a linha de base
        (não emite evento).
        """
        events: list[PortAuditEvent] = []
        for spec in self._specs:
            try:
                report = self._check(spec)
            except Exception as exc:  # pragma: no cover — rede defensiva
                logger.warning("Falha ao auditar porta %s: %s", spec.port, exc)
                continue
            freed = bool(getattr(report, "freed", False))
            summary = getattr(report, "summary", "")
            prev = self._last.get(spec.key)
            if prev is not None and prev != freed:
                kind = (
                    PortAuditEventKind.BECAME_OCCUPIED
                    if not freed
                    else PortAuditEventKind.BECAME_FREE
                )
                events.append(
                    PortAuditEvent(spec=spec, kind=kind, freed=freed, summary=summary)
                )
            self._last[spec.key] = freed
        return events

    def run(self, on_event: Callable[[PortAuditEvent], None]) -> None:
        """Loop de auditoria (bloqueante) até `stop()` ser chamado."""
        while not self._stop.is_set():
            for event in self.poll():
                try:
                    on_event(event)
                except Exception as exc:  # pragma: no cover — handler do chamador
                    logger.warning("Handler de evento de auditoria falhou: %s", exc)
            self._stop.wait(self._interval)

    def start(self, on_event: Callable[[PortAuditEvent], None]) -> threading.Thread:
        """Inicia o loop numa thread daemon e devolve a thread."""
        thread = threading.Thread(target=self.run, args=(on_event,), daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()

    def reset(self) -> None:
        """Limpa o estado observado (nova linha de base no próximo poll)."""
        self._last.clear()
        self._stop.clear()


def check_once(
    specs: Iterable[PortSpec],
    checker: CheckFn = confirm_port_freed,
) -> dict[tuple[int, str], PortTerminationReport]:
    """Verifica uma vez e retorna {key: report} para todas as portas."""
    result: dict[tuple[int, str], PortTerminationReport] = {}
    for spec in specs:
        try:
            result[spec.key] = checker(
                spec.port,
                spec.protocol,
                expected_process=spec.process_name,
                ip_binding=spec.ip_binding,
            )
        except Exception as exc:  # pragma: no cover — rede defensiva
            logger.warning("Falha ao verificar porta %s: %s", spec.port, exc)
    return result


def to_prometheus(
    reports: dict[tuple[int, str], PortTerminationReport],
    namespace: str = "zorin_port",
) -> str:
    """Serializa os relatórios no formato de exposição Prometheus (texto).

    Métrica `zorin_port_free{port,protocol}`: 1 = livre, 0 = ocupada.
    Pronta para Pushgateway ou exporter de arquivo. Sem dependências externas.
    """
    lines = [
        f"# HELP {namespace}_free 1 se a porta esta livre, 0 se ocupada",
        f"# TYPE {namespace}_free gauge",
    ]
    for (port, protocol), report in sorted(reports.items()):
        value = 1 if getattr(report, "freed", False) else 0
        lines.append(
            f'{namespace}_free{{port="{port}",protocol="{protocol}"}} {value}'
        )
    return "\n".join(lines) + "\n"
