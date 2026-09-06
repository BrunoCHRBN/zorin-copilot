# Decisão de design: monitoramento contínuo e em tempo real de documentos locais via inotify/Gio.FileMonitor.
# Detecta criação, edição e exclusão de arquivos nas pastas monitoradas (~/Documentos, ~/Downloads)
# com debounce inteligente para esperar o fim de downloads ou salvamentos antes de indexar.

"""Monitor contínuo de arquivos locais para o Zorin Copilot."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable, Sequence

from .rag import SUPPORTED_EXTENSIONS, LocalDocumentRAG

logger = logging.getLogger(__name__)

try:
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib
    HAS_GIO = True
except Exception:
    HAS_GIO = False


class DocumentFileWatcher:
    """Monitora alterações em diretórios locais em segundo plano e sincroniza o RAG."""

    def __init__(
        self,
        rag: LocalDocumentRAG | None = None,
        directories: Sequence[Path | str] | None = None,
        debounce_seconds: float = 1.5,
        on_event_callback: Callable[[str, str], None] | None = None,
    ):
        self.rag = rag or LocalDocumentRAG()
        self.debounce_seconds = debounce_seconds
        self.on_event_callback = on_event_callback
        self._is_running = False
        self._monitors: list[Any] = []
        self._pending_updates: dict[str, float] = {}
        self._lock = threading.Lock()
        self._worker_thread: threading.Thread | None = None

        if directories is not None:
            self.directories = [Path(d).expanduser().resolve() for d in directories]
        else:
            self.directories = [Path(d).resolve() for d in self.rag.watched_dirs]

    def start(self) -> bool:
        """Inicia os monitores de diretório e a thread de processamento de debounce."""
        if self._is_running:
            return True

        if not HAS_GIO:
            logger.warning("PyGObject / Gio indisponível. FileWatcher não iniciado.")
            return False

        self._is_running = True
        for d in self.directories:
            if not d.exists() or not d.is_dir():
                continue
            try:
                gfile = Gio.File.new_for_path(str(d))
                monitor = gfile.monitor_directory(Gio.FileMonitorFlags.WATCH_MOUNTS, None)
                monitor.connect("changed", self._on_file_changed)
                self._monitors.append(monitor)
                logger.info(f"Monitorando diretório para RAG em tempo real: {d}")
            except Exception as exc:
                logger.warning(f"Falha ao registrar monitor no diretório {d}: {exc}")

        # Thread de debounce e despacho
        self._worker_thread = threading.Thread(target=self._debounce_worker, daemon=True)
        self._worker_thread.start()
        return True

    def stop(self) -> None:
        """Encerra todos os monitores e a thread de sincronização."""
        self._is_running = False
        for mon in self._monitors:
            try:
                mon.cancel()
            except Exception:
                pass
        self._monitors.clear()

    def _on_file_changed(self, monitor: Any, file: Any, other_file: Any, event_type: Any) -> None:
        """Callback acionado pelo barramento Gio quando um arquivo sofre mutação."""
        path_str = file.get_path()
        if not path_str:
            return

        fpath = Path(path_str)
        ext = fpath.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return

        ev_name = getattr(event_type, "value_nick", str(event_type))

        # Eventos de exclusão
        if "deleted" in ev_name.lower():
            with self._lock:
                self._pending_updates.pop(path_str, None)
            logger.info(f"Arquivo removido detectado: {fpath.name}")
            self.rag.delete_document(fpath)
            if self.on_event_callback:
                self.on_event_callback("delete", path_str)
            return

        # Eventos de criação ou modificação: agenda para processamento com debounce
        with self._lock:
            self._pending_updates[path_str] = time.time() + self.debounce_seconds

    def _debounce_worker(self) -> None:
        """Verifica periodicamente arquivos cujos eventos de gravação cessaram e os indexa."""
        while self._is_running:
            now = time.time()
            ready_paths: list[str] = []

            with self._lock:
                for path_str, fire_time in list(self._pending_updates.items()):
                    if now >= fire_time:
                        ready_paths.append(path_str)
                        del self._pending_updates[path_str]

            for path_str in ready_paths:
                p = Path(path_str)
                if p.exists() and p.is_file():
                    try:
                        ok = self.rag.index_file(p)
                        if ok:
                            logger.info(f"Sincronização automática de documento concluída: {p.name}")
                            if self.on_event_callback:
                                self.on_event_callback("index", path_str)
                    except Exception as exc:
                        logger.debug(f"Erro na sincronização de {p.name}: {exc}")

            time.sleep(0.5)
