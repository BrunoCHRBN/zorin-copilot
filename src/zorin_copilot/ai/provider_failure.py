# Decisão de design: neste projeto um provedor sinaliza falha de DUAS maneiras
# diferentes — levantando exceção (`GeminiProvider.complete`) ou devolvendo a frase
# de erro NO LUGAR da resposta (`chat()`, em todos os cinco provedores concretos).
# A segunda é a perigosa: quem chama recebe uma `str`, não um erro, e nada no tipo
# avisa que aquilo não é conteúdo do modelo.
#
# Este módulo é a fonte única dessas frases. Antes dele as sentenças estavam
# copiadas em oito pontos de `providers.py` e farejadas por `startswith` em dois —
# e esses dois farejavam apenas DUAS das doze. Ou seja: o failover do HybridProvider
# e a síntese da pesquisa profunda reconheciam falha do Gemini, mas tratavam uma
# cota esgotada do provedor OpenAI-compatível ou do WorkBuddy como se fosse o
# relatório do modelo. Nenhum teste ficava vermelho.
#
# O que o módulo NÃO faz: decidir o que o chamador deve fazer com a falha. Ele só
# responde "isto é uma resposta ou é um erro?".
#
# LIMITE CONHECIDO: o reconhecimento é textual porque o contrato de `chat()` é
# textual. A alternativa correta seria `chat()` sinalizar falha na estrutura (exceção
# ou sentinela), mas isso muda a assinatura consumida por todo o app. Enquanto o
# contrato for este, `tests/test_provider_failure.py` guarda a lista contra
# esquecimento varrendo os `return` de `providers.py` pela AST.

"""Reconhecimento da frase de falha que os provedores devolvem no lugar da resposta."""

from __future__ import annotations

#: Gemini sem resposta: a cadeia de fallback de modelos se esgotou (cota 429,
#: 503 de alta demanda, timeout). `GeminiProvider.chat`.
GEMINI_UNAVAILABLE_PREFIX = "Não foi possível obter resposta do Gemini:"

#: Gemini sem chave configurada. `GeminiProvider.chat` devolve; `complete` levanta.
GEMINI_KEY_MISSING_PREFIX = "Chave de API do Google Gemini não configurada"

#: O provedor híbrido tentou Gemini e Ollama e não alcançou nenhum dos dois.
PROVIDER_UNAVAILABLE_PREFIX = "Não foi possível processar sua solicitação:"

#: Ollama local inalcançável (serviço fora do ar, modelo não baixado).
OLLAMA_ERROR_PREFIX = "Erro ao consultar Ollama"

#: Ollama respondeu com status de erro. Frase diferente da anterior de propósito:
#: aqui o serviço está no ar e recusou; lá ele não foi alcançado.
OLLAMA_HTTP_ERROR_PREFIX = "Erro no Ollama"

#: Recusa de capacidade, não erro de transporte: o modelo local não vê imagens.
#: Também não é resposta do modelo — é o app falando.
#:
#: Esta NÃO é um prefixo: a frase começa com "O modelo local '<nome>' ...". Por isso
#: vive em `FAILURE_MARKERS`, procurada em qualquer posição.
OLLAMA_VISION_UNSUPPORTED_MARKER = "não possui suporte a visão de imagens"

#: Provedor compatível com a API da OpenAI (OpenRouter, Groq, llama.cpp, LM Studio).
OPENAI_COMPAT_KEY_MISSING_PREFIX = "Chave de API ou URL não configurada"
OPENAI_COMPAT_HTTP_ERROR_PREFIX = "Erro na API"
OPENAI_COMPAT_REQUEST_ERROR_PREFIX = "Erro na requisição:"

#: Provedor WorkBuddy.
WORKBUDDY_KEY_MISSING_PREFIX = "Chave de API do WorkBuddy AI não configurada"
WORKBUDDY_HTTP_ERROR_PREFIX = "Erro no WorkBuddy AI"
WORKBUDDY_COMM_ERROR_PREFIX = "Erro na comunicação com WorkBuddy AI:"

#: Falhas que ABREM a mensagem. Comparação por prefixo, então a cauda (status HTTP,
#: nome do modelo, exceção original) pode variar à vontade.
#:
#: As onze vêm de `providers.py`; qualquer uma que falte aqui vira um erro
#: travestido de resposta no consumidor.
FAILURE_PREFIXES: tuple[str, ...] = (
    GEMINI_UNAVAILABLE_PREFIX,
    GEMINI_KEY_MISSING_PREFIX,
    PROVIDER_UNAVAILABLE_PREFIX,
    OLLAMA_ERROR_PREFIX,
    OLLAMA_HTTP_ERROR_PREFIX,
    OPENAI_COMPAT_KEY_MISSING_PREFIX,
    OPENAI_COMPAT_HTTP_ERROR_PREFIX,
    OPENAI_COMPAT_REQUEST_ERROR_PREFIX,
    WORKBUDDY_KEY_MISSING_PREFIX,
    WORKBUDDY_HTTP_ERROR_PREFIX,
    WORKBUDDY_COMM_ERROR_PREFIX,
)

#: Falhas que aparecem em qualquer posição da mensagem. Existem porque nem toda
#: recusa do app começa com o motivo — a de visão começa nomeando o modelo.
FAILURE_MARKERS: tuple[str, ...] = (OLLAMA_VISION_UNSUPPORTED_MARKER,)


def is_provider_failure(text: object) -> bool:
    """Diz se `text` é uma falha de provedor em vez de uma resposta do modelo.

    Duas detecções: `FAILURE_PREFIXES` (a mensagem abre com o motivo) e
    `FAILURE_MARKERS` (o motivo aparece no meio). Separadas de propósito — tratar a
    recusa de visão como prefixo seria mentira, e a diferença importa para quem for
    acrescentar a próxima frase.

    Texto vazio conta como falha: um provedor que não devolveu nada não produziu
    resposta. É a mesma escolha que `HybridProvider._is_gemini_error` já fazia para
    o caso do Gemini, agora valendo para todos os provedores.

    Aceita `object` de propósito: o chamador costuma estar segurando o resultado de
    um `chat()` que pode não ter devolvido `str` (dublê, `None`, tupla no lugar do
    texto). Qualquer coisa que não seja uma string com conteúdo é falha — o default
    seguro é "não tenho resposta", nunca "tenho".
    """
    if not isinstance(text, str):
        return True
    clean = text.strip()
    if not clean:
        return True
    if clean.startswith(FAILURE_PREFIXES):
        return True
    return any(marcador in clean for marcador in FAILURE_MARKERS)


def failure_reason(text: object) -> str:
    """Devolve a frase de falha pronta para log/exibição, sem inventar conteúdo.

    Usado por quem precisa registrar *por que* a síntese não aconteceu sem
    despejar a resposta inteira no log.
    """
    if isinstance(text, str) and text.strip():
        return text.strip()
    return "O provedor não devolveu resposta."
