# Decisão de design: estes testes cobrem o CONTRATO do subagente — isolamento de
# contexto, restrição de ferramentas, orçamento, encadeamento e progresso — não a
# qualidade do raciocínio dele. O subagente é um `AgentLoop` comum, então os freios
# do laço já têm teste próprio em `test_agent_loop.py`; aqui se verifica o que o
# pipeline faz por cima deles.
#
# Tudo roda sem display, sem rede e sem modelo: planejadores e registros são dublês,
# como manda o cabeçalho de `test_agent_loop.py`.

"""Testes dos subagentes especializados (contexto isolado, ferramentas restritas)."""

from __future__ import annotations

import threading
import time

import pytest

from zorin_copilot.ai.agent import (
    STOP_ABORTED,
    STOP_DONE,
    STOP_ERROR,
    STOP_LOOP,
    STOP_MAX_STEPS,
    STOP_NO_PROVIDER,
    STOP_REJECTED,
    STOP_REPEATED,
    STOP_TIMEOUT,
    AgentDecision,
    ToolCall,
)
from zorin_copilot.ai.agent_tools import FINISH_TOOLS, ToolRegistry
from zorin_copilot.ai.subagents import (
    CATALOG,
    PHASE_END,
    PHASE_START,
    PIPELINE_ABORTED,
    PIPELINE_DONE,
    PIPELINE_NO_PLANNER,
    PIPELINE_NO_STAGES,
    PIPELINE_STAGE_FAILED,
    PIPELINE_TIMEOUT,
    SEVERITY_ERROR,
    SEVERITY_LABELS,
    SEVERITY_SUCCESS,
    SEVERITY_WARNING,
    SubagentPipeline,
    SubagentRun,
    SubagentSpec,
    build_pipeline,
    catalog_names,
    chain_input,
    get_spec,
    matches_allowlist,
    resolve_allow,
    restrict_registry,
    stage_label,
    stage_severity,
)


# --------------------------------------------------------------------------- #
# Dublês
# --------------------------------------------------------------------------- #


class StubRegistry(ToolRegistry):
    """Registro com respostas programadas; conta chamadas."""

    def __init__(self, results: dict | None = None, default: dict | None = None) -> None:
        super().__init__()
        self.results = results or {}
        self.default = default or {"ok": True, "message": "feito"}
        self.calls: list[tuple[str, dict]] = []

    def call(self, name, args=None, contract=None) -> dict:
        self.calls.append((name, dict(args or {})))
        if name in self.results:
            return dict(self.results[name])
        return dict(self.default)


class SleepyRegistry(StubRegistry):
    """Registro lento — para exercitar orçamento de tempo."""

    def __init__(self, delay: float) -> None:
        super().__init__()
        self.delay = delay

    def call(self, name, args=None, contract=None) -> dict:
        time.sleep(self.delay)
        return super().call(name, args, contract=contract)


class RecordingPlanner:
    """Planejador que registra o que recebeu e responde por objetivo."""

    def __init__(self, responder=None, name: str = "recording") -> None:
        self.name = name
        self.responder = responder or (
            lambda objective: AgentDecision(final_answer=f"relatório de {objective[:24]}")
        )
        self.seen: list[dict] = []

    def decide(self, objective, tools, history, session_context=None):
        self.seen.append(
            {
                "objective": objective,
                "tools": [t["name"] for t in tools],
                "history": list(history),
                "session_context": list(session_context or []),
                "thread": threading.get_ident(),
            }
        )
        return self.responder(objective)


class VaryingPlanner:
    """Nunca encerra sozinho: pede uma ferramenta diferente a cada passo.

    Varia os argumentos de propósito — um roteiro constante pararia por
    `loop_detected` (4 chamadas idênticas) antes de exercitar o orçamento, e o
    teste passaria a medir outra coisa sem avisar.
    """

    name = "varying"

    def __init__(self) -> None:
        self.calls = 0

    def decide(self, objective, tools, history, session_context=None):
        self.calls += 1
        return AgentDecision(tool_call=ToolCall("list_directory", {"path": f"/tmp/alvo-{self.calls}"}))


class OneToolThenFinishPlanner:
    """Uma ferramenta e encerra. Consome tempo de verdade no estágio."""

    name = "one-then-finish"

    def decide(self, objective, tools, history, session_context=None):
        if history:
            return AgentDecision(final_answer=f"concluído: {objective[:40]}")
        return AgentDecision(tool_call=ToolCall("list_directory", {"path": "/tmp/alvo"}))


