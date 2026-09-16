# Decisão de design: os testes cobrem as três coisas que já deram errado em uso real:
#   1. prompt v1 gerando card trivial (o filtro de utilidade e o gate de material curto);
#   2. regenerar deck zerando o histórico de revisão (merge por id);
#   3. agendamento SM-2 errado (nota + estado = data, determinístico).
# Nenhum teste fala com modelo ou rede: o modelo entra como dublê.

"""Testes dos flashcards e da revisão espaçada (SM-2)."""

from __future__ import annotations

import importlib
import json
from datetime import date, timedelta

import pytest

try:
    import docx
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

from zorin_copilot import cli
from zorin_copilot.core.study_cards import (
    GRADE_SCALE,
    MIN_SOURCE_WORDS,
    Card,
    Deck,
    DeckStore,
    StudyAI,
    content_hash,
    deck_stats,
    due_cards,
    generate_cards,
    grade_card,
    initial_state,
    make_card,
    merge_cards,
    new_deck,
    parse_cards,
    read_discipline,
    read_source,
)

HOJE = date(2026, 9, 15)


class FakeAI:
    """Modelo dublê: devolve o JSON que mandarmos."""

    name = "fake"

    def __init__(self, payload: str | dict) -> None:
        if isinstance(payload, dict):
            payload = json.dumps(payload, ensure_ascii=False)
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def complete(self, prompt: str, system_prompt: str | None = None, json_mode: bool = True) -> str:
        # Mesma assinatura de `ai.providers` para o dublê não esconder desvios.
        self.calls.append((system_prompt or "", prompt))
        return self.payload


def material_longo(paragrafos: int = 12) -> str:
    return " ".join(
        f"Este é o parágrafo número {i} sobre contabilidade gerencial e gestão comercial "
        "com conteúdo suficiente para sustentar a geração de flashcards."  # ~20 palavras
        for i in range(paragrafos)
    )


CARDS_JSON = {
    "cards": [
        {
            "front": "O que é contabilidade gerencial?",
            "back": "É o ramo voltado a informações para decisões internas.",
            "tags": ["contabilidade"],
        },
        {
            "front": "Qual a diferença entre custeio por absorção e variável?",
            "back": "O absorção rateia todos os custos; o variável só os variáveis.",
            "tags": ["custeio"],
        },
    ]
}


# --------------------------------------------------------------------------- #
# Identidade e criação
# --------------------------------------------------------------------------- #


def test_content_hash_e_estavel_e_normaliza():
    assert content_hash("Olá  Mundo") == content_hash("olá mundo")


def test_make_card_id_deriva_da_frente_e_vence_hoje():
    card = make_card("Pergunta?", "Resposta.", ["tag"], today=HOJE)
    assert card.id == content_hash("Pergunta?")
    assert card.next_review == HOJE.isoformat()
    assert card.easiness == 2.5
    assert card.repetitions == 0
    assert card.is_due(HOJE)


# --------------------------------------------------------------------------- #
# SM-2
# --------------------------------------------------------------------------- #


def test_primeiro_acerto_agenda_um_dia():
    state = grade_card(initial_state(HOJE), 5, today=HOJE)
    assert state["repetitions"] == 1
    assert state["interval_days"] == 1
    assert state["next_review"] == (HOJE + timedelta(days=1)).isoformat()


def test_segundo_acerto_agenda_seis_dias():
    first = grade_card(initial_state(HOJE), 4, today=HOJE)
    second = grade_card(first, 4, today=HOJE + timedelta(days=1))
    assert second["interval_days"] == 6


def test_terceiro_acerto_usa_fator_de_facilidade():
    state = initial_state(HOJE)
    state["repetitions"] = 2
    state["interval_days"] = 6
    state = grade_card(state, 5, today=HOJE)
    assert state["interval_days"] == round(6 * state["easiness"])


def test_nota_abaixo_de_tres_zera_repeticoes():
    state = initial_state(HOJE)
    state["repetitions"] = 4
    state["interval_days"] = 20
    state = grade_card(state, 1, today=HOJE)
    assert state["repetitions"] == 0
    assert state["interval_days"] == 1
    assert state["next_review"] == (HOJE + timedelta(days=1)).isoformat()


def test_facilidade_tem_piso():
    state = initial_state(HOJE)
    for _ in range(12):
        state = grade_card(state, 0, today=HOJE)
    assert state["easiness"] >= 1.3


