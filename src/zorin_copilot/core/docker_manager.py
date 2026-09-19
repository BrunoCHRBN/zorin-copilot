"""Módulo de gestão e inspeção de containers Docker para o Zorin Copilot.

Implementa comunicação de alta performance diretamente com o daemon Docker
através do UNIX domain socket (/var/run/docker.sock) utilizando a biblioteca
padrão HTTP do Python, com fallback determinístico para o CLI docker.
Inclui destilação heurística de logs com colapso de repetições e sanitização
de privacidade (Zero-Token-Bloat).
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import re
import socket
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DOCKER_SOCKET_PATH = "/var/run/docker.sock"


class UnixHTTPConnection(http.client.HTTPConnection):
    """Conexão HTTP sobre UNIX Domain Socket (sem dependências externas)."""

    def __init__(self, socket_path: str, timeout: float = 4.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


@dataclass
class ContainerPortMapping:
    """Mapeamento de portas de container."""
    container_port: int
    host_ip: str = ""
    host_port: int = 0
    proto: str = "tcp"


@dataclass
class ContainerInfo:
    """Metadados e métricas estruturadas de um container Docker."""
    id: str
    name: str
    image: str
    state: str  # running, exited, paused, restarting
    status: str  # ex: "Up 2 hours", "Exited (0) 5 minutes ago"
    created: int
    ports: list[str] = field(default_factory=list)
    health: str = "unknown"  # healthy, unhealthy, starting, none, unknown
    cpu_percent: float = 0.0
    memory_usage_mb: float = 0.0
    memory_limit_mb: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DockerManager:
    """Gerenciador operacional de containers com foco em estabilidade e segurança."""

    def __init__(self, socket_path: str = DOCKER_SOCKET_PATH) -> None:
        self.socket_path = socket_path

    def is_available(self) -> bool:
        """Verifica se o Docker está instalado e acessível (socket ou CLI)."""
        if os.path.exists(self.socket_path) and os.access(self.socket_path, os.R_OK | os.W_OK):
            return True
        # Fallback para checar se o binário docker responde
        try:
            res = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            return res.returncode == 0
        except Exception:
            return False

    def _socket_request(
        self,
        method: str,
        endpoint: str,
        body: dict[str, Any] | None = None,
        timeout: float = 4.0,
    ) -> tuple[int, Any]:
        """Executa uma requisição HTTP via socket UNIX."""
        if not os.path.exists(self.socket_path) or not os.access(self.socket_path, os.R_OK | os.W_OK):
            raise ConnectionError(f"Socket do Docker inacessível em {self.socket_path}")

        conn = UnixHTTPConnection(self.socket_path, timeout=timeout)
        headers = {"Host": "localhost", "Content-Type": "application/json"}
        req_data = json.dumps(body) if body else None

        try:
            conn.request(method, endpoint, body=req_data, headers=headers)
            response = conn.getresponse()
            raw_body = response.read()
            status = response.status

            if not raw_body:
                return status, None

            try:
                parsed = json.loads(raw_body.decode("utf-8", errors="replace"))
                return status, parsed
            except Exception:
                return status, raw_body.decode("utf-8", errors="replace")
        finally:
            conn.close()

    def list_containers(self, all_containers: bool = True) -> list[ContainerInfo]:
        """Lista containers com metadados e status de saúde."""
        containers: list[ContainerInfo] = []

        # Tentativa 1: Via Socket UNIX
        try:
            status_code, data = self._socket_request("GET", f"/containers/json?all={1 if all_containers else 0}")
            if status_code == 200 and isinstance(data, list):
                for item in data:
                    c_id = item.get("Id", "")[:12]
                    names = item.get("Names", [])
                    name = names[0].lstrip("/") if names else c_id
                    image = item.get("Image", "")
                    state = item.get("State", "unknown")
                    status_str = item.get("Status", "")

                    # Portas
                    ports_list: list[str] = []
                    for p in item.get("Ports", []):
                        priv = p.get("PrivatePort")
                        pub = p.get("PublicPort")
                        ptype = p.get("Type", "tcp")
                        if pub:
                            ports_list.append(f"{pub}->{priv}/{ptype}")
                        elif priv:
                            ports_list.append(f"{priv}/{ptype}")

                    # Extrai healthcheck se presente na string Status
                    health = "none"
                    if "(healthy)" in status_str:
                        health = "healthy"
                    elif "(unhealthy)" in status_str:
                        health = "unhealthy"
                    elif "(health: starting)" in status_str:
                        health = "starting"

                    containers.append(
                        ContainerInfo(
                            id=c_id,
                            name=name,
                            image=image,
                            state=state,
                            status=status_str,
                            created=item.get("Created", 0),
                            ports=ports_list,
                            health=health,
                        )
                    )
                return containers
        except Exception as exc:
            logger.debug("Falha na consulta do Docker via socket, tentando CLI: %s", exc)

        # Tentativa 2: Fallback via CLI docker
        try:
            cmd = [
                "docker",
                "ps",
                "-a" if all_containers else "",
                "--format",
                "{{json .}}",
            ]
            cmd = [c for c in cmd if c]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3.0)
            if proc.returncode == 0:
                for line in proc.stdout.strip().splitlines():
                    if not line:
                        continue
                    obj = json.loads(line)
                    status_text = obj.get("Status", "")
                    health = "none"
                    if "(healthy)" in status_text:
                        health = "healthy"
                    elif "(unhealthy)" in status_text:
                        health = "unhealthy"
                    elif "(health: starting)" in status_text:
                        health = "starting"

                    ports_raw = obj.get("Ports", "")
                    ports_list = [p.strip() for p in ports_raw.split(",") if p.strip()]

                    containers.append(
                        ContainerInfo(
                            id=obj.get("ID", "")[:12],
                            name=obj.get("Names", ""),
                            image=obj.get("Image", ""),
                            state=obj.get("State", "unknown").lower(),
                            status=status_text,
                            created=0,
                            ports=ports_list,
                            health=health,
                        )
                    )
        except Exception as exc:
            logger.warning("Falha geral ao listar containers Docker: %s", exc)

        return containers

    @staticmethod
    def _clean_docker_stream_frames(raw_data: bytes) -> str:
        """Decodifica stream multiplexado do Docker (stdout/stderr com cabeçalho de 8 bytes)."""
        lines: list[str] = []
        offset = 0
        length = len(raw_data)

        # Se não tiver formato de frame de 8 bytes, decodifica diretamente
        if length < 8:
            return raw_data.decode("utf-8", errors="replace")

        # Tenta decodificar frames padrão multiplexados do Docker
        while offset + 8 <= length:
            stream_type = raw_data[offset]
            if stream_type not in (1, 2):  # 1 = stdout, 2 = stderr
                # Dados normais de texto simples
                return raw_data.decode("utf-8", errors="replace")
            frame_size = int.from_bytes(raw_data[offset + 4 : offset + 8], byteorder="big")
            offset += 8
            if offset + frame_size > length:
                chunk = raw_data[offset:]
                lines.append(chunk.decode("utf-8", errors="replace"))
                break
            chunk = raw_data[offset : offset + frame_size]
            lines.append(chunk.decode("utf-8", errors="replace"))
            offset += frame_size

        return "".join(lines) if lines else raw_data.decode("utf-8", errors="replace")

    def get_container_logs(
        self,
        name_or_id: str,
        tail: int = 60,
        filter_errors_only: bool = False,
    ) -> dict[str, Any]:
        """Obtém e destila logs de um container com eliminação de ruído e deduplicação."""
        raw_text = ""

        # Via Socket
        try:
            endpoint = f"/containers/{name_or_id}/logs?stdout=1&stderr=1&tail={tail}"
            conn = UnixHTTPConnection(self.socket_path, timeout=5.0)
            conn.request("GET", endpoint, headers={"Host": "localhost"})
            res = conn.getresponse()
            raw_bytes = res.read()
            conn.close()
            if res.status == 200:
                raw_text = self._clean_docker_stream_frames(raw_bytes)
        except Exception as exc:
            logger.debug("Falha na leitura de logs via socket: %s", exc)

        # Fallback CLI
        if not raw_text:
            try:
                proc = subprocess.run(
                    ["docker", "logs", "--tail", str(tail), name_or_id],
                    capture_output=True,
                    text=True,
                    timeout=4.0,
                )
                raw_text = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
            except Exception as exc:
                return {"error": f"Não foi possível obter logs: {exc}", "container": name_or_id}

        return self.distill_logs(raw_text, filter_errors_only=filter_errors_only, container_name=name_or_id)

    @classmethod
    def distill_logs(
        cls,
        raw_logs: str,
        max_output_lines: int = 35,
        filter_errors_only: bool = False,
        container_name: str = "",
    ) -> dict[str, Any]:
        """Destilação semântica: colapsa linhas repetidas, extrai sinais críticos e sanitiza segredos."""
        if not raw_logs.strip():
            return {
                "container": container_name,
                "total_lines": 0,
                "distilled_lines": [],
                "error_count": 0,
                "summary": "Nenhum registro de log encontrado.",
            }

        lines = [line.strip() for line in raw_logs.splitlines() if line.strip()]
        error_keywords = (
            "error",
            "fatal",
            "panic",
            "exception",
            "failed",
            "critical",
            "traceback",
            "err:",
            "refused",
            "timeout",
            "denied",
        )

        # Filtro de segredos via PrivacyGate (se disponível)
        try:
            from .privacy_gate import PrivacyGate
            sanitized_lines = [PrivacyGate.mask_sensitive_data(l) for l in lines]
        except Exception:
            # Regex básica para chaves/senhas
            secret_pattern = re.compile(r"(password|token|secret|api_key|authorization)=\S+", re.IGNORECASE)
            sanitized_lines = [secret_pattern.sub(r"\1=[REDACTED]", l) for l in lines]

        # Colapso de repetições consecutivas (Deduplicação Heurística)
        collapsed: list[tuple[str, int]] = []
        for line in sanitized_lines:
            if collapsed and collapsed[-1][0] == line:
                collapsed[-1] = (line, collapsed[-1][1] + 1)
            else:
                collapsed.append((line, 1))

        formatted_lines: list[str] = []
        error_count = 0

        for line, count in collapsed:
            is_error = any(kw in line.lower() for kw in error_keywords)
            if is_error:
                error_count += count

            if filter_errors_only and not is_error:
                continue

            if count > 1:
                formatted_lines.append(f"[Repetido {count}x] {line}")
            else:
                formatted_lines.append(line)

        # Limitação da janela
        final_lines = formatted_lines[-max_output_lines:]

        summary = f"{len(lines)} linhas analisadas."
        if error_count > 0:
            summary += f" Detectados {error_count} registros de erro/falha."
        else:
            summary += " Nenhum erro crítico recente identificado."

        return {
            "container": container_name,
            "total_lines": len(lines),
            "distilled_lines": final_lines,
            "error_count": error_count,
            "summary": summary,
        }

    def restart_container(self, name_or_id: str, wait_timeout: float = 6.0) -> dict[str, Any]:
        """Reinicia o container e executa verificação de estado pós-ação."""
        start_time = time.time()
        success = False
        message = ""

        # Via Socket
        try:
            status_code, _ = self._socket_request("POST", f"/containers/{name_or_id}/restart?t=3", timeout=wait_timeout + 2.0)
            if status_code in (204, 200):
                success = True
                message = "Sinal de reinício enviado via socket Docker."
        except Exception as exc:
            logger.debug("Falha no restart via socket: %s", exc)

        # Fallback CLI
        if not success:
            try:
                proc = subprocess.run(
                    ["docker", "restart", name_or_id],
                    capture_output=True,
                    text=True,
                    timeout=wait_timeout + 2.0,
                )
                if proc.returncode == 0:
                    success = True
                    message = "Container reiniciado via CLI docker."
                else:
                    return {"success": False, "container": name_or_id, "error": proc.stderr.strip()}
            except Exception as exc:
                return {"success": False, "container": name_or_id, "error": str(exc)}

        # Verificação pós-ação: aguarda container subir e verifica estado
        time.sleep(1.0)
        containers = self.list_containers(all_containers=True)
        target = next((c for c in containers if c.name == name_or_id or c.id.startswith(name_or_id)), None)

        if not target:
            return {
                "success": success,
                "container": name_or_id,
                "verified": False,
                "message": f"{message} (Container não localizado após reinício)",
            }

        elapsed = round(time.time() - start_time, 2)
        is_running = target.state == "running"

        return {
            "success": is_running,
            "container": target.name,
            "state": target.state,
            "status": target.status,
            "health": target.health,
            "elapsed_seconds": elapsed,
            "verified": True,
            "message": f"Container {target.name} está {target.state} ({target.status}) após {elapsed}s.",
        }

    def stop_container(self, name_or_id: str, timeout_sec: int = 5) -> dict[str, Any]:
        """Interrompe a execução de um container com confirmação de parada."""
        success = False
        try:
            status_code, _ = self._socket_request("POST", f"/containers/{name_or_id}/stop?t={timeout_sec}")
            if status_code in (204, 200, 304):
                success = True
        except Exception:
            pass

        if not success:
            try:
                proc = subprocess.run(["docker", "stop", "-t", str(timeout_sec), name_or_id], capture_output=True, text=True, timeout=timeout_sec + 3)
                success = proc.returncode == 0
            except Exception as exc:
                return {"success": False, "container": name_or_id, "error": str(exc)}

        return {
            "success": success,
            "container": name_or_id,
            "message": f"Container {name_or_id} interrompido com sucesso." if success else "Falha ao interromper container.",
        }
