# Decisão de design: só entram na pilha as ações cujo efeito o app consegue
# descrever e reverter sozinho — escrever/sobrescrever arquivo e organizar pasta.
# Clique, digitação, abrir app e abrir URL mexem em estado de terceiros: prometer
# desfazer isso seria pior que não ter undo nenhum, porque a promessa seria falsa.
#
# A pilha guarda *como* reverter (`revert`), decidido por quem executou — é o
# executor que conhece o estado anterior, não a UI.

"""Pilha de desfazer das ações reversíveis do executor."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

__all__ = ["UndoEntry", "UndoStack", "UNDO_HISTORY_LIMIT"]

#: Teto do backlog (item 10): rollback das últimas 5 ações.
UNDO_HISTORY_LIMIT = 5

#: Assinatura de quem reverte: (sucesso, mensagem para o usuário).
RevertFn = Callable[[], "tuple[bool, str]"]

#: Aviso à UI depois de desfazer, para repintar a linha da ação sem reconstruir
#: o fluxo inteiro (o que faria as ações de turnos antigos desaparecerem).
UndoneHook = Callable[[bool, str], None]


@dataclass
class UndoEntry:
    """Uma ação executada que pode ser revertida."""

    id: str
    label: str
    revert: RevertFn
    action_type: str = ""
    timestamp: float = field(default_factory=time.time)
    #: Chave no registro de resultados, para a linha da ação refletir o desfazer.
    ui_key: Optional[str] = None
    on_undone: Optional[UndoneHook] = None


class UndoStack:
    """Histórico limitado de ações reversíveis, do mais recente ao mais antigo."""

    def __init__(self, max_size: int = UNDO_HISTORY_LIMIT):
        self.max_size = max(1, int(max_size))
        # `deque(maxlen=)` descarta o mais antigo sozinho quando estoura o teto.
        self._entries: deque[UndoEntry] = deque(maxlen=self.max_size)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> list[UndoEntry]:
        """Do mais antigo para o mais recente."""
        return list(self._entries)

    def push(
        self,
        label: str,
        revert: RevertFn,
        *,
        action_type: str = "",
        ui_key: Optional[str] = None,
        on_undone: Optional[UndoneHook] = None,
    ) -> UndoEntry:
        """Registra uma ação reversível. Devolve a entrada criada."""
        entry = UndoEntry(
            id=f"{time.time():.6f}",
            label=label,
            revert=revert,
            action_type=action_type,
            ui_key=ui_key,
            on_undone=on_undone,
        )
        self._entries.append(entry)
        return entry

    def peek(self) -> Optional[UndoEntry]:
        """Ação mais recente, sem remover."""
        return self._entries[-1] if self._entries else None

    def undo(self) -> "tuple[bool, str, Optional[UndoEntry]]":
        """Reverte a ação mais recente.

        A entrada sai da pilha **antes** de reverter: um desfazer que falha
        (arquivo movido pelo usuário, por exemplo) não deve trancar os quatro
        anteriores. A mensagem devolvida diz o que aconteceu.
        """
        entry = self.peek()
        if entry is None:
            return False, "Nada para desfazer.", None

        self._entries.pop()
        try:
            ok, message = entry.revert()
        except Exception as exc:  # reverter não pode derrubar a janela
            ok, message = False, f"Falha ao desfazer: {exc}"

        if entry.on_undone is not None:
            try:
                entry.on_undone(ok, message)
            except Exception:  # aviso à UI é best-effort
                pass

        return ok, message, entry

    def clear(self) -> None:
        self._entries.clear()
