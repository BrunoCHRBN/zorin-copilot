# Decisão de design: o modo tutor é a única superfície onde a resposta "ruim"
# (sem solução pronta) é a correta — os testes trancam o contrato socrático:
# assinatura exata do complete(), contexto determinístico por palavra-chave e
# injeção do TUTOR_ADDON no prompt do HUD apenas quando a config está ligada.

"""Testes do modo tutor: `study ask` + injeção no prompt do chat/HUD."""

from __future__ import annotations

import importlib
import json
import os

from zorin_copilot import cli
from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.core.desktop import env as env_mod
from zorin_copilot.core.study_tutor import (
    TUTOR_ADDON,
    TUTOR_SYSTEM_PROMPT,
    _keywords,
    answer,
    load_context,
)


class FakeAI:
    """Grava os argumentos de complete() e devolve uma resposta fixa."""

    used = "fake"

    def __init__(self, resposta: str = "Boa pergunta! O que você já sabe sobre custos fixos?"):
        self.resposta = resposta
        self.calls: list[dict] = []

    def complete(self, prompt, system_prompt=None, json_mode=True):
        self.calls.append(
            {"prompt": prompt, "system_prompt": system_prompt, "json_mode": json_mode}
        )
        return self.resposta


class ExplodingAI:
    used = "exploding"

    def complete(self, *args, **kwargs):
        raise RuntimeError("modelo fora do ar")


def _paragrafo(texto: str) -> str:
    """Garante parágrafo >= 80 chars (limiar de _paragraphs)."""
    return texto if len(texto) >= 80 else texto + " " + "conteúdo complementar da aula." * 3


def _grava(base, nome, texto, subpasta=None):
    pasta = os.path.join(str(base), subpasta) if subpasta else str(base)
    os.makedirs(pasta, exist_ok=True)
    caminho = os.path.join(pasta, nome)
    with open(caminho, "w", encoding="utf-8") as handle:
        handle.write(texto)
    return caminho


# --- _keywords ---------------------------------------------------------------


def test_keywords_removem_stopwords_e_palavras_curtas():
    chaves = _keywords("Como calculo o ponto de equilíbrio da empresa?")

    assert "calculo" in chaves
    assert "ponto" in chaves
    assert "equilíbrio" in chaves
    assert "empresa" in chaves
    assert "como" not in chaves
    assert "da" not in chaves


# --- load_context ------------------------------------------------------------


def test_load_context_escolhe_paragrafo_relevante(tmp_path):
    _grava(
        tmp_path,
        "marketing.md",
        _paragrafo("A elasticidade-preço da demanda mede a reação da quantidade "
                   "demandada quando o preço varia, mantidos os demais fatores."),
    )
    _grava(
        tmp_path,
        "logistica.md",
        _paragrafo("O picklist separa os itens do pedido no armazém seguindo a rota "
                   "de coleta definida pelo sistema WMS."),
    )

    contexto = load_context("o que é elasticidade-preço?", str(tmp_path))

    assert len(contexto.files) == 1
    assert contexto.files[0].endswith("marketing.md")
    assert "elasticidade" in contexto.excerpts[0]


def test_load_context_pasta_da_disciplina_ganha_o_empate(tmp_path):
    externo = _grava(tmp_path, "geral.md", _paragrafo("O markup define o preço de venda."))
    interno = _grava(
        tmp_path, "aula.md", _paragrafo("O markup define o preço de venda."),
        subpasta="gestao-comercial",
    )

    contexto = load_context("o que é markup?", str(tmp_path), discipline="Gestão Comercial")

    assert contexto.files[0] == interno
    assert externo in contexto.files


def test_load_context_diretorio_inexistente_devolve_vazio(tmp_path):
    contexto = load_context("qualquer coisa", str(tmp_path / "nao-existe"))

    assert contexto.excerpts == []
    assert contexto.files == []
    assert contexto.to_text() == ""


def test_load_context_respeita_orcamento_de_caracteres(tmp_path):
    _grava(tmp_path, "aula.md", _paragrafo("O ponto de equilíbrio aparece aqui. " * 6))

    contexto = load_context("ponto de equilíbrio", str(tmp_path), max_chars=100)

    assert contexto.excerpts
    assert sum(len(trecho) for trecho in contexto.excerpts) <= 100


# --- answer ------------------------------------------------------------------