class ByObjectivePlanner:
    """Comportamento por objetivo — permite um estágio bom e outro ruim no mesmo run.

    Um planejador só para os dois estágios mediria a coisa errada: os dois
    falhariam, e o teste "o estágio 1 passou e o 2 falhou" passaria a não
    distinguir nada.
    """

    name = "by-objective"

    def __init__(self, mapa, padrao=None) -> None:
        self.mapa = mapa
        self.padrao = padrao or (lambda objective: AgentDecision(final_answer="ok"))
        self.seen: list[str] = []

    def decide(self, objective, tools, history, session_context=None):
        self.seen.append(objective)
        for chave, responder in self.mapa.items():
            if chave in objective:
                return responder(objective, tools, history)
        return self.padrao(objective)


def spec(name: str = "teste", **kwargs) -> SubagentSpec:
    base = dict(
        title="Especialista de teste",
        objective_template="Faça: {input}",
        allow=("list_directory", "read_file"),
        max_steps=4,
        max_seconds=30.0,
    )
    base.update(kwargs)
    return SubagentSpec(name=name, **base)


# --------------------------------------------------------------------------- #
# Allowlist e registro restrito
# --------------------------------------------------------------------------- #


def test_padrao_com_asterisco_casa_por_prefixo():
    assert matches_allowlist("mcp__github__create_issue", ("mcp__github__*",))
    assert matches_allowlist("mcp__github__create_issue", ("mcp__*",))


def test_padrao_sem_asterisco_e_nome_exato():
    assert matches_allowlist("read_file", ("read_file",))
    assert not matches_allowlist("read_file", ("read",))
    assert not matches_allowlist("read_file_extra", ("read_file",))


def test_padrao_vazio_ou_nome_vazio_nao_casa():
    assert not matches_allowlist("", ("read_file",))
    assert not matches_allowlist("read_file", ("", "   "))


def test_registro_restrito_nao_muta_o_original():
    """A armadilha do `copy.copy`: ele é rasa e compartilha o MESMO `_specs`.

    Filtrar no dicionário herdado apagaria ferramentas do registro original — e o
    dano apareceria longe daqui, no agente principal, que perderia ferramentas
    depois de um subagente rodar.
    """
    original = ToolRegistry()
    antes = sorted(original.names())
    assert "read_file" in antes and "run_command" in antes

    restrito = original.restricted(["read_file"])

    assert sorted(original.names()) == antes, "o registro original foi mutado"
    assert sorted(restrito.names()) == ["read_file"]


def test_registro_restrito_compartilha_handlers():
    """O clone executa de verdade — não é um registro só de nomes."""
    original = ToolRegistry()
    restrito = original.restricted(["list_directory"])
    assert restrito.spec("list_directory") is original.spec("list_directory")


def test_restrict_registry_nao_muta_o_registro_do_pai():
    """A função que o pipeline usa de verdade, e não só o método do registro.

    Sem isto a suíte ficava verde com um `restrict_registry` que apagava as
    ferramentas do registro original — o subagente rodava certo e o agente
    principal ficava sem ferramentas, longe daqui. Descoberto por mutação.
    """
    original = ToolRegistry()
    antes = sorted(original.names())

    restrito = restrict_registry(original, ("list_directory",))

    assert sorted(original.names()) == antes, "o registro do pai foi mutado"
    assert sorted(restrito.names()) == ["done", "list_directory"]


def test_restricao_chega_ao_schema_que_o_modelo_recebe():
    """Restrição de schema, não checagem posterior: o modelo não vê o proibido."""
    restrito = restrict_registry(ToolRegistry(), ("list_directory",))
    nomes = [t["name"] for t in restrito.schema()]
    assert "list_directory" in nomes
    assert "run_command" not in nomes
    assert "mouse_click" not in nomes


def test_resolve_allow_sempre_inclui_encerramento():
    """Sem `done` no schema o subagente não sabe que pode terminar."""
    resolvido = resolve_allow(ToolRegistry(), ("list_directory",))
    for finisher in FINISH_TOOLS:
        if finisher in ToolRegistry().names():
            assert finisher in resolvido


def test_resolve_allow_nao_duplica_encerramento_declarado():
    resolvido = resolve_allow(ToolRegistry(), ("list_directory", "done"))
    assert resolvido.count("done") == 1


def test_ferramenta_proibida_nao_e_executavel_no_registro_restrito():
    """Mesmo que o modelo alucine o nome, o registro restrito recusa."""
    restrito = restrict_registry(ToolRegistry(), ("list_directory",))
    resultado = restrito.call("run_command", {"command": "rm -rf /"})
    assert resultado["ok"] is False
    assert "desconhecida" in resultado["error"].lower()


# --------------------------------------------------------------------------- #
# Catálogo — o que está escrito tem de existir
# --------------------------------------------------------------------------- #


def test_todo_padrao_do_catalogo_acha_ferramenta_de_verdade():
    """Pega nome de ferramenta inventado: um typo sumiria em silêncio do schema."""
    registro = ToolRegistry()
    disponiveis = registro.names()
    for nome, esp in CATALOG.items():
        for padrao in esp.allow:
            assert any(matches_allowlist(n, (padrao,)) for n in disponiveis), (
                f"o especialista '{nome}' autoriza '{padrao}', que não existe no registro"
            )


