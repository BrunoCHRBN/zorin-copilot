"""Testes da voz da ponte HA→desktop (`scripts/ha_bridge.py`).

A ponte é stdlib-only de propósito — tem de rodar em qualquer máquina com
Python 3 —, então ela **não** importa `zorin_copilot`. Isso deixa duas coisas
para travar aqui, do lado de fora:

1. a **paridade dos códigos de saída** com `core/ha_announce.py` (a ponte os
   duplica; sem este teste, um dia divergiriam em silêncio);
2. a **regra de plano B**, que é o que decide se a casa fala uma vez ou duas.

Escrito em estilo `unittest` de propósito, como `test_ha_announce.py`: o runner
de guarda do projeto usa `unittest`, e um arquivo pytest-style entraria na
contagem como `exit 5` estrutural.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import types
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]


def _load_bridge():
    """Carrega `scripts/ha_bridge.py` sem transformar `scripts/` em pacote.

    Mesma estratégia de `test_home_assistant_realtime.py`: o arquivo é um script
    solto, não um módulo instalável.
    """
    path = _ROOT / "scripts" / "ha_bridge.py"
    spec = importlib.util.spec_from_file_location("ha_bridge_voice", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


bridge = _load_bridge()


class _FakeSubprocess:
    """Substituto do módulo `subprocess` **só para a ponte**.

    Troca pontual (`mock.patch.object(bridge, "subprocess", ...)`) em vez de
    `mock.patch("subprocess.run")` global: o patch global mexeria com qualquer
    outra coisa rodando no mesmo processo de teste.

    `timeout_programs` permite estourar o tempo em **um** programa só — é o que
    separa "o Copilot travou" de "o plano B também travou", que são cenários
    diferentes.
    """

    SubprocessError = subprocess.SubprocessError

    def __init__(
        self,
        code: int = 0,
        stderr: bytes = b"",
        timeout_programs: tuple[str, ...] = (),
    ) -> None:
        self.code = code
        self.stderr = stderr
        self.timeout_programs = timeout_programs
        self.calls: list[tuple[list[str], dict]] = []

    def run(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if Path(argv[0]).name in self.timeout_programs:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 1.0))
        return types.SimpleNamespace(returncode=self.code, stderr=self.stderr)

    # -- consultas usadas pelos testes ----------------------------------
    @property
    def programs(self) -> list[str]:
        """Só os nomes dos programas chamados, na ordem."""
        return [Path(argv[0]).name for argv, _ in self.calls]

    def argv_for(self, program: str) -> list[str]:
        for argv, _ in self.calls:
            if Path(argv[0]).name == program:
                return argv
        raise AssertionError(f"{program} não foi chamado; chamou: {self.programs}")


def _env(*, copilot: bool, system: bool):
    """Contexto que finge o PATH: só os binários pedidos existem."""

    def _which(name: str):
        if name == bridge.COPILOT_BIN and copilot:
            return f"/usr/bin/{name}"
        if name == bridge.SPD_BIN and system:
            return f"/usr/bin/{name}"
        return None

    return mock.patch.object(bridge, "shutil", types.SimpleNamespace(which=_which))


def _speak(
    text: str,
    *,
    engine: str,
    copilot: bool,
    system: bool,
    code: int = 0,
    stderr: bytes = b"",
    timeout: bool = False,
):
    """Roda `speak()` num PATH fingido e devolve o subprocess falso.

    `timeout=True` faz **só** o pedido ao Copilot estourar o tempo — é o cenário
    "app travado", que é diferente de "app não está rodando".
    """
    fake = _FakeSubprocess(
        code=code,
        stderr=stderr,
        timeout_programs=(bridge.COPILOT_BIN,) if timeout else (),
    )
    with _env(copilot=copilot, system=system), mock.patch.object(bridge, "subprocess", fake):
        bridge.speak(text, engine=engine)
    return fake


# ---------------------------------------------------------------------------
# Paridade com o pacote (o motivo de a ponte duplicar constantes)
# ---------------------------------------------------------------------------
class ExitCodeParityTest(unittest.TestCase):
    def test_codigos_batem_com_ha_announce(self):
        """A ponte duplica os códigos; este teste é o que impede a deriva."""
        from zorin_copilot.core import ha_announce

        self.assertEqual(bridge.EXIT_SPOKEN, ha_announce.EXIT_SPOKEN)
        self.assertEqual(bridge.EXIT_NO_TARGET, ha_announce.EXIT_NO_TARGET)
        self.assertEqual(bridge.EXIT_EMPTY, ha_announce.EXIT_EMPTY)

    def test_ponte_nao_importa_o_pacote(self):
        """O isolamento é o ponto: stdlib apenas, roda em qualquer máquina."""
        fonte = (_ROOT / "scripts" / "ha_bridge.py").read_text(encoding="utf-8")
        self.assertNotIn("import zorin_copilot", fonte)
        self.assertNotIn("from zorin_copilot", fonte)


# ---------------------------------------------------------------------------
# Escolha da boca, antes de tentar
# ---------------------------------------------------------------------------
class DecideVoiceTest(unittest.TestCase):
    def test_auto_prefere_o_copilot(self):
        self.assertIs(
            bridge.decide_voice("auto", copilot=True, system=True), bridge.VoiceRoute.COPILOT
        )

    def test_auto_sem_copilot_cai_no_sistema(self):
        self.assertIs(
            bridge.decide_voice("auto", copilot=False, system=True), bridge.VoiceRoute.SYSTEM
        )

    def test_auto_sem_nenhum_fica_mudo(self):
        self.assertIs(
            bridge.decide_voice("auto", copilot=False, system=False), bridge.VoiceRoute.SILENT
        )

    def test_system_ignora_o_copilot_mesmo_disponivel(self):
        """Escolha explícita não é sugestão."""
        self.assertIs(
            bridge.decide_voice("system", copilot=True, system=True), bridge.VoiceRoute.SYSTEM
        )

    def test_system_sem_spd_say_nao_cai_no_copilot(self):
        self.assertIs(
            bridge.decide_voice("system", copilot=True, system=False), bridge.VoiceRoute.SILENT
        )

    def test_copilot_ignora_o_spd_say_mesmo_disponivel(self):
        self.assertIs(
            bridge.decide_voice("copilot", copilot=True, system=True), bridge.VoiceRoute.COPILOT
        )

    def test_copilot_sem_app_nao_cai_no_spd_say(self):
        """Modo estrito: prefere silêncio a reintroduzir a segunda boca."""
        self.assertIs(
            bridge.decide_voice("copilot", copilot=False, system=True), bridge.VoiceRoute.SILENT
        )

    def test_engine_desconhecido_se_comporta_como_auto(self):
        """`argparse` já barra, mas `BridgeState` é chamável direto."""
        self.assertIs(
            bridge.decide_voice("qualquer", copilot=True, system=True), bridge.VoiceRoute.COPILOT
        )


# ---------------------------------------------------------------------------
# O plano B, depois de tentar — onde mora o risco de falar duas vezes
# ---------------------------------------------------------------------------
class DecideAfterCopilotTest(unittest.TestCase):
    def test_falou_nao_cai_em_nada(self):
        """O teste mais importante do arquivo.

        Sem esta condição, o `spd-say` repetiria a frase logo depois da fala do
        Copilot e a casa diria tudo duas vezes, com dois timbres.
        """
        self.assertIs(
            bridge.decide_after_copilot("auto", bridge.EXIT_SPOKEN, system=True),
            bridge.VoiceRoute.COPILOT,
        )

    def test_falou_nao_cai_em_nada_nem_no_modo_estrito(self):
        self.assertIs(
            bridge.decide_after_copilot("copilot", bridge.EXIT_SPOKEN, system=True),
            bridge.VoiceRoute.COPILOT,
        )

    def test_sem_app_rodando_cai_no_sistema(self):
        self.assertIs(
            bridge.decide_after_copilot("auto", bridge.EXIT_NO_TARGET, system=True),
            bridge.VoiceRoute.SYSTEM,
        )

    def test_sem_app_rodando_e_sem_spd_say_fica_mudo(self):
        self.assertIs(
            bridge.decide_after_copilot("auto", bridge.EXIT_NO_TARGET, system=False),
            bridge.VoiceRoute.SILENT,
        )

    def test_timeout_cai_no_sistema(self):
        """`None` = o processo nem rodou. Aí o plano B é legítimo."""
        self.assertIs(
            bridge.decide_after_copilot("auto", None, system=True), bridge.VoiceRoute.SYSTEM
        )

    def test_pedido_vazio_nao_vira_fala_do_sistema(self):
        """Não havia o que dizer; o `spd-say` também não teria."""
        self.assertIs(
            bridge.decide_after_copilot("auto", bridge.EXIT_EMPTY, system=True),
            bridge.VoiceRoute.SILENT,
        )

    def test_modo_estrito_nao_cai_no_sistema(self):
        self.assertIs(
            bridge.decide_after_copilot("copilot", bridge.EXIT_NO_TARGET, system=True),
            bridge.VoiceRoute.SILENT,
        )

    def test_modo_estrito_e_timeout_tambem_nao(self):
        self.assertIs(
            bridge.decide_after_copilot("copilot", None, system=True), bridge.VoiceRoute.SILENT
        )


# ---------------------------------------------------------------------------
# `speak()`: a decisão e o efeito juntos
# ---------------------------------------------------------------------------
class SpeakTest(unittest.TestCase):
    def test_auto_fala_pelo_copilot_e_nao_pelo_spd_say(self):
        fake = _speak("Tocou a campainha", engine="auto", copilot=True, system=True)
        self.assertEqual(fake.programs, [bridge.COPILOT_BIN])

    def test_auto_cai_no_spd_say_quando_nao_ha_app(self):
        fake = _speak(
            "Tocou a campainha", engine="auto", copilot=True, system=True,
            code=bridge.EXIT_NO_TARGET,
        )
        self.assertEqual(fake.programs, [bridge.COPILOT_BIN, bridge.SPD_BIN])

    def test_auto_nao_repete_a_fala_quando_o_copilot_falou(self):
        """A guarda do falar-duas-vezes, agora ponta a ponta."""
        fake = _speak("Bom dia", engine="auto", copilot=True, system=True, code=bridge.EXIT_SPOKEN)
        self.assertNotIn(bridge.SPD_BIN, fake.programs)

    def test_pedido_vazio_nao_chama_ninguem(self):
        """Tentou o Copilot, ele disse "vazio", e ninguém mais é incomodado.

        O `assertLogs` não é decoração: é ele que fixa que a mensagem **é
        registrada** em vez de sumir calada.
        """
        with self.assertLogs("ha_bridge", level="INFO") as capturado:
            fake = _speak("", engine="auto", copilot=True, system=True, code=bridge.EXIT_EMPTY)
        self.assertEqual(fake.programs, [bridge.COPILOT_BIN])
        self.assertTrue(any("[Fala sem voz]" in linha for linha in capturado.output))

    def test_timeout_cai_no_spd_say(self):
        fake = _speak("Bom dia", engine="auto", copilot=True, system=True, timeout=True)
        self.assertEqual(fake.programs, [bridge.COPILOT_BIN, bridge.SPD_BIN])

    def test_modo_estrito_nao_cai_no_spd_say(self):
        with self.assertLogs("ha_bridge", level="INFO"):
            fake = _speak(
                "Bom dia", engine="copilot", copilot=True, system=True, code=bridge.EXIT_NO_TARGET
            )
        self.assertEqual(fake.programs, [bridge.COPILOT_BIN])

    def test_sem_app_nem_spd_say_nao_explode(self):
        """Nem tenta nada: não há boca, e o texto vai para o log."""
        with self.assertLogs("ha_bridge", level="INFO"):
            fake = _speak("Bom dia", engine="auto", copilot=False, system=False)
        self.assertEqual(fake.programs, [])

    def test_system_so_chama_o_spd_say(self):
        fake = _speak("Bom dia", engine="system", copilot=True, system=True)
        self.assertEqual(fake.programs, [bridge.SPD_BIN])

    def test_so_o_copilot_disponivel_usa_o_copilot(self):
        fake = _speak("Bom dia", engine="auto", copilot=True, system=False)
        self.assertEqual(fake.programs, [bridge.COPILOT_BIN])

    def test_sem_boca_a_mensagem_aparece_no_log(self):
        """Silêncio relatado, não engolido — a lição do `spd-say` ausente."""
        with _env(copilot=False, system=False), mock.patch.object(
            bridge, "subprocess", _FakeSubprocess()
        ), self.assertLogs("ha_bridge", level="INFO") as capturado:
            bridge.speak("Bom dia", engine="auto")
        self.assertTrue(any("Bom dia" in linha for linha in capturado.output), capturado.output)


# ---------------------------------------------------------------------------
# A forma do pedido: um argumento só, sem shell
# ---------------------------------------------------------------------------
class RequestShapeTest(unittest.TestCase):
    def test_texto_vai_colado_a_opcao(self):
        """`--announce=<texto>` é um argumento único; nada de `--announce texto`."""
        fake = _speak("Alguém tocou a campainha", engine="auto", copilot=True, system=False)
        self.assertEqual(
            fake.argv_for(bridge.COPILOT_BIN),
            ["/usr/bin/zorin-copilot", "--announce=Alguém tocou a campainha"],
        )

    def test_nao_passa_por_shell(self):
        """A ponte não executa comandos da rede; o texto é dado, não código."""
        fake = _speak("oi; rm -rf /", engine="auto", copilot=True, system=False)
        _, kwargs = fake.calls[0]
        self.assertNotIn("shell", kwargs)

    def test_texto_parecido_com_opcao_nao_vira_opcao(self):
        """Medido com o parser real do GTK: `--announce=--toggle` chega como
        a string `--toggle`, e `--toggle` nem aparece nos argumentos."""
        fake = _speak("--toggle", engine="auto", copilot=True, system=False)
        self.assertEqual(fake.argv_for(bridge.COPILOT_BIN)[1], "--announce=--toggle")

    def test_texto_com_quebra_de_linha_continua_sendo_um_argumento(self):
        fake = _speak("linha 1\nlinha 2", engine="auto", copilot=True, system=False)
        self.assertEqual(len(fake.calls[0][0]), 2)

    def test_timeout_e_configurado(self):
        fake = _speak("Bom dia", engine="auto", copilot=True, system=False)
        _, kwargs = fake.calls[0]
        self.assertEqual(kwargs["timeout"], bridge.SPEAK_TIMEOUT)


# ---------------------------------------------------------------------------
# Fiação: `BridgeState`, `make_speaker`, aviso de mudez
# ---------------------------------------------------------------------------
class SpeakerFactoryTest(unittest.TestCase):
    def test_make_speaker_fala_com_o_motor_pedido(self):
        fake = _FakeSubprocess()
        with _env(copilot=True, system=True), mock.patch.object(bridge, "subprocess", fake):
            bridge.make_speaker("system")("Bom dia")
        self.assertEqual(fake.programs, [bridge.SPD_BIN])

    def test_make_speaker_devolve_callable(self):
        self.assertTrue(callable(bridge.make_speaker()))


class BridgeStateVoiceTest(unittest.TestCase):
    def test_state_sem_falante_injetado_usa_a_voz_escolhida(self):
        fake = _FakeSubprocess()
        with _env(copilot=True, system=True), mock.patch.object(bridge, "subprocess", fake):
            state = bridge.BridgeState(speak=True, voice="system")
            state.process({"event": "teste", "message": "Bom dia"})
        self.assertEqual(fake.programs, [bridge.SPD_BIN])

    def test_falante_injetado_continua_ganhando(self):
        """O contrato antigo de `BridgeState(speaker=...)` não muda."""
        falados: list[str] = []
        state = bridge.BridgeState(speak=True, speaker=falados.append)
        state.process({"event": "teste", "message": "Bom dia"})
        self.assertEqual(falados, ["Bom dia"])

    def test_voice_e_guardado_para_diagnostico(self):
        self.assertEqual(bridge.BridgeState(voice="copilot").voice, "copilot")

    def test_voice_tem_padrao_auto(self):
        self.assertEqual(bridge.BridgeState().voice, bridge.DEFAULT_VOICE)

    def test_sem_speak_nao_fala(self):
        fake = _FakeSubprocess()
        with _env(copilot=True, system=True), mock.patch.object(bridge, "subprocess", fake):
            bridge.BridgeState(speak=False).process({"event": "t", "message": "Bom dia"})
        self.assertEqual(fake.programs, [])

    def test_create_server_aceita_voice(self):
        server = bridge.create_server(port=0, voice="system")
        try:
            self.assertEqual(server.bridge_state.voice, "system")
        finally:
            server.server_close()


class WarnIfMuteTest(unittest.TestCase):
    def test_avisa_quando_nao_ha_boca_nenhuma(self):
        with _env(copilot=False, system=False), self.assertLogs(
            "ha_bridge", level="WARNING"
        ) as capturado:
            self.assertTrue(bridge.warn_if_mute("auto"))
        texto = "\n".join(capturado.output)
        self.assertIn(bridge.COPILOT_BIN, texto)
        self.assertIn(bridge.SPD_BIN, texto)

    def test_nao_avisa_quando_o_copilot_esta_disponivel(self):
        with _env(copilot=True, system=False):
            self.assertFalse(bridge.warn_if_mute("auto"))

    def test_avisa_no_modo_estrito_sem_app(self):
        """`--voice copilot` sem app: fica mudo por escolha, mas o usuário
        precisa saber — senão a caçada começa no lugar errado."""
        with _env(copilot=False, system=True), self.assertLogs(
            "ha_bridge", level="WARNING"
        ):
            self.assertTrue(bridge.warn_if_mute("copilot"))

    def test_modo_estrito_com_app_nao_avisa(self):
        with _env(copilot=True, system=False):
            self.assertFalse(bridge.warn_if_mute("copilot"))


class CliTest(unittest.TestCase):
    def test_voice_entra_no_state_via_linha_de_comando(self):
        """A opção tem de chegar ao `BridgeState`, não só ser aceita pelo parser."""
        criados: list[dict] = []

        class _ServidorFalso:
            def serve_forever(self):
                raise KeyboardInterrupt  # corta antes de escutar de verdade

            def server_close(self):
                pass

        def espiao(*args, **kwargs):
            criados.append(kwargs)
            return _ServidorFalso()

        with mock.patch.object(bridge, "create_server", espiao), self.assertLogs(
            "ha_bridge", level="INFO"
        ):
            codigo = bridge.main(["--voice", "system", "--speak"])
        self.assertEqual(codigo, 0)
        self.assertEqual(criados[0]["voice"], "system")
        self.assertTrue(criados[0]["speak"])

    def test_voice_padrao_e_auto(self):
        criados: list[dict] = []

        class _ServidorFalso:
            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                pass

        def espiao(*args, **kwargs):
            criados.append(kwargs)
            return _ServidorFalso()

        with mock.patch.object(bridge, "create_server", espiao), self.assertLogs(
            "ha_bridge", level="INFO"
        ):
            bridge.main([])
        self.assertEqual(criados[0]["voice"], bridge.DEFAULT_VOICE)

    def test_voice_invalido_e_recusado(self):
        """Valor fora das escolhas morre no argparse, com código de erro."""
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as capturado:
                bridge.main(["--voice", "papagaio"])
        self.assertNotEqual(capturado.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
