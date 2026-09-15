# Decisão de design: o roteamento local→nuvem é DETERMINÍSTICO (léxico + conectores),
# não "pergunte ao modelo qual caminho tomar". Motivo: classificar o objetivo com um LLM
# custaria uma chamada inteira — justamente o que queremos evitar quando a tarefa é
# simples e o Qwen local resolve. O modelo só é consultado para decidir O PASSO, nunca
# para decidir QUEM decide.
#
# A escalada é por falha, não por palpite: começa no local; se o local não responder,
# tenta a nuvem; se a nuvem também falhar, o loop para com `error` (nunca inventa ação).

"""Roteamento de planejamento do modo agente entre modelo local e nuvem."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Sequence

from .agent import AgentDecision, ToolCall
from .agent_tools import FINISH_TOOLS

logger = logging.getLogger(__name__)


class RouteMode(str, Enum):
    """De onde pode vir o raciocínio."""

    AUTO = "auto"
    LOCAL = "local"
    CLOUD = "cloud"


# Verbos que o modelo local resolve bem: mapeiam 1:1 numa ferramenta.
SIMPLE_VERBS = (
    "abrir",
    "abre",
    "fechar",
    "fecha",
    "clicar",
    "clica",
    "digitar",
    "digite",
    "escrever",
    "escreva",
    "listar",
    "lista",
    "ler",
    "leia",
    "capturar",
    "captura",
    "print",
    "rolar",
    "rola",
    "minimizar",
    "maximizar",
    "copiar",
    "copia",
    "colar",
    "cola",
)

# Conectores que delatam cadeia de passos — é onde o 7B local costuma se perder.
COMPOUND_MARKERS = (
    " e depois ",
    " e então ",
    " em seguida ",
    " depois ",
    " após ",
    " antes de ",
    " e por fim ",
    " passo a passo ",
)

# Pedidos que exigem síntese/leitura longa: nuvem.
SYNTHESIS_MARKERS = (
    "pesquis",
    "resum",
    "analis",
    "compar",
    "expliqu",
    "redig",
    "relatório",
    "relatorio",
    "estud",
    "sintetiz",
)

#: Acima deste número de verbos o objetivo é tratado como composto.
MAX_SIMPLE_VERBS = 2


# --------------------------------------------------------------------------- #
# Classificação (pura, testável)
# --------------------------------------------------------------------------- #


def classify_objective(objective: str) -> tuple[str, str]:
    """Classifica `objective` como "simple" ou "complex" e explica por quê.

    Determinístico de propósito: mesma entrada, mesma saída, zero custo.
    """
    normalized = re.sub(r"\s+", " ", (objective or "").strip().lower())
    text = f" {normalized} "

    for marker in SYNTHESIS_MARKERS:
        if marker in text:
            return "complex", f"pedido de síntese/pesquisa ('{marker.strip()}')"

    compound = [marker.strip() for marker in COMPOUND_MARKERS if marker in text]
    if compound:
        return "complex", f"cadeia de passos (conector '{compound[0]}')"

    verbs = [verb for verb in SIMPLE_VERBS if re.search(rf"\b{re.escape(verb)}\b", text)]
    if len(verbs) > MAX_SIMPLE_VERBS:
        return "complex", f"muitos verbos de ação ({len(verbs)})"
    if len(verbs) == 0:
        # Sem verbo conhecido: não é "simples"; é desconhecido. Nuvem erra menos.
        return "complex", "nenhum verbo de ação reconhecido"

    return "simple", f"verbo(s) direto(s): {', '.join(verbs[:3])}"


# --------------------------------------------------------------------------- #
# Planejador sobre LLM (provider-agnóstico)
# --------------------------------------------------------------------------- #

_SYSTEM_INSTRUCTION = (
    "Você é o modo agente do Zorin Copilot. Controla o desktop do usuário executando "
    "UMA ferramenta por vez, em português do Brasil.\n"
    "Regras:\n"
    "1. Responda SOMENTE um objeto JSON, sem markdown, sem explicação fora do JSON.\n"
    "2. Para agir: {\"tool\": \"nome\", \"args\": {...}, \"rationale\": \"por quê\"}.\n"
    "3. Para encerrar: {\"final_answer\": \"resposta final para o usuário\"} ou "
    "{\"tool\": \"done\", \"args\": {\"answer\": \"...\"}}.\n"
    "4. Use o `rationale` curto (uma frase).\n"
    "5. Prefira `find_element` + `click_element` a `mouse_click` por coordenada.\n"
    "6. Se uma ação falhou, mude de estratégia — não repita os mesmos argumentos.\n"
    "7. Se faltar informação que só o usuário tem, encerre com `final_answer` perguntando."
)


class LLMPlanner:
    """Planejador que usa qualquer `BaseLLMProvider` (Ollama, Gemini, OpenAI...)."""

    def __init__(self, provider: Any, name: str = "llm") -> None:
        self.provider = provider
        self.name = name

    def decide(
        self,
        objective: str,
        tools: list[dict[str, Any]],
        history: list[dict[str, Any]],
    ) -> AgentDecision:
        prompt = build_planner_prompt(objective, tools, history)
        try:
            text, _actions = self.provider.chat(prompt)
        except Exception as exc:
            return AgentDecision(error=f"Provedor '{self.name}' falhou: {exc}")

        raw = (text or "").strip()
        if not raw:
            return AgentDecision(error=f"Provedor '{self.name}' respondeu vazio.")

        parsed = extract_json_object(raw)
        if parsed is None:
            return AgentDecision(
                error=f"Resposta do provedor não era JSON utilizável: {raw[:160]}"
            )
        return decision_from_json(parsed)


def build_planner_prompt(
    objective: str,
    tools: list[dict[str, Any]],
    history: Sequence[dict[str, Any]] | None,
) -> str:
    """Monta o prompt de um passo. Separada para poder ser testada isoladamente."""
    lines = [
        _SYSTEM_INSTRUCTION,
        "",
        f"OBJETIVO: {objective}",
        "",
        "FERRAMENTAS DISPONÍVEIS:",
    ]
    for tool in tools:
        name = tool.get("name", "")
        args = _describe_args(tool.get("parameters") or {})
        lines.append(f"- {name}({args}): {tool.get('description', '')}")

    if history:
        lines.append("")
        lines.append("HISTÓRICO (ações já executadas e seus resultados):")
        for entry in history:
            # +1 porque o índice interno é 0-based; o modelo raciocina melhor
            # com passos começando em 1.
            index = _step_number(entry.get("index", 0))
            tool = entry.get("tool", "?")
            args = json.dumps(entry.get("args") or {}, ensure_ascii=False, default=str)
            ok = "OK" if entry.get("ok") else ("FALHOU" if entry.get("ok") is False else "PENDENTE")
            observation = json.dumps(
                entry.get("observation") or entry.get("error") or "", ensure_ascii=False, default=str
            )
            lines.append(f"{index}. {tool}({args}) -> {ok}: {_clip(observation, 300)}")
    else:
        lines.append("")
        lines.append("HISTÓRICO: nenhuma ação executada ainda.")

    lines.append("")
    lines.append("PRÓXIMO PASSO (JSON apenas):")
    return "\n".join(lines)


def _step_number(index: Any) -> int:
    try:
        return int(index) + 1
    except (TypeError, ValueError):
        return 0


def _describe_args(parameters: dict[str, Any]) -> str:
    props = parameters.get("properties") or {}
    required = set(parameters.get("required") or [])
    return ", ".join(f"{name}*" if name in required else name for name in props)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Extrai o primeiro objeto JSON completo de um texto possivelmente sujo.

    Modelos locais adoram cercar a resposta com ```json ou com frase antes/depois.
    O scanner respeita strings e escapes para não cortar no primeiro `}` de um valor.
    """
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start = cleaned.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(cleaned[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def decision_from_json(payload: dict[str, Any]) -> AgentDecision:
    """Converte o JSON do modelo em `AgentDecision`, tolerando variações de chave."""
    answer = str(payload.get("final_answer") or payload.get("answer") or "").strip()
    name = str(payload.get("tool") or payload.get("name") or payload.get("action") or "").strip()
    args = payload.get("args")
    if not isinstance(args, dict):
        args = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
        if not args and isinstance(payload.get("parameters"), dict):
            args = payload["parameters"]
    rationale = str(payload.get("rationale") or payload.get("reason") or payload.get("thought") or "")

    if name:
        return AgentDecision(
            tool_call=ToolCall(name=name, args=dict(args or {}), rationale=rationale),
            final_answer=answer,
        )
    if answer:
        return AgentDecision(final_answer=answer)
    return AgentDecision(error=f"JSON sem ferramenta nem resposta final: {payload}")


# --------------------------------------------------------------------------- #
# Planejador com fallback
# --------------------------------------------------------------------------- #


class FallbackPlanner:
    """Tenta uma lista de planejadores em ordem; o primeiro que responder, ganha."""

    def __init__(self, planners: Iterable[Any]) -> None:
        self.planners = [p for p in planners if p is not None]
        self.name = "→".join(getattr(p, "name", "?") for p in self.planners) or "none"
        self.used: str = ""

    def decide(
        self,
        objective: str,
        tools: list[dict[str, Any]],
        history: list[dict[str, Any]],
    ) -> AgentDecision:
        errors: list[str] = []
        for planner in self.planners:
            decision = planner.decide(objective, tools, history)
            if not decision.error:
                self.used = getattr(planner, "name", "?")
                logger.debug("Planejador '%s' respondeu.", self.used)
                return decision
            errors.append(decision.error)
            logger.debug("Planejador '%s' falhou: %s", getattr(planner, "name", "?"), decision.error)
        return AgentDecision(error=" | ".join(errors) or "Nenhum planejador disponível.")


# --------------------------------------------------------------------------- #
# Roteador
# --------------------------------------------------------------------------- #


@dataclass
class RouteDecision:
    planner: Any
    mode: str
    reason: str
    complexity: str = ""

    @property
    def has_planner(self) -> bool:
        return self.planner is not None


class AgentRouter:
    """Escolhe quem planeja cada objetivo.

    `config` é opcional para permitir uso com planejadores já prontos (testes, UI).
    """

    def __init__(
        self,
        config: Any | None = None,
        *,
        local_planner: Any | None = None,
        cloud_planner: Any | None = None,
    ) -> None:
        self.config = config
        self._local = local_planner
        self._cloud = cloud_planner

    # -- construção sob demanda -------------------------------------------- #

    def local_planner(self) -> Any | None:
        if self._local is not None:
            return self._local
        if self.config is None:
            return None
        try:
            from .providers import OllamaProvider

            provider = OllamaProvider(
                getattr(self.config, "ollama_url", "http://127.0.0.1:11434"),
                getattr(self.config, "ollama_model", "qwen2.5:7b"),
                getattr(self.config, "ollama_vision_model", "minicpm-v"),
            )
        except Exception as exc:
            logger.debug("Provedor local indisponível: %s", exc)
            return None
        self._local = LLMPlanner(provider, name="local")
        return self._local

    def cloud_planner(self) -> Any | None:
        if self._cloud is not None:
            return self._cloud
        if self.config is None:
            return None
        config = self.config
        provider = None
        try:
            from .providers import GeminiProvider, OpenAICompatProvider

            if getattr(config, "gemini_api_key", "").strip():
                provider = GeminiProvider(config.gemini_api_key, getattr(config, "gemini_model", "gemini-flash-latest"))
            elif getattr(config, "openai_api_key", "").strip():
                provider = OpenAICompatProvider(
                    getattr(config, "openai_url", "https://api.openai.com/v1"),
                    config.openai_api_key,
                    getattr(config, "openai_model", "gpt-4o-mini"),
                )
        except Exception as exc:
            logger.debug("Provedor de nuvem indisponível: %s", exc)
            return None
        if provider is None:
            return None
        self._cloud = LLMPlanner(provider, name="cloud")
        return self._cloud

    # -- seleção ------------------------------------------------------------- #

    def route(self, objective: str, mode: RouteMode | str = RouteMode.AUTO) -> RouteDecision:
        """Devolve o planejador escolhido (ou `None` em `planner`, com o motivo)."""
        mode = RouteMode(mode) if not isinstance(mode, RouteMode) else mode
        complexity, reason = classify_objective(objective)

        if mode is RouteMode.LOCAL:
            planner = self.local_planner()
            return RouteDecision(
                planner, "local", "modo --local-only" if planner else "modelo local indisponível", complexity
            )

        if mode is RouteMode.CLOUD:
            planner = self.cloud_planner()
            return RouteDecision(
                planner, "cloud", "modo --cloud" if planner else "nenhuma credencial de nuvem configurada", complexity
            )

        local = self.local_planner()
        cloud = self.cloud_planner()

        if complexity == "simple":
            if local:
                # Local primeiro, nuvem como rede de segurança: se o Qwen não
                # responder, uma tarefa simples não deve simplesmente morrer.
                return RouteDecision(
                    FallbackPlanner([local, cloud]) if cloud else local,
                    "local",
                    reason,
                    complexity,
                )
            return RouteDecision(cloud, "cloud", f"{reason}; modelo local indisponível", complexity)

        if cloud:
            return RouteDecision(
                FallbackPlanner([cloud, local]) if local else cloud,
                "cloud",
                reason,
                complexity,
            )
        return RouteDecision(local, "local", f"{reason}; nuvem não configurada", complexity)