def test_nenhum_especialista_pode_abrir_outro_subagente():
    """A recursão é impedida por construção, não por checagem em tempo de execução."""
    registro = ToolRegistry()
    assert "run_subagent" in registro.names(), "a ferramenta precisa existir para o teste valer"
    for nome, esp in CATALOG.items():
        assert "run_subagent" not in resolve_allow(registro, esp.allow), (
            f"o especialista '{nome}' pode abrir outro subagente"
        )


def test_todo_especialista_consegue_encerrar():
    registro = ToolRegistry()
    for nome, esp in CATALOG.items():
        assert "done" in resolve_allow(registro, esp.allow), (
            f"o especialista '{nome}' não tem como concluir"
        )


def test_template_de_todo_especialista_formata_com_a_entrada():
    for nome, esp in CATALOG.items():
        texto = esp.build_objective("ALVO-ÚNICO")
        assert "ALVO-ÚNICO" in texto, f"o template de '{nome}' perdeu a entrada"
        assert "{input}" not in texto, f"o template de '{nome}' não substituiu {{input}}"


def test_template_quebrado_nao_derruba_a_execucao():
    """Chave desconhecida ou `{` solto: anexa e segue, em vez de levantar."""
    quebrado = spec(objective_template="Analise {nao_existe} isto")
    assert "Analise {nao_existe} isto" in quebrado.build_objective("ALVO")


def test_descricao_da_ferramenta_cita_todo_especialista():
    """O modelo só delega se souber os nomes — defasagem aqui é falha silenciosa."""
    registro = ToolRegistry()
    descricao = registro.spec("run_subagent").description
    for nome in catalog_names():
        assert nome in descricao, f"a descrição de `run_subagent` não cita '{nome}'"


def test_get_spec_aceita_caixa_e_espaco():
    assert get_spec("  RESEARCHER ") is CATALOG["researcher"]
    assert get_spec("inexistente") is None


# --------------------------------------------------------------------------- #
# Contexto isolado
# --------------------------------------------------------------------------- #


def test_subagente_nao_recebe_o_historico_do_pai():
    planner = RecordingPlanner()
    pipeline = SubagentPipeline([spec()], StubRegistry(), planner)
    pipeline.run("tarefa")

    assert planner.seen, "o planejador nunca foi consultado"
    primeira = planner.seen[0]
    assert primeira["session_context"] == []
    assert primeira["history"] == []


def test_subagente_so_enxerga_as_ferramentas_autorizadas():
    planner = RecordingPlanner()
    pipeline = SubagentPipeline(
        [spec(allow=("list_directory",))], StubRegistry(), planner
    )
    pipeline.run("tarefa")

    ferramentas = set(planner.seen[0]["tools"])
    assert "list_directory" in ferramentas
    assert "done" in ferramentas
    assert "run_command" not in ferramentas


def test_contexto_de_um_estagio_nao_vaza_para_o_proximo():
    """Cada estágio tem histórico próprio: o segundo não vê os passos do primeiro."""
    planner = RecordingPlanner()
    pipeline = SubagentPipeline([spec("a"), spec("b")], StubRegistry(), planner)
    pipeline.run("tarefa")

    # Cada estágio consulta o planejador pelo menos uma vez, e cada consulta
    # começa com histórico vazio (o estágio anterior não é replantado nele).
    assert len(planner.seen) >= 2
    for consulta in planner.seen:
        assert consulta["history"] == []


# --------------------------------------------------------------------------- #
# Orçamento
# --------------------------------------------------------------------------- #


def test_orcamento_de_passos_vem_do_especialista():
    pipeline = SubagentPipeline(
        [spec(max_steps=3, max_seconds=60.0)], StubRegistry(), VaryingPlanner()
    )
    resultado = pipeline.run("tarefa")

    estagio = resultado.runs[0]
    assert len(estagio.steps) == 3
    assert estagio.stop_reason == STOP_MAX_STEPS
    assert estagio.ok is False


def test_orcamento_de_tempo_do_estagio_e_limitado_pelo_teto_total():
    """Com teto total de 1 s, um estágio de 120 s para em ~1 s, não em 120 s.

    Sem o corte, este teste levaria dois minutos — o próprio tempo de execução é
    a asserção.
    """
    largo = spec(max_steps=200, max_seconds=120.0)
    pipeline = SubagentPipeline(
        [largo],
        SleepyRegistry(delay=0.05),
        VaryingPlanner(),
        max_total_seconds=1.0,
    )
    inicio = time.monotonic()
    resultado = pipeline.run("tarefa")
    decorrido = time.monotonic() - inicio

    assert resultado.runs[0].stop_reason == STOP_TIMEOUT
    assert decorrido < 10.0, "o teto total não foi aplicado ao estágio"


