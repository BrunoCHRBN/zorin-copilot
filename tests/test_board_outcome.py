# Decisão de design: estes testes são o CONTRATO do desfecho do Quadro Negro, não
# a qualidade do raciocínio matemático (que tem suíte própria). O ponto que eles
# protegem é a honestidade: o sistema só pode afirmar o que verificou.
#
# O defeito que originou o módulo: `board_opener` devolvia sempre a frase "Quadro
# Negro aberto", e os três consumidores hard-codavam `ok/success: True`. Sem
# provedor de IA configurado, nada abria — e a voz falava "aberto" enquanto o log
# da sessão gravava sucesso. Nenhum teste que olhasse só a mensagem pegaria: a
# mensagem era sempre a mesma, otimista.
#
# Tudo aqui é puro: sem display, sem provedor, sem rede.

"""Testes do desfecho da abertura do Quadro Negro."""

from __future__ import annotations

import pytest

from zorin_copilot.core.board_outcome import (
    BOARD_FAILED,
    BOARD_OK,
    BOARD_PENDING,
    BOARD_UNAVAILABLE,
    BOARD_UNKNOWN,
    BoardOutcome,
    decide_path,
    message_for,
    outcome_from_payload,
)

#: Todos os desfechos possíveis. Guarda contra acrescentar um e esquecer o resto.
ALL_STATUSES = (BOARD_OK, BOARD_PENDING, BOARD_UNAVAILABLE, BOARD_FAILED, BOARD_UNKNOWN)


# --------------------------------------------------------------------------- #
# decide_path — qual rota a abertura toma
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("has_graph", "has_problem", "provider_ready", "expected"),
    [
        # Com grafo, a álgebra pura resolveu: não precisa de modelo nem provedor.
        (True, True, True, BOARD_OK),
        (True, True, False, BOARD_OK),
        (True, False, False, BOARD_OK),
        # Sem problema, reusa a última solução da conversa: idem.
        (False, False, True, BOARD_OK),
        (False, False, False, BOARD_OK),
        # Problema sem grafo: depende do modelo.
        (False, True, True, BOARD_PENDING),
        # Problema sem grafo e sem provedor: NADA vai abrir. Era este o caso que
        # o app reportava como sucesso.
        (False, True, False, BOARD_UNAVAILABLE),
    ],
)
def test_decide_path(has_graph, has_problem, provider_ready, expected):
    assert (
        decide_path(
            has_graph=has_graph, has_problem=has_problem, provider_ready=provider_ready
        )
        == expected
    )


def test_sem_problema_vence_a_ausencia_de_provedor():
    """A ordem das condições é a regra: reusar a última solução não usa modelo."""
    assert decide_path(has_graph=False, has_problem=False, provider_ready=False) == BOARD_OK


def test_nunca_devolve_ok_sem_evidencia():
    """Sem grafo E com problema, `ok` é impossível — é a garantia central."""
    for provider_ready in (True, False):
        status = decide_path(has_graph=False, has_problem=True, provider_ready=provider_ready)
        assert status != BOARD_OK


# --------------------------------------------------------------------------- #
# BoardOutcome — as três perguntas que os consumidores fazem
# --------------------------------------------------------------------------- #


def test_apenas_ok_afirma_que_abriu():
    assert BoardOutcome(BOARD_OK).opened is True
    for status in (BOARD_PENDING, BOARD_UNAVAILABLE, BOARD_FAILED, BOARD_UNKNOWN):
        assert BoardOutcome(status).opened is False, status


def test_apenas_pending_esta_em_andamento():
    assert BoardOutcome(BOARD_PENDING).pending is True
    for status in (BOARD_OK, BOARD_UNAVAILABLE, BOARD_FAILED, BOARD_UNKNOWN):
        assert BoardOutcome(status).pending is False, status


def test_aceito_cobre_ok_e_pending_mas_nao_o_desconhecido():
    """O log de auditoria usa isto como booleano — e `unknown` não pode virar True.

    Em `pending` fomos nós que iniciamos a resolução. Em `unknown` não se sabe
    nem se o pedido chegou; gravar `True` seria preencher auditoria com suposição.
    """
    assert BoardOutcome(BOARD_OK).accepted is True
    assert BoardOutcome(BOARD_PENDING).accepted is True
    assert BoardOutcome(BOARD_UNKNOWN).accepted is False
    assert BoardOutcome(BOARD_UNAVAILABLE).accepted is False
    assert BoardOutcome(BOARD_FAILED).accepted is False


