# Decisão de design: Ferramentas nativas de alta densidade para inspeção de sistema e busca de código.
# Permite ao agente ReAct e ao Gemini Live localizar definições, funções, variáveis e arquivos sem
# esgotar o orçamento de passos, além de inspecionar consumo de recursos e processos ativos do desktop Linux.

"""Inspeção de processos do sistema e busca nativa de código em alta velocidade."""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".idea",
    ".vscode",
    "dist",
    "build",
    ".egg-info",
    "target",
}


@dataclass
class CodeMatch:
    """Representa uma linha correspondente encontrada na busca de código."""

    file: str
    line: int
    content: str
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "file": self.file,
            "line": self.line,
            "content": self.content,
        }
        if self.context_before:
            data["context_before"] = self.context_before
        if self.context_after:
            data["context_after"] = self.context_after
        return data


@dataclass
class CodeSearchResult:
    """Resultado estruturado de uma pesquisa em código-fonte."""

    pattern: str
    path: str
    engine: str
    matches: list[CodeMatch] = field(default_factory=list)
    total_matches: int = 0
    truncated: bool = False
    elapsed_sec: float = 0.0

    def format_summary(self) -> str:
        """Gera um resumo compacto e legível para contexto de LLMs e observações de agentes."""
        if not self.matches:
            return f"Nenhuma ocorrência encontrada para '{self.pattern}' em '{self.path}'."

        grouped: dict[str, list[CodeMatch]] = {}
        for m in self.matches:
            grouped.setdefault(m.file, []).append(m)

        lines = [
            f"Encontradas {self.total_matches} ocorrências em {len(grouped)} arquivos "
            f"({self.elapsed_sec:.2f}s via {self.engine}):"
        ]

        for file_path, file_matches in grouped.items():
            lines.append(f"\n{file_path}:")
            for m in file_matches:
                lines.append(f"  L{m.line}: {m.content.strip()}")

        if self.truncated:
            lines.append("\n[... Limite máximo de correspondências atingido; refine com file_glob ou padrão mais estrito ...]")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "success": True,
            "pattern": self.pattern,
            "path": self.path,
            "engine": self.engine,
            "total_matches": self.total_matches,
            "truncated": self.truncated,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "matches": [m.to_dict() for m in self.matches],
            "summary": self.format_summary(),
            "message": f"Encontradas {self.total_matches} ocorrências para '{self.pattern}'.",
        }


