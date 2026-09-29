"""Validação por mutação do P4 (identidade única).

Motivo de existir: o P4 mexeu em **seis** arquivos de produção para consertar três
defeitos medidos. Uma suíte verde prova que os testes passam; não prova que eles
*pegariam o defeito de volta*. Este harness reintroduz cada defeito, um por vez, e
exige que a suíte falhe.

O caso que este arquivo foi escrito para cobrir, e que é o mais importante:

  *"reintroduzir a URL hardcoded do faber tem de falhar"*

Se essa mutação sobreviver, o P4 não corrigiu nada — só mudou o texto de lugar.

**Como rodar** (da raiz do repo):

    .venv/bin/python tests/mutacao/mutate_identity.py

Mesmas quatro categorias de sempre: KILLED / SURVIVED / EQUIVALENTE / INVÁLIDA.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: Raiz do repo derivada do próprio arquivo, não cravada: o harness mora em
#: `tests/mutacao/`, então a raiz são dois níveis acima.
RAIZ = Path(__file__).resolve().parents[2]
SRC = RAIZ / "src" / "zorin_copilot"

IDENTIDADE = SRC / "core" / "identity.py"
VOZ = SRC / "ai" / "local_voice.py"
PROVIDERS = SRC / "ai" / "providers.py"
CONFIG = SRC / "core" / "config.py"
JANELA = SRC / "ui" / "app.py"
PREFS = SRC / "ui" / "preferences.py"

TESTES = ["tests/test_identity.py", "tests/test_identity_wiring.py"]

FABER_ONNX = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "pt_BR/faber/medium/pt_BR-faber-medium.onnx"
)
FABER_JSON = f"{FABER_ONNX}.json"

#: (arquivo, nome, de, para)
MUTACOES: list[tuple[Path, str, str, str]] = [
    # =====================================================================
    # core/identity.py — o desmonte do nome da voz
    # =====================================================================
    (IDENTIDADE, "voz: aceita nome com qualquer número de partes",
     "    if len(partes) != 3:\n        return None", "    if False:\n        return None"),
    (IDENTIDADE, "voz: locale sem `_` passa a valer",
     '    if "_" not in locale:\n        return None', "    if False:\n        return None"),
    (IDENTIDADE, "voz: aceita valor que não é string",
     "    if not isinstance(key, str):\n        return None", "    if False:\n        return None"),
    (IDENTIDADE, "voz: parte vazia passa a valer",
     "    if not locale or not voz or not qualidade:\n        return None",
     "    if False:\n        return None"),

    # =====================================================================
    # core/identity.py — a URL derivada (o coração do defeito medido)
    # =====================================================================
    (IDENTIDADE, "URL: volta a ser a do faber, fixa, para qualquer voz",
     "    return spec.model_url, spec.config_url",
     f'    return ("{FABER_ONNX}", "{FABER_JSON}")'),
    (IDENTIDADE, "URL: ordem dos segmentos do caminho invertida",
     '        return f"{self.family}/{self.locale}/{self.voice}/{self.quality}"',
     '        return f"{self.locale}/{self.family}/{self.voice}/{self.quality}"'),
    (IDENTIDADE, "URL: config sem o sufixo `.json`",
     '        return f"{self.model_url}.json"', "        return self.model_url"),
    (IDENTIDADE, "URL: qualidade some do caminho",
     '        return f"{PIPER_BASE_URL}/{self.directory}/{self.key}.onnx"',
     '        return f"{PIPER_BASE_URL}/{self.directory}/modelo.onnx"'),
    (IDENTIDADE, "URL: voz malformada deixa de devolver None",
     "    spec = parse_voice(key)\n    if spec is None:\n        return None",
     "    spec = parse_voice(key)\n    if spec is None:\n        return (None, None)"),

    # =====================================================================
    # core/identity.py — a identidade
    # =====================================================================
    (IDENTIDADE, "identidade: override em branco passa a contar",
     "        return bool(self.prompt_override.strip())", "        return bool(self.prompt_override)"),
    (IDENTIDADE, "identidade: nome vazio deixa de cair no default",
     "        return self.name.strip() or DEFAULT_NAME", "        return self.name"),
    (IDENTIDADE, "identidade: nome não é mais normalizado",
     "        name=nome.strip() or DEFAULT_NAME,", "        name=nome,"),
    (IDENTIDADE, "identidade: config parcial derruba a montagem",
     '    nome = getattr(config, "assistant_name", "") or ""',
     "    nome = config.assistant_name or \"\""),
    (IDENTIDADE, "identidade: voz local vazia deixa de cair no default",
     "        local_voice=voz.strip() or DEFAULT_LOCAL_VOICE,",
     "        local_voice=voz,"),

    # =====================================================================
    # core/identity.py — a composição do prompt
    # =====================================================================
    (IDENTIDADE, "prompt: override apaga o contrato de saída",
     '            corpo = f"{override}\\n{persona_marker}{diretrizes}"', "            corpo = override"),
    (IDENTIDADE, "prompt: a sentinela de nome não é mais substituída",
     "        corpo = base_prompt.replace(NAME_SENTINEL, identity.display_name)",
     "        corpo = base_prompt"),
    (IDENTIDADE, "prompt: só a PRIMEIRA ocorrência do nome é trocada",
     "        corpo = base_prompt.replace(NAME_SENTINEL, identity.display_name)",
     "        corpo = base_prompt.replace(NAME_SENTINEL, identity.display_name, 1)"),
    (IDENTIDADE, "prompt: a descrição do ambiente não é mais trocada",
     "            corpo = corpo.replace(env_placeholder, env_description, 1)",
     "            corpo = corpo"),
    (IDENTIDADE, "prompt: os addons (modo tutor) são descartados",
     '    return "".join([corpo, *(a for a in addons if a)])', "    return corpo"),

    # =====================================================================
    # core/identity.py — a guarda de migração
    # =====================================================================
    (IDENTIDADE, "migração: o texto gerado deixa de ser reconhecido",
     "def is_legacy_auto_prompt(text: Any) -> bool:", "def is_legacy_auto_prompt(text: Any) -> bool:\n    return False\n"),
    (IDENTIDADE, "migração: qualquer texto é tratado como gerado",
     "def is_legacy_auto_prompt(text: Any) -> bool:", "def is_legacy_auto_prompt(text: Any) -> bool:\n    return True\n"),

    # =====================================================================
    # ai/local_voice.py — A MUTAÇÃO CENTRAL
    # =====================================================================
    (VOZ, "**A CENTRAL** — a URL do faber volta a ser cravada no download",
     "        urls = voice_urls(self.voice_name)",
     f'        urls = ("{FABER_ONNX}", "{FABER_JSON}")'),
    (VOZ, "voz malformada deixa de recusar e baixa assim mesmo",
     "        if urls is None:", "        if False:"),
    (VOZ, "o erro de download deixa de nomear a URL",
     '                "Falha ao baixar o modelo de voz Piper \'%s\' de %s: %s",\n                self.voice_name,\n                model_url,\n                exc,',
     '                "Falha ao baixar o modelo de voz Piper \'%s\': %s",\n                self.voice_name,\n                exc,'),
    (VOZ, "o caminho do arquivo volta a ser de outra voz",
     '        self.model_path = PIPER_MODELS_DIR / f"{voice_name}.onnx"',
     f'        self.model_path = PIPER_MODELS_DIR / "pt_BR-faber-medium.onnx"'),
    (VOZ, "o .onnx é gravado no lugar do .json",
     "                urllib.request.urlretrieve(config_url, str(self.config_path))",
     "                urllib.request.urlretrieve(config_url, str(self.model_path))"),

    # =====================================================================
    # ai/providers.py — a fiação do prompt
    # =====================================================================
    (PROVIDERS, "fiação: a config volta a ser ignorada no prompt",
     "        identidade = identity_from_config(config)", "        pass"),
    (PROVIDERS, "fiação: o override perde o marcador de persona",
     "        persona_marker=PERSONA_MARKER,", "        persona_marker=None,"),
    (PROVIDERS, "fiação: o ambiente real deixa de entrar no prompt",
     "        env_description=description,", "        env_description=None,"),
    (PROVIDERS, "fiação: o addon do modo tutor é descartado",
     "        addons=addons,", "        addons=(),"),
    (PROVIDERS, "fiação: a sentinela volta a ser o literal no texto base",
     'SYSTEM_PROMPT = """Você é o __ASSISTANT_NAME__, assistente e parceiro',
     'SYSTEM_PROMPT = """Você é o Zorin Copilot, assistente e parceiro'),

    # =====================================================================
    # core/config.py — a guarda de migração no load()
    # =====================================================================
    (CONFIG, "config: a guarda de migração some do load()",
     "        if is_legacy_auto_prompt(config.system_prompt):\n            config.system_prompt = \"\"",
     "        pass"),

    # =====================================================================
    # ui/app.py — o título
    # =====================================================================
    (JANELA, "janela: o gatilho vira no-op (título nunca é escrito)",
     '        self.set_title(identity_from_config(getattr(self, "config", None)).display_name)',
     "        pass"),
    (JANELA, "janela: o gatilho usa o default, ignorando a config",
     '        self.set_title(identity_from_config(getattr(self, "config", None)).display_name)',
     '        self.set_title("Zorin Copilot")'),
    (JANELA, "janela: salvar deixa de reaplicar a identidade",
     "        self._apply_identity()\n        self._reconfigure_wake_word()",
     "        self._reconfigure_wake_word()"),

    # =====================================================================
    # ui/preferences.py — o coletor
    # =====================================================================
    (PREFS, "preferências: salvar apaga a persona do usuário",
     '        cfg.system_prompt = getattr(self.config, "system_prompt", "") or ""',
     '        cfg.system_prompt = ""'),
    (PREFS, "preferências: salvar apaga o nome configurado",
     "        cfg.assistant_name = self.assistant_name_row.get_text().strip()", "        pass"),
    (PREFS, "preferências: salvar reseta a voz para o faber",
     "        cfg.piper_voice_model = voice_from_selection(",
     '        cfg.piper_voice_model = (lambda *_: "pt_BR-faber-medium")('),
]

