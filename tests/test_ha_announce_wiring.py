"""Testes do fio entre eventos da casa e fala — sem display e sem alto-falante.

O que se testa aqui é o **efeito**: quem assina, quem fala e quem se cala. A
regra de decisão tem suíte própria (`tests/test_ha_announce.py`).

Técnica: `types.SimpleNamespace` com os métodos da janela **não vinculados** e
religados por `types.MethodType`. Assim o teste roda sem display e sem subir uma
`CopilotWindow` — que, com o gate de teste desligado, custaria +109 MB e
subprocessos de MCP/RAG por instância (ver a skill de GUI do projeto).
"""

from __future__ import annotations

import collections
import inspect
import re
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from zorin_copilot.core.ha_announce import AnnounceDecision
from zorin_copilot.ui import app as app_module
from zorin_copilot.ui.app import CopilotWindow

try:  # importado como pacote (`tests._window_stub`) quando há `tests/__init__.py`
    from tests._window_stub import bind_window_methods, missing_collaborators
except ImportError:  # importado solto, conforme o rootdir do pytest
    from _window_stub import bind_window_methods, missing_collaborators  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parent.parent
PREFERENCES_PATH = REPO_ROOT / "src" / "zorin_copilot" / "ui" / "preferences.py"


def _event(**overrides):
    base = {
        "kind": "state",
        "entity_id": "light.sala",
        "domain": "light",
        "state": "on",
        "old_state": "off",
        "attributes": {"friendly_name": "Luz da sala"},
        "friendly_name": "Luz da sala",
    }
    base.update(overrides)
    return base


class _WindowStub:
    """Janela de mentira com os métodos reais religados.

    Só os atributos que os métodos tocam — nada de `CopilotWindow.__init__`.
    """

    METHODS = (
        "_init_ha_announcements",
        "_announcement_busy",
        "_own_speech_in_progress",
        "_live_session_active",
        "_claim_speaker",
        "_on_ha_event",
        "_speak_announcement",
        "_report_silent_announcement",
        "_stop_ha_announcements",
        "_reconfigure_ha_announcements",
        "teardown_background_services",
    )

    #: `teardown_background_services` é emprestado só para observar **um** passo
    #: do desligamento (o que cala os anúncios); ele toca meia dúzia de
    #: subsistemas alheios, então não entra na guarda de completude abaixo.
    GUARDED_METHODS = tuple(m for m in METHODS if m != "teardown_background_services")

    #: A política de atenção (`core/attention.py`) passou a decidir *quando* falar.
    #: `_on_ha_event` a consulta, então o stub precisa dos colaboradores dela —
    #: mas **não** dos que tocam o barramento: a sonda de bloqueio de tela é
    #: substituída por uma resposta fixa (ver `_screen_is_locked` abaixo).
    ATTENTION_METHODS = (
        "_attention_policy",
        "_interruption_budget",
        "_note_interruption",
        "_note_badge",
        "_screen_is_locked",
        "_attention_context",
    )

    #: A porta do ambiente (`core/ambient_events.py`, P1): `_init_ha_announcements`
    #: passou a entregar `_on_home_ambient` ao canal, em vez de `_on_ha_event`
    #: direto. A cadeia entra inteira para a guarda cobrir o roteamento novo — o
    #: defeito que ela pega aqui é uma fonte que chega e não é tratada, que não
    #: levanta exceção nenhuma, só não acontece.
    AMBIENT_METHODS = (
        "_on_home_ambient",
        "_on_ambient_event",
        "_on_document_event",
    )

    def __init__(self, **overrides):
        self.config = types.SimpleNamespace(
            ha_enabled=True,
            ha_realtime_enabled=True,
            ha_announce_enabled=True,
            ha_announce_entities="",
            piper_voice_model="pt_BR-faber-medium",
        )
        self.live_client = None
        self._ha_announce_cancel = None
        self._ha_speaker = None
        self._ha_speak_lock = threading.Lock()
        self._ha_speaker_busy = False
        self._ha_voice_missing_warned = False
        self._services_torn_down = False
        self._interruption_log = collections.deque()
        self._attention_badges = 0
        self._screen_lock_probe_warned = False
        self._attention_lock = threading.Lock()
        self.logger = mock.MagicMock()
        self.show_toast = mock.MagicMock()
        bind_window_methods(
            self, self.METHODS + self.ATTENTION_METHODS + self.AMBIENT_METHODS
        )
        for key, value in overrides.items():
            setattr(self, key, value)
        # Depois dos overrides, de propósito: nenhum teste daqui deve falar com o
        # barramento da sessão. O que se testa é a cadeia a partir do evento, e a
        # sonda de bloqueio tem suíte própria em `test_attention_wiring.py`.
        self._screen_is_locked = lambda: False


