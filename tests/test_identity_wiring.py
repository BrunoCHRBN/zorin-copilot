"""Testes da fiação da identidade: config, prompt, voz Piper, título e preferências.

A suíte pura (`tests/test_identity.py`) prova as **regras**. Aqui se prova que os
lugares que *usavam* a identidade espalhada passaram a usá-la de um lugar só — e que
os três defeitos medidos morreram no fio, não apenas na função pura:

1. **O campo de prompt que mentia.** `config.system_prompt` era escrito, persistido e
   nunca lido. Agora é lido — e por isso precisou de **guarda de migração**: toda
   instalação já usada tem o texto gerado gravado ali, e sem a guarda o prompt de
   9,5 KB cairia para 260 caracteres em todas elas.
2. **O nome em quatro lugares.** Título da janela, duas vezes no prompt, e o texto
   gerado por `load()`.
3. **A voz Piper que baixava o modelo errado.** A URL saía de uma constante do faber
   enquanto o caminho saía de `voice_name`.
"""

from __future__ import annotations

import ast
import hashlib
import re
import unittest
from pathlib import Path
from unittest import mock

from zorin_copilot.ai import local_voice as lv_module
from zorin_copilot.ai import providers
from zorin_copilot.ai.providers import build_system_prompt
from zorin_copilot.core import config as config_module
from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.core.identity import (
    DEFAULT_NAME,
    PERSONA_MARKER,
    AssistantIdentity,
    is_legacy_auto_prompt,
    voice_from_selection,
)

#: Snapshot do prompt **antes** do refactor do P4: 9479 caracteres, 81 linhas.
#: É a prova de que a mudança não alterou uma vírgula do que o modelo recebe quando o
#: usuário não pediu identidade nenhuma. Sem este número, "refatorei sem mudar
#: comportamento" seria asserção.
PROMPT_SHA = "9d62ea3f74ea0f0e"
PROMPT_LEN = 9479

#: O texto exato que `CopilotConfig.load()` gerava sozinho — e que ficou gravado no
#: `config.json` de quem já usou o app.
PROMPT_GERADO = (
    "Você é o Zorin Copilot, assistente inteligente do sistema operacional "
    "EndeavourOS (Hyprland no wayland). Você ajuda o usuário a realizar tarefas, "
    "responder dúvidas sobre o computador e propor ações de sistema de forma clara, "
    "prestativa e objetiva em português."
)


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()[:16]


def _nomes(no: ast.AST) -> set[str]:
    """Nomes e atributos referenciados dentro de um nó — **sem comentários nem
    docstrings**. Um comentário não gera `ast.Name`; uma docstring gera `ast.Constant`,
    e por isso é excluída por identidade de nó.
    """
    prosa: set[int] = set()
    for sub in ast.walk(no):
        corpo = getattr(sub, "body", None)
        if not isinstance(corpo, list) or not corpo:
            continue
        primeiro = corpo[0]
        if (
            isinstance(primeiro, ast.Expr)
            and isinstance(primeiro.value, ast.Constant)
            and isinstance(primeiro.value.value, str)
        ):
            prosa.add(id(primeiro.value))

    nomes: set[str] = set()
    for sub in ast.walk(no):
        if isinstance(sub, ast.Constant):
            continue
        if isinstance(sub, ast.Name):
            nomes.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            nomes.add(sub.attr)
    return nomes


def _constantes(no: ast.AST) -> set[str]:
    """Strings literais **em código** dentro de um nó, fora docstrings."""
    prosa: set[int] = set()
    for sub in ast.walk(no):
        corpo = getattr(sub, "body", None)
        if not isinstance(corpo, list) or not corpo:
            continue
        primeiro = corpo[0]
        if (
            isinstance(primeiro, ast.Expr)
            and isinstance(primeiro.value, ast.Constant)
            and isinstance(primeiro.value.value, str)
        ):
            prosa.add(id(primeiro.value))
    return {
        sub.value
        for sub in ast.walk(no)
        if isinstance(sub, ast.Constant)
        and isinstance(sub.value, str)
        and id(sub) not in prosa
    }


