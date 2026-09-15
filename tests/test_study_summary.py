# Decisão de design: os testes usam um modelo dublê que devolve JSON pronto, então o
# que se valida aqui é a PARTE QUE IMPORTA — os filtros. O modelo local insiste em
# devolver rótulo de seção ("Introdução") e definição circular ("custeio é custeio"),
# e é o código que precisa barrar isso, não o prompt.

"""Testes de resumo em tópicos e glossário."""

from __future__ import annotations

import importlib
import json
import os

import pytest

from zorin_copilot import cli
from zorin_copilot.core.study_summary import (
    GlossaryTerm,
    Summary,
    generate_summary,
    parse_summary,
    render_markdown,
    save as save_summary,
)


class FakeAI:
    """Modelo dublê: devolve o JSON que mandarmos."""

    name = "fake"
    used = "fake"

    def __init__(self, payload: str | dict) -> None:
        if isinstance(payload, dict):
            payload = json.dumps(payload, ensure_ascii=False)
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def complete(self, prompt: str, system_prompt: str | None = None, json_mode: bool = True) -> str:
        self.calls.append((system_prompt or "", prompt))
        return self.payload


def material(paragrafos: int = 14) -> str:
    return " ".join(
        f"A contabilidade gerencial usa o custeio variável para apoiar decisões de "
        f"preço e mix de produtos, diferente do custeio por absorção. Parágrafo {i}."
        for i in range(paragrafos)
    )


RESPOSTA_BOA = {
    "topics": [
        "O custeio variável separa custos fixos e variáveis para apoiar decisões de preço.",
        "A margem de contribuição indica quanto cada produto contribui para cobrir os custos fixos.",
        "Introdução",
        "O texto aborda a importância da contabilidade gerencial.",
    ],
    "glossary": [
        {"term": "Custeio variável", "definition": "Método que apropria apenas os custos variáveis aos produtos."},
        {"term": "Margem de contribuição", "definition": "Diferença entre a receita e os custos variáveis do produto."},
        {"term": "Custeio por absorção", "definition": "Custeio por absorção."},
        {"term": "Ponto de equilíbrio", "definition": "Curta."},
    ],
}


def test_gera_topicos_e_glossario():
    summary, warnings = generate_summary(material(), FakeAI(RESPOSTA_BOA))

    assert len(summary.topics) == 2, "rótulo de seção e metacomentário devem cair"
    assert "custeio variável" in summary.topics[0].lower()
    assert [t.term for t in summary.glossary] == ["Custeio variável", "Margem de contribuição"]
    assert warnings == []


def test_respeita_limites_de_quantidade():
    payload = {
        "topics": [f"O custeio variável aparece no parágrafo {i} do material." for i in range(20)],
        "glossary": [
            {"term": f"Termo {i}", "definition": "Definição técnica com mais de quatro palavras."}
            for i in range(20)
        ],
    }
    summary, _ = generate_summary(material(), FakeAI(payload), max_topics=3, max_terms=5)

    assert len(summary.topics) == 3
    assert len(summary.glossary) == 5


def test_remove_duplicatas():
    payload = {
        "topics": [
            "O custeio variável separa custos fixos dos variáveis na formação do preço.",
            "O custeio variável separa custos fixos dos variáveis na formação do preço.",
            "A margem de contribuição mostra o quanto sobra para cobrir os custos fixos.",
        ],
        "glossary": [
            {"term": "Custeio", "definition": "Método de apropriação de custos aos produtos."},
            {"term": "custeio", "definition": "Outra definição igualmente válida e completa."},
        ],
    }
    summary, _ = generate_summary(material(), FakeAI(payload))

    assert len(summary.topics) == 2
    assert len(summary.glossary) == 1


def test_material_curto_nao_chama_o_modelo():
    ai = FakeAI(RESPOSTA_BOA)
    summary, warnings = generate_summary("Texto curto.", ai)

    assert summary.topics == []
    assert ai.calls == []
    assert any("curto demais" in aviso for aviso in warnings)


def test_resposta_fora_do_formato_devolve_aviso():
    summary, warnings = generate_summary(material(), FakeAI("desculpe, não entendi"))

    assert summary.topics == []
    assert any("não devolveu conteúdo" in aviso for aviso in warnings)


def test_json_vazio_e_resposta_valida():
    summary, warnings = generate_summary(material(), FakeAI({"topics": [], "glossary": []}))

    assert summary.topics == []
    assert summary.glossary == []
    assert any("Resumo vazio" in aviso for aviso in warnings)


def test_parse_summary_aceita_json_cercado_de_texto():
    payload = parse_summary('aqui vai:\n```json\n{"topics": ["a"], "glossary": []}\n```\nfim')
    assert payload["topics"] == ["a"]


def test_prompt_cita_disciplina_e_limites():
    ai = FakeAI(RESPOSTA_BOA)
    generate_summary(material(), ai, title="Aula 3", discipline="Marketing", max_topics=7, max_terms=4)
    system, user = ai.calls[0]

    assert "Marketing" in user
    assert "Aula 3" in user
    assert "7 tópicos" in user and "4 termos" in user
    assert "AFIRMAÇÃO COMPLETA" in system


def test_render_markdown_traz_as_duas_secoes():
    summary = Summary(
        title="Aula 3",
        discipline="Contabilidade",
        topics=["O custeio variável separa custos fixos e variáveis."],
        glossary=[GlossaryTerm("Custeio", "Método de apropriação de custos.")],
    )
    markdown = render_markdown(summary)

    assert "## Tópicos" in markdown
    assert "## Glossário" in markdown
    assert "- Disciplina: Contabilidade" in markdown


def test_save_nao_sobrescreve(tmp_path):
    summary = Summary(title="Aula 3", topics=["O custeio variável separa custos fixos."])
    alvo = str(tmp_path / "resumo.md")

    primeiro = save_summary(summary, alvo)
    segundo = save_summary(summary, alvo)

    assert primeiro == alvo
    assert segundo == str(tmp_path / "resumo-2.md")
    assert os.path.exists(primeiro) and os.path.exists(segundo)


def test_cli_summarize_gera_e_salva(monkeypatch, capsys, tmp_path):
    modulo = importlib.import_module("zorin_copilot.core.study_summary")
    monkeypatch.setattr(modulo, "StudyAI", lambda *a, **k: FakeAI(RESPOSTA_BOA))

    origem = tmp_path / "aula3.md"
    origem.write_text(
        "# Contabilidade Gerencial\n\n- Palavras: 300\n- Disciplina: Marketing\n\n---\n\n" + material(),
        encoding="utf-8",
    )

    code = cli.main(["study", "summarize", "--from", str(origem), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["discipline"] == "Marketing"  # herdada da captura
    assert len(payload["topics"]) == 2
    assert payload["path"].endswith("resumo-aula3.md")
    assert os.path.exists(payload["path"])
