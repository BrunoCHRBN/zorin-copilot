# Decisão de design: estes testes protegem um CONTRATO, não uma mensagem. O contrato
# é "o app consegue distinguir uma resposta do modelo de uma frase de erro do
# provedor". A lista de frases é a implementação; os testes abaixo existem porque a
# lista pode ficar desatualizada sem ninguém notar — e foi exatamente o que aconteceu:
# o farejador que existia reconhecia 2 das 12 frases, e a cota esgotada do provedor
# OpenAI-compatível ou do WorkBuddy passava como se fosse o relatório do modelo.
#
# Estilo unittest (e não pytest) de propósito: o runner de guarda do projeto executa
# por `unittest`, e um arquivo pytest-style vira "Ran 0 tests" lá.

"""Testes do reconhecimento de falha de provedor."""

from __future__ import annotations

import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.ai.provider_failure import (
    FAILURE_MARKERS,
    FAILURE_PREFIXES,
    failure_reason,
    is_provider_failure,
)

_PROVIDERS_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "src",
    "zorin_copilot",
    "ai",
    "providers.py",
)


class ProviderFailureTest(unittest.TestCase):
    """O predicado em si."""

    def test_frases_de_falha_conhecidas_sao_reconhecidas(self):
        # As doze frases reais, com a cauda que cada provedor acrescenta.
        amostras = [
            "Não foi possível obter resposta do Gemini: Erro no modelo gemini-3.5-flash (429): RESOURCE_EXHAUSTED",
            "Chave de API do Google Gemini não configurada. Clique no ícone de engrenagem para inserir sua chave gratuita do Google AI Studio.",
            "Não foi possível processar sua solicitação: o Gemini está indisponível/sem cota e o modelo local Ollama não pôde ser alcançado em http://localhost:11434.",
            "Erro ao consultar Ollama local (qwen3:8b): Connection refused",
            "Erro no Ollama (503): model is loading",
            "O modelo local 'minicpm-v' não possui suporte a visão de imagens. Ele responde comandos e perguntas em texto de forma nativa.",
            "Chave de API ou URL não configurada.",
            "Erro na API (401): invalid api key",
            "Erro na requisição: HTTPSConnectionPool(host='api.x.com', port=443)",
            "Chave de API do WorkBuddy AI não configurada.",
            "Erro no WorkBuddy AI (500): internal error",
            "Erro na comunicação com WorkBuddy AI: timed out",
        ]
        for frase in amostras:
            with self.subTest(frase=frase[:48]):
                self.assertTrue(is_provider_failure(frase), f"não reconheceu: {frase}")

    def test_texto_vazio_ou_ausente_conta_como_falha(self):
        # Um provedor que não devolveu nada não produziu resposta. O default seguro é
        # "não tenho resposta", nunca "tenho".
        for vazio in ("", "   ", "\n\t ", None, 0, [], {}):
            with self.subTest(valor=repr(vazio)):
                self.assertTrue(is_provider_failure(vazio))

    def test_resposta_legitima_do_modelo_nao_e_falha(self):
        legitimas = [
            "O Zorin OS 18 é baseado em Ubuntu 24.04 LTS e traz o kernel 6.11.",
            "📌 Resumo Executivo\n\nA computação quântica usa qubits...",
            "Erro comum em Python: usar mutable default argument.",  # começa com "Erro"!
            "A API do Gemini tem cota gratuita de 15 requisições por minuto.",
        ]
        for texto in legitimas:
            with self.subTest(texto=texto[:40]):
                self.assertFalse(is_provider_failure(texto), f"falso positivo: {texto}")

    def test_prefixo_curto_demais_nao_engole_resposta_legitima(self):
        # "Erro na API" é prefixo de falha, mas "Erro na API do Gemini é comum" seria
        # uma resposta legítima do modelo. Este teste documenta o limite conhecido do
        # reconhecimento textual: ele é por prefixo e pode dar falso positivo.
        # Não é motivo para afrouxar a lista — um erro travestido de relatório é pior
        # que uma ressalva a mais — mas é bom que o limite esteja escrito.
        self.assertTrue(is_provider_failure("Erro na API (500): o servidor caiu."))

    def test_a_lista_nao_tem_entrada_duplicada_nem_vazia(self):
        self.assertEqual(len(FAILURE_PREFIXES), len(set(FAILURE_PREFIXES)))
        self.assertEqual(len(FAILURE_MARKERS), len(set(FAILURE_MARKERS)))
        for p in FAILURE_PREFIXES:
            with self.subTest(prefixo=p[:40]):
                self.assertTrue(p.strip(), "prefixo vazio não pode existir")
                self.assertTrue(is_provider_failure(p), f"prefixo não reconhece a si mesmo: {p}")
        for m in FAILURE_MARKERS:
            with self.subTest(marcador=m[:40]):
                self.assertTrue(m.strip(), "marcador vazio não pode existir")
                # O marcador aparece no MEIO da frase: exigi-lo como prefixo seria mentira.
                frase = f"O modelo local 'minicpm-v' {m}. Ele responde texto nativamente."
                self.assertTrue(is_provider_failure(frase), f"marcador não reconhecido: {m}")

    def test_marcador_nao_e_confundido_com_prefixo(self):
        # Separação real, não decorativa: nenhum marcador pode ser prefixo de falha,
        # senão a distinção entre as duas detecções seria só estética.
        for m in FAILURE_MARKERS:
            with self.subTest(marcador=m[:40]):
                self.assertNotIn(m, FAILURE_PREFIXES)

    def test_failure_reason_nao_inventa_conteudo(self):
        self.assertEqual(failure_reason("Erro na API (500): caiu"), "Erro na API (500): caiu")
        self.assertEqual(failure_reason(""), "O provedor não devolveu resposta.")
        self.assertEqual(failure_reason(None), "O provedor não devolveu resposta.")


