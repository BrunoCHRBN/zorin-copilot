# Decisão de design: os helpers de agrupamento/classificação da sidebar são funções
# puras, testáveis sem construir a janela inteira — a lista agrupada é justamente a
# parte que mais erra em silêncio (data mal calculada some com uma conversa de "Hoje").

"""Testes dos helpers de agrupamento por data e classificação da barra lateral."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.ui.widgets.sidebar import (  # noqa: E402
    classify_topic,
    format_clock,
    group_topics_by_date,
)

NOW = datetime(2026, 9, 20, 22, 0, 0)

def _topic(title: str, days_ago: float) -> dict:
    updated = NOW - timedelta(days=days_ago)
    return {"id": title, "title": title, "updated_at": updated.isoformat(), "turn_count": 1}

class GroupTopicsByDateTest(unittest.TestCase):
    def test_buckets_by_recency_in_canonical_order(self):
        topics = [
            _topic("hoje", 0),
            _topic("ontem", 1),
            _topic("tres dias", 3),
            _topic("trinta dias", 30),
        ]
        groups = group_topics_by_date(topics, now=NOW)
        self.assertEqual([label for label, _ in groups], ["Hoje", "Ontem", "Esta semana", "Anterior"])
        self.assertEqual(groups[0][1][0]["title"], "hoje")
        self.assertEqual(groups[3][1][0]["title"], "trinta dias")

    def test_empty_buckets_are_omitted(self):
        groups = group_topics_by_date([_topic("hoje", 0)], now=NOW)
        self.assertEqual([label for label, _ in groups], ["Hoje"])

    def test_future_or_invalid_timestamps_do_not_vanish(self):
        """Data no futuro ou corrompida cai em "Hoje"/"Anterior", nunca some.

        Uma conversa que desaparece da lista é pior que uma mal categorizada:
        o usuário conclui que perdeu o histórico.
        """
        bad = {"id": "x", "title": "x", "updated_at": "não é data", "turn_count": 0}
        future = _topic("futuro", -1)
        groups = group_topics_by_date([bad, future], now=NOW)
        labels = [label for label, _ in groups]
        self.assertIn("Hoje", labels)
        self.assertIn("Anterior", labels)
        total = sum(len(items) for _, items in groups)
        self.assertEqual(total, 2)

    def test_six_days_is_this_week_and_seven_is_older(self):
        groups = group_topics_by_date([_topic("seis", 6), _topic("sete", 7)], now=NOW)
        mapping = {label: [t["title"] for t in items] for label, items in groups}
        self.assertEqual(mapping["Esta semana"], ["seis"])
        self.assertEqual(mapping["Anterior"], ["sete"])

class ClassifyTopicTest(unittest.TestCase):
    def test_voice_titles_are_detected(self):
        self.assertEqual(classify_topic({"title": "Conversa de Voz ao Vivo"}), "voice")
        self.assertEqual(classify_topic({"title": "Chamada de Voz e Tela ao Vivo"}), "voice")

    def test_regular_titles_are_chat(self):
        self.assertEqual(classify_topic({"title": "Como listar arquivos grandes?"}), "chat")
        self.assertEqual(classify_topic({"title": ""}), "chat")
        self.assertEqual(classify_topic({}), "chat")

class FormatClockTest(unittest.TestCase):
    def test_returns_time_only(self):
        self.assertEqual(format_clock("2026-09-20T14:32:10"), "14:32")

    def test_invalid_input_is_empty_not_exception(self):
        self.assertEqual(format_clock(""), "")
        self.assertEqual(format_clock("lixo"), "")

if __name__ == "__main__":
    unittest.main()
