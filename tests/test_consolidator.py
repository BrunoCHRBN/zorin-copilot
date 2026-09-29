"""Testes unitários para o consolidador de memória (MemoryConsolidator)."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from zorin_copilot.ai.actions import ActionType
from zorin_copilot.core.consolidator import (
    DEFAULT_DAYS_TO_KEEP,
    DEFAULT_INTERVAL_HOURS,
    STATE_FILENAME,
    ConsolidationDecision,
    ConsolidationOutcome,
    ConsolidationState,
    MemoryConsolidator,
    consolidation_state_path,
    decide_consolidation,
    load_consolidation_state,
    save_consolidation_state,
)
from zorin_copilot.core.memory import MemoryManager

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_TOOLS_PATH = REPO_ROOT / "src" / "zorin_copilot" / "ai" / "agent_tools.py"

#: O que a destilação procura em `action_logs` (`consolidator.py`). Escrito aqui de propósito:
#: é a lista que o teste confere, não a que o teste importa — uma guarda que lesse a mesma
#: constante que o código não guardaria nada.
FILTRO_DE_DESTILACAO = ("write_document", "open_document", "vscode_workspace")


class TestMemoryConsolidator(unittest.TestCase):
    def test_consolidation_and_pruning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_memory.db"
            mem = MemoryManager(db_path=db_path)

            # Insere ações recentes
            now_iso = datetime.now().isoformat()
            old_iso = (datetime.now() - timedelta(days=30)).isoformat()

            with mem._get_connection() as conn:
                # Log recente
                conn.execute(
                    """
                    INSERT INTO action_logs (timestamp, prompt, action_type, target, params_json, success, message)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (now_iso, "abrir TCC", "open_document", "TCC_Gestao.docx", "{}", 1, "ok"),
                )
                # Log antigo para ser podado
                conn.execute(
                    """
                    INSERT INTO action_logs (timestamp, prompt, action_type, target, params_json, success, message)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (old_iso, "tocar musica", "media_control", "Queen", "{}", 1, "ok"),
                )

            consolidator = MemoryConsolidator(db_path=db_path)
            report = consolidator.run_consolidation(days_to_keep=14)

            self.assertEqual(report.logs_pruned, 1)
            self.assertGreaterEqual(report.nodes_updated, 1)

            # Verifica que o documento frequente foi promovido para o Grafo de Conhecimento
            node = consolidator.graph.get_node("file:TCC_Gestao.docx")
            self.assertIsNotNone(node)
            self.assertEqual(node.label, "FrequentDocument")


# ---------------------------------------------------------------------------
# A decisão de "quando consolidar"
# ---------------------------------------------------------------------------
class DecideConsolidationOrderTest(unittest.TestCase):
    """A ordem das condições é a regra: da mais fundamental para a mais circunstancial."""

    NOW = datetime(2026, 9, 21, 18, 0, 0)

    def test_disabled_wins_over_everything(self):
        """Recurso desligado: "nunca rodou" ou "relógio para trás" seriam motivos enganosos."""
        casos = (
            ConsolidationState(),
            ConsolidationState(self.NOW - timedelta(days=90)),
            ConsolidationState(self.NOW + timedelta(days=90)),
        )
        for state in casos:
            with self.subTest(state=state):
                outcome = decide_consolidation(state, now=self.NOW, enabled=False)
                self.assertIs(outcome.decision, ConsolidationDecision.DISABLED)
                self.assertFalse(outcome.should_run)

    def test_never_ran_is_reported_as_its_own_reason(self):
        outcome = decide_consolidation(ConsolidationState(), now=self.NOW, enabled=True)
        self.assertIs(outcome.decision, ConsolidationDecision.NEVER_RAN)
        self.assertTrue(outcome.should_run)
        self.assertIsNone(outcome.next_due_at)

    def test_clock_skewed_wins_over_due_and_not_yet(self):
        """Estado no futuro: sem esta condição o intervalo ficaria negativo para sempre."""
        state = ConsolidationState(self.NOW + timedelta(days=3))
        with self.assertLogs("zorin_copilot.core.consolidator", level="WARNING"):
            outcome = decide_consolidation(state, now=self.NOW, enabled=True)
        self.assertIs(outcome.decision, ConsolidationDecision.CLOCK_SKEWED)
        self.assertTrue(outcome.should_run)

    def test_due_when_the_interval_elapsed(self):
        state = ConsolidationState(self.NOW - timedelta(hours=25))
        outcome = decide_consolidation(state, now=self.NOW, enabled=True)
        self.assertIs(outcome.decision, ConsolidationDecision.DUE)
        self.assertTrue(outcome.should_run)

    def test_not_yet_inside_the_interval(self):
        state = ConsolidationState(self.NOW - timedelta(hours=1))
        outcome = decide_consolidation(state, now=self.NOW, enabled=True)
        self.assertIs(outcome.decision, ConsolidationDecision.NOT_YET)
        self.assertFalse(outcome.should_run)
        self.assertEqual(outcome.next_due_at, state.last_run_at + timedelta(hours=24))


class DecideConsolidationBoundaryTest(unittest.TestCase):
    NOW = datetime(2026, 9, 21, 18, 0, 0)

    def test_exactly_at_the_interval_is_due(self):
        """O limite é inclusivo: "24 h depois" já venceu, não "ainda falta um instante"."""
        state = ConsolidationState(self.NOW - timedelta(hours=24))
        self.assertIs(
            decide_consolidation(state, now=self.NOW, enabled=True).decision,
            ConsolidationDecision.DUE,
        )

    def test_one_second_before_is_not_due(self):
        state = ConsolidationState(self.NOW - timedelta(hours=24) + timedelta(seconds=1))
        self.assertIs(
            decide_consolidation(state, now=self.NOW, enabled=True).decision,
            ConsolidationDecision.NOT_YET,
        )

    def test_zero_interval_means_always_due(self):
        state = ConsolidationState(self.NOW)
        self.assertIs(
            decide_consolidation(state, now=self.NOW, enabled=True, interval_hours=0).decision,
            ConsolidationDecision.DUE,
        )

    def test_negative_interval_does_not_freeze_the_feature(self):
        """Um valor sem sentido não pode virar um recurso que nunca roda."""
        state = ConsolidationState(self.NOW)
        self.assertIs(
            decide_consolidation(state, now=self.NOW, enabled=True, interval_hours=-5).decision,
            ConsolidationDecision.DUE,
        )

    def test_negative_interval_does_not_put_next_due_in_the_past(self):
        """O que o `max(0.0, ...)` compra é um `next_due` coerente.

        A decisão já seria DUE de qualquer jeito — o tempo decorrido nunca é menor
        que um intervalo negativo. O que muda é `next_due_at`, que faz parte do
        `to_dict()` público: sem a normalização ele ficaria **antes** do próprio
        `last_run_at`, e quem lesse dali uma data para agendar tiraria um valor sem
        sentido.
        """
        state = ConsolidationState(self.NOW)
        outcome = decide_consolidation(state, now=self.NOW, enabled=True, interval_hours=-5)
        self.assertIs(outcome.decision, ConsolidationDecision.DUE)
        self.assertIsNotNone(outcome.next_due_at)
        self.assertGreaterEqual(
            outcome.next_due_at,
            state.last_run_at,
            "next_due_at ficou no passado",
        )

    def test_custom_interval_is_respected(self):
        state = ConsolidationState(self.NOW - timedelta(hours=2))
        self.assertIs(
            decide_consolidation(state, now=self.NOW, enabled=True, interval_hours=1).decision,
            ConsolidationDecision.DUE,
        )
        self.assertIs(
            decide_consolidation(state, now=self.NOW, enabled=True, interval_hours=6).decision,
            ConsolidationDecision.NOT_YET,
        )

    def test_should_run_covers_exactly_three_outcomes(self):
        """Um desfecho novo não pode passar a rodar por descuido."""
        rodam = {
            decisao
            for decisao in ConsolidationDecision
            if ConsolidationOutcome(decisao).should_run
        }
        self.assertEqual(
            rodam,
            {
                ConsolidationDecision.NEVER_RAN,
                ConsolidationDecision.CLOCK_SKEWED,
                ConsolidationDecision.DUE,
            },
        )

    def test_defaults_are_the_documented_ones(self):
        self.assertEqual(DEFAULT_INTERVAL_HOURS, 24.0)
        self.assertEqual(DEFAULT_DAYS_TO_KEEP, 14)
        self.assertEqual(STATE_FILENAME, "consolidation_state.json")


# ---------------------------------------------------------------------------
# O estado em disco
# ---------------------------------------------------------------------------
class ConsolidationStateShapeTest(unittest.TestCase):
    def test_round_trip(self):
        original = ConsolidationState(
            last_run_at=datetime(2026, 9, 21, 3, 15, 0), last_logs_pruned=7
        )
        self.assertEqual(ConsolidationState.from_dict(original.to_dict()), original)

    def test_empty_state_round_trip(self):
        self.assertEqual(ConsolidationState.from_dict(ConsolidationState().to_dict()), ConsolidationState())

    def test_garbage_is_read_as_never_ran(self):
        """Lixo ⇒ nunca rodou. Rodar a mais custa uma consulta; achar que rodou custa nunca rodar."""
        for lixo in (None, [], "texto", 42, {}, {"last_run_at": "ontem"}, {"last_run_at": 123}):
            with self.subTest(lixo=lixo):
                self.assertEqual(ConsolidationState.from_dict(lixo), ConsolidationState())

    def test_valid_timestamp_inside_garbage_is_kept(self):
        state = ConsolidationState.from_dict(
            {"last_run_at": "2026-09-20T10:00:00", "last_logs_pruned": "muito"}
        )
        self.assertEqual(state.last_run_at, datetime(2026, 9, 20, 10, 0, 0))
        self.assertEqual(state.last_logs_pruned, 0, "contagem inválida não pode virar número")

    def test_negative_count_is_refused(self):
        self.assertEqual(ConsolidationState.from_dict({"last_logs_pruned": -3}).last_logs_pruned, 0)

    def test_to_dict_is_json_serializable(self):
        payload = ConsolidationState(datetime(2026, 9, 21, 3, 0), 2).to_dict()
        self.assertEqual(json.loads(json.dumps(payload))["last_run_at"], "2026-09-21T03:00:00")


class ConsolidationStateFileTest(unittest.TestCase):
    def test_missing_file_is_never_ran(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                load_consolidation_state(Path(tmp) / "nao_existe.json"), ConsolidationState()
            )

    def test_corrupt_json_is_never_ran(self):
        """Um desligamento no meio da escrita não pode travar a consolidação para sempre."""
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "estado.json"
            caminho.write_text('{"last_run_at": "2026-09-2', encoding="utf-8")
            with self.assertLogs("zorin_copilot.core.consolidator", level="WARNING"):
                self.assertEqual(load_consolidation_state(caminho), ConsolidationState())

    def test_save_then_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "estado.json"
            estado = ConsolidationState(datetime(2026, 9, 21, 3, 0, 0), 4)
            save_consolidation_state(caminho, estado)
            self.assertEqual(load_consolidation_state(caminho), estado)

    def test_save_creates_missing_parents(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "fundo" / "mais" / "estado.json"
            save_consolidation_state(caminho, ConsolidationState())
            self.assertTrue(caminho.exists())

    def test_save_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "estado.json"
            save_consolidation_state(caminho, ConsolidationState())
            self.assertEqual(
                sorted(p.name for p in Path(tmp).iterdir()),
                ["estado.json"],
                "sobrou arquivo temporário da gravação atômica",
            )

    def test_a_crash_mid_write_preserves_the_previous_state(self):
        """O que a gravação atômica compra: o estado anterior sobrevive a uma falha.

        Sem o arquivo temporário, `open(destino, "w")` trunca o destino **antes** de
        escrever qualquer byte. Um erro no meio da escrita (disco cheio, desligamento)
        deixaria um JSON pela metade — e estado ilegível significa "nunca rodou". O
        recurso continuaria funcionando, mas o registro de que já rodou sumiria sem
        deixar pista.

        A guarda do nome do arquivo temporário não pega isso: a implementação sem
        `replace` nunca cria temporário nenhum, então passa naquela checagem.
        """
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "estado.json"
            anterior = ConsolidationState(datetime(2026, 9, 21, 3, 0, 0), 7)
            save_consolidation_state(caminho, anterior)

            with mock.patch(
                "zorin_copilot.core.consolidator.json.dump",
                side_effect=OSError("disco cheio"),
            ):
                save_consolidation_state(
                    caminho, ConsolidationState(datetime(2026, 9, 22, 3, 0, 0))
                )

            self.assertEqual(
                load_consolidation_state(caminho),
                anterior,
                "a gravação truncou o estado anterior antes de falhar",
            )
            self.assertFalse(
                (Path(tmp) / "estado.json.tmp").exists(),
                "o temporário de uma gravação falha ficou para trás",
            )

    def test_save_failure_does_not_raise(self):
        """Falhar ao gravar não pode derrubar quem chamou — a consolidação já rodou."""
        with tempfile.TemporaryDirectory() as tmp:
            # Um diretório onde se espera um arquivo: `open` falha com OSError.
            caminho = Path(tmp) / "estado.json"
            caminho.mkdir()
            save_consolidation_state(caminho, ConsolidationState())  # não pode levantar

    def test_state_path_sits_next_to_the_database(self):
        """Ao lado do banco, e **não** em `system_profile`: aquela tabela entra no prompt."""
        self.assertEqual(
            consolidation_state_path("/tmp/qualquer/memory.db"),
            Path("/tmp/qualquer") / STATE_FILENAME,
        )


# ---------------------------------------------------------------------------
# Guarda da regra de destilação
# ---------------------------------------------------------------------------
class DestillationRuleGuardTest(unittest.TestCase):
    """Os tipos que a destilação procura precisam existir de verdade.

    Medição de 2026-09-21, contra o banco real: 194 linhas de `action_logs` em 5 dias, 40
    `action_type` distintos — **nenhum** dos três procurados. A regra não está errada: os três
    são nomes válidos (medido abaixo), mas na prática `nodes_updated` é sempre 0.

    E o outro lado, que é o achado mais forte: ninguém **lê** o que ela escreve.
    `FrequentDocument`, `FREQUENTLY_ACCESSED` e `user:default` só aparecem no próprio
    `consolidator.py` e neste arquivo de teste — o grafo é escrito e nunca consultado. Por isso
    esta guarda **não** tenta "consertar" a regra: escolher o que merece ser lembrado só faz
    sentido quando existir quem leia.

    O vocabulário que pode cair em `action_logs` é a **união** de duas fontes, e isso importa:
    `ActionType` (`ai/actions.py`, o motor de intenções) e os nomes de `ToolSpec`
    (`ai/agent_tools.py`, o agente). `write_file`, por exemplo, é `ActionType` e **não** é
    `ToolSpec` — conferir só uma das duas fontes daria veredito errado.
    """

    def _ferramentas_do_agente(self) -> set[str]:
        """Nomes de `ToolSpec`, lidos do fonte — importar `agent_tools` é pesado demais."""
        fonte = AGENT_TOOLS_PATH.read_text(encoding="utf-8")
        return set(re.findall(r'ToolSpec\(\s*name=["\']([a-z0-9_]+)["\']', fonte))

    def _vocabulario_do_log(self) -> set[str]:
        return self._ferramentas_do_agente() | {a.value for a in ActionType}

    def test_a_extracao_esta_funcionando(self):
        """Guarda da guarda: se o regex parar de casar, os testes abaixo passariam vazios."""
        nomes = self._ferramentas_do_agente()
        self.assertGreater(len(nomes), 50, f"extraí só {len(nomes)} ferramentas")
        # `write_file` é ActionType e não ToolSpec: é o exemplo que prova que a união é necessária.
        self.assertIn("write_file", {a.value for a in ActionType})
        self.assertNotIn("write_file", nomes)

    def test_o_filtro_nao_tem_erro_de_digitacao(self):
        """Um tipo escrito errado nunca casaria — e falharia em silêncio, para sempre."""
        vocabulario = self._vocabulario_do_log()
        for tipo in FILTRO_DE_DESTILACAO:
            with self.subTest(tipo=tipo):
                self.assertIn(
                    tipo,
                    vocabulario,
                    f"'{tipo}' é procurado pela destilação mas não existe em ActionType nem "
                    "em ToolSpec: a regra nunca poderia disparar",
                )

    def test_o_filtro_continua_igual_ao_do_codigo(self):
        """Se a regra mudar, este teste obriga a atualizar o comentário e a doc junto."""
        fonte = (REPO_ROOT / "src" / "zorin_copilot" / "core" / "consolidator.py").read_text(
            encoding="utf-8"
        )
        trecho = fonte[fonte.index("if act in (") :][:120]
        for tipo in FILTRO_DE_DESTILACAO:
            with self.subTest(tipo=tipo):
                self.assertIn(f'"{tipo}"', trecho)

    def test_a_destilacao_e_estreita_de_proposito(self):
        """3 de ~100 nomes possíveis. Ampliar isso é uma decisão de produto, não um ajuste —
        e só vale quando houver quem leia o grafo (ver o docstring da classe)."""
        self.assertEqual(len(FILTRO_DE_DESTILACAO), 3)
        self.assertGreater(len(self._vocabulario_do_log()), 90)


if __name__ == "__main__":
    unittest.main()
