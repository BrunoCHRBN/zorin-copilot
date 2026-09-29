# Decisão de design: valida os pré-requisitos de autonomia
# (`docs/ANEXO_AUTONOMIA_PREREQUISITOS.md`, itens 1–3). Três contratos, exercitados
# sem WebSocket real, sem rede e sem AT-SPI:
#   (1) o Live higieniza a observação ANTES de devolvê-la ao modelo — verificado no
#       caminho real (`_execute_tool_call`), não só no helper de higienização;
#   (2) o portão de risco usa selo POR CHAMADA, não estado do cliente (fim do fail-open);
#   (3) o cliente aceita uma política injetada — Live e registry compartilham a MESMA fonte.

"""Pré-requisitos de autonomia: higienização de observação, selo por chamada, política injetável."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from zorin_copilot.ai.live import GeminiLiveClient  # noqa: E402
from zorin_copilot.core.config import CopilotConfig  # noqa: E402
from zorin_copilot.shell.risk import RiskLevel, RiskPolicy  # noqa: E402


class _FakeInputDriver:
    def __init__(self):
        self.calls: list[tuple] = []

    def hotkey(self, *keys):
        self.calls.append(keys)
        return True, f"hotkey {'+'.join(keys)}"


def _bare_client(policy: RiskPolicy | None = None) -> GeminiLiveClient:
    """Cliente mínimo via `__new__` (mesmo padrão de test_fase3_risk), sem __init__."""
    c = GeminiLiveClient.__new__(GeminiLiveClient)
    c.config = CopilotConfig()
    c.risk_policy = policy if policy is not None else RiskPolicy()
    c._pending_actions = {}
    c.input_driver = _FakeInputDriver()
    c._executed_actions_log = []
    c.on_tool_executed = None
    # `_set_state` lê este atributo direto (sem getattr) — `__init__` o define; o
    # cliente nu precisa fornecê-lo para exercitar qualquer caminho que mude de estado.
    c.on_state_change = None
    return c


class PerCallRiskSealTest(unittest.TestCase):
    """O selo de aprovação é por chamada — nunca estado do cliente."""

    def test_risky_call_is_gated_without_seal(self):
        c = _bare_client()
        out = c._dispatch_tool("keyboard_hotkey", {"keys": ["alt", "f4"]})
        self.assertTrue(out.get("requires_confirmation"))
        self.assertEqual(c.input_driver.calls, [])

    def test_seal_is_per_call_and_does_not_leak(self):
        c = _bare_client()
        out = c._dispatch_tool("keyboard_hotkey", {"keys": ["alt", "f4"]})
        self.assertTrue(out.get("requires_confirmation"))
        # Aprovado explicitamente: executa.
        res = c._dispatch_tool("keyboard_hotkey", {"keys": ["alt", "f4"]}, approved=True)
        self.assertTrue(res.get("success"))
        self.assertEqual(c.input_driver.calls, [("alt", "f4")])
        # A PRÓXIMA chamada volta a ser barrada: o selo não vazou para ela.
        out2 = c._dispatch_tool("keyboard_hotkey", {"keys": ["alt", "f4"]})
        self.assertTrue(out2.get("requires_confirmation"))
        self.assertEqual(len(c.input_driver.calls), 1)

    def test_confirm_action_executes_the_approved_action(self):
        c = _bare_client()
        out = c._dispatch_tool("keyboard_hotkey", {"keys": ["alt", "f4"]})
        cid = out["confirmation_id"]
        res = c._dispatch_tool("confirm_action", {"confirmation_id": cid, "approve": True})
        self.assertTrue(res.get("success"))
        self.assertEqual(c.input_driver.calls, [("alt", "f4")])

    def test_deny_cancels_without_executing(self):
        c = _bare_client()
        out = c._dispatch_tool("keyboard_hotkey", {"keys": ["alt", "f4"]})
        cid = out["confirmation_id"]
        res = c._dispatch_tool("confirm_action", {"confirmation_id": cid, "approve": False})
        self.assertTrue(res.get("success"))
        self.assertEqual(c.input_driver.calls, [])

    def test_no_shared_bypass_attribute(self):
        # Guarda contra a reintrodução do booleano de instância (fail-open sob concorrência).
        c = _bare_client()
        self.assertFalse(hasattr(c, "_bypass_risk_gate"))


class ObservationSanitizationTest(unittest.TestCase):
    """O Live envolve conteúdo externo como não confiável antes de devolvê-lo ao modelo."""

    def test_long_external_content_is_wrapped(self):
        c = _bare_client()
        content = "linha um\nlinha dois\nlinha três"
        out = c._sanitize_tool_output(
            "read_file", {"ok": True, "path": "/tmp/x", "content": content}
        )
        self.assertIn("untrusted_tool_observation", out["content"])
        self.assertIn(content, out["content"])
        # Campos curtos e não-textuais ficam intactos.
        self.assertEqual(out["path"], "/tmp/x")
        self.assertIs(out["ok"], True)

    def test_short_status_passes_through(self):
        c = _bare_client()
        out = c._sanitize_tool_output("run_command", {"ok": True, "message": "OK"})
        self.assertEqual(out["message"], "OK")

    def test_nested_structures_are_traversed(self):
        c = _bare_client()
        long_title = "a" * 200
        out = c._sanitize_tool_output("web_search", {"items": [{"title": long_title}]})
        self.assertIn("untrusted_tool_observation", out["items"][0]["title"])

    def test_input_dict_is_not_mutated(self):
        # O dicionário original (log/UI) não é alterado pela higienização.
        c = _bare_client()
        raw = {"content": "linha um\nlinha dois"}
        c._sanitize_tool_output("read_file", raw)
        self.assertEqual(raw["content"], "linha um\nlinha dois")

    def test_execute_tool_call_sends_the_sanitized_observation(self):
        """O caminho real (`_execute_tool_call`) USA a higienização.

        Os testes acima provam que ``_sanitize_tool_output`` funciona — não que o Live
        o chame. A diferença é o defeito inteiro: apagar a chamada em
        ``_execute_tool_call`` deixava a suíte verde. Aqui se inspeciona o payload que
        de fato vai pelo WebSocket, e se confirma que o log de auditoria fica cru.
        """
        c = _bare_client()
        raw = "linha um\nlinha dois\nlinha três"
        c._dispatch_tool = lambda name, args: {  # type: ignore[method-assign]
            "success": True,
            "path": "/tmp/x",
            "content": raw,
        }

        enviados: list[str] = []

        class _FakeWS:
            async def send(self, payload: str) -> None:
                enviados.append(payload)

        asyncio.run(
            c._execute_tool_call(
                _FakeWS(),
                {
                    "functionCalls": [
                        {"id": "c1", "name": "read_file", "args": {"path": "/tmp/x"}}
                    ]
                },
            )
        )

        self.assertEqual(len(enviados), 1)
        fr = json.loads(enviados[0])["toolResponse"]["functionResponses"][0]
        model_output = fr["response"]["output"]

        # O modelo recebe o conteúdo externo envolvido como não confiável...
        self.assertIn("untrusted_tool_observation", model_output["content"])
        self.assertIn(raw, model_output["content"])
        # ...e campos não-textuais seguem intactos.
        self.assertEqual(model_output["path"], "/tmp/x")

        # O log de auditoria preserva o resultado cru (UI/depuração).
        self.assertEqual(c._executed_actions_log[-1]["output"]["content"], raw)


class InjectedRiskPolicyTest(unittest.TestCase):
    """Uma política injetada é a fonte única de risco (Live + registry)."""

    def test_injected_policy_is_used_by_client_and_registry(self):
        policy = RiskPolicy()
        c = GeminiLiveClient(config=CopilotConfig(gemini_api_key=""), risk_policy=policy)
        self.assertIs(c.risk_policy, policy)
        self.assertIs(c.tool_registry.policy, policy)

    def test_without_injection_client_owns_a_consistent_policy(self):
        c = GeminiLiveClient(config=CopilotConfig(gemini_api_key=""))
        self.assertIsInstance(c.risk_policy, RiskPolicy)
        self.assertIs(c.tool_registry.policy, c.risk_policy)

    def test_rule_registered_on_injected_policy_is_honored(self):
        policy = RiskPolicy()
        policy.register_risk("ferramenta_de_teste_x", RiskLevel.CONFIRM, "teste")
        c = _bare_client(policy=policy)
        out = c._dispatch_tool("ferramenta_de_teste_x", {})
        self.assertTrue(out.get("requires_confirmation"))


if __name__ == "__main__":
    unittest.main()
