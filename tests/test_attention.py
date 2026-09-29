"""Testes da política de atenção — puros, sem display, sem áudio e sem relógio.

O que se testa aqui é a **regra**: dado um evento e um contexto, qual nível de
interrupção. Nada de GTK, nada de rede, nada de tempo real — tudo entra por
parâmetro. É o que permite exercitar a noite inteira, o orçamento estourado e a
tela bloqueada sem esperar que aconteçam.

A ordem das condições **é** a regra, então boa parte desta suíte é sobre ordem:
um caso que casa duas condições tem de relatar o motivo mais fundamental.
"""

from __future__ import annotations

import inspect
import unittest

from zorin_copilot.core import attention
from zorin_copilot.core.attention import (
    DEFAULT_AWAY_SECONDS,
    DEFAULT_MAX_PER_HOUR,
    AttentionContext,
    AttentionDecision,
    AttentionLevel,
    AttentionPolicy,
    AttentionReason,
    InterruptionBudget,
    Severity,
    decide_attention,
    is_explicitly_selected,
    is_within_silence,
    parse_silence_windows,
    severity_for,
)
from zorin_copilot.core.ha_announce import _BINARY_SENSOR_VERBS


def _evento(**overrides):
    base = {
        "kind": "state",
        "entity_id": "binary_sensor.fumaca",
        "domain": "binary_sensor",
        "state": "on",
        "old_state": "off",
        "attributes": {"device_class": "smoke"},
        "friendly_name": "Sensor de fumaça",
    }
    base.update(overrides)
    return base


def _ctx(**overrides) -> AttentionContext:
    """Contexto de um usuário presente, acordado, numa hora qualquer."""
    base = {"hour": 14, "idle_seconds": 5.0}
    base.update(overrides)
    return AttentionContext(**base)


def _politica(**overrides) -> AttentionPolicy:
    base = {
        "enabled": True,
        "silence_windows": parse_silence_windows("23-7"),
        "away_seconds": DEFAULT_AWAY_SECONDS,
        "max_per_hour": DEFAULT_MAX_PER_HOUR,
    }
    base.update(overrides)
    return AttentionPolicy(**base)


def _decide(severity=Severity.NORMAL, context=None, budget=None, policy=None):
    return decide_attention(
        severity=severity,
        context=context if context is not None else _ctx(),
        budget=budget if budget is not None else InterruptionBudget(),
        policy=policy if policy is not None else _politica(),
    )


# ---------------------------------------------------------------------------
# Vocabulário
# ---------------------------------------------------------------------------
class VocabularioTest(unittest.TestCase):
    def test_niveis_sao_estaveis(self):
        """Fixado para que renomear um nível exija atualizar quem o lê."""
        self.assertEqual(
            {nivel.value for nivel in AttentionLevel},
            {"speak", "notify", "badge", "ignore"},
        )

    def test_motivos_sao_estaveis(self):
        self.assertEqual(
            {motivo.value for motivo in AttentionReason},
            {
                "disabled",
                "urgent",
                "silenced",
                "screen_locked",
                "live_session",
                "speaking",
                "budget_exhausted",
                "away",
                "routine",
                "normal",
            },
        )

    def test_severidades_sao_estaveis(self):
        self.assertEqual(
            {s.value for s in Severity}, {"urgent", "normal", "routine"}
        )

    def test_os_quatro_niveis_sao_mutuamente_exclusivos(self):
        """Um nível não pode responder "sim" a duas perguntas ao mesmo tempo."""
        for nivel in AttentionLevel:
            decisao = AttentionDecision(nivel, AttentionReason.NORMAL, Severity.NORMAL)
            marcas = [
                decisao.should_speak,
                decisao.should_notify,
                decisao.should_badge,
                decisao.should_ignore,
            ]
            with self.subTest(nivel=nivel):
                self.assertEqual(sum(marcas), 1)

    def test_interrupts_now_cobre_fala_e_aviso(self):
        self.assertTrue(
            AttentionDecision(
                AttentionLevel.SPEAK, AttentionReason.NORMAL, Severity.NORMAL
            ).interrupts_now
        )
        self.assertTrue(
            AttentionDecision(
                AttentionLevel.NOTIFY, AttentionReason.LIVE_SESSION, Severity.NORMAL
            ).interrupts_now
        )
        self.assertFalse(
            AttentionDecision(
                AttentionLevel.BADGE, AttentionReason.ROUTINE, Severity.ROUTINE
            ).interrupts_now
        )
        self.assertFalse(
            AttentionDecision(
                AttentionLevel.IGNORE, AttentionReason.SILENCED, Severity.NORMAL
            ).interrupts_now
        )