def test_answer_chama_complete_em_modo_texto_livre(tmp_path):
    ai = FakeAI()

    texto, avisos = answer(
        "como calculo o ponto de equilíbrio?",
        ai,
        discipline="Contabilidade",
        documents_dir=str(tmp_path),
    )

    assert texto == ai.resposta
    assert len(ai.calls) == 1
    chamada = ai.calls[0]
    assert chamada["system_prompt"] is TUTOR_SYSTEM_PROMPT
    assert chamada["json_mode"] is False  # tutor escreve prosa, não payload
    assert "ponto de equilíbrio" in chamada["prompt"]
    assert "Contabilidade" in chamada["prompt"]


def test_answer_avisa_quando_material_nao_conversa_com_a_pergunta(tmp_path):
    ai = FakeAI()

    _, avisos = answer("dúvida sem material", ai, documents_dir=str(tmp_path))

    assert any("Nenhum material" in aviso for aviso in avisos)


def test_answer_pergunta_vazia_nem_consulta_o_modelo():
    ai = FakeAI()

    texto, avisos = answer("   ", ai)

    assert texto == ""
    assert avisos
    assert ai.calls == []


def test_answer_falha_do_modelo_vira_aviso():
    texto, avisos = answer("pergunta", ExplodingAI(), include_context=False)

    assert texto == ""
    assert any("Falha" in aviso for aviso in avisos)


# --- injeção no prompt do HUD -------------------------------------------------


def _env_zorin():
    return env_mod.Environment(
        distro=env_mod.DistroInfo(id="zorin", pretty_name="Zorin OS 18 Core"),
        session_type="wayland",
        desktop="gnome",
    )


def test_prompt_hud_ganha_addon_quando_tutor_ligado(monkeypatch):
    from zorin_copilot.ai import providers

    cfg = CopilotConfig(study_tutor=True)
    monkeypatch.setattr(CopilotConfig, "load", classmethod(lambda cls: cfg))

    prompt = providers.build_system_prompt(_env_zorin())

    assert TUTOR_ADDON in prompt
    assert "PERSONALIDADE & TOM DE VOZ" in prompt  # prompt de desktop continua intacto


def test_prompt_hud_fica_intocado_quando_tutor_desligado(monkeypatch):
    from zorin_copilot.ai import providers

    cfg = CopilotConfig(study_tutor=False)
    monkeypatch.setattr(CopilotConfig, "load", classmethod(lambda cls: cfg))

    prompt = providers.build_system_prompt(_env_zorin())

    assert TUTOR_ADDON not in prompt


def test_prompt_hud_sobrevive_a_config_quebrada(monkeypatch):
    from zorin_copilot.ai import providers

    def _boom(cls):
        raise RuntimeError("config corrompida")

    monkeypatch.setattr(CopilotConfig, "load", classmethod(_boom))

    prompt = providers.build_system_prompt(_env_zorin())

    assert "PERSONALIDADE & TOM DE VOZ" in prompt
    assert TUTOR_ADDON not in prompt


# --- CLI ---------------------------------------------------------------------


def test_cli_ask_devolve_resposta_do_tutor(monkeypatch, capsys):
    modulo = importlib.import_module("zorin_copilot.core.study_summary")
    monkeypatch.setattr(modulo, "build_default_ai", lambda *a, **k: FakeAI())

    code = cli.main([
        "study", "ask",
        "o que é ponto de equilíbrio?",
        "--discipline", "Contabilidade Gerencial",
        "--no-context",
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["answer"].startswith("Boa pergunta!")
    assert payload["provider"] == "fake"


def test_cli_ask_falha_do_modelo_retorna_1(monkeypatch, capsys):
    modulo = importlib.import_module("zorin_copilot.core.study_summary")
    monkeypatch.setattr(modulo, "build_default_ai", lambda *a, **k: ExplodingAI())

    code = cli.main(["study", "ask", "pergunta", "--no-context"])

    assert code == 1
    assert "não conseguiu responder" in capsys.readouterr().out


def test_cli_config_tutor_liga_e_desliga(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert cli.main(["config", "--tutor", "on"]) == 0
    assert "Modo tutor ativado" in capsys.readouterr().out
    assert CopilotConfig.load().study_tutor is True

    assert cli.main(["config", "--tutor", "off"]) == 0
    assert "Modo tutor desativado" in capsys.readouterr().out
    assert CopilotConfig.load().study_tutor is False