def _funcao(caminho: Path, nome: str) -> ast.AST:
    """O nó da função `nome` — para perguntar sobre o **corpo dela**, e não sobre o
    módulo. Sem isto, `voice_from_selection` passaria na asserção só por estar
    importada no topo do arquivo."""
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)) and no.name == nome:
            return no
    raise AssertionError(f"função {nome!r} não existe em {caminho.name}")


def _codigo(caminho: Path) -> tuple[set[str], set[str]]:
    """Devolve `(constantes de string, nomes)` de um arquivo — **sem comentários nem
    docstrings**.

    **Por que não `read_text()`.** Varredura textual não distingue uso de menção. O
    comentário que explica o defeito antigo *contém o literal do defeito*, então
    `assertNotIn(literal, fonte)` acusa o próprio arquivo que documenta a correção.
    Aconteceu **duas vezes** ao escrever esta suíte: no título da janela (o literal
    `self.set_title("Zorin Copilot")` sobrevive num comentário) e nas constantes de URL
    do Piper (`PIPER_MODEL_URL` idem). É a mesma armadilha que fez a tabela de órfãos
    contar 4 onde havia 5 — *menção em comentário não é uso*.
    """
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    return _constantes(arvore), _nomes(arvore)


# ---------------------------------------------------------------------------
# O campo que mentia — e a migração que ele obrigou
# ---------------------------------------------------------------------------
class MigracaoDoPromptTest(unittest.TestCase):
    """`load()` deixou de **gerar** o prompt; e passou a **descartar** o que gerou."""

    def _load_com(self, valor: str) -> CopilotConfig:
        """Roda o `load()` real com um `config.json` de mentira."""
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "config.json"
            caminho.write_text(json.dumps({"system_prompt": valor}), encoding="utf-8")
            with mock.patch.object(
                CopilotConfig, "config_file", classmethod(lambda cls: caminho)
            ):
                return CopilotConfig.load()

    def test_o_texto_gerado_e_reconhecido(self):
        self.assertTrue(is_legacy_auto_prompt(PROMPT_GERADO))
        self.assertTrue(is_legacy_auto_prompt("  " + PROMPT_GERADO + "\n"))

    def test_prompt_de_pessoa_nao_e_confundido_com_o_gerado(self):
        for proprio in (
            "Você é o Jarvis. Seja breve.",
            "Você é o Zorin Copilot, assistente inteligente do sistema operacional X.",
            "Fale pouco e em português.",
            "",
            None,
            42,
        ):
            with self.subTest(valor=repr(proprio)):
                self.assertFalse(is_legacy_auto_prompt(proprio))

    def test_a_migracao_limpa_o_texto_gerado(self):
        """**A regressão que a guarda impede.** Sem isto, o prompt cairia para 260
        caracteres em toda instalação existente no dia em que o campo passou a ser
        lido — trocar um campo inerte por uma regressão silenciosa."""
        self.assertEqual(self._load_com(PROMPT_GERADO).system_prompt, "")

    def test_a_migracao_preserva_o_prompt_do_usuario(self):
        """Migração que apaga o que o usuário escreveu não é migração, é perda."""
        proprio = "Você é o Vigia. Fale pouco e nunca sugira."
        self.assertEqual(self._load_com(proprio).system_prompt, proprio)

    def test_o_load_nao_gera_mais_o_prompt(self):
        """A causa-raiz: enquanto `load()` escrevesse, o campo voltaria a mentir."""
        self.assertEqual(self._load_com("").system_prompt, "")

    def test_a_migracao_roda_uma_vez_e_fica_limpa(self):
        """Limpar e salvar não pode reintroduzir o texto: senão a guarda seria eterna
        e o campo voltaria a ser escrito pelo próprio app."""
        cfg = self._load_com(PROMPT_GERADO)
        self.assertEqual(cfg.system_prompt, "")
        self.assertEqual(self._load_com(cfg.system_prompt).system_prompt, "")

    def test_a_config_nova_nasce_com_o_campo_vazio(self):
        """Vazio = "derive". É o que a docstring do campo sempre prometeu."""
        self.assertEqual(CopilotConfig().system_prompt, "")
        self.assertEqual(CopilotConfig().assistant_name, "")