def test_teto_total_esgotado_impede_ate_o_primeiro_estagio():
    """Sem orçamento total, o pipeline recusa começar — em vez de ganhar tempo de graça.

    Um teto de 0 costumava virar 1 s por causa de um `max(1.0, ...)` defensivo, e
    o pipeline rodava normalmente. Zero significa zero.

    Nota deliberada: o caso "o estágio 1 concluiu e o teto acabou antes do 2" não
    tem teste determinístico possível. O orçamento de um estágio é limitado pelo
    que sobrou do total, e o `AgentLoop` confere o orçamento no início de cada
    passo — então um estágio que **conclui** sempre termina dentro do total, e a
    janela em que ele ultrapassa é a duração de um passo lento. Um teste disso
    seria uma corrida, não uma verificação. O que dá para afirmar sem corrida é
    esta regra: com o total esgotado, nenhum estágio começa.
    """
    resultado = SubagentPipeline(
        [spec("a"), spec("b")], StubRegistry(), RecordingPlanner(), max_total_seconds=0
    ).run("tarefa")

    assert resultado.status == PIPELINE_TIMEOUT
    assert resultado.stage_count == 0


# --------------------------------------------------------------------------- #
# Encadeamento
# --------------------------------------------------------------------------- #


def test_saida_de_um_estagio_e_a_entrada_do_proximo():
    """O requisito central do passo: a saída vira a entrada, com estado explícito."""
    respostas = {
        "Pesquisar": AgentDecision(final_answer="ACHADO-DA-PESQUISA"),
    }
    planner = RecordingPlanner(
        responder=lambda objective: respostas.get(
            objective.split(":")[0], AgentDecision(final_answer=f"eco de {objective}")
        )
    )
    pipeline = SubagentPipeline(
        [
            spec("pesquisa", objective_template="Pesquisar: {input}"),
            spec("revisao", objective_template="Revisar: {input}"),
        ],
        StubRegistry(),
        planner,
    )
    resultado = pipeline.run("tema inicial")

    assert resultado.status == PIPELINE_DONE
    assert resultado.stage_count == 2
    assert resultado.runs[0].output == "ACHADO-DA-PESQUISA"
    assert resultado.runs[0].input == "tema inicial"

    # O segundo estágio recebeu a SAÍDA do primeiro, não a entrada original.
    assert resultado.runs[1].input == "ACHADO-DA-PESQUISA"
    assert "ACHADO-DA-PESQUISA" in resultado.runs[1].objective
    assert "tema inicial" not in resultado.runs[1].objective

    # A saída final do pipeline é a do último estágio.
    assert resultado.output == resultado.runs[1].output


def test_entrada_anterior_segue_quando_o_estagio_nao_produz_texto():
    """Estágio mudo não deixa o próximo às cegas.

    A regra é testada na função pura porque, no caminho real, o `AgentLoop`
    substitui uma resposta vazia por "Objetivo concluído." — ou seja, chegar aqui
    com saída vazia depende de um planejador futuro, não do de hoje. Testar a
    regra por um caminho que não a exercita daria um teste verde que não verifica
    nada.
    """
    assert chain_input("entrada-original", "") == "entrada-original"
    assert chain_input("entrada-original", "   ") == "entrada-original"
    assert chain_input("", "") == ""
    assert chain_input("entrada-original", "saída nova") == "saída nova"


def test_estagio_mudo_ainda_produz_texto_padrao_para_o_proximo():
    """Comportamento observado: o laço não deixa o estágio terminar sem resposta."""
    planner = RecordingPlanner(responder=lambda objective: AgentDecision(final_answer=""))
    resultado = SubagentPipeline(
        [spec("a"), spec("b", objective_template="Revisar: {input}")],
        StubRegistry(),
        planner,
    ).run("entrada-original")

    assert resultado.status == PIPELINE_DONE
    assert resultado.runs[0].output
    assert resultado.runs[1].input == resultado.runs[0].output


# --------------------------------------------------------------------------- #
# Parada
# --------------------------------------------------------------------------- #


def test_pipeline_sem_estagios_termina_com_motivo_explicito():
    resultado = SubagentPipeline([], StubRegistry(), RecordingPlanner()).run("x")
    assert resultado.status == PIPELINE_NO_STAGES
    assert resultado.ok is False
    assert resultado.error


def test_sem_planejador_termina_com_motivo_explicito():
    resultado = SubagentPipeline([spec()], StubRegistry(), None).run("x")
    assert resultado.status == PIPELINE_NO_PLANNER
    assert resultado.stage_count == 0


