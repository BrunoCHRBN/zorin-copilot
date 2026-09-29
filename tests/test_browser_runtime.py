# Decisão de design: o runtime do agente guiado por visão é testado SEM GTK, SEM
# AT-SPI e SEM rede. A percepção entra como dublê (FakePerception) e o registry real
# recebe handlers falsos. O que sobra para o teste é a lógica que importa: o assert
# pré-ação bloqueia a ação e NÃO chama o handler; o anti-loop recusa repetir (estado,
# ação) que já falhou; e o loop completo roda um roteiro ponta a ponta.

"""Testes do runtime do agente guiado por visão (percepção + validação + anti-loop)."""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Any

from zorin_copilot.ai.agent import AgentDecision, ScriptedPlanner, ToolCall
from zorin_copilot.ai.agent_tools import ToolRegistry, ToolSpec, _param
from zorin_copilot.ai.browser_runtime import (
    AntiLoopGuard,
    EpisodeMemory,
    StepContract,
    VLMConditionChecker,
    ValidatingToolRegistry,
    VisionBrowserPlanner,
    build_browser_runtime,
)
from zorin_copilot.core.perception import ScreenState
from zorin_copilot.shell.risk import RiskLevel


class FakeFence:
    def is_coordinate_allowed(self, x: int, y: int) -> tuple[bool, str]:
        return True, ""


class FakeDriver:
    def click(self, x: int, y: int, button: str = "left", double: bool = False):
        return True, "ok"

    def click_relative(self, rel_x: float, rel_y: float, button: str = "left", double: bool = False):
        return True, "ok"

    def type_text(self, text: str, press_enter: bool = False):
        return True, "ok"

    def hotkey(self, *keys: str):
        return True, "ok"


@dataclass
class FakeElement:
    uid: str
    states: tuple[str, ...] = ()
    name: str = ""
    role: str = "push_button"

    def find(self, pred):
        return self if pred(self) else None


class FakeTree:
    def __init__(self, elements: list[FakeElement]) -> None:
        self._elements = elements

    def find(self, pred):
        for el in self._elements:
            if pred(el):
                return el
        return None

    def to_summary(self, include_bounds: bool = False) -> str:
        return "TREE:" + ",".join(e.uid for e in self._elements)


class FakePerception:
    """PerceptionChannel dublê: devolve a árvore/tela que o teste controla."""

    def __init__(self, tree: FakeTree | None = None, screenshot: bytes = b"img") -> None:
        self.tree = tree
        self.screenshot = screenshot
        self.calls = 0

    def observe(self) -> ScreenState:
        self.calls += 1
        return ScreenState(ui_tree=self.tree, screenshot_bytes=self.screenshot, app="fake")

    def ground(self, query: str, mode: str = "bbox") -> list[dict[str, Any]]:
        return []


def _reg_with(handlers: dict[str, Any]) -> ToolRegistry:
    reg = ToolRegistry(input_driver=FakeDriver(), fence=FakeFence())
    for name, handler in handlers.items():
        reg.register(ToolSpec(name=name, description=name, parameters=_param({}), handler=handler, mutating=True))
    return reg


# --------------------------------------------------------------------------- #
# Memória de episódio
# --------------------------------------------------------------------------- #


class TestEpisodeMemory(unittest.TestCase):
    def test_would_repeat_only_after_failure(self):
        mem = EpisodeMemory(max_attempts_per_state_action=1)
        self.assertFalse(mem.would_repeat("s1", "click|a"))
        mem.note("s1", "click|a", False, "not found")
        self.assertTrue(mem.would_repeat("s1", "click|a"))
        # um sucesso limpa a recusa
        mem.note("s1", "click|a", True)
        self.assertFalse(mem.would_repeat("s1", "click|a"))

    def test_sticky_notes(self):
        mem = EpisodeMemory()
        mem.learn("mercadolivre", "cookie banner primeiro")
        self.assertEqual(mem.recall("mercadolivre"), "cookie banner primeiro")