def test_nota_fora_da_escala_levanta():
    with pytest.raises(ValueError):
        grade_card(initial_state(HOJE), 7, today=HOJE)
    with pytest.raises(ValueError):
        grade_card(initial_state(HOJE), True, today=HOJE)


def test_card_apply_grade_atualiza_o_proprio_estado():
    card = make_card("P?", "R.", today=HOJE)
    card.apply_grade(5, today=HOJE)
    assert card.repetitions == 1
    assert card.interval_days == 1
    assert card.is_due(HOJE) is False


def test_escala_de_notas_tem_seis_niveis():
    assert [n for n, _ in GRADE_SCALE] == [0, 1, 2, 3, 4, 5]


# --------------------------------------------------------------------------- #
# Fila e estatísticas
# --------------------------------------------------------------------------- #


def test_due_cards_traz_novos_primeiro_e_respeita_limite():
    novo = make_card("novo?", "r", today=HOJE)
    antigo = make_card("antigo?", "r", today=HOJE)
    antigo.apply_grade(5, today=HOJE)
    deck = new_deck("t", [antigo, novo])

    fila = due_cards(deck, HOJE)
    assert fila[0].id == novo.id  # novos primeiro
    assert antigo not in fila  # acabou de revisar, vence amanhã
    assert len(due_cards(deck, HOJE + timedelta(days=2), limit=1)) == 1


def test_deck_stats_conta_por_estagio():
    cards = [make_card(f"p{i}?", "r", today=HOJE) for i in range(4)]
    for card in cards[:2]:
        card.apply_grade(5, today=HOJE)
    stats = deck_stats(new_deck("t", cards), HOJE)
    assert stats["total"] == 4
    assert stats["due"] == 2  # os dois não revisados ainda vencem hoje
    assert stats["new"] == 2
    assert stats["learning"] == 2


# --------------------------------------------------------------------------- #
# Geração
# --------------------------------------------------------------------------- #


def test_material_curto_nao_gera_cards():
    cards, warnings = generate_cards("texto curto", FakeAI(CARDS_JSON))
    assert cards == []
    assert any("curto demais" in w for w in warnings)
    assert str(MIN_SOURCE_WORDS) in warnings[0]


def test_geracao_com_modelo_duble():
    ai = FakeAI(CARDS_JSON)
    cards, warnings = generate_cards(material_longo(), ai, title="Aula 3", n_cards=5, today=HOJE)

    assert len(cards) == 2
    assert cards[0].front == "O que é contabilidade gerencial?"
    assert cards[0].tags == ["contabilidade"]
    assert warnings == []
    assert len(ai.calls) == 1


def test_prompt_cita_disciplina_e_limite_de_cards():
    ai = FakeAI(CARDS_JSON)
    generate_cards(material_longo(), ai, title="Aula 3", discipline="Marketing", n_cards=7)
    system, user = ai.calls[0]
    assert "Marketing" in user
    assert "7 flashcards" in user
    assert "NUNCA crie cards sobre dados burocráticos" in system


def test_resposta_fora_do_formato_devolve_aviso():
    cards, warnings = generate_cards(material_longo(), FakeAI("desculpe, não entendi"))
    assert cards == []
    assert any("não devolveu cards" in w for w in warnings)


def test_modelo_que_explode_nao_derruba():
    class Explode:
        def complete(self, system, user):
            raise RuntimeError("sem modelo")

    cards, warnings = generate_cards(material_longo(), Explode())
    assert cards == []
    assert "Falha ao consultar o modelo" in warnings[0]


def test_cards_triviais_sao_descartados():
    payload = {
        "cards": [
            {"front": "Código?", "back": "1234"},  # resposta só número
            {"front": "Qual é o código do curso de Ambientação EAD?", "back": "x y z"},  # curta demais
            {"front": "O que é mix de marketing?", "back": "Conjunto de variáveis controláveis de marketing."},
        ]
    }
    cards, _ = generate_cards(material_longo(), FakeAI(payload), today=HOJE)
    assert [c.front for c in cards] == ["O que é mix de marketing?"]


