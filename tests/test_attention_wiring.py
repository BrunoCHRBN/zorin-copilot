"""Testes do fio entre a política de atenção e o caminho de anúncio — sem display.

O que se testa aqui é o **efeito**: quem consulta a política, quem fala, quem mostra
aviso, quem acumula selo e quem não faz nada. A regra de "qual nível" tem suíte
própria (`tests/test_attention.py`).

Técnica: o mesmo stub de `tests/_window_stub.py` — métodos reais religados num objeto
simples. Subir uma `CopilotWindow` de verdade custa +109 MB e subprocessos de MCP/RAG.

O contexto do momento entra **substituído** nos testes de roteamento: ele depende de
`datetime.now()` e do barramento da sessão, e um teste que muda de resultado conforme
a hora do dia não é um teste.
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

from zorin_copilot.core.attention import (
    AttentionContext,
    AttentionLevel,
    AttentionReason,
    Severity,
)
from zorin_copilot.core.ha_announce import AnnounceDecision
from zorin_copilot.ui import app as app_module

try:  # importado como pacote (`tests._window_stub`) quando há `tests/__init__.py`
    from tests._window_stub import bind_window_methods, missing_collaborators
except ImportError:  # importado solto, conforme o rootdir do pytest
    from _window_stub import bind_window_methods, missing_collaborators  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parent.parent
PREFERENCES_PATH = REPO_ROOT / "src" / "zorin_copilot" / "ui" / "preferences.py"


class _WindowStub:
    """Janela de mentira com os métodos da atenção religados."""

    METHODS = (
        "_own_speech_in_progress",
        "_live_session_active",
        "_announcement_busy",
        "_attention_policy",
        "_interruption_budget",
        "_note_interruption",
        "_note_badge",
        "_screen_is_locked",
        "_attention_context",
        "_claim_speaker",
        # A porta do ambiente (`core/ambient_events.py`) entrou no caminho do
        # evento no P1. Entra **inteira** de propósito: a guarda de completude lê
        # o fonte de cada método religado, então parar no meio faria o roteamento
        # novo ficar sem cobertura — que é justamente onde ele pode errar em
        # silêncio (uma fonte sem tratamento não levanta, só não acontece).
        "_on_home_ambient",
        "_on_ambient_event",
        "_on_document_event",
        "_on_ha_event",
        "_speak_announcement",
        # Não é exercitado aqui (tem suíte própria em `test_ha_announce_wiring.py`),
        # mas `_speak_announcement` o chama — e a guarda de completude lê o
        # código-fonte, não o caminho percorrido. Foi ela que apontou a falta.
        "_report_silent_announcement",
    )

    def __init__(self, **overrides):
        self.config = types.SimpleNamespace(
            ha_enabled=True,
            ha_realtime_enabled=True,
            ha_announce_enabled=True,
            ha_announce_entities="",
            attention_enabled=True,
            attention_silence_hours="",
            attention_max_per_hour=6,
            attention_away_minutes=30,
        )
        self.live_client = None
        self._ha_speaker_busy = False
        self._ha_speak_lock = threading.Lock()
        self._ha_speaker = None
        self._ha_voice_missing_warned = False
        self._interruption_log = collections.deque()
        self._attention_badges = 0
        self._screen_lock_probe_warned = False
        #: Duas threads produtoras desde o P1 — ver `CopilotWindow.__init__`.
        self._attention_lock = threading.Lock()
        self.logger = mock.MagicMock()
        self.show_toast = mock.MagicMock()
        bind_window_methods(self, self.METHODS)
        for key, value in overrides.items():
            setattr(self, key, value)
        # Depois dos overrides: nenhum teste daqui fala com o barramento.
        self._screen_is_locked = lambda: False


def _evento(**overrides):
    """Evento `NORMAL` por natureza: uma porta que abriu.

    Porta e não luz de propósito — `light` é `ROUTINE`, e a maioria destes testes
    quer exercitar o caminho em que a política **pode** falar.
    """
    base = {
        "kind": "state",
        "entity_id": "binary_sensor.porta",
        "domain": "binary_sensor",
        "state": "on",
        "old_state": "off",
        "attributes": {"device_class": "door"},
        "friendly_name": "Porta da sala",
    }
    base.update(overrides)
    return base


def _luz(**overrides):
    base = {
        "kind": "state",
        "entity_id": "light.sala",
        "domain": "light",
        "state": "on",
        "old_state": "off",
        "attributes": {},
        "friendly_name": "Luz da sala",
    }
    base.update(overrides)
    return base


class StubCompletenessTest(unittest.TestCase):
    """A lista `METHODS` envelhece em silêncio — a guarda compartilhada impede."""

    def test_stub_tem_tudo_o_que_os_metodos_tocam(self):
        win = _WindowStub()
        faltando = missing_collaborators(win, _WindowStub.METHODS)
        self.assertEqual(faltando, set(), "o stub não tem: " + ", ".join(sorted(faltando)))

    def test_a_guarda_esta_olhando_para_metodos_de_verdade(self):
        self.assertGreaterEqual(len(_WindowStub.METHODS), 10)


# ---------------------------------------------------------------------------
# Os ajustes
# ---------------------------------------------------------------------------
class PolicyTest(unittest.TestCase):
    def test_le_o_config(self):
        win = _WindowStub()
        win.config.attention_enabled = False
        win.config.attention_silence_hours = "23-7,13-14"
        win.config.attention_max_per_hour = 3
        win.config.attention_away_minutes = 5
        politica = win._attention_policy()
        self.assertFalse(politica.enabled)
        self.assertEqual(politica.silence_windows, ((23, 7), (13, 14)))
        self.assertEqual(politica.max_per_hour, 3)
        self.assertEqual(politica.away_seconds, 300.0)

    def test_lixo_no_config_nao_levanta(self):
        """Preferência corrompida não pode derrubar o caminho do evento."""
        win = _WindowStub()
        win.config.attention_max_per_hour = "muitas"
        win.config.attention_away_minutes = "meia hora"
        politica = win._attention_policy()
        self.assertEqual(politica.max_per_hour, 6)
        self.assertEqual(politica.away_seconds, 1800.0)

    def test_campo_ausente_usa_o_default(self):
        win = _WindowStub()
        win.config = types.SimpleNamespace()
        politica = win._attention_policy()
        self.assertTrue(politica.enabled)
        self.assertEqual(politica.silence_windows, ())
        self.assertEqual(politica.away_seconds, 1800.0)

    def test_ausencia_negativa_nao_vira_ausencia_permanente(self):
        """Zero é escolha legítima ("sempre presente"); negativo não é."""
        win = _WindowStub()
        win.config.attention_away_minutes = -5
        self.assertEqual(win._attention_policy().away_seconds, 0.0)

    def test_e_lida_a_cada_evento(self):
        """Não há estado a rearmar: salvar as preferências já vale para o próximo."""
        win = _WindowStub()
        self.assertEqual(win._attention_policy().max_per_hour, 6)
        win.config.attention_max_per_hour = 1
        self.assertEqual(win._attention_policy().max_per_hour, 1)


class BudgetTest(unittest.TestCase):
    def test_orcamento_comeca_vazio(self):
        self.assertEqual(_WindowStub()._interruption_budget().spoken_last_hour, 0)

    def test_falar_entra_no_orcamento(self):
        win = _WindowStub()
        win._note_interruption()
        win._note_interruption()
        self.assertEqual(win._interruption_budget().spoken_last_hour, 2)

    def test_fala_antiga_sai_da_janela(self):
        """A janela desliza: uma fala de duas horas atrás não conta mais."""
        win = _WindowStub()
        agora = 10_000.0
        win._interruption_log.extend([agora - 7200.0, agora - 1800.0, agora - 10.0])
        self.assertEqual(
            win._interruption_budget(now=agora).spoken_last_hour, 2
        )

    def test_a_poda_de_fato_encolhe_a_lista(self):
        """Não é só a contagem: a lista não pode crescer para sempre."""
        win = _WindowStub()
        agora = 10_000.0
        win._interruption_log.extend([agora - 7200.0, agora - 1800.0])
        win._interruption_budget(now=agora)
        self.assertEqual(list(win._interruption_log), [agora - 1800.0])

    def test_o_limite_vem_da_politica(self):
        win = _WindowStub()
        win.config.attention_max_per_hour = 1
        win._note_interruption()
        self.assertTrue(win._interruption_budget().exhausted)

    def test_zero_significa_que_nada_cabe(self):
        win = _WindowStub()
        win.config.attention_max_per_hour = 0
        self.assertTrue(win._interruption_budget().exhausted)


# ---------------------------------------------------------------------------
# O contexto
# ---------------------------------------------------------------------------
class ContextTest(unittest.TestCase):
    def test_a_hora_vem_do_relogio_local(self):
        from datetime import datetime

        win = _WindowStub()
        self.assertEqual(win._attention_context().hour, datetime.now().hour)

    def test_a_ociosidade_e_desconhecida(self):
        """Medido: não há fonte confiável nesta máquina — ver `AttentionContext`."""
        self.assertIsNone(_WindowStub()._attention_context().idle_seconds)

    def test_fala_propria_e_distingue_de_sessao_ao_vivo(self):
        """A política trata as duas de forma diferente; o contexto não pode fundir."""
        falando = _WindowStub(_ha_speaker_busy=True)
        self.assertTrue(falando._attention_context().speaking)
        self.assertFalse(falando._attention_context().live_session_active)

        cliente = mock.MagicMock()
        cliente.is_active.return_value = True
        ao_vivo = _WindowStub(live_client=cliente)
        self.assertFalse(ao_vivo._attention_context().speaking)
        self.assertTrue(ao_vivo._attention_context().live_session_active)

    def test_sintetizador_falando_conta_como_sessao_ao_vivo(self):
        cliente = mock.MagicMock()
        cliente.is_active.return_value = False
        cliente.synthesizer.is_speaking = True
        self.assertTrue(_WindowStub(live_client=cliente)._live_session_active())

    def test_bloqueio_de_tela_vem_da_sonda(self):
        win = _WindowStub()
        win._screen_is_locked = lambda: True
        self.assertTrue(win._attention_context().screen_locked)


class ScreenLockProbeTest(unittest.TestCase):
    """A sonda de bloqueio tem de ser **honesta sobre não saber**.

    O stub troca `_screen_is_locked` por um lambda fixo para que o resto da suíte
    não fale com o barramento da sessão. Aqui é justamente ela que está sob
    teste, então o lambda sai e o método **real** entra: sem isto os três casos
    abaixo passariam (ou falhariam) por causa do dublê, e não da sonda — o
    primeiro deles passava vazio, que é a pior forma de um teste passar.
    """

    def setUp(self):
        self.win = _WindowStub()
        self.win._screen_is_locked = types.MethodType(
            app_module.CopilotWindow._screen_is_locked, self.win
        )

    def test_falha_de_barramento_devolve_desbloqueado(self):
        """Presumir bloqueio calaria o assistente sem deixar rastro."""
        with mock.patch(
            "gi.repository.Gio.bus_get_sync", side_effect=RuntimeError("sem barramento")
        ):
            self.assertFalse(self.win._screen_is_locked())

    def test_falha_e_registrada_uma_vez_so(self):
        """Uma vez por execução: não poluir o log a cada evento da casa."""
        with mock.patch(
            "gi.repository.Gio.bus_get_sync", side_effect=RuntimeError("sem barramento")
        ), mock.patch.object(app_module, "logger") as log:
            for _ in range(4):
                self.win._screen_is_locked()
        self.assertEqual(log.debug.call_count, 1)
        self.assertTrue(self.win._screen_lock_probe_warned)

    def test_resposta_do_barramento_e_lida(self):
        barramento = mock.MagicMock()
        barramento.call_sync.return_value.unpack.return_value = (True,)
        with mock.patch("gi.repository.Gio.bus_get_sync", return_value=barramento):
            self.assertTrue(self.win._screen_is_locked())

    def test_bloqueio_negativo_nao_vira_afirmativo(self):
        """O outro lado: um `False` do barramento não pode ser lido como bloqueio."""
        barramento = mock.MagicMock()
        barramento.call_sync.return_value.unpack.return_value = (False,)
        with mock.patch("gi.repository.Gio.bus_get_sync", return_value=barramento):
            self.assertFalse(self.win._screen_is_locked())

    def test_a_sonda_e_consultada_uma_vez_por_evento(self):
        """Ela é o único ponto de contato com o barramento — vale saber o custo."""
        with mock.patch(
            "gi.repository.Gio.bus_get_sync", return_value=mock.MagicMock()
        ) as sonda:
            self.win._attention_context()
        self.assertEqual(sonda.call_count, 1)


# ---------------------------------------------------------------------------
# O roteamento: evento -> nível -> efeito
# ---------------------------------------------------------------------------
class RoutingTest(unittest.TestCase):
    """O que `_on_ha_event` faz com cada nível da política."""

    def _win(self, *, contexto=None, **overrides):
        win = _WindowStub(**overrides)
        if contexto is not None:
            win._attention_context = lambda: contexto
        return win

    def _falado(self, win, evento=None) -> list[str]:
        """Roda o evento e devolve o que foi falado, esperando a thread.

        O evento é parâmetro e **não** um valor fixo embutido aqui: a versão
        anterior disparava sempre o mesmo evento, e um teste que queria a luz
        media a porta — ficava verde ou vermelho pelo motivo errado.

        E só se espera quando há fala para esperar: `_claim_speaker` marca
        `_ha_speaker_busy` **antes** de a thread nascer, então marca baixa com a
        lista vazia significa "nada foi falado", e não há o que aguardar. Sem
        isso cada caso negativo pagava o timeout inteiro (medido: 24 s de
        módulo, quase tudo à espera de uma fala que nunca viria).
        """
        falado: list[str] = []
        pronto = threading.Event()

        def recorder(text):
            falado.append(text)
            with win._ha_speak_lock:
                win._ha_speaker_busy = False
            pronto.set()

        win._speak_announcement = recorder
        win._on_ha_event(_evento() if evento is None else evento)
        if win._ha_speaker_busy:
            self.assertTrue(pronto.wait(3.0), "a fala não terminou a tempo")
        return falado

    def test_presente_e_orcamento_sobrando_fala(self):
        win = self._win(contexto=AttentionContext(hour=14, idle_seconds=None))
        self.assertEqual(self._falado(win), ["Porta da sala abriu."])

    def test_falar_entra_no_orcamento(self):
        win = self._win(contexto=AttentionContext(hour=14, idle_seconds=None))
        self._falado(win)
        self.assertEqual(win._interruption_budget().spoken_last_hour, 1)

    def test_janela_de_silencio_nao_faz_nada(self):
        """O critério de pronto do roteiro, no fio: silêncio não fala nem avisa."""
        win = self._win(
            contexto=AttentionContext(hour=2, idle_seconds=None)
        )
        win.config.attention_silence_hours = "23-7"
        with mock.patch.object(app_module.GLib, "idle_add") as agendado:
            self.assertEqual(self._falado(win), [])
        agendado.assert_not_called()
        self.assertEqual(win._attention_badges, 0)

    def test_urgente_fura_a_janela_de_silencio(self):
        """A condição que justifica o nível urgente, medida no fio."""
        win = self._win(contexto=AttentionContext(hour=3, idle_seconds=None))
        win.config.attention_silence_hours = "23-7"
        fumaca = _evento(
            entity_id="binary_sensor.fumaca",
            attributes={"device_class": "smoke"},
            friendly_name="Sensor de fumaça",
        )
        self.assertEqual(self._falado(win, fumaca), ["Sensor de fumaça detectou fumaça."])

    def test_sessao_ao_vivo_vira_aviso_visual_sem_falar(self):
        win = self._win(
            contexto=AttentionContext(hour=14, live_session_active=True, idle_seconds=None)
        )
        with mock.patch.object(app_module.GLib, "idle_add") as agendado:
            self.assertEqual(self._falado(win), [])
        agendado.assert_called_once()
        self.assertIs(agendado.call_args.args[0], win.show_toast)
        self.assertEqual(agendado.call_args.args[1], "Porta da sala abriu.")

    def test_corriqueiro_durante_sessao_ao_vivo_nao_vira_aviso(self):
        """Regressão: o defeito que a varredura exaustiva pegou.

        A sessão ao vivo rebaixava a fala para aviso visual — e fazia isso para
        **qualquer** severidade, inclusive `ROUTINE`. Resultado: uma luz acendendo
        durante uma conversa por voz dava um toast, ou seja, recebia **mais**
        atenção do que o mesmo evento teria sem a sessão. Aqui se cobra o desfecho
        no fio, e não só a regra no módulo puro.
        """
        win = self._win(
            contexto=AttentionContext(hour=14, live_session_active=True, idle_seconds=None)
        )
        with mock.patch.object(app_module.GLib, "idle_add") as agendado:
            self._falado(win, _luz())
        agendado.assert_not_called()
        self.assertEqual(win._attention_badges, 1)

    def test_orcamento_estourado_acumula_em_selo(self):
        win = self._win(contexto=AttentionContext(hour=14, idle_seconds=None))
        win.config.attention_max_per_hour = 1
        win._note_interruption()
        with mock.patch.object(app_module.GLib, "idle_add") as agendado:
            self.assertEqual(self._falado(win), [])
        agendado.assert_not_called()
        self.assertEqual(win._attention_badges, 1)

    def test_tela_bloqueada_acumula_em_selo(self):
        win = self._win(
            contexto=AttentionContext(hour=14, screen_locked=True, idle_seconds=None)
        )
        self.assertEqual(self._falado(win), [])
        self.assertEqual(win._attention_badges, 1)

    def test_ausente_acumula_em_selo(self):
        win = self._win(
            contexto=AttentionContext(hour=14, idle_seconds=99999.0)
        )
        self.assertEqual(self._falado(win), [])
        self.assertEqual(win._attention_badges, 1)

    def test_evento_corriqueiro_acumula_em_selo(self):
        win = self._win(contexto=AttentionContext(hour=14, idle_seconds=None))
        win._on_ha_event(_luz())
        self.assertEqual(win._attention_badges, 1)

    def test_nomear_a_entidade_no_filtro_faz_ela_falar(self):
        """Sem isto o filtro seria um botão que não faz nada — `light` é `ROUTINE`."""
        win = self._win(contexto=AttentionContext(hour=14, idle_seconds=None))
        win.config.ha_announce_entities = "light.sala"
        self.assertEqual(self._falado(win, _luz()), ["Luz da sala acendeu."])

    def test_o_ajudante_respeita_o_evento_que_recebe(self):
        """Guarda contra o defeito que este ajudante já teve.

        Ele disparava sempre a mesma porta, então um caso que queria a luz media
        outra coisa. Os dois eventos abaixo dão desfechos **diferentes** de
        propósito (`NORMAL` fala, `ROUTINE` não): se o ajudante voltasse a
        ignorar o parâmetro, esta comparação deixaria de distinguir os dois.
        """
        porta = self._falado(
            self._win(contexto=AttentionContext(hour=14, idle_seconds=None)), _evento()
        )
        luz = self._falado(
            self._win(contexto=AttentionContext(hour=14, idle_seconds=None)), _luz()
        )
        self.assertEqual(porta, ["Porta da sala abriu."])
        self.assertEqual(luz, [])

    def test_politica_desligada_nao_fala_nem_avisa(self):
        win = self._win(contexto=AttentionContext(hour=14, idle_seconds=None))
        win.config.attention_enabled = False
        with mock.patch.object(app_module.GLib, "idle_add") as agendado:
            self.assertEqual(self._falado(win), [])
        agendado.assert_not_called()
        self.assertEqual(win._attention_badges, 0)

    def test_anuncio_desligado_nao_chega_a_politica(self):
        """A primeira pergunta é de `ha_announce`; a política nem é consultada."""
        win = self._win(contexto=AttentionContext(hour=14, idle_seconds=None))
        win.config.ha_announce_enabled = False
        with mock.patch.object(win, "_attention_context") as contexto:
            win._on_ha_event(_evento())
        contexto.assert_not_called()

    def test_fala_em_andamento_nao_e_atropelada(self):
        """Rebaixa em vez de descartar: a frase não se perde."""
        win = self._win(
            contexto=AttentionContext(hour=14, speaking=True, idle_seconds=None)
        )
        self.assertEqual(self._falado(win), [])
        self.assertEqual(win._attention_badges, 1)

    def test_evento_sem_frase_nao_chega_a_politica(self):
        win = self._win(contexto=AttentionContext(hour=14, idle_seconds=None))
        with mock.patch.object(win, "_attention_context") as contexto:
            win._on_ha_event(_evento(state="unavailable"))
        contexto.assert_not_called()

    def test_sem_motor_de_voz_o_aviso_visual_continua_valendo(self):
        """A política é ortogonal ao motor de voz: o aviso não depende de áudio."""
        win = self._win(
            contexto=AttentionContext(hour=14, live_session_active=True, idle_seconds=None)
        )
        with mock.patch.object(app_module.GLib, "idle_add") as agendado:
            win._on_ha_event(_evento())
        agendado.assert_called_once()


class NivelDoEventoTest(unittest.TestCase):
    """A severidade que chega à política é a do **evento**, não um default."""

    def _severidade_vista(self, evento, *, filtro="") -> Severity:
        win = _WindowStub()
        win.config.ha_announce_entities = filtro
        win._attention_context = lambda: AttentionContext(hour=14, idle_seconds=None)
        visto: list[Severity] = []
        real = app_module.decide_attention

        def espiao(*, severity, context, budget, policy):
            visto.append(severity)
            return real(
                severity=severity, context=context, budget=budget, policy=policy
            )

        with mock.patch.object(app_module, "decide_attention", espiao):
            win._on_ha_event(evento)
        return visto[0]

    def test_fumaca_chega_como_urgente(self):
        self.assertIs(
            self._severidade_vista(
                _evento(
                    entity_id="binary_sensor.fumaca",
                    attributes={"device_class": "smoke"},
                )
            ),
            Severity.URGENT,
        )

    def test_porta_chega_como_normal(self):
        self.assertIs(self._severidade_vista(_evento()), Severity.NORMAL)

    def test_luz_chega_como_corriqueira(self):
        self.assertIs(self._severidade_vista(_luz()), Severity.ROUTINE)

    def test_luz_nomeada_chega_como_normal(self):
        """O filtro é consultado **no evento**, não uma vez no arranque."""
        self.assertIs(
            self._severidade_vista(_luz(), filtro="light.sala"), Severity.NORMAL
        )

    def test_a_severidade_nao_e_constante(self):
        """Guarda contra a política receber sempre o mesmo valor por engano."""
        valores = {
            self._severidade_vista(_evento()),
            self._severidade_vista(_luz()),
            self._severidade_vista(
                _evento(
                    entity_id="binary_sensor.fumaca",
                    attributes={"device_class": "smoke"},
                )
            ),
        }
        self.assertEqual(valores, {Severity.NORMAL, Severity.ROUTINE, Severity.URGENT})


class PreferenciasDaAtencaoTest(unittest.TestCase):
    """A tela das preferências é onde um campo novo some em silêncio.

    `_collect_current_config` parte de um `CopilotConfig()` **novo** e reatribui
    campo por campo. Um campo esquecido volta ao default toda vez que o usuário
    salva, e o sintoma é a preferência "não pegar", sem erro nenhum — a mesma
    armadilha que a skill de GUI do projeto registra.

    Aqui se lê o **código-fonte** da tela. Subir a `PreferencesWindow` exigiria
    display, e o que se quer fixar é o contrato do coletor, não o desenho dos
    widgets.
    """

    #: Campos que o usuário controla por uma linha da tela.
    CAMPOS_COM_LINHA = (
        "attention_enabled",
        "attention_silence_hours",
        "attention_max_per_hour",
    )
    #: Campo **sem** linha: a condição de ausência não tem fonte de ociosidade
    #: nesta máquina, então um controle aqui não mudaria nada. Mesmo tratamento
    #: de `memory_consolidation_interval_hours`.
    CAMPO_SEM_LINHA = "attention_away_minutes"
    LINHAS = ("attention_row", "attention_silence_row", "attention_budget_row")

    def _fonte(self) -> str:
        return PREFERENCES_PATH.read_text(encoding="utf-8")

    def _coletor(self) -> str:
        fonte = self._fonte()
        inicio = fonte.index("def _collect_current_config")
        resto = fonte[inicio + 1 :]
        match = re.search(r"\n    def ", resto)
        return resto[: match.start()] if match else resto

    def test_o_coletor_reatribui_os_campos_com_linha(self):
        corpo = self._coletor()
        for chave in self.CAMPOS_COM_LINHA:
            with self.subTest(chave=chave):
                self.assertIn(
                    f"cfg.{chave}",
                    corpo,
                    f"{chave} não é reatribuído em _collect_current_config: "
                    "a preferência voltaria ao default a cada salvamento.",
                )

    def test_o_campo_sem_linha_e_preservado_e_nao_zerado(self):
        """Sem linha, ele tem de vir do config atual — não do default da classe.

        O coletor monta um `CopilotConfig()` novo; não reatribuir este campo o
        devolveria a 30 min a cada salvamento, apagando em silêncio um valor que
        os testes (e um dia a interface) ajustaram.
        """
        corpo = self._coletor()
        self.assertIn(f'getattr(self.config, "{self.CAMPO_SEM_LINHA}"', corpo)

    def test_o_carregador_preenche_as_linhas(self):
        fonte = self._fonte()
        for linha in self.LINHAS:
            with self.subTest(linha=linha):
                self.assertIn(f"self.{linha}", fonte)

    def test_as_linhas_entram_no_grupo_de_atencao(self):
        fonte = self._fonte()
        for linha in self.LINHAS:
            with self.subTest(linha=linha):
                self.assertIn(f"attention_group.add(self.{linha})", fonte)
        self.assertIn("page.add(attention_group)", fonte)

    def test_o_campo_de_ausencia_so_volta_quando_houver_fonte_de_ociosidade(self):
        """A razão de não haver linha, fixada como teste e não como comentário.

        Enquanto `_attention_context` mandar `idle_seconds=None` (não há fonte
        nesta máquina), a condição de ausência é pulada — e uma linha para ela
        seria um controle sem efeito. Quem acrescentar a fonte tem de apagar
        esta guarda de propósito, e é isso que se quer: a decisão, revisitada.
        """
        fonte = inspect.getsource(app_module.CopilotWindow._attention_context)
        self.assertIn("idle_seconds=None", fonte.replace(" ", ""))
        self.assertNotIn("attention_away_row", self._fonte())


class AnnounceDecisionContratoTest(unittest.TestCase):
    """A decisão de `ha_announce` continua sendo a **primeira** pergunta.

    A política não pode "salvar" um evento que não tem o que dizer: ela decide
    *com que força*, nunca *se há frase*.
    """

    def test_evento_sem_frase_nao_e_promovido_pela_politica(self):
        win = _WindowStub()
        win.config.ha_announce_entities = "sensor.temperatura"
        win._attention_context = lambda: AttentionContext(hour=14, idle_seconds=None)
        with mock.patch.object(app_module.GLib, "idle_add") as agendado:
            win._on_ha_event(
                _evento(
                    domain="sensor",
                    entity_id="sensor.temperatura",
                    attributes={},
                )
            )
        agendado.assert_not_called()
        self.assertEqual(win._attention_badges, 0)


if __name__ == "__main__":
    unittest.main()