# --------------------------------------------------------------------------- #
# Registry validador
# --------------------------------------------------------------------------- #


class TestValidatingRegistry(unittest.TestCase):
    def test_precondition_blocks_and_does_not_call_handler(self):
        called: list[dict[str, Any]] = []

        def handler(args):
            called.append(args)
            return {"ok": True, "did": "click"}

        reg = _reg_with({"click_element": handler})
        percep = FakePerception(tree=FakeTree([FakeElement("ok1")]))
        vreg = ValidatingToolRegistry(reg, perception=percep)

        res = vreg.call("click_element", {"uid": "sumiu"})
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("precondition_failed"))
        self.assertEqual(called, [])  # handler NÃO foi executado

    def test_allows_when_target_present(self):
        called: list[dict[str, Any]] = []

        def handler(args):
            called.append(args)
            return {"ok": True, "did": "click"}

        reg = _reg_with({"click_element": handler})
        percep = FakePerception(tree=FakeTree([FakeElement("ok1")]))
        vreg = ValidatingToolRegistry(reg, perception=percep)

        res = vreg.call("click_element", {"uid": "ok1"})
        self.assertTrue(res["ok"])
        self.assertEqual(len(called), 1)

    def test_anti_loop_blocks_repeat(self):
        called: list[int] = []

        def handler(args):
            called.append(1)
            return {"ok": False, "error": "falhou"}

        reg = _reg_with({"click_element": handler})
        percep = FakePerception(tree=FakeTree([FakeElement("x1")]))
        guard = AntiLoopGuard(max_attempts=1)
        vreg = ValidatingToolRegistry(reg, perception=percep, guard=guard)

        r1 = vreg.call("click_element", {"uid": "x1"})  # executa, falha, memoriza
        self.assertFalse(r1["ok"])
        r2 = vreg.call("click_element", {"uid": "x1"})  # anti-loop bloqueia
        self.assertTrue(r2.get("anti_loop"))
        self.assertEqual(len(called), 1)  # handler só rodou uma vez


# --------------------------------------------------------------------------- #
# Planner consciente da visão
# --------------------------------------------------------------------------- #


class TestVisionBrowserPlanner(unittest.TestCase):
    def test_enriches_history_with_screen(self):
        scripted = ScriptedPlanner([ToolCall(name="click_element", args={"uid": "a"})])
        percep = FakePerception(tree=FakeTree([FakeElement("a")]))
        planner = VisionBrowserPlanner(scripted, perception=percep)

        planner.decide("obj", [], [])
        self.assertGreaterEqual(percep.calls, 1)  # percepção foi consultada


# --------------------------------------------------------------------------- #
# Ponta a ponta
# --------------------------------------------------------------------------- #


class TestBrowserRuntimeEndToEnd(unittest.TestCase):
    def test_run_scripted_flow(self):
        def click_h(args):
            return {"ok": True, "did": "click"}

        def type_h(args):
            return {"ok": True, "did": "type"}

        reg = _reg_with({"click_element": click_h, "type_element": type_h})
        percep = FakePerception(tree=FakeTree([FakeElement("a"), FakeElement("b")]))
        planner = VisionBrowserPlanner(
            ScriptedPlanner(
                [
                    ToolCall(name="click_element", args={"uid": "a"}),
                    ToolCall(name="type_element", args={"uid": "b", "text": "produto x"}),
                    AgentDecision(final_answer="10 primeiros produtos apresentados"),
                ]
            ),
            perception=percep,
        )
        loop = build_browser_runtime(planner=planner, perception=percep, registry=reg, max_steps=10)

        result = loop.run("abrir mercado livre e listar 10", on_approval=lambda s: True)
        self.assertTrue(result.success, result.error)
        # 2 ações (click + type); o final_answer encerra sem criar passo.
        self.assertEqual(len(result.steps), 2)
        self.assertEqual(result.stop_reason, "done")

    def test_step_contract_is_dataclass(self):
        c = StepContract(intent="clicar", preconditions=["botao visivel"])
        self.assertEqual(c.intent, "clicar")


