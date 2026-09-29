# Decisão de design: Confirmação e Verificação de Liberação de Porta
# Complementa `release_local_port` em network_actions.py. Enquanto aquele envia
# os sinais (SIGTERM/SIGKILL) e mata o processo, este módulo **confirma**:
#   1. O processo-alvo realmente saiu (PID gone / zumbi / reutilizado).
#   2. A porta TCP/UDP correspondente foi liberada pelo kernel — testada com
#      bind real na **interface correta** (IPv4 ou IPv6, conforme o ip_binding
#      registrado) e corroborada por `ss` quando o bind é inconclusivo.
# Notas de precisão (S1):
#   - Um processo zumbi (estado Z) JÁ liberou seus sockets — o kernel fecha os
#     descritores quando o processo sai; o estado Z só retém o task_struct para
#     o pai colher o status. Portanto zumbi + porta livre é o caso normal.
#   - TIME_WAIT pertence ao lado *ativo* de uma conexão encerrada, nunca a um
#     socket de escuta. Ao fechar um LISTEN, a porta é liberada imediatamente.
# Segue o contrato infra-safety-standard: leitura default, **não** envia sinais,
# apenas classifica o estado, reporta evidências e sugere próximo passo.

"""Confirmação e verificação de encerramento de processo e liberação de porta."""

from __future__ import annotations

import enum
import errno
import logging
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class Messages:
    """Catálogo único das mensagens de usuário (pt-BR) — S9.

    O app é **pt-BR-only** hoje (não há gettext/`locale` em lugar algum), então
    não se introduz um pipeline de tradução só neste módulo. Em vez disso, todas
    as strings voltadas ao usuário ficam **centralizadas aqui**, o que:
      1. garante consistência (uma fonte por mensagem);
      2. facilita testes (asserções contra constantes);
      3. torna a extração para gettext/`.po` trivial se/quando a i18n for
         adotada no app (basta envolver cada template num `_()`).

    Ao adicionar mensagem nova, adicione aqui — não espalhe f-strings pelo
    módulo. Ver `docs/fronteira-core-skills.md`.
    """

    # summary
    PORT_FREED = "Porta {port}/{protocol} confirmada livre em {ms:.0f}ms."
    PID_TERMINATED = " PID {pid} encerrado."
    PID_STILL_ALIVE = "Processo-alvo (PID {pid}) ainda ativo; porta ocupada."
    PORT_OCCUPIED = "Porta {port}/{protocol} ainda ocupada{occupant}."
    OCCUPANT_SUFFIX = " (PID {pid})"

    # next_steps
    STEP_PORT_FREED = "Porta liberada. Pode ser reusada pelo consumidor."
    STEP_ZOMBIE_OK = (
        "PID em zumbi não retém socket — nenhuma ação necessária; "
        "o processo-pai fará wait() e o kernel recolhe o status."
    )
    STEP_SIGKILL = "Enviar SIGKILL para forçar encerramento (release_local_port(..., force=True))."
    STEP_INVESTIGATE_SIGTERM = "Investigar por que o processo ignora SIGTERM (handler ou I/O bloqueante)."
    STEP_ZOMBIE_OCCUPIED = (
        "Zumbi não retém a porta — o socket real é de outro processo. "
        "Ver occupying_pid e investigar quem de fato escuta."
    )
    STEP_PID_REUSED = (
        "PID {pid} foi reutilizado por '{name}'. Verifique se o novo dono é o serviço esperado."
    )
    STEP_OTHER_OCCUPANT = "Outro processo (PID {pid}) assume a porta — investigar antes de reusar."
    STEP_WAIT_KERNEL = "Aguardar o kernel liberar o socket e reexecutar a verificação."
    STEP_PRIVILEGE = "Não foi possível testar (<1024 ou sem capability) — usar `ss`/`lsof` como root."
    STEP_UNKNOWN = "Estado inconclusivo — reexecutar a verificação em alguns segundos."


