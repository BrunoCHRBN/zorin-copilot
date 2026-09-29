# Decisão de design: Módulo de Percepção de Infraestrutura de Rede e Topologia Local.
# Coleta dados não-bloqueantes do kernel Linux (Netlink JSON, /proc, ss, nmcli) sem
# privilégios de root, isola interfaces de containers (Docker/Podman), identifica fabricantes
# via OUI resolver local (sem rede externa) e sincroniza a topologia no KnowledgeGraph com TTL.

"""Inspetor de infraestrutura de rede, topologia local e portas em execução."""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import time
import xml.etree.ElementTree as ET
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
        "1c:36:bb": ("Apple Inc.", "phone"),
        "1c:5a:6b": ("Apple Inc.", "phone"),
        "04:15:52": ("Apple Inc.", "phone"),
        "08:6d:41": ("Apple Inc.", "phone"),
        "10:9a:dd": ("Apple Inc.", "phone"),
        "14:10:9f": ("Apple Inc.", "phone"),
        "18:ee:69": ("Apple Inc.", "phone"),
        "28:cf:e9": ("Apple Inc.", "phone"),
        "2c:33:61": ("Apple Inc.", "phone"),
        "34:36:3b": ("Apple Inc.", "phone"),
        "3c:22:fb": ("Apple Inc.", "computer"),
        "44:00:49": ("Apple Inc.", "phone"),
        "48:60:bc": ("Apple Inc.", "phone"),
        "54:e3:2e": ("Apple Inc.", "phone"),
        "58:b0:35": ("Apple Inc.", "phone"),
        "60:ab:d2": ("Apple Inc.", "phone"),
        "68:5b:35": ("Apple Inc.", "phone"),
        "70:56:81": ("Apple Inc.", "computer"),
        "78:4f:43": ("Apple Inc.", "phone"),
        "7c:c3:a1": ("Apple Inc.", "phone"),
        "84:38:35": ("Apple Inc.", "phone"),
        "88:1f:a1": ("Apple Inc.", "phone"),
        "90:84:0d": ("Apple Inc.", "phone"),
        "98:01:a7": ("Apple Inc.", "phone"),
        "98:5d:ad": ("Apple Inc.", "phone"),
        "9c:ad:97": ("Apple Inc.", "phone"),
        "a0:99:9b": ("Apple Inc.", "phone"),
        "a4:83:e7": ("Apple Inc.", "phone"),
        "ac:bc:32": ("Apple Inc.", "phone"),
        "b0:19:c6": ("Apple Inc.", "phone"),
        "bc:d0:74": ("Apple Inc.", "phone"),
        "c0:56:27": ("Apple Inc.", "phone"),
        "c8:e0:eb": ("Apple Inc.", "phone"),
        "cc:08:e0": ("Apple Inc.", "phone"),
        "d4:c7:a7": ("Apple Inc.", "phone"),
        "d8:3a:dd": ("Apple Inc.", "computer"),
        "e0:ac:cb": ("Apple Inc.", "phone"),
        "e4:98:d6": ("Apple Inc.", "phone"),
        "e8:8d:28": ("Apple Inc.", "phone"),
        "f0:18:98": ("Apple Inc.", "phone"),
        "f4:5c:89": ("Apple Inc.", "phone"),
        "f8:1e:df": ("Apple Inc.", "phone"),
        "fc:fc:48": ("Apple Inc.", "phone"),
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

    @classmethod
    def local_subnet_cidr(cls, prefix: int = 24) -> str | None:
        """Deriva o CIDR da sub-rede local a partir do IP do host (ex.: '192.168.5.0/24').

        Usa a primeira interface física (ethernet/wifi) com IPv4 utilizável. Ignora
        loopback, containers e VPN. Retorna None se não houver IP local utilizável.
        """
        interfaces = cls.get_interfaces()
        for iface in interfaces:
            if iface.iface_type in (
                InterfaceType.LOOPBACK,
                InterfaceType.VIRTUAL_CONTAINER,
                InterfaceType.VPN_TUNNEL,
            ):
                continue
            for ipv4 in iface.ipv4:
                if ipv4.startswith("127.") or ipv4.startswith("169.254."):
                    continue
                parts = ipv4.split(".")
                if len(parts) == 4:
                    return f"{parts[0]}.{parts[1]}.{parts[2]}.0/{prefix}"
        return None

    @classmethod
    def scan_subnet(cls, cidr: str, timeout_sec: float = 45.0) -> list[NeighborDevice]:
        """Descoberta **ativa** de hosts na sub-rede via ``nmap -sn`` (host discovery).

        Diferente de :meth:`get_neighbors` (que é **passivo** — lê a tabela ARP do
        kernel e só lista quem o host já contatou), este método varre ativamente o
        CIDR e encontra dispositivos que ainda não estão na tabela ARP — é o que
        permite detectar um aparelho desconhecido na LAN.

        Read-only: não abre portas, gera apenas tráfego ARP/ICMP mínimo. Requer
        ``nmap`` instalado; sem ele, retorna lista vazia com log de aviso.
        """
        if not shutil.which("nmap"):
            logger.warning("nmap ausente: scan_subnet indisponível.")
            return []
        try:
            proc = subprocess.run(
                ["nmap", "-sn", "-T4", "--host-timeout", "10s", "-oX", "-", cidr],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
            if proc.returncode != 0:
                logger.warning("nmap retornou %s em scan_subnet", proc.returncode)
                return []
            return cls._parse_nmap_hosts_xml(proc.stdout)
        except Exception as exc:
            logger.warning("Falha em scan_subnet: %s", exc)
            return []

    @classmethod
    def _parse_nmap_hosts_xml(cls, xml_text: str) -> list[NeighborDevice]:
        """Parser puro de ``nmap -sn -oX`` → lista de ``NeighborDevice`` (hosts 'up').

        Função pura e testável sem depender do ``nmap`` ou da rede: basta alimentá-la
        com o XML de saída. Hosts com ``state != "up"`` são descartados.
        """
        devices: list[NeighborDevice] = []
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return []
        default_gw_ip, _ = cls.get_default_gateway()
        for host in root.iter("host"):
            status = host.find("status")
            if status is None or status.get("state") != "up":
                continue
            ip = ""
            mac = ""
            nmap_vendor = ""
            for addr in host.findall("address"):
                addrtype = addr.get("addrtype")
                if addrtype == "ipv4":
                    ip = addr.get("addr", "")
                elif addrtype == "mac":
                    mac = addr.get("addr", "")
                    nmap_vendor = addr.get("vendor", "") or ""
            if not ip:
                continue
            if mac:
                vendor_oui, dev_type = OUIResolver.resolve(mac)
                # nmap traz o fabricante mais preciso quando o conhece; OUI é fallback.
                if vendor_oui == "Dispositivo de Rede" and nmap_vendor:
                    vendor = nmap_vendor
                else:
                    vendor = vendor_oui
            else:
                vendor = nmap_vendor or "Desconhecido"
                dev_type = "unknown"
            if ip == default_gw_ip:
                dev_type = "router"
                if vendor in ("Dispositivo de Rede", "", "Desconhecido"):
                    vendor = "Roteador da Rede"
            hostname = ""
            hostnames = host.find("hostnames")
            if hostnames is not None:
                for hn in hostnames.findall("hostname"):
                    name = hn.get("name")
                    if name:
                        hostname = name
                        break
            devices.append(
                NeighborDevice(
                    ip=ip,
                    mac=mac,
                    hostname=hostname,
                    vendor=vendor,
                    device_type=dev_type,
                    interface="",
                    is_online=True,
                )
            )
        return devices

    @classmethod
    def identify_device(cls, ip: str, timeout_sec: float = 30.0) -> dict[str, Any]:
        """Fingerprint **ativo** de um dispositivo: SO (``nmap -O``) + serviços (``-sV``).

        Read-only. Devolve dicionário com ``mac``, ``vendor``, ``hostname``, ``os_name``,
        ``os_family``, ``device_type``, ``open_ports`` e ``is_up``. Requer ``nmap``.
        """
        if not shutil.which("nmap"):
            return {"ok": False, "error": "nmap ausente: identify_device indisponível."}
        try:
            proc = subprocess.run(
                ["nmap", "-O", "-sV", "-T4", "--host-timeout", "15s", "-oX", "-", ip],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
            if proc.returncode != 0:
                return {"ok": False, "error": f"nmap retornou {proc.returncode}"}
            return cls._parse_nmap_device_xml(proc.stdout, ip)
        except Exception as exc:
            return {"ok": False, "error": f"Falha na identificação do dispositivo: {exc}"}

    @classmethod
    def _parse_nmap_device_xml(cls, xml_text: str, ip: str) -> dict[str, Any]:
        """Parser puro de ``nmap -O -sV -oX`` → dicionário de fingerprint.

        Função pura e testável: aceita o XML de saída e extrai SO, fabricante,
        hostname e portas abertas. Sem SO detectado mas com MAC, cai no OUIResolver.
        """
        result: dict[str, Any] = {
            "ok": True,
            "ip": ip,
            "mac": "",
            "vendor": "",
            "hostname": "",
            "os_name": None,
            "os_family": None,
            "os_vendor": None,
            "device_type": "unknown",
            "open_ports": [],
            "is_up": False,
        }
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return {"ok": False, "error": "XML de resposta inválido."}
        host = root.find("host")
        if host is None:
            result["note"] = "host não encontrado na resposta"
            return result
        status = host.find("status")
        result["is_up"] = bool(status is not None and status.get("state") == "up")
        for addr in host.findall("address"):
            if addr.get("addrtype") == "mac":
                result["mac"] = addr.get("addr", "")
                result["vendor"] = addr.get("vendor", "") or ""
        hostnames = host.find("hostnames")
        if hostnames is not None:
            for hn in hostnames.findall("hostname"):
                name = hn.get("name")
                if name:
                    result["hostname"] = name
                    break
        os_el = host.find("os")
        if os_el is not None:
            best = None
            best_acc = -1
            for match in os_el.findall("osmatch"):
                try:
                    acc = int(match.get("accuracy", "0"))
                except ValueError:
                    acc = 0
                if acc > best_acc:
                    best_acc = acc
                    best = match
            if best is not None:
                result["os_name"] = best.get("name")
                cls_el = best.find("osclass")
                if cls_el is not None:
                    result["os_family"] = cls_el.get("osfamily")
                    result["os_vendor"] = cls_el.get("vendor")
                    result["device_type"] = _nmap_type_to_device_type(cls_el.get("type"))
        if not result["os_name"] and result["mac"]:
            vendor_oui, dev_type = OUIResolver.resolve(result["mac"])
            if vendor_oui != "Dispositivo de Rede":
                result["vendor"] = result["vendor"] or vendor_oui
                if dev_type != "unknown":
                    result["device_type"] = dev_type
        ports_el = host.find("ports")
        if ports_el is not None:
            for port in ports_el.findall("port"):
                if port.get("protocol") != "tcp":
                    continue
                state = port.find("state")
                if state is None or state.get("state") != "open":
                    continue
                svc = port.find("service")
                svc_name = svc.get("name") if svc is not None else ""
                result["open_ports"].append(
                    {
                        "port": int(port.get("portid", "0")),
                        "service": svc_name,
                        "product": (svc.get("product") if svc is not None else "") or "",
                        "version": (svc.get("version") if svc is not None else "") or "",
                    }
                )
        return result


def _nmap_type_to_device_type(nmap_type: str | None) -> str:
    """Mapeia o ``osclass.type`` do nmap para o vocabulário de ``device_type``.

    Valores comuns do nmap: 'router', 'general purpose', 'phone', 'printer',
    'media device', 'WAP', 'firewall', 'webcam', 'game console', 'storage-misc'…
    """
    if not nmap_type:
        return "unknown"
    t = nmap_type.strip().lower()
    mapping = {
        "router": "router",
        "firewall": "router",
        "wap": "router",
        "bridge": "router",
        "general purpose": "computer",
        "printer": "printer",
        "media device": "tv",
        "phone": "phone",
        "smartphone": "phone",
        "tablet": "phone",
        "game console": "iot",
        "webcam": "iot",
        "tv": "tv",
        "pda": "phone",
    }
    return mapping.get(t, "unknown")


def _device_to_dict(dev: "NeighborDevice") -> dict[str, Any]:
    """Converte um ``NeighborDevice`` em dicionário plano para persistência."""
    return {
        "mac": dev.mac,
        "ip": dev.ip,
        "vendor": dev.vendor,
        "device_type": dev.device_type,
        "hostname": dev.hostname,
    }


def _safe_new_device(dev: "NeighborDevice") -> dict[str, Any]:
    """Versão segura de um device recém-visto para o modelo: MAC mascarado (OUI preservado)."""
    return {
        "ip": dev.ip,
        "mac_masked": NetworkInspector.mask_mac(dev.mac),
        "vendor": dev.vendor,
        "device_type": dev.device_type,
        "hostname": dev.hostname,
    }


class DeviceInventory:
    """Baseline persistente de dispositivos LAN conhecidos — 100% host-local e privado.

    Não toca na rede nem no desktop: é apenas estado do assistente num arquivo SQLite
    dentro do ``XDG_DATA_HOME`` (``~/.local/share/zorin-copilot/lan_inventory.db``).
    Foi separado do ``MemoryManager`` de propósito: mantém a semântica de rede no
    módulo de rede e permite testar contra um DB temporário sem risco ao
    ``memory.db`` real do usuário.

    Princípio ``None ≠ 0.0``: baseline vazio é um estado **reconhecido** (ainda não
    varremos), não "dispositivo desconhecido". O ``monitor_lan`` semeia silenciosamente
    nesse caso (em vez de gritar a LAN inteira de uma vez). Um MAC ausente de um
    baseline **já populado** é o que dispara o alerta — e nunca bloqueia sozinho.
    """

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
            dir_path = Path(base) / "zorin-copilot"
            dir_path.mkdir(parents=True, exist_ok=True)
            self.db_path = dir_path / "lan_inventory.db"
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS known_devices (
                    mac TEXT PRIMARY KEY,
                    ip TEXT NOT NULL DEFAULT '',
                    vendor TEXT NOT NULL DEFAULT 'Desconhecido',
                    device_type TEXT NOT NULL DEFAULT 'unknown',
                    hostname TEXT NOT NULL DEFAULT '',
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    trusted INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_known_devices_trusted "
                "ON known_devices (trusted)"
            )
            conn.commit()

    @staticmethod
    def _norm_mac(mac: str) -> str:
        if not mac:
            return ""
        return mac.replace("-", ":").lower()

    def get_all(self) -> list[dict[str, Any]]:
        """Devolve todos os dispositivos conhecidos como dicionários (com ``trusted``)."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT mac, ip, vendor, device_type, hostname, first_seen, last_seen, trusted "
                "FROM known_devices ORDER BY last_seen DESC"
            ).fetchall()
        return [
            {
                "mac": r["mac"],
                "ip": r["ip"],
                "vendor": r["vendor"],
                "device_type": r["device_type"],
                "hostname": r["hostname"],
                "first_seen": r["first_seen"],
                "last_seen": r["last_seen"],
                "trusted": bool(r["trusted"]),
            }
            for r in rows
        ]

    def get_known_macs(self) -> set[str]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT mac FROM known_devices").fetchall()
        return {r["mac"] for r in rows}

    def find_by_ip(self, ip: str) -> dict[str, Any] | None:
        """Devolve o device conhecido mais recente para um IP (case-insensitive)."""
        if not ip:
            return None
        target = ip.strip().lower()
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT mac, ip, vendor, device_type, hostname, first_seen, last_seen, trusted "
                "FROM known_devices WHERE lower(ip)=? ORDER BY last_seen DESC LIMIT 1",
                (target,),
            ).fetchall()
        if not rows:
            return None
        r = rows[0]
        return {
            "mac": r["mac"],
            "ip": r["ip"],
            "vendor": r["vendor"],
            "device_type": r["device_type"],
            "hostname": r["hostname"],
            "first_seen": r["first_seen"],
            "last_seen": r["last_seen"],
            "trusted": bool(r["trusted"]),
        }

    def count(self) -> int:
        with self._get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM known_devices").fetchone()
        return row["n"] or 0

    def remember(self, device: "dict[str, Any] | NeighborDevice", trusted: bool = False) -> bool:
        """Insere ou atualiza um device no baseline (upsert). Mantém ``first_seen`` e
        ``trusted`` do registro existente quando há conflito — re-ver o device não o
        promove a confiável nem reseta sua origem. Aceita ``dict`` (via
        ``_device_to_dict``) ou diretamente um ``NeighborDevice``."""
        if isinstance(device, NeighborDevice):
            device = _device_to_dict(device)
        mac = self._norm_mac(device.get("mac", ""))
        if not mac:
            return False
        now = datetime.datetime.now().isoformat()
        ip = device.get("ip", "") or ""
        vendor = device.get("vendor", "") or "Desconhecido"
        dev_type = device.get("device_type", "") or "unknown"
        hostname = device.get("hostname", "") or ""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO known_devices (mac, ip, vendor, device_type, hostname, first_seen, last_seen, trusted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mac) DO UPDATE SET
                    ip=excluded.ip,
                    vendor=excluded.vendor,
                    device_type=excluded.device_type,
                    hostname=excluded.hostname,
                    last_seen=excluded.last_seen
                """,
                (mac, ip, vendor, dev_type, hostname, now, now, 1 if trusted else 0),
            )
            conn.commit()
        return True

    def mark_trusted(self, mac: str, trusted: bool = True) -> bool:
        mac = self._norm_mac(mac)
        if not mac:
            return False
        with self._get_connection() as conn:
            cur = conn.execute(
                "UPDATE known_devices SET trusted=? WHERE mac=?",
                (1 if trusted else 0, mac),
            )
            conn.commit()
            return cur.rowcount > 0

    def forget(self, mac: str) -> bool:
        mac = self._norm_mac(mac)
        if not mac:
            return False
        with self._get_connection() as conn:
            cur = conn.execute("DELETE FROM known_devices WHERE mac=?", (mac,))
            conn.commit()
            return cur.rowcount > 0

    def clear(self) -> int:
        with self._get_connection() as conn:
            cur = conn.execute("DELETE FROM known_devices")
            conn.commit()
            return cur.rowcount


@classmethod
def monitor_lan(cls, cidr: str | None = None, inventory: "DeviceInventory | None" = None,
                seed_if_empty: bool = True, timeout_sec: float = 45.0) -> dict[str, Any]:
    """Varre a sub-rede e confronta contra o baseline persistido.

    Fluxo:
    1. Baseline vazio + ``seed_if_empty`` → semeia (remember todos) e retorna
       ``seeded=True`` **sem** alertar (modelo "estabelecer conhecidos").
    2. Baseline populado → dispositivos com MAC ausente viram ``new_devices`` (o
       alerta) e são imediatamente lembrados (para não re-alertar). Dispositivos
       conhecidos porém ``trusted=False`` entram em ``untrusted_present``
       (informativo). Nunca bloqueia nada.
    """
    if inventory is None:
        inventory = DeviceInventory()
    cidr = cidr or cls.local_subnet_cidr() or "192.168.1.0/24"
    devices = cls.scan_subnet(cidr, timeout_sec=timeout_sec)

    known = {d["mac"]: d for d in inventory.get_all()}

    if not known and seed_if_empty:
        for dev in devices:
            if dev.mac:
                inventory.remember(_device_to_dict(dev), trusted=False)
        return {
            "ok": True,
            "seeded": True,
            "cidr": cidr,
            "total_seen": len(devices),
            "baseline_size": inventory.count(),
            "new_devices": [],
            "untrusted_present": [],
            "missing_count": 0,
        }

    new_devices: list[NeighborDevice] = []
    untrusted_present: list[NeighborDevice] = []
    seen_macs: set[str] = set()
    for dev in devices:
        mac = dev.mac.lower()
        if not mac:
            continue
        seen_macs.add(mac)
        if mac not in known:
            new_devices.append(dev)
            inventory.remember(_device_to_dict(dev), trusted=False)
        elif not known[mac]["trusted"]:
            untrusted_present.append(dev)

    missing_count = sum(1 for mac in known if mac not in seen_macs)
    return {
        "ok": True,
        "seeded": False,
        "cidr": cidr,
        "total_seen": len(devices),
        "baseline_size": inventory.count(),
        "new_devices": [_safe_new_device(d) for d in new_devices],
        "untrusted_present": [_safe_new_device(d) for d in untrusted_present],
        "missing_count": missing_count,
    }


@classmethod
def establish_baseline(cls, cidr: str | None = None, inventory: "DeviceInventory | None" = None,
                       trusted: bool = True, timeout_sec: float = 45.0) -> dict[str, Any]:
    """(Re)semeia o baseline a partir de uma varredura atual: tudo o que está na LAN
    agora vira "conhecido" (e, por padrão, confiável). Útil para o usuário dizer
    "verifiquei minha rede, confie nestes". Read-only na rede; escreve só estado local.
    """
    if inventory is None:
        inventory = DeviceInventory()
    cidr = cidr or cls.local_subnet_cidr() or "192.168.1.0/24"
    devices = cls.scan_subnet(cidr, timeout_sec=timeout_sec)
    for dev in devices:
        if dev.mac:
            inventory.remember(_device_to_dict(dev), trusted=trusted)
    return {
        "ok": True,
        "cidr": cidr,
        "seen": len(devices),
        "baseline_size": inventory.count(),
    }


# Monitoramento e baseline são expostos como classmethods de NetworkInspector
# (definidos acima como funções de módulo para ficarem após DeviceInventory).
NetworkInspector.monitor_lan = monitor_lan
NetworkInspector.establish_baseline = establish_baseline
