"""Testes para core/study_session — estado compartilhado de estudo.

Cobertura: pub/sub, mutações concorrentes (lock), serialização versionada,
migração e persistência atômica. Tudo headless (sem GTK/rede)."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from zorin_copilot.core.study_session import (
    SCHEMA_VERSION,
    STUDY_EVENT_BLACKBOARD_EXPANDED,
    STUDY_EVENT_METHOD_SELECTED,
    STUDY_EVENT_PROBLEM_SET,
    STUDY_EVENT_STEP_HIGHLIGHTED,
    STUDY_EVENT_VERIFICATION_DONE,
    StudySession,
    _migrate,
)


class _FakeGraph:
    """Dublê de SolutionGraph com to_dict/from_dict para testar serialização."""

    def __init__(self, text: str = ""):
        self.problem_statement = text

    def to_dict(self) -> dict:
        return {"problem_statement": self.problem_statement}


class PubSubTest(unittest.TestCase):
    def test_set_problem_emite_evento_com_payload(self):
        s = StudySession()
        seen = []
        s.subscribe(lambda ev, pl: seen.append((ev, pl)))
        s.set_problem(problem_statement="x²-5x+6=0", givens="a=1", answer="S={2,3}")
        self.assertEqual(len(seen), 1)
        ev, pl = seen[0]
        self.assertEqual(ev, STUDY_EVENT_PROBLEM_SET)
        self.assertEqual(pl["problem_statement"], "x²-5x+6=0")
        self.assertEqual(pl["answer"], "S={2,3}")

    def test_eventos_de_navegacao_do_quadro(self):
        s = StudySession()
        events = []
        s.subscribe(lambda ev, pl: events.append((ev, pl)))
        s.highlight_step("m_bhas/b2")
        s.select_method("m_fator")
        s.expand_blackboard()
        s.mark_verification(passed=True)
        self.assertEqual(
            [e for e, _ in events],
            [
                STUDY_EVENT_STEP_HIGHLIGHTED,
                STUDY_EVENT_METHOD_SELECTED,
                STUDY_EVENT_BLACKBOARD_EXPANDED,
                STUDY_EVENT_VERIFICATION_DONE,
            ],
        )
        self.assertEqual(events[0][1]["node_id"], "m_bhas/b2")
        self.assertEqual(events[3][1]["passed"], True)

    def test_set_modality_emite_e_atualiza(self):
        s = StudySession()
        seen = []
        s.subscribe(lambda ev, pl: seen.append((ev, pl)))
        s.set_modality("voice")
        self.assertEqual(s.modality, "voice")
        self.assertTrue(any(pl.get("modality") == "voice" for _, pl in seen))

    def test_unsubscribe_para_de_receber(self):
        s = StudySession()
        hits = []
        cb = lambda ev, pl: hits.append(ev)  # noqa: E731
        s.subscribe(cb)
        s.set_problem(problem_statement="p")
        s.unsubscribe(cb)
        s.set_problem(problem_statement="q")
        self.assertEqual(len(hits), 1)


class ConcurrencyTest(unittest.TestCase):
    def test_muitas_threads_nao_corrompem_estado(self):
        s = StudySession()
        counter = {"problem": 0, "step": 0}

        def reader():
            for _ in range(50):
                # Acesso concorrente sob lock; não deve lançar nem retornar parcial.
                _ = (s.problem_statement, s.modality, s.updated_at)

        def writer():
            for i in range(25):
                s.set_modality("voice" if i % 2 else "chat")
                s.highlight_step(f"n{i}")
                counter["step"] += 1

        threads = [threading.Thread(target=reader) for _ in range(4)] + [
            threading.Thread(target=writer) for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Nenhuma exceção e os contadores batem com o esperado.
        self.assertEqual(counter["step"], 50)
        self.assertIn(s.modality, ("voice", "chat"))
        # updated_at reflete atividade e é numérico/finito.
        self.assertTrue(s.updated_at > 0)


class SerializationTest(unittest.TestCase):
    def test_roundtrip_to_dict_from_dict(self):
        s = StudySession(discipline="Contabilidade", rigor_mode="CN")
        s.set_problem(problem_statement="p", blackboard=_FakeGraph("x²"))
        d = s.to_dict()
        self.assertEqual(d["version"], SCHEMA_VERSION)
        self.assertEqual(d["blackboard"], {"problem_statement": "x²"})

        s2 = StudySession.from_dict(d)
        self.assertEqual(s2.problem_statement, "p")
        self.assertEqual(s2.discipline, "Contabilidade")
        self.assertEqual(s2.rigor_mode, "CN")

    def test_get_blackboard_reconstrroi_objeto(self):
        s = StudySession()
        s.set_problem(problem_statement="p", blackboard=_FakeGraph("x²"))
        # objeto já é instância: devolvido direto
        self.assertEqual(s.get_blackboard().problem_statement, "x²")
        # após serializar/recarregar, blackboard vira dict e é reconstruído via import tardio
        s2 = StudySession.from_dict(s.to_dict())
        self.assertIsInstance(s2.blackboard, dict)
        g = s2.get_blackboard()
        self.assertEqual(g.problem_statement, "x²")

    def test_save_e_load_persistem_e_sao_atomicos(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sess" / "session.json"
            s = StudySession(discipline="Física")
            s.set_problem(problem_statement="E=mc²", answer="ok")
            s.save(path)
            self.assertTrue(path.exists())
            # arquivo .tmp não deve sobrar
            self.assertFalse((path.parent / (path.name + ".tmp")).exists())
            s2 = StudySession.load(path)
            self.assertEqual(s2.problem_statement, "E=mc²")
            self.assertEqual(s2.discipline, "Física")

    def test_migrate_aceita_versao_atual(self):
        raw = {"version": SCHEMA_VERSION, "problem_statement": "p"}
        self.assertEqual(_migrate(raw)["version"], SCHEMA_VERSION)

    def test_migrate_recusa_versao_futura(self):
        raw = {"version": SCHEMA_VERSION + 5, "problem_statement": "p"}
        with self.assertRaises(ValueError):
            _migrate(raw)


if __name__ == "__main__":
    unittest.main()