class StubCompletenessTest(unittest.TestCase):
    """A lista `METHODS` do stub envelhece em silêncio — esta guarda impede.

    Cada método religado chama outros (`self._claim_speaker()`, por exemplo). Se
    um deles for acrescentado em `app.py` e esquecido na lista, o sintoma é um
    `AttributeError` — e só no teste que exercita aquele caminho, nunca no
    arquivo que declara a lista. Foi o que aconteceu: `_claim_speaker` entrou em
    `_on_ha_event` depois de a lista ser escrita, e dois testes ficaram vermelhos
    apontando para o lugar errado.

    Aqui se lê o **código-fonte** dos métodos religados e se cobra cada
    `self.<nome>` que eles tocam. Custa milissegundos e não depende de alguém
    lembrar de nada.
    """

    def test_stub_tem_tudo_o_que_os_metodos_tocam(self):
        win = _WindowStub()
        faltando = missing_collaborators(win, _WindowStub.GUARDED_METHODS)
        self.assertEqual(
            faltando,
            set(),
            "o stub não tem: " + ", ".join(sorted(faltando)),
        )

    def test_a_guarda_esta_olhando_para_metodos_de_verdade(self):
        """Uma guarda que não acha nada porque não olhou para nada é pior do
        que nenhuma guarda."""
        self.assertGreaterEqual(len(_WindowStub.GUARDED_METHODS), 6)
        source = inspect.getsource(getattr(CopilotWindow, "_on_ha_event"))
        self.assertIn("_claim_speaker", source)


