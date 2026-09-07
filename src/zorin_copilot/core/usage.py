"""Contagem de tokens e telemetria de uso por provedor.

Módulo puro (sem GTK), para poder ser testado sem display. Cada provedor
preenche um `TokenUsage` a partir do campo de `usage` que sua API devolve e o
acumula num `TokenUsageTracker` por sessão.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TokenUsage:
    """Consumo de uma única resposta de modelo."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )


def format_tokens(n: int) -> str:
    """Formata um total de tokens em pt-BR: ``842``, ``1,2 mil``, ``2,3 mi``."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f} mil".replace(".", ",")
    return f"{n / 1_000_000:.1f} mi".replace(".", ",")


@dataclass
class TokenUsageTracker:
    """Acumula o consumo de tokens de toda a sessão (uma janela/um app)."""

    session: TokenUsage = field(default_factory=TokenUsage)
    requests: int = 0
    last: TokenUsage | None = None
    last_model: str = ""

    def record(self, usage: TokenUsage, *, provider: str = "", model: str = "") -> None:
        if usage.total_tokens == 0:
            # Respostas vazias (ex.: erro já tratado em outro lugar) não contam.
            return
        self.session += usage
        self.last = usage
        self.last_model = model
        self.requests += 1

    def reset(self) -> None:
        self.session = TokenUsage()
        self.requests = 0
        self.last = None
        self.last_model = ""


# ---------------------------------------------------------------------------
# Parsers por provedor — cada API devolve o uso num campo diferente.
# ---------------------------------------------------------------------------
def usage_from_gemini(data: dict[str, Any]) -> TokenUsage | None:
    meta = data.get("usageMetadata") or {}
    prompt = int(meta.get("promptTokenCount") or 0)
    completion = int(meta.get("candidatesTokenCount") or 0)
    if not prompt and not completion:
        return None
    return TokenUsage(prompt_tokens=prompt, completion_tokens=completion)


def usage_from_openai(data: dict[str, Any]) -> TokenUsage | None:
    usage = data.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    if not prompt and not completion:
        return None
    return TokenUsage(prompt_tokens=prompt, completion_tokens=completion)


def usage_from_ollama(data: dict[str, Any]) -> TokenUsage | None:
    # Ollama devolve o uso no próprio objeto de resposta, não aninhado.
    prompt = int(data.get("prompt_eval_count") or 0)
    completion = int(data.get("eval_count") or 0)
    if not prompt and not completion:
        return None
    return TokenUsage(prompt_tokens=prompt, completion_tokens=completion)
