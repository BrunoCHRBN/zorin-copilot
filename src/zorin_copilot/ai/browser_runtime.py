# Decisão de design: estende o modo agente JÁ EXISTENTE (ai/agent.py + ai/agent_tools.py)
# por COMPOSIÇÃO, sem reescrever o loop. O segredo de "não entrar em loop nem repetir erro"
# entra como: (1) ValidatingToolRegistry — valida o estado da tela ANTES de agir e verifica
# DEPOIS; (2) EpisodeMemory / AntiLoopGuard — guarda (estado, ação) e recusa repetir o que já
# falhou. Tudo devolve dicionário e nunca levanta, seguindo o contrato do ToolRegistry.call.
# O AgentLoop continua dono das paradas (max_steps, timeout, aborto, aprovação).

"""Runtime do agente guiado por visão: percepção fusionada + validação + memória anti-loop."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence

from .agent_tools import ToolRegistry
from .agent import AgentLoop, AgentPlanner
from .agent_router import build_planner_prompt, decision_from_json, extract_json_object
from .provider_failure import failure_reason, is_provider_failure
from ..core.perception import ScreenState, PerceptionChannel, hash_state

logger = logging.getLogger(__name__)

#: Instrução de sistema do planner consciente da tela: pede pré/pós-condições e
#: lembra o modelo de agir UMA coisa de cada vez, validando o que vê antes de tocar.
_VISION_SYSTEM_INSTRUCTION = (
    "Voce e o planejador de um assistente de desktop que VE a tela (captura + arvore "
    "de acessibilidade). Aja UM passo de cada vez. Para cada passo, retire um objeto JSON "
    "com: tool, args, rationale, e opcionalmente preconditions (o que deve ser verdadeiro "
    "ANTES de agir, ex.: 'o campo de busca esta visivel') e postconditions (o que deve ser "
    "verdadeiro DEPOIS, ex.: 'a lista de resultados apareceu'). Antes de clicar/digitar, "
    "confirme pelo estado da tela que o alvo existe e esta visivel. Se algo falhou no passo "
    "anterior, tente uma estrategia diferente (outro seletor, rolar a pagina, fechar banner "
    "de cookie) em vez de repetir a mesma acao. Nunca invente coordenadas ou resultados."
)


#: Palavras funcionais PT-BR que não ajudam a identificar o que uma condição quer.
_STOPWORDS = {
    "para", "com", "que", "esta", "está", "estao", "estão", "foi", "ser", "tem", "sao",
    "são", "uma", "um", "dos", "das", "nao", "não", "sim", "antes", "depois",
    "visivel", "visível", "campo", "lista", "tela", "site", "pagina", "página", "janela",
    "elemento", "alvo", "existe", "apareceu", "aparece", "resultado", "resultados",
    "produto", "produtos", "busca", "clicar", "digitar", "texto", "acao", "ação",
}


#: Mapeia frases-comando conhecidas a termos que DEVEM aparecer no dump da árvore.
#: Usado pela heurística barata quando não há VLM disponível. None = extrai por
#: substantivos (ver _screen_condition).
_CONDITION_KEYWORDS: dict[str, list[str]] = {
    "o campo de busca esta visivel": ["search", "busca", "pesquisar", "searchbox", "entry"],
    "a barra de pesquisa apareceu": ["search", "busca", "pesquisar", "searchbox"],
    "a lista de resultados apareceu": ["resultado", "result", "list", "lista", "item"],
    "a pagina carregou": ["content", "document", "frame", "page"],
    "o botao apareceu": ["button", "botao", "btn"],
}


def _extract_condition_keywords(condition: str) -> list[str]:
    """Substantivos da condição (sem stopwords) para a heurística da árvore."""
    cond = (condition or "").strip().lower()
    if cond in _CONDITION_KEYWORDS:
        return _CONDITION_KEYWORDS[cond]
    words = re.findall(r"[a-zà-ú]{4,}", cond)
    return [w for w in words if w not in _STOPWORDS]


# --------------------------------------------------------------------------- #
# Contrato de passo (plano como máquina de estados)
# --------------------------------------------------------------------------- #


@dataclass
class StepContract:
    """O que validar ANTES e DEPOIS de cada ação.

    `preconditions`/`postconditions` são frases em linguagem natural que o
    planejador preenche e o ValidatingToolRegistry confere contra o ScreenState
    (quando possível) ou repassa ao modelo para checagem.
    """

    intent: str = ""
    target: str = ""
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Memória de episódio (anti-loop)
# --------------------------------------------------------------------------- #


@dataclass
class _Outcome:
    ok: bool
    detail: str
    ts: float = field(default_factory=time.monotonic)


class EpisodeMemory:
    """Memória de episódio: (estado, ação) -> resultado.

    É o que impede "clicar no mesmo botão quebrado 5x". Bate com o contrato
    `memory.log_action` do AgentLoop (auditoria) e adiciona a consulta
    `would_repeat` usada na hora de agir.
    """

    def __init__(
        self,
        *,
        max_attempts_per_state_action: int = 1,
        sticky_notes: dict[str, str] | None = None,
    ) -> None:
        self.max_attempts = max(1, int(max_attempts_per_state_action))
        self._outcomes: dict[tuple[str, str], list[_Outcome]] = {}
        self.sticky: dict[str, str] = dict(sticky_notes or {})

    # -- contrato do AgentLoop (auditoria) ---------------------------------- #
    def log_action(
        self,
        objective: str,
        tool: str,
        args_summary: str,
        meta: dict[str, Any],
        ok: bool,
        detail: str,
    ) -> None:
        state_sig = str(meta.get("state_sig") or meta.get("agent_run_id") or "")
        self.note(state_sig, f"{tool}|{args_summary}", ok, detail or "")

    # -- API anti-loop ------------------------------------------------------ #
    def note(self, state_sig: str, action_sig: str, ok: bool, detail: str = "") -> None:
        self._outcomes.setdefault((state_sig, action_sig), []).append(
            _Outcome(ok=bool(ok), detail=detail[:200])
        )

    def attempts(self, state_sig: str, action_sig: str) -> int:
        return len(self._outcomes.get((state_sig, action_sig), ()))

    def would_repeat(self, state_sig: str, action_sig: str) -> bool:
        """True se esse (estado, ação) já foi tentado >= max e NUNCA deu certo."""
        hist = self._outcomes.get((state_sig, action_sig))
        if not hist:
            return False
        return len(hist) >= self.max_attempts and not any(o.ok for o in hist)

    # -- anotações 'sticky' entre execuções -------------------------------- #
    def learn(self, key: str, note: str) -> None:
        """Ex.: 'mercadolivre: aparece banner de cookie antes da home'."""
        self.sticky[key] = note

    def recall(self, key: str) -> str | None:
        return self.sticky.get(key)


class AntiLoopGuard:
    """Porta anti-loop usada pelo ValidatingToolRegistry.

    Decide se um (estado, ação) pode executar e registra o resultado. Por padrão
    delega numa EpisodeMemory, mas pode usar outra traseira que implemente a mesma
    API mínima (would_repeat / note / recall / learn).
    """

    def __init__(self, memory: EpisodeMemory | None = None, *, max_attempts: int = 1) -> None:
        self.memory = memory or EpisodeMemory(max_attempts_per_state_action=max_attempts)
        self.max_attempts = max(1, int(max_attempts))

    def would_repeat(self, state_sig: str, action_sig: str) -> bool:
        return self.memory.would_repeat(state_sig, action_sig)

    def note(self, state_sig: str, action_sig: str, ok: bool, detail: str = "") -> None:
        self.memory.note(state_sig, action_sig, ok, detail)

    def recall(self, key: str) -> str | None:
        return self.memory.recall(key)

    def learn(self, key: str, note: str) -> None:
        self.memory.learn(key, note)


# --------------------------------------------------------------------------- #
# Registry validador: assert pré-ação + verify pós-ação + anti-loop
# --------------------------------------------------------------------------- #


def _action_sig(name: str, args: dict[str, Any]) -> str:
    """Assinatura estável da ação para a memória (só campos relevantes)."""
    safe = {
        k: v
        for k, v in args.items()
        if k in {"uid", "target", "query", "text", "url", "app"}
    }
    return f"{name}|{json.dumps(safe, sort_keys=True, default=str)}"


def _find_uid(state: ScreenState, uid: str) -> Any:
    tree = state.ui_tree
    if tree is None or not hasattr(tree, "find"):
        return None
    return tree.find(lambda el: getattr(el, "uid", None) == uid)


def _is_visible(el: Any) -> bool:
    states = getattr(el, "states", ()) or ()
    banned = {"hidden", "defunct", "inactive"}
    return not any(s in banned for s in states)


class ValidatingToolRegistry:
    """Envolve um ToolRegistry real e valida o estado da tela.

    - assert pré-ação: o alvo existe/visível no ScreenState atual (quando aplicável)
      E as pré-condições em linguagem natural do `contract` do passo (ex.: "a lista
      de resultados apareceu") são satisfeitas — via VLM quando `condition_checker`
      está presente, senão por heurística da árvore, senão aprovadas como incertas.
    - anti-loop: não repete (estado, ação) que já falhou (via AntiLoopGuard).
    - verify pós-ação: confere as pós-condições do contrato e anexa `verify`.
    Qualquer falha de validação volta como `{"ok": False, ...}` SEM executar a ação.
    Métodos de que o AgentLoop precisa (schema/classify/for_dry_run) são delegados.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        perception: PerceptionChannel | None = None,
        guard: AntiLoopGuard | None = None,
        condition_checker: Callable[[str, ScreenState], bool | None] | None = None,
    ) -> None:
        self._registry = registry
        self.perception = perception
        self.guard = guard or AntiLoopGuard()
        #: Avalia condição NL x tela. None => só heurística da árvore. VLMConditionChecker
        #: é a implementação real (multimodal); injetado pela UI/CLI quando há provedor.
        self.condition_checker = condition_checker

    # -- delegação do que o AgentLoop consome ------------------------------- #
    def __getattr__(self, item: str) -> Any:
        return getattr(self._registry, item)

    # -- validação ---------------------------------------------------------- #
    def call(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        contract: Any | None = None,
    ) -> dict[str, Any]:
        args = dict(args or {})

        # 1) anti-loop: não repetir (estado, ação) já falho
        if self.perception is not None:
            try:
                state_sig = hash_state(self.perception.observe())
            except Exception:
                state_sig = ""
            if state_sig and self.guard.would_repeat(state_sig, _action_sig(name, args)):
                return {
                    "ok": False,
                    "anti_loop": True,
                    "error": (
                        "Anti-loop: (estado, acao) ja falhou antes. Tente estrategia "
                        "alternativa (outro seletor, scroll, fechar banner de cookie)."
                    ),
                    "tool": name,
                }

        # 2) assert pré-ação (uid determinístico + pré-condições NL do contrato)
        pre = self._pre_assert(name, args, contract)
        if not pre["ok"]:
            return {"ok": False, "precondition_failed": True, "error": pre["error"], "tool": name}

        # 3) executa de fato no registry real
        result = self._registry.call(name, args)

        # 4) verify pós-ação (best-effort, confere pós-condições do contrato)
        if self.perception is not None and result.get("ok"):
            try:
                result["verify"] = self._post_verify(name, args, result, contract)
            except Exception as exc:
                logger.debug("verify pos-acao falhou: %s", exc)

        # 5) registra na memória anti-loop
        if self.perception is not None:
            try:
                state_sig = hash_state(self.perception.observe())
                self.guard.note(
                    state_sig, _action_sig(name, args), bool(result.get("ok")),
                    str(result.get("error", "")),
                )
            except Exception:
                pass

        return result

    def _pre_assert(
        self,
        name: str,
        args: dict[str, Any],
        contract: Any | None = None,
    ) -> dict[str, Any]:
        """Confere pré-condições: uid determinístico + condições NL do contrato."""
        if self.perception is None:
            return {"ok": True}
        state = None

        # a) alvo determinístico por uid (click/type)
        if name in {"click_element", "type_element"}:
            uid = str(args.get("uid") or args.get("target") or "")
            if uid:
                try:
                    state = self.perception.observe()
                except Exception as exc:
                    logger.debug("observe falhou no assert: %s", exc)
                    return {"ok": True}  # não bloqueia se não dá para observar
                found = _find_uid(state, uid)
                if found is None:
                    return {
                        "ok": False,
                        "error": f"Pre-condicao falhou: elemento uid='{uid}' nao esta na arvore atual.",
                    }
                if not _is_visible(found):
                    return {
                        "ok": False,
                        "error": f"Pre-condicao falhou: elemento uid='{uid}' existe mas nao esta visivel/habilitado.",
                    }

        # b) pré-condições em linguagem natural do contrato do passo
        preconds = (contract or {}).get("preconditions") or []
        if preconds:
            if state is None:
                try:
                    state = self.perception.observe()
                except Exception as exc:
                    logger.debug("observe falhou nas pre-condicoes: %s", exc)
                    return {"ok": True}
            chk = self._check_conditions(state, preconds)
            if chk["failed"]:
                return {
                    "ok": False,
                    "error": "Pre-condicao (tela) falhou: " + "; ".join(chk["failed"]),
                }
        return {"ok": True}

    def _post_verify(
        self,
        name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        contract: Any | None = None,
    ) -> dict[str, Any]:
        """Confere as pós-condições NL do contrato contra a tela (best-effort)."""
        out = {"tool": name, "observation_keys": sorted(result.keys())}
        post = (contract or {}).get("postconditions") or []
        if not post:
            return out
        try:
            state = self.perception.observe()
            chk = self._check_conditions(state, post)
            out["postconditions"] = chk
            out["satisfied"] = not chk["failed"]
            if chk["failed"]:
                out["suggestion"] = (
                    "Pos-condicao nao satisfeita. Tente estrategia alternativa "
                    "(outro seletor, scroll, fechar banner) em vez de repetir a mesma acao."
                )
        except Exception as exc:
            logger.debug("verify pos-acao (contrato) falhou: %s", exc)
            out["postconditions"] = {"passed": [], "failed": [], "unverified": list(post)}
        return out

    # -- avaliação de condições NL x tela ----------------------------------- #
    def _check_conditions(
        self, state: ScreenState, conditions: list[str]
    ) -> dict[str, list[str]]:
        """Classifica cada condição em passou / falhou / incerta.

        Ordem: `condition_checker` (VLM) quando presente; senão heurística da
        árvore (`_screen_condition`). Incerta (`None`) NÃO bloqueia a ação — só
        uma falha explícita (`False`) impede. Isso evita paralisar por dúvida.
        """
        passed: list[str] = []
        failed: list[str] = []
        unverified: list[str] = []
        for cond in conditions or []:
            res: bool | None = None
            if self.condition_checker is not None:
                try:
                    res = self.condition_checker(cond, state)
                except Exception:
                    res = None
            if res is None:
                res = self._screen_condition(state, cond)
            if res is True:
                passed.append(cond)
            elif res is False:
                failed.append(cond)
            else:
                unverified.append(cond)
        return {"passed": passed, "failed": failed, "unverified": unverified}

    def _screen_condition(self, state: ScreenState, condition: str) -> bool | None:
        """Heurística barata: todos os substantivos da condição aparecem no dump?

        Só devolve True quando ACHA (evidência forte); devolve None quando não
        acha (incerto) — assim não bloqueamos ações por falsa dúvida. É o
        fallback quando não há VLM (`condition_checker`).
        """
        keywords = _extract_condition_keywords(condition)
        if not keywords:
            return None
        tree = state.ui_tree
        text = ""
        if tree is not None:
            if hasattr(tree, "dump"):
                try:
                    text = str(tree.dump() if callable(tree.dump) else tree).lower()
                except Exception:
                    text = ""
            elif hasattr(tree, "to_summary"):
                try:
                    text = tree.to_summary(include_bounds=False).lower()
                except Exception:
                    text = ""
            else:
                text = str(tree).lower()
        if not text:
            text = str(getattr(state, "extra", "") or "").lower()
        if not text:
            return None
        found = all(k in text for k in keywords)
        return True if found else None


