# Decisão de design: despachante thread-safe com isolamento estrito para GTK4/Libadwaita.
# Permite que threads de streaming (Hyprland socket, PipeWire, RNN ONNX)
# enviem atualizações para a interface sem violar a restrição de thread única do Wayland.
# Se GLib não estiver disponível (testes headless), faz fallback gracioso para fila síncrona.

"""Ponte thread-safe de despacho de eventos para a interface GTK4."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

try:
    from gi.repository import GLib  # type: ignore

    _HAS_GLIB = True
except (ImportError, ValueError):
    _HAS_GLIB = False


class UIThreadBridge:
    """Despachante central para execução segura de chamadas na thread principal de interface gráfica."""

    _main_thread_id: int | None = None

    @classmethod
    def mark_main_thread(cls) -> None:
        """Marca a thread atual como a thread proprietária da UI do GTK."""
        cls._main_thread_id = threading.get_ident()

    @classmethod
    def is_ui_thread(cls) -> bool:
        """Retorna True se o código estiver rodando na thread principal da UI."""
        if cls._main_thread_id is None:
            return threading.current_thread() is threading.main_thread()
        return threading.get_ident() == cls._main_thread_id

    @classmethod
    def post_to_ui(cls, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Enfileira uma função para ser executada de forma assíncrona na thread da UI.
        
        Não bloqueia a thread chamadora. Exceções na callback são capturadas e logadas.
        """
        def _wrapper() -> bool:
            try:
                callback(*args, **kwargs)
            except Exception as exc:
                logger.error("Exceção na callback de UI enfileirada: %s", exc, exc_info=True)
            return False  # False garante que GLib.idle_add não repetirá a execução

        if _HAS_GLIB:
            GLib.idle_add(_wrapper)
        else:
            # Fallback para ambientes de teste sem GTK/GLib
            _wrapper()

    @classmethod
    def call_on_ui_sync(
        cls,
        callback: Callable[..., T],
        *args: Any,
        timeout: float = 5.0,
        **kwargs: Any,
    ) -> T:
        """Executa a função na thread da UI e bloqueia a thread trabalhadora até obter o resultado.
        
        Útil para consultar propriedades geométricas ou estados de widgets antes de prosseguir.
        """
        # Se já estiver na thread da UI, executa diretamente para evitar deadlock
        if cls.is_ui_thread():
            return callback(*args, **kwargs)

        future: Future[T] = Future()

        def _wrapper() -> bool:
            try:
                result = callback(*args, **kwargs)
                future.set_result(result)
            except Exception as exc:
                future.set_exception(exc)
            return False

        if _HAS_GLIB:
            GLib.idle_add(_wrapper)
        else:
            _wrapper()

        return future.result(timeout=timeout)

    @classmethod
    def drain_events(cls, max_iterations: int = 100) -> int:
        """Drena e processa eventos pendentes no contexto padrão do GLib (essencial para testes)."""
        count = 0
        if _HAS_GLIB:
            ctx = GLib.MainContext.default()
            while ctx.pending() and count < max_iterations:
                ctx.iteration(False)
                count += 1
        return count


# Funções de conveniência no nível do módulo
post_to_ui = UIThreadBridge.post_to_ui
call_on_ui_sync = UIThreadBridge.call_on_ui_sync
drain_ui_events = UIThreadBridge.drain_events