# --------------------------------------------------------------------------- #
# Planner multimodal (enxerga a tela)
# --------------------------------------------------------------------------- #


class FakeVisionProvider:
    def __init__(self) -> None:
        self.chat_calls: list[tuple[str, Any]] = []
        self.complete_calls: list[str] = []

    def chat(self, prompt: str, image_bytes: bytes | None = None, image_mime: str = "image/jpeg"):
        self.chat_calls.append((prompt, image_bytes))
        return (
            json.dumps(
                {
                    "tool": "click_element",
                    "args": {"uid": "a"},
                    "preconditions": ["botao visivel"],
                    "postconditions": ["clicado"],
                }
            ),
            [],
        )

    def complete(self, prompt: str, system_prompt: str | None = None, json_mode: bool = True) -> str:
        self.complete_calls.append(prompt)
        return json.dumps({"tool": "click_element", "args": {"uid": "a"}})


class TestVisionLLMPlanner(unittest.TestCase):
    def test_decides_with_image_and_contract(self):
        from zorin_copilot.ai.browser_runtime import VisionLLMPlanner

        percep = FakePerception(tree=FakeTree([FakeElement("a")]), screenshot=b"IMG")
        provider = FakeVisionProvider()
        planner = VisionLLMPlanner(provider, perception=percep)

        dec = planner.decide("obj", [], [])
        self.assertIsNotNone(dec.tool_call)
        self.assertEqual(dec.tool_call.name, "click_element")
        self.assertEqual(dec.preconditions, ["botao visivel"])
        self.assertEqual(dec.postconditions, ["clicado"])
        # o screenshot foi realmente enviado ao provedor
        self.assertEqual(len(provider.chat_calls), 1)
        self.assertEqual(provider.chat_calls[0][1], b"IMG")

    def test_degrades_to_text_without_screenshot(self):
        from zorin_copilot.ai.browser_runtime import VisionLLMPlanner

        percep = FakePerception(tree=FakeTree([FakeElement("a")]), screenshot=None)
        provider = FakeVisionProvider()
        planner = VisionLLMPlanner(provider, perception=percep)

        planner.decide("obj", [], [])
        self.assertEqual(len(provider.complete_calls), 1)
        self.assertEqual(len(provider.chat_calls), 0)


# --------------------------------------------------------------------------- #
# Contrato (pré/pós-condições) propagado pelo loop
# --------------------------------------------------------------------------- #


class TestContractPlumbing(unittest.TestCase):
    def test_step_carries_contract(self):
        from zorin_copilot.ai.agent import AgentDecision, AgentLoop, ToolCall

        reg = _reg_with({"click_element": lambda a: {"ok": True}})
        planner = ScriptedPlanner(
            [
                AgentDecision(
                    tool_call=ToolCall(name="click_element", args={"uid": "a"}),
                    preconditions=["visivel"],
                    postconditions=["clicado"],
                ),
                AgentDecision(final_answer="fim"),
            ]
        )
        loop = AgentLoop(planner, reg, max_steps=5)
        result = loop.run("obj", on_approval=lambda s: True)
        self.assertTrue(result.success, result.error)
        self.assertIsNotNone(result.steps[0].contract)
        self.assertEqual(result.steps[0].contract["preconditions"], ["visivel"])
        self.assertEqual(result.steps[0].contract["postconditions"], ["clicado"])


# --------------------------------------------------------------------------- #
# Validação por contrato (pré/pós-condições em linguagem natural)
# --------------------------------------------------------------------------- #


class FakeConditionChecker:
    """condition_checker determinístico: devolve o mapeamento que o teste define."""

    def __init__(self, mapping: dict[str, bool | None] | None = None) -> None:
        self.mapping = dict(mapping or {})
        self.calls: list[str] = []

    def __call__(self, condition: str, state: Any) -> bool | None:
        self.calls.append(condition)
        return self.mapping.get(condition)