# ---------------------------------------------------------------------------
# Severidade
# ---------------------------------------------------------------------------
class SeveridadeTest(unittest.TestCase):
    def test_deteccao_de_fumaca_e_urgente(self):
        self.assertIs(severity_for(_evento()), Severity.URGENT)

    def test_o_alivio_da_fumaca_nao_e_urgente(self):
        """O fim de uma detecção informa; não é o momento de alarmar com força."""
        self.assertIs(severity_for(_evento(state="off", old_state="on")), Severity.NORMAL)

    def test_gas_e_agua_tambem_sao_urgentes(self):
        for classe in ("gas", "moisture", "carbon_monoxide"):
            with self.subTest(classe=classe):
                self.assertIs(
                    severity_for(_evento(attributes={"device_class": classe})),
                    Severity.URGENT,
                )

    def test_movimento_e_corriqueiro(self):
        """Movimento dispara o tempo todo; falar a cada passo é insuportável."""
        evento = _evento(
            entity_id="binary_sensor.corredor",
            attributes={"device_class": "motion"},
        )
        self.assertIs(severity_for(evento), Severity.ROUTINE)

    def test_luz_tomada_e_persiana_sao_corriqueiras(self):
        for dominio in ("light", "switch", "cover"):
            with self.subTest(dominio=dominio):
                evento = _evento(
                    domain=dominio,
                    entity_id=f"{dominio}.sala",
                    state="on",
                    attributes={},
                )
                self.assertIs(severity_for(evento), Severity.ROUTINE)

    def test_porta_janela_e_pessoa_sao_normais(self):
        for classe in ("door", "window", "garage_door", "opening", "lock"):
            with self.subTest(classe=classe):
                evento = _evento(
                    entity_id=f"binary_sensor.{classe}",
                    attributes={"device_class": classe},
                )
                self.assertIs(severity_for(evento), Severity.NORMAL)
        self.assertIs(
            severity_for(
                _evento(
                    domain="person",
                    entity_id="person.bruno",
                    state="home",
                    attributes={},
                )
            ),
            Severity.NORMAL,
        )

    def test_desconhecido_cai_em_corriqueiro(self):
        """O viés de um assistente ambiente tem de ser o silêncio."""
        self.assertIs(
            severity_for(_evento(domain="sensor", entity_id="sensor.x", attributes={})),
            Severity.ROUTINE,
        )

    def test_o_conjunto_normal_e_fixado(self):
        """`NORMAL` é a **exceção** explícita; o caso geral é `ROUTINE`.

        Não existe conjunto `_ROUTINE_DOMAINS`: ele seria código morto, porque
        devolveria o mesmo que o caso geral. Ampliar `NORMAL` exige tocar aqui.
        """
        self.assertEqual(set(attention._NORMAL_DOMAINS), {"person", "lock"})

    def test_entrada_lixo_nao_levanta(self):
        for entrada in (None, [], "texto", 42, {}, {"attributes": None}):
            with self.subTest(entrada=entrada):
                self.assertIs(severity_for(entrada), Severity.ROUTINE)

    def test_estado_booleano_e_lido_como_ha(self):
        self.assertIs(
            severity_for(_evento(state=True, attributes={"device_class": "smoke"})),
            Severity.URGENT,
        )
        self.assertIs(
            severity_for(_evento(state=False, attributes={"device_class": "smoke"})),
            Severity.NORMAL,
        )

    def test_caixa_e_espaco_nao_mudam_a_classe(self):
        self.assertIs(
            severity_for(_evento(attributes={"device_class": "  SMOKE  "})),
            Severity.URGENT,
        )


class SelecaoExplicitaTest(unittest.TestCase):
    """Nomear a entidade no filtro é intenção — e vence a classificação.

    Sem esta regra a política viraria uma jaula: quem escrevesse `light.sala` em
    `ha_announce_entities` continuaria sem ouvir sobre a luz, porque `light` é
    `ROUTINE` por natureza. O filtro seria um botão que não faz nada.
    """

    LUZ = {"domain": "light", "entity_id": "light.sala", "state": "on", "attributes": {}}

    def test_nomeada_promove_corriqueiro_a_normal(self):
        self.assertIs(severity_for(self.LUZ), Severity.ROUTINE)
        self.assertIs(
            severity_for(self.LUZ, explicitly_selected=True), Severity.NORMAL
        )

    def test_nomear_nao_rebaixa_a_seguranca(self):
        """Um sensor de fumaça é urgente por si, nomeado ou não."""
        self.assertIs(
            severity_for(_evento(), explicitly_selected=True), Severity.URGENT
        )

    def test_nomear_nao_mexe_em_quem_ja_e_normal(self):
        porta = {
            "domain": "binary_sensor",
            "entity_id": "binary_sensor.porta",
            "state": "on",
            "attributes": {"device_class": "door"},
        }
        self.assertIs(severity_for(porta), Severity.NORMAL)
        self.assertIs(severity_for(porta, explicitly_selected=True), Severity.NORMAL)

    def test_filtro_vazio_nao_nomeia_ninguem(self):
        self.assertFalse(is_explicitly_selected(self.LUZ, None))
        self.assertFalse(is_explicitly_selected(self.LUZ, set()))

    def test_filtro_com_a_entidade_nomeia(self):
        self.assertTrue(is_explicitly_selected(self.LUZ, {"light.sala"}))
        self.assertFalse(is_explicitly_selected(self.LUZ, {"light.quarto"}))

    def test_entrada_lixo_nao_levanta(self):
        for evento in (None, [], "texto", 42):
            with self.subTest(evento=evento):
                self.assertFalse(is_explicitly_selected(evento, {"light.sala"}))
        self.assertFalse(is_explicitly_selected({}, {"light.sala"}))

    def test_entidade_vazia_nunca_casa(self):
        """Guarda de API: `parse_entity_filter` nunca produz `""`, mas quem
        chamar a função com um filtro montado à mão poderia. Sem a checagem,
        um evento sem `entity_id` casaria com um filtro contendo `""`."""
        self.assertFalse(is_explicitly_selected({"entity_id": ""}, {""}))
        self.assertFalse(is_explicitly_selected({}, {""}))

    def test_a_promocao_muda_o_desfecho(self):
        """A regra só vale se chegar ao nível: `ROUTINE` acumula, `NORMAL` fala."""
        sem_filtro = _decide(severity=severity_for(self.LUZ))
        com_filtro = _decide(
            severity=severity_for(self.LUZ, explicitly_selected=True)
        )
        self.assertIs(sem_filtro.level, AttentionLevel.BADGE)
        self.assertIs(com_filtro.level, AttentionLevel.SPEAK)


class SeveridadeSemDerivaTest(unittest.TestCase):
    """As duas tabelas de vocabulário não podem divergir em silêncio.

    `ha_announce._BINARY_SENSOR_VERBS` decide **se há frase**; a tabela daqui
    decide **quão grave é**. Se alguém acrescentar uma classe de sensor numa
    tabela e esquecer a outra, o sintoma é um sensor de segurança entrando mudo
    (sem frase) ou falando baixinho (severidade errada) — e nenhum dos dois
    levanta erro.
    """

    #: Classes que o Home Assistant tem e que `ha_announce` ainda **não** sabe
    #: frasear. Escritas aqui de propósito: elas nunca chegam à política hoje, e
    #: a lista existe para que acrescentar a frase depois não caia na severidade
    #: errada sem ninguém notar.
    FORA_DA_TABELA_DE_FRASES = {"carbon_monoxide", "occupancy", "presence"}

    #: Classes com frase que **não** aparecem nas tabelas daqui porque caem no
    #: caso geral (`Severity.NORMAL`) de `severity_for`.
    CAEM_NO_CASO_GERAL = {"door", "window", "garage_door", "opening", "lock"}

    def _classes_da_politica(self) -> set[str]:
        return set(attention._URGENT_DEVICE_CLASSES) | set(
            attention._ROUTINE_DEVICE_CLASSES
        )

    def test_nenhuma_classe_inventada(self):
        """Classe que não existe no HA é typo, e typo em segurança é sensor mudo."""
        self.assertEqual(
            self._classes_da_politica() - set(_BINARY_SENSOR_VERBS),
            self.FORA_DA_TABELA_DE_FRASES,
        )

    def test_toda_classe_com_frase_esta_classificada(self):
        """Nenhuma classe fraseada pode ficar sem decisão explícita."""
        self.assertEqual(
            set(_BINARY_SENSOR_VERBS) - self._classes_da_politica(),
            self.CAEM_NO_CASO_GERAL,
        )

    def test_as_classes_de_seguranca_da_tabela_de_frases_sao_urgentes(self):
        """Fumaça, gás e água têm frase **e** são segurança. Não podem divergir."""
        seguranca = {"smoke", "gas", "moisture"}
        self.assertTrue(seguranca <= set(_BINARY_SENSOR_VERBS))
        self.assertTrue(seguranca <= set(attention._URGENT_DEVICE_CLASSES))

    def test_as_classes_urgentes_sao_fixadas(self):
        """Ampliar a lista de urgência exige tocar neste teste, de propósito."""
        self.assertEqual(
            set(attention._URGENT_DEVICE_CLASSES),
            {"smoke", "gas", "moisture", "carbon_monoxide"},
        )


