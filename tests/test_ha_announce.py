"""Testes da decisão de anúncio falado de eventos do Home Assistant.

Módulo puro: sem display, sem rede, sem áudio. É o ponto do desenho — a regra
"vale interromper?" precisa ser exercitável sem alto-falante.

Escrito em estilo `unittest` (não pytest) de propósito: o runner de guarda do
projeto usa `unittest`, e um arquivo pytest-style entraria na contagem como
`exit 5` estrutural, engordando o ruído que se confere a cada execução.
"""

from __future__ import annotations

import unittest

from zorin_copilot.core.ha_announce import (
    EXIT_EMPTY,
    EXIT_NO_TARGET,
    EXIT_SPOKEN,
    MAX_SPOKEN_CHARS,
    AnnounceCommand,
    AnnounceDecision,
    AnnounceRequest,
    Announcement,
    announcement_text,
    decide_announcement,
    parse_announce_command,
    parse_entity_filter,
    truncate_for_speech,
)


def _event(**overrides):
    """Evento normalizado típico, com o que o teste quiser trocado."""
    base = {
        "kind": "state",
        "entity_id": "light.sala",
        "domain": "light",
        "state": "on",
        "old_state": "off",
        "attributes": {"friendly_name": "Luz da sala"},
        "friendly_name": "Luz da sala",
        "time_fired": "2026-09-21T19:00:00",
    }
    base.update(overrides)
    return base


class DecisionOrderTest(unittest.TestCase):
    """A ordem das condições É a regra: o motivo mais fundamental vence."""

    def test_disabled_wins_over_everything(self):
        result = decide_announcement(
            _event(), enabled=False, busy=True, entity_filter={"light.outra"}
        )
        self.assertIs(result.decision, AnnounceDecision.DISABLED)
        self.assertFalse(result.should_speak)

    def test_not_state_wins_over_busy(self):
        """Um evento de conexão com o app falando não é "ocupado" — é inaplicável."""
        result = decide_announcement(
            _event(kind="connection"), enabled=True, busy=True
        )
        self.assertIs(result.decision, AnnounceDecision.NOT_STATE)

    def test_filter_wins_over_no_phrase(self):
        """Filtrado é o motivo, mesmo que o domínio também não tivesse frase."""
        result = decide_announcement(
            _event(entity_id="sensor.x", domain="sensor", state="23.5"),
            enabled=True,
            entity_filter={"light.sala"},
        )
        self.assertIs(result.decision, AnnounceDecision.FILTERED)

    def test_unchanged_wins_over_no_phrase(self):
        result = decide_announcement(
            _event(entity_id="sensor.x", domain="sensor", state="23.5", old_state="23.5"),
            enabled=True,
        )
        self.assertIs(result.decision, AnnounceDecision.UNCHANGED)

    def test_busy_wins_over_announce_but_keeps_the_text(self):
        result = decide_announcement(_event(), enabled=True, busy=True)
        self.assertIs(result.decision, AnnounceDecision.BUSY)
        self.assertFalse(result.should_speak)
        self.assertEqual(result.text, "Luz da sala acendeu.")


class NotUsableTest(unittest.TestCase):
    def test_non_dict_is_not_state(self):
        for payload in (None, [], "state_changed", 42):
            with self.subTest(payload=payload):
                self.assertIs(
                    decide_announcement(payload, enabled=True).decision,
                    AnnounceDecision.NOT_STATE,
                )

    def test_missing_entity_id_is_not_state(self):
        result = decide_announcement(_event(entity_id=""), enabled=True)
        self.assertIs(result.decision, AnnounceDecision.NOT_STATE)

    def test_unknown_and_unavailable_are_silent(self):
        for state in ("unknown", "unavailable", "UNKNOWN", ""):
            with self.subTest(state=state):
                result = decide_announcement(_event(state=state), enabled=True)
                self.assertIs(result.decision, AnnounceDecision.NO_STATE)

    def test_missing_state_is_silent(self):
        event = _event()
        del event["state"]
        self.assertIs(
            decide_announcement(event, enabled=True).decision,
            AnnounceDecision.NO_STATE,
        )

    def test_silent_decision_carries_no_text(self):
        result = decide_announcement(_event(state="unavailable"), enabled=True)
        self.assertEqual(result.text, "")
        self.assertFalse(result.should_speak)