class TestContractValidation(unittest.TestCase):
    def test_precondition_false_blocks_without_calling_handler(self):
        called: list[int] = []

        def handler(args):
            called.append(1)
            return {"ok": True, "did": "click"}

        reg = _reg_with({"click_element": handler})
        percep = FakePerception(tree=FakeTree([FakeElement("ok1")]))
        checker = FakeConditionChecker({"a lista apareceu": False})
        vreg = ValidatingToolRegistry(
            reg, perception=percep, condition_checker=checker
        )
        contract = {"preconditions": ["a lista apareceu"], "postconditions": []}

        res = vreg.call("click_element", {"uid": "ok1"}, contract=contract)
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("precondition_failed"))
        self.assertEqual(called, [])  # handler NÃO executou

    def test_precondition_true_allows(self):
        called: list[int] = []

        def handler(args):
            called.append(1)
            return {"ok": True, "did": "click"}

        reg = _reg_with({"click_element": handler})
        percep = FakePerception(tree=FakeTree([FakeElement("ok1")]))
        checker = FakeConditionChecker({"o campo visivel": True})
        vreg = ValidatingToolRegistry(reg, perception=percep, condition_checker=checker)
        contract = {"preconditions": ["o campo visivel"], "postconditions": []}

        res = vreg.call("click_element", {"uid": "ok1"}, contract=contract)
        self.assertTrue(res["ok"])
        self.assertEqual(len(called), 1)

    def test_postcondition_recorded_in_verify(self):
        def handler(args):
            return {"ok": True, "did": "click"}

        reg = _reg_with({"click_element": handler})
        percep = FakePerception(tree=FakeTree([FakeElement("ok1")]))
        checker = FakeConditionChecker({"clicado": True})
        vreg = ValidatingToolRegistry(reg, perception=percep, condition_checker=checker)
        contract = {"preconditions": [], "postconditions": ["clicado"]}

        res = vreg.call("click_element", {"uid": "ok1"}, contract=contract)
        self.assertTrue(res["ok"])
        verify = res.get("verify", {})
        self.assertIn("postconditions", verify)
        self.assertEqual(verify["postconditions"]["passed"], ["clicado"])
        self.assertTrue(verify["satisfied"])

    def test_unverified_precondition_does_not_block(self):
        """Sem VLM nem palavra-chave na árvore => incerto (None) => não bloqueia."""
        called: list[int] = []

        def handler(args):
            called.append(1)
            return {"ok": True}

        reg = _reg_with({"click_element": handler})
        percep = FakePerception(tree=FakeTree([FakeElement("ok1")]))
        vreg = ValidatingToolRegistry(reg, perception=percep)  # sem checker
        contract = {"preconditions": ["condicao vaga e estranha xyz"], "postconditions": []}

        res = vreg.call("click_element", {"uid": "ok1"}, contract=contract)
        self.assertTrue(res["ok"])
        self.assertEqual(len(called), 1)

    def test_heuristic_screen_condition_true_and_unknown(self):
        vreg = ValidatingToolRegistry(_reg_with({}), perception=None)
        state_present = ScreenState(
            ui_tree=FakeTree([FakeElement("produto_disponivel")]), app="fake"
        )
        state_absent = ScreenState(ui_tree=FakeTree([FakeElement("outro")]), app="fake")
        # contém ambas as palavras-chave extraídas => True
        self.assertTrue(vreg._screen_condition(state_present, "produto disponivel"))
        # só uma => incerto (None), nunca bloqueia por dúvida
        self.assertIsNone(vreg._screen_condition(state_absent, "produto disponivel"))