class PidState(str, enum.Enum):
    GONE = "gone"                       # /proc/<pid> não existe mais
    STILL_ALIVE = "still_alive"         # processo em estado R/S/D
    ZOMBIE = "zombie"                   # estado Z — já saiu, sockets liberados
    DIFFERENT_PROCESS = "different_process"  # PID foi reutilizado por outro
    UNKNOWN = "unknown"


class PortState(str, enum.Enum):
    FREE = "free"                        # bind() succeeded
    OCCUPIED = "occupied"                # EADDRINUSE no bind() (ou ss com listener)
    PRIVILEGE = "insufficient_privilege" # <1024 sem capability; ss indisponível
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PortTerminationReport:
    """Resultado consolidado da verificação de encerramento + liberação."""

    port: int
    protocol: str                          # "tcp" | "udp"
    expected_pid: int | None
    expected_process: str | None
    ip_binding: str | None                 # interface/endereço registrado pelo ss
    pid_state: PidState
    pid_actual: str | None                 # comm do /proc/<pid>/stat se existe
    port_state: PortState
    occupying_pid: int | None              # quem está na porta agora (via ss)
    evidence: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: float = 0.0

    @property
    def freed(self) -> bool:
        """Verdadeiro quando o kernel aceita novo bind — critério operacional."""
        return self.port_state == PortState.FREE

    @property
    def summary(self) -> str:
        if self.freed:
            base = Messages.PORT_FREED.format(
                port=self.port, protocol=self.protocol, ms=self.elapsed_ms,
            )
            if self.expected_pid is not None and self.pid_state == PidState.GONE:
                base += Messages.PID_TERMINATED.format(pid=self.expected_pid)
            return base
        if self.pid_state == PidState.STILL_ALIVE:
            return Messages.PID_STILL_ALIVE.format(pid=self.expected_pid)
        occupant = (
            Messages.OCCUPANT_SUFFIX.format(pid=self.occupying_pid)
            if self.occupying_pid
            else ""
        )
        return Messages.PORT_OCCUPIED.format(
            port=self.port, protocol=self.protocol, occupant=occupant,
        )

    @property
    def next_steps(self) -> list[str]:
        steps: list[str] = []
        if self.freed:
            steps.append(Messages.STEP_PORT_FREED)
            if self.pid_state == PidState.ZOMBIE:
                steps.append(Messages.STEP_ZOMBIE_OK)
        else:
            if self.pid_state == PidState.STILL_ALIVE:
                steps.append(Messages.STEP_SIGKILL)
                steps.append(Messages.STEP_INVESTIGATE_SIGTERM)
            elif self.pid_state == PidState.ZOMBIE:
                steps.append(Messages.STEP_ZOMBIE_OCCUPIED)
            elif self.pid_state == PidState.DIFFERENT_PROCESS:
                steps.append(
                    Messages.STEP_PID_REUSED.format(
                        pid=self.expected_pid, name=self.pid_actual,
                    )
                )
            if self.port_state == PortState.OCCUPIED:
                if self.occupying_pid and self.occupying_pid != self.expected_pid:
                    steps.append(Messages.STEP_OTHER_OCCUPANT.format(pid=self.occupying_pid))
                else:
                    steps.append(Messages.STEP_WAIT_KERNEL)
            elif self.port_state == PortState.PRIVILEGE:
                steps.append(Messages.STEP_PRIVILEGE)
            elif self.port_state == PortState.UNKNOWN:
                steps.append(Messages.STEP_UNKNOWN)
        return steps


# ---------------------------------------------------------------------------
# Classificação do PID
# ---------------------------------------------------------------------------

