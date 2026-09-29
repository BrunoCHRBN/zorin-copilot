"""Testes da fiação do ambiente: as duas fontes, uma porta — sem display.

O que se testa aqui é o **roteamento**: de qual produtor veio, para qual tratamento
foi, e o que foi feito com a decisão. O vocabulário do envelope tem suíte própria
(`tests/test_ambient_events.py`) e a regra de interromper também
(`tests/test_attention.py`).

A técnica é a de sempre neste repo: os métodos reais religados num objeto simples
(`tests/_window_stub.py`). Subir uma `CopilotWindow` de verdade custa +109 MB e
subprocessos de MCP/RAG por instância.
"""

from __future__ import annotations

import collections
import enum
import threading
import time
import types
import unittest
from unittest import mock

from zorin_copilot.core.ambient_events import (
    AmbientEvent,
    AmbientSource,
    DocumentKind,
    document_event,
    home_event,
)
from zorin_copilot.core.attention import AttentionContext, Severity
from zorin_copilot.ui import app as app_module

try:  # importado como pacote (`tests._window_stub`) quando há `tests/__init__.py`
    from tests._window_stub import bind_window_methods, missing_collaborators
except ImportError:  # importado solto, conforme o rootdir do pytest
    from _window_stub import bind_window_methods, missing_collaborators  # type: ignore[no-redef]


class _FonteInventada(str, enum.Enum):
    """Uma fonte que já existe no produtor mas ainda não tem ramo na porta.

    Enum local de propósito: o cenário real é alguém acrescentar `AmbientSource.VISION`
    e o produtor que o emite, esquecendo o ramo. Usar um enum *novo* — e não um
    atributo inventado no dataclass — é o que mantém o envelope verdadeiro: o
    `AmbientEvent` continua sendo o congelado, só a fonte é desconhecida.
    """

    VISION = "vision"


class _WindowStub:
    """Janela de mentira com a porta do ambiente e os colaboradores dela."""

    METHODS = (
        # A cadeia inteira, e não só a porta: a guarda de completude lê o fonte de
        # cada método religado, então parar no meio deixaria o roteamento novo sem
        # cobertura — e o defeito de roteamento não levanta exceção, só não acontece.
        "_on_ambient_event",
        "_on_home_ambient",
        "_on_document_ambient",
        "_on_document_event",
        "_on_ha_event",
        "_note_badge",
        "_attention_policy",
        "_interruption_budget",
        "_note_interruption",
        "_screen_is_locked",
        "_attention_context",
        "_own_speech_in_progress",
        "_live_session_active",
        "_announcement_busy",
        "_claim_speaker",
        "_speak_announcement",
        "_report_silent_announcement",
        "_init_file_watcher",
        "_stop_file_watcher",
        "_reconfigure_file_watcher",
        # O teardown entra na lista porque é ele que garante que o monitor **morre**
        # com a janela: os monitores do Gio vivem no barramento e manteriam a janela
        # viva por referência. Sem cobrir isto, "esqueci de parar" não falharia aqui.
        "teardown_background_services",
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
            file_watcher_enabled=True,
            piper_voice_model="pt_BR-faber-medium",
        )
        self.live_client = None
        self.rag = mock.MagicMock()
        self.wake_word_engine = None
        self.mcp_manager = None
        self._services_torn_down = False
        # Colaboradores que o teardown toca mas que **não** são o objeto sob teste:
        # o que se cobra é que ele chame o passo 7 (o monitor), não o que os passos
        # 1–6 fazem. Religar os métodos reais deles traria a árvore inteira para cá.
        self.stop_live_voice = mock.MagicMock()
        self._cancel_pending_end_session = mock.MagicMock()
        self._stop_consolidation = mock.MagicMock()
        self._stop_ha_announcements = mock.MagicMock()
        self._ha_speaker = None
        self._ha_speak_lock = threading.Lock()
        self._ha_speaker_busy = False
        self._ha_voice_missing_warned = False
        self._interruption_log = collections.deque()
        self._attention_badges = 0
        self._attention_lock = threading.Lock()
        self._screen_lock_probe_warned = False
        self._file_watcher = None
        self.logger = mock.MagicMock()
        self.show_toast = mock.MagicMock()
        bind_window_methods(self, self.METHODS)
        for key, value in overrides.items():
            setattr(self, key, value)
        # Depois dos overrides: nenhum teste daqui fala com o barramento da sessão.
        self._screen_is_locked = lambda: False