#: Sobreviventes que **não** são lacuna: a mutação não muda comportamento observável.
#:
#: Este é o único, e ele foi **medido**, não presumido. Remover o `if "_" not in locale`
#: de `parse_voice` não muda nada porque o `partition` logo abaixo já rejeita um locale
#: sem `_` (a região sai vazia). Verificação: 324 entradas hostis (9 locales × 3 vozes ×
#: 3 qualidades × 4 formas, incluindo espaço em volta e parte extra), **0 divergências**
#: entre o parse real e uma cópia sem a linha. A linha fica — ela diz a regra do
#: formato, a de baixo diz que os dados não podem ser vazios —, mas é honesto registrar
#: que nenhum teste pode distingui-la, em vez de inventar um teste que "cobre" o
#: inalcançável.
EQUIVALENTES: dict[str, str] = {
    "voz: locale sem `_` passa a valer": "redundante com a checagem de família/região (0/324 divergências)",
}


def rodar_testes() -> bool:
    ambiente = dict(os.environ, ZORIN_TEST_MODE="1")
    r = subprocess.run(
        [str(RAIZ / ".venv" / "bin" / "python"), "-m", "pytest", "-q", *TESTES],
        cwd=str(RAIZ),
        capture_output=True,
        text=True,
        env=ambiente,
    )
    return r.returncode == 0