class VLMConditionChecker:
    """Avalia uma condição NL contra a tela via VLM (multimodal real).

    Encapsula o provedor (Gemini/Ollama com `chat(image_bytes=...)`) e devolve
    True/False/None. None quando não há imagem ou o provedor não souber
    responder — aí o ValidatingToolRegistry cai para a heurística da árvore (ou
    aprova, se não houver como conferir). Nunca levanta.
    """

    def __init__(self, provider: Any, perception: PerceptionChannel | None = None) -> None:
        self.provider = provider
        self.perception = perception

    def __call__(self, condition: str, state: ScreenState) -> bool | None:
        if not condition:
            return None
        screenshot = getattr(state, "screenshot_bytes", None)
        try:
            prompt = (
                "Responda SOMENTE 'SIM' ou 'NAO' (uma unica palavra). A condicao abaixo "
                f"e verdadeira na imagem da tela? Condicao: {condition}"
            )
            if screenshot and hasattr(self.provider, "chat"):
                res = self.provider.chat(prompt, image_bytes=screenshot)
            elif hasattr(self.provider, "complete"):
                res = self.provider.complete(prompt, json_mode=False)
            else:
                return None
            text = _normalize_provider_text(res)
            # A falha do provedor chega no canal de texto (ver `provider_failure.py`)
            # e PRECISA ser checada antes do casamento de "sim"/"nao". As frases do
            # Gemini e do HybridProvider começam com "Não foi possível…", que
            # minusculizado casa com `startswith("não")` — ou seja, uma cota esgotada
            # era lida como a resposta "NÃO". O verificador devolvia `False` convicto
            # (a condição "não é verdadeira") e o loop do navegador agia sobre uma
            # condição que ninguém verificou. `None` = "não sei" é o desfecho correto.
            if is_provider_failure(text):
                logger.debug("VLMConditionChecker sem resposta do provedor: %s", failure_reason(text))
                return None
            low = (text or "").lower()
            # aceita 'sim'/'nao' no início ou próximo do início
            #
            # LIMITE CONHECIDO: o casamento é por prefixo, então uma RECUSA do modelo
            # que comece com "Não" ("Não foi possível determinar pela imagem.") ainda é
            # lida como a resposta "NÃO". Não é falha de provedor — é o modelo dizendo
            # que não sabe — e distinguir os dois exigiria reconhecer recusas, que são
            # abertas por natureza. Fica registrado: apertar isto sem uma lista de
            # recusas só trocaria um falso `False` por um `None` a mais.
            if low.startswith("sim") or " sim " in low[:12]:
                return True
            if low.startswith("nao") or low.startswith("não") or " nao " in low[:12]:
                return False
            return None
        except Exception as exc:
            logger.debug("VLMConditionChecker falhou: %s", exc)
            return None


