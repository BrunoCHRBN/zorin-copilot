# Decisão de design: o roteamento é determinístico e por isso testável sem rede.
# Os testes fixam o léxico (o que é "simples") e a regra de escalada por falha —
# as duas coisas que mais mudam de comportamento quando alguém mexe no prompt.

"""Testes do roteamento local→nuvem do modo agente."""

from __future__ import annotations

from zorin_copilot.ai.agent import AgentDecision, ToolCall
from zorin_copilot.ai.agent_router import (
    AgentRouter,
    FallbackPlanner,
    LLMPlanner,
    RouteMode,
    build_planner_prompt,
    classify_objective,
    decision_from_json,
    extract_json_object,
)


class StubPlanner:
    def __init__(self, name, decision=None, error=""):
        self.name = name
        self._decision = decision
        self._error = error

    def decide(self, objective, tools, history):
        if self._error:
            return AgentDecision(error=self._error)
        return self._decision or AgentDecision(final_answer=f"ok-{self.name}")


class StubProvider:
    """Imita `BaseLLMProvider.chat` devolvendo um texto fixo."""

    def __init__(self, text):
        self.text = text
        self.prompts: list[str] = []

    def chat(self, prompt, *_args, **_kwargs):
        self.prompts.append(prompt)
        return self.text, []


# --------------------------------------------------------------------------- #
# Classificação
# --------------------------------------------------------------------------- #


def test_objetivo_direto_e_simples():
    kind, reason = classify_objective("abrir o Firefox")
    assert kind == "simple"
    assert "abrir" in reason


def test_cadeia_de_passos_e_complexa():
    kind, _ = classify_objective("abrir o Firefox e depois pesquisar contabilidade gerencial")
    assert kind == "complex"


def test_pedido_de_sintese_e_complexo():
    kind, reason = classify_objective("resumir o capítulo 3 de contabilidade")
    assert kind == "complex"
    assert "síntese" in reason


def test_nome_de_pasta_com_estudos_nao_escala_para_a_nuvem():
    """'pasta de estudos' é tarefa local; só 'estudar' (infinitivo) é síntese."""
    assert classify_objective("abrir a pasta de estudos")[0] == "simple"
    assert classify_objective("estudar contabilidade gerencial")[0] == "complex"


def test_muitos_verbos_tornam_o_objetivo_composto():
    kind, _ = classify_objective("abrir lista clicar digita")
    assert kind == "complex"


def test_sem_verbo_conhecido_vai_para_nuvem():
    kind, reason = classify_objective("me surpreenda")
    assert kind == "complex"
    assert "nenhum verbo" in reason


# --------------------------------------------------------------------------- #
# Parsing da resposta do modelo
# --------------------------------------------------------------------------- #


def test_extrai_json_cercado_por_markdown():
    payload = extract_json_object('```json\n{"tool": "get_ui_tree", "args": {}}\n```')
    assert payload == {"tool": "get_ui_tree", "args": {}}


def test_extrai_json_no_meio_de_prosa():
    payload = extract_json_object('Claro! {"tool": "done", "args": {"answer": "feito"}} Espero que ajude.')
    assert payload["tool"] == "done"


def test_extrai_json_com_chaves_dentro_de_string():
    payload = extract_json_object('{"tool": "write_document", "args": {"content": "a {b} c"}}')
    assert payload["args"]["content"] == "a {b} c"


def test_texto_sem_json_devolve_none():
    assert extract_json_object("não entendi o que você quer") is None


def test_decisao_aceita_variacoes_de_chave():
    decision = decision_from_json({"name": "get_ui_tree", "arguments": {"app": "Firefox"}})
    assert decision.tool_call is not None
    assert decision.tool_call.name == "get_ui_tree"
    assert decision.tool_call.args == {"app": "Firefox"}


def test_decisao_final_sem_ferramenta():
    decision = decision_from_json({"final_answer": "pronto"})
    assert decision.tool_call is None
    assert decision.final_answer == "pronto"


def test_decisao_vazia_vira_erro():
    assert decision_from_json({}).error


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #


def test_prompt_inclui_objetivo_ferramentas_e_historico():
    tools = [{"name": "get_ui_tree", "description": "lê a tela", "parameters": {"properties": {"app": {}}, "required": []}}]
    history = [{"index": 0, "tool": "get_ui_tree", "args": {}, "ok": True, "observation": {"ok": True}}]
    prompt = build_planner_prompt("abrir o Firefox", tools, history)

    assert "abrir o Firefox" in prompt
    assert "get_ui_tree(app)" in prompt
    assert "1. get_ui_tree" in prompt


def test_llm_planner_converte_resposta_em_tool_call():
    provider = StubProvider('{"tool": "launch_app", "args": {"query": "firefox"}}')
    planner = LLMPlanner(provider, name="local")
    decision = planner.decide("abrir o Firefox", [], [])

    assert decision.tool_call is not None
    assert decision.tool_call.name == "launch_app"
    assert len(provider.prompts) == 1


def test_llm_planner_reporta_erro_quando_o_provedor_falha():
    class Boom:
        def chat(self, *_a, **_k):
            raise RuntimeError("sem rede")

    decision = LLMPlanner(Boom(), name="cloud").decide("x", [], [])
    assert decision.error
    assert "sem rede" in decision.error


def test_llm_planner_reporta_erro_quando_a_resposta_nao_e_json():
    decision = LLMPlanner(StubProvider("desculpe, não entendi"), name="local").decide("x", [], [])
    assert decision.error