def test_cards_duplicados_aparecem_uma_vez():
    payload = {
        "cards": [
            {"front": "Qual é a pergunta duplicada?", "back": "Resposta completa aqui."},
            {"front": "Qual é a pergunta duplicada?", "back": "Resposta completa aqui."},
        ]
    }
    cards, _ = generate_cards(material_longo(), FakeAI(payload), today=HOJE)
    assert len(cards) == 1


def test_material_longo_e_cortado_com_aviso():
    ai = FakeAI(CARDS_JSON)
    _cards, warnings = generate_cards(material_longo(400), ai)
    assert any("cortado" in w for w in warnings)


def test_parse_cards_tolera_markdown_e_prosa():
    assert len(parse_cards('```json\n{"cards": [{"front": "a b c", "back": "d e"}]}\n```')) == 1
    assert len(parse_cards('Claro! {"cards": [{"front": "a b c", "back": "d e"}]}')) == 1
    assert parse_cards("nada") == []


# --------------------------------------------------------------------------- #
# Merge (carry-over de revisão)
# --------------------------------------------------------------------------- #


def test_merge_preserva_historico_de_cards_conhecidos():
    antigo = make_card("P?", "R.", today=HOJE)
    antigo.apply_grade(5, today=HOJE)
    novo = make_card("P?", "R.", today=HOJE)  # mesmo id, estado zerado

    merged = merge_cards([antigo], [novo])
    assert merged[0].repetitions == 1  # histórico do antigo venceu


def test_merge_mantem_cards_que_sairam_do_material_novo():
    antigo = make_card("Saiu?", "R.", today=HOJE)
    novo = make_card("Novo?", "R2.", today=HOJE)
    merged = merge_cards([antigo], [novo])
    assert {c.id for c in merged} == {antigo.id, novo.id}


# --------------------------------------------------------------------------- #
# Persistência
# --------------------------------------------------------------------------- #


def test_store_salva_carrega_e_lista(tmp_path):
    store = DeckStore(directory=str(tmp_path))
    deck = new_deck("Aula 3", [make_card("P?", "R.", today=HOJE)], source="/tmp/aula3.md")

    path = store.save(deck)
    assert path.endswith(f"{deck.id}.json")

    loaded = store.load(deck.id)
    assert loaded is not None
    assert loaded.title == "Aula 3"
    assert loaded.cards[0].front == "P?"

    assert [d.id for d in store.list()] == [deck.id]


def test_store_sem_diretorio_devolve_lista_vazia(tmp_path):
    assert DeckStore(directory=str(tmp_path / "nada")).list() == []


def test_store_deck_inexistente_devolve_none(tmp_path):
    assert DeckStore(directory=str(tmp_path)).load("nao-existe") is None


def test_roundtrip_de_deck_com_estado_sm2(tmp_path):
    store = DeckStore(directory=str(tmp_path))
    card = make_card("P?", "R.", today=HOJE)
    card.apply_grade(4, today=HOJE)
    store.save(new_deck("d", [card]))

    loaded = store.load(new_deck("d", [card]).id)
    assert loaded is not None
    assert loaded.cards[0].interval_days == card.interval_days
    assert loaded.cards[0].easiness == card.easiness


# --------------------------------------------------------------------------- #
# Leitura do material capturado
# --------------------------------------------------------------------------- #


def test_read_source_remove_cabecalho_de_metadados(tmp_path):
    path = tmp_path / "aula3.md"
    path.write_text(
        "# Contabilidade Gerencial\n"
        "\n"
        "- Capturado em: 2026-09-15 02:40\n"
        "- Origem: Firefox (atspi)\n"
        "- Palavras: 812\n"
        "\n"
        "---\n"
        "\n"
        "A contabilidade gerencial é o ramo voltado a decisões.\n",
        encoding="utf-8",
    )
    title, body = read_source(str(path))
    assert title == "Contabilidade Gerencial"
    assert body.startswith("A contabilidade gerencial")
    assert "Capturado em" not in body
    assert "---" not in body


def test_read_source_sem_cabecalho_devolve_tudo(tmp_path):
    path = tmp_path / "simples.md"
    path.write_text("Só texto corrido aqui.\n", encoding="utf-8")
    title, body = read_source(str(path))
    assert title == ""
    assert body == "Só texto corrido aqui."


# --------------------------------------------------------------------------- #
# StudyAI
# --------------------------------------------------------------------------- #