def test_estagio_que_falha_para_o_pipeline_e_nomeia_o_estagio():
    planner = ByObjectivePlanner(
        {"Bom:": lambda o, t, h: AgentDecision(final_answer="feito"),
         "Ruim:": lambda o, t, h: VaryingPlanner().decide(o, t, h)},
    )
    resultado = SubagentPipeline(
        [
            spec("bom", objective_template="Bom: {input}", max_steps=4),
            spec("ruim", objective_template="Ruim: {input}", max_steps=2, max_seconds=60.0),
        ],
        StubRegistry(),
        planner,
    ).run("tarefa")

    assert resultado.status == PIPELINE_STAGE_FAILED
    assert resultado.stage_count == 2
    assert resultado.runs[0].ok is True
    assert resultado.runs[1].ok is False
    assert "ruim" in resultado.error


def test_estagio_que_falha_nao_impede_o_relato_do_que_ja_foi_feito():
    """O pipeline para no estágio ruim, mas o que já foi produzido vem no resultado."""
    planner = ByObjectivePlanner(
        {"Bom:": lambda o, t, h: AgentDecision(final_answer="relatório do estágio bom"),
         "Ruim:": lambda o, t, h: VaryingPlanner().decide(o, t, h)},
    )
    resultado = SubagentPipeline(
        [
            spec("bom", objective_template="Bom: {input}"),
            spec("ruim", objective_template="Ruim: {input}", max_steps=2),
        ],
        StubRegistry(),
        planner,
    ).run("tarefa")

    assert len(resultado.runs) == 2
    assert resultado.runs[0].output == "relatório do estágio bom"


def test_aborto_para_antes_do_proximo_estagio():
    planner = RecordingPlanner()
    pipeline = SubagentPipeline([spec("a"), spec("b")], StubRegistry(), planner)
    pipeline.abort()
    resultado = pipeline.run("tarefa")

    assert resultado.status == PIPELINE_ABORTED
    assert resultado.stage_count == 0
    assert planner.seen == []


def test_run_nao_limpa_um_aborto_pedido_de_proposito():
    """Limpar sozinho faria o botão de parada parecer não funcionar.

    O `AgentLoop` tem a mesma convenção: quem quer reusar chama `reset()`.
    """
    pipeline = SubagentPipeline([spec("a")], StubRegistry(), RecordingPlanner())
    pipeline.abort()
    assert pipeline.run("primeira").status == PIPELINE_ABORTED
    assert pipeline.run("segunda").status == PIPELINE_ABORTED

    pipeline.reset()
    assert pipeline.run("terceira").status == PIPELINE_DONE


# --------------------------------------------------------------------------- #
# Progresso
# --------------------------------------------------------------------------- #


def test_on_stage_notifica_inicio_e_fim_com_posicao():
    estados = []
    pipeline = SubagentPipeline(
        [spec("a"), spec("b")], StubRegistry(), RecordingPlanner(), on_stage=estados.append
    )
    pipeline.run("tarefa")

    assert [e.phase for e in estados] == [PHASE_START, PHASE_END, PHASE_START, PHASE_END]
    assert [e.position for e in estados] == ["1/2", "1/2", "2/2", "2/2"]
    assert [e.name for e in estados] == ["a", "a", "b", "b"]

    # No fim do estágio o run vem preenchido; no início, não.
    assert estados[0].run is None
    assert estados[1].run is not None and estados[1].run.ok is True
    assert estados[1].runs[0].name == "a"


def test_on_stage_roda_na_thread_de_quem_chamou():
    """A UI depende disso para poder fazer o marshal com `GLib.idle_add`.

    Se a notificação viesse de outra thread, mexer em widget aqui seria corrupção
    silenciosa de estado do GTK em vez de erro visível.
    """
    estados = []
    pipeline = SubagentPipeline(
        [spec()], StubRegistry(), RecordingPlanner(), on_stage=estados.append
    )
    pipeline.run("tarefa")
    assert estados
    assert all(e.phase in (PHASE_START, PHASE_END) for e in estados)
    assert threading.get_ident() == threading.main_thread().ident


def test_observador_quebrado_nao_derruba_o_pipeline():
    def explode(_state):
        raise RuntimeError("observador com defeito")

    resultado = SubagentPipeline(
        [spec()], StubRegistry(), RecordingPlanner(), on_stage=explode
    ).run("tarefa")
    assert resultado.status == PIPELINE_DONE


def test_on_step_recebe_o_especialista_e_o_passo():
    vistos = []
    pipeline = SubagentPipeline(
        [spec("a")], StubRegistry(), VaryingPlanner(), on_step=lambda s, step: vistos.append((s.name, step.tool))
    )
    pipeline.run("tarefa")
    assert vistos
    assert all(nome == "a" for nome, _ in vistos)


def test_on_step_quebrado_nao_derruba_o_pipeline():
    def explode(_spec, _step):
        raise RuntimeError("observador com defeito")

    resultado = SubagentPipeline(
        [spec()], StubRegistry(), RecordingPlanner(), on_step=explode
    ).run("tarefa")
    assert resultado.status == PIPELINE_DONE