def main() -> int:
    originais = {caminho: caminho.read_text(encoding="utf-8") for caminho, *_ in MUTACOES}
    matadas: list[str] = []
    sobreviveram: list[str] = []
    invalidas: list[str] = []

    try:
        for caminho, nome, de, para in MUTACOES:
            original = originais[caminho]
            if de not in original:
                invalidas.append(nome)
                print(f"INVÁLIDA   {nome}  (trecho não encontrado em {caminho.name})")
                continue
            if original.count(de) > 1:
                invalidas.append(nome)
                print(
                    f"INVÁLIDA   {nome}  (trecho ambíguo em {caminho.name}: "
                    f"{original.count(de)} ocorrências)"
                )
                continue
            mutado = original.replace(de, para, 1)
            if mutado == original:
                invalidas.append(nome)
                print(f"INVÁLIDA   {nome}  (substituição não mudou nada)")
                continue
            caminho.write_text(mutado, encoding="utf-8")
            try:
                if rodar_testes():
                    if nome in EQUIVALENTES:
                        print(f"EQUIVALENTE {nome}")
                    else:
                        sobreviveram.append(nome)
                        print(f"SOBREVIVEU {nome}")
                else:
                    matadas.append(nome)
                    print(f"KILLED     {nome}")
            finally:
                caminho.write_text(original, encoding="utf-8")
    finally:
        for caminho, original in originais.items():
            caminho.write_text(original, encoding="utf-8")

    print("\n" + "=" * 72)
    print(
        f"matadas: {len(matadas)}   sobreviveram: {len(sobreviveram)}   "
        f"equivalentes: {len(EQUIVALENTES)}   inválidas: {len(invalidas)}"
    )
    if sobreviveram:
        print("\nSOBREVIVENTES (lacunas de teste):")
        for nome in sobreviveram:
            print(f"  - {nome}")
    if invalidas:
        print("\nINVÁLIDAS (mutação não aplicada):")
        for nome in invalidas:
            print(f"  - {nome}")
    return 1 if (sobreviveram or invalidas) else 0


if __name__ == "__main__":
    sys.exit(main())