def _normalize_provider_text(res: Any) -> str:
    """Normaliza o retorno variável de provedores (str / tuple / objeto)."""
    if isinstance(res, tuple):
        return res[0] if res and isinstance(res[0], str) else ""
    if isinstance(res, str):
        return res
    return str(getattr(res, "content", res) or "")


# --------------------------------------------------------------------------- #
# Planner consciente da visão
# --------------------------------------------------------------------------- #


class VisionBrowserPlanner:
    """Adapta um AgentPlanner para raciocinar com o estado da tela (visão+semântica).

    Não substitui o planejador: injeta o ScreenState atual no histórico para que o
    modelo veja a tela antes de decidir o próximo passo. Implementa o protocolo
    AgentPlanner. Para multimodal de verdade, troque o provedor por um com
    `chat(image_bytes=...)` e passe o screenshot junto — o ponto de injeção é este.
    """

    name = "vision-browser"

    def __init__(self, planner: AgentPlanner, perception: PerceptionChannel | None = None) -> None:
        self._planner = planner
        self.perception = perception

    @property
    def provider(self) -> str:
        return str(getattr(self._planner, "provider", getattr(self._planner, "name", "")))

    def decide(
        self,
        objective: str,
        tools: list[dict[str, Any]],
        history: list[dict[str, Any]],
        session_context: Sequence[dict[str, str]] | None = None,
    ) -> Any:
        enriched: list[dict[str, Any]] = list(history)
        if self.perception is not None:
            try:
                snap = self.perception.observe()
                enriched.append(
                    {"role": "system", "content": "TELA ATUAL:\n" + snap.to_summary()}
                )
            except Exception as exc:
                logger.debug("Falha ao observar tela no planejador: %s", exc)
        return self._planner.decide(objective, tools, enriched, session_context=session_context)