class FilterTest(unittest.TestCase):
    def test_entity_filter_blocks_other_entities(self):
        result = decide_announcement(
            _event(), enabled=True, entity_filter={"light.quarto"}
        )
        self.assertIs(result.decision, AnnounceDecision.FILTERED)

    def test_entity_filter_allows_listed_entity(self):
        result = decide_announcement(
            _event(), enabled=True, entity_filter={"light.sala"}
        )
        self.assertIs(result.decision, AnnounceDecision.ANNOUNCE)

    def test_domain_filter_allows_matching_domain(self):
        result = decide_announcement(_event(), enabled=True, domains={"light"})
        self.assertIs(result.decision, AnnounceDecision.ANNOUNCE)

    def test_domain_filter_blocks_other_domain(self):
        result = decide_announcement(_event(), enabled=True, domains={"switch"})
        self.assertIs(result.decision, AnnounceDecision.FILTERED)

    def test_empty_filter_means_everything(self):
        for empty in (None, set()):
            with self.subTest(empty=empty):
                self.assertIs(
                    decide_announcement(_event(), enabled=True, entity_filter=empty).decision,
                    AnnounceDecision.ANNOUNCE,
                )


class PhraseTest(unittest.TestCase):
    def test_light_turns_on_and_off(self):
        self.assertEqual(announcement_text(_event(state="on")), "Luz da sala acendeu.")
        self.assertEqual(announcement_text(_event(state="off")), "Luz da sala apagou.")

    def test_switch_turns_on_and_off(self):
        event = _event(domain="switch", entity_id="switch.monitor", friendly_name="Tomada do monitor")
        self.assertEqual(announcement_text(event), "Tomada do monitor ligou.")
        event["state"] = "off"
        self.assertEqual(announcement_text(event), "Tomada do monitor desligou.")

    def test_door_sensor_opens_and_closes(self):
        event = _event(
            domain="binary_sensor",
            entity_id="binary_sensor.porta",
            friendly_name="Porta da frente",
            attributes={"friendly_name": "Porta da frente", "device_class": "door"},
        )
        self.assertEqual(announcement_text(event), "Porta da frente abriu.")
        event["state"] = "off"
        self.assertEqual(announcement_text(event), "Porta da frente fechou.")

    def test_window_sensor_uses_the_same_verbs_as_door(self):
        event = _event(
            domain="binary_sensor",
            friendly_name="Janela do quarto",
            attributes={"device_class": "window"},
        )
        self.assertEqual(announcement_text(event), "Janela do quarto abriu.")

    def test_motion_announces_arrival_but_not_the_end(self):
        event = _event(
            domain="binary_sensor",
            friendly_name="Sensor da escada",
            attributes={"device_class": "motion"},
        )
        self.assertEqual(announcement_text(event), "Sensor da escada detectou movimento.")
        # `old_state` precisa virar "on": com o base em "off", mudar só o `state`
        # para "off" produziria uma transição **inalterada** — e o teste passaria
        # por UNCHANGED, afirmando o silêncio pelo motivo errado.
        event["state"] = "off"
        event["old_state"] = "on"
        self.assertEqual(announcement_text(event), "")
        self.assertIs(
            decide_announcement(event, enabled=True).decision,
            AnnounceDecision.NO_PHRASE,
        )

    def test_lock_and_cover_and_person(self):
        cases = [
            ({"domain": "lock", "state": "locked", "friendly_name": "Fechadura"}, "Fechadura trancou."),
            ({"domain": "lock", "state": "unlocked", "friendly_name": "Fechadura"}, "Fechadura destrancou."),
            ({"domain": "cover", "state": "open", "friendly_name": "Persiana"}, "Persiana abriu."),
            ({"domain": "cover", "state": "closed", "friendly_name": "Persiana"}, "Persiana fechou."),
            ({"domain": "person", "state": "home", "friendly_name": "Bruno"}, "Bruno chegou em casa."),
            ({"domain": "person", "state": "not_home", "friendly_name": "Bruno"}, "Bruno saiu de casa."),
        ]
        for overrides, expected in cases:
            with self.subTest(overrides=overrides):
                self.assertEqual(announcement_text(_event(**overrides)), expected)

    def test_state_matching_is_case_insensitive(self):
        self.assertEqual(announcement_text(_event(state="ON")), "Luz da sala acendeu.")

    def test_boolean_state_is_treated_as_on_off(self):
        """Alguns caminhos entregam `True`/`False` em vez de "on"/"off"."""
        self.assertEqual(announcement_text(_event(state=True)), "Luz da sala acendeu.")
        self.assertEqual(announcement_text(_event(state=False)), "Luz da sala apagou.")

    def test_name_falls_back_to_entity_id(self):
        event = _event(friendly_name="", attributes={})
        self.assertEqual(announcement_text(event), "light.sala acendeu.")

    def test_no_name_at_all_is_silent(self):
        event = _event(friendly_name="", entity_id="", attributes={})
        self.assertEqual(announcement_text(event), "")

    def test_attributes_not_a_dict_does_not_crash(self):
        event = _event(domain="binary_sensor", attributes="lixo")
        # Sem device_class conhecida não há verbo — silêncio, não exceção.
        self.assertEqual(announcement_text(event), "")


