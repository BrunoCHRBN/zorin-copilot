# Decisão de design: Gerenciador de tópicos e sessões de chat sob demanda — consultas permanecem
# isoladas e rápidas por padrão, com opção do usuário FIXAR o tópico (pin) para preservar o
# histórico conversacional e passar contexto em perguntas subsequentes.

"""Gerenciador de tópicos e sessões contextuais para o Zorin Copilot."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ChatTurn:
    prompt: str
    answer: str
    timestamp: float = field(default_factory=time.time)


class TopicSession:
    """Controla o ciclo de vida do tópico ativo e histórico conversacional."""

    def __init__(self, max_history_turns: int = 8):
        self.is_pinned: bool = False
        self.turns: list[ChatTurn] = []
        self.max_history_turns = max_history_turns
        self._last_unpinned_turn: ChatTurn | None = None

    def pin(self) -> None:
        """Fixa o tópico atual. Se houver uma última resposta não fixada, incorpora ao histórico."""
        self.is_pinned = True
        if self._last_unpinned_turn and self._last_unpinned_turn not in self.turns:
            self.turns.append(self._last_unpinned_turn)

    def unpin(self) -> None:
        """Desafixa o tópico e limpa o histórico de contexto."""
        self.is_pinned = False
        self.turns.clear()
        self._last_unpinned_turn = None

    def toggle_pin(self) -> bool:
        """Alterna o estado de fixação. Retorna o novo estado (True = fixado)."""
        if self.is_pinned:
            self.unpin()
            return False
        else:
            self.pin()
            return True

    def record_turn(self, prompt: str, answer: str) -> None:
        """Registra uma interação usuário/assistente."""
        turn = ChatTurn(prompt=prompt.strip(), answer=answer.strip())
        if self.is_pinned:
            self.turns.append(turn)
            if len(self.turns) > self.max_history_turns:
                self.turns = self.turns[-self.max_history_turns:]
        else:
            # Guarda em memória temporária caso o usuário decida fixar logo em seguida
            self._last_unpinned_turn = turn

    def get_history_for_llm(self) -> list[dict[str, str]]:
        """Retorna o histórico formatado para envio aos provedores de IA (Gemini, Ollama, OpenAI)."""
        if not self.is_pinned or not self.turns:
            return []

        history: list[dict[str, str]] = []
        for t in self.turns:
            history.append({"role": "user", "content": t.prompt})
            history.append({"role": "assistant", "content": t.answer})
        return history

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def message_count(self) -> int:
        return len(self.turns) * 2
