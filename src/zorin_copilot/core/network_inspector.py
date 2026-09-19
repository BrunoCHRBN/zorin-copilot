# Decisão de design: Módulo de Percepção de Infraestrutura de Rede e Topologia Local.
# Coleta dados não-bloqueantes do kernel Linux (Netlink JSON, /proc, ss, nmcli) sem
# privilégios de root, isola interfaces de containers (Docker/Podman), identifica fabricantes
# via OUI resolver local (sem rede externa) e sincroniza a topologia no KnowledgeGraph com TTL.

"""Inspetor de infraestrutura de rede, topologia local e portas em execução."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

logger = logging.getLogger(__name__)


class InterfaceType(str, Enum):
    PHYSICAL_ETHERNET = "ethernet"
    PHYSICAL_WIFI = "wifi"
    VPN_TUNNEL = "vpn"
    VIRTUAL_CONTAINER = "container"
    LOOPBACK = "loopback"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class InterfaceInfo:
    """Informações detalhadas de uma interface de rede."""

    name: str
    iface_type: InterfaceType
    ipv4: list[str]
    ipv6: list[str]
    mac: str
    is_up: bool
    is_default: bool = False
    rx_bytes: int = 0
    tx_bytes: int = 0
    speed_mbps: int | None = None


@dataclass(frozen=True)
class WiFiMetrics:
    """Métricas em tempo real de conexão sem fio."""

    ssid: str
    bssid: str
    signal_dbm: int
    signal_percent: int
    frequency_ghz: float
    channel: int
    rate_mbps: float


@dataclass(frozen=True)
class NeighborDevice:
    """Dispositivo detectado na sub-rede local (LAN)."""

    ip: str
    mac: str
    hostname: str
    vendor: str
    device_type: str  # "router", "computer", "phone", "tv", "printer", "iot", "unknown"
    interface: str
    is_online: bool
    last_seen: float = field(default_factory=time.time)
    ping_ms: float | None = None


@dataclass(frozen=True)
class ListeningPort:
    """Porta escutando conexões no computador local."""

    protocol: str  # "tcp" ou "udp"
    port: int
    ip_binding: str
    process_name: str
    pid: int | None = None


@dataclass(frozen=True)
class NetworkHealthReport:
    """Relatório condensado de integridade e conectividade de rede."""

    primary_interface: str
    primary_type: str
    local_ip: str
    gateway_ip: str
    gateway_ping_ms: float | None
    internet_ping_ms: float | None
    dns_servers: list[str]
    wifi: WiFiMetrics | None
    packet_loss_percent: float = 0.0
    public_ip: str | None = None


class OUIResolver:
    """Tabela local compacta para identificação rápida de fabricantes via MAC address."""

    # Mapeamento estático dos maiores fabricantes de roteadores, IoT, TVs e computadores
    VENDOR_TABLE: dict[str, tuple[str, str]] = {
        # Sagemcom / Roteadores de operadora (Vivo, Claro, Tim)
        "44:89:6d": ("Sagemcom (Roteador Fibra)", "router"),
        "00:1a:2b": ("Ayecom", "router"),
        "e4:c3:2a": ("Sagemcom", "router"),
        "cc:96:a0": ("ZTE Corporation", "router"),
        "e8:65:d4": ("Huawei Technologies", "router"),
        "00:1f:a4": ("Huawei Technologies", "router"),
        "50:d4:f7": ("TP-Link Corporation", "router"),
        "e4:c3:2b": ("TP-Link Corporation", "router"),
        "c0:06:c3": ("ASUSTeK Computer", "router"),
        # Apple
        "1c:5a:6b": ("Apple Inc.", "phone"),
        "bc:d0:74": ("Apple Inc.", "phone"),
        "3c:5a:37": ("Apple Inc.", "computer"),
        "d8:3a:dd": ("Apple Inc.", "computer"),
        "b4:fb:e4": ("Apple Inc.", "computer"),
        # Samsung
        "ac:cc:fc": ("Samsung Electronics", "phone"),
        "d0:03:df": ("Samsung Electronics", "tv"),
        "cc:29:f5": ("Samsung Electronics", "tv"),
        # LG
        "98:d7:42": ("LG Electronics", "tv"),
        "a8:23:fe": ("LG Electronics", "tv"),
        # Espressif / Tuya (Lâmpadas inteligentes, Avant Neo, tomadas inteligentes)
        "ec:fa:bc": ("Espressif (Avant Neo / IoT)", "iot"),
        "24:4c:e3": ("Espressif IoT", "iot"),
        "84:f3:eb": ("Espressif IoT", "iot"),
        "dc:4f:22": ("Espressif IoT", "iot"),
        "a4:cf:12": ("Espressif IoT", "iot"),
        # Raspberry Pi / Servidores / Home Assistant
        "dc:a6:32": ("Raspberry Pi Foundation", "iot"),
        "b8:27:eb": ("Raspberry Pi Foundation", "iot"),
        "e4:5f:01": ("Raspberry Pi Foundation", "iot"),
        # Intel / Realtek (Placas mãe e computadores)
        "a8:a1:59": ("Realtek Semiconductor", "computer"),
        "00:e0:4c": ("Realtek Semiconductor", "computer"),
        "00:1b:21": ("Intel Corporation", "computer"),
        "68:05:ca": ("Intel Corporation", "computer"),
        # HP / Impressoras
        "00:1e:0b": ("HP Inc. (Impressora)", "printer"),
        "18:60:24": ("HP Inc. (Impressora)", "printer"),
        "30:8d:99": ("HP Inc. (Impressora)", "printer"),
        # Google / Chromecast
        "f4:f5:db": ("Google LLC (Chromecast)", "tv"),
        "d8:6c:63": ("Google LLC (Nest/Home)", "iot"),
    }

    @classmethod
    def resolve(cls, mac: str) -> tuple[str, str]:
        """Devolve (nome_fabricante, tipo_dispositivo) a partir do MAC."""
        if not mac or len(mac) < 8:
            return "Desconhecido", "unknown"

        prefix = mac.lower().replace("-", ":")[:8]
        if prefix in cls.VENDOR_TABLE:
            return cls.VENDOR_TABLE[prefix]

        return "Dispositivo de Rede", "unknown"


class NetworkInspector:
    """Motor central de inspeção e diagnóstico de infraestrutura de rede."""

    @classmethod
    def classify_interface(cls, name: str) -> InterfaceType:
        """Classifica o tipo de interface baseando-se no nome e atributos do kernel."""
        low = name.lower()
        if low == "lo":
            return InterfaceType.LOOPBACK
        if low.startswith(("docker", "br-", "veth", "virbr", "cni")):
            return InterfaceType.VIRTUAL_CONTAINER
        if low.startswith(("tailscale", "wg", "tun", "tap", "ppp")):
            return InterfaceType.VPN_TUNNEL
        if low.startswith(("wl", "wlan", "wifi")):
            return InterfaceType.PHYSICAL_WIFI
        if low.startswith(("en", "eth", "eno", "ens", "enp")):
            return InterfaceType.PHYSICAL_ETHERNET
        return InterfaceType.UNKNOWN

    @classmethod
    def get_interfaces(cls) -> list[InterfaceInfo]:
        """Obtém a lista de todas as interfaces e endereços via Netlink JSON ou sysfs."""
        interfaces: list[InterfaceInfo] = []
        default_gw_ip, default_gw_dev = cls.get_default_gateway()

        try:
            proc = subprocess.run(
                ["ip", "-j", "addr", "show"],
                capture_output=True,
                text=True,
                timeout=0.6,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout)
                for iface in data:
                    name = iface.get("ifname", "")
                    if not name:
                        continue

                    iface_type = cls.classify_interface(name)
                    mac = iface.get("address", "")
                    flags = iface.get("flags", [])
                    is_up = "UP" in flags

                    ipv4_addrs: list[str] = []
                    ipv6_addrs: list[str] = []

                    for addr in iface.get("addr_info", []):
                        fam = addr.get("family")
                        local = addr.get("local")
                        if fam == "inet" and local:
                            ipv4_addrs.append(local)
                        elif fam == "inet6" and local:
                            ipv6_addrs.append(local)

                    # Leitura de bytes RX/TX em /sys/class/net
                    rx_bytes = 0
                    tx_bytes = 0
                    speed = None
                    sys_dir = Path(f"/sys/class/net/{name}")
                    if sys_dir.exists():
                        try:
                            rx_f = sys_dir / "statistics" / "rx_bytes"
                            tx_f = sys_dir / "statistics" / "tx_bytes"
                            if rx_f.exists():
                                rx_bytes = int(rx_f.read_text().strip())
                            if tx_f.exists():
                                tx_bytes = int(tx_f.read_text().strip())
                            speed_f = sys_dir / "speed"
                            if speed_f.exists():
                                sp_val = int(speed_f.read_text().strip())
                                if sp_val > 0:
                                    speed = sp_val
                        except Exception:
                            pass

                    interfaces.append(
                        InterfaceInfo(
                            name=name,
                            iface_type=iface_type,
                            ipv4=ipv4_addrs,
                            ipv6=ipv6_addrs,
                            mac=mac,
                            is_up=is_up,
                            is_default=(name == default_gw_dev),
                            rx_bytes=rx_bytes,
                            tx_bytes=tx_bytes,
                            speed_mbps=speed,
                        )
                    )
                return interfaces
        except Exception as exc:
            logger.debug("Falha na consulta via ip -j addr: %s", exc)

        return interfaces

    @classmethod
    def get_default_gateway(cls) -> tuple[str | None, str | None]:
        """Obtém o IP e a interface do Gateway padrão (ex: 192.168.15.1, enp4s0)."""
        try:
            proc = subprocess.run(
                ["ip", "-j", "route", "show", "default"],
                capture_output=True,
                text=True,
                timeout=0.5,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                routes = json.loads(proc.stdout)
                if routes and isinstance(routes, list):
                    first = routes[0]
                    return first.get("gateway"), first.get("dev")
        except Exception as exc:
            logger.debug("Falha ao obter gateway padrão: %s", exc)
        return None, None

    @classmethod
    def get_neighbors(cls) -> list[NeighborDevice]:
        """Lê a tabela de vizinhos do kernel (ARP/Netlink) de forma 100% passiva e não-bloqueante."""
        devices: list[NeighborDevice] = []
        default_gw_ip, _ = cls.get_default_gateway()

        try:
            proc = subprocess.run(
                ["ip", "-j", "neigh", "show"],
                capture_output=True,
                text=True,
                timeout=0.6,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                entries = json.loads(proc.stdout)
                for entry in entries:
                    ip = entry.get("dst", "")
                    mac = entry.get("lladdr", "")
                    dev = entry.get("dev", "")
                    state = entry.get("state", [])

                    # Ignora IPv6 link-local ou entradas vazias
                    if not ip or not mac or ":" in ip:
                        continue

                    # Ignora interfaces de containers
                    if cls.classify_interface(dev) == InterfaceType.VIRTUAL_CONTAINER:
                        continue

                    vendor, dev_type = OUIResolver.resolve(mac)

                    # Se for o gateway padrão, classifica como roteador
                    if ip == default_gw_ip:
                        dev_type = "router"
                        if vendor == "Dispositivo de Rede":
                            vendor = "Roteador da Rede"

                    is_online = any(s in ("REACHABLE", "DELAY", "PROBE") for s in state)

                    devices.append(
                        NeighborDevice(
                            ip=ip,
                            mac=mac,
                            hostname="",
                            vendor=vendor,
                            device_type=dev_type,
                            interface=dev,
                            is_online=is_online,
                        )
                    )
        except Exception as exc:
            logger.debug("Falha ao consultar tabela de vizinhos: %s", exc)

        return devices

    @classmethod
    def get_wifi_metrics(cls) -> WiFiMetrics | None:
        """Coleta métricas de Wi-Fi em tempo real via nmcli ou D-Bus."""
        if not shutil.which("nmcli"):
            return None

        try:
            proc = subprocess.run(
                ["nmcli", "-t", "-f", "in-use,ssid,bssid,signal,freq,chan,rate", "dev", "wifi"],
                capture_output=True,
                text=True,
                timeout=0.8,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                for line in proc.stdout.splitlines():
                    if line.startswith("*:") or line.startswith("yes:"):
                        parts = line.split(":")
                        if len(parts) >= 7:
                            ssid = parts[1].strip()
                            bssid = parts[2].strip()
                            try:
                                sig_pct = int(parts[3])
                            except ValueError:
                                sig_pct = 70
                            # Conversão percentual -> dBm aproximado: dBm = (pct / 2) - 100
                            sig_dbm = int((sig_pct / 2) - 100)

                            freq_ghz = 2.4
                            try:
                                freq_int = int(re.sub(r"\D", "", parts[4]))
                                freq_ghz = round(freq_int / 1000.0, 3)
                            except (ValueError, ZeroDivisionError):
                                pass

                            chan = 1
                            try:
                                chan = int(parts[5])
                            except ValueError:
                                pass

                            rate = 54.0
                            try:
                                rate = float(re.sub(r"[^\d.]", "", parts[6]))
                            except ValueError:
                                pass

                            return WiFiMetrics(
                                ssid=ssid,
                                bssid=bssid,
                                signal_dbm=sig_dbm,
                                signal_percent=sig_pct,
                                frequency_ghz=freq_ghz,
                                channel=chan,
                                rate_mbps=rate,
                            )
        except Exception as exc:
            logger.debug("Falha na telemetria de Wi-Fi: %s", exc)

        return None

    @classmethod
    def get_listening_ports(cls) -> list[ListeningPort]:
        """Mapeia portas e processos escutando conexões no computador via ss."""
        ports: list[ListeningPort] = []
        if not shutil.which("ss"):
            return ports

        try:
            proc = subprocess.run(
                ["ss", "-tulpn"],
                capture_output=True,
                text=True,
                timeout=0.6,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                lines = proc.stdout.splitlines()[1:]  # Pula o cabeçalho
                for line in lines:
                    parts = line.split()
                    if len(parts) < 5:
                        continue

                    netid = parts[0].strip().lower()  # "tcp" ou "udp"
                    local_addr = parts[4]

                    # Extrai endereço e porta
                    if ":" in local_addr:
                        ip_bind, port_str = local_addr.rsplit(":", 1)
                        try:
                            port_num = int(port_str)
                        except ValueError:
                            continue
                    else:
                        continue

                    # Extrai processo e PID se visível (users:(("processo",pid=123,fd=4)))
                    proc_name = ""
                    pid_num = None
                    if len(parts) >= 7:
                        proc_info = parts[6]
                        m = re.search(r'"([^"]+)",pid=(\d+)', proc_info)
                        if m:
                            proc_name = m.group(1)
                            try:
                                pid_num = int(m.group(2))
                            except ValueError:
                                pass

                    ports.append(
                        ListeningPort(
                            protocol=netid,
                            port=port_num,
                            ip_binding=ip_bind,
                            process_name=proc_name,
                            pid=pid_num,
                        )
                    )
        except Exception as exc:
            logger.debug("Falha na inspeção de portas via ss: %s", exc)

        # Remove duplicatas e ordena por número da porta
        seen = set()
        unique_ports = []
        for p in ports:
            key = (p.protocol, p.port, p.ip_binding)
            if key not in seen:
                seen.add(key)
                unique_ports.append(p)
        unique_ports.sort(key=lambda x: x.port)
        return unique_ports

    @classmethod
    def ping_host(cls, target_ip: str, timeout_sec: float = 0.4) -> float | None:
        """Executa um ping ICMP unprivileged retornando a latência em milissegundos."""
        if not target_ip:
            return None

        # Timeout em segundos para o comando ping
        timeout_int = max(1, int(round(timeout_sec)))
        try:
            start = time.perf_counter()
            res = subprocess.run(
                ["ping", "-c", "1", "-W", str(timeout_int), target_ip],
                capture_output=True,
                text=True,
                timeout=timeout_sec + 0.5,
                check=False,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000.0

            if res.returncode == 0:
                # Tenta extrair o tempo exato reportado pelo kernel (tempo=X.X ms)
                m = re.search(r"tempo[=\s]([\d.]+)\s*ms|time[=\s]([\d.]+)\s*ms", res.stdout, re.I)
                if m:
                    val = m.group(1) or m.group(2)
                    try:
                        return float(val)
                    except ValueError:
                        pass
                return round(elapsed_ms, 2)
        except Exception:
            pass

        return None

    @classmethod
    def get_health_report(cls, target_internet: str = "1.1.1.1") -> NetworkHealthReport:
        """Compila o relatório condensado de saúde da rede e conectividade."""
        gw_ip, gw_dev = cls.get_default_gateway()
        interfaces = cls.get_interfaces()

        primary_iface = gw_dev or "unknown"
        primary_type = "unknown"
        local_ip = "127.0.0.1"

        for iface in interfaces:
            if iface.name == primary_iface:
                primary_type = iface.iface_type.value
                if iface.ipv4:
                    local_ip = iface.ipv4[0]
                break

        gw_ping = cls.ping_host(gw_ip) if gw_ip else None
        inet_ping = cls.ping_host(target_internet)

        # Servidores DNS ativos
        dns_servers: list[str] = []
        try:
            resolv = Path("/etc/resolv.conf")
            if resolv.exists():
                for line in resolv.read_text().splitlines():
                    if line.strip().startswith("nameserver"):
                        parts = line.split()
                        if len(parts) >= 2:
                            dns_servers.append(parts[1])
        except Exception:
            pass

        wifi = cls.get_wifi_metrics()

        loss = 0.0
        if inet_ping is None and gw_ping is None:
            loss = 100.0
        elif inet_ping is None:
            loss = 50.0

        return NetworkHealthReport(
            primary_interface=primary_iface,
            primary_type=primary_type,
            local_ip=local_ip,
            gateway_ip=gw_ip or "Não detectado",
            gateway_ping_ms=gw_ping,
            internet_ping_ms=inet_ping,
            dns_servers=dns_servers[:3],
            wifi=wifi,
            packet_loss_percent=loss,
        )

    @classmethod
    def mask_mac(cls, mac: str) -> str:
        """Aplica máscara Zero-Knowledge ao MAC/BSSID preservando apenas o prefixo OUI (fabricante).
        Ex: '44:89:6d:12:34:56' -> '44:89:6d:xx:xx:xx'
        """
        if not mac:
            return ""
        norm = mac.replace("-", ":").lower()
        parts = norm.split(":")
        if len(parts) == 6:
            return f"{parts[0]}:{parts[1]}:{parts[2]}:xx:xx:xx"
        return "xx:xx:xx:xx:xx:xx"

    @classmethod
    def to_safe_summary(
        cls,
        report: NetworkHealthReport | None = None,
        neighbors: list[NeighborDevice] | None = None,
    ) -> dict[str, Any]:
        """Gera resumo seguro para consumo por modelos LLM/Cloud, com MACs e BSSIDs ofuscados."""
        res: dict[str, Any] = {}
        if report:
            wifi_dict = None
            if report.wifi:
                wifi_dict = {
                    "ssid": report.wifi.ssid,
                    "bssid": cls.mask_mac(report.wifi.bssid),
                    "signal_percent": report.wifi.signal_percent,
                    "frequency_ghz": report.wifi.frequency_ghz,
                    "rate_mbps": report.wifi.rate_mbps,
                }
            res["health"] = {
                "primary_interface": report.primary_interface,
                "primary_type": report.primary_type,
                "local_ip": report.local_ip,
                "gateway_ip": report.gateway_ip,
                "gateway_ping_ms": report.gateway_ping_ms,
                "internet_ping_ms": report.internet_ping_ms,
                "dns_servers": report.dns_servers,
                "wifi": wifi_dict,
                "packet_loss_percent": report.packet_loss_percent,
            }
        if neighbors is not None:
            res["lan_devices"] = [
                {
                    "ip": n.ip,
                    "mac_masked": cls.mask_mac(n.mac),
                    "vendor": n.vendor,
                    "type": n.device_type,
                    "hostname": n.hostname,
                    "is_online": n.is_online,
                }
                for n in neighbors
            ]
        return res


    @classmethod
    def sync_to_knowledge_graph(cls, graph: Any | None = None) -> int:
        """Sincroniza a topologia e aparelhos da rede no Grafo de Conhecimento Semântico."""
        if graph is None:
            try:
                from .knowledge_graph import KnowledgeGraph
                graph = KnowledgeGraph()
            except Exception:
                return 0

        updated_count = 0
        gw_ip, _ = cls.get_default_gateway()
        hostname = socket.gethostname()

        # 1. Nó do Host atual
        graph.add_node(
            node_id=f"host:{hostname}",
            label="HostMachine",
            properties={"hostname": hostname, "os": "EndeavourOS"},
        )

        # 2. Nó do Roteador / Gateway
        if gw_ip:
            router_node_id = f"router:{gw_ip}"
            graph.add_node(
                node_id=router_node_id,
                label="GatewayRouter",
                properties={"ip": gw_ip},
            )
            graph.add_edge(
                source_id=f"host:{hostname}",
                target_id=router_node_id,
                relation="CONNECTED_VIA_GATEWAY",
            )
            updated_count += 2

        # 3. Dispositivos da rede local
        neighbors = cls.get_neighbors()
        for dev in neighbors:
            dev_id = f"device:{dev.mac}"
            graph.add_node(
                node_id=dev_id,
                label="LANConnectedDevice",
                properties={
                    "ip": dev.ip,
                    "mac": dev.mac,
                    "vendor": dev.vendor,
                    "type": dev.device_type,
                    "is_online": dev.is_online,
                },
            )
            if gw_ip:
                graph.add_edge(
                    source_id=f"router:{gw_ip}",
                    target_id=dev_id,
                    relation="SERVES_DEVICE",
                )
            updated_count += 1

        return updated_count