def test_aprovacao_do_pai_e_repassada_ao_subagente():
    """Ação sensível dentro de um subagente continua pedindo aprovação ao usuário."""
    pedidos = []

    def aprovador(step):
        pedidos.append(step.tool)
        return True

    # `write_document` é CONFIRM no `shell/risk.py` — precisa de aprovação.
    planner = RecordingPlanner(
        responder=lambda objective: AgentDecision(tool_call=ToolCall("write_document", {"content": "x"}))
    )
    registro = StubRegistry()
    resultado = SubagentPipeline(
        [spec("a", allow=("write_document",), max_steps=1)],
        registro,
        planner,
        on_approval=aprovador,
    ).run("tarefa")

    assert pedidos == ["write_document"]
    assert resultado.runs[0].steps[0].approved is True


def test_sem_aprovador_acao_sensivel_e_recusada_no_subagente():
    planner = RecordingPlanner(
        responder=lambda objective: AgentDecision(tool_call=ToolCall("write_document", {"content": "x"}))
    )
    resultado = SubagentPipeline(
        [spec("a", allow=("write_document",), max_steps=1)], StubRegistry(), planner
    ).run("tarefa")

    assert resultado.runs[0].ok is False
    assert resultado.runs[0].steps[0].approved is None


# --------------------------------------------------------------------------- #
# Ferramenta `run_subagent`
# --------------------------------------------------------------------------- #


def test_run_subagent_sem_gancho_nao_quebra():
    """CLI headless e testes não têm pipeline: mensagem graciosa, não exceção."""
    registro = ToolRegistry()
    resultado = registro.call("run_subagent", {"specialist": "researcher", "input": "algo"})
    assert resultado["ok"] is False
    assert "não estão disponíveis" in resultado["error"]


def test_run_subagent_valida_especialista():
    registro = ToolRegistry()
    resultado = registro.call("run_subagent", {"specialist": "inexistente", "input": "x"})
    assert resultado["ok"] is False
    assert "inexistente" in resultado["error"]
    # A mensagem precisa dizer o que existe, senão o modelo não tem como corrigir.
    for nome in catalog_names():
        assert nome in resultado["error"]


def test_run_subagent_exige_entrada():
    registro = ToolRegistry()
    resultado = registro.call("run_subagent", {"specialist": "researcher"})
    assert resultado["ok"] is False
    assert "input" in resultado["error"]


def test_run_subagent_repassa_ao_gancho():
    recebido = {}

    def runner(especialista, entrada):
        recebido["especialista"] = especialista
        recebido["entrada"] = entrada
        return {"ok": True, "report": "relatório pronto"}

    registro = ToolRegistry(subagent_runner=runner)
    resultado = registro.call("run_subagent", {"specialist": "Refactorer", "input": "limpe isto"})

    assert recebido == {"especialista": "refactorer", "entrada": "limpe isto"}
    assert resultado["ok"] is True
    assert resultado["specialist"] == "refactorer"
    assert resultado["report"] == "relatório pronto"


def test_run_subagent_nao_tem_o_teto_de_tempo_do_registro():
    """Com o teto padrão de 25 s o pipeline morreria no meio do primeiro estágio."""
    spec_tool = ToolRegistry().spec("run_subagent")
    assert spec_tool.timeout == 0
    assert spec_tool.mutating is False


def test_run_subagent_nao_derruba_o_loop_se_o_gancho_explodir():
    def runner(_especialista, _entrada):
        raise RuntimeError("gancho quebrado")

    registro = ToolRegistry(subagent_runner=runner)
    resultado = registro.call("run_subagent", {"specialist": "researcher", "input": "x"})
    assert resultado["ok"] is False
    assert "gancho quebrado" in resultado["error"]


# --------------------------------------------------------------------------- #
# Montagem
# --------------------------------------------------------------------------- #


def test_build_pipeline_monta_na_ordem_pedida():
    pipeline = build_pipeline(["refactorer", "researcher"], StubRegistry(), RecordingPlanner())
    assert [s.name for s in pipeline.stages] == ["refactorer", "researcher"]


def test_build_pipeline_falha_cedo_com_nome_desconhecido():
    """Melhor falhar na montagem que descobrir no meio da execução."""
    with pytest.raises(ValueError) as exc:
        build_pipeline(["researcher", "inexistente"], StubRegistry(), RecordingPlanner())
    assert "inexistente" in str(exc.value)


def test_planner_for_permite_um_modelo_por_especialista():
    """Cada estágio pode usar um planejador diferente (local vs nuvem)."""
    barato = RecordingPlanner(name="barato")
    caro = RecordingPlanner(name="caro")
    escolhidos = {"researcher": caro, "refactorer": barato}

    pipeline = SubagentPipeline(
        [spec("researcher"), spec("refactorer")],
        StubRegistry(),
        planner=None,
        planner_for=lambda s: escolhidos[s.name],
    )
    resultado = pipeline.run("tarefa")

    assert resultado.status == PIPELINE_DONE
    assert len(caro.seen) >= 1
    assert len(barato.seen) >= 1