def _casa(**overrides) -> AmbientEvent:
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
    return home_event(base)


def _arquivo(kind: str = "index", path: str = "/home/u/Documentos/contrato.pdf"):
    return document_event(kind, path)


class StubCompletenessTest(unittest.TestCase):
    def test_stub_tem_tudo_o_que_os_metodos_tocam(self):
        win = _WindowStub()
        faltando = missing_collaborators(win, _WindowStub.METHODS)
        self.assertEqual(faltando, set(), "o stub não tem: " + ", ".join(sorted(faltando)))

    def test_a_guarda_esta_olhando_para_metodos_de_verdade(self):
        self.assertGreaterEqual(len(_WindowStub.METHODS), 15)


# ---------------------------------------------------------------------------
# A porta
# ---------------------------------------------------------------------------
class PortaTest(unittest.TestCase):
    """`_on_ambient_event` roteia por fonte — e só por isso."""

    def test_evento_da_casa_vai_para_o_tratamento_da_casa(self):
        win = _WindowStub()
        evento = _casa()
        with mock.patch.object(win, "_on_ha_event") as casa, mock.patch.object(
            win, "_on_document_event"
        ) as arquivo:
            win._on_ambient_event(evento)
        casa.assert_called_once_with(evento.payload)
        arquivo.assert_not_called()

    def test_evento_de_arquivo_vai_para_o_tratamento_de_arquivo(self):
        win = _WindowStub()
        evento = _arquivo()
        with mock.patch.object(win, "_on_ha_event") as casa, mock.patch.object(
            win, "_on_document_event"
        ) as arquivo:
            win._on_ambient_event(evento)
        arquivo.assert_called_once_with(evento)
        casa.assert_not_called()

    def test_fonte_sem_tratamento_nao_levanta(self):
        """Uma fonte nova sem ramo não pode derrubar a thread do produtor.

        Não há asserção sobre o **conteúdo** do log de propósito: o que se cobra é o
        contrato — chegar e não explodir. Um evento de fonte desconhecida é defeito de
        fiação, e o custo de descobri-lo é uma linha de `debug`, não um canal morto.
        (O texto do log tem teste próprio, abaixo — e por um motivo diferente.)
        """
        win = _WindowStub()
        orfa = AmbientEvent(source=AmbientSource.HOME, kind="state")
        object.__setattr__(orfa, "source", _FonteInventada.VISION)
        win._on_ambient_event(orfa)  # não pode levantar

    def test_o_log_da_fonte_orfa_diz_o_nome_do_fio(self):
        """`event.source.value`, e não `event.source`.

        O ramo de fonte desconhecida não tem **outro** efeito observável além da
        linha de log — então o texto dela é o contrato, não um detalhe. `%s` de um
        `str`-Enum imprime `Fonte.VISION`; o `.value` imprime `vision`, que é o nome
        que aparece no produtor e no `to_dict()`. Uma busca por `vision` no log tem de
        encontrar o evento; `AmbientSource.VISION` não casa com nada.
        """
        win = _WindowStub()
        orfa = AmbientEvent(source=AmbientSource.HOME, kind="state")
        object.__setattr__(orfa, "source", _FonteInventada.VISION)
        with self.assertLogs(app_module.logger, level="DEBUG") as capturado:
            win._on_ambient_event(orfa)
        texto = "\n".join(capturado.output)
        self.assertIn("vision", texto)
        self.assertNotIn("AmbientSource", texto)

    def test_a_porta_nao_adivinha_a_fonte_pelo_kind(self):
        """Roteia por **fonte**, não por `kind` — os dois vocabulários se sobrepõem.

        Um `kind="state"` de documento existe (`DocumentKind` não tem "state", mas o
        teste é sobre o *princípio*): se a porta olhasse o `kind`, acrescentar uma
        fonte com o vocabulário da casa mandaria o evento para o tratamento errado.
        """
        win = _WindowStub()
        evento = AmbientEvent(source=AmbientSource.DOCUMENTS, kind="state", subject="/tmp/x")
        with mock.patch.object(win, "_on_ha_event") as casa, mock.patch.object(
            win, "_on_document_event"
        ) as arquivo:
            win._on_ambient_event(evento)
        casa.assert_not_called()
        arquivo.assert_called_once_with(evento)


class AdaptadorDaCasaTest(unittest.TestCase):
    def test_evento_de_estado_chega_ao_tratamento(self):
        win = _WindowStub()
        with mock.patch.object(win, "_on_ambient_event") as porta:
            win._on_home_ambient(dict(kind="state", entity_id="light.sala"))
        porta.assert_called_once()
        self.assertIs(porta.call_args.args[0].source, AmbientSource.HOME)

    def test_evento_de_conexao_e_descartado_antes_da_porta(self):
        """Saúde do canal não é percepção da casa — e quem decide isso é o adaptador."""
        win = _WindowStub()
        with mock.patch.object(win, "_on_ambient_event") as porta:
            win._on_home_ambient(dict(kind="connection", entity_id=""))
        porta.assert_not_called()

    def test_entrada_que_nao_e_dicionario_e_descartada(self):
        win = _WindowStub()
        with mock.patch.object(win, "_on_ambient_event") as porta:
            win._on_home_ambient(None)  # type: ignore[arg-type]
        porta.assert_not_called()


class AdaptadorDeArquivoTest(unittest.TestCase):
    def test_indexacao_chega_a_porta(self):
        win = _WindowStub()
        with mock.patch.object(win, "_on_ambient_event") as porta:
            win._on_document_ambient("index", "/tmp/x.pdf")
        porta.assert_called_once()
        self.assertEqual(porta.call_args.args[0].kind, "index")

    def test_tipo_desconhecido_e_descartado(self):
        win = _WindowStub()
        with mock.patch.object(win, "_on_ambient_event") as porta:
            win._on_document_ambient("explodiu", "/tmp/x.pdf")
        porta.assert_not_called()

    def test_caminho_vazio_e_descartado(self):
        win = _WindowStub()
        with mock.patch.object(win, "_on_ambient_event") as porta:
            win._on_document_ambient("index", "")
        porta.assert_not_called()


# ---------------------------------------------------------------------------
# O tratamento do documento
# ---------------------------------------------------------------------------
class DocumentoTest(unittest.TestCase):
    """Documento **nunca** interrompe — e a decisão passa pela política."""

    def _win(self, **contexto):
        win = _WindowStub()
        base = {"hour": 14, "idle_seconds": None}
        base.update(contexto)
        win._attention_context = lambda: AttentionContext(**base)
        return win

    def test_a_severidade_enviada_e_corriqueira(self):
        """Falhar aqui é falar a cada arquivo salvo — o alarme que fala sozinho."""
        win = self._win()
        visto: list[Severity] = []
        real = app_module.decide_attention

        def espiao(*, severity, context, budget, policy):
            visto.append(severity)
            return real(severity=severity, context=context, budget=budget, policy=policy)

        with mock.patch.object(app_module, "decide_attention", espiao):
            win._on_document_event(_arquivo())
        self.assertEqual(visto, [Severity.ROUTINE])

    def test_documento_acumula_em_selo(self):
        win = self._win()
        win._on_document_event(_arquivo())
        self.assertEqual(win._attention_badges, 1)

    def test_documento_nunca_fala_nem_avisa(self):
        """A premissa do tratamento, cobrada no fio.

        `_on_document_event` trata "não ignorado" como selo, sem ramo para fala.
        Se a política pudesse devolver `SPEAK`/`NOTIFY` para `ROUTINE`, aquele
        código estaria errado em silêncio.
        """
        win = self._win(live_session_active=True)
        with mock.patch.object(app_module.GLib, "idle_add") as agendado, mock.patch.object(
            win, "_speak_announcement"
        ) as falado:
            win._on_document_event(_arquivo())
        agendado.assert_not_called()
        falado.assert_not_called()
        self.assertEqual(win._attention_badges, 1)

    def test_o_caminho_do_documento_nao_toca_em_gtk(self):
        """**O caminho do `delete` roda na thread da UI**, e é por isso que não pode
        haver GTK nenhum aqui dentro.

        O sinal `changed` do `Gio.FileMonitor` é entregue pelo `GMainContext` — a
        mesma thread do GTK. Um `show_toast` direto no tratamento de documento
        corromperia a árvore de widgets quando alguém apagasse um arquivo. O teste
        envenena o `GLib` inteiro e passa as duas rotas: qualquer acesso derruba.
        """
        veneno = mock.MagicMock()
        veneno.side_effect = AssertionError("o caminho do documento tocou em GLib")

        for kind in ("index", DocumentKind.DELETED.value):
            with self.subTest(kind=kind):
                win = self._win()
                with mock.patch.object(app_module, "GLib", veneno), mock.patch.object(
                    win, "show_toast"
                ) as aviso:
                    win._on_document_event(_arquivo(kind=kind))
                aviso.assert_not_called()

    def test_o_caminho_do_documento_sobrevive_a_uma_trava_segurada(self):
        """`_note_badge` toma a trava; o tratamento não pode tomá-la por fora.

        Se alguém envolver `_on_document_event` num `with self._attention_lock`,
        a chamada interna trava para sempre — deadlock, não teste vermelho. Aqui a
        trava é segurada por outra thread e o evento tem de completar assim mesmo.
        """
        win = self._win()
        segurando = threading.Event()
        soltar = threading.Event()

        def segurar():
            with win._attention_lock:
                segurando.set()
                soltar.wait(2.0)

        t = threading.Thread(target=segurar, daemon=True)
        t.start()
        self.assertTrue(segurando.wait(2.0), "a thread não pegou a trava")

        feito = threading.Event()

        def disparar():
            win._on_document_event(_arquivo())
            feito.set()

        d = threading.Thread(target=disparar, daemon=True)
        d.start()
        soltar.set()
        self.assertTrue(feito.wait(3.0), "o tratamento travou: trava tomada duas vezes")
        self.assertEqual(win._attention_badges, 1)

    def test_janela_de_silencio_nao_registra_nem_selo(self):
        """À noite, livro-caixa não é pendência — é ruído. `IGNORE`, não `BADGE`."""
        win = self._win(hour=2)
        win.config.attention_silence_hours = "23-7"
        win._on_document_event(_arquivo())
        self.assertEqual(win._attention_badges, 0)

    def test_politica_desligada_nao_registra(self):
        win = self._win()
        win.config.attention_enabled = False
        win._on_document_event(_arquivo())
        self.assertEqual(win._attention_badges, 0)

    def test_exclusao_tambem_e_tratada(self):
        """Apagar um arquivo é o mesmo livro-caixa: o índice tem de acompanhar."""
        win = self._win()
        win._on_document_event(_arquivo(kind=DocumentKind.DELETED.value))
        self.assertEqual(win._attention_badges, 1)