def test_studyai_com_provedor_injetado_nao_usarede():
    ai = StudyAI(provider=FakeAI('{"cards": []}'))
    assert ai.complete("sys", "usr") == '{"cards": []}'
    assert ai.used == "fake"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


class StubAI:
    """Substitui o StudyAI no comando `study deck`."""

    def __init__(self, *args, **kwargs) -> None:
        self.used = "fake"

    def complete(self, prompt, system_prompt=None, json_mode=True):
        return json.dumps(CARDS_JSON, ensure_ascii=False)


@pytest.fixture
def material_no_disco(tmp_path):
    path = tmp_path / "aula3.md"
    path.write_text(
        "# Contabilidade Gerencial\n\n- Palavras: 200\n\n---\n\n" + material_longo(),
        encoding="utf-8",
    )
    return path


def test_cli_study_deck_gera_e_salva(monkeypatch, capsys, tmp_path, material_no_disco):
    module = importlib.import_module("zorin_copilot.core.study_cards")
    monkeypatch.setattr(module, "DeckStore", lambda *a, **k: DeckStore(directory=str(tmp_path)))
    monkeypatch.setattr(module, "StudyAI", StubAI)

    code = cli.main(["study", "deck", "--from", str(material_no_disco), "--max-cards", "5"])
    out = capsys.readouterr().out

    assert code == 0
    assert "2 cards" in out
    assert "study review --deck" in out
    assert any(name.endswith(".json") for name in __import__("os").listdir(tmp_path))


def test_cli_study_deck_arquivo_inexistente(capsys):
    code = cli.main(["study", "deck", "--from", "/tmp/nao-existe.md"])
    assert code == 1
    assert "não encontrado" in capsys.readouterr().out


def test_cli_study_decks_lista_vencidos(monkeypatch, capsys, tmp_path):
    module = importlib.import_module("zorin_copilot.core.study_cards")
    monkeypatch.setattr(module, "DeckStore", lambda *a, **k: DeckStore(directory=str(tmp_path)))
    store = DeckStore(directory=str(tmp_path))
    store.save(new_deck("Aula 3", [make_card("P?", "R.", today=HOJE)], source="x"))

    code = cli.main(["study", "decks"])
    out = capsys.readouterr().out

    assert code == 0
    assert "Aula 3" in out
    assert "Total vencido agora: 1" in out


def test_cli_study_review_aplica_notas_e_salva(monkeypatch, capsys, tmp_path):
    module = importlib.import_module("zorin_copilot.core.study_cards")
    monkeypatch.setattr(module, "DeckStore", lambda *a, **k: DeckStore(directory=str(tmp_path)))

    card = make_card("P?", "R.", today=date.today())
    deck = new_deck("Aula 3", [card], source="x")
    DeckStore(directory=str(tmp_path)).save(deck)

    respostas = iter(["", "5"])  # Enter vê a resposta, depois nota 5
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(respostas))

    code = cli.main(["study", "review", "--deck", deck.id])
    out = capsys.readouterr().out

    assert code == 0
    assert "Revisados: 1" in out
    recarregado = DeckStore(directory=str(tmp_path)).load(deck.id)
    assert recarregado is not None
    assert recarregado.cards[0].repetitions == 1
    assert recarregado.cards[0].interval_days == 1


def test_cli_study_review_sem_baralho(monkeypatch, capsys, tmp_path):
    module = importlib.import_module("zorin_copilot.core.study_cards")
    monkeypatch.setattr(module, "DeckStore", lambda *a, **k: DeckStore(directory=str(tmp_path)))

    code = cli.main(["study", "review"])
    assert code == 1
    assert "Nenhum baralho" in capsys.readouterr().out


def test_cli_study_review_nota_invalida_encera_sem_quebrar(monkeypatch, capsys, tmp_path):
    module = importlib.import_module("zorin_copilot.core.study_cards")
    monkeypatch.setattr(module, "DeckStore", lambda *a, **k: DeckStore(directory=str(tmp_path)))

    deck = new_deck("Aula 3", [make_card("P?", "R.", today=date.today())], source="x")
    DeckStore(directory=str(tmp_path)).save(deck)

    respostas = iter(["", "9"])  # nota fora da escala
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(respostas))

    code = cli.main(["study", "review", "--deck", deck.id])
    assert code == 0
    assert "Fora da escala" in capsys.readouterr().out
    assert DeckStore(directory=str(tmp_path)).load(deck.id).cards[0].repetitions == 0


