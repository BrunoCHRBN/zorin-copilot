"""Testes da identidade do assistente (`core/identity.py`).

Módulo puro, então a suíte é pura: sem display, sem rede, sem disco, sem relógio.

Os três defeitos que este módulo existe para corrigir estão cada um travado aqui:
o **campo de prompt que mentia**, o **nome em quatro lugares** e a **voz que baixava o
modelo errado**. Um teste para cada, com o nome do defeito no nome do teste — porque
daqui a seis meses o que interessa não é "parse_voice funciona", é *por que* ele existe.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from zorin_copilot.core import identity as idn
from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.core.identity import (
    DEFAULT_LIVE_VOICE,
    DEFAULT_LOCAL_VOICE,
    DEFAULT_NAME,
    NAME_SENTINEL,
    PT_BR_VOICES,
    AssistantIdentity,
    build_prompt,
    identity_from_config,
    parse_voice,
    voice_urls,
)

#: Base sintética com as duas ocorrências do nome, como o texto real.
BASE = (
    f"Você é o {NAME_SENTINEL}, assistente de bancada.\n"
    "Seja direto.\n"
    f"O {NAME_SENTINEL} usará automaticamente a redação integral.\n"
    "Ambiente: Zorin OS 18 Core (Linux / GNOME 46 no Wayland).\n"
)


# ---------------------------------------------------------------------------
# A voz do Piper: a URL sai do MESMO lugar que o caminho
# ---------------------------------------------------------------------------
class DesmontarVozTest(unittest.TestCase):
    def test_a_voz_padrao_desmonta(self):
        spec = parse_voice("pt_BR-faber-medium")
        self.assertEqual(
            (spec.family, spec.locale, spec.voice, spec.quality),
            ("pt", "pt_BR", "faber", "medium"),
        )

    def test_o_diretorio_e_o_do_indice_oficial(self):
        """O caminho tem de bater com o `files` do `voices.json` do repo oficial."""
        self.assertEqual(
            parse_voice("pt_BR-cadu-medium").directory, "pt/pt_BR/cadu/medium"
        )

    def test_espaco_em_volta_nao_atrapalha(self):
        spec = parse_voice("  pt_BR-jeff-medium  ")
        self.assertEqual(spec.key, "pt_BR-jeff-medium")

    def test_formato_invalido_devolve_none_em_vez_de_levantar(self):
        """Voz malformada é caso normal (config editado à mão), não exceção.

        Levantar aqui derrubaria a fala inteira por causa de uma string — e a fala é
        caminho de toda resposta falada.
        """
        for ruim in ("", "   ", "faber", "pt_BR-faber", "pt_BR-faber-medium-x",
                     "ptBR-faber-medium", "pt_BR--medium", "pt_BR-faber-", None, 42, []):
            with self.subTest(entrada=repr(ruim)):
                self.assertIsNone(parse_voice(ruim))

    def test_as_quatro_vozes_pt_br_do_indice_desmontam(self):
        """São **4** no índice oficial do `rhasspy/piper-voices` (medido)."""
        self.assertEqual(len(PT_BR_VOICES), 4)
        for chave, rotulo in PT_BR_VOICES:
            with self.subTest(voz=chave):
                self.assertIsNotNone(parse_voice(chave), f"{chave} não desmonta")
                self.assertTrue(rotulo.strip())

    def test_o_default_esta_na_lista_ofertada(self):
        """Default fora da lista = a interface não consegue mostrar o que está em uso."""
        self.assertIn(DEFAULT_LOCAL_VOICE, [c for c, _ in PT_BR_VOICES])


class UrlDerivadaTest(unittest.TestCase):
    """**O defeito da voz errada.** Antes: caminho do `voice_name`, URL de constante."""

    def test_cada_voz_aponta_para_o_proprio_modelo(self):
        for chave, _ in PT_BR_VOICES:
            with self.subTest(voz=chave):
                onnx, cfg = voice_urls(chave)
                self.assertTrue(onnx.endswith(f"/{chave}.onnx"))
                self.assertEqual(cfg, onnx + ".json")

    def test_o_nome_da_voz_aparece_no_caminho_e_no_arquivo(self):
        onnx, _ = voice_urls("pt_BR-cadu-medium")
        self.assertIn("/cadu/", onnx)
        self.assertIn("pt_BR-cadu-medium.onnx", onnx)

    def test_pedir_cadu_nunca_devolve_o_faber(self):
        """A regressão exata que este módulo existe para impedir.

        O código antigo baixava o ONNX do **faber** para dentro do arquivo
        `pt_BR-cadu-medium.onnx` e registrava "carregado com sucesso: pt_BR-cadu-medium".
        O usuário pedia uma voz e recebia outra, com o log confirmando a errada.
        """
        onnx, _ = voice_urls("pt_BR-cadu-medium")
        self.assertNotIn("faber", onnx)

    def test_nenhuma_voz_conhecida_aponta_para_o_faber(self):
        for chave, _ in PT_BR_VOICES:
            if chave == DEFAULT_LOCAL_VOICE:
                continue
            with self.subTest(voz=chave):
                self.assertNotIn("faber", voice_urls(chave)[0])

    def test_voz_malformada_nao_tem_url(self):
        self.assertIsNone(voice_urls("faber"))
        self.assertIsNone(voice_urls(None))

    def test_o_formato_vale_alem_das_quatro_ofertadas(self):
        """As 177 vozes do índice têm 3 partes: o formato é regra, não sorte do pt_BR."""
        onnx, _ = voice_urls("en_US-lessac-medium")
        self.assertEqual(
            onnx,
            f"{idn.PIPER_BASE_URL}/en/en_US/lessac/medium/en_US-lessac-medium.onnx",
        )


# ---------------------------------------------------------------------------
# A identidade
# ---------------------------------------------------------------------------
class IdentidadeTest(unittest.TestCase):
    def test_default_e_o_nome_historico(self):
        """Se o default mudasse, todo prompt enviado mudaria sem ninguém pedir."""
        self.assertEqual(AssistantIdentity().name, DEFAULT_NAME)

    def test_nome_vazio_nao_vira_titulo_em_branco(self):
        self.assertEqual(AssistantIdentity(name="   ").display_name, DEFAULT_NAME)
        self.assertEqual(AssistantIdentity(name="").display_name, DEFAULT_NAME)

    def test_nome_com_espaco_e_limpo(self):
        self.assertEqual(AssistantIdentity(name="  Jarvis  ").display_name, "Jarvis")

    def test_override_em_branco_nao_conta(self):
        """`"   "` não é uma identidade — é um campo que o usuário limpou."""
        for vazio in ("", "   ", "\n\t "):
            with self.subTest(valor=repr(vazio)):
                self.assertFalse(AssistantIdentity(prompt_override=vazio).has_override)

    def test_e_imutavel(self):
        from dataclasses import FrozenInstanceError

        with self.assertRaises(FrozenInstanceError):
            AssistantIdentity().name = "outro"  # type: ignore[misc]

    def test_voice_spec_acompanha_a_voz_escolhida(self):
        ident = AssistantIdentity(local_voice="pt_BR-jeff-medium")
        self.assertEqual(ident.voice_spec().voice, "jeff")

    def test_voice_spec_de_voz_ruim_e_none(self):
        self.assertIsNone(AssistantIdentity(local_voice="lixo").voice_spec())


class IdentidadeDaConfigTest(unittest.TestCase):
    def test_config_none_da_a_identidade_default(self):
        self.assertEqual(identity_from_config(None), AssistantIdentity())

    def test_config_parcial_nao_levanta(self):
        """Testes e scripts montam config parcial; `AttributeError` aqui derrubaria
        a montagem do prompt, que é caminho de toda resposta."""

        class Parcial:
            pass

        self.assertEqual(identity_from_config(Parcial()), AssistantIdentity())

    def test_campos_vazios_caem_no_default(self):
        class Vazia:
            assistant_name = ""
            piper_voice_model = "   "
            gemini_live_voice = ""
            system_prompt = ""

        self.assertEqual(identity_from_config(Vazia()), AssistantIdentity())

    def test_os_campos_da_config_sao_lidos(self):
        class Cheia:
            assistant_name = "Jarvis"
            piper_voice_model = "pt_BR-cadu-medium"
            gemini_live_voice = "Kore"
            system_prompt = "Você é o Jarvis."

        ident = identity_from_config(Cheia())
        self.assertEqual(ident.name, "Jarvis")
        self.assertEqual(ident.local_voice, "pt_BR-cadu-medium")
        self.assertEqual(ident.live_voice, "Kore")
        self.assertTrue(ident.has_override)

    def test_override_que_nao_e_string_e_descartado(self):
        class Estranha:
            assistant_name = "X"
            system_prompt = 12345

        self.assertEqual(identity_from_config(Estranha()).prompt_override, "")

    def test_os_defaults_batem_com_os_da_config(self):
        """**Duas fontes para o mesmo default divergem em silêncio.**

        Se `CopilotConfig` mudasse a voz padrão e a identidade não, o app mostraria
        uma voz na tela e falaria com outra — sem nenhum teste vermelho.
        """
        padrao = CopilotConfig()
        self.assertEqual(DEFAULT_LOCAL_VOICE, padrao.piper_voice_model)
        self.assertEqual(DEFAULT_LIVE_VOICE, padrao.gemini_live_voice)


# ---------------------------------------------------------------------------
# A composição do prompt
# ---------------------------------------------------------------------------
class ComporPromptTest(unittest.TestCase):
    def test_o_nome_entra_nas_DUAS_ocorrencias(self):
        """Trocar só a primeira deixaria o assistente com dois nomes no mesmo prompt."""
        prompt = build_prompt(AssistantIdentity(name="Jarvis"), BASE)
        self.assertEqual(prompt.count("Jarvis"), 2)
        self.assertNotIn(NAME_SENTINEL, prompt)
        self.assertNotIn("Zorin Copilot", prompt)

    def test_a_identidade_default_nao_deixa_sentinela(self):
        prompt = build_prompt(AssistantIdentity(), BASE)
        self.assertNotIn(NAME_SENTINEL, prompt)
        self.assertIn("Você é o Zorin Copilot", prompt)

    def test_o_ambiente_so_e_trocado_quando_ha_o_par(self):
        """Substituir com um lado só trocaria a frase por `None` — ou não trocaria."""
        original = "Ambiente: Zorin OS 18 Core (Linux / GNOME 46 no Wayland)."
        par = ("Zorin OS 18 Core (Linux / GNOME 46 no Wayland)", "Arch (Hyprland)")
        ident = AssistantIdentity()

        self.assertIn(original, build_prompt(ident, BASE))
        self.assertIn(
            "Ambiente: Arch (Hyprland).",
            build_prompt(ident, BASE, env_placeholder=par[0], env_description=par[1]),
        )
        # Só o placeholder, sem a descrição: não mexe.
        self.assertIn(original, build_prompt(ident, BASE, env_placeholder=par[0]))
        # Só a descrição, sem o placeholder: não mexe.
        self.assertIn(original, build_prompt(ident, BASE, env_description=par[1]))

    def test_o_nome_nao_colide_com_a_frase_do_ambiente(self):
        """**Ordem importa.** O ambiente é trocado depois do nome.

        Se fosse antes, um assistente chamado "Zorin OS 18 Core" substituiria o
        próprio nome ao mesmo tempo que a frase do ambiente, e o resultado dependeria
        de qual substituição o `str.replace` encontrou primeiro.
        """
        par = ("Zorin OS 18 Core (Linux / GNOME 46 no Wayland)", "Arch (Hyprland)")
        prompt = build_prompt(
            AssistantIdentity(name="Zorin OS 18 Core"),
            BASE,
            env_placeholder=par[0],
            env_description=par[1],
        )
        self.assertIn("Você é o Zorin OS 18 Core,", prompt)
        self.assertIn("Ambiente: Arch (Hyprland).", prompt)

    def test_o_override_substitui_o_texto_base(self):
        """**O campo que mentia.** Era escrito, persistido e nunca lido."""
        prompt = build_prompt(
            AssistantIdentity(prompt_override="Você é o Vigia. Só observe."), BASE
        )
        self.assertEqual(prompt, "Você é o Vigia. Só observe.")
        self.assertNotIn("assistente de bancada", prompt)

    def test_o_override_nao_e_reescrito_nem_escapado(self):
        """Texto do usuário é dado, não template: entra literal."""
        texto = "Use {chaves} e %s e __ASSISTANT_NAME__ sem medo."
        self.assertEqual(
            build_prompt(AssistantIdentity(prompt_override=texto), BASE), texto
        )

    def test_os_addons_entram_nos_dois_caminhos(self):
        """Modo tutor é *modo*, não persona: perder por ter prompt próprio seria surpresa."""
        addon = "\n[MODO TUTOR]"
        self.assertTrue(
            build_prompt(AssistantIdentity(), BASE, addons=[addon]).endswith(addon)
        )
        self.assertEqual(
            build_prompt(
                AssistantIdentity(prompt_override="Curto."), BASE, addons=[addon]
            ),
            "Curto." + addon,
        )

    def test_addon_vazio_nao_suja_o_prompt(self):
        self.assertEqual(build_prompt(AssistantIdentity(), BASE, addons=["", ""]), BASE.replace(NAME_SENTINEL, DEFAULT_NAME))

    def test_override_em_branco_volta_a_derivar(self):
        prompt = build_prompt(AssistantIdentity(prompt_override="   "), BASE)
        self.assertIn("assistente de bancada", prompt)


# ---------------------------------------------------------------------------
# Guardas estruturais
# ---------------------------------------------------------------------------
class ModuloPuroTest(unittest.TestCase):
    """Sem GTK, sem rede, sem disco, sem relógio — a regra tem de rodar sem display."""

    PROIBIDOS = {
        "gi", "gi.repository", "Gtk", "GLib", "Gio", "Adw",
        "socket", "requests", "urllib", "http", "httpx",
        "subprocess", "sqlite3", "time", "datetime", "os", "pathlib", "random",
    }

    def _fonte(self) -> str:
        return (Path(idn.__file__)).read_text(encoding="utf-8")

    def test_nao_importa_nada_de_efeito(self):
        arvore = ast.parse(self._fonte())
        importados: set[str] = set()
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                importados.update(a.name.split(".")[0] for a in no.names)
            elif isinstance(no, ast.ImportFrom) and no.module:
                importados.add(no.module.split(".")[0])
        proibidos = importados & self.PROIBIDOS
        self.assertEqual(
            proibidos, set(), f"módulo puro importando efeito: {sorted(proibidos)}"
        )

    def test_nao_ha_import_tardio_escondido(self):
        """Um `import` dentro de função escaparia da varredura de topo — e é como o
        acoplamento volta sem ninguém ver."""
        arvore = ast.parse(self._fonte())
        tardios = [
            no.lineno
            for no in ast.walk(arvore)
            if isinstance(no, (ast.Import, ast.ImportFrom))
            and no.col_offset > 0
        ]
        self.assertEqual(tardios, [], f"import dentro de bloco nas linhas {tardios}")


class NomeNoFonteTest(unittest.TestCase):
    """O nome do assistente **não** pode voltar a ser literal dentro do prompt."""

    def test_o_texto_base_usa_a_sentinela(self):
        from zorin_copilot.ai import providers

        self.assertIn(NAME_SENTINEL, providers.SYSTEM_PROMPT)
        self.assertNotIn(
            "Você é o Zorin Copilot",
            providers.SYSTEM_PROMPT,
            "o nome voltou a ser literal: a identidade deixa de mandar no prompt",
        )

    def test_a_sentinela_aparece_duas_vezes_no_texto_base(self):
        from zorin_copilot.ai import providers

        self.assertEqual(providers.SYSTEM_PROMPT.count(NAME_SENTINEL), 2)


if __name__ == "__main__":
    unittest.main()