def test_planner_for_que_falha_vira_motivo_de_parada():
    def explode(_spec):
        raise RuntimeError("roteador quebrado")

    resultado = SubagentPipeline(
        [spec()], StubRegistry(), None, planner_for=explode
    ).run("tarefa")
    assert resultado.status == PIPELINE_NO_PLANNER


def test_resultado_e_serializavel():
    """O relatório vai para o chat e para o log — precisa virar JSON."""
    import json

    resultado = SubagentPipeline([spec()], StubRegistry(), RecordingPlanner()).run("tarefa")
    payload = json.dumps(resultado.to_dict(), ensure_ascii=False)
    assert "runs" in payload
    assert resultado.to_dict()["stage_count"] == 1


def test_estagio_bem_sucedido_termina_com_done():
    resultado = SubagentPipeline([spec()], StubRegistry(), RecordingPlanner()).run("tarefa")
    assert resultado.runs[0].stop_reason == STOP_DONE
    assert resultado.runs[0].ok is True


# --------------------------------------------------------------------------- #
# Gravidade do estágio (o que o ícone e o rótulo do card mostram)
# --------------------------------------------------------------------------- #
#
# `SubagentRun.ok` é só `stop_reason == "done"`. Usar o booleano direto para
# escolher a cor faz um estágio parado por orçamento sair VERDE — o ícone diz
# "deu certo" enquanto o texto ao lado diz "interrompido". Estes testes fixam a
# separação entre "acabou o orçamento" (trabalho parcial) e "quebrou".


def _run(**kwargs) -> SubagentRun:
    base = dict(
        name="teste", title="Especialista de teste", objective="o", input="i",
        output="saída", elapsed=1.0,
    )
    base.update(kwargs)
    return SubagentRun(**base)


def test_estagio_concluido_e_verde():
    assert stage_severity(_run(ok=True, stop_reason=STOP_DONE)) == SEVERITY_SUCCESS
    assert stage_label(_run(ok=True, stop_reason=STOP_DONE)) == "concluído"


def test_parada_por_orcamento_nao_e_verde():
    """O bug original: `max_steps` e `timeout` saíam como sucesso."""
    for motivo in (STOP_MAX_STEPS, STOP_TIMEOUT, STOP_ABORTED):
        run = _run(ok=False, stop_reason=motivo)
        assert stage_severity(run) == SEVERITY_WARNING, motivo
        assert stage_label(run) == "interrompido", motivo


def test_estagio_que_quebrou_e_vermelho():
    for motivo in (STOP_ERROR, STOP_NO_PROVIDER, STOP_REJECTED, STOP_REPEATED, STOP_LOOP):
        run = _run(ok=False, stop_reason=motivo)
        assert stage_severity(run) == SEVERITY_ERROR, motivo
        assert stage_label(run) == "falhou", motivo


def test_motivo_desconhecido_nao_passa_batido():
    """Motivo novo no `AgentLoop` deve aparecer, não virar sucesso nem silêncio."""
    run = _run(ok=False, stop_reason="motivo-que-ainda-nao-existe")
    assert stage_severity(run) == SEVERITY_ERROR
    assert stage_label(run) == "falhou"


def test_estagio_ausente_nao_pretende_sucesso():
    """Sem `run` não há evidência de sucesso — o conservador é o aviso."""
    assert stage_severity(None) == SEVERITY_WARNING
    assert stage_label(None) == "interrompido"


def test_ok_verdadeiro_vence_motivo_estranho():
    """`ok` é o sinal primário; o motivo só refina o caso de falha."""
    assert stage_severity(_run(ok=True, stop_reason="qualquer")) == SEVERITY_SUCCESS


def test_toda_gravidade_tem_rotulo():
    """Guardar contra acrescentar uma gravidade sem texto no card."""
    assert set(SEVERITY_LABELS) == {SEVERITY_SUCCESS, SEVERITY_WARNING, SEVERITY_ERROR}


def test_gravidade_do_estagio_real_bate_com_o_motivo_do_loop():
    """Ponta a ponta: um estágio que estoura o orçamento NÃO pode sair verde.

    `VaryingPlanner` nunca conclui, então o `AgentLoop` para por `max_steps` e o
    `ok` do relatório é False. É a mesma situação que o render do card mostrou.
    """
    resultado = SubagentPipeline(
        [spec(max_steps=3, max_seconds=60.0)], StubRegistry(), VaryingPlanner()
    ).run("tarefa")

    estagio = resultado.runs[0]
    assert estagio.ok is False
    assert estagio.stop_reason == STOP_MAX_STEPS
    assert stage_severity(estagio) == SEVERITY_WARNING