class ContabilidadeTest(unittest.TestCase):
    """O contador de selos passou a ter **duas** threads produtoras."""

    def test_o_total_vem_de_dentro_da_trava(self):
        win = _WindowStub()
        self.assertEqual(win._note_badge(), 1)
        self.assertEqual(win._note_badge(), 2)

    def test_a_soma_espera_a_trava(self):
        """Prova de exclusão mútua, em vez de contar com a sorte do escalonador.

        Um teste de estresse com N threads *costuma* pegar a corrida, mas "costuma"
        não é uma asserção. Aqui a trava é segurada por outra thread e se cobra que
        a soma **espere** — determinístico, com margem folgada.
        """
        win = _WindowStub()
        segurando = threading.Event()

        def segurar():
            with win._attention_lock:
                segurando.set()
                time.sleep(0.25)

        t = threading.Thread(target=segurar, daemon=True)
        t.start()
        self.assertTrue(segurando.wait(2.0), "a thread não pegou a trava")

        inicio = time.monotonic()
        win._note_badge()
        decorrido = time.monotonic() - inicio
        t.join(2.0)

        self.assertGreaterEqual(
            decorrido, 0.1,
            "a soma não esperou a trava: duas threads produtoras perderiam selo",
        )

    def test_as_duas_fontes_somam_no_mesmo_contador(self):
        """É um contador só — o digesto da Fase 2 quer tudo junto."""
        win = _WindowStub()
        win._attention_context = lambda: AttentionContext(hour=14, idle_seconds=None)
        win._on_document_event(_arquivo())
        win._on_ha_event(
            dict(
                kind="state", entity_id="light.sala", domain="light",
                state="on", old_state="off", attributes={}, friendly_name="Luz",
            )
        )
        self.assertEqual(win._attention_badges, 2)


# ---------------------------------------------------------------------------
# A fiação do monitor de arquivos
# ---------------------------------------------------------------------------
class FiacaoDoMonitorTest(unittest.TestCase):
    def _com_monitor(self, *, inicia: bool = True, levanta: bool = False):
        """Devolve (win, patchers, instância do monitor falso)."""
        instancia = mock.MagicMock()
        instancia.directories = ["/home/u/Documentos"]
        instancia.start.return_value = inicia
        if levanta:
            instancia.start.side_effect = RuntimeError("gio morreu")
        classe = mock.MagicMock(return_value=instancia)
        patcher = mock.patch.object(app_module, "DocumentFileWatcher", classe)
        patcher.start()
        self.addCleanup(patcher.stop)
        win = _WindowStub()
        return win, classe, instancia

    def test_nao_sobe_no_ambiente_de_teste(self):
        """Sem este gate, cada janela de teste abriria monitores inotify de verdade."""
        win, classe, _ = self._com_monitor()
        with mock.patch.object(app_module, "_is_test_environment", return_value=True):
            win._init_file_watcher()
        classe.assert_not_called()
        self.assertIsNone(win._file_watcher)

    def test_interruptor_desligado_nao_sobe(self):
        win, classe, _ = self._com_monitor()
        win.config.file_watcher_enabled = False
        with mock.patch.object(app_module, "_is_test_environment", return_value=False):
            win._init_file_watcher()
        classe.assert_not_called()

    def test_sobe_com_o_rag_e_o_callback_da_porta(self):
        win, classe, instancia = self._com_monitor()
        with mock.patch.object(app_module, "_is_test_environment", return_value=False):
            win._init_file_watcher()
        _, kwargs = classe.call_args
        self.assertIs(kwargs["rag"], win.rag)
        self.assertIs(kwargs["on_event_callback"], win._on_document_ambient)
        instancia.start.assert_called_once()
        self.assertIs(win._file_watcher, instancia)

    def test_falta_de_gio_nao_deixa_o_recurso_meio_ligado(self):
        """`start()` devolve False sem PyGObject/Gio. Nesse caso não há monitor."""
        win, _, _ = self._com_monitor(inicia=False)
        with mock.patch.object(app_module, "_is_test_environment", return_value=False):
            win._init_file_watcher()
        self.assertIsNone(win._file_watcher)

    def test_excecao_no_arranque_nao_impede_o_app_de_abrir(self):
        win, _, _ = self._com_monitor(levanta=True)
        with mock.patch.object(app_module, "_is_test_environment", return_value=False):
            win._init_file_watcher()  # não pode levantar
        self.assertIsNone(win._file_watcher)

    def test_parar_e_idempotente(self):
        win, _, instancia = self._com_monitor()
        with mock.patch.object(app_module, "_is_test_environment", return_value=False):
            win._init_file_watcher()
        win._stop_file_watcher()
        win._stop_file_watcher()
        instancia.stop.assert_called_once()
        self.assertIsNone(win._file_watcher)

    def test_parar_sem_ter_subido_nao_levanta(self):
        _WindowStub()._stop_file_watcher()

    def test_falha_ao_parar_nao_levanta(self):
        """`_stop_file_watcher` roda no teardown: levantar aqui derruba a saída.

        Um `gio` já desmontado no meio do encerramento não pode transformar um
        fechamento normal de janela em exceção — e, pior, deixar de marcar
        `_file_watcher = None` faria o teardown repetir a tentativa.
        """
        win, _, instancia = self._com_monitor()
        with mock.patch.object(app_module, "_is_test_environment", return_value=False):
            win._init_file_watcher()
        instancia.stop.side_effect = RuntimeError("gio já desmontado")
        win._stop_file_watcher()  # não pode levantar
        self.assertIsNone(win._file_watcher, "o recurso ficou meio-ligado")

    def test_o_caminho_de_parada_e_o_unico_que_zera_o_atributo(self):
        """`_file_watcher = None` **antes** da checagem de `None`, não depois.

        Zerar depois deixaria o atributo apontando para um monitor já parado, e a
        próxima chamada tentaria parar de novo. É por isso que o teste cobra o
        atributo, e não só que `stop()` foi chamado.
        """
        win, _, instancia = self._com_monitor()
        with mock.patch.object(app_module, "_is_test_environment", return_value=False):
            win._init_file_watcher()
        win._stop_file_watcher()
        self.assertIsNone(win._file_watcher)
        instancia.stop.assert_called_once()

    def test_reconfigurar_para_e_sobe_de_novo(self):
        """Salvar as preferências tem de valer já, não no próximo arranque."""
        win, classe, instancia = self._com_monitor()
        with mock.patch.object(app_module, "_is_test_environment", return_value=False):
            win._init_file_watcher()
            win._reconfigure_file_watcher()
        instancia.stop.assert_called_once()
        self.assertEqual(classe.call_count, 2, "não subiu de novo depois de parar")

    def test_o_teardown_para_o_monitor(self):
        """Os monitores do Gio vivem no barramento e manteriam a janela viva."""
        win, _, instancia = self._com_monitor()
        with mock.patch.object(app_module, "_is_test_environment", return_value=False):
            win._init_file_watcher()
        win._stop_file_watcher = mock.MagicMock(wraps=win._stop_file_watcher)
        win.teardown_background_services()
        win._stop_file_watcher.assert_called_once()
        instancia.stop.assert_called_once()