# --------------------------------------------------------------------------- #
# Planner multimodal (enxerga de verdade)
# --------------------------------------------------------------------------- #


class VisionLLMPlanner:
    """Planejador que passa o screenshot do estado atual ao provedor (multimodal real).

    Diferente do `LLMPlanner` (texto puro), aqui o modelo RECEBE a imagem da tela e
    decide o próximo passo vendo-a. Reaproveita `build_planner_prompt` do router. Se o
    provedor não souber de imagem ou a percepção falhar, degrada para texto (`complete`).
    """

    name = "vision-llm"

    def __init__(self, provider: Any, perception: PerceptionChannel | None = None) -> None:
        self.provider = provider
        self.perception = perception

    @property
    def provider_name(self) -> str:
        return str(getattr(self.provider, "name", getattr(self.provider, "model", "")))

    def decide(
        self,
        objective: str,
        tools: list[dict[str, Any]],
        history: list[dict[str, Any]],
        session_context: Sequence[dict[str, str]] | None = None,
        max_steps: int | None = None,
    ) -> AgentDecision:
        prompt = build_planner_prompt(
            objective,
            tools,
            history,
            session_context=session_context,
            max_steps=max_steps,
            system_instruction=_VISION_SYSTEM_INSTRUCTION,
        )
        screenshot = None
        if self.perception is not None:
            try:
                screenshot = self.perception.observe().screenshot_bytes
            except Exception as exc:
                logger.debug("Falha ao capturar tela no planner vision: %s", exc)

        raw = self._ask_provider(prompt, screenshot)
        if not raw:
            return AgentDecision(error="Provedor vision nao retornou conteudo.")
        parsed = extract_json_object(raw)
        if parsed is None:
            return AgentDecision(error=f"Resposta vision nao era JSON utilizavel: {raw[:160]}")
        return decision_from_json(parsed)

    def _ask_provider(self, prompt: str, screenshot: bytes | None) -> str:
        """Chama o provedor com imagem quando possível; degrada para texto."""
        provider = self.provider
        if screenshot and hasattr(provider, "chat"):
            try:
                res = provider.chat(prompt, image_bytes=screenshot)
                if isinstance(res, tuple):
                    return res[0] if res and isinstance(res[0], str) else ""
                if isinstance(res, str):
                    return res
                return str(getattr(res, "content", res))
            except Exception as exc:
                logger.debug("chat multimodal falhou, caindo para complete: %s", exc)
        if hasattr(provider, "complete"):
            try:
                return provider.complete(prompt, system_prompt=_VISION_SYSTEM_INSTRUCTION, json_mode=True)
            except Exception as exc:
                return AgentDecision(error=f"Provedor vision falhou: {exc}").error or ""
        return ""


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #


def build_browser_runtime(
    *,
    planner: AgentPlanner,
    perception: PerceptionChannel | None = None,
    registry: ToolRegistry | None = None,
    guard: AntiLoopGuard | None = None,
    condition_checker: Callable[[str, ScreenState], bool | None] | None = None,
    max_steps: int = 15,
    max_seconds: float = 300.0,
    run_id: str | None = None,
) -> AgentLoop:
    """Monta um AgentLoop pronto para o fluxo guiado por visão.

    Reaproveita o AgentLoop existente: a validação pré/pós-ação e o anti-loop entram
    via ValidatingToolRegistry + EpisodeMemory (memory=), sem tocar no núcleo do loop.
    `condition_checker` (ex.: VLMConditionChecker) habilita validar pré/pós-condições
    em linguagem natural do contrato do passo contra a tela.
    """
    if registry is None:
        registry = ToolRegistry()
    if guard is None:
        guard = AntiLoopGuard()

    vregistry = ValidatingToolRegistry(
        registry,
        perception=perception,
        guard=guard,
        condition_checker=condition_checker,
    )
    loop = AgentLoop(
        planner,
        vregistry,
        max_steps=max_steps,
        max_seconds=max_seconds,
        run_id=run_id,
        memory=guard.memory,
    )
    return loop