class CodeSearchEngine:
    """Motor de busca de código com suporte prioritário a ripgrep e fallback em Python puro."""

    _instance: CodeSearchEngine | None = None

    def __init__(self, default_timeout: float = 8.0) -> None:
        self.default_timeout = default_timeout
        self.rg_binary = shutil.which("rg") or shutil.which("ripgrep")

    @classmethod
    def get_default(cls) -> CodeSearchEngine:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def is_ripgrep_available(self) -> bool:
        return bool(self.rg_binary and os.path.exists(self.rg_binary))

    def search(
        self,
        pattern: str,
        path: str = ".",
        file_glob: str = "",
        case_sensitive: bool = False,
        is_regex: bool = True,
        max_results: int = 30,
        context_lines: int = 1,
        timeout: float | None = None,
    ) -> CodeSearchResult:
        """Executa busca de código usando ripgrep se disponível, ou fallback puro em Python."""
        t_start = time.perf_counter()
        target_path = os.path.abspath(os.path.expanduser(path or "."))
        if not os.path.exists(target_path):
            raise FileNotFoundError(f"Caminho não encontrado: '{path}'")

        max_results = max(1, min(max_results, 100))
        context_lines = max(0, min(context_lines, 5))
        eff_timeout = timeout or self.default_timeout

        if self.is_ripgrep_available():
            try:
                return self._search_ripgrep(
                    pattern=pattern,
                    target_path=target_path,
                    file_glob=file_glob,
                    case_sensitive=case_sensitive,
                    is_regex=is_regex,
                    max_results=max_results,
                    context_lines=context_lines,
                    timeout=eff_timeout,
                    t_start=t_start,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                raise
            except Exception as exc:
                logger.warning(f"ripgrep falhou ({exc}); recorrendo ao fallback em Python.")

        return self._search_python(
            pattern=pattern,
            target_path=target_path,
            file_glob=file_glob,
            case_sensitive=case_sensitive,
            is_regex=is_regex,
            max_results=max_results,
            context_lines=context_lines,
            t_start=t_start,
        )

    def _search_ripgrep(
        self,
        pattern: str,
        target_path: str,
        file_glob: str,
        case_sensitive: bool,
        is_regex: bool,
        max_results: int,
        context_lines: int,
        timeout: float,
        t_start: float,
    ) -> CodeSearchResult:
        cmd: list[str] = [
            str(self.rg_binary),
            "--json",
            "--color",
            "never",
            "-m",
            str(max_results),
        ]

        if not case_sensitive:
            cmd.append("-i")
        else:
            cmd.append("-s")

        if not is_regex:
            cmd.append("-F")

        if file_glob.strip():
            cmd.extend(["-g", file_glob.strip()])

        if context_lines > 0:
            cmd.extend(["-C", str(context_lines)])

        cmd.extend(["--", pattern, target_path])

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

        if proc.returncode not in (0, 1):
            err_msg = proc.stderr.strip() or f"Código de saída {proc.returncode}"
            raise RuntimeError(f"Erro no ripgrep: {err_msg}")

        matches: list[CodeMatch] = []
        truncated = False
        current_match: CodeMatch | None = None

        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue

            etype = event.get("type")
            edata = event.get("data", {})

            if etype == "match":
                file_rel = edata.get("path", {}).get("text", "")
                line_no = int(edata.get("line_number") or 1)
                text = edata.get("lines", {}).get("text", "").rstrip("\r\n")

                if target_path and file_rel.startswith(target_path):
                    file_display = os.path.relpath(file_rel, target_path)
                else:
                    file_display = file_rel

                current_match = CodeMatch(
                    file=file_display,
                    line=line_no,
                    content=text,
                )
                matches.append(current_match)

                if len(matches) >= max_results:
                    truncated = True
                    break

            elif etype == "context" and current_match is not None:
                ctx_text = edata.get("lines", {}).get("text", "").rstrip("\r\n")
                ctx_line_no = int(edata.get("line_number") or 0)
                if ctx_line_no < current_match.line:
                    current_match.context_before.append(ctx_text)
                else:
                    current_match.context_after.append(ctx_text)

        elapsed = time.perf_counter() - t_start
        return CodeSearchResult(
            pattern=pattern,
            path=target_path,
            engine="ripgrep",
            matches=matches,
            total_matches=len(matches),
            truncated=truncated,
            elapsed_sec=elapsed,
        )

    def _search_python(
        self,
        pattern: str,
        target_path: str,
        file_glob: str,
        case_sensitive: bool,
        is_regex: bool,
        max_results: int,
        context_lines: int,
        t_start: float,
    ) -> CodeSearchResult:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            rx = re.compile(pattern if is_regex else re.escape(pattern), flags)
        except re.error as exc:
            raise ValueError(f"Expressão regular inválida '{pattern}': {exc}") from exc

        matches: list[CodeMatch] = []
        truncated = False

        paths_to_scan: list[str] = []
        if os.path.isfile(target_path):
            paths_to_scan = [target_path]
        else:
            for root, dirs, files in os.walk(target_path):
                dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
                for fname in files:
                    if file_glob and not fnmatch.fnmatch(fname, file_glob):
                        continue
                    full_p = os.path.join(root, fname)
                    paths_to_scan.append(full_p)

        for fpath in paths_to_scan:
            if len(matches) >= max_results:
                truncated = True
                break

            try:
                if os.path.getsize(fpath) > 10 * 1024 * 1024:
                    continue  # ignora arquivos > 10MB
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    file_lines = f.readlines()
            except Exception:
                continue

            file_display = os.path.relpath(fpath, target_path) if os.path.isdir(target_path) else fpath

            for idx, raw_line in enumerate(file_lines):
                line_content = raw_line.rstrip("\r\n")
                if rx.search(line_content):
                    c_before = [
                        file_lines[i].rstrip("\r\n")
                        for i in range(max(0, idx - context_lines), idx)
                    ]
                    c_after = [
                        file_lines[i].rstrip("\r\n")
                        for i in range(idx + 1, min(len(file_lines), idx + 1 + context_lines))
                    ]
                    matches.append(
                        CodeMatch(
                            file=file_display,
                            line=idx + 1,
                            content=line_content,
                            context_before=c_before,
                            context_after=c_after,
                        )
                    )
                    if len(matches) >= max_results:
                        truncated = True
                        break

        elapsed = time.perf_counter() - t_start
        return CodeSearchResult(
            pattern=pattern,
            path=target_path,
            engine="python_fallback",
            matches=matches,
            total_matches=len(matches),
            truncated=truncated,
            elapsed_sec=elapsed,
        )


@dataclass
class ProcessInfo:
    """Informações estruturadas de um processo em execução no sistema."""

    pid: int
    ppid: int
    cpu_percent: float
    memory_percent: float
    user: str
    command: str
    args: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "ppid": self.ppid,
            "cpu_percent": self.cpu_percent,
            "memory_percent": self.memory_percent,
            "user": self.user,
            "command": self.command,
            "args": self.args,
        }