def test_run_subagent_nao_promove_gancho_sem_desfecho_a_sucesso():
    """Gancho que devolve um dict sem `ok` não vira sucesso por omissão.

    Mesma regra do `board_opener`: o `PipelineResult` real sempre traz `ok`, então
    isto só vale para um gancho de outra implementação — e presumir ali é como o
    defeito nasce. O `error` acompanha, senão o modelo fica sem o motivo.
    """

    def runner(_especialista, _entrada):
        return {"report": "relatório sem desfecho"}

    resultado = ToolRegistry(subagent_runner=runner).call(
        "run_subagent", {"specialist": "researcher", "input": "x"}
    )

    assert resultado["ok"] is False
    assert resultado["error"]
    assert resultado["specialist"] == "researcher"


def test_run_subagent_nao_promove_retorno_nao_legivel_a_sucesso():
    def runner(_especialista, _entrada):
        return "só um texto solto"

    resultado = ToolRegistry(subagent_runner=runner).call(
        "run_subagent", {"specialist": "researcher", "input": "x"}
    )

    assert resultado["ok"] is False
    assert "relatório" in resultado["error"]


# --------------------------------------------------------------------------- #
# 5ª Entrega: Escopo de janela dedicado e Scratchpad compartilhado
# --------------------------------------------------------------------------- #


def test_window_analyst_esta_no_catalogo_com_inspect_window_e_scratchpad():
    assert "window_analyst" in CATALOG
    analista = CATALOG["window_analyst"]
    for ferramenta in ("inspect_window", "scratchpad_write", "scratchpad_read", "plan_update"):
        assert ferramenta in analista.allow


def test_subagente_compartilha_scratchpad_com_pai_e_isola_milestones():
    pai = ToolRegistry()
    pai.call("scratchpad_write", {"key": "contexto_pai", "content": "dados iniciais"})
    pai.call("plan_update", {"steps": ["Etapa 1 do pai", "Etapa 2 do pai"]})

    restrito = restrict_registry(pai, ("scratchpad_write", "scratchpad_read", "plan_update"))
    assert restrito.scratchpad == {"contexto_pai": "dados iniciais"}
    assert restrito.plan_milestones == []

    # Escrita no subagente reflete no Scratchpad do pai, mas plano do subagente não altera o pai
    restrito.call("scratchpad_write", {"key": "nota_sub", "content": "achado"})
    restrito.call("plan_update", {"steps": ["Sub-etapa única"]})
    assert pai.scratchpad["nota_sub"] == "achado"
    assert len(pai.plan_milestones) == 2

    # Pipeline persiste saída consolidada automaticamente no Scratchpad do registro
    planner = RecordingPlanner(
        responder=lambda objective: AgentDecision(final_answer="Relatório consolidado do estágio")
    )
    resultado = SubagentPipeline([spec("window_analyst")], pai, planner).run(
        "comparar janelas", scratchpad_key="comparacao_final"
    )
    assert resultado.ok is True
    assert pai.scratchpad["subagent_window_analyst"] == "Relatório consolidado do estágio"
    assert pai.scratchpad["comparacao_final"] == "Relatório consolidado do estágio"


def test_target_window_injeta_escopo_de_janela_no_objetivo_e_no_run():
    planner = RecordingPlanner()
    pipeline = SubagentPipeline([spec("window_analyst")], StubRegistry(), planner)
    resultado = pipeline.run("extrair tabela de custos", target_window="LibreOffice Calc")

    assert resultado.ok is True
    assert resultado.runs[0].target_window == "LibreOffice Calc"
    assert resultado.runs[0].to_dict()["target_window"] == "LibreOffice Calc"
    assert "ESCOPO DE JANELA DEDICADO" in resultado.runs[0].objective
    assert "LibreOffice Calc" in resultado.runs[0].objective


def test_run_subagent_repassa_target_window_e_grava_scratchpad():
    recebido = {}

    def runner(especialista, entrada, *, target_window="", scratchpad_key=""):
        recebido["especialista"] = especialista
        recebido["entrada"] = entrada
        recebido["target_window"] = target_window
        recebido["scratchpad_key"] = scratchpad_key
        return {"ok": True, "report": "Dados da planilha extraídos"}

    registro = ToolRegistry(subagent_runner=runner)
    resultado = registro.call(
        "run_subagent",
        {
            "specialist": "window_analyst",
            "input": "extrair coluna B",
            "target_window": "Calc",
            "scratchpad_key": "coluna_b",
        },
    )

    assert resultado["ok"] is True
    assert recebido == {
        "especialista": "window_analyst",
        "entrada": "extrair coluna B",
        "target_window": "Calc",
        "scratchpad_key": "coluna_b",
    }
    assert resultado["target_window"] == "Calc"
    assert resultado["scratchpad_key"] == "coluna_b"
    assert registro.scratchpad["coluna_b"] == "Dados da planilha extraídos"
    assert registro.scratchpad["subagent_window_analyst"] == "Dados da planilha extraídos"