def _pid_exists(pid: int) -> bool:
    """Testa existência do PID sem enviar sinal (equivalente a `kill -0`)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # existe, só não somos dono


def _read_proc_state(pid: int) -> tuple[str, str]:
    """Lê /proc/<pid>/stat e devolve (state_letter, comm)."""
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as fh:
            content = fh.read()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return ("?", "?")
    lpar = content.find("(")
    rpar = content.rfind(")")
    if lpar < 0 or rpar <= lpar:
        return ("?", "?")
    name = content[lpar + 1:rpar]
    state = content[rpar + 2] if rpar + 2 < len(content) else "?"
    return (state, name)


def classify_pid_state(
    pid: int,
    expected_name: str | None = None,
) -> tuple[PidState, str | None]:
    """Classifica o estado de um PID (gone / still_alive / zombie / reused).

    Obs.: `comm` em /proc/<pid>/stat é truncado em 15 caracteres (TASK_COMM_LEN),
    então `expected_name` deve ser comparado com essa limitação em mente.
    """
    if pid <= 1:
        return (PidState.UNKNOWN, None)
    if not _pid_exists(pid):
        return (PidState.GONE, None)
    state, name = _read_proc_state(pid)
    if state == "Z":
        return (PidState.ZOMBIE, name or None)
    if expected_name and name and name != expected_name:
        return (PidState.DIFFERENT_PROCESS, name)
    return (PidState.STILL_ALIVE, name or None)


# ---------------------------------------------------------------------------
# Resolução do alvo de bind (IPv4 / IPv6 / interface específica) — S2
# ---------------------------------------------------------------------------

def _bind_targets_for(ip_binding: str | None) -> list[tuple[int, str]]:
    """Traduz um `ip_binding` do ss em alvos (family, endereço) para o bind.

    - wildcard IPv4 (`0.0.0.0`, `*`, vazio)  -> bind em 0.0.0.0
    - wildcard IPv6 (`::`)                    -> bind em ::
    - loopback (`127.0.0.1`, `localhost`)     -> bind em 127.0.0.1
    - loopback IPv6 (`::1`)                    -> bind em ::1
    - IP concreto                             -> bind no próprio IP (v4 ou v6)
    """
    b = (ip_binding or "").strip()
    if b in ("", "0.0.0.0", "*"):
        return [(socket.AF_INET, "0.0.0.0")]
    if b == "::":
        return [(socket.AF_INET6, "::")]
    if b in ("127.0.0.1", "localhost"):
        return [(socket.AF_INET, "127.0.0.1")]
    if b == "::1":
        return [(socket.AF_INET6, "::1")]
    if ":" in b:
        return [(socket.AF_INET6, b)]
    return [(socket.AF_INET, b)]


def _target_from_bind_ip(bind_ip: str) -> tuple[int, str]:
    """Deriva (family, endereço) de um bind_ip explícito."""
    return (socket.AF_INET6, bind_ip) if ":" in bind_ip else (socket.AF_INET, bind_ip)


# ---------------------------------------------------------------------------
# Verificação da porta (bind test + ss)
# ---------------------------------------------------------------------------

def _try_bind_target(
    port: int,
    protocol: str,
    family: int,
    bind_ip: str,
) -> tuple[PortState, str]:
    """Tenta bind num alvo único; sucesso = livre, EADDRINUSE = ocupada."""
    if protocol not in ("tcp", "udp"):
        return (PortState.UNKNOWN, f"protocolo inválido: {protocol}")
    stype = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
    try:
        with socket.socket(family, stype) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(0.5)
            sock.bind((bind_ip, port))
            return (PortState.FREE, f"bind({bind_ip}:{port}/{protocol}) succeeded")
    except PermissionError as exc:
        return (PortState.PRIVILEGE, f"sem privilégio para bind em {bind_ip}:{port}: {exc}")
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            return (PortState.OCCUPIED, f"bind({bind_ip}:{port}/{protocol}) recusado: EADDRINUSE")
        if exc.errno == errno.EACCES:
            return (PortState.PRIVILEGE, f"bind({bind_ip}:{port}/{protocol}) sem permissão: EACCES")
        if exc.errno in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL):
            return (PortState.UNKNOWN, f"família/endereço indisponível para bind: {exc}")
        return (PortState.UNKNOWN, f"falha inesperada no bind: {exc}")
    except Exception as exc:  # pragma: no cover — rede defensiva
        return (PortState.UNKNOWN, f"erro no bind: {exc}")


def _try_bind(
    port: int,
    protocol: str,
    targets: list[tuple[int, str]],
) -> tuple[PortState, str]:
    """Tenta bind em cada alvo; FREE só se algum alvo aceitar o bind."""
    last_state, last_msg = PortState.UNKNOWN, ""
    for family, addr in targets:
        state, msg = _try_bind_target(port, protocol, family, addr)
        if state == PortState.FREE:
            return (state, msg)
        last_state, last_msg = state, msg
    return (last_state, last_msg)


def _ss_lookup(port: int, protocol: str = "tcp") -> tuple[bool, bool, int | None]:
    """Consulta `ss` para a porta. Retorna (ss_disponivel, tem_listener, pid)."""
    flag = "-ltnp" if protocol == "tcp" else "-lunp"
    cmd = ["ss", flag, f"sport = :{port}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1.0)
    except FileNotFoundError:
        return (False, False, None)
    except subprocess.TimeoutExpired:
        return (False, False, None)
    if proc.returncode != 0:
        return (True, False, None)
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    data_lines = lines[1:] if lines else []
    has_listener = len(data_lines) > 0
    match = re.search(r"pid=(\d+)", proc.stdout or "")
    pid = int(match.group(1)) if match else None
    return (True, has_listener, pid)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def confirm_port_freed(
    port: int,
    protocol: str = "tcp",
    expected_pid: int | None = None,
    expected_process: str | None = None,
    ip_binding: str | None = None,
    bind_ip: str | None = None,
    retries: int = 3,
    retry_delay: float = 0.1,
) -> PortTerminationReport:
    """Confirma encerramento do processo e liberação da porta.

    Estratégia em 3 camadas:
      1. Classifica o PID esperado (gone / still_alive / zombie / reused).
      2. Tenta bind real no alvo correto — derivado de `ip_binding` (IPv4/IPv6/
         interface específica) ou de `bind_ip` explícito. Default: 127.0.0.1.
      3. Quando o bind é inconclusivo (porta privilegiada), usa `ss` como fonte
         de verdade para decidir entre ocupada e livre.

    Parâmetros:
      ip_binding: endereço registrado pelo inspetor (ex.: "0.0.0.0", "::1",
        "192.168.15.14"). Se ausente, assume loopback IPv4.
      bind_ip: força um endereço de bind específico (sobrepõe ip_binding).

    Retorna um PortTerminationReport com propriedades derived:
      `.freed`, `.summary`, `.next_steps` (próximo passo sugerido).
    """
    started = time.monotonic()

    if bind_ip:
        targets: list[tuple[int, str]] = [_target_from_bind_ip(bind_ip)]
    else:
        targets = _bind_targets_for(ip_binding)

    pid_state: PidState = PidState.UNKNOWN
    pid_actual: str | None = None
    if expected_pid is not None and expected_pid > 0:
        pid_state, pid_actual = classify_pid_state(expected_pid, expected_process)

    port_state: PortState = PortState.UNKNOWN
    last_msg = ""
    attempts = max(1, retries)
    for i in range(attempts):
        port_state, last_msg = _try_bind(port, protocol, targets)
        if port_state == PortState.FREE:
            break
        if port_state == PortState.OCCUPIED and i < attempts - 1:
            time.sleep(retry_delay)
            continue
        break

    occupying_pid: int | None = None
    if port_state == PortState.OCCUPIED:
        _, _, occupying_pid = _ss_lookup(port, protocol)
    elif port_state == PortState.PRIVILEGE:
        # S3: bind inconclusivo (porta privilegiada). ss decide.
        ss_avail, has_listener, pid = _ss_lookup(port, protocol)
        if has_listener:
            port_state = PortState.OCCUPIED
            occupying_pid = pid
        elif ss_avail:
            port_state = PortState.FREE  # porta privilegiada, sem listener
        # se ss indisponível, mantém PRIVILEGE

    elapsed_ms = (time.monotonic() - started) * 1000.0

    evidence: dict[str, Any] = {
        "bind_test": last_msg,
        "bind_targets": [f"{addr}:{port}/{protocol}" for _, addr in targets],
        "retries": retries,
    }
    if occupying_pid is not None:
        evidence["occupying_pid"] = occupying_pid

    return PortTerminationReport(
        port=port,
        protocol=protocol,
        expected_pid=expected_pid,
        expected_process=expected_process,
        ip_binding=ip_binding,
        pid_state=pid_state,
        pid_actual=pid_actual,
        port_state=port_state,
        occupying_pid=occupying_pid,
        evidence=evidence,
        elapsed_ms=elapsed_ms,
    )


def wait_until_port_freed(
    port: int,
    protocol: str = "tcp",
    expected_pid: int | None = None,
    expected_process: str | None = None,
    ip_binding: str | None = None,
    bind_ip: str | None = None,
    timeout_sec: float = 2.0,
    poll_interval: float = 0.1,
) -> PortTerminationReport:
    """Faz polling até a porta ser confirmada livre ou o timeout expirar."""
    deadline = time.monotonic() + max(0.1, timeout_sec)
    report = confirm_port_freed(
        port, protocol, expected_pid, expected_process, ip_binding, bind_ip,
    )
    while not report.freed and time.monotonic() < deadline:
        time.sleep(poll_interval)
        report = confirm_port_freed(
            port, protocol, expected_pid, expected_process, ip_binding, bind_ip,
        )
    return report


# ---------------------------------------------------------------------------
# Direções de extensão (documentação operacional — não executa nada aqui)
# ---------------------------------------------------------------------------
# 1. Monitoramento de múltiplas portas
#    Construa um mapa {(port, proto) -> PortTerminationReport}, passando o
#    ip_binding de cada porta (IPv4/IPv6) para uma verificação correta:
#        reports = {
#            (p.port, p.protocol): confirm_port_freed(
#                p.port, p.protocol, p.pid, p.process_name, ip_binding=p.ip_binding,
#            )
#            for p in NetworkInspector.get_listening_ports()
#        }
#        unhealth = {k: v for k, v in reports.items() if not v.freed}
#
# 2. Auditoria contínua (watcher)
#    Encadear em QTimer/thread que roda confirm_port_freed() periodicamente
#    sobre uma whitelist de portas críticas. Alertar (log, DBus, notificação
#    nativa do GNOME) quando o estado mudar de FREE para OCCUPIED de forma
#    inesperada — útil para detectar serviços que sobem sozinhos após
#    release_local_port, ou para monitorar portas de banco que deveriam
#    estar fechadas.
#
# 3. Integração com ferramentas de diagnóstico
#    - `ss -ltnp` / `ss -lunp`     → confirmar ocupação e dono (já usado).
#    - `lsof -iTCP:<port> -sTCP:LISTEN` → alternativa legada; útil quando
#      `ss` não está disponível ou em macOS (lsof é nativo).
#    - `fuser -v <port>/tcp`        → mostra o PID de forma direta e permite
#      encerrar in-loco (fuser -k) como último recurso.
#    - `mtr` / `traceroute` / `ping` → correlacionar a porta de serviço com
#      hop problemático quando o serviço não sobe. Combinar com
#      diagnose_connection_path() para isolar carrier/gateway/DNS.
#    - `ss -o` (timers)             → mostra timers de conexão. Obs.: TIME_WAIT
#      é do lado ativo de uma conexão, NÃO de um socket de escuta — ao fechar
#      um LISTEN a porta é liberada imediatamente, então não há "resíduo" a
#      esperar. O timer serve para inspecionar conexões em fechamento.
#    - Prometheus Pushgateway       → exportar PortTerminationReport como
#      métricas (port_up=1/0, port_occupation_seconds) para painéis e
#      alertas de longo prazo.
#    - `netstat -anp` (fallback)    → em sistemas sem `ss`, ainda funciona.
#    - Hooks com `probe_service()` (já existente em network_actions.py):
#      depois de confirmar a liberação, faça probe no host:porta para
#      certificar que o serviço de fato respondeu ou recusa conexão.