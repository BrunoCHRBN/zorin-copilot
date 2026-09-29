"""Identidade do assistente: **um** lugar que sabe quem ele é.

Módulo puro — sem GTK, sem rede, sem disco, sem relógio. Mesmo padrão de
`core/attention.py`, `core/ha_announce.py` e `core/ambient_events.py`, pelo mesmo
motivo: a regra tem de ser exercitável sem display.

## Por que esta peça existe (medido, não deduzido)

A identidade do assistente estava espalhada, e três pedaços dela **mentiam**:

1. **`config.system_prompt` era um campo morto.** `CopilotConfig.load()` o preenchia
   quando vazio, `save()` o persistia (via `asdict`), e **ninguém o lia**:
   `ai/providers.build_system_prompt()` sempre usava a constante `SYSTEM_PROMPT`.
   Quem editasse o campo no `config.json` não mudava nada — e ainda via o campo
   preenchido, o que sugere o contrário. Campo que mente é pior do que campo ausente.
2. **O nome do assistente aparecia em quatro lugares independentes**: o título da
   janela (`ui/app.py`, literal `"Zorin Copilot"`), duas vezes dentro do prompt de
   sistema, e o texto que `load()` gerava. Mudar o nome num deles não mudava os outros.
3. **A voz local do Piper baixava o modelo errado.** `ensure_model_available()`
   montava o caminho a partir de `voice_name` — o que o usuário escolheu — mas as
   URLs de download eram constantes do módulo, apontando **sempre** para o
   `pt_BR-faber-medium`. Pedir `pt_BR-cadu-medium` baixava o faber, salvava com o
   nome do cadu e registrava `Piper TTS carregado com sucesso: pt_BR-cadu-medium`.
   **O log confirmava a voz errada.** Por isso `voice_urls()` aqui é *derivada*: o
   caminho e a URL saem da mesma fonte, e uma voz inexistente dá 404 em vez de
   entregar outra voz em silêncio.

## O que **não** entrou, e por quê

- **"Tom" como chave de configuração.** O roteiro lista "tom" como parte da
  identidade. O tom, hoje, é *texto* dentro do prompt — e não há como verificar que
  trocar um enum mudou o comportamento do modelo. Um controle que não muda nada é
  pior do que um controle ausente (mesmo raciocínio de `attention_away_minutes`).
  Se um dia o tom virar grandeza mensurável, ele entra aqui.
- **As janelas de silêncio.** O roteiro também as lista aqui. Elas **ficam** com a
  política de atenção: são um parâmetro de *interrupção*, e a linha na tela mora ao
  lado dos anúncios que ela governa. Trazer o controle para a identidade separaria o
  botão do efeito dele — o oposto do que a Fase 0/P2 fez. Mesmo raciocínio que tirou
  `trust_level` de `decide_attention`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

#: Sentinela do nome dentro do texto base do prompt. ASCII e improvável de aparecer
#: por acidente, ao contrário de `{nome}` — o prompt tem 11 pares de chaves (exemplos
#: de JSON), então `str.format` está fora de questão aqui.
NAME_SENTINEL = "__ASSISTANT_NAME__"

#: Onde o texto base deixa de ser **persona** e passa a ser **diretriz técnica**.
#: Medido: o prompt tem dois blocos — `PERSONALIDADE & TOM DE VOZ` (linhas 4–17) e
#: `DIRETRIZES TÉCNICAS E DE RESPOSTA` (linhas 18–80), e é neste segundo que mora o
#: contrato de saída do agente (o esquema JSON, as regras de `explanation`, as
#: ferramentas). Por isso um prompt próprio do usuário substitui **só o primeiro**:
#: trocar o texto inteiro apagaria o esquema e o agente pararia de funcionar — sem
#: que o usuário tivesse como saber que a caixa "prompt" também era o contrato da API.
PERSONA_MARKER = "DIRETRIZES TÉCNICAS E DE RESPOSTA:"

#: Nome padrão. Também é o que o prompt histórico dizia, então com a identidade
#: default a composição sai **byte-idêntica** ao que já era enviado ao modelo.
DEFAULT_NAME = "Zorin Copilot"

#: Vozes pt_BR existentes no índice oficial do `rhasspy/piper-voices`
#: (`voices.json`, 177 vozes no total, **4** em pt_BR — medido em 2026-09-21).
#: A lista é curta de propósito: ela é a **oferta** da interface, não o limite do
#: formato. `voice_urls()` aceita qualquer chave bem formada das 177.
PT_BR_VOICES: tuple[tuple[str, str], ...] = (
    ("pt_BR-faber-medium", "Faber — médio (o padrão histórico)"),
    ("pt_BR-cadu-medium", "Cadu — médio"),
    ("pt_BR-jeff-medium", "Jeff — médio"),
    ("pt_BR-edresson-low", "Edresson — leve (mais rápido)"),
)

DEFAULT_LOCAL_VOICE = "pt_BR-faber-medium"

#: Voz do Gemini Live. O default repete o de `core/config.py` de propósito: um
#: teste compara os dois, porque duas fontes para o mesmo default divergem em silêncio.
DEFAULT_LIVE_VOICE = "Puck"

#: Raiz dos modelos Piper no Hugging Face. O caminho é
#: `{família}/{locale}/{voz}/{qualidade}/{chave}.onnx` — o mesmo que o `voices.json`
#: publica em `files`.
PIPER_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


@dataclass(frozen=True)
class VoiceSpec:
    """Uma voz Piper desmontada. Os quatro campos **são** o caminho do arquivo."""

    key: str
    family: str
    locale: str
    voice: str
    quality: str

    @property
    def directory(self) -> str:
        return f"{self.family}/{self.locale}/{self.voice}/{self.quality}"

    @property
    def model_url(self) -> str:
        return f"{PIPER_BASE_URL}/{self.directory}/{self.key}.onnx"

    @property
    def config_url(self) -> str:
        return f"{self.model_url}.json"


def parse_voice(key: Any) -> VoiceSpec | None:
    """Desmonta `pt_BR-faber-medium`. Devolve `None` se não casar o formato.

    O formato é sempre `{locale}-{voz}-{qualidade}`, e o `locale` sempre tem um `_`
    separando família e região. Medido no índice oficial: **as 177 vozes** têm
    exatamente 3 partes, então o formato é regra, não coincidência do faber.

    `None` em vez de exceção porque quem chama trata "voz desconhecida" como caso
    normal: cai no default e segue. Exceção aqui derrubaria a fala por causa de uma
    string de configuração.
    """
    if not isinstance(key, str):
        return None
    partes = key.strip().split("-")
    if len(partes) != 3:
        return None
    locale, voz, qualidade = (p.strip() for p in partes)
    if not locale or not voz or not qualidade:
        return None
    # Redundante **de propósito**: o `partition` logo abaixo já rejeita um locale sem
    # `_` (a região sai vazia). Medido: 324 entradas hostis, 0 divergências entre ter
    # e não ter esta linha. Fica porque ela diz a regra do formato, e a de baixo diz
    # que os dados não podem ser vazios — quem reescrever o split um dia precisa da
    # primeira. É a única linha deste módulo que a mutação classificou como
    # EQUIVALENTE, e o harness diz isso em vez de esconder.
    if "_" not in locale:
        return None
    familia, _, regiao = locale.partition("_")
    if not familia or not regiao:
        return None
    return VoiceSpec(
        key=f"{locale}-{voz}-{qualidade}",
        family=familia,
        locale=locale,
        voice=voz,
        quality=qualidade,
    )


def voice_urls(key: Any) -> tuple[str, str] | None:
    """`(url do .onnx, url do .json)` derivadas da voz — ou `None`.

    **É esta função que corrige o defeito medido.** Antes, o caminho do arquivo saía
    de `voice_name` e a URL saía de uma constante: pedir cadu baixava faber. Aqui as
    duas saem da mesma fonte, então não há como divergirem — e uma voz que não existe
    dá 404, que é uma falha **visível**, em vez de outra voz em silêncio.
    """
    spec = parse_voice(key)
    if spec is None:
        return None
    return spec.model_url, spec.config_url


@dataclass(frozen=True)
class AssistantIdentity:
    """Quem o assistente é. Imutável: identidade que muda sozinha não é identidade."""

    name: str = DEFAULT_NAME
    local_voice: str = DEFAULT_LOCAL_VOICE
    live_voice: str = DEFAULT_LIVE_VOICE
    prompt_override: str = ""

    @property
    def has_override(self) -> bool:
        """Override só conta se tiver conteúdo. `"   "` não é uma identidade."""
        return bool(self.prompt_override.strip())

    @property
    def display_name(self) -> str:
        """O nome como ele aparece na tela. Vazio nunca: um título em branco é defeito."""
        return self.name.strip() or DEFAULT_NAME

    def voice_spec(self) -> VoiceSpec | None:
        return parse_voice(self.local_voice)


def identity_from_config(config: Any) -> AssistantIdentity:
    """Traduz a configuração do usuário numa identidade. Puro.

    `getattr` com default em vez de acesso direto: este módulo também é chamado de
    testes e de scripts que montam um config parcial, e um `AttributeError` aqui
    derrubaria a montagem do prompt — que é caminho de toda resposta.
    """
    if config is None:
        return AssistantIdentity()
    nome = getattr(config, "assistant_name", "") or ""
    voz = getattr(config, "piper_voice_model", "") or ""
    live = getattr(config, "gemini_live_voice", "") or ""
    override = getattr(config, "system_prompt", "") or ""
    return AssistantIdentity(
        name=nome.strip() or DEFAULT_NAME,
        local_voice=voz.strip() or DEFAULT_LOCAL_VOICE,
        live_voice=live.strip() or DEFAULT_LIVE_VOICE,
        prompt_override=override if isinstance(override, str) else "",
    )


def build_prompt(
    identity: AssistantIdentity,
    base_prompt: str,
    *,
    persona_marker: str | None = None,
    env_placeholder: str | None = None,
    env_description: str | None = None,
    addons: Iterable[str] = (),
) -> str:
    """Compõe o prompt de sistema. **A** função que decide o que o modelo lê.

    Quatro regras, na ordem em que importam:

    1. **O override substitui a persona — e só ela.** Era o que o campo prometia e
       não fazia; mas substituir o texto **inteiro** seria pior do que não ter o
       campo: o contrato de saída do agente (esquema JSON, regras de `explanation`,
       lista de ferramentas) mora no mesmo bloco, depois de `persona_marker`. Um
       usuário que escrevesse "seja breve e sarcástico" perderia o esquema e o agente
       pararia de responder — sem nada na tela ligando uma coisa à outra. Com o
       marcador, o override troca **quem** o assistente é, e as diretrizes técnicas
       ficam.
    2. **O nome entra nas DUAS ocorrências.** O texto histórico nomeia o assistente no
       começo e no meio ("O `__ASSISTANT_NAME__` usará automaticamente a redação…").
       Trocar só a primeira deixaria o assistente com dois nomes no mesmo prompt.
    3. **A descrição de ambiente é substituída por último**, sobre o texto já nomeado:
       ela fala do *sistema operacional* (`Zorin OS 18 Core`), não do assistente, e
       trocar a ordem faria um assistente chamado "Zorin OS 18 Core" colidir com a
       frase do ambiente.
    4. **Os `addons` entram sempre**, inclusive com override: modo tutor é *modo*, não
       persona, e perdê-lo por ter escrito um prompt próprio seria uma surpresa.

    Sem `persona_marker`, ou com um marcador que não existe no texto, o override vale
    como texto **completo** — comportamento antigo, mantido para quem chama esta
    função com um texto que não tem a estrutura do prompt real. A escolha é
    conservadora: na dúvida, respeita o que o usuário escreveu.

    As substituições são literais (`str.replace`), nunca `format`: o texto base tem 11
    pares de chaves em exemplos de JSON.

    O par `env_placeholder`/`env_description` é **parâmetro**, e não import: quem
    conhece a frase do ambiente é o provedor, não a identidade. Assim este módulo
    continua puro e importável sem o pacote de desktop — e sem uma dependência que
    faria a identidade saber sobre sistemas operacionais, que não é assunto dela.
    """
    if identity.has_override:
        override = identity.prompt_override.strip()
        if persona_marker and persona_marker in base_prompt:
            _, _, diretrizes = base_prompt.partition(persona_marker)
            corpo = f"{override}\n{persona_marker}{diretrizes}"
        else:
            corpo = override
    else:
        corpo = base_prompt.replace(NAME_SENTINEL, identity.display_name)
        if env_placeholder and env_description:
            corpo = corpo.replace(env_placeholder, env_description, 1)

    return "".join([corpo, *(a for a in addons if a)])


def voice_from_selection(indice: Any, atual: str = "") -> str:
    """Converte o índice de um `Adw.ComboRow` na chave da voz. Puro.

    **O caso que importa é o índice inválido**, e ele não é hipotético: se o
    `config.json` traz uma voz fora da lista ofertada (editado à mão, ou uma voz que
    o índice oficial deixou de publicar), o combo não tem onde selecioná-la e fica sem
    seleção. Cair no default ali **trocaria a voz do usuário sem ele ter pedido** — a
    interface não pode decidir que uma escolha que ela não sabe exibir é inválida.
    Preservar é a resposta honesta.
    """
    chaves = [chave for chave, _ in PT_BR_VOICES]
    # `bool` **antes** de `int`: em Python `True` é `int` e passaria no teste de faixa
    # como índice 1, trocando a voz em silêncio por causa de um valor booleano que
    # ninguém quis passar. A ordem das duas condições é a correção, não um detalhe.
    if not isinstance(indice, bool) and isinstance(indice, int) and 0 <= indice < len(chaves):
        return chaves[indice]
    return atual.strip() or DEFAULT_LOCAL_VOICE


#: Frase que só existe no prompt que `CopilotConfig.load()` **gerava** sozinho.
#: Ancorada nas duas pontas: um prompt escrito por uma pessoa pode começar igual, mas
#: não termina com esta frase exata.
_LEGACY_AUTO_PROMPT = re.compile(
    r"^Você é o Zorin Copilot, assistente inteligente do sistema operacional .+?\. "
    r"Você ajuda o usuário a realizar tarefas, responder dúvidas sobre o computador e "
    r"propor ações de sistema de forma clara, prestativa e objetiva em português\.$",
    re.DOTALL,
)


def is_legacy_auto_prompt(text: Any) -> bool:
    """`True` se `text` é o prompt que o próprio app gerava — não um prompt do usuário.

    **Guarda de migração, e ela é obrigatória.** `CopilotConfig.load()` preenchia
    `system_prompt` quando vazio e `save()` o persistia: toda instalação já usada tem
    esse texto gravado no `config.json`. No instante em que o campo passou a ser
    **lido** (o conserto do campo que mentia), esse texto viraria um override de
    verdade — e o prompt de 9,5 KB cairia para 260 caracteres **em toda instalação
    existente**. Seria trocar um campo inerte por uma regressão silenciosa.

    Com esta guarda, o texto gerado é reconhecido e descartado: vazio volta a
    significar "derive da identidade", que é o que o campo sempre prometeu.
    """
    if not isinstance(text, str):
        return False
    return bool(_LEGACY_AUTO_PROMPT.match(text.strip()))


__all__ = [
    "AssistantIdentity",
    "PERSONA_MARKER",
    "DEFAULT_LIVE_VOICE",
    "DEFAULT_LOCAL_VOICE",
    "DEFAULT_NAME",
    "NAME_SENTINEL",
    "PIPER_BASE_URL",
    "PT_BR_VOICES",
    "VoiceSpec",
    "build_prompt",
    "identity_from_config",
    "is_legacy_auto_prompt",
    "voice_from_selection",
    "parse_voice",
    "voice_urls",
]