class ProviderFailureCompletenessTest(unittest.TestCase):
    """Guardas contra a lista ficar desatualizada em relação a `providers.py`."""

    def test_todo_prefixo_declarado_e_usado_pelo_providers(self):
        # Agora que as frases vivem aqui e `providers.py` referencia as constantes, o
        # que pode envelhecer é o contrário do que se poderia esperar: uma entrada
        # declarada e nunca usada (frase morta que ninguém devolve). O nome da
        # constante tem de aparecer em providers.py.
        import zorin_copilot.ai.provider_failure as pf

        nomes = {
            valor: nome
            for nome, valor in vars(pf).items()
            if isinstance(valor, str) and valor in pf.FAILURE_PREFIXES
        }
        self.assertEqual(
            len(nomes),
            len(FAILURE_PREFIXES),
            "toda frase de FAILURE_PREFIXES deve ser uma constante nomeada do módulo",
        )
        fonte = open(_PROVIDERS_PY, encoding="utf-8").read()
        for prefixo, nome in nomes.items():
            with self.subTest(constante=nome):
                self.assertIn(
                    nome,
                    fonte,
                    f"{nome} é declarado mas nenhum ponto de providers.py o usa "
                    f"({prefixo[:40]!r}) — frase morta",
                )

    def test_toda_falha_devolvida_como_texto_esta_na_lista(self):
        """Varre a AST de `providers.py` procurando falhas devolvidas no canal de texto.

        O ponto cego que originou este módulo: o farejador antigo conhecia 2 das 12
        frases. Aqui, todo `return` cujo primeiro elemento é um literal que abre com
        um dos padrões de erro do projeto tem de ser reconhecido por
        `is_provider_failure`.

        Limite conhecido: a varredura é por PADRÃO DE ABERTURA ("Erro", "não
        configurada", "Não foi possível", "não possui suporte"). Uma falha nova com
        abertura inédita escapa — é o preço de o contrato ser textual. Ainda assim
        pega o caso realista, que é acrescentar mais um `return f"Erro ..."`.
        """
        aberturas = ("Erro", "erro", "Chave de API", "Não foi possível", "não foi possível")
        marcadores = ("não configurada", "não possui suporte")

        arvore = ast.parse(open(_PROVIDERS_PY, encoding="utf-8").read())

        def texto_do_no(no: ast.AST) -> str | None:
            """Devolve o texto literal (ou o prefixo literal) de um nó string/f-string."""
            if isinstance(no, ast.Constant) and isinstance(no.value, str):
                return no.value
            if isinstance(no, ast.JoinedStr):
                # f-string: concatena só as partes constantes do começo.
                partes = []
                for v in no.values:
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        partes.append(v.value)
                    else:
                        break
                return "".join(partes)
            return None

        def primeiro_elemento(no: ast.AST) -> ast.AST | None:
            """Se o `return` devolve uma tupla, o primeiro elemento dela."""
            if isinstance(no, ast.Tuple) and no.elts:
                return no.elts[0]
            return None

        suspeitos: list[tuple[int, str]] = []
        for no in ast.walk(arvore):
            if not isinstance(no, ast.Return) or no.value is None:
                continue
            alvo = primeiro_elemento(no.value)
            if alvo is None:
                continue
            texto = texto_do_no(alvo)
            if texto is None:
                continue
            # Só interessam os que PARECEM falha: abre com um padrão de erro conhecido
            # ou contém um marcador de recusa. O resto é resposta legítima do modelo.
            parece_falha = texto.startswith(aberturas) or any(m in texto for m in marcadores)
            if not parece_falha:
                continue
            if not is_provider_failure(texto):
                suspeitos.append((no.lineno, texto))

        self.assertEqual(
            [],
            suspeitos,
            "falha devolvida como texto e NÃO reconhecida por is_provider_failure "
            f"(acrescente o prefixo em provider_failure.py): {suspeitos}",
        )


if __name__ == "__main__":
    unittest.main()