class NoGenericFallbackTest(unittest.TestCase):
    """Guarda contra o catch-all barulhento.

    Um fallback "mudou para X" transformaria todo `sensor.*` num narrador de
    variações de leitura. Se alguém quiser anunciar um domínio novo, o caminho é
    acrescentar a frase dele à tabela — e é isso que este teste exige.
    """

    def test_unmapped_domain_is_silent(self):
        for domain, state in (("sensor", "23.5"), ("input_number", "7"), ("weather", "rainy")):
            with self.subTest(domain=domain):
                event = _event(domain=domain, state=state, friendly_name="Alguma coisa")
                self.assertEqual(announcement_text(event), "")
                self.assertIs(
                    decide_announcement(event, enabled=True).decision,
                    AnnounceDecision.NO_PHRASE,
                )

    def test_unmapped_state_of_a_mapped_domain_is_silent(self):
        """Uma luz com brilho mudado não é um evento falável."""
        event = _event(state="unavailable")
        self.assertEqual(announcement_text(event), "")
        event["state"] = "playing"
        self.assertEqual(announcement_text(event), "")


class ParseEntityFilterTest(unittest.TestCase):
    def test_empty_inputs_give_empty_set(self):
        for raw in (None, "", "   ", ",", " , , "):
            with self.subTest(raw=raw):
                self.assertEqual(parse_entity_filter(raw), set())

    def test_parses_and_trims(self):
        self.assertEqual(
            parse_entity_filter(" light.sala , binary_sensor.porta ,switch.monitor "),
            {"light.sala", "binary_sensor.porta", "switch.monitor"},
        )

    def test_result_is_usable_as_a_filter(self):
        filt = parse_entity_filter("light.quarto")
        self.assertIs(
            decide_announcement(_event(), enabled=True, entity_filter=filt).decision,
            AnnounceDecision.FILTERED,
        )


class ResultShapeTest(unittest.TestCase):
    def test_to_dict_reports_the_reason(self):
        payload = decide_announcement(_event(), enabled=True).to_dict()
        self.assertEqual(payload["decision"], "announce")
        self.assertTrue(payload["should_speak"])
        self.assertEqual(payload["text"], "Luz da sala acendeu.")
        self.assertEqual(payload["entity_id"], "light.sala")
        self.assertEqual(payload["state"], "on")

    def test_to_dict_of_a_silent_decision_is_honest(self):
        payload = decide_announcement(_event(), enabled=False).to_dict()
        self.assertEqual(payload["decision"], "disabled")
        self.assertFalse(payload["should_speak"])
        self.assertEqual(payload["text"], "")

    def test_default_announcement_never_claims_speech(self):
        """Um `Announcement` construído sem frase não pode virar fala."""
        self.assertFalse(Announcement(AnnounceDecision.ANNOUNCE).should_speak)


