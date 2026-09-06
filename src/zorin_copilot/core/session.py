# Decisão de design: Gerenciador de tópicos e sessões de chat sob demanda — consultas permanecem
# isoladas e rápidas por padrão ("chat de agora"), com opção de fixar (📌) e persistir o tópico
# em histórico acessível para continuar raciocínios prévios a qualquer momento.

"""Gerenciador de tópicos e sessões contextuais para o Zorin Copilot."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ChatTurn:
    prompt: str
    answer: str
    timestamp: float = field(default_factory=time.time)
    # Identificador estável: permite associar resultados de execução a um turno
    # específico mesmo depois de o fluxo de chat ser reconstruído do zero.
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict:
        return {
            "prompt": self.prompt,
            "answer": self.answer,
            "timestamp": self.timestamp,
            "id": self.id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ChatTurn:
        return cls(
            prompt=data.get("prompt", ""),
            answer=data.get("answer", ""),
            timestamp=float(data.get("timestamp", time.time())),
            # Turnos gravados antes deste campo existir recebem um id novo na
            # leitura — aceitável, porque o registro de execução é em memória.
            id=data.get("id") or uuid.uuid4().hex,
        )


class TopicSession:
    """Controla o ciclo de vida do tópico ativo e histórico conversacional (Estilo Gemini)."""

    def __init__(
        self,
        session_id: str | None = None,
        title: str = "",
        max_history_turns: int = 10,
        auto_persist: bool = False,
    ):
        self.id: str = session_id or uuid.uuid4().hex[:12]
        self.title: str = title
        self.auto_persist: bool = auto_persist
        self.is_pinned: bool = auto_persist
        self.turns: list[ChatTurn] = []
        self.max_history_turns = max_history_turns
        self._last_unpinned_turn: ChatTurn | None = None
        self.created_at: str = datetime.now().isoformat()
        self.updated_at: str = self.created_at

    def pin(self, title: str | None = None) -> None:
        """Fixa o tópico atual. Se houver uma última resposta não fixada, incorpora ao histórico."""
        self.is_pinned = True
        if self._last_unpinned_turn and self._last_unpinned_turn not in self.turns:
            self.turns.append(self._last_unpinned_turn)
            if not self.title:
                self.title = self._derive_title(self._last_unpinned_turn.prompt)
        if title:
            self.title = title
        elif not self.title and self.turns:
            self.title = self._derive_title(self.turns[0].prompt)
        self.updated_at = datetime.now().isoformat()

    def unpin(self) -> None:
        """Desafixa o tópico e limpa o histórico de contexto, voltando a ser um chat de agora."""
        self.reset_new()

    def reset_new(self) -> None:
        """Gera um novo identificador limpo e reseta o estado conversacional para uma nova demanda."""
        self.id = uuid.uuid4().hex[:12]
        self.title = ""
        self.is_pinned = self.auto_persist
        self.turns.clear()
        self._last_unpinned_turn = None
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at

    def toggle_pin(self) -> bool:
        """Alterna o estado de fixação. Retorna o novo estado (True = fixado)."""
        if self.is_pinned:
            self.unpin()
            return False
        else:
            self.pin()
            return True

    def _derive_title(self, prompt: str) -> str:
        """Deriva um título curto, semântico e amigável para a demanda (estilo Gemini)."""
        cleaned = " ".join(prompt.strip().split())
        lower = cleaned.lower()
        prefixes_to_strip = [
            "como posso ", "como eu posso ", "como fazer para ", "como faço para ",
            "por favor ", "por gentileza ", "me ajude a ", "me ajude com ",
            "gostaria de ", "eu gostaria de ", "você pode ", "voce pode ",
            "pesquise sobre ", "pesquise por ", "pesquise ", "pesquisar ",
            "abra o ", "abrir o ", "abre o ", "inicie o ", "iniciar o ",
            "execute o ", "executar o ", "rode o ", "rodar o ",
            "organize a ", "organizar a ", "organize o ", "organizar o ", "organizar ", "organize ",
            "analisar ", "analise ", "resumir ", "resuma ",
        ]
        while True:
            stripped_any = False
            for prefix in prefixes_to_strip:
                if lower.startswith(prefix):
                    candidate = cleaned[len(prefix):].strip()
                    if candidate:
                        cleaned = candidate
                        lower = cleaned.lower()
                        stripped_any = True
                        break
            if not stripped_any:
                break

        if not cleaned:
            return "Nova Demanda"

        cleaned = cleaned[0].upper() + cleaned[1:] if len(cleaned) > 1 else cleaned.upper()
        if len(cleaned) <= 50:
            return cleaned
        return cleaned[:47].rstrip() + "..."

    def record_turn(self, prompt: str, answer: str) -> ChatTurn:
        """Registra uma interação usuário/assistente na demanda ativa.

        Retorna o turno criado para que a interface possa renderizá-lo imediatamente.
        """
        clean_p = prompt.strip()
        clean_a = answer.strip()
        turn = ChatTurn(prompt=clean_p, answer=clean_a)

        if not self.title and clean_p:
            self.title = self._derive_title(clean_p)

        self.updated_at = datetime.now().isoformat()

        if self.is_pinned or self.auto_persist:
            self.is_pinned = True
            self.turns.append(turn)
            if len(self.turns) > self.max_history_turns:
                self.turns = self.turns[-self.max_history_turns:]
        else:
            # Guarda em memória temporária caso o usuário decida fixar logo em seguida
            self._last_unpinned_turn = turn

        return turn

    def get_history_for_llm(self) -> list[dict[str, str]]:
        """Retorna o histórico formatado para envio aos provedores de IA (Gemini, Ollama, OpenAI)."""
        if (not self.is_pinned and not self.auto_persist) or not self.turns:
            return []

        history: list[dict[str, str]] = []
        for t in self.turns:
            history.append({"role": "user", "content": t.prompt})
            history.append({"role": "assistant", "content": t.answer})
        return history

    def to_dict(self) -> dict:
        """Exporta os dados da sessão para persistência."""
        return {
            "id": self.id,
            "title": self.title,
            "is_pinned": self.is_pinned,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turn_count": len(self.turns),
            "turns": [t.to_dict() for t in self.turns],
        }

    def load_from_dict(self, data: dict) -> None:
        """Restaura o estado da sessão a partir de dados salvos."""
        self.id = data.get("id") or uuid.uuid4().hex[:12]
        self.title = data.get("title", "Tópico Sem Título")
        self.is_pinned = bool(data.get("is_pinned", True))
        self.created_at = data.get("created_at", datetime.now().isoformat())
        self.updated_at = data.get("updated_at", self.created_at)
        raw_turns = data.get("turns", [])
        self.turns = [ChatTurn.from_dict(t) for t in raw_turns]
        self._last_unpinned_turn = None

    @classmethod
    def from_dict(cls, data: dict, max_history_turns: int = 8) -> TopicSession:
        session = cls(
            session_id=data.get("id"),
            title=data.get("title", ""),
            max_history_turns=max_history_turns,
        )
        session.load_from_dict(data)
        return session

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def message_count(self) -> int:
        return len(self.turns) * 2