def test_to_dict_e_serializavel_e_nao_tem_chave_ambigua():
    payload = BoardOutcome(BOARD_OK, "abri", detail="").to_dict()
    assert payload["ok"] is True
    assert payload["opened"] is True
    assert payload["pending"] is False
    assert payload["status"] == BOARD_OK
    assert payload["message"] == "abri"
    assert payload["error"] == ""


def test_todo_desfecho_tem_frase():
    """Guardar contra acrescentar um desfecho sem texto para falar."""
    for status in ALL_STATUSES:
        frase = message_for(status)
        assert frase and frase.strip(), status


def test_frase_de_sucesso_menciona_o_destaque():
    assert "fatoração" in message_for(BOARD_OK, "fatoração")
    # Sem destaque, nenhuma menção pendurada.
    assert "destaque" not in message_for(BOARD_OK, "")


def test_nenhuma_frase_de_falha_diz_que_abriu():
    """A frase é o que a voz fala: uma falha não pode soar como sucesso."""
    for status in (BOARD_UNAVAILABLE, BOARD_FAILED, BOARD_UNKNOWN):
        frase = message_for(status).lower()
        assert "aberto" not in frase, status


def test_desfecho_desconhecido_tem_frase_neutra():
    """`unknown` não pode afirmar nem sucesso nem falha."""
    frase = message_for(BOARD_UNKNOWN).lower()
    assert "não foi possível confirmar" in frase
    assert "não consegui" not in frase


# --------------------------------------------------------------------------- #
# outcome_from_payload — ler o gancho sem confiar no formato
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("payload", [None, {}, "texto solto", 42, [], {"algo": "outro"}])
def test_payload_sem_desfecho_vira_desconhecido_e_nunca_sucesso(payload):
    """A regra que fecha o defeito: não saber ≠ ter dado certo."""
    outcome = outcome_from_payload(payload)
    assert outcome.status == BOARD_UNKNOWN
    assert outcome.opened is False
    assert outcome.accepted is False


def test_payload_com_status_explicito_e_respeitado():
    for status in ALL_STATUSES:
        outcome = outcome_from_payload({"status": status, "message": "m", "error": "d"})
        assert outcome.status == status
        assert outcome.message == "m"
        assert outcome.detail == "d"


def test_payload_com_status_sem_mensagem_usa_a_frase_do_desfecho():
    outcome = outcome_from_payload({"status": BOARD_UNAVAILABLE})
    assert outcome.message == message_for(BOARD_UNAVAILABLE)


def test_formato_antigo_sem_status_nao_vira_sucesso():
    """Um gancho que só devolve mensagem (o formato que mentia) não é promovido.

    A frase dele é preservada — quem a escreveu foi ele — mas o veredito continua
    sendo nosso: `opened` fica False e `accepted`, também.
    """
    outcome = outcome_from_payload({"message": "Quadro Negro aberto."})
    assert outcome.status == BOARD_UNKNOWN
    assert outcome.message == "Quadro Negro aberto."
    assert outcome.opened is False
    assert outcome.accepted is False


def test_booleano_explicito_do_gancho_ainda_vale():
    """Um gancho que afirma `opened`/`ok` explicitamente é levado a sério."""
    assert outcome_from_payload({"opened": True}).status == BOARD_OK
    assert outcome_from_payload({"ok": True}).status == BOARD_OK
    falha = outcome_from_payload({"ok": False, "error": "sem display"})
    assert falha.status == BOARD_FAILED
    assert falha.detail == "sem display"


def test_status_desconhecido_nao_e_inventado():
    """Um `status` que não conhecemos não vira sucesso por engano."""
    outcome = outcome_from_payload({"status": "em-andamento-talvez"})
    assert outcome.status == BOARD_UNKNOWN
    assert outcome.accepted is False


def test_falha_sem_detalhe_proprio_explica_se_pela_mensagem():
    """`ok: False` com `error` vazio deixaria o modelo sem o motivo.

    É o defeito original em outra roupa: um consumidor que só leia `error` não
    saberia por que a abertura não aconteceu.
    """
    payload = BoardOutcome(BOARD_UNAVAILABLE, message_for(BOARD_UNAVAILABLE)).to_dict()
    assert payload["ok"] is False
    assert payload["error"] == message_for(BOARD_UNAVAILABLE)


def test_detalhe_proprio_tem_prioridade_sobre_a_mensagem():
    payload = BoardOutcome(BOARD_FAILED, "falhou", detail="modelo fora do ar").to_dict()
    assert payload["error"] == "modelo fora do ar"


def test_sucesso_nao_carrega_erro():
    payload = BoardOutcome(BOARD_OK, "abri").to_dict()
    assert payload["error"] == ""