class PreferenciasDoMonitorTest(unittest.TestCase):
    """A armadilha do coletor: campo esquecido volta ao default a cada salvamento."""

    def _prefs(self) -> str:
        from pathlib import Path

        raiz = Path(__file__).resolve().parent.parent
        return (raiz / "src" / "zorin_copilot" / "ui" / "preferences.py").read_text(
            encoding="utf-8"
        )

    def _coletor(self) -> str:
        import re

        fonte = self._prefs()
        inicio = fonte.index("def _collect_current_config")
        resto = fonte[inicio + 1 :]
        m = re.search(r"\n    def ", resto)
        return resto[: m.start()] if m else resto

    def test_o_coletor_reatribui_a_chave(self):
        self.assertIn(
            "cfg.file_watcher_enabled",
            self._coletor(),
            "a preferência voltaria ao default a cada salvamento",
        )

    def test_o_carregador_preenche_a_linha(self):
        self.assertIn("self.file_watcher_row.set_active", self._prefs())

    def test_a_linha_entra_no_grupo_de_privacidade(self):
        fonte = self._prefs()
        self.assertIn("privacy_group.add(self.file_watcher_row)", fonte)

    def test_o_default_da_config_e_o_mesmo_do_carregador(self):
        """Duas fontes para o mesmo default divergem em silêncio."""
        from zorin_copilot.core.config import CopilotConfig

        self.assertIs(CopilotConfig().file_watcher_enabled, True)


if __name__ == "__main__":
    unittest.main()