class SubscriptionTest(unittest.TestCase):
    """Quem assina o canal — e quem não assina."""

    def setUp(self):
        self.ha = mock.MagicMock()
        self.ha.is_configured.return_value = True
        self.cancel = mock.MagicMock()
        self.ha.on_event.return_value = self.cancel
        patcher = mock.patch(
            "zorin_copilot.core.home_assistant.HomeAssistantManager.get_default",
            return_value=self.ha,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # `_init_ha_announcements` sai cedo sob runner de teste de propósito. O
        # teste do caminho de assinatura precisa passar pelo gate.
        gate = mock.patch.object(app_module, "_is_test_environment", return_value=False)
        gate.start()
        self.addCleanup(gate.stop)
        # O motor de voz é opcional no projeto (extra `voice`). Fixado em "presente"
        # por padrão para que os testes de assinatura não dependam da máquina; o
        # caminho "ausente" tem teste próprio.
        voice = mock.patch.object(app_module, "piper_available", return_value=True)
        voice.start()
        self.addCleanup(voice.stop)

    def test_disabled_does_not_subscribe(self):
        win = _WindowStub()
        win.config.ha_announce_enabled = False
        win._init_ha_announcements()
        self.ha.on_event.assert_not_called()
        self.assertIsNone(win._ha_announce_cancel)

    def test_subscribes_and_keeps_the_cancel(self):
        win = _WindowStub()
        win._init_ha_announcements()
        self.ha.on_event.assert_called_once()
        self.assertIs(win._ha_announce_cancel, self.cancel)
        self.assertIsNotNone(win._ha_speaker)

    def test_does_not_subscribe_without_realtime(self):
        """Sem canal de tempo real não há evento — avisar em vez de assinar à toa."""
        win = _WindowStub()
        win.config.ha_realtime_enabled = False
        win._init_ha_announcements()
        self.ha.on_event.assert_not_called()

    def test_does_not_subscribe_when_ha_is_disabled(self):
        win = _WindowStub()
        win.config.ha_enabled = False
        win._init_ha_announcements()
        self.ha.on_event.assert_not_called()

    def test_does_not_subscribe_when_ha_is_unconfigured(self):
        self.ha.is_configured.return_value = False
        win = _WindowStub()
        win._init_ha_announcements()
        self.ha.on_event.assert_not_called()

    def test_entity_filter_from_config_reaches_the_channel(self):
        win = _WindowStub()
        win.config.ha_announce_entities = " light.sala , binary_sensor.porta "
        win._init_ha_announcements()
        _, kwargs = self.ha.on_event.call_args
        self.assertEqual(kwargs["entity_filter"], {"light.sala", "binary_sensor.porta"})

    def test_empty_filter_becomes_none_not_an_empty_set(self):
        """Conjunto vazio não casaria com entidade nenhuma — tem de virar `None`."""
        win = _WindowStub()
        win.config.ha_announce_entities = "   "
        win._init_ha_announcements()
        _, kwargs = self.ha.on_event.call_args
        self.assertIsNone(kwargs["entity_filter"])

    def test_channel_failure_never_raises(self):
        """Recurso, não requisito: um erro aqui não pode impedir o app de abrir."""
        self.ha.on_event.side_effect = RuntimeError("canal caiu")
        win = _WindowStub()
        win._init_ha_announcements()  # não pode levantar
        self.assertIsNone(win._ha_announce_cancel)


class MissingVoiceEngineTest(unittest.TestCase):
    """Motor de voz ausente tem de ser **dito**, não descoberto como silêncio.

    Medido nesta máquina: `piper-tts` é extra opcional e não está instalado. Sem
    o aviso, o usuário liga os anúncios, nada acontece e não há pista alguma.
    """

    def setUp(self):
        self.ha = mock.MagicMock()
        self.ha.is_configured.return_value = True
        self.ha.on_event.return_value = mock.MagicMock()
        for patcher in (
            mock.patch(
                "zorin_copilot.core.home_assistant.HomeAssistantManager.get_default",
                return_value=self.ha,
            ),
            mock.patch.object(app_module, "_is_test_environment", return_value=False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _with_piper(self, available: bool) -> _WindowStub:
        patcher = mock.patch.object(app_module, "piper_available", return_value=available)
        patcher.start()
        self.addCleanup(patcher.stop)
        win = _WindowStub()
        win._init_ha_announcements()
        return win

    def test_missing_engine_warns_and_toasts(self):
        win = self._with_piper(False)
        win.show_toast.assert_called_once()
        message = win.show_toast.call_args[0][0]
        self.assertIn("voz", message.lower())
        # A assinatura continua valendo: o anúncio fica mudo, mas o fio está feito.
        self.assertIsNotNone(win._ha_announce_cancel)

    def test_present_engine_does_not_warn(self):
        win = self._with_piper(True)
        win.show_toast.assert_not_called()


class SpeakTest(unittest.TestCase):
    """Do evento à fala, com a política de atenção no meio.

    O filtro nomeia a luz de propósito: `light` é `ROUTINE` por natureza, e sem
    a seleção explícita a política a mandaria para o selo. É o caminho **real**
    que se quer exercitar aqui — nada de dublê da política.
    """

    def _armed(self, **overrides):
        win = _WindowStub(**overrides)
        # Sem isto a luz seria `ROUTINE` e a política a mandaria para o selo —
        # ver `SelecaoExplicitaTest` em `test_attention.py`.
        win.config.ha_announce_entities = win.config.ha_announce_entities or "light.sala"
        spoken: list[str] = []
        done = threading.Event()

        def recorder(text):
            spoken.append(text)
            with win._ha_speak_lock:
                win._ha_speaker_busy = False
            done.set()

        win._speak_announcement = recorder
        return win, spoken, done

    def test_mapped_event_is_spoken(self):
        win, spoken, done = self._armed()
        win._on_ha_event(_event())
        self.assertTrue(done.wait(3.0), "a fala não foi agendada")
        self.assertEqual(spoken, ["Luz da sala acendeu."])

    def test_busy_flag_goes_up_and_comes_back_down(self):
        win, spoken, done = self._armed()
        win._on_ha_event(_event())
        self.assertTrue(done.wait(3.0))
        self.assertFalse(win._ha_speaker_busy, "a trava ficou presa")

    def test_silent_event_is_not_spoken(self):
        win, spoken, done = self._armed()
        win._on_ha_event(_event(state="unavailable"))
        self.assertFalse(done.wait(0.2))
        self.assertEqual(spoken, [])
        self.assertFalse(win._ha_speaker_busy)

    def test_unmapped_domain_is_not_spoken(self):
        win, spoken, done = self._armed()
        win._on_ha_event(_event(domain="sensor", entity_id="sensor.t", state="23.5"))
        self.assertFalse(done.wait(0.2))
        self.assertEqual(spoken, [])

    def test_filtered_event_is_not_spoken(self):
        win, spoken, done = self._armed()
        win.config.ha_announce_entities = "light.quarto"
        win._on_ha_event(_event())
        self.assertFalse(done.wait(0.2))
        self.assertEqual(spoken, [])

    def test_disabled_does_not_speak(self):
        win, spoken, done = self._armed()
        win.config.ha_announce_enabled = False
        win._on_ha_event(_event())
        self.assertFalse(done.wait(0.2))
        self.assertEqual(spoken, [])

    def test_does_not_speak_over_its_own_speech(self):
        """A trava é a garantia: falar por cima da própria fala é o defeito."""
        win, spoken, done = self._armed(_ha_speaker_busy=True)
        win._on_ha_event(_event())
        self.assertFalse(done.wait(0.2))
        self.assertEqual(spoken, [])
        self.assertTrue(win._ha_speaker_busy, "o teste não deve baixar a trava do outro")

    def test_speak_failure_clears_the_busy_flag(self):
        """O `finally` do `_speak_announcement` é o que devolve o alto-falante."""
        win = _WindowStub()
        win._ha_speaker = mock.MagicMock()
        win._ha_speaker.speak.side_effect = RuntimeError("piper explodiu")
        win._ha_speaker_busy = True
        win._speak_announcement("texto")  # não pode levantar
        self.assertFalse(win._ha_speaker_busy)

    def test_speak_returning_false_is_reported(self):
        """`speak()` devolve `False` quando não conseguiu falar.

        É o caminho que a ponte usa: ela recebe "aceito" do app e não tem como
        saber que nada saiu pelo alto-falante. Descartar o retorno fazia o
        anúncio sumir sem uma linha ligando o silêncio à causa.
        """
        win = _WindowStub()
        win._ha_speaker = mock.MagicMock()
        win._ha_speaker.speak.return_value = False
        win._ha_speaker_busy = True
        with mock.patch.object(app_module.GLib, "idle_add") as agendado:
            with self.assertLogs("zorin_copilot.ui.app", level="WARNING") as capturado:
                win._speak_announcement("Alguém tocou a campainha")
        self.assertFalse(win._ha_speaker_busy)
        texto = "\n".join(capturado.output)
        self.assertIn("não saiu pelo alto-falante", texto)
        self.assertIn("Alguém tocou a campainha", texto)
        # E o aviso na tela, porque o log do app vai para `/dev/null` quando ele
        # é iniciado pela sessão — o log sozinho não alcança o usuário.
        self.assertEqual(agendado.call_count, 1)
        self.assertIs(agendado.call_args.args[0], win.show_toast)

    def test_the_screen_warning_is_not_repeated_for_every_announcement(self):
        """Quem não tem motor de voz não precisa ser avisado a cada campainha."""
        win = _WindowStub()
        win._ha_speaker = mock.MagicMock()
        win._ha_speaker.speak.return_value = False
        with mock.patch.object(app_module.GLib, "idle_add") as agendado:
            with self.assertLogs("zorin_copilot.ui.app", level="WARNING"):
                for _ in range(3):
                    win._speak_announcement("Bom dia")
        self.assertEqual(agendado.call_count, 1, "o toast repetiu")

    def test_speak_returning_true_is_not_reported_as_failure(self):
        """O contrário também precisa de guarda: sucesso não pode virar alarme."""
        win = _WindowStub()
        win._ha_speaker = mock.MagicMock()
        win._ha_speaker.speak.return_value = True
        win._ha_speaker_busy = True
        with mock.patch.object(app_module.GLib, "idle_add") as agendado:
            win._speak_announcement("Alguém tocou a campainha")
        self.assertFalse(win._ha_speaker_busy)
        agendado.assert_not_called()
        self.assertFalse(win._ha_voice_missing_warned)

    def test_speak_without_speaker_does_not_raise(self):
        win = _WindowStub()
        win._ha_speaker = None
        win._ha_speaker_busy = True
        win._speak_announcement("texto")
        self.assertFalse(win._ha_speaker_busy)


class BusyTest(unittest.TestCase):
    """`_announcement_busy` — as três fontes que impedem a fala."""

    def test_idle_window_is_not_busy(self):
        self.assertFalse(_WindowStub()._announcement_busy())

    def test_own_speech_makes_it_busy(self):
        self.assertTrue(_WindowStub(_ha_speaker_busy=True)._announcement_busy())

    def test_active_live_session_makes_it_busy(self):
        """Conversa por voz em andamento: não é hora de avisar que a porta abriu."""
        client = mock.MagicMock()
        client.is_active.return_value = True
        self.assertTrue(_WindowStub(live_client=client)._announcement_busy())

    def test_inactive_live_session_does_not_make_it_busy(self):
        client = mock.MagicMock()
        client.is_active.return_value = False
        client.synthesizer.is_speaking = False
        self.assertFalse(_WindowStub(live_client=client)._announcement_busy())

    def test_local_synthesizer_speaking_makes_it_busy(self):
        client = mock.MagicMock()
        client.is_active.return_value = False
        client.synthesizer.is_speaking = True
        self.assertTrue(_WindowStub(live_client=client)._announcement_busy())

    def test_client_without_synthesizer_does_not_break(self):
        """O cliente do Gemini Live não tem sintetizador local."""
        client = mock.MagicMock(spec=["is_active"])
        client.is_active.return_value = False
        self.assertFalse(_WindowStub(live_client=client)._announcement_busy())


class TeardownTest(unittest.TestCase):
    def test_stop_cancels_the_subscription_and_interrupts(self):
        cancel = mock.MagicMock()
        speaker = mock.MagicMock()
        win = _WindowStub(_ha_announce_cancel=cancel, _ha_speaker=speaker)
        win._stop_ha_announcements()
        cancel.assert_called_once()
        speaker.interrupt.assert_called_once()
        self.assertIsNone(win._ha_announce_cancel)

    def test_stop_is_idempotent(self):
        cancel = mock.MagicMock()
        win = _WindowStub(_ha_announce_cancel=cancel, _ha_speaker=mock.MagicMock())
        win._stop_ha_announcements()
        win._stop_ha_announcements()
        cancel.assert_called_once()

    def test_stop_survives_a_broken_cancel(self):
        cancel = mock.MagicMock(side_effect=RuntimeError("assinatura já morta"))
        win = _WindowStub(_ha_announce_cancel=cancel, _ha_speaker=mock.MagicMock())
        win._stop_ha_announcements()  # não pode levantar
        self.assertIsNone(win._ha_announce_cancel)

    def test_teardown_stops_announcements(self):
        """O canal é singleton e sobrevive à janela: o callback tem de sair junto."""
        win = _WindowStub()
        win.live_client = None
        win.wake_word_engine = None
        win.rag = None
        win.mcp_manager = None
        win._cancel_pending_end_session = mock.MagicMock()
        win._stop_ha_announcements = mock.MagicMock(wraps=win._stop_ha_announcements)
        win.teardown_background_services()
        win._stop_ha_announcements.assert_called_once()

    def test_reconfigure_stops_then_starts(self):
        win = _WindowStub()
        win._stop_ha_announcements = mock.MagicMock()
        win._init_ha_announcements = mock.MagicMock()
        win._reconfigure_ha_announcements()
        win._stop_ha_announcements.assert_called_once()
        win._init_ha_announcements.assert_called_once()


class CollectorGuardTest(unittest.TestCase):
    """Guarda contra o campo novo sumir ao salvar as preferências.

    `_collect_current_config` parte de um `CopilotConfig()` **novo** e reatribui
    campo por campo. Um campo esquecido volta ao default toda vez que o usuário
    salva — e o sintoma é a preferência "não pegar", sem erro nenhum. É a
    armadilha que a skill de GUI do projeto registra.
    """

    KEYS = ("ha_announce_enabled", "ha_announce_entities")

    def _collector_source(self) -> str:
        source = PREFERENCES_PATH.read_text(encoding="utf-8")
        start = source.index("def _collect_current_config")
        rest = source[start + 1 :]
        match = re.search(r"\n    def ", rest)
        return rest[: match.start()] if match else rest

    def test_collector_reattributes_the_new_fields(self):
        body = self._collector_source()
        for key in self.KEYS:
            with self.subTest(key=key):
                self.assertIn(
                    f"cfg.{key}",
                    body,
                    f"{key} não é reatribuído em _collect_current_config: "
                    "a preferência voltaria ao default a cada salvamento.",
                )

    def test_loader_fills_the_new_rows(self):
        source = PREFERENCES_PATH.read_text(encoding="utf-8")
        for attr in ("self.ha_announce_row", "self.ha_announce_entities_row"):
            with self.subTest(attr=attr):
                self.assertIn(attr, source)

    def test_rows_are_added_to_the_ha_group(self):
        source = PREFERENCES_PATH.read_text(encoding="utf-8")
        self.assertIn("ha_group.add(self.ha_announce_row)", source)
        self.assertIn("ha_group.add(self.ha_announce_entities_row)", source)


class PiperAvailabilityTest(unittest.TestCase):
    """`piper_available()` é a checagem que transforma silêncio em aviso."""

    def test_returns_a_bool_and_does_not_raise(self):
        from zorin_copilot.ai.local_voice import piper_available

        self.assertIsInstance(piper_available(), bool)

    def test_maps_find_spec_to_a_bool(self):
        from zorin_copilot.ai.local_voice import piper_available

        with mock.patch(
            "zorin_copilot.ai.local_voice.importlib.util.find_spec",
            return_value=object(),
        ):
            self.assertTrue(piper_available())
        with mock.patch(
            "zorin_copilot.ai.local_voice.importlib.util.find_spec",
            return_value=None,
        ):
            self.assertFalse(piper_available())


class ImportGuardTest(unittest.TestCase):
    """O módulo de decisão precisa continuar puro.

    Se alguém importar GTK aqui, a regra deixa de ser testável sem display — que
    é justamente o motivo de o módulo existir separado da janela.
    """

    def test_decision_module_has_no_gtk(self):
        source = inspect.getsource(
            __import__("zorin_copilot.core.ha_announce", fromlist=["x"])
        )
        for forbidden in ("import gi", "from gi.repository", "Adw", "Gtk."):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_decision_enum_is_stable(self):
        """Fixado para que renomear um desfecho exija atualizar quem o lê."""
        self.assertEqual(
            {member.value for member in AnnounceDecision},
            {
                "announce",
                "disabled",
                "not_state",
                "no_state",
                "filtered",
                "unchanged",
                "no_phrase",
                "busy",
            },
        )


if __name__ == "__main__":
    unittest.main()