# ---------------------------------------------------------------------------
# Janela de silêncio
# ---------------------------------------------------------------------------
class JanelaDeSilencioTest(unittest.TestCase):
    def test_vazio_e_sem_janela(self):
        self.assertEqual(parse_silence_windows(""), ())
        self.assertEqual(parse_silence_windows("   "), ())
        self.assertEqual(parse_silence_windows(None), ())

    def test_le_uma_e_varias(self):
        self.assertEqual(parse_silence_windows("23-7"), ((23, 7),))
        self.assertEqual(parse_silence_windows("23-7,13-14"), ((23, 7), (13, 14)))
        self.assertEqual(parse_silence_windows(" 1-2 , 3-4 "), ((1, 2), (3, 4)))

    def test_lixo_e_descartado_sem_derrubar_o_resto(self):
        """Uma janela malformada não pode invalidar as outras."""
        self.assertEqual(parse_silence_windows("23-7,lixo,13-14"), ((23, 7), (13, 14)))
        self.assertEqual(parse_silence_windows("24-7"), ())
        self.assertEqual(parse_silence_windows("-1-5"), ())
        self.assertEqual(parse_silence_windows("5"), ())
        self.assertEqual(parse_silence_windows("a-b"), ())
        self.assertEqual(parse_silence_windows(42), ())
        self.assertEqual(parse_silence_windows(["23-7"]), ())

    def test_janela_normal_e_inclusiva_no_inicio_e_exclusiva_no_fim(self):
        janelas = parse_silence_windows("13-14")
        self.assertTrue(is_within_silence(13, janelas))
        self.assertFalse(is_within_silence(14, janelas))
        self.assertFalse(is_within_silence(12, janelas))

    def test_janela_atravessa_a_meia_noite(self):
        """`23-7` cobre 23 e 0..6 — o fim é exclusivo."""
        janelas = parse_silence_windows("23-7")
        for hora in (23, 0, 1, 2, 3, 4, 5, 6):
            with self.subTest(hora=hora):
                self.assertTrue(is_within_silence(hora, janelas))
        for hora in (7, 8, 12, 22):
            with self.subTest(hora=hora):
                self.assertFalse(is_within_silence(hora, janelas))

    def test_janelas_complementares_nao_se_sobrepoem(self):
        """`23-7` e `7-23` juntas cobrem o dia sem contar hora duas vezes."""
        noite = parse_silence_windows("23-7")
        dia = parse_silence_windows("7-23")
        for hora in range(24):
            with self.subTest(hora=hora):
                self.assertNotEqual(
                    is_within_silence(hora, noite), is_within_silence(hora, dia)
                )

    def test_sem_janela_nunca_silencia(self):
        for hora in range(24):
            with self.subTest(hora=hora):
                self.assertFalse(is_within_silence(hora, ()))

    def test_hora_invalida_nao_silencia(self):
        """Hora sem sentido não pode calar o assistente por acidente."""
        janelas = parse_silence_windows("23-7")
        for hora in (-1, 24, 99):
            with self.subTest(hora=hora):
                self.assertFalse(is_within_silence(hora, janelas))


# ---------------------------------------------------------------------------
# Orçamento
# ---------------------------------------------------------------------------
class OrcamentoTest(unittest.TestCase):
    def test_zero_nao_e_limite(self):
        """`0` não significa "sem limite" — significa que nada cabe."""
        self.assertTrue(InterruptionBudget(max_per_hour=0).exhausted)

    def test_negativo_tambem_nao_e_sem_limite(self):
        """A assimetria com `decide_consolidation` é deliberada.

        Ali um valor sem sentido virando "nunca rodar" deixaria a memória
        invisivelmente sem poda; aqui, virando "sem limite", deixaria o
        assistente visivelmente falante — que é o defeito a evitar.
        """
        self.assertTrue(InterruptionBudget(max_per_hour=-1).exhausted)
        self.assertTrue(InterruptionBudget(max_per_hour=-99).exhausted)

    def test_esgota_no_limite_inclusive(self):
        self.assertFalse(
            InterruptionBudget(spoken_last_hour=5, max_per_hour=6).exhausted
        )
        self.assertTrue(
            InterruptionBudget(spoken_last_hour=6, max_per_hour=6).exhausted
        )
        self.assertTrue(
            InterruptionBudget(spoken_last_hour=99, max_per_hour=6).exhausted
        )

    def test_restante_nunca_e_negativo(self):
        self.assertEqual(
            InterruptionBudget(spoken_last_hour=2, max_per_hour=6).remaining, 4
        )
        self.assertEqual(
            InterruptionBudget(spoken_last_hour=9, max_per_hour=6).remaining, 0
        )
        self.assertEqual(
            InterruptionBudget(spoken_last_hour=0, max_per_hour=0).remaining, 0
        )
        self.assertEqual(
            InterruptionBudget(spoken_last_hour=0, max_per_hour=-1).remaining, 0
        )