class FakeTextProvider:
    """Provedor que devolve texto simples (usado pelo VLMConditionChecker)."""

    def __init__(self, chat_text: str = "SIM", complete_text: str = "SIM") -> None:
        self.chat_text = chat_text
        self.complete_text = complete_text
        self.chat_calls: list[tuple[str, Any]] = []

    def chat(self, prompt: str, image_bytes: bytes | None = None) -> str:
        self.chat_calls.append((prompt, image_bytes))
        return self.chat_text

    def complete(self, prompt: str, system_prompt: str | None = None, json_mode: bool = True) -> str:
        return self.complete_text


class TestVLMConditionChecker(unittest.TestCase):
    def test_yes_no_unknown(self):
        percep = FakePerception(tree=FakeTree([FakeElement("a")]), screenshot=b"IMG")
        self.assertTrue(VLMConditionChecker(FakeTextProvider("SIM"), percep)("visivel", percep.observe()))
        self.assertFalse(VLMConditionChecker(FakeTextProvider("NAO"), percep)("visivel", percep.observe()))
        # resposta ambígua => None (incerto)
        self.assertIsNone(VLMConditionChecker(FakeTextProvider("talvez"), percep)("visivel", percep.observe()))

    def test_falls_back_to_complete_without_image(self):
        percep = FakePerception(tree=FakeTree([FakeElement("a")]), screenshot=None)
        provider = FakeTextProvider(complete_text="SIM")
        checker = VLMConditionChecker(provider, percep)
        self.assertTrue(checker("visivel", percep.observe()))
        # sem imagem, deve ter usado complete (não chat)
        self.assertEqual(len(provider.chat_calls), 0)

    def test_falha_do_provedor_vira_incerto_e_nunca_afirma(self):
        """A frase de erro do provedor não pode virar `True` nem `False`.

        O contrato do verificador é `True`/`False`/`None`, e a falha chega pelo canal
        de texto (ver `provider_failure.py`). Se a frase fosse interpretada, o loop do
        navegador agiria com base numa condição que ninguém verificou. `None` é o
        desfecho correto: "não sei", e quem chama cai para a heurística da árvore.

        O docstring da classe já afirmava "nunca levanta"; aqui a afirmação vira
        evidência — inclusive para os formatos de falha que não abrem a mensagem.
        """
        percep = FakePerception(tree=FakeTree([FakeElement("a")]), screenshot=b"IMG")
        frases = [
            "Não foi possível obter resposta do Gemini: Erro no modelo (429): RESOURCE_EXHAUSTED",
            "Não foi possível processar sua solicitação: o Gemini está indisponível/sem cota.",
            "Erro ao consultar Ollama local (qwen3:8b): Connection refused",
            "",
            "   ",
        ]
        for frase in frases:
            with self.subTest(frase=frase[:44]):
                provider = FakeTextProvider(chat_text=frase, complete_text=frase)
                self.assertIsNone(
                    VLMConditionChecker(provider, percep)("visivel", percep.observe())
                )

    def test_loop_records_postcondition_verify(self):
        """Ponta a ponta: o loop passa o contrato e a pós-condição vai ao observation."""
        from zorin_copilot.ai.agent import AgentDecision, AgentLoop, ToolCall

        reg = _reg_with({"click_element": lambda a: {"ok": True}})
        percep = FakePerception(tree=FakeTree([FakeElement("a")]))
        checker = FakeConditionChecker({"clicado": True})
        vreg = ValidatingToolRegistry(reg, perception=percep, condition_checker=checker)
        planner = ScriptedPlanner(
            [
                AgentDecision(
                    tool_call=ToolCall(name="click_element", args={"uid": "a"}),
                    preconditions=[],
                    postconditions=["clicado"],
                ),
                AgentDecision(final_answer="fim"),
            ]
        )
        loop = AgentLoop(planner, vreg, max_steps=5)
        result = loop.run("obj", on_approval=lambda s: True)
        self.assertTrue(result.success, result.error)
        self.assertTrue(result.steps[0].observation.get("verify", {}).get("satisfied"))


if __name__ == "__main__":
    unittest.main()
