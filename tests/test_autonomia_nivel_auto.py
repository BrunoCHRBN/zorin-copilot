# Decisão de design: valida o nível `RiskLevel.AUTO` (item 4 do
# `docs/ANEXO_AUTONOMIA_PREREQUISITOS.md`). O nível executa SEM pergunta e sinaliza aviso
# posterior (`notify_after`) — sinal distinto de `requires_approval`. Nenhuma regra embutida
# produz AUTO (fail-safe), então o caminho é exercitado via `register_risk`, a API pública que
# MCP e as futuras regras de zona de confiança/reversibilidade usam.

"""Nível de risco AUTO: executa sem aprovação, com aviso posterior."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from zorin_copilot.ai.agent import (  # noqa: E402
    STOP_DONE,
    AgentLoop,
    ScriptedPlanner,
    Step,
    ToolCall,
)
from zorin_copilot.ai.agent_tools import ToolRegistry  # noqa: E402
from zorin_copilot.ai.live import GeminiLiveClient  # noqa: E402
from zorin_copilot.core.config import CopilotConfig  # noqa: E402
from zorin_copilot.shell.risk import RiskLevel, RiskPolicy  # noqa: E402

_AUTO_TOOL = "ferramenta_reversivel"


class _StubRegistry(ToolRegistry):
    """Registro com `call` programado — o laço não precisa de desktop nem de spec real."""

    def __init__(self, policy: RiskPolicy | None = None) -> None:
        super().__init__(policy=policy)
        self.calls: list[tuple[str, dict]] = []

    def call(self, name, args=None, contract=None):
        self.calls.append((name, dict(args or {})))
        return {"ok": True, "message": "feito"}


def _auto_registry() -> _StubRegistry:
    policy = RiskPolicy()
    policy.register_risk(_AUTO_TOOL, RiskLevel.AUTO, "ação reversível")
    return _StubRegistry(policy=policy)


class RiskLevelAutoTest(unittest.TestCase):
    def test_auto_eh_um_nivel_distinto(self):
        self.assertEqual(RiskLevel.AUTO.value, "auto")
        self.assertIsNot(RiskLevel.AUTO, RiskLevel.SAFE)
        self.assertIsNot(RiskLevel.AUTO, RiskLevel.CONFIRM)

    def test_nenhuma_regra_embutida_produz_auto(self):
        # Fail-safe: nada embutido é AUTO por enquanto. Escrita, agenda, Gmail, Drive,
        # hotkey destrutivo e comando sensível continuam CONFIRM; o resto, SAFE.
        policy = RiskPolicy()
        casos = [
            ("write_file", {"file_path": "/tmp/x"}),
            ("edit_file", {"path": "/tmp/x"}),
            ("write_document", {"filename": "a.md", "content": "x"}),
            ("calendar_event", {"action": "create"}),
            ("google_gmail", {"action": "send"}),
            ("google_drive", {"action": "trash"}),
            ("keyboard_hotkey", {"keys": ["alt", "f4"]}),
            ("run_command", {"command": "rm -rf /tmp/x"}),
            ("get_ui_tree", {}),
            ("launch_app", {"app_name": "firefox"}),
        ]
        for name, args in casos:
            level, _ = policy.classify(name, args)
            self.assertIsNot(level, RiskLevel.AUTO, f"{name} não pode ser AUTO por padrão")

    def test_ferramenta_registrada_como_auto_sinaliza_aviso_e_nao_aprovacao(self):
        registry = _auto_registry()
        self.assertIs(registry.classify(_AUTO_TOOL, {})[0], RiskLevel.AUTO)
        self.assertTrue(registry.notify_after(_AUTO_TOOL, {}))
        self.assertFalse(registry.requires_approval(_AUTO_TOOL, {}))


class StepNotifyAfterTest(unittest.TestCase):
    def test_roundtrip_preserva_notify_after(self):
        marcado = Step(index=0, tool="x", notify_after=True)
        self.assertTrue(Step.from_dict(marcado.to_dict()).notify_after)
        self.assertFalse(Step.from_dict(Step(index=1, tool="y").to_dict()).notify_after)

    def test_loop_executa_auto_sem_aprovar_e_marca_o_passo(self):
        registry = _auto_registry()
        pedidos: list = []

        def on_approval(step):  # se fosse pedida aprovação, isto recusaria
            pedidos.append(step)
            return False

        planner = ScriptedPlanner(
            [ToolCall(_AUTO_TOOL, {"x": 1}), ToolCall("done", {"answer": "ok"})]
        )
        result = AgentLoop(planner, registry).run("faça", on_approval=on_approval)

        self.assertEqual(result.stop_reason, STOP_DONE)
        self.assertEqual(pedidos, [])  # nunca pediu aprovação
        passo = result.steps[0]
        self.assertTrue(passo.notify_after)
        self.assertFalse(passo.requires_approval)
        self.assertEqual(passo.risk, "auto")
        self.assertEqual(registry.calls, [(_AUTO_TOOL, {"x": 1})])

    def test_confirm_continua_pedindo_aprovacao(self):
        registry = _StubRegistry(policy=RiskPolicy())
        pedidos: list = []

        def on_approval(step):
            pedidos.append(step)
            return True

        planner = ScriptedPlanner(
            [ToolCall("write_file", {"file_path": "/tmp/x"}), ToolCall("done", {"answer": "ok"})]
        )
        result = AgentLoop(planner, registry).run("faça", on_approval=on_approval)

        self.assertEqual(len(pedidos), 1)
        self.assertTrue(result.steps[0].requires_approval)
        self.assertFalse(result.steps[0].notify_after)


class LiveAutoTest(unittest.TestCase):
    def test_live_executa_auto_sem_confirmation_id(self):
        policy = RiskPolicy()
        policy.register_risk(_AUTO_TOOL, RiskLevel.AUTO, "ação reversível")
        c = GeminiLiveClient.__new__(GeminiLiveClient)
        c.config = CopilotConfig()
        c.risk_policy = policy
        c._pending_actions = {}
        c.tool_registry = ToolRegistry(policy=policy)

        out = c._dispatch_tool(_AUTO_TOOL, {})
        self.assertFalse(out.get("requires_confirmation", False))
        self.assertEqual(c._pending_actions, {})


if __name__ == "__main__":
    unittest.main()