# ---------------------------------------------------------------------------
# A ordem das condições
# ---------------------------------------------------------------------------
class OrdemDasCondicoesTest(unittest.TestCase):
    """Um caso que casa duas condições tem de relatar o motivo mais fundamental."""

    def test_desligado_vence_ate_a_urgencia(self):
        """O interruptor é do usuário, e a escolha dele vence.

        Isto é uma decisão, não um descuido: o recurso é **opt-in** e quem o
        desligou pediu silêncio. O Zorin Copilot é um assistente ambiente, não um
        sistema de salvamento — a detecção de fumaça da casa não depende dele
        para funcionar, e um alarme que ignora o interruptor é pior do que um
        alarme que não existe, porque ninguém sabe quando ele vai falar.
        """
        contexto = _ctx(speaking=True, screen_locked=True, live_session_active=True)
        decisao = _decide(
            severity=Severity.URGENT,
            context=contexto,
            budget=InterruptionBudget(spoken_last_hour=99, max_per_hour=1),
            policy=_politica(enabled=False),
        )
        self.assertIs(decisao.level, AttentionLevel.IGNORE)
        self.assertIs(decisao.reason, AttentionReason.DISABLED)

    def test_urgente_vence_a_janela_de_silencio(self):
        """A condição que justifica a existência do nível urgente."""
        decisao = _decide(severity=Severity.URGENT, context=_ctx(hour=3))
        self.assertIs(decisao.level, AttentionLevel.SPEAK)
        self.assertIs(decisao.reason, AttentionReason.URGENT)

    def test_urgente_vence_o_orcamento(self):
        decisao = _decide(
            severity=Severity.URGENT,
            budget=InterruptionBudget(spoken_last_hour=99, max_per_hour=1),
        )
        self.assertIs(decisao.level, AttentionLevel.SPEAK)

    def test_urgente_vence_a_tela_bloqueada(self):
        decisao = _decide(severity=Severity.URGENT, context=_ctx(screen_locked=True))
        self.assertIs(decisao.level, AttentionLevel.SPEAK)

    def test_urgente_vence_a_sessao_ao_vivo(self):
        decisao = _decide(
            severity=Severity.URGENT, context=_ctx(live_session_active=True)
        )
        self.assertIs(decisao.level, AttentionLevel.SPEAK)

    def test_urgente_vence_o_usuario_ausente(self):
        decisao = _decide(
            severity=Severity.URGENT, context=_ctx(idle_seconds=99999.0)
        )
        self.assertIs(decisao.level, AttentionLevel.SPEAK)

    def test_urgente_nao_vence_a_propria_fala(self):
        """Não há como sobrepor áudio — o melhor possível é ficar visível já."""
        decisao = _decide(severity=Severity.URGENT, context=_ctx(speaking=True))
        self.assertIs(decisao.level, AttentionLevel.NOTIFY)
        self.assertIs(decisao.reason, AttentionReason.URGENT)
        self.assertTrue(decisao.interrupts_now)

    def test_falar_vence_a_janela_de_silencio(self):
        """A própria fala em andamento é capacidade, não preferência."""
        decisao = _decide(context=_ctx(hour=3, speaking=True))
        self.assertIs(decisao.reason, AttentionReason.SPEAKING)

    def test_falar_vence_a_tela_bloqueada(self):
        decisao = _decide(context=_ctx(screen_locked=True, speaking=True))
        self.assertIs(decisao.reason, AttentionReason.SPEAKING)

    def test_silencio_vence_a_tela_bloqueada(self):
        decisao = _decide(context=_ctx(hour=3, screen_locked=True))
        self.assertIs(decisao.reason, AttentionReason.SILENCED)

    def test_tela_bloqueada_vence_a_sessao_ao_vivo(self):
        decisao = _decide(context=_ctx(screen_locked=True, live_session_active=True))
        self.assertIs(decisao.reason, AttentionReason.SCREEN_LOCKED)

    def test_sessao_ao_vivo_vence_o_orcamento(self):
        decisao = _decide(
            context=_ctx(live_session_active=True),
            budget=InterruptionBudget(spoken_last_hour=99, max_per_hour=1),
        )
        self.assertIs(decisao.reason, AttentionReason.LIVE_SESSION)

    def test_orcamento_vence_a_ausencia(self):
        decisao = _decide(
            context=_ctx(idle_seconds=99999.0),
            budget=InterruptionBudget(spoken_last_hour=99, max_per_hour=1),
        )
        self.assertIs(decisao.reason, AttentionReason.BUDGET_EXHAUSTED)

    def test_ausencia_vence_o_evento_corriqueiro(self):
        decisao = _decide(
            severity=Severity.ROUTINE, context=_ctx(idle_seconds=99999.0)
        )
        self.assertIs(decisao.reason, AttentionReason.AWAY)

    def test_corriqueiro_vence_o_caso_geral(self):
        decisao = _decide(severity=Severity.ROUTINE)
        self.assertIs(decisao.reason, AttentionReason.ROUTINE)