# --------------------------------------------------------------------------- #
# Fallback
# --------------------------------------------------------------------------- #


def test_fallback_usa_o_primeiro_que_responde():
    planner = FallbackPlanner(
        [StubPlanner("local", error="timeout"), StubPlanner("cloud")]
    )
    decision = planner.decide("x", [], [])
    assert decision.error == ""
    assert planner.used == "cloud"


def test_fallback_sem_ninguem_disponivel_devolve_erro():
    planner = FallbackPlanner([])
    assert planner.decide("x", [], []).error


# --------------------------------------------------------------------------- #
# Roteador
# --------------------------------------------------------------------------- #


def test_local_only_escolhe_o_modelo_local():
    local = StubPlanner("local")
    router = AgentRouter(local_planner=local, cloud_planner=StubPlanner("cloud"))
    route = router.route("abrir o Firefox", RouteMode.LOCAL)
    assert route.planner is local
    assert route.mode == "local"


def test_cloud_escolhe_a_nuvem():
    cloud = StubPlanner("cloud")
    router = AgentRouter(local_planner=StubPlanner("local"), cloud_planner=cloud)
    route = router.route("resumir o capítulo", RouteMode.CLOUD)
    assert route.planner is cloud
    assert route.mode == "cloud"


def test_auto_manda_tarefa_simples_para_o_local():
    router = AgentRouter(local_planner=StubPlanner("local"), cloud_planner=StubPlanner("cloud"))
    route = router.route("abrir o Firefox")
    assert route.mode == "local"
    assert route.complexity == "simple"


def test_auto_manda_cadeia_de_passos_para_a_nuvem():
    router = AgentRouter(local_planner=StubPlanner("local"), cloud_planner=StubPlanner("cloud"))
    route = router.route("abrir o navegador e depois pesquisar contabilidade")
    assert route.mode == "cloud"
    assert route.complexity == "complex"


def test_local_only_sem_modelo_local_explica_o_motivo():
    router = AgentRouter(local_planner=None, cloud_planner=StubPlanner("cloud"))
    route = router.route("abrir o Firefox", RouteMode.LOCAL)
    assert route.planner is None
    assert route.has_planner is False
    assert "indisponível" in route.reason


def test_sem_nuvem_configurada_tarefa_complexa_cai_no_local():
    local = StubPlanner("local")
    router = AgentRouter(local_planner=local, cloud_planner=None)
    route = router.route("resumir o capítulo 3 e depois montar um relatório")
    assert route.planner is local
    assert "nuvem não configurada" in route.reason


def test_router_sem_config_e_sem_planners_nao_quebra():
    route = AgentRouter().route("abrir o Firefox")
    assert route.planner is None
    assert route.has_planner is False


def test_roteamento_aceita_string_como_modo():
    router = AgentRouter(local_planner=StubPlanner("local"), cloud_planner=StubPlanner("cloud"))
    assert router.route("abrir o Firefox", "local").mode == "local"


def test_fallback_recebe_os_dois_caminhos_em_auto():
    local = StubPlanner("local")
    cloud = StubPlanner("cloud")
    route = AgentRouter(local_planner=local, cloud_planner=cloud).route("abrir o Firefox")
    assert isinstance(route.planner, FallbackPlanner)
    assert route.planner.planners == [local, cloud]


def test_decision_from_json_converte_actions_hud():
    payload = {
        "explanation": "Elaborando capítulo do TCC",
        "actions": [
            {
                "type": "write_document",
                "target": "tcc_senac.docx",
                "params": {"content": "# Introdução", "directory": "~/Documentos"},
                "description": "Criar documento do TCC",
            }
        ],
    }
    decision = decision_from_json(payload)
    assert decision.tool_call is not None
    assert decision.tool_call.name == "write_document"
    assert decision.tool_call.args["filename"] == "tcc_senac.docx"
    assert decision.tool_call.args["content"] == "# Introdução"


def test_decision_from_json_converte_open_document():
    payload = {
        "actions": [
            {
                "type": "open_document",
                "target": "/tmp/relatorio.docx",
                "params": {},
            }
        ]
    }
    decision = decision_from_json(payload)
    assert decision.tool_call is not None
    assert decision.tool_call.name == "open_document"
    assert decision.tool_call.args["path"] == "/tmp/relatorio.docx"


def test_route_prioriza_gemini_quando_configurado():
    from zorin_copilot.core.config import CopilotConfig

    config = CopilotConfig(provider="gemini", fallback_to_ollama=True)
    local = StubPlanner("local")
    cloud = StubPlanner("cloud")
    router = AgentRouter(config=config, local_planner=local, cloud_planner=cloud)

    # Mesmo tarefa simples vai para cloud primeiro com fallback local quando provider=gemini
    route = router.route("abrir o Firefox")
    assert route.mode == "cloud"
    assert isinstance(route.planner, FallbackPlanner)
    assert route.planner.planners == [cloud, local]


def test_route_prioriza_gemini_sem_fallback():
    from zorin_copilot.core.config import CopilotConfig

    config = CopilotConfig(provider="gemini", fallback_to_ollama=False)
    local = StubPlanner("local")
    cloud = StubPlanner("cloud")
    router = AgentRouter(config=config, local_planner=local, cloud_planner=cloud)

    route = router.route("abrir o Firefox")
    assert route.mode == "cloud"
    assert route.planner is cloud
