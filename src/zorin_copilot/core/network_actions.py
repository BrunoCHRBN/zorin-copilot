# Decisão de design: Motor Operacional de Ações de Rede (Network Actions Engine).
# Executa ações ativas sobre a infraestrutura de rede local com proteção estrita:
# 1. Zero Root / Sudo: Todas as ações utilizam sockets padrão de usuário ou D-Bus Polkit.
# 2. Proteção de Processos: Blacklist imutável bloqueia finalização de processos vitais do SO.
# 3. Anti-Race Condition: Validação de PID (/proc/<pid>/cmdline e stat) antes de qualquer sinal.
# 4. Non-blocking Sockets: Timeouts agressivos (300-800ms) impedem bloqueio de threads.
# 5. Zero-Knowledge: Mantém a sanitização e máscara de endereços físicos.

"""Motor de ações ativas e remediação de rede do Zorin Copilot."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import socket
import stat
import struct
import subprocess
import time
from pathlib import Path
from typing import Any

from .network_inspector import DeviceInventory, NetworkInspector

logger = logging.getLogger(__name__)

#: Processos protegidos que o agente NUNCA pode encerrar sob hipótese alguma.
PROTECTED_PROCESS_NAMES = {
    "systemd",
    "dbus-daemon",
    "dbus-broker",
    "hyprland",
    "hyprlock",
    "waybar",
    "pipewire",
    "wireplumber",
    "pipewire-pulse",
    "sshd",
    "zorin-copilot",
    "python3-zorin-copilot",
    "Xwayland",
    "polkitd",
    "login",
    "init",
    "udevd",
    "systemd-udevd",
    "systemd-journald",
}


def _normalize_mac(mac: str) -> bytes:
    """Normaliza e valida endereço MAC (formato XX:XX:XX:XX:XX:XX ou XX-XX-XX-XX-XX-XX)."""
    clean = re.sub(r"[^0-9a-fA-F]", "", mac.strip())
    if len(clean) != 12:
        raise ValueError(f"Formato de MAC address inválido: '{mac}'. Esperados 12 dígitos hexadecimais.")
    return bytes.fromhex(clean)


def wake_on_lan(
    mac: str,
    broadcast_ip: str = "255.255.255.255",
    port: int = 9,
    burst_count: int = 3,
) -> tuple[bool, str]:
    """Envia Magic Packet UDP para acordar dispositivo via Wake-on-LAN.

    Não requer privilégios de root (socket UDP de usuário com SO_BROADCAST).
    """
    try:
        mac_bytes = _normalize_mac(mac)
    except ValueError as exc:
        return False, str(exc)

    # Magic Packet: 6 bytes 0xFF seguidos por 16 repetições do MAC alvo (102 bytes total)
    packet = b"\xff" * 6 + mac_bytes * 16

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            # Envia rajada controlada com 40ms de intervalo
            for i in range(burst_count):
                sock.sendto(packet, (broadcast_ip, port))
                if i < burst_count - 1:
                    time.sleep(0.04)

        masked = NetworkInspector.mask_mac(mac)
        return True, f"Magic packet de Wake-on-LAN enviado para {masked} via {broadcast_ip}:{port}."
    except Exception as exc:
        logger.warning("Falha ao enviar Wake-on-LAN para %s: %s", mac, exc)
        return False, f"Falha ao enviar pacote Wake-on-LAN: {exc}"


def _parse_systemd_unit_from_cgroup(cgroup_content: str) -> str | None:
    """Extrai o nome do unit `.service` de um conteúdo de /proc/<pid>/cgroup.

    Ex.: '0::/user.slice/user-1000.slice/user@1000.service/app.slice/aigis0-ui.service'
    -> 'aigis0-ui.service'. Retorna None se não houver unit systemd.

    Escolhe o `.service` mais específico (último da hierarquia) e **descarta o
    gerenciador de sessão** `user@<uid>.service` — pará-lo derrubaria toda a
    sessão do usuário e nunca é o alvo correto.
    """
    units = [
        part.strip()
        for part in cgroup_content.replace("\n", "/").split("/")
        if part.strip().endswith(".service")
    ]
    units = [u for u in units if not u.startswith("user@")]
    return units[-1] if units else None


def _is_user_unit_from_cgroup(cgroup_content: str, uid: int) -> bool:
    """Heurística: unit de usuário se o cgroup contém 'user@<uid>.service'."""
    return f"user@{uid}.service" in cgroup_content


def systemd_unit_for_pid(pid: int) -> tuple[str | None, bool]:
    """Retorna (unit, is_user_unit) para um PID gerenciado por systemd.

    unit=None quando o processo não é gerenciado por systemd.
    """
    try:
        with open(f"/proc/{pid}/cgroup", "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return (None, False)
    if isinstance(content, (bytes, bytearray)):  # robustez a mocks/leitura binária
        content = content.decode("utf-8", errors="replace")
    unit = _parse_systemd_unit_from_cgroup(content)
    is_user = _is_user_unit_from_cgroup(content, os.getuid())
    return (unit, is_user)


def release_local_port(
    port: int,
    target_pid: int | None = None,
    force: bool = False,
) -> tuple[bool, str, dict[str, Any]]:
    """Libera com segurança uma porta de rede ocupada por processo local.

    Executa verificações de segurança:
    1. Verifica se o PID não é de sistema ou protegido (PROTECTED_PROCESS_NAMES).
    2. Detecta se o processo pertence a um serviço systemd: nesse caso matar por
       PID é ineficaz (o systemd pode reiniciá-lo conforme `Restart=`); o correto
       é parar o unit via `systemctl`. Units de usuário são parados diretamente;
       units de sistema exigem privilégio (orienta `sudo systemctl stop`).
    3. Confere se o processo pertence ao usuário corrente (os.getuid()).
    4. Valida identidade do processo via /proc/<pid>/cmdline antes de enviar sinal.
    """
    if port < 1 or port > 65535:
        return False, f"Número de porta inválido: {port}.", {}

    # 1. Identifica o processo escutando na porta se o PID não foi informado
    pid = target_pid
    proc_name = ""
    if pid is None:
        ports = NetworkInspector.get_listening_ports()
        matched = [p for p in ports if p.port == port]
        if not matched:
            return False, f"Nenhum processo escutando na porta {port}.", {}
        pid = matched[0].pid
        proc_name = matched[0].process_name or ""

    if pid is None or pid <= 1:
        return False, f"Não foi possível identificar o PID do processo na porta {port}.", {}

    # 2. Prevenção de Race Condition e Checagem de Existência
    proc_path = f"/proc/{pid}"
    if not os.path.exists(proc_path):
        return False, f"Processo PID {pid} não existe mais no sistema.", {}

    # 3. Leitura da linha de comando atual do processo
    cmdline = ""
    try:
        with open(f"{proc_path}/cmdline", "rb") as fh:
            raw = fh.read().replace(b"\x00", b" ").strip()
            cmdline = raw.decode("utf-8", errors="replace")
    except OSError:
        pass

    effective_name = proc_name or os.path.basename(cmdline.split()[0]) if cmdline else f"PID-{pid}"

    # 4. Blacklist de Processos Vitais do Sistema
    low_cmd = cmdline.lower()
    low_name = effective_name.lower()
    for protected in PROTECTED_PROCESS_NAMES:
        if protected in low_name or protected in low_cmd.split():
            return (
                False,
                f"Ação bloqueada por segurança: o processo '{effective_name}' (PID {pid}) é vital ao sistema operacional.",
                {"pid": pid, "process": effective_name, "port": port},
            )

    # 4b. Processo gerenciado por systemd? Matar por PID é ineficaz.
    unit, is_user_unit = systemd_unit_for_pid(pid)
    if unit:
        if not is_user_unit:
            return (
                False,
                f"O processo '{effective_name}' (PID {pid}) pertence ao serviço systemd de sistema '{unit}'. "
                f"Encerrar por PID é ineficaz (o systemd pode reiniciá-lo). Use: 'sudo systemctl stop {unit}'.",
                {"pid": pid, "process": effective_name, "port": port,
                 "systemd_unit": unit, "systemd_scope": "system"},
            )
        try:
            stop_proc = subprocess.run(
                ["systemctl", "--user", "stop", unit],
                capture_output=True, text=True, timeout=15.0,
            )
        except FileNotFoundError:
            return (
                False,
                f"O processo pertence ao serviço '{unit}', mas 'systemctl' não está disponível. "
                f"Execute manualmente: 'systemctl --user stop {unit}'.",
                {"pid": pid, "process": effective_name, "port": port, "systemd_unit": unit},
            )
        except Exception as exc:
            return (
                False,
                f"Falha ao parar o serviço systemd '{unit}': {exc}",
                {"pid": pid, "process": effective_name, "port": port, "systemd_unit": unit},
            )
        if stop_proc.returncode != 0:
            return (
                False,
                f"Falha ao parar o serviço '{unit}': {stop_proc.stderr.strip() or 'erro desconhecido'}",
                {"pid": pid, "process": effective_name, "port": port, "systemd_unit": unit},
            )
        return (
            True,
            f"Serviço systemd '{unit}' parado; porta {port} liberada.",
            {"pid": pid, "process": effective_name, "port": port,
             "systemd_unit": unit, "alive": False},
        )

    # 5. Verificação de Proprietário do Processo (UID)
    try:
        proc_stat = os.stat(proc_path)
        if proc_stat.st_uid != os.getuid():
            return (
                False,
                f"Permissão insuficiente: o processo '{effective_name}' pertence ao usuário UID {proc_stat.st_uid} (root/sistema). "
                f"Para encerrar manualmente, execute no terminal: 'sudo kill {pid}'.",
                {"pid": pid, "process": effective_name, "port": port, "uid": proc_stat.st_uid},
            )
    except OSError as exc:
        return False, f"Falha ao validar permissões do processo {pid}: {exc}", {}

    # 6. Envio de Término Gradual (SIGTERM primeiro)
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return False, f"Falha ao enviar sinal de término para o processo {pid}: {exc}", {}

    # Aguarda até 500ms para verificar se o processo encerrou
    alive = True
    for _ in range(10):
        time.sleep(0.05)
        if not os.path.exists(proc_path):
            alive = False
            break

    # Se ainda estiver vivo e 'force' habilitado, aplica SIGKILL
    if alive and force:
        try:
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.05)
            alive = os.path.exists(proc_path)
        except OSError:
            pass

    if alive:
        return (
            False,
            f"O processo '{effective_name}' (PID {pid}) recebeu o sinal mas ainda não encerrou.",
            {"pid": pid, "process": effective_name, "port": port, "alive": True},
        )

    return (
        True,
        f"Porta {port} liberada com sucesso. Processo '{effective_name}' (PID {pid}) foi encerrado.",
        {"pid": pid, "process": effective_name, "port": port, "alive": False},
    )


def probe_service(
    host: str,
    port: int,
    timeout_sec: float = 0.5,
) -> dict[str, Any]:
    """Testa handshake TCP e disponibilidade de um serviço na rede local.

    Mede latência em milissegundos sem sobrecarregar a rede ou emitir scans ruidosos.
    """
    if port < 1 or port > 65535:
        return {"ok": False, "status": "invalid_port", "error": f"Porta inválida: {port}"}

    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
            return {
                "ok": True,
                "host": host,
                "port": port,
                "latency_ms": elapsed_ms,
                "status": "online",
            }
    except socket.timeout:
        return {
            "ok": False,
            "host": host,
            "port": port,
            "latency_ms": None,
            "status": "timeout",
            "error": f"Tempo esgotado ({timeout_sec*1000:.0f}ms)",
        }
    except ConnectionRefusedError:
        return {
            "ok": False,
            "host": host,
            "port": port,
            "latency_ms": None,
            "status": "refused",
            "error": "Porta fechada / conexão recusada pelo host",
        }
    except Exception as exc:
        return {
            "ok": False,
            "host": host,
            "port": port,
            "latency_ms": None,
            "status": "error",
            "error": str(exc),
        }


def _build_dns_a_query(domain: str, tx_id: int = 0x5432) -> bytes:
    """Monta um pacote RFC 1035 puro de consulta DNS tipo A (IPv4)."""
    # Header: ID, Flags(0x0100 = Standard Query com Recursão), QDCOUNT=1, ANCOUNT=0, NSCOUNT=0, ARCOUNT=0
    header = struct.pack(">HHHHHH", tx_id, 0x0100, 1, 0, 0, 0)
    qname_parts = []
    for part in domain.split("."):
        if part:
            b = part.encode("ascii", errors="replace")
            qname_parts.append(bytes([len(b)]) + b)
    qname = b"".join(qname_parts) + b"\x00"
    # QTYPE=1 (A), QCLASS=1 (IN)
    footer = struct.pack(">HH", 1, 1)
    return header + qname + footer


def benchmark_dns(
    domains: list[str] | None = None,
    servers: dict[str, str] | None = None,
    timeout_sec: float = 0.8,
) -> list[dict[str, Any]]:
    """Mede a latência comparativa de resolução entre diferentes provedores de DNS.

    Compara servidores públicos (Cloudflare, Google, Quad9) e o resolvedor local.
    """
    test_domains = domains or ["archlinux.org", "kernel.org", "google.com"]

    # Detecta o resolvedor atual
    gw_ip, _ = NetworkInspector.get_default_gateway()
    default_servers = {
        "Roteador / Local": gw_ip or "127.0.0.53",
        "Cloudflare (1.1.1.1)": "1.1.1.1",
        "Google (8.8.8.8)": "8.8.8.8",
        "Quad9 (9.9.9.9)": "9.9.9.9",
    }
    target_servers = servers or default_servers
    results: list[dict[str, Any]] = []

    for name, server_ip in target_servers.items():
        latencies: list[float] = []
        errors = 0

        for dom in test_domains:
            packet = _build_dns_a_query(dom)
            started = time.monotonic()
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(timeout_sec)
                    sock.sendto(packet, (server_ip, 53))
                    data, _ = sock.recvfrom(512)
                    if len(data) >= 12:
                        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                        latencies.append(elapsed_ms)
                    else:
                        errors += 1
            except Exception:
                errors += 1

        avg_ms = round(sum(latencies) / len(latencies), 2) if latencies else None
        results.append(
            {
                "provider": name,
                "server_ip": server_ip,
                "avg_latency_ms": avg_ms,
                "success_rate": round(len(latencies) / len(test_domains), 2),
                "is_fastest": False,
            }
        )

    # Identifica o provedor mais rápido
    valid = [r for r in results if r["avg_latency_ms"] is not None]
    if valid:
        valid.sort(key=lambda x: x["avg_latency_ms"])
        valid[0]["is_fastest"] = True

    return results


def reconnect_interface(interface_name: str | None = None) -> tuple[bool, str]:
    """Reconecta a interface de rede ativa utilizando as regras nativas de polkit do NetworkManager."""
    iface = interface_name
    if not iface:
        _, iface = NetworkInspector.get_default_gateway()

    if not iface:
        return False, "Nenhuma interface de rede ativa identificada para reconexão."

    try:
        # 1. Tenta via nmcli connection up / device reapply
        proc = subprocess.run(
            ["nmcli", "device", "reapply", iface],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if proc.returncode == 0:
            return True, f"Interface {iface} reaplicada e reconectada com sucesso."

        # Fallback: desconecta e reconecta o dispositivo
        subprocess.run(["nmcli", "device", "disconnect", iface], capture_output=True, timeout=5.0)
        time.sleep(0.5)
        proc_conn = subprocess.run(["nmcli", "device", "connect", iface], capture_output=True, text=True, timeout=8.0)
        if proc_conn.returncode == 0:
            return True, f"Interface {iface} reconectada com sucesso."
        return False, f"Falha ao reconectar {iface}: {proc_conn.stderr.strip() or 'Erro desconhecido'}"
    except Exception as exc:
        logger.warning("Falha ao reconectar interface %s: %s", iface, exc)
        return False, f"Erro ao executar reconexão: {exc}"


def audit_attack_surface() -> dict[str, Any]:
    """Audita todas as portas em escuta classificando exposição em loopback vs rede local."""
    ports = NetworkInspector.get_listening_ports()
    audited_ports: list[dict[str, Any]] = []

    dev_ports_risk = {
        3000: "Servidor Web / Frontend Dev (Node/React)",
        5173: "Servidor Vite Dev (sem autenticação)",
        8000: "API / FastAPI / Django Dev",
        8080: "Servidor HTTP alternativo / Proxy",
        5432: "Banco de Dados PostgreSQL",
        3306: "Banco de Dados MySQL/MariaDB",
        6379: "Banco de Dados Redis (In-Memory)",
        27017: "Banco de Dados MongoDB",
    }

    exposed_count = 0
    high_risk_warnings: list[str] = []

    for p in ports:
        ip = (p.ip_binding or "").strip()
        port_num = p.port

        # Classificação de exposição
        if ip in ("127.0.0.1", "localhost", "::1"):
            exposure = "SAFE_LOOPBACK"
            desc = "Apenas Localhost (Seguro)"
        elif ip in ("0.0.0.0", "*"):
            exposure = "EXPOSED_LAN"
            desc = "Exposto em Toda a Rede (0.0.0.0)"
            exposed_count += 1
        elif ip in ("::", ""):
            exposure = "DUAL_STACK_EXPOSED"
            desc = "Dual-Stack IPv6/IPv4 Exposto (::)"
            exposed_count += 1
        else:
            exposure = "EXPOSED_LAN"
            desc = f"Exposto no IP {ip}"
            exposed_count += 1

        is_dev_risk = port_num in dev_ports_risk and exposure != "SAFE_LOOPBACK"
        if is_dev_risk:
            high_risk_warnings.append(
                f"Porta {port_num} ({dev_ports_risk[port_num]}) exposta publicamente na LAN pelo processo '{p.process_name}' (PID {p.pid or '?'})."
            )

        audited_ports.append({
            "port": port_num,
            "proto": p.protocol,
            "ip": ip,
            "process": p.process_name,
            "pid": p.pid,
            "exposure": exposure,
            "exposure_desc": desc,
            "is_sensitive": is_dev_risk,
        })

    if high_risk_warnings:
        verdict = "Atenção: Portas de desenvolvimento ou bancos expostos na rede local"
    elif exposed_count > 0:
        verdict = f"{exposed_count} serviços escutando em interfaces de rede externas"
    else:
        verdict = "Seguro: Nenhuma porta exposta fora de loopback"

    return {
        "total_listening": len(ports),
        "exposed_count": exposed_count,
        "verdict": verdict,
        "high_risk_warnings": high_risk_warnings,
        "ports": audited_ports,
    }


def measure_throughput(interface_name: str | None = None, sample_seconds: float = 0.4) -> dict[str, Any]:
    """Mede a taxa de download e upload em tempo real com relógio monotônico e zero jitter."""
    iface = interface_name
    if not iface:
        _, iface = NetworkInspector.get_default_gateway()
    if not iface:
        iface = "enp4s0"

    rx_file = f"/sys/class/net/{iface}/statistics/rx_bytes"
    tx_file = f"/sys/class/net/{iface}/statistics/tx_bytes"

    if not os.path.exists(rx_file) or not os.path.exists(tx_file):
        return {
            "interface": iface,
            "error": f"Estatísticas de rede inacessíveis em /sys para a interface {iface}.",
            "rx_kb_s": 0.0,
            "tx_kb_s": 0.0,
        }

    try:
        def read_bytes() -> tuple[int, int]:
            with open(rx_file, "r") as fr, open(tx_file, "r") as ft:
                return int(fr.read().strip()), int(ft.read().strip())

        t0 = time.monotonic()
        rx0, tx0 = read_bytes()

        # Janela de amostragem segura
        window = max(0.2, min(sample_seconds, 1.5))
        time.sleep(window)

        t1 = time.monotonic()
        rx1, tx1 = read_bytes()

        delta_t = t1 - t0
        if delta_t < 0.05:
            delta_t = 0.05

        rx_bytes_sec = max(0, rx1 - rx0) / delta_t
        tx_bytes_sec = max(0, tx1 - tx0) / delta_t

        rx_kb_s = round(rx_bytes_sec / 1024.0, 2)
        tx_kb_s = round(tx_bytes_sec / 1024.0, 2)
        rx_mb_s = round(rx_kb_s / 1024.0, 2)
        tx_mb_s = round(tx_kb_s / 1024.0, 2)

        return {
            "interface": iface,
            "rx_kb_s": rx_kb_s,
            "tx_kb_s": tx_kb_s,
            "rx_mb_s": rx_mb_s,
            "tx_mb_s": tx_mb_s,
            "sample_seconds": round(delta_t, 3),
        }
    except Exception as exc:
        logger.warning("Falha ao calcular throughput de rede: %s", exc)
        return {"interface": iface, "error": str(exc), "rx_kb_s": 0.0, "tx_kb_s": 0.0}


def diagnose_connection_path(target_host: str = "1.1.1.1") -> dict[str, Any]:
    """Executa diagnóstico segmentado hop-by-hop isolando o ponto de falha de conexão."""
    gateway_ip, iface = NetworkInspector.get_default_gateway()
    if not iface:
        iface = "enp4s0"

    stages = {
        "1_physical_carrier": False,
        "2_local_gateway": False,
        "3_public_route": False,
        "4_dns_resolution": False,
    }

    # 1. Enlace Físico (Carrier)
    carrier_file = f"/sys/class/net/{iface}/carrier"
    carrier_ok = False
    if os.path.exists(carrier_file):
        try:
            with open(carrier_file, "r") as f:
                carrier_ok = f.read().strip() == "1"
        except Exception:
            carrier_ok = False
    else:
        carrier_ok = True  # Em interfaces virtuais pode não existir carrier
    stages["1_physical_carrier"] = carrier_ok

    if not carrier_ok:
        return {
            "ok": False,
            "failing_stage": "Enlace Físico (Cabo / Wi-Fi)",
            "root_cause": f"A interface {iface} está sem sinal de portadora (cabo desconectado ou rádio desligado).",
            "stages": stages,
        }

    # 2. Gateway Local
    gw_ok = False
    if gateway_ip:
        # Tenta ping ou probe de porta no gateway
        try:
            proc = subprocess.run(
                ["ping", "-c", "1", "-W", "1", gateway_ip],
                capture_output=True,
                timeout=2.0,
            )
            gw_ok = proc.returncode == 0
        except Exception:
            gw_ok = False
    stages["2_local_gateway"] = gw_ok

    if not gw_ok and gateway_ip:
        return {
            "ok": False,
            "failing_stage": "Roteador Local (Gateway)",
            "root_cause": f"O roteador local ({gateway_ip}) não respondeu ao teste de ping. O roteador pode estar desligado ou travado.",
            "stages": stages,
        }

    # 3. Rota Pública Externa
    public_ok = False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            # Conexão TCP na porta 53 do Cloudflare ou Google DNS
            res = sock.connect_ex((target_host, 53))
            public_ok = res == 0
    except Exception:
        public_ok = False
    stages["3_public_route"] = public_ok

    if not public_ok:
        return {
            "ok": False,
            "failing_stage": "Backbone Externo (Operadora)",
            "root_cause": "O roteador local responde normalmente, mas o tráfego externo além do gateway falhou. Queda de sinal no provedor de internet.",
            "stages": stages,
        }

    # 4. Resolução de DNS
    dns_ok = False
    try:
        addr_info = socket.getaddrinfo("google.com", 80, socket.AF_UNSPEC, socket.SOCK_STREAM)
        dns_ok = len(addr_info) > 0
    except Exception:
        dns_ok = False
    stages["4_dns_resolution"] = dns_ok

    if not dns_ok:
        return {
            "ok": False,
            "failing_stage": "Servidor DNS",
            "root_cause": "Conexão IP ativa com a internet, mas a resolução de nomes de domínio falhou. Servidor DNS indisponível.",
            "stages": stages,
        }

    return {
        "ok": True,
        "failing_stage": "Nenhum",
        "root_cause": "Conexão com a rede e internet totalmente operacional em todas as etapas.",
        "stages": stages,
    }


def check_ssl_certificate(host: str, port: int = 443, timeout: float = 2.5) -> dict[str, Any]:
    """Inspeciona certificado SSL/TLS com timeout estrito de 2.5s (proteção anti-SYN-blackhole)."""
    clean_host = host.strip()
    if clean_host.startswith("http://"):
        clean_host = clean_host[7:]
    elif clean_host.startswith("https://"):
        clean_host = clean_host[8:]
    clean_host = clean_host.split("/")[0].split(":")[0]

    if not clean_host:
        return {"ok": False, "error": "Endereço de host inválido."}

    import ssl
    from datetime import datetime

    ctx = ssl.create_default_context()

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            with ctx.wrap_socket(sock, server_hostname=clean_host) as ssock:
                ssock.connect((clean_host, port))
                cert = ssock.getpeercert()
                tls_version = ssock.version()
                cipher = ssock.cipher()

        if not cert:
            return {"ok": False, "error": "Nenhum certificado retornado pelo servidor."}

        from datetime import timezone

        # Parsing de expiração imune a locale (GLib/GTK define LC_TIME no sistema)
        exp_date_str = cert.get("notAfter", "")
        month_map = {
            "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
            "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
        }
        parts = exp_date_str.split()
        if len(parts) >= 4 and parts[0] in month_map:
            month = month_map[parts[0]]
            day = int(parts[1])
            h, m, s = [int(x) for x in parts[2].split(":")]
            year = int(parts[3])
            exp_dt = datetime(year, month, day, h, m, s, tzinfo=timezone.utc)
        else:
            exp_dt = datetime.strptime(exp_date_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        days_remaining = (exp_dt - now).days

        # Extrai Emissor
        issuer_dict: dict[str, str] = {}
        for rdn in cert.get("issuer", ()):
            for k, v in rdn:
                issuer_dict[k] = v

        issuer_org = issuer_dict.get("organizationName") or issuer_dict.get("commonName") or "Desconhecido"

        return {
            "ok": True,
            "host": clean_host,
            "port": port,
            "tls_version": tls_version,
            "cipher": cipher[0] if cipher else "",
            "issuer": issuer_org,
            "expires_at": exp_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "days_remaining": days_remaining,
            "is_expired": days_remaining < 0,
            "warning": "Certificado expira em breve!" if 0 <= days_remaining <= 15 else "Normal",
        }
    except Exception as exc:
        logger.debug("Falha na inspeção SSL de %s: %s", clean_host, exc)
        return {"ok": False, "host": clean_host, "error": f"Falha na conexão TLS: {exc}"}


def discover_mdns_services(timeout_sec: float = 2.0) -> list[dict[str, Any]]:
    """Descubre passivamente serviços locais via mDNS / Avahi sem travamento de daemon."""
    # Salvaguarda: checagem do socket do avahi-daemon
    avahi_sockets = ("/run/avahi-daemon/socket", "/var/run/avahi-daemon/socket")
    if not any(os.path.exists(s) for s in avahi_sockets):
        return []

    services: list[dict[str, Any]] = []
    try:
        cmd = [
            "avahi-browse",
            "-r",
            "-t",
            "-k",
            "-p",
            "_http._tcp",
            "_home-assistant._tcp",
            "_ipp._tcp",
            "_googlecast._tcp",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec + 0.5)
        if proc.returncode != 0:
            return services

        for line in proc.stdout.splitlines():
            # Formato parseável com delimitador ';':
            # =;iface;proto;name;type;domain;host;ip;port;txt
            parts = line.split(";")
            if len(parts) >= 9 and parts[0] == "=":
                services.append({
                    "name": parts[3],
                    "type": parts[4],
                    "domain": parts[5],
                    "host": parts[6],
                    "ip": parts[7],
                    "port": int(parts[8]) if parts[8].isdigit() else 0,
                })
    except Exception as exc:
        logger.debug("Falha na descoberta mDNS: %s", exc)

    return services


class NetworkBlocker:
    """Contenção defensiva de um dispositivo na LAN — **delegada** ao roteador/HA.

    Honestidade de arquitetura (PROPOSTA_DEFESA_REDE.md, item 4): o host NÃO derruba o
    tráfego de um terceiro localmente — isso exigiria ser o gateway/switch e seria
    ofensivo (equivale ao "ataque" que o usuário perguntou e que recusamos). O bloqueio
    real acontece no roteador (ACL / MAC filter / DHCP deny) ou via Home Assistant. Esta
    classe apenas *solicita* o bloqueio através de um backend configurado pelo usuário.

    Sem backend configurado **não executa nada** e devolve orientação explícita — nunca
    finge sucesso (fail-closed). Backends:

      - ``none`` (padrão): não configurado ⇒ ``ok=False`` + ``guidance``.
      - ``script``: shell-out para um script próprio do usuário
        ``<script> <acao> <identificador>`` (args como lista, sem shell ⇒ sem injeção).
        O usuário faz a ponte com seu roteador/HA nesse script — é o adaptador universal;
        roteador-API e HA são realizados por ele.

    Reversível: ``action="allow"`` desbloqueia (comando de desbloqueio explícito no
    roteador). O MAC é mascarado na resposta ao modelo; o MAC **real** (privado, local)
    é usado apenas no comando ao roteador.
    """

    DEFAULT_CONFIG_REL = "zorin-copilot/network_block.json"
    VALID_ACTIONS = {"block", "allow"}

    def __init__(self, config_path: str | Path | None = None):
        if config_path is None:
            base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
            self.config_path = Path(base) / self.DEFAULT_CONFIG_REL
        else:
            self.config_path = Path(config_path)

    def load_config(self) -> dict[str, Any]:
        if not self.config_path or not self.config_path.exists():
            return {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def is_configured(self, config: dict[str, Any] | None = None) -> bool:
        config = config if config is not None else self.load_config()
        if not isinstance(config, dict):
            return False
        if config.get("backend") != "script":
            return False
        script = config.get("script")
        if not script:
            return False
        script_path = Path(script)
        if not script_path.exists():
            return False
        try:
            mode = script_path.stat().st_mode
            return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
        except OSError:
            return False

    @staticmethod
    def _normalize_action(action: str) -> str | None:
        a = (action or "").strip().lower()
        if a in ("block", "deny", "ban"):
            return "block"
        if a in ("allow", "unblock", "unban", "permit"):
            return "allow"
        return None

    def _resolve_identifier(self, mac, ip, inventory) -> tuple[str | None, str | None, str, str]:
        """Devolve (identificador_para_comando, mac_cru_para_log, tipo, tipo_entrada).

        ``tipo`` ∈ {"mac", "ip", "invalid_mac", "missing"}; ``tipo_entrada`` ∈ {"mac", "ip"}.
        O MAC cru só é usado localmente (comando ao roteador); nunca sai na resposta.
        """
        if mac:
            try:
                raw = _normalize_mac(mac)
            except ValueError:
                return None, None, "invalid_mac", "mac"
            return raw.hex(":"), raw.hex(":"), "mac", "mac"
        if ip:
            # Resolve o MAC real no inventário local (privado) quando possível — o
            # roteador precisa do MAC; o modelo só enxerga o IP.
            if inventory is not None:
                found = inventory.find_by_ip(ip)
                if found and found.get("mac"):
                    return found["mac"], found["mac"], "mac", "ip"
            return ip.strip(), None, "ip", "ip"
        return None, None, "missing", "missing"

    def _guidance(self) -> list[str]:
        return [
            "Bloqueio de dispositivo exige um backend de roteador/HA configurado.",
            "Crie ~/.config/zorin-copilot/network_block.json com:",
            '  {"backend": "script", "script": "/caminho/para/block_device.sh"}',
            "O script recebe: <acao> <identificador>  (acao = block | allow; "
            "identificador = MAC ou IP).",
            "Nele você faz a ponte com seu roteador (API MikroTik/OpenWrt/ASUS/UniFi, "
            "shell_command no Home Assistant, ou reserva DHCP + deny no servidor DHCP).",
            "Nunca implementamos drop local de pacote de terceiro — isso seria ofensivo.",
        ]

    def _masked_target(self, identifier: str, kind: str) -> str:
        if kind == "mac" and identifier:
            return NetworkInspector.mask_mac(identifier)
        return identifier or ""

    def request_block(
        self,
        mac: str | None = None,
        ip: str | None = None,
        action: str = "block",
        dry_run: bool = False,
        inventory: "DeviceInventory | None" = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_action = self._normalize_action(action)
        if normalized_action is None:
            return {
                "ok": False,
                "executed": False,
                "action": action,
                "target": "",
                "error": f"Ação inválida: '{action}'. Use 'block' ou 'allow'.",
                "guidance": [],
            }

        identifier, _raw_mac, kind, input_kind = self._resolve_identifier(mac, ip, inventory)
        if kind == "invalid_mac":
            return {
                "ok": False,
                "executed": False,
                "action": normalized_action,
                "target": "",
                "error": f"MAC inválido: '{mac}'.",
                "guidance": [],
            }
        if kind == "missing":
            return {
                "ok": False,
                "executed": False,
                "action": normalized_action,
                "target": "",
                "error": "Informe 'mac' (completo) ou 'ip' do dispositivo a conter.",
                "guidance": [],
            }

        # A resposta ao modelo mostra o que o agente referenciou (IP), ou o MAC
        # mascarado (quando o agente informou o MAC cru). O MAC cru só vai ao roteador.
        if input_kind == "ip":
            masked_target = (ip or "").strip()
        else:
            masked_target = self._masked_target(identifier, kind)
        cfg = config if config is not None else self.load_config()

        # Fail-closed: sem backend configurado, não executa e orienta.
        if not self.is_configured(cfg):
            return {
                "ok": False,
                "executed": False,
                "action": normalized_action,
                "target": masked_target,
                "error": "Backend de contenção não configurado.",
                "guidance": self._guidance(),
            }

        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "executed": False,
                "action": normalized_action,
                "target": masked_target,
                "backend": "script",
                "error": "",
                "guidance": [],
            }

        script = cfg.get("script")
        try:
            # Sem shell: args como lista ⇒ sem injeção de comando.
            proc = subprocess.run(
                [str(script), normalized_action, identifier],
                capture_output=True,
                text=True,
                timeout=30.0,
            )
        except FileNotFoundError:
            return {
                "ok": False,
                "executed": False,
                "action": normalized_action,
                "target": masked_target,
                "error": f"Script de backend não encontrado: {script}",
                "guidance": self._guidance(),
            }
        except Exception as exc:
            logger.warning("Falha ao executar backend de contenção: %s", exc)
            return {
                "ok": False,
                "executed": False,
                "action": normalized_action,
                "target": masked_target,
                "error": f"Falha ao executar backend de contenção: {exc}",
                "guidance": [],
            }

        if proc.returncode != 0:
            return {
                "ok": False,
                "executed": True,
                "action": normalized_action,
                "target": masked_target,
                "error": proc.stderr.strip() or "Backend de contenção retornou erro.",
                "guidance": [],
            }

        return {
            "ok": True,
            "executed": True,
            "action": normalized_action,
            "target": masked_target,
            "backend": "script",
            "error": "",
            "guidance": [],
        }