# ---------------------------------------------------------------------------
# Cada nível, pelo caminho feliz
# ---------------------------------------------------------------------------
class NiveisTest(unittest.TestCase):
    def test_presente_e_orcamento_sobrando_fala(self):
        decisao = _decide(severity=Severity.NORMAL)
        self.assertIs(decisao.level, AttentionLevel.SPEAK)
        self.assertIs(decisao.reason, AttentionReason.NORMAL)
        self.assertTrue(decisao.should_speak)

    def test_sessao_ao_vivo_vira_aviso_visual(self):
        """O usuário está conversando: avisar sem falar por cima."""
        decisao = _decide(context=_ctx(live_session_active=True))
        self.assertIs(decisao.level, AttentionLevel.NOTIFY)
        self.assertTrue(decisao.should_notify)

    def test_janela_de_silencio_ignora(self):
        """Critério de pronto do roteiro: silêncio resulta em `IGNORE`."""
        decisao = _decide(severity=Severity.NORMAL, context=_ctx(hour=2))
        self.assertIs(decisao.level, AttentionLevel.IGNORE)
        self.assertIs(decisao.reason, AttentionReason.SILENCED)

    def test_tela_bloqueada_acumula_em_selo(self):
        decisao = _decide(context=_ctx(screen_locked=True))
        self.assertIs(decisao.level, AttentionLevel.BADGE)

    def test_orcamento_estourado_acumula_em_selo(self):
        decisao = _decide(budget=InterruptionBudget(spoken_last_hour=6, max_per_hour=6))
        self.assertIs(decisao.level, AttentionLevel.BADGE)
        self.assertIs(decisao.reason, AttentionReason.BUDGET_EXHAUSTED)

    def test_ausente_acumula_em_selo(self):
        """Falar para uma cadeira vazia não informa ninguém."""
        decisao = _decide(context=_ctx(idle_seconds=DEFAULT_AWAY_SECONDS))
        self.assertIs(decisao.level, AttentionLevel.BADGE)
        self.assertIs(decisao.reason, AttentionReason.AWAY)

    def test_ociosidade_desconhecida_pula_a_condicao(self):
        """`None` não é zero: "não sei" não vira palpite.

        Medido nesta máquina: a interface de bloqueio de tela responde, mas a de
        tempo ocioso não existe. Quando a ociosidade é desconhecida, a condição de
        ausência é pulada — chutar "presente" ou "ausente" erra para um dos dois
        lados, e os dois são piores do que não usar a condição.
        """
        decisao = _decide(context=_ctx(idle_seconds=None))
        self.assertIs(decisao.level, AttentionLevel.SPEAK)
        self.assertIs(decisao.reason, AttentionReason.NORMAL)

    def test_ociosidade_desconhecida_nao_e_o_mesmo_que_zero(self):
        """A distinção só aparece quando `away_seconds` torna a condição sensível.

        Com `away_seconds=0`, "zero segundos ocioso" **casa** a condição e "não
        sei" não casa. É esta diferença que prova que `None` não está sendo
        silenciosamente convertido em `0.0`.
        """
        desconhecida = _decide(
            context=_ctx(idle_seconds=None), policy=_politica(away_seconds=0.0)
        )
        zero = _decide(
            context=_ctx(idle_seconds=0.0), policy=_politica(away_seconds=0.0)
        )
        self.assertIs(desconhecida.reason, AttentionReason.NORMAL)
        self.assertIs(zero.reason, AttentionReason.AWAY)

    def test_corriqueiro_acumula_em_selo(self):
        decisao = _decide(severity=Severity.ROUTINE)
        self.assertIs(decisao.level, AttentionLevel.BADGE)

    def test_um_segundo_antes_de_ausente_ainda_fala(self):
        """A fronteira é inclusiva no lado de "ausente"."""
        decisao = _decide(context=_ctx(idle_seconds=DEFAULT_AWAY_SECONDS - 1.0))
        self.assertIs(decisao.level, AttentionLevel.SPEAK)