@pytest.mark.skipif(not HAS_DOCX, reason="python-docx não instalado")
def test_cli_study_abnt_gera_docx(tmp_path, capsys):
    source = tmp_path / "trabalho.md"
    source.write_text("# Trabalho\n\nIntrodução do trabalho acadêmico.\n", encoding="utf-8")
    out = tmp_path / "trabalho.docx"

    code = cli.main(["study", "abnt", "--from", str(source), "--out", str(out)])

    assert code == 0
    assert out.exists() and out.stat().st_size > 0
    assert "ABNT" in capsys.readouterr().out


def test_cli_study_abnt_arquivo_inexistente(capsys):
    code = cli.main(["study", "abnt", "--from", "/tmp/nao-existe.md"])
    assert code == 1


def test_parser_do_study_deck_e_review():
    args = cli.build_parser().parse_args(
        ["study", "deck", "--from", "aula.md", "--max-cards", "7", "--local-only"]
    )
    assert args.study_action == "deck"
    assert args.max_cards == 7
    assert args.local_only is True

    rev = cli.build_parser().parse_args(["study", "review", "--deck", "abc", "--limit", "5"])
    assert rev.study_action == "review"
    assert rev.deck == "abc"


def test_parser_de_busca_academica():
    args = cli.build_parser().parse_args(["search", "mercado", "--academic", "--source", "sebrae"])
    assert args.academic is True
    assert args.source == "sebrae"


# --------------------------------------------------------------------------- #
# A2: disciplina herdada da captura
# --------------------------------------------------------------------------- #


def material_com_disciplina(tmp_path, disciplina: str | None):
    path = tmp_path / "aula7.md"
    meta = f"- Disciplina: {disciplina}\n" if disciplina else ""
    path.write_text(
        f"# Contabilidade Gerencial\n\n- Palavras: 200\n{meta}\n---\n\n" + material_longo(),
        encoding="utf-8",
    )
    return path


def test_read_discipline_le_o_cabecalho(tmp_path):
    assert read_discipline(material_com_disciplina(tmp_path, "Marketing")) == "Marketing"


def test_read_discipline_sem_metadado_devolve_vazio(tmp_path):
    assert read_discipline(material_com_disciplina(tmp_path, None)) == ""


def test_baralho_herda_a_disciplina_da_captura(monkeypatch, capsys, tmp_path):
    module = importlib.import_module("zorin_copilot.core.study_cards")
    monkeypatch.setattr(module, "DeckStore", lambda *a, **k: DeckStore(directory=str(tmp_path)))
    monkeypatch.setattr(module, "StudyAI", StubAI)

    material = material_com_disciplina(tmp_path, "Contabilidade Gerencial")
    code = cli.main(["study", "deck", "--from", str(material), "--json"])
    deck = json.loads(capsys.readouterr().out)

    assert code == 0
    assert deck["discipline"] == "Contabilidade Gerencial"


def test_disciplina_explicita_ganha_da_captura(monkeypatch, capsys, tmp_path):
    module = importlib.import_module("zorin_copilot.core.study_cards")
    monkeypatch.setattr(module, "DeckStore", lambda *a, **k: DeckStore(directory=str(tmp_path)))
    monkeypatch.setattr(module, "StudyAI", StubAI)

    material = material_com_disciplina(tmp_path, "Contabilidade Gerencial")
    code = cli.main(["study", "deck", "--from", str(material), "--discipline", "Marketing", "--json"])
    deck = json.loads(capsys.readouterr().out)

    assert code == 0
    assert deck["discipline"] == "Marketing"


def test_decks_filtra_por_disciplina(monkeypatch, capsys, tmp_path):
    store = DeckStore(directory=str(tmp_path))
    store.save(new_deck("Aula de Marketing", [make_card("Pergunta?", "Resposta")], discipline="Marketing"))
    store.save(new_deck("Aula de Custos", [make_card("Pergunta?", "Resposta")], discipline="Contabilidade"))

    module = importlib.import_module("zorin_copilot.core.study_cards")
    monkeypatch.setattr(module, "DeckStore", lambda *a, **k: store)

    code = cli.main(["study", "decks", "--discipline", "Marketing"])
    out = capsys.readouterr().out

    assert code == 0
    assert "Marketing" in out
    assert "Contabilidade" not in out