# ---------------------------------------------------------------------------
# Pedido vindo de fora (`zorin-copilot --announce "texto"`, usado pela ponte)
# ---------------------------------------------------------------------------
class ParseAnnounceCommandTest(unittest.TestCase):
    def test_remote_request_speaks(self):
        command = parse_announce_command("Alguém tocou a campainha", is_remote=True)
        self.assertIs(command.action, AnnounceRequest.SPEAK)
        self.assertTrue(command.should_speak)
        self.assertEqual(command.text, "Alguém tocou a campainha")
        self.assertFalse(command.truncated)
        self.assertEqual(command.exit_code, EXIT_SPOKEN)

    def test_local_request_is_refused(self):
        """`is_remote=False` = o app não estava rodando.

        Abrir a interface inteira só para dizer uma frase seria o oposto do que
        a ponte quer. Recusar é o que a deixa cair no `spd-say`.
        """
        command = parse_announce_command("Bom dia", is_remote=False)
        self.assertIs(command.action, AnnounceRequest.NO_TARGET)
        self.assertFalse(command.should_speak)
        self.assertEqual(command.text, "")
        self.assertEqual(command.exit_code, EXIT_NO_TARGET)

    def test_empty_text_is_refused_even_with_a_listener(self):
        """A ordem das condições é a regra: validade antes de circunstância.

        "Não há app" seria um motivo enganoso para um pedido vazio.
        """
        for raw in ("", "   ", "\n\t "):
            with self.subTest(raw=raw):
                command = parse_announce_command(raw, is_remote=True)
                self.assertIs(command.action, AnnounceRequest.EMPTY)
                self.assertEqual(command.exit_code, EXIT_EMPTY)

    def test_empty_beats_local(self):
        """Vazio e local ao mesmo tempo: o motivo relatado é o vazio."""
        command = parse_announce_command("", is_remote=False)
        self.assertIs(command.action, AnnounceRequest.EMPTY)
        self.assertEqual(command.exit_code, EXIT_EMPTY)

    def test_non_string_is_refused(self):
        for raw in (None, 42, ["texto"], {"a": 1}):
            with self.subTest(raw=raw):
                self.assertIs(
                    parse_announce_command(raw, is_remote=True).action,
                    AnnounceRequest.EMPTY,
                )

    def test_surrounding_whitespace_is_trimmed(self):
        command = parse_announce_command("  Bom dia  ", is_remote=True)
        self.assertEqual(command.text, "Bom dia")

    def test_long_text_is_truncated_and_flagged(self):
        """Truncar **sem avisar** apagaria informação do usuário sem que ele
        soubesse; `truncated` é o aviso."""
        command = parse_announce_command("palavra " * 200, is_remote=True)
        self.assertTrue(command.truncated)
        self.assertLessEqual(len(command.text), MAX_SPOKEN_CHARS)
        self.assertTrue(command.should_speak)

    def test_short_text_is_not_flagged(self):
        self.assertFalse(parse_announce_command("curto", is_remote=True).truncated)

    def test_exit_codes_are_distinct(self):
        """Se dois desfechos compartilhassem código, a ponte não teria como
        distinguir "falei" de "não havia app"."""
        self.assertEqual(
            len({EXIT_SPOKEN, EXIT_NO_TARGET, EXIT_EMPTY}), 3
        )

    def test_exit_code_of_speaking_is_zero(self):
        """A ponte trata 0 como sucesso; um dia isso pode virar 0 por acidente."""
        self.assertEqual(EXIT_SPOKEN, 0)

    def test_to_dict_reports_the_reason(self):
        payload = parse_announce_command("Bom dia", is_remote=True).to_dict()
        self.assertEqual(payload["action"], "speak")
        self.assertTrue(payload["should_speak"])
        self.assertEqual(payload["text"], "Bom dia")
        self.assertFalse(payload["truncated"])
        self.assertEqual(payload["exit_code"], EXIT_SPOKEN)

    def test_default_command_never_claims_speech(self):
        self.assertFalse(AnnounceCommand(AnnounceRequest.SPEAK).should_speak)


class TruncateForSpeechTest(unittest.TestCase):
    def test_short_text_passes_through(self):
        self.assertEqual(truncate_for_speech("Bom dia"), ("Bom dia", False))

    def test_exact_limit_is_not_truncated(self):
        texto = "a" * MAX_SPOKEN_CHARS
        self.assertEqual(truncate_for_speech(texto), (texto, False))

    def test_cuts_at_a_word_boundary(self):
        """Cortar no meio de uma palavra faz o sintetizador pronunciar um
        fragmento esquisito."""
        texto = "palavra " * 100
        curto, truncado = truncate_for_speech(texto)
        self.assertTrue(truncado)
        self.assertLessEqual(len(curto), MAX_SPOKEN_CHARS)
        self.assertFalse(curto.endswith(" "))
        # A última palavra está inteira.
        self.assertTrue(texto.startswith(curto))
        self.assertEqual(texto[len(curto)], " ")

    def test_cuts_at_the_limit_when_there_is_no_space(self):
        """Uma URL gigante não tem espaço nenhum: cortar no limite é melhor do
        que falar a coisa inteira."""
        curto, truncado = truncate_for_speech("x" * 1000, max_chars=10)
        self.assertEqual(curto, "x" * 10)
        self.assertTrue(truncado)

    def test_space_only_at_the_start_does_not_zero_the_text(self):
        """O corte só vale se houver palavra **antes** dele.

        `head.rfind(" ")` devolve 0 quando o único espaço do trecho está no
        índice 0, e `head[:0]` seria string vazia — ou seja, o teto viraria
        mudez. É essa a guarda que o `cut > 0` protege.
        """
        curto, truncado = truncate_for_speech(" " + "y" * 50, max_chars=10)
        self.assertTrue(truncado)
        self.assertEqual(curto, " yyyyyyyyy")
        self.assertEqual(len(curto), 10)

    def test_zero_limit_disables_truncation(self):
        """`max_chars <= 0` é "sem teto", não "texto vazio"."""
        texto = "a" * 5000
        self.assertEqual(truncate_for_speech(texto, max_chars=0), (texto, False))

    def test_custom_limit_is_respected(self):
        curto, truncado = truncate_for_speech("um dois tres quatro", max_chars=8)
        self.assertEqual(curto, "um dois")
        self.assertTrue(truncado)


if __name__ == "__main__":
    unittest.main()