class NivelCombinaComOContextoTest(unittest.TestCase):
    """Nível e motivo nunca se contradizem.

    Percorre o produto cartesiano inteiro do que entra e cobra que cada desfecho
    seja coerente. É a guarda mais barata contra uma condição nova inserida no
    lugar errado: a ordem muda e este teste acusa.
    """

    def test_matriz_de_contextos_e_severidades(self):
        permitidos = {
            AttentionReason.DISABLED: {AttentionLevel.IGNORE},
            AttentionReason.URGENT: {AttentionLevel.SPEAK, AttentionLevel.NOTIFY},
            AttentionReason.SPEAKING: {AttentionLevel.BADGE},
            AttentionReason.SILENCED: {AttentionLevel.IGNORE},
            AttentionReason.SCREEN_LOCKED: {AttentionLevel.BADGE},
            # Dois níveis, e não um: a sessão ao vivo rebaixa a fala para aviso
            # visual, mas **não promove** um evento corriqueiro a aviso. Sem isto,
            # uma luz acendendo durante uma conversa por voz daria um toast —
            # mais atenção do que o mesmo evento teria sem a sessão. Descoberto
            # pela varredura de `CorriqueiroNuncaInterrompeTest`.
            AttentionReason.LIVE_SESSION: {AttentionLevel.NOTIFY, AttentionLevel.BADGE},
            AttentionReason.BUDGET_EXHAUSTED: {AttentionLevel.BADGE},
            AttentionReason.AWAY: {AttentionLevel.BADGE},
            AttentionReason.ROUTINE: {AttentionLevel.BADGE},
            AttentionReason.NORMAL: {AttentionLevel.SPEAK},
        }
        for severity in Severity:
            for hora in (2, 14):
                for bloqueada in (False, True):
                    for ao_vivo in (False, True):
                        for falando in (False, True):
                            for ocioso in (5.0, 99999.0, None):
                                for orcamento in (
                                    InterruptionBudget(),
                                    InterruptionBudget(spoken_last_hour=99, max_per_hour=1),
                                ):
                                    contexto = AttentionContext(
                                        hour=hora,
                                        screen_locked=bloqueada,
                                        live_session_active=ao_vivo,
                                        speaking=falando,
                                        idle_seconds=ocioso,
                                    )
                                    decisao = _decide(
                                        severity=severity,
                                        context=contexto,
                                        budget=orcamento,
                                    )
                                    with self.subTest(
                                        severity=severity,
                                        hora=hora,
                                        bloqueada=bloqueada,
                                        ao_vivo=ao_vivo,
                                        falando=falando,
                                        ocioso=ocioso,
                                    ):
                                        self.assertIn(
                                            decisao.level,
                                            permitidos[decisao.reason],
                                            f"{decisao.reason.value} não pode dar "
                                            f"{decisao.level.value}",
                                        )

    def test_ignorar_e_o_unico_nivel_sem_rastro(self):
        """`IGNORE` só pode vir de duas causas, e as duas são silêncio pedido."""
        vistos = set()
        for severity in Severity:
            for enabled in (False, True):
                for hora in (2, 14):
                    decisao = _decide(
                        severity=severity,
                        context=_ctx(hour=hora),
                        policy=_politica(enabled=enabled),
                    )
                    if decisao.should_ignore:
                        vistos.add(decisao.reason)
        self.assertEqual(vistos, {AttentionReason.DISABLED, AttentionReason.SILENCED})


# ---------------------------------------------------------------------------
# Guardas de forma
# ---------------------------------------------------------------------------
class FormaDaDecisaoTest(unittest.TestCase):
    def test_to_dict_serializa_o_essencial(self):
        decisao = AttentionDecision(
            AttentionLevel.SPEAK, AttentionReason.NORMAL, Severity.NORMAL
        )
        self.assertEqual(
            decisao.to_dict(),
            {
                "level": "speak",
                "reason": "normal",
                "severity": "normal",
                "should_speak": True,
                "should_notify": False,
                "should_badge": False,
                "should_ignore": False,
            },
        )

    def test_decisao_e_imutavel(self):
        decisao = AttentionDecision(
            AttentionLevel.BADGE, AttentionReason.ROUTINE, Severity.ROUTINE
        )
        with self.assertRaises(Exception):
            decisao.level = AttentionLevel.SPEAK  # type: ignore[misc]

    def test_a_severidade_de_origem_viaja_com_a_decisao(self):
        """O log precisa poder dizer de que gravidade era o evento recusado."""
        decisao = _decide(severity=Severity.URGENT, policy=_politica(enabled=False))
        self.assertIs(decisao.severity, Severity.URGENT)


class ModuloPuroTest(unittest.TestCase):
    """Se alguém importar GTK aqui, a regra deixa de ser testável sem display."""

    def test_modulo_de_decisao_nao_tem_gtk(self):
        fonte = inspect.getsource(attention)
        for proibido in ("import gi", "from gi.repository", "Adw", "Gtk.", "GLib"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, fonte)

    def test_modulo_de_decisao_nao_tem_rede_nem_audio(self):
        fonte = inspect.getsource(attention)
        for proibido in ("requests", "subprocess", "socket", "sqlite3", "piper"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, fonte)

    def test_modulo_de_decisao_nao_le_relogio(self):
        """A hora entra por parâmetro — senão a regra não é determinística."""
        fonte = inspect.getsource(attention)
        for proibido in ("time.time", "datetime.now", "monotonic"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, fonte)


