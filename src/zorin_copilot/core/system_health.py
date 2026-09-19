"""Módulo de telemetria, saúde física de hardware e diagnóstico preditivo do sistema.

Implementa inspeção de sensores térmicos, pressão de memória (PSI / zRAM),
saúde de discos/NVMe (S.M.A.R.T. com salvaguarda de spin-down) e síntese
heurística de falhas críticas no journalctl (Zero-Token-Bloat).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ThermalMetrics:
    """Métricas térmicas e de ventilação do sistema."""
    cpu_temp_c: float = 0.0
    gpu_temp_c: float = 0.0
    nvme_temp_c: float = 0.0
    fans_rpm: dict[str, int] = field(default_factory=dict)
    throttle_warning: bool = False
    status_summary: str = "Normal"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryPressureMetrics:
    """Métricas de memória, pressão PSI e zRAM."""
    total_mb: float = 0.0
    available_mb: float = 0.0
    used_percent: float = 0.0
    swap_total_mb: float = 0.0
    swap_used_mb: float = 0.0
    psi_some_avg10: float = 0.0  # Pressure Stall Information (10s)
    zram_used_mb: float = 0.0
    pressure_level: str = "Baixa"  # Baixa, Moderada, Alta, Crítica

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StorageHealthMetrics:
    """Saúde de unidades de armazenamento (NVMe/SSD/HDD)."""
    device: str
    health_passed: bool = True
    percentage_used: int | None = None
    temperature_c: float | None = None
    error_count: int = 0
    standby_protected: bool = True
    permission_granted: bool = True
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class JournalIncident:
    """Incidente ou erro extraído do diário do sistema."""
    timestamp: str
    unit: str
    message: str
    priority: int
    repeat_count: int = 1


class SystemHealthInspector:
    """Inspetor assíncrono de saúde do host, termodinâmica e logs de erro."""

    @classmethod
    def get_thermals(cls) -> ThermalMetrics:
        """Coleta temperaturas de CPU, GPU e NVMe via lm_sensors (JSON puro)."""
        metrics = ThermalMetrics()

        try:
            res = subprocess.run(["sensors", "-j"], capture_output=True, text=True, timeout=2.5)
            if res.returncode == 0:
                data = json.loads(res.stdout)
                cls._parse_sensors_json(data, metrics)
        except Exception as exc:
            logger.debug("Falha ao consultar sensors -j: %s. Tentando sysfs fallback.", exc)
            cls._fallback_sysfs_thermals(metrics)

        # Avaliação de Throttling
        max_temp = max(metrics.cpu_temp_c, metrics.gpu_temp_c)
        if max_temp >= 90.0:
            metrics.throttle_warning = True
            metrics.status_summary = f"Alerta Crítico: {max_temp:.1f}°C (Risco iminente de Throttling)"
        elif max_temp >= 80.0:
            metrics.throttle_warning = True
            metrics.status_summary = f"Atenção: Carga térmica elevada ({max_temp:.1f}°C)"
        else:
            metrics.status_summary = f"Normal ({max_temp:.1f}°C estável)"

        return metrics

    @classmethod
    def _parse_sensors_json(cls, data: dict[str, Any], metrics: ThermalMetrics) -> None:
        """Extrai temperaturas de adaptadores conhecidos (AMD, Intel, Nvidia, NVMe)."""
        for chip, sensors in data.items():
            chip_lower = chip.lower()

            # CPU (k10temp, coretemp, zenpower)
            if any(k in chip_lower for k in ("k10temp", "coretemp", "zenpower", "cpu")):
                for sensor_key, values in sensors.items():
                    if isinstance(values, dict):
                        for k, v in values.items():
                            if k.endswith("_input") and isinstance(v, (int, float)):
                                if "tctl" in sensor_key.lower() or "package" in sensor_key.lower() or metrics.cpu_temp_c == 0:
                                    metrics.cpu_temp_c = round(float(v), 1)

            # GPU (amdgpu, nouveau, nvidia)
            elif any(k in chip_lower for k in ("amdgpu", "nouveau", "nvidia", "gpu")):
                for sensor_key, values in sensors.items():
                    if isinstance(values, dict):
                        # Fans
                        if "fan" in sensor_key.lower():
                            for k, v in values.items():
                                if k.endswith("_input") and isinstance(v, (int, float)):
                                    metrics.fans_rpm[sensor_key] = int(v)
                        # Temp
                        for k, v in values.items():
                            if k.endswith("_input") and isinstance(v, (int, float)):
                                if "edge" in sensor_key.lower() or "junction" in sensor_key.lower() or metrics.gpu_temp_c == 0:
                                    metrics.gpu_temp_c = max(metrics.gpu_temp_c, round(float(v), 1))

            # NVMe
            elif "nvme" in chip_lower:
                for sensor_key, values in sensors.items():
                    if isinstance(values, dict):
                        for k, v in values.items():
                            if k.endswith("_input") and isinstance(v, (int, float)):
                                if "composite" in sensor_key.lower() or metrics.nvme_temp_c == 0:
                                    metrics.nvme_temp_c = round(float(v), 1)

    @classmethod
    def _fallback_sysfs_thermals(cls, metrics: ThermalMetrics) -> None:
        """Fallback lendo diretamente de /sys/class/thermal."""
        try:
            import glob
            for zone in glob.glob("/sys/class/thermal/thermal_zone*"):
                try:
                    with open(os.path.join(zone, "temp"), "r") as f:
                        temp = float(f.read().strip()) / 1000.0
                    if metrics.cpu_temp_c == 0:
                        metrics.cpu_temp_c = round(temp, 1)
                except Exception:
                    continue
        except Exception:
            pass

    @classmethod
    def get_memory_pressure(cls) -> MemoryPressureMetrics:
        """Coleta métricas de consumo de RAM, swap, zRAM e PSI (Pressure Stall Information)."""
        metrics = MemoryPressureMetrics()

        # 1. /proc/meminfo
        try:
            meminfo: dict[str, float] = {}
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip().split()[0]
                        meminfo[key] = float(val) / 1024.0  # MB

            metrics.total_mb = round(meminfo.get("MemTotal", 0.0), 1)
            metrics.available_mb = round(meminfo.get("MemAvailable", 0.0), 1)
            if metrics.total_mb > 0:
                used = metrics.total_mb - metrics.available_mb
                metrics.used_percent = round((used / metrics.total_mb) * 100.0, 1)

            metrics.swap_total_mb = round(meminfo.get("SwapTotal", 0.0), 1)
            swap_free = meminfo.get("SwapFree", 0.0)
            metrics.swap_used_mb = round(metrics.swap_total_mb - swap_free, 1)
        except Exception as exc:
            logger.debug("Falha na leitura do meminfo: %s", exc)

        # 2. Pressure Stall Information (PSI)
        try:
            psi_path = "/proc/pressure/memory"
            if os.path.exists(psi_path):
                with open(psi_path, "r") as f:
                    for line in f:
                        if line.startswith("some"):
                            # some avg10=0.00 avg60=0.00 avg300=0.00 total=49621
                            match = re.search(r"avg10=([\d\.]+)", line)
                            if match:
                                metrics.psi_some_avg10 = float(match.group(1))
        except Exception as exc:
            logger.debug("PSI não suportado: %s", exc)

        # Nível de pressão
        if metrics.psi_some_avg10 >= 20.0 or metrics.used_percent >= 95.0:
            metrics.pressure_level = "Crítica (Risco de OOM)"
        elif metrics.psi_some_avg10 >= 5.0 or metrics.used_percent >= 85.0:
            metrics.pressure_level = "Alta"
        elif metrics.psi_some_avg10 >= 1.0 or metrics.used_percent >= 70.0:
            metrics.pressure_level = "Moderada"
        else:
            metrics.pressure_level = "Baixa"

        return metrics

    @classmethod
    def get_storage_health(cls, device: str = "/dev/nvme0n1") -> StorageHealthMetrics:
        """Coleta saúde S.M.A.R.T. com proteção contra despertar de discos mecânicos."""
        metrics = StorageHealthMetrics(device=device)

        # Salvaguarda: -n standby impede que discos dormindo girem
        cmd = ["smartctl", "-j", "-H", "-A", "-n", "standby", device]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3.0)
            data = json.loads(proc.stdout) if proc.stdout else {}

            # Verifica mensagens de erro
            messages = data.get("smartctl", {}).get("messages", [])
            for msg in messages:
                text = msg.get("string", "")
                if "Permission denied" in text:
                    metrics.permission_granted = False
                    metrics.notes = "Permissão negada (adicione o usuário ao grupo 'disk' para S.M.A.R.T. completo)."
                    return metrics
                if "Device is in STANDBY" in text:
                    metrics.notes = "Unidade em modo STANDBY (preservada sem spin-up mecânico)."
                    return metrics

            # Avaliação de saúde
            smart_status = data.get("smart_status", {})
            metrics.health_passed = smart_status.get("passed", True)

            # NVMe health log
            nvme_log = data.get("nvme_smart_health_information_log", {})
            if nvme_log:
                metrics.percentage_used = nvme_log.get("percentage_used")
                metrics.temperature_c = float(nvme_log.get("temperature", 0))
                metrics.error_count = int(nvme_log.get("media_errors", 0))
                metrics.notes = f"Desgaste do SSD: {metrics.percentage_used}%. Erros de mídia: {metrics.error_count}."
            else:
                metrics.notes = "Relatório S.M.A.R.T. íntegro (sem setores anômalos detectados)."

        except Exception as exc:
            logger.debug("Falha ao ler S.M.A.R.T.: %s", exc)
            metrics.notes = f"smartctl indisponível ou dispositivo inacessível ({exc})"

        return metrics

    @classmethod
    def get_journal_incidents(cls, lines: int = 50) -> list[JournalIncident]:
        """Extrai erros críticos do journalctl com agrupamento heurístico e deduplicação."""
        incidents: list[JournalIncident] = []

        try:
            cmd = ["journalctl", "-p", "err..emerg", "-n", str(lines), "--no-pager", "--output=json"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=4.0)
            if proc.returncode != 0:
                return incidents

            raw_incidents: list[dict[str, Any]] = []
            for l in proc.stdout.strip().splitlines():
                if not l:
                    continue
                try:
                    obj = json.loads(l)
                    raw_incidents.append(obj)
                except Exception:
                    continue

            # Deduplicação sequencial
            collapsed: list[dict[str, Any]] = []
            for item in raw_incidents:
                unit = item.get("USER_UNIT") or item.get("_SYSTEMD_USER_UNIT") or item.get("_SYSTEMD_UNIT") or item.get("SYSLOG_IDENTIFIER") or "system"
                raw_msg = item.get("MESSAGE")
                if isinstance(raw_msg, list):
                    msg = bytes(raw_msg).decode("utf-8", errors="replace").strip()
                else:
                    msg = str(raw_msg or "").strip()
                prio = int(item.get("PRIORITY", 3))
                ts = str(item.get("__REALTIME_TIMESTAMP") or "")

                if collapsed and collapsed[-1]["unit"] == unit and collapsed[-1]["message"] == msg:
                    collapsed[-1]["count"] += 1
                else:
                    collapsed.append({
                        "unit": unit,
                        "message": msg,
                        "priority": prio,
                        "timestamp": ts,
                        "count": 1,
                    })

            for c in collapsed[-30:]:  # Limita aos últimos 30 incidentes únicos
                incidents.append(
                    JournalIncident(
                        timestamp=c["timestamp"],
                        unit=c["unit"],
                        message=c["message"],
                        priority=c["priority"],
                        repeat_count=c["count"],
                    )
                )

        except Exception as exc:
            logger.warning("Falha na consulta do journalctl: %s", exc)

        return incidents

    @classmethod
    def get_complete_report(cls) -> dict[str, Any]:
        """Gera um diagnóstico unificado de alta densidade sem travar a interface."""
        thermals = cls.get_thermals()
        memory = cls.get_memory_pressure()
        storage = cls.get_storage_health()
        incidents = cls.get_journal_incidents(lines=30)

        return {
            "thermals": thermals.to_dict(),
            "memory": memory.to_dict(),
            "storage": storage.to_dict(),
            "incidents_count": len(incidents),
            "incidents_summary": [
                {
                    "unit": inc.unit,
                    "message": inc.message[:140] + ("..." if len(inc.message) > 140 else ""),
                    "repeats": inc.repeat_count,
                }
                for inc in incidents[-5:]
            ],
            "timestamp": time.time(),
        }