class ProcessInspector:
    """Inspetor de processos em execução no desktop Linux."""

    _instance: ProcessInspector | None = None

    def __init__(self, default_timeout: float = 5.0) -> None:
        self.default_timeout = default_timeout
        self.ps_binary = shutil.which("ps")

    @classmethod
    def get_default(cls) -> ProcessInspector:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def list_processes(
        self,
        filter_query: str = "",
        sort_by: str = "cpu",
        limit: int = 20,
        timeout: float | None = None,
    ) -> list[ProcessInfo]:
        """Lista e filtra processos ativos no sistema."""
        eff_limit = max(1, min(limit, 50))
        eff_timeout = timeout or self.default_timeout

        if self.ps_binary and os.path.exists(self.ps_binary):
            try:
                return self._list_processes_ps(
                    filter_query=filter_query,
                    sort_by=sort_by,
                    limit=eff_limit,
                    timeout=eff_timeout,
                )
            except Exception as exc:
                logger.warning(f"Comando ps falhou ({exc}); tentando inspecionar /proc.")

        return self._list_processes_proc(
            filter_query=filter_query,
            sort_by=sort_by,
            limit=eff_limit,
        )

    def _list_processes_ps(
        self,
        filter_query: str,
        sort_by: str,
        limit: int,
        timeout: float,
    ) -> list[ProcessInfo]:
        sort_arg = "--sort=-%cpu"
        if sort_by == "memory":
            sort_arg = "--sort=-%mem"
        elif sort_by == "pid":
            sort_arg = "--sort=pid"

        cmd = [
            str(self.ps_binary),
            "-eo",
            "pid,ppid,%cpu,%mem,user,comm,args",
            sort_arg,
        ]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"ps falhou com código {proc.returncode}")

        lines = proc.stdout.strip().splitlines()
        if not lines:
            return []

        results: list[ProcessInfo] = []
        q_lower = filter_query.strip().lower()

        # Pula a primeira linha (cabeçalho PID PPID ...)
        for line in lines[1:]:
            parts = line.strip().split(None, 6)
            if len(parts) < 6:
                continue

            try:
                pid = int(parts[0])
                ppid = int(parts[1])
                cpu_p = float(parts[2].replace(",", "."))
                mem_p = float(parts[3].replace(",", "."))
            except ValueError:
                continue

            user = parts[4]
            comm = parts[5]
            args_str = parts[6] if len(parts) > 6 else comm

            if q_lower:
                matched = (
                    q_lower in comm.lower()
                    or q_lower in args_str.lower()
                    or q_lower in user.lower()
                    or (q_lower.isdigit() and int(q_lower) in (pid, ppid))
                )
                if not matched:
                    continue

            results.append(
                ProcessInfo(
                    pid=pid,
                    ppid=ppid,
                    cpu_percent=cpu_p,
                    memory_percent=mem_p,
                    user=user,
                    command=comm,
                    args=args_str,
                )
            )

            if len(results) >= limit:
                break

        return results

    def _list_processes_proc(
        self,
        filter_query: str,
        sort_by: str,
        limit: int,
    ) -> list[ProcessInfo]:
        """Fallback lendo o pseudofilesystem /proc diretamente."""
        proc_dir = "/proc"
        if not os.path.exists(proc_dir):
            return []

        q_lower = filter_query.strip().lower()
        items: list[ProcessInfo] = []

        for entry in os.listdir(proc_dir):
            if not entry.isdigit():
                continue
            pid = int(entry)
            p_path = os.path.join(proc_dir, entry)

            comm = ""
            try:
                with open(os.path.join(p_path, "comm"), "r", errors="ignore") as f:
                    comm = f.read().strip()
            except Exception:
                comm = ""

            args_str = ""
            try:
                with open(os.path.join(p_path, "cmdline"), "r", errors="ignore") as f:
                    args_str = f.read().replace("\x00", " ").strip()
            except Exception:
                args_str = comm

            if not args_str:
                args_str = comm

            if q_lower:
                matched = (
                    q_lower in comm.lower()
                    or q_lower in args_str.lower()
                    or (q_lower.isdigit() and int(q_lower) == pid)
                )
                if not matched:
                    continue

            items.append(
                ProcessInfo(
                    pid=pid,
                    ppid=1,
                    cpu_percent=0.0,
                    memory_percent=0.0,
                    user="user",
                    command=comm or f"pid_{pid}",
                    args=args_str or comm,
                )
            )

        if sort_by == "pid":
            items.sort(key=lambda p: p.pid)
        else:
            items.sort(key=lambda p: p.pid, reverse=True)

        return items[:limit]

    def format_table(self, processes: list[ProcessInfo]) -> str:
        """Formata a lista de processos em formato tabular claro e conciso."""
        if not processes:
            return "Nenhum processo em execução encontrado para o critério especificado."

        header = f"{'PID':>7}  {'%CPU':>5}  {'%MEM':>5}  {'USER':<10}  {'COMMAND':<18}  {'ARGS'}"
        sep = f"{'-'*7}  {'-'*5}  {'-'*5}  {'-'*10}  {'-'*18}  {'-'*25}"
        rows = [header, sep]

        for p in processes:
            args_truncated = p.args if len(p.args) <= 80 else p.args[:77] + "..."
            comm_truncated = p.command if len(p.command) <= 18 else p.command[:15] + "..."
            row = (
                f"{p.pid:>7}  {p.cpu_percent:>5.1f}  {p.memory_percent:>5.1f}  "
                f"{p.user[:10]:<10}  {comm_truncated:<18}  {args_truncated}"
            )
            rows.append(row)

        return "\n".join(rows)