class CorriqueiroNuncaInterrompeTest(unittest.TestCase):
    """`ROUTINE` **nunca** fala nem avisa — em contexto nenhum.

    Este não é um teste de mais um caso: é a **premissa** de que o caminho dos
    documentos depende. `_on_document_event` (`ui/app.py`) manda severidade
    `ROUTINE` fixa e trata "não ignorado" como selo, sem ramo para fala. Se
    `ROUTINE` pudesse chegar a `SPEAK` ou `NOTIFY`, aquele código estaria errado
    em silêncio — o evento sairia como selo quando devia ser falado.

    Varredura exaustiva do espaço de contexto em vez de exemplos escolhidos: é o
    que transforma "eu acho que não fala" em "não fala".
    """

    def test_em_nenhum_contexto_corriqueiro_fala_ou_avisa(self):
        horas = (0, 2, 6, 7, 12, 14, 22, 23)
        for hora in horas:
            for travada in (False, True):
                for ao_vivo in (False, True):
                    for falando in (False, True):
                        for ocioso in (None, 0.0, 60.0, 99999.0):
                            with self.subTest(
                                hora=hora, travada=travada, ao_vivo=ao_vivo,
                                falando=falando, ocioso=ocioso,
                            ):
                                decisao = _decide(
                                    severity=Severity.ROUTINE,
                                    context=_ctx(
                                        hour=hora, screen_locked=travada,
                                        live_session_active=ao_vivo,
                                        speaking=falando, idle_seconds=ocioso,
                                    ),
                                )
                                self.assertIn(
                                    decisao.level,
                                    (AttentionLevel.BADGE, AttentionLevel.IGNORE),
                                    "corriqueiro alcançou um nível de interrupção",
                                )

    def test_urgente_e_a_unica_coisa_que_fura_a_janela_de_silencio(self):
        """O contraponto, no mesmo lugar: só `URGENT` interrompe no silêncio."""
        noite = _ctx(hour=3)
        silencioso = _politica(silence_windows=((23, 7),))
        self.assertIs(
            _decide(severity=Severity.ROUTINE, context=noite, policy=silencioso).level,
            AttentionLevel.IGNORE,
        )
        self.assertIs(
            _decide(severity=Severity.NORMAL, context=noite, policy=silencioso).level,
            AttentionLevel.IGNORE,
        )
        self.assertIs(
            _decide(severity=Severity.URGENT, context=noite, policy=silencioso).level,
            AttentionLevel.SPEAK,
        )


class DefaultsDaConfigTest(unittest.TestCase):
    """Os defaults existem em **dois** lugares, e eles têm de concordar.

    `AttentionPolicy` traz defaults para quando nada é informado, e
    `CopilotConfig` traz os seus para o que o usuário vê na tela. Duas fontes
    para o mesmo número divergem em silêncio: quem mexer numa acha que mexeu no
    default. Estes testes são o que transforma a duplicação em contrato.
    """

    def test_os_dois_conjuntos_de_default_batem(self):
        from zorin_copilot.core.config import CopilotConfig

        cfg = CopilotConfig()
        self.assertEqual(
            attention.parse_silence_windows(cfg.attention_silence_hours),
            attention.parse_silence_windows(attention.DEFAULT_SILENCE_SPEC),
        )
        self.assertEqual(cfg.attention_max_per_hour, attention.DEFAULT_MAX_PER_HOUR)
        self.assertEqual(cfg.attention_away_minutes * 60.0, attention.DEFAULT_AWAY_SECONDS)
        self.assertIs(cfg.attention_enabled, attention.AttentionPolicy().enabled)

    def test_a_politica_sem_argumentos_usa_os_defaults_do_modulo(self):
        padrao = AttentionPolicy()
        self.assertEqual(padrao.away_seconds, attention.DEFAULT_AWAY_SECONDS)
        self.assertEqual(padrao.max_per_hour, attention.DEFAULT_MAX_PER_HOUR)
        self.assertEqual(
            attention.parse_silence_windows(attention.DEFAULT_SILENCE_SPEC),
            ((23, 7),),
        )

    def test_o_default_de_silencio_e_a_noite(self):
        """O único default capaz, sozinho, de evitar a impressão de alarme."""
        self.assertTrue(attention.is_within_silence(2, attention.parse_silence_windows(attention.DEFAULT_SILENCE_SPEC)))
        self.assertFalse(attention.is_within_silence(14, attention.parse_silence_windows(attention.DEFAULT_SILENCE_SPEC)))


if __name__ == "__main__":
    unittest.main()