# ---------------------------------------------------------------------------
# O prompt composto
# ---------------------------------------------------------------------------
class PromptCompostoTest(unittest.TestCase):
    def test_com_a_identidade_default_o_prompt_nao_muda_um_byte(self):
        """**A prova de que o refactor não mudou comportamento.**

        `build_system_prompt()` era `SYSTEM_PROMPT` + duas substituições. Agora passa
        por `identity.build_prompt`. Se o resultado divergisse, todo prompt enviado ao
        modelo teria mudado sem ninguém pedir — e nenhum teste de unidade pegaria,
        porque os dois lados estariam "certos" isoladamente.
        """
        prompt = build_system_prompt()
        self.assertEqual(len(prompt), PROMPT_LEN)
        self.assertEqual(_sha(prompt), PROMPT_SHA)

    def test_a_sentinela_nunca_vaza_para_o_modelo(self):
        """`__ASSISTANT_NAME__` no prompt seria um vazamento de implementação na cara
        do usuário — e o modelo poderia repeti-lo."""
        self.assertNotIn("__ASSISTANT_NAME__", build_system_prompt())

    def test_o_texto_base_continua_tendo_os_dois_blocos(self):
        """A separação persona/diretrizes é o que torna o override seguro."""
        self.assertEqual(providers.SYSTEM_PROMPT.count(PERSONA_MARKER), 1)
        self.assertIn("PERSONALIDADE & TOM DE VOZ", providers.SYSTEM_PROMPT)

    def test_o_override_do_usuario_e_de_fato_lido(self):
        """**O defeito.** Antes, escrever em `system_prompt` não mudava nada."""
        with mock.patch.object(
            providers.CopilotConfig, "load", return_value=_config(system_prompt="Você é o Vigia.")
        ):
            prompt = build_system_prompt()
        self.assertTrue(prompt.startswith("Você é o Vigia."))

    def test_o_override_preserva_o_contrato_de_saida(self):
        """Substituir o texto **inteiro** apagaria o esquema JSON, as regras de
        `explanation` e a lista de ferramentas — o agente pararia de responder, e nada
        na tela ligaria a caixa "prompt" a isso."""
        with mock.patch.object(
            providers.CopilotConfig, "load", return_value=_config(system_prompt="Você é o Vigia.")
        ):
            prompt = build_system_prompt()
        for marca in (PERSONA_MARKER, '"explanation"', "write_file", "fix_command"):
            with self.subTest(marca=marca):
                self.assertIn(marca, prompt)
        self.assertNotIn("PERSONALIDADE & TOM DE VOZ", prompt)

    def test_o_nome_da_config_chega_ao_prompt(self):
        with mock.patch.object(
            providers.CopilotConfig, "load", return_value=_config(assistant_name="Jarvis")
        ):
            prompt = build_system_prompt()
        self.assertEqual(prompt.count("Jarvis"), 2)
        self.assertNotIn(DEFAULT_NAME, prompt)

    def test_config_ilegivel_nao_impede_a_resposta(self):
        """Recurso, não requisito: um `config.json` corrompido não pode deixar o
        assistente mudo — cai na identidade default e responde."""
        with mock.patch.object(providers.CopilotConfig, "load", side_effect=RuntimeError("boom")):
            prompt = build_system_prompt()
        self.assertEqual(_sha(prompt), PROMPT_SHA)

    def test_o_addon_do_modo_tutor_entra_no_prompt(self):
        """**Lacuna fechada por mutação.** `addons=()` no adaptador deixava a suíte
        verde: nenhum teste ligava o modo tutor, e o addon do tutor é o que faz o
        assistente *ensinar* em vez de *responder*.
        """
        from zorin_copilot.core.study_tutor import TUTOR_ADDON

        with mock.patch.object(
            providers.CopilotConfig, "load", return_value=_config(study_tutor=True)
        ):
            prompt = build_system_prompt()
        self.assertIn(TUTOR_ADDON, prompt)
        # E o outro lado: sem o modo ligado, o addon não entra.
        with mock.patch.object(
            providers.CopilotConfig, "load", return_value=_config(study_tutor=False)
        ):
            self.assertNotIn(TUTOR_ADDON, build_system_prompt())

    def test_o_addon_do_tutor_sobrevive_ao_override(self):
        """Modo tutor é **modo**, não persona: quem escreve um prompt próprio continua
        no modo tutor. Perder isso seria a surpresa cara."""
        from zorin_copilot.core.study_tutor import TUTOR_ADDON

        with mock.patch.object(
            providers.CopilotConfig,
            "load",
            return_value=_config(study_tutor=True, system_prompt="Você é o Vigia."),
        ):
            prompt = build_system_prompt()
        self.assertIn(TUTOR_ADDON, prompt)

    def test_o_ambiente_continua_sendo_trocado(self):
        """Este é um port para EndeavourOS/Hyprland: dizer "Zorin OS 18" faria o modelo
        sugerir comandos que não existem nesta máquina."""
        prompt = build_system_prompt()
        self.assertNotIn("Zorin OS 18 Core (Linux / GNOME 46 no Wayland)", prompt)
        self.assertIn("EndeavourOS", prompt)


def _config(**kwargs) -> CopilotConfig:
    """Config completa com os campos pedidos — o `load()` real é substituído."""
    cfg = CopilotConfig()
    for chave, valor in kwargs.items():
        setattr(cfg, chave, valor)
    return cfg


# ---------------------------------------------------------------------------
# A voz Piper: a URL sai do mesmo lugar que o caminho
# ---------------------------------------------------------------------------
class VozPiperTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        patcher = mock.patch.object(lv_module, "PIPER_MODELS_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.baixados: list[tuple[str, str]] = []

    def _synth(self, voz: str):
        return lv_module.LocalVoiceSynthesizer(voice_name=voz)

    def _urlretrieve_falso(self, url, destino):
        self.baixados.append((url, destino))
        Path(destino).write_bytes(b"modelo falso")

    def test_o_caminho_do_arquivo_segue_a_voz_escolhida(self):
        s = self._synth("pt_BR-cadu-medium")
        self.assertEqual(s.model_path.name, "pt_BR-cadu-medium.onnx")
        self.assertEqual(s.config_path.name, "pt_BR-cadu-medium.onnx.json")

    def test_o_download_usa_a_url_da_voz_escolhida(self):
        """**O defeito medido.** Antes, as duas URLs eram constantes do faber: pedir
        cadu baixava o faber para dentro do arquivo `pt_BR-cadu-medium.onnx`, carregava
        e registrava "carregado com sucesso: pt_BR-cadu-medium". O usuário pedia uma
        voz, recebia outra, e o log confirmava a errada."""
        with mock.patch("urllib.request.urlretrieve", side_effect=self._urlretrieve_falso):
            self.assertTrue(self._synth("pt_BR-cadu-medium").ensure_model_available())
        urls = [u for u, _ in self.baixados]
        self.assertEqual(len(urls), 2)
        for url in urls:
            with self.subTest(url=url):
                self.assertIn("/cadu/", url)
                self.assertNotIn("faber", url)

    def test_o_arquivo_gravado_corresponde_a_url_baixada(self):
        """Caminho e URL da **mesma** voz, na mesma chamada."""
        with mock.patch("urllib.request.urlretrieve", side_effect=self._urlretrieve_falso):
            self._synth("pt_BR-jeff-medium").ensure_model_available()
        for url, destino in self.baixados:
            with self.subTest(url=url):
                self.assertIn("pt_BR-jeff-medium", url)
                self.assertIn("pt_BR-jeff-medium", destino)

    def test_voz_malformada_recusa_em_vez_de_baixar_outra(self):
        """Recusar é a resposta certa: baixar qualquer coisa aqui seria entregar uma
        voz diferente da pedida — exatamente o defeito antigo."""
        with mock.patch("urllib.request.urlretrieve", side_effect=self._urlretrieve_falso):
            with self.assertLogs(lv_module.logger, level="ERROR") as log:
                self.assertFalse(self._synth("faber").ensure_model_available())
        self.assertEqual(self.baixados, [], "baixou algo para uma voz malformada")
        self.assertIn("faber", "\n".join(log.output))

    def test_falha_de_rede_e_relatada_com_a_url(self):
        """404 era o sintoma do defeito antigo sem rastro; agora a linha de erro diz
        qual URL falhou, e o retorno é `False` em vez de uma exceção."""
        with mock.patch("urllib.request.urlretrieve", side_effect=OSError("404")):
            with self.assertLogs(lv_module.logger, level="ERROR") as log:
                self.assertFalse(self._synth("pt_BR-cadu-medium").ensure_model_available())
        texto = "\n".join(log.output)
        self.assertIn("pt_BR-cadu-medium", texto)
        self.assertIn("/cadu/", texto)

    def test_modelo_ja_presente_nao_e_baixado_de_novo(self):
        s = self._synth("pt_BR-cadu-medium")
        s.model_path.write_bytes(b"ja tenho")
        s.config_path.write_text("{}", encoding="utf-8")
        with mock.patch("urllib.request.urlretrieve", side_effect=self._urlretrieve_falso):
            self.assertTrue(s.ensure_model_available())
        self.assertEqual(self.baixados, [])

    def test_cada_arquivo_vai_para_o_seu_proprio_destino(self):
        """**Lacuna fechada por mutação.** Trocar o destino do `.json` para o caminho do
        `.onnx` deixava a suíte verde: os dois downloads aconteciam, o retorno era
        `True`, e o `.onnx` ficava com o conteúdo do `.json` — o modelo carregaria lixo
        e o erro só apareceria na fala. A asserção é sobre o par (url, destino), que é
        o que o defeito muda.
        """
        with mock.patch("urllib.request.urlretrieve", side_effect=self._urlretrieve_falso):
            self.assertTrue(self._synth("pt_BR-cadu-medium").ensure_model_available())
        self.assertEqual(len(self.baixados), 2)
        for url, destino in self.baixados:
            with self.subTest(url=url.rsplit("/", 1)[-1]):
                if url.endswith(".onnx.json"):
                    self.assertTrue(destino.endswith("pt_BR-cadu-medium.onnx.json"))
                else:
                    self.assertTrue(destino.endswith("pt_BR-cadu-medium.onnx"))

    def test_nao_existem_mais_constantes_de_url_cravadas(self):
        """A causa-raiz do defeito: uma URL de voz fixa no módulo.

        A asserção é sobre **código**, não sobre texto: o comentário que explica a
        remoção cita `PIPER_MODEL_URL` de propósito, e uma busca textual acusaria o
        arquivo que documenta a correção.
        """
        constantes, nomes = _codigo(Path(lv_module.__file__))
        self.assertNotIn("PIPER_MODEL_URL", nomes)
        self.assertNotIn("PIPER_CONFIG_URL", nomes)
        self.assertNotIn("/faber/medium/", constantes)
        # E o outro lado: a derivação **tem** de estar no código, senão a asserção
        # acima passaria num módulo que simplesmente não soubesse mais montar URL.
        self.assertIn("voice_urls", nomes)


# ---------------------------------------------------------------------------
# A janela
# ---------------------------------------------------------------------------
class TituloDaJanelaTest(unittest.TestCase):
    """O título vinha de um literal; agora vem da identidade."""

    def _app_fonte(self) -> str:
        from zorin_copilot.ui import app as app_module

        return Path(app_module.__file__).read_text(encoding="utf-8")

    def test_o_titulo_nao_e_mais_literal(self):
        """**Varredura por AST, não por texto** — e isto é uma correção, não um gosto.

        A primeira versão deste teste fazia `assertNotIn('self.set_title("Zorin
        Copilot")', fonte)` e **falhou**. O literal existe no arquivo: dentro de um
        comentário que explica justamente que ele *era* o título. Busca textual não
        distingue uso de menção — a mesma armadilha que já tinha feito a tabela de
        órfãos contar 4 onde havia 5. Aqui a asserção pergunta ao `ast` se existe
        **chamada**, que é a única coisa que muda comportamento.
        """
        fonte = self._app_fonte()
        arvore = ast.parse(fonte)
        literais = [
            no
            for no in ast.walk(arvore)
            if isinstance(no, ast.Call)
            and isinstance(no.func, ast.Attribute)
            and no.func.attr == "set_title"
            and isinstance(no.func.value, ast.Name)
            and no.func.value.id == "self"
            and no.args
            and isinstance(no.args[0], ast.Constant)
            and isinstance(no.args[0].value, str)
        ]
        self.assertEqual(
            [],
            [no.args[0].value for no in literais],
            "o título da janela voltou a ser um literal; tem de vir de `_apply_identity()`",
        )
        self.assertIn("self._apply_identity()", fonte)

    def test_a_armadilha_do_comentario_esta_presa(self):
        """O literal continua no arquivo — como comentário. Se um dia sumir de lá, este
        teste avisa que a varredura textual voltaria a funcionar por acidente, e que o
        motivo documentado acima deixou de valer."""
        fonte = self._app_fonte()
        # `assertTrue` e não `assertIn`: uma falha de `assertIn` sobre um arquivo de
        # 3.000 linhas imprime as 3.000 linhas no relatório. Mensagem curta, diagnóstico
        # igual.
        self.assertTrue(
            'self.set_title("Zorin Copilot")' in fonte,
            "o comentário que explica o título antigo sumiu do `app.py`",
        )
        self.assertTrue(
            "identity_from_config" in fonte,
            "o `app.py` não importa mais `identity_from_config`",
        )

    def test_o_gatilho_de_fato_escreve_o_titulo(self):
        """**Executar, não ler.** Os testes acima varrem o fonte, e varredura de fonte
        aprova um `_apply_identity` que virou `pass`: a chamada continua lá, o literal
        não existe, e o título nunca é escrito. Aqui o método é chamado de verdade,
        sobre uma janela de mentira, e o que se observa é o efeito.
        """
        from zorin_copilot.ui import app as app_module

        janela = mock.Mock()
        janela.config = _config(assistant_name="Jarvis")
        app_module.CopilotWindow._apply_identity(janela)
        janela.set_title.assert_called_once_with("Jarvis")

    def test_o_gatilho_cai_no_default_sem_nome(self):
        """Sem nome configurado o título é o default — nunca vazio, nunca `None`."""
        from zorin_copilot.ui import app as app_module

        janela = mock.Mock()
        janela.config = _config()
        app_module.CopilotWindow._apply_identity(janela)
        janela.set_title.assert_called_once_with(DEFAULT_NAME)

    def test_o_titulo_e_aplicado_depois_da_config(self):
        """**Armadilha real:** `set_title` antes de `self.config = ...` não teria de
        onde ler o nome, e cairia no default para sempre — um no-op silencioso."""
        fonte = self._app_fonte()
        i_config = fonte.index("self.config = CopilotConfig.load()")
        i_titulo = fonte.index("self._apply_identity()", i_config)
        self.assertGreater(i_titulo, i_config)

    def test_salvar_a_config_reaplica_a_identidade(self):
        """Salvar tem de valer **já**, não no próximo arranque — mesma regra do monitor."""
        fonte = self._app_fonte()
        corpo = fonte[fonte.index("def _on_config_saved") :]
        corpo = corpo[: corpo.index("\n    def ", 10)]
        self.assertIn("self._apply_identity()", corpo)

    def test_apply_identity_le_a_identidade_da_config(self):
        from zorin_copilot.ui import app as app_module

        class Win:
            config = _config(assistant_name="Jarvis")

            def set_title(self, texto):
                self.titulo = texto

        win = Win()
        app_module.CopilotWindow._apply_identity(win)
        self.assertEqual(win.titulo, "Jarvis")

    def test_apply_identity_sem_config_nao_levanta(self):
        from zorin_copilot.ui import app as app_module

        class Win:
            def set_title(self, texto):
                self.titulo = texto

        win = Win()
        app_module.CopilotWindow._apply_identity(win)
        self.assertEqual(win.titulo, DEFAULT_NAME)


# ---------------------------------------------------------------------------
# As preferências
# ---------------------------------------------------------------------------
class PreferenciasDaIdentidadeTest(unittest.TestCase):
    def _fonte(self) -> str:
        from zorin_copilot.ui import preferences as prefs

        return Path(prefs.__file__).read_text(encoding="utf-8")

    def _coletor(self) -> str:
        fonte = self._fonte()
        inicio = fonte.index("def _collect_current_config")
        resto = fonte[inicio + 1 :]
        m = re.search(r"\n    def ", resto)
        return resto[: m.start()] if m else resto

    def _coletor_ast(self) -> ast.AST:
        from zorin_copilot.ui import preferences as prefs_module

        return _funcao(Path(prefs_module.__file__), "_collect_current_config")

    def test_o_coletor_reatribui_o_nome(self):
        """Campo com linha e não reatribuído volta ao default a cada salvamento."""
        self.assertIn("assistant_name", _nomes(self._coletor_ast()))

    def test_o_coletor_preserva_a_persona(self):
        """**A armadilha do config NOVO.** `_collect_current_config` parte de um
        `CopilotConfig()`, então todo campo não reatribuído volta ao default. A persona
        não tem linha na tela — sem esta preservação, o primeiro clique em "Salvar"
        apagaria o que o usuário escreveu no `config.json`.

        A asserção é sobre o **corpo do coletor** (`_funcao`), não sobre o módulo:
        `getattr` solto em qualquer outro ponto do arquivo não preservaria nada.
        """
        coletor = self._coletor_ast()
        self.assertIn("getattr", _nomes(coletor))
        self.assertIn("system_prompt", _nomes(coletor))
        self.assertIn('getattr(self.config, "system_prompt", "")', self._coletor())

    def test_o_coletor_mapeia_a_voz_pelo_indice(self):
        """Sem o mapeamento, `piper_voice_model` receberia o **índice** do combo (um
        `int`) ou o default — trocando a voz do usuário a cada salvamento."""
        nomes = _nomes(self._coletor_ast())
        self.assertIn("voice_from_selection", nomes)
        self.assertIn("piper_voice_model", nomes)

    def test_o_grupo_de_identidade_tem_as_linhas(self):
        fonte = self._fonte()
        self.assertIn("self.assistant_name_row = Adw.EntryRow", fonte)
        self.assertIn("self.local_voice_row = Adw.ComboRow", fonte)
        self.assertIn("identity_group.add(self.assistant_name_row)", fonte)
        self.assertIn("identity_group.add(self.local_voice_row)", fonte)
        self.assertIn("page.add(identity_group)", fonte)

    def test_o_carregador_preenche_as_linhas(self):
        fonte = self._fonte()
        self.assertIn("self.assistant_name_row.set_text", fonte)
        self.assertIn("self.local_voice_row.set_selected", fonte)

    def test_o_subtitulo_da_voz_nao_e_mais_literal(self):
        """Ele afirmava `pt_BR-faber-medium` fixo: quem trocasse de voz continuaria
        lendo a voz antiga — um texto que mentia no mesmo app que baixava a voz errada.

        A asserção procura a frase entre as **constantes de string** do módulo: o
        docstring acima a cita, e um `assertNotIn` sobre o texto cru acusaria o teste
        que documenta a correção.
        """
        from zorin_copilot.ui import preferences as prefs_module
        constantes, nomes = _codigo(Path(prefs_module.__file__))
        self.assertNotIn("voz masculina brasileira pt_BR-faber-medium", constantes)
        self.assertIn("_refresh_local_voice_info", nomes)

    def test_a_persona_esta_documentada_na_tela_sem_controle(self):
        """Ajuste que ninguém sabe que existe é o mesmo que não existir — então a linha
        informativa existe, mesmo sem campo (é um texto de vários parágrafos)."""
        self.assertIn("system_prompt", self._fonte())


class EscolhaDeVozTest(unittest.TestCase):
    """A regra pura por trás do combo — testável sem subir preferência nenhuma."""

    def test_indice_valido_vira_a_chave_da_lista(self):
        self.assertEqual(voice_from_selection(0), "pt_BR-faber-medium")
        self.assertEqual(voice_from_selection(1), "pt_BR-cadu-medium")

    def test_indice_invalido_preserva_a_voz_configurada(self):
        """**O caso que importa.** Voz fora da lista (editada à mão) não pode ser
        trocada pelo default só porque o combo não sabe exibi-la."""
        for invalido in (-1, 99, None, "1", 4294967295):
            with self.subTest(indice=repr(invalido)):
                self.assertEqual(
                    voice_from_selection(invalido, "pt_BR-custom-voz-medium"),
                    "pt_BR-custom-voz-medium",
                )

    def test_indice_invalido_sem_voz_configurada_cai_no_default(self):
        self.assertEqual(voice_from_selection(None, ""), "pt_BR-faber-medium")
        self.assertEqual(voice_from_selection(None, "   "), "pt_BR-faber-medium")

    def test_o_bool_nao_e_indice(self):
        """`True` é `int` em Python: sem o cuidado, `True` viraria a voz de índice 1."""
        self.assertEqual(
            voice_from_selection(True, "pt_BR-custom-voz-medium"), "pt_BR-custom-voz-medium"
        )


if __name__ == "__main__":
    unittest.main()
