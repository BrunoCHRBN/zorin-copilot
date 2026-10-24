# Decisão de design: a janela principal atua apenas como orquestradora — monta os componentes
# de interface (cabeçalho, barra lateral, fluxo de conversa, barra de prompt e anexo visual),
# mantém o estado da sessão e coordena o ciclo pergunta -> IA -> renderização. Toda a
# construção de widgets foi movida para `zorin_copilot.ui.widgets`.

"""Interface gráfica do Zorin Copilot em GTK4 / Libadwaita."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime
# `Any` era usado em 17 anotações deste arquivo sem nunca ser importado. Não dava
# erro só porque `from __future__ import annotations` adia a avaliação — mas
# qualquer `typing.get_type_hints()` no módulo quebrava.
from typing import Any

import gi

from .gi_versions import require_gtk4  # noqa: E402
require_gtk4()
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import __app_id__
from ..ai.actions import ActionPlan, ActionType
from ..ai.engine import IntentEngine
from ..ai.live import GeminiLiveClient, LiveVoiceState
from ..core.a11y import DesktopInspector
from ..core.board_outcome import (
    BOARD_OK,
    BOARD_PENDING,
    BoardOutcome,
    decide_path,
    message_for,
)
from ..core.desktop_context import DesktopContext, DesktopContextDetector
from ..core.attachments import (
    Attachment,
    AttachmentKind,
    compose_prompt,
    load_attachment,
    load_attachments,
)
from ..core.vision import ScreenCaptureService
from ..core.config import CopilotConfig
from ..core.export import (
    exportable_turns,
    render_conversation_markdown,
    suggest_filename,
)
from ..core.fence import ScreenFenceManager
from ..core.ha_announce import (
    decide_announcement,
    parse_announce_command,
    parse_entity_filter,
)
from ..core.attention import (
    AttentionContext,
    AttentionPolicy,
    InterruptionBudget,
    Severity,
    decide_attention,
    is_explicitly_selected,
    parse_silence_windows,
    severity_for,
)
from ..core.ambient_events import (
    AmbientEvent,
    AmbientSource,
    document_event,
    home_event,
)
from ..core.identity import identity_from_config
from ..core.rag import LocalDocumentRAG
from ..core.file_watcher import DocumentFileWatcher
from ..core.session import ChatTurn, TopicSession
from ..core.shortcuts import APP_SHORTCUTS, ShortcutManager, is_gnome_desktop
from ..shell.action_status import ActionOutcome
from ..shell.executor import ActionExecutor, ExecutionReport
from ..shell.undo import UndoEntry
from ..shell.risk import RiskPolicy
from ..shell.wake_word import WakeWordEngine, default_backend
from ..mcp.manager import MCPManager
from ..mcp.protocol import MCPPrompt
from ..mcp import prompts as mcp_prompts
from ..ai.local_voice import LocalLiveVoiceClient, LocalVoiceSynthesizer, piper_available
from .hyprland_effects import hyprland_effects
from .live_view import LiveVoiceWidget
from .preferences import PreferencesDialog
from .style import setup_glass_window
from .voice_pill import VoicePillWindow
from .widgets.attachments import AttachmentBar
from .widgets.chat_stream import (
    ChatStreamView,
    format_markdown_to_markup,  # noqa: F401 - reexportado por compatibilidade
    get_action_icon,  # noqa: F401 - reexportado por compatibilidade
)
from .widgets.status_bar import StatusBarWidget
from .widgets.compare_dialog import CompareResponsesDialog
from .widgets.command_palette import CommandPalette, PaletteCommand
from .widgets.drop_zone import DropZone
from .widgets.header import HeaderBarWidget
from .widgets.prompt_bar import (
    PromptBar,
    get_app_subtitle,  # noqa: F401 - reexportado por compatibilidade
)
from .widgets.sidebar import (
    SidebarPanel,
    format_relative_timestamp,  # noqa: F401 - reexportado por compatibilidade
)
from .widgets.vision import VisionAttachment

logger = logging.getLogger(__name__)


def _is_dialog_dismissed(error: GLib.Error) -> bool:
    """Distingue "usuário cancelou" de uma falha real do Gtk.FileDialog.

    Cancelar é uma ação legítima e não deve gerar toast de erro; qualquer outra
    falha do diálogo (portal ausente, permissão negada) deve.
    """
    quark_fn = getattr(Gtk, "dialog_error_quark", None)
    dismissed = getattr(getattr(Gtk, "DialogError", None), "DISMISSED", None)
    if quark_fn is None or dismissed is None:
        return False
    try:
        return bool(error.matches(quark_fn(), int(dismissed)))
    except (TypeError, ValueError):  # bindings antigos sem Gtk.DialogError
        return False


def _overlay_is_pill(config: object) -> bool:
    """A invocação por voz deve abrir a pílula ou a janela completa?

    Só "full" escolhe o HUD: qualquer valor ausente, vazio ou desconhecido
    cai na pílula, que é o padrão e o caminho menos intrusivo.
    """
    mode = str(getattr(config, "voice_overlay_mode", "pill") or "pill").strip().lower()
    return mode != "full"


def _is_test_environment() -> bool:
    """Detecta se o código está sendo executado sob um runner de testes.

    Por que isto é crítico e não cosmético: cada `CopilotWindow` construída em
    teste sobe, por padrão, os servidores MCP habilitados (subprocessos Node/
    Python via `uvx`/`npx`), o indexador RAG e o motor de wake word. Medido
    nesta máquina (contando a árvore de processos, não só o pai): **+109,5 MB e
    +1,80 processo por janela**, e nem threads nem descritores são devolvidos
    quando a janela é destruída. A suíte de UI cria dezenas de janelas por
    arquivo (há um teste que cria 30 de uma vez), o que somava ~3,3 GB e ~54
    processos — sem swap, o OOM killer entra em ação e o sistema reinicia.

    Regressão que isto conserta: a detecção só olhava para o pytest. A suíte do
    projeto roda com `python -m unittest`, então o gate simplesmente não
    disparava e o cenário acima acontecia em toda execução local.

    Sinais, do mais explícito ao mais implícito:
      1. `ZORIN_TEST_MODE` — opt-in explícito (usado pelo runner do projeto);
      2. pytest — variável por teste e presença do módulo;
      3. unittest — não expõe variável de ambiente; o processo é o runner
         (`python -m unittest ...`), o que aparece em `__main__.__spec__.name`.
    """
    if os.environ.get("ZORIN_TEST_MODE"):
        return True
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return True
    main_module = sys.modules.get("__main__")
    main_spec = getattr(main_module, "__spec__", None)
    if getattr(main_spec, "name", "") in ("unittest.__main__", "unittest"):
        return True
    return False


def _required_badge() -> Gtk.Widget:
    """Marca visual de argumento obrigatório num `Adw.EntryRow`.

    Texto, não ícone: o projeto usa apenas ícones simbólicos, e nenhum símbolo do
    tema comunica "obrigatório" sem ambiguidade. Um rótulo curto em `dim-label`
    mantém o peso visual baixo e continua legível por leitor de tela.
    """
    label = Gtk.Label(label="obrigatório")
    label.add_css_class("dim-label")
    label.add_css_class("caption")
    label.set_valign(Gtk.Align.CENTER)
    return label


def _focus_widget_once(widget: Gtk.Widget) -> bool:
    """Foca um widget no próximo ciclo do loop principal e não repete.

    O foco só pega depois que o diálogo foi mapeado; pedir antes disso é
    silenciosamente ignorado pelo GTK.
    """
    widget.grab_focus()
    return GLib.SOURCE_REMOVE


class CopilotWindow(Adw.ApplicationWindow):
    """Janela principal do Copilot: compõe os widgets e orquestra a conversa."""

    def __init__(self, app: Adw.Application):
        super().__init__(application=app)
        self.set_default_size(880, 620)
        self.set_resizable(True)

        self.config = CopilotConfig.load()
        # A identidade **depois** de `self.config`, porque depende dele. Antes esta
        # linha era `self.set_title("Zorin Copilot")` cravado no topo do `__init__`, e o
        # nome aparecia em mais três lugares (duas vezes no prompt, uma no
        # `system_prompt` gerado). Quatro fontes para o mesmo nome, e mudar uma não
        # mudava as outras. Ver `core/identity.py`.
        self._apply_identity()
        self.inspector = DesktopInspector()
        self.context_detector = DesktopContextDetector(self.inspector)
        self.current_context: DesktopContext | None = None
        mcp_cfg_path = getattr(self.config, "mcp_config_path", "") or None
        self.mcp_manager = MCPManager(config_path=mcp_cfg_path)
        self.executor = ActionExecutor(
            self.inspector,
            mcp_manager=self.mcp_manager if getattr(self.config, "mcp_enabled", True) else None,
        )
        self.engine = IntentEngine(self.inspector, self.config)
        self.session = TopicSession(auto_persist=True)
        self.current_plan: ActionPlan | None = None
        self.attachments: list[Attachment] = []
        self._raw_answer_text: str = ""
        self._is_busy = False
        self._pending_turn_box: Gtk.Widget | None = None

        # Contexto de Visão Contínua (retém recorte para conversas multi-turn)
        self._active_image_bytes: bytes | None = None
        self._active_image_is_area: bool = False
        self._active_image_is_clipboard: bool = False
        self._current_ocr_text: str | None = None

        # Voz ao Vivo (Gemini Multimodal Live / Local Soberana)
        self.live_client: GeminiLiveClient | LocalLiveVoiceClient | None = None
        self.live_voice_widget: LiveVoiceWidget | None = None
        self.voice_pill_window: VoicePillWindow | None = None
        self._live_voice_as_pill: bool = False
        # Estado da barra lateral antes de a chamada recolhê-la. `None` = não há
        # chamada a restaurar; ver `_collapse_sidebar_for_live`.
        self._sidebar_revealed_before_live: bool | None = None

        # Ditado Global / Voice Typing no app em foco
        self.dictation_service: Any | None = None
        self.dictation_osd_window: Any | None = None

        # Wake word ("palavra de ativação") — invocação hands-free.
        # Opt-in via preferências; pausa durante a sessão Live (o microfone já
        # está em uso pelo pw-record do Gemini Live).
        self.wake_word_engine: WakeWordEngine | None = None

        # Anúncios falados de eventos da casa (opt-in via `ha_announce_enabled`).
        # O cancelamento da assinatura, o sintetizador e a trava de "já falando"
        # vivem aqui porque o teardown precisa devolver os três.
        self._ha_announce_cancel: Any | None = None
        self._ha_speaker: LocalVoiceSynthesizer | None = None
        self._ha_speak_lock = threading.Lock()
        self._ha_speaker_busy = False
        # Uma vez por execução: avisar na tela que a fala não sai (motor de voz
        # ausente) sem repetir o aviso a cada anúncio.
        self._ha_voice_missing_warned = False

        # Política de atenção (`core/attention.py`): a decisão de **interromper**.
        # `_interruption_log` guarda os instantes das falas recentes, e é dela que
        # sai o orçamento — uma lista de marcas em vez de um contador, porque o
        # orçamento é "por hora" e um contador precisaria de um temporizador para
        # zerar (que morreria com o processo, como o da consolidação).
        self._interruption_log: deque[float] = deque()
        #: Selos acumulados desde a última vez que o usuário olhou. É o que faz um
        #: período de silêncio virar digesto em vez de perda.
        self._attention_badges = 0
        #: Uma vez por execução: dizer que a tela bloqueada não pôde ser consultada.
        self._screen_lock_probe_warned = False
        #: A contabilidade da atenção tem **duas** threads produtoras desde o P1 (a
        #: do canal da casa e a do monitor de arquivos). `deque.append` é atômico,
        #: mas `self._attention_badges += 1` é ler-somar-gravar: sem a trava, dois
        #: eventos simultâneos perdem um selo — em silêncio, que é o pior tipo de
        #: perda num contador que ninguém vê.
        self._attention_lock = threading.Lock()

        # Monitor de arquivos (inotify/Gio) — a segunda fonte do barramento.
        # Sem ele, `index_directories()` roda **uma vez** no arranque e um documento
        # salvo com o app aberto nunca entra no índice: some da busca até reiniciar.
        self._file_watcher: DocumentFileWatcher | None = None

        # Consolidação da memória ("sono REM"), opt-in via `memory_consolidation_enabled`.
        # Os caminhos ficam explícitos para que o efeito nunca construa um
        # `MemoryConsolidator()` apontando para o banco real sem querer.
        self._consolidation_source_id = 0
        self._consolidation_running = False
        self._consolidation_db_path: Any | None = None
        self._consolidation_state_path: Any | None = None

        # Encerramento autônomo pedido pelo agente (ferramenta `end_session`).
        self._end_session_source_id: int = 0
        self._pending_end_mode: str | None = None

        # Guarda de idempotência do teardown de serviços de segundo plano
        # (MCP, RAG, wake word, voz ao vivo). Ver `teardown_background_services`.
        self._services_torn_down: bool = False

        # Cerca de Proteção Espacial (isolamento de monitores no Wayland)
        self.fence = ScreenFenceManager()

        # Fonte única de risco. A mesma instância é injetada no cliente de voz ao
        # vivo e nos dois registros do loop de agente (start/continue), em vez de
        # cada um criar a sua. Sem isto, uma camada de confiança/autonomia
        # compartilhada não teria onde morar: cada `ToolRegistry()` sem `policy=`
        # fabrica uma política própria e as decisões divergiriam em silêncio.
        # Ver `docs/ANEXO_AUTONOMIA_PREREQUISITOS.md` (item A1).
        # `autonomy_level` e as zonas de confiança vêm da config: é o que permite o
        # nível "high" promover escritas reversíveis em `trusted_directories` a AUTO.
        self.risk_policy = RiskPolicy(
            autonomy_level=getattr(self.config, "autonomy_level", "standard"),
            trusted_dirs=getattr(self.config, "trusted_directories", None),
        )

        # Cursor Fantasma da IA (Overlay do Operador / Computer-Use)
        try:
            from .ghost_cursor import GhostCursorOverlay
            if not _is_test_environment():
                GhostCursorOverlay.get_default().initialize(app)
        except Exception as exc:
            logger.debug("Ghost Cursor não inicializado na janela: %s", exc)

        # Campos usados por componentes; criados antes da construção dos widgets
        self.entry: Gtk.Entry = Gtk.Entry()
        self.answer_label: Gtk.Label = Gtk.Label()
        self.exec_status: Gtk.Label = Gtk.Label()
        self.agent_mode_active: bool = False
        self._active_agent_loop: Any | None = None
        self._active_agent_widget: Any | None = None
        # Fonte única do watchdog do Modo Agente (Frente A.3 / Defeito 1):
        # handle do GLib.timeout_add que força o encerramento da UI se o agente
        # exceder `agent_max_seconds + 30`.
        self._agent_watchdog_source: int | None = None

        # RAG Local & Inteligência Documental (indexação incremental em segundo plano)
        self.rag = LocalDocumentRAG(memory=self.engine.memory)
        self.executor.rag = self.rag
        self.engine.rag = self.rag
        if not _is_test_environment():
            self.rag.start_background_indexing()

        self._build_ui()
        # Alvo de arrastar-e-soltar na janela inteira, não só no fluxo de chat.
        self.drop_zone.attach_to(self)
        setup_glass_window(self)
        self.header.update_provider_badge()
        self.chat_stream.rebuild()
        self.sidebar.populate()
        self.connect("close-request", self._on_close_request)
        # O teardown de MCP/RAG/wake word NÃO passa por sinal: no GTK4,
        # `Gtk.Window.destroy()` não emite `destroy`. Ver a sobrescrita do
        # método `CopilotWindow.destroy`.
        # Blur real da janela principal (Hyprland): aplica no realize, quando a
        # superfície XDG já existe e o compositor pode casar a windowrule.
        self.connect("realize", self._on_realize_window)
        self._init_wake_word()
        self._init_ha_announcements()
        self._init_file_watcher()
        self._init_consolidation()
        self._init_mcp_servers()

    def _init_mcp_servers(self) -> None:
        """Inicia servidores MCP habilitados em segundo plano, se ativado."""
        if _is_test_environment():
            return
        if getattr(self.config, "mcp_enabled", True) and getattr(self.config, "mcp_auto_connect", True):
            import threading
            threading.Thread(target=self.mcp_manager.start_all_enabled, daemon=True).start()

    # ------------------------------------------------------------------
    # Wake word ("palavra de ativação")
    # ------------------------------------------------------------------
    def _init_wake_word(self) -> None:
        """Cria e (se habilitado) inicia o motor de wake word a partir do config."""
        phrases = getattr(self.config, "wake_phrases", None) or []
        configured = getattr(self.config, "wake_word_model_path", "")
        lang = getattr(self.config, "wake_word_lang", None) or "pt-BR"
        # Auto-localiza: se o caminho configurado estiver vazio/inválido, tenta
        # achar um modelo já baixado no cache. É o que faz a wake word funcionar
        # de imediato após o download, sem o usuário ter que apontar o caminho.
        from ..shell.vosk_model import find_model

        model_path = find_model(configured, lang) or configured
        self.wake_word_engine = WakeWordEngine(
            phrases=phrases,
            backend=default_backend(
                model_path,
                vad_enabled=bool(getattr(self.config, "wake_word_vad_enabled", True)),
                sensitivity=float(getattr(self.config, "wake_word_sensitivity", 0.5)),
                device=str(getattr(self.config, "wake_word_device", "") or ""),
            ),
            on_wake=self._on_wake_word,
            cooldown_sec=float(getattr(self.config, "wake_word_cooldown_sec", 4.0)),
            on_error=self._on_wake_word_error,
        )
        self.wake_word_engine.match_partials = bool(
            getattr(self.config, "wake_word_match_partials", False)
        )
        if _is_test_environment():
            return
        if getattr(self.config, "wake_word_enabled", False):
            started = self.wake_word_engine.start()
            if not started:
                self._report_wake_unavailable(configured, model_path, lang)

    def _report_wake_unavailable(
        self, configured: str, model_path: str, lang: str
    ) -> None:
        """Avisa por que a wake word não subiu, com a causa real.

        As duas causas raiz são diferentes e exigem ações diferentes:
          * pacote ``vosk`` não instalado -> ``pip install vosk``;
          * modelo ausente -> baixar em Preferências (botão "Baixar modelo").
        Dizer "indisponível" sem distinguir deixava o usuário sem saber o quê
        consertar — foi o que gerou esta conversa.
        """
        import importlib.util

        if importlib.util.find_spec("vosk") is None:
            logger.info("Wake word: pacote 'vosk' não instalado.")
            self.show_toast(
                "Palavra de ativação: instale o pacote 'vosk' (pip install vosk)."
            )
        elif not model_path:
            logger.info("Wake word: modelo %s ausente.", lang)
            self.show_toast(
                f"Palavra de ativação: baixe o modelo Vosk ({lang}) em Preferências."
            )
        else:
            logger.info(
                "Wake word habilitado mas indisponível (modelo/captura: %s).", model_path
            )

    def _on_wake_word_error(self, message: str) -> None:
        """Chamado pela thread do motor quando o microfone/captura falha."""
        GLib.idle_add(self._show_wake_word_error, message)

    def _show_wake_word_error(self, message: str) -> bool:
        """(main thread) Mostra o erro no máximo uma vez por minuto.

        Sem o rate-limit, um microfone indisponível geraria uma enxurrada de
        toasts conforme o loop retenta com backoff.
        """
        now = time.monotonic()
        last = getattr(self, "_last_wake_error_at", 0.0)
        if now - last < 60.0:
            return GLib.SOURCE_REMOVE
        self._last_wake_error_at = now
        self.show_toast(f"\U0001f399️ Palavra de ativação: {message}")
        return GLib.SOURCE_REMOVE

    def _reconfigure_wake_word(self) -> None:
        """Reconfigura o motor após salvar as preferências (frases/modelo/habilitação)."""
        if self.wake_word_engine is not None:
            self.wake_word_engine.stop()
        self._init_wake_word()
        if getattr(self.config, "wake_word_enabled", False) and (
            self.wake_word_engine and self.wake_word_engine.running
        ):
            self.show_toast("Palavra de ativação atualizada e ouvindo.")

    # ------------------------------------------------------------------
    # Anúncios falados de eventos da casa (Home Assistant)
    # ------------------------------------------------------------------
    def _init_ha_announcements(self) -> None:
        """Assina o canal de eventos do HA para falar o que valer a pena.

        É o primeiro consumidor do `on_event` — até aqui o canal de tempo real
        subia no arranque, recebia `state_changed` e só servia para responder
        `smart_home_events` quando o usuário perguntava. O gancho existia e
        estava vazio.

        A **decisão** de falar não mora aqui: vem de `core/ha_announce.py`, que é
        puro. Aqui só se executa o efeito.
        """
        if _is_test_environment():
            return
        if not getattr(self.config, "ha_announce_enabled", False):
            return
        # Sem o canal de tempo real não há evento nenhum para ouvir. Avisar em
        # vez de falhar calado: a combinação de flags é fácil de errar.
        if not (
            getattr(self.config, "ha_enabled", False)
            and getattr(self.config, "ha_realtime_enabled", False)
        ):
            logger.info(
                "Anúncios da casa ligados, mas o canal de tempo real está desligado "
                "(ligue 'Eventos em Tempo Real' na Casa Inteligente)."
            )
            return
        try:
            from ..core.home_assistant import HomeAssistantManager

            ha = HomeAssistantManager.get_default()
            if not ha.is_configured():
                logger.info("Anúncios da casa ligados, mas o Home Assistant não está configurado.")
                return
            entity_filter = parse_entity_filter(
                getattr(self.config, "ha_announce_entities", "")
            )
            self._ha_announce_cancel = ha.on_event(
                self._on_home_ambient,
                entity_filter=entity_filter or None,
            )
            # O sintetizador é criado agora (barato — só caminhos de arquivo); o
            # modelo Piper carrega na primeira fala, dentro do `speak()`.
            self._ha_speaker = LocalVoiceSynthesizer(
                voice_name=getattr(self.config, "piper_voice_model", "") or "pt_BR-faber-medium"
            )
            # Sem o motor de voz, todo anúncio sairia mudo — e o usuário não teria
            # como saber por quê. Dizer agora, no arranque, custa uma linha e
            # evita a caçada. (Medido: `piper-tts` é extra opcional e pode não
            # estar instalado; sem ele `speak()` devolve False em silêncio.)
            if not piper_available():
                logger.warning(
                    "Anúncios da casa ligados, mas o motor de voz local (piper) não está "
                    "instalado — os anúncios sairão mudos. Instale o extra 'voice'."
                )
                self.show_toast(
                    "Anúncios da casa ligados, mas falta o motor de voz local "
                    "(instale o extra 'voice' do projeto)."
                )
            logger.info("Anúncios falados da casa ativos.")
        except Exception as exc:
            # Recurso, não requisito: falhar aqui nunca pode impedir o app de abrir.
            logger.debug("Falha ao assinar eventos da casa para anúncio: %s", exc)

    def _own_speech_in_progress(self) -> bool:
        """Uma fala nossa está no ar. Limite de **capacidade**: não há como sobrepor áudio."""
        return self._ha_speaker_busy

    def _live_session_active(self) -> bool:
        """Há sessão de voz ao vivo — o usuário está conversando, ou o sintetizador fala.

        Separado de `_own_speech_in_progress` porque a política de atenção trata os
        dois de forma diferente: falar por cima da **própria** fala é impossível,
        mas falar por cima da conversa do usuário é apenas indesejável (e uma
        detecção de fumaça o faz mesmo assim).
        """
        client = self.live_client
        if client is not None and client.is_active():
            return True
        synthesizer = getattr(client, "synthesizer", None)
        return bool(synthesizer is not None and getattr(synthesizer, "is_speaking", False))

    def _announcement_busy(self) -> bool:
        """Diz se falar agora atropelaria alguma coisa.

        Três fontes, e todas importam: uma fala nossa em andamento, uma sessão de
        voz ao vivo ativa (o usuário está conversando — não é hora de avisar que
        a porta abriu) e o sintetizador no meio de uma frase.

        Continua sendo a pergunta que a **ponte** faz antes de pedir uma fala
        (`announce_text`), porque uma mensagem pedida de fora não tem política de
        atenção: ela é para ser dita, e o único motivo para recusar é não haver
        como. O caminho dos eventos da casa não usa mais isto como porteiro — quem
        decide lá é `decide_attention`.
        """
        return self._own_speech_in_progress() or self._live_session_active()

    # ------------------------------------------------------------------
    # Política de atenção (ver `core/attention.py`)
    # ------------------------------------------------------------------

    #: Janela do orçamento de interrupções. "Por hora" é literal.
    ATTENTION_WINDOW_SECONDS = 3600.0

    def _attention_policy(self) -> AttentionPolicy:
        """Lê os ajustes do usuário **no momento do evento**.

        Não há nada a rearmar quando as preferências são salvas: a política é
        consultada a cada evento, então o que foi salvo já vale para o próximo.
        Isso evita o modo de falha do `ha_announce_enabled`, que precisa de um
        caminho de reconfiguração explícito.
        """
        cfg = self.config
        try:
            minutos = float(getattr(cfg, "attention_away_minutes", 30))
        except (TypeError, ValueError):
            minutos = 30.0
        try:
            maximo = int(getattr(cfg, "attention_max_per_hour", 6))
        except (TypeError, ValueError):
            maximo = 6
        return AttentionPolicy(
            enabled=bool(getattr(cfg, "attention_enabled", True)),
            silence_windows=parse_silence_windows(
                getattr(cfg, "attention_silence_hours", "")
            ),
            # `max(0.0, ...)` para que um valor negativo não signifique "nunca
            # ausente" por acidente — aqui zero é uma escolha legítima.
            away_seconds=max(0.0, minutos * 60.0),
            max_per_hour=maximo,
        )

    def _interruption_budget(self, now: float | None = None) -> InterruptionBudget:
        """Quantas falas houve na última hora, podando a lista ao consultar.

        Lista de instantes em vez de contador: um contador precisaria de um
        temporizador para zerar, e o temporizador morreria com o processo — o
        mesmo motivo pelo qual a consolidação usa estado em disco. Aqui a janela
        deslizante é mais simples e não guarda nada entre execuções.
        """
        instante = time.monotonic() if now is None else now
        limite = instante - self.ATTENTION_WINDOW_SECONDS
        while self._interruption_log and self._interruption_log[0] < limite:
            self._interruption_log.popleft()
        return InterruptionBudget(
            spoken_last_hour=len(self._interruption_log),
            max_per_hour=self._attention_policy().max_per_hour,
        )

    def _note_interruption(self) -> None:
        """Registra que se falou agora. Só depois de a fala ser de fato aceita."""
        self._interruption_log.append(time.monotonic())

    def _screen_is_locked(self) -> bool:
        """Pergunta ao barramento da sessão se a tela está bloqueada.

        Medido nesta máquina: `org.freedesktop.ScreenSaver.GetActive` responde;
        `GetSessionIdleTime` **não existe**. Então o que se sabe de verdade é o
        bloqueio, e a ociosidade vai como desconhecida — ver `AttentionContext`.

        Falha de barramento devolve `False` (não bloqueado) e registra **uma vez**.
        Presumir bloqueio calaria o assistente sem que nada aparecesse no log, e
        um silêncio sem causa é exatamente o defeito que este trabalho existe
        para evitar. O caminho oposto (presumir desbloqueado) erra para o lado
        que se **vê** — e o que se vê, se conserta.
        """
        try:
            from gi.repository import Gio

            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            resultado = bus.call_sync(
                "org.freedesktop.ScreenSaver",
                "/org/freedesktop/ScreenSaver",
                "org.freedesktop.ScreenSaver",
                "GetActive",
                None,
                None,
                Gio.DBusCallFlags.NONE,
                500,
                None,
            )
            return bool(resultado.unpack()[0])
        except Exception as exc:
            if not self._screen_lock_probe_warned:
                self._screen_lock_probe_warned = True
                logger.debug("Não consegui consultar o bloqueio de tela: %s", exc)
            return False

    def _attention_context(self) -> AttentionContext:
        """Monta o retrato do momento — tudo o que a política precisa saber."""
        return AttentionContext(
            hour=datetime.now().hour,
            screen_locked=self._screen_is_locked(),
            live_session_active=self._live_session_active(),
            speaking=self._own_speech_in_progress(),
            # Sem fonte confiável de ociosidade nesta máquina. `None` faz a
            # política **pular** a condição de ausência em vez de chutá-la.
            idle_seconds=None,
        )

    def _claim_speaker(self) -> bool:
        """Reivindica o alto-falante, ou diz que não. **Ponto único de arbitragem.**

        Quem fala por conta própria — um evento da casa ou um pedido da ponte —
        passa por aqui. É isto que impede duas falas simultâneas, e é a razão de
        a ponte não ter voz própria: arbitragem só funciona com um dono só.
        """
        if self._announcement_busy():
            return False
        # A reivindicação é o `if` dentro da trava, não a leitura acima: entre
        # decidir e falar, outro pedido pode ter ocupado a saída.
        with self._ha_speak_lock:
            if self._ha_speaker_busy:
                return False
            self._ha_speaker_busy = True
        return True

    def announce_text(self, text: str, *, source: str = "ponte") -> bool:
        """Fala um texto pedido de fora (ponte HA→desktop). Devolve se deu.

        Independente de `ha_announce_enabled`: aquele interruptor governa *reagir
        a mudanças de estado*, este caminho governa *falar uma mensagem que alguém
        escreveu*. São coisas diferentes e é útil poder ter só uma delas.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return False
        if not self._claim_speaker():
            logger.info("Anúncio de %s descartado: já há fala em andamento.", source)
            return False
        if self._ha_speaker is None:
            # Criado aqui, na primeira fala: o caminho da ponte pode ser o único
            # ligado, e nesse caso `_init_ha_announcements` nunca criou nada.
            self._ha_speaker = LocalVoiceSynthesizer(
                voice_name=getattr(self.config, "piper_voice_model", "") or "pt_BR-faber-medium"
            )
        logger.info("Anunciando texto de %s: %s", source, cleaned)
        threading.Thread(
            target=self._speak_announcement,
            args=(cleaned,),
            daemon=True,
            name="ha-announce",
        ).start()
        return True

    # ------------------------------------------------------------------
    # Eventos do ambiente (ver `core/ambient_events.py`)
    # ------------------------------------------------------------------
    #
    # Duas fontes, uma porta. Cada produtor tem o seu formato cru (a casa manda um
    # `dict`, o monitor manda uma tupla) e o seu adaptador, que é puro; o que chega
    # aqui já é `AmbientEvent`. Sem esta porta, cada consumidor novo teria de
    # conhecer as duas formas — e é isso que faz uma terceira fonte custar caro.

    def _on_ambient_event(self, event: AmbientEvent) -> None:
        """Porta única de entrada. Roteia por **fonte**, e nada mais.

        Cada fonte tem o seu tratamento porque faz uma pergunta diferente: a casa
        pergunta *"isto tem o que dizer, e vale interromper?"*; o arquivo pergunta
        *"isto entra no índice?"*. O que as duas compartilham é o formato do evento
        **e** a decisão de interromper.

        **Não há uma thread só aqui, e supor que há é o erro caro.** As três origens
        chegam por threads diferentes:

        | Origem | Thread |
        |---|---|
        | casa (`state_changed`) | a do canal WebSocket do HA |
        | documento `"index"` | a de debounce do `DocumentFileWatcher` |
        | documento `"delete"` | a do **laço principal** — o sinal `changed` do Gio |

        Consequência: **este método, e tudo que ele chama, tem de ser seguro na thread
        da UI.** Não é "roda sempre fora dela" — é "não pode assumir nada". Quem
        precisar de GTK usa `GLib.idle_add`, que vale nos três casos. O contrato de
        thread do monitor está travado em `tests/test_file_watcher.py`.
        """
        if event.source is AmbientSource.HOME:
            self._on_ha_event(event.payload)
        elif event.source is AmbientSource.DOCUMENTS:
            self._on_document_event(event)
        else:  # pragma: no cover - fonte nova sem tratamento é defeito de fiação
            logger.debug("Evento de fonte sem tratamento: %s", event.source.value)

    def _on_home_ambient(self, raw: dict) -> None:
        """Adapta o formato cru do canal do Home Assistant e entrega na porta.

        O adaptador também **descarta** `kind="connection"`: a conexão caiu ou
        voltou é saúde do canal, não percepção da casa. Fazer isso aqui, num lugar
        só, é o que evita que cada consumidor tenha de lembrar — era o que
        `decide_announcement` fazia, sozinho, até agora.
        """
        evento = home_event(raw)
        if evento is not None:
            self._on_ambient_event(evento)

    def _on_document_ambient(self, kind: str, path: str) -> None:
        """Adapta o que o monitor de arquivos emite (`(kind, caminho)`)."""
        evento = document_event(kind, path)
        if evento is not None:
            self._on_ambient_event(evento)

    def _note_badge(self) -> int:
        """Soma um selo e devolve o total.

        Sob trava porque a contabilidade da atenção passou a ter **duas** threads
        produtoras com o P1 (ver `__init__`). O total sai daqui, e não de uma
        leitura solta depois, para que o número do log seja o número de verdade.
        """
        with self._attention_lock:
            self._attention_badges += 1
            return self._attention_badges

    def _on_document_event(self, event: AmbientEvent) -> None:
        """Reage a um evento de arquivo.

        **Pode rodar na thread da UI** — e essa é a armadilha. `"index"` chega pela
        thread de debounce do monitor, mas `"delete"` chega pela thread do laço
        principal, porque o sinal `changed` do `Gio.FileMonitor` é entregue pelo
        `GMainContext`. Daí não haver **nenhuma** chamada de GTK aqui dentro: um
        `show_toast` direto corromperia a árvore de widgets no caminho do `delete`.
        Quem precisar de GTK use `GLib.idle_add`, que é seguro nos dois caminhos.

        **Documento não interrompe, e isto é decisão, não esquecimento.** Um arquivo
        salvo é livro-caixa: o ganho é ele passar a ser encontrável na busca, não
        alguém ser avisado em voz alta. Falar a cada arquivo salvo seria exatamente
        o alarme que fala sozinho — o defeito que a política de atenção existe para
        impedir. Daí a severidade fixa em `ROUTINE`: o melhor que um evento
        corriqueiro consegue é o selo.

        Mesmo assim a decisão **passa pela política**, e não por um `if` local: é
        isto que torna verdadeira a tese do P1 — a mesma regra governa as duas
        fontes. O que muda entre elas é a severidade do evento, não a regra. E a
        direção da falha é a segura: `tests/test_attention.py` varre 384 contextos
        provando que `ROUTINE` nunca alcança `SPEAK`/`NOTIFY`, então tratar "não
        ignorado" como selo erra para **menos**, nunca para mais.

        Nota honesta: o selo ainda não tem consumidor visível (ver `_on_ha_event`),
        e aqui isso é aceitável — silêncio é a resposta **certa** para livro-caixa.
        O consumidor visível desta fonte é o índice do RAG: o arquivo passa a ser
        encontrável, e isso o usuário vê na busca.
        """
        decision = decide_attention(
            severity=Severity.ROUTINE,
            context=self._attention_context(),
            budget=self._interruption_budget(),
            policy=self._attention_policy(),
        )
        if decision.should_ignore:
            logger.debug(
                "Documento %s não registrado (%s): %s",
                event.kind,
                decision.reason.value,
                event.subject,
            )
            return
        logger.info(
            "Documento %s acumulado em selo (%s, total %d): %s",
            event.kind,
            decision.reason.value,
            self._note_badge(),
            event.subject,
        )

    def _on_ha_event(self, event: dict) -> None:
        """Reage a um evento da casa. Roda na thread do canal, **nunca** na da UI.

        Por isso não há GTK aqui: um `show_toast` desta thread corromperia a
        árvore de widgets. O aviso visual passa por `GLib.idle_add`.

        São **duas** perguntas, e elas são diferentes de propósito:

        1. `decide_announcement` — *isto tem o que dizer?* Responde sobre o
           evento: é de estado, passa o filtro, mudou de verdade, tem frase.
        2. `decide_attention` — *vale interromper agora, e com que força?*
           Responde sobre o usuário e o momento.

        Separadas porque as duas mudam por motivos diferentes e a segunda não
        precisa saber nada sobre entidades do Home Assistant.
        """
        entity_filter = (
            parse_entity_filter(getattr(self.config, "ha_announce_entities", "")) or None
        )
        decision = decide_announcement(
            event,
            enabled=bool(getattr(self.config, "ha_announce_enabled", False)),
            entity_filter=entity_filter,
            # A arbitragem de **quando** falar deixou de ser daqui. Passar `busy`
            # faria a frase ser *descartada*; a política a **rebaixa** para um
            # aviso visual ou um selo, que é a diferença entre perder a
            # informação e guardá-la para depois.
            busy=False,
        )
        if not decision.should_speak:
            logger.debug(
                "Anúncio da casa não falado (%s): %s",
                decision.decision.value,
                decision.entity_id or "-",
            )
            return

        attention = decide_attention(
            # Nomear a entidade no filtro é intenção declarada: quem listou
            # `light.sala` quer ouvir sobre ela, e sem isto a classificação por
            # severidade transformaria o filtro num botão que não faz nada.
            severity=severity_for(
                event, explicitly_selected=is_explicitly_selected(event, entity_filter)
            ),
            context=self._attention_context(),
            budget=self._interruption_budget(),
            policy=self._attention_policy(),
        )

        if attention.should_speak:
            if not self._claim_speaker():
                # A política disse "fale", mas entre decidir e falar outro pedido
                # ocupou a saída. A reivindicação é o `if` dentro da trava.
                logger.debug("Anúncio descartado: já há fala em andamento.")
                return
            self._note_interruption()
            logger.info(
                "Anunciando evento da casa (%s): %s",
                attention.reason.value,
                decision.text,
            )
            threading.Thread(
                target=self._speak_announcement,
                args=(decision.text,),
                daemon=True,
                name="ha-announce",
            ).start()
            return

        if attention.should_notify:
            logger.info(
                "Evento da casa em aviso visual (%s): %s",
                attention.reason.value,
                decision.text,
            )
            GLib.idle_add(self.show_toast, decision.text)
            return

        if attention.should_badge:
            # Acumula em vez de alertar. **O consumidor visível deste contador
            # ainda não existe**: hoje ele aparece no log, e é a entrada já
            # testada do digesto da Fase 2 ("uma fala às 18h com o que mudou, em
            # vez de sete avisos ao longo do dia"). Registrado aqui para que
            # ninguém leia o contador como um recurso pronto.
            total = self._note_badge()
            logger.info(
                "Evento da casa acumulado em selo (%s, total %d): %s",
                attention.reason.value,
                total,
                decision.text,
            )
            return

        logger.debug(
            "Evento da casa ignorado (%s): %s",
            attention.reason.value,
            decision.entity_id or "-",
        )

    def _speak_announcement(self, text: str) -> None:
        """Fala o anúncio. Thread própria porque `speak()` é bloqueante."""
        try:
            speaker = self._ha_speaker
            if speaker is None:
                return
            if not speaker.speak(text):
                self._report_silent_announcement(text)
        except Exception as exc:
            logger.warning("Falha ao falar o anúncio da casa: %s", exc)
        finally:
            with self._ha_speak_lock:
                self._ha_speaker_busy = False

    def _report_silent_announcement(self, text: str) -> None:
        """Relata que a fala foi aceita e não saiu som nenhum.

        Roda na thread de trabalho do anúncio, então **não** pode tocar em GTK
        diretamente — daí o `idle_add`.

        O aviso na tela não é enfeite: medido, o app iniciado pela sessão manda
        stderr para `/dev/null` (`/proc/<pid>/fd/2 -> /dev/null`), então a linha
        de log só alcança quem roda pelo terminal. Sem o toast, o usuário que usa
        a ponte HA não teria como descobrir que a casa está muda.

        Uma vez por execução, e não a cada anúncio: quem não tem o motor de voz
        não precisa ser avisado a cada campainha.
        """
        logger.warning("O anúncio não saiu pelo alto-falante: %s", text)
        if self._ha_voice_missing_warned:
            return
        self._ha_voice_missing_warned = True
        GLib.idle_add(
            self.show_toast,
            "O anúncio não saiu pelo alto-falante: falta o motor de voz local "
            "(instale o extra 'voice' do projeto).",
        )

    def _stop_ha_announcements(self) -> None:
        """Cancela a assinatura e cala a fala. Idempotente."""
        cancel = self._ha_announce_cancel
        self._ha_announce_cancel = None
        if cancel is not None:
            try:
                cancel()
            except Exception as exc:
                logger.debug("Falha ao cancelar assinatura de anúncios da casa: %s", exc)
        speaker = self._ha_speaker
        if speaker is not None:
            try:
                speaker.interrupt()
            except Exception as exc:
                logger.debug("Falha ao interromper fala no teardown: %s", exc)

    def _reconfigure_ha_announcements(self) -> None:
        """Reassina depois de salvar as preferências (ligar/desligar/filtro)."""
        self._stop_ha_announcements()
        self._init_ha_announcements()

    # -- monitor de arquivos: a segunda fonte de eventos --------------------
    #
    # `DocumentFileWatcher` existia com suíte própria e **0 referências** no `src/`
    # — a quinta peça escrita e não instalada deste repo. O custo de não estar
    # ligada é medível e não é sutil: `index_directories()` só é chamado **uma vez**,
    # em `start_background_indexing`, então um documento salvo com o app aberto
    # **nunca** entra no índice. Ele existe no disco e não existe para a busca, até
    # o próximo arranque. Não é uma funcionalidade nova: é a que já existe
    # funcionando pela metade.
    #
    # Opt-in é o default errado aqui, ao contrário da consolidação: aquela **apaga**
    # linhas, esta só mantém o índice em dia com o que o usuário já mandou indexar.
    # O interruptor existe para poder desligar, não para precisar ligar.

    def _init_file_watcher(self) -> None:
        """Sobe o monitor de arquivos, se ligado. Falha nunca impede o app de abrir."""
        if _is_test_environment():
            # Sem este gate, cada teste que constrói uma janela abriria monitores
            # inotify sobre ~/Documentos e ~/Downloads de verdade.
            return
        if not getattr(self.config, "file_watcher_enabled", True):
            return
        try:
            self._file_watcher = DocumentFileWatcher(
                rag=self.rag,
                on_event_callback=self._on_document_ambient,
            )
            if self._file_watcher.start():
                logger.info(
                    "Monitor de arquivos ativo em %d diretório(s): documento salvo com o "
                    "app aberto passa a entrar no índice.",
                    len(self._file_watcher.directories),
                )
            else:
                # `start()` devolve False quando falta PyGObject/Gio. Dizer, em vez
                # de deixar o recurso meio-ligado: a indexação continuaria só no
                # arranque e nada indicaria por quê.
                logger.warning(
                    "Monitor de arquivos indisponível (sem PyGObject/Gio): documentos "
                    "salvos durante a sessão não entrarão no índice."
                )
                self._file_watcher = None
        except Exception as exc:
            # Recurso, não requisito: o app abre e indexa no arranque, como antes.
            logger.debug("Falha ao iniciar o monitor de arquivos: %s", exc)
            self._file_watcher = None

    def _stop_file_watcher(self) -> None:
        """Encerra os monitores e a thread de debounce. Idempotente."""
        watcher = self._file_watcher
        self._file_watcher = None
        if watcher is None:
            return
        try:
            watcher.stop()
        except Exception as exc:
            logger.debug("Falha ao parar o monitor de arquivos: %s", exc)

    def _reconfigure_file_watcher(self) -> None:
        """Aplica o interruptor depois de salvar as preferências.

        Parar e subir de novo, em vez de guardar "já está no estado certo": o
        watcher não tem estado a comparar, e comparar seria mais código do que
        refazer.

        **Limite conhecido:** os diretórios vigiados vêm de `self.rag.watched_dirs`,
        que foi montado com a config do arranque. Mudar as pastas confiáveis ainda
        exige reiniciar o app — vale para a varredura do RAG também, não só para o
        monitor. Registrado aqui para não parecer que o interruptor cobre isso.
        """
        self._stop_file_watcher()
        self._init_file_watcher()

    # -- consolidação da memória ("sono REM") -------------------------------
    #
    # `MemoryConsolidator` existia com suíte própria e **0 referências** no `src/`:
    # a memória nunca era consolidada, e `action_logs` crescia sem teto (medido:
    # ~6,9 MB por ano no ritmo desta máquina). Aqui só se obedece à decisão pura de
    # `core/consolidator.py` — a regra de "já passou o intervalo?" não mora aqui.
    #
    # Um `threading.Timer` de 24 h não serviria: o timer morre com o processo, então
    # quem reinicia o app todo dia nunca consolidaria nada. Daí o estado em disco e
    # a verificação periódica.

    #: De quanto em quanto tempo se pergunta "é hora?". Não é o intervalo da
    #: consolidação (esse é diário) — é só a frequência da pergunta, e ela precisa
    #: ser menor que o intervalo para que uma sessão longa não perca a vez.
    CONSOLIDATION_TICK_SECONDS = 3600

    def _init_consolidation(self) -> None:
        """Arma a verificação periódica da consolidação, se ela estiver ligada."""
        if _is_test_environment():
            return
        if not getattr(self.config, "memory_consolidation_enabled", False):
            return
        try:
            from ..core.consolidator import consolidation_state_path
            from ..core.memory import MemoryManager

            db_path = MemoryManager().db_path
        except Exception as exc:
            logger.debug("Não consegui resolver os caminhos da consolidação: %s", exc)
            return
        # Banco e estado resolvidos **aqui** e guardados: o efeito recebe os dois por
        # atributo, em vez de construir `MemoryConsolidator()` sem argumento. Sem isso,
        # um teste que exercitasse este caminho apontaria para o banco real do usuário
        # — e `run_consolidation` **apaga** linhas. Com os caminhos explícitos, o teste
        # aponta para um banco temporário e exercita o motor de verdade.
        self._consolidation_db_path = db_path
        self._consolidation_state_path = consolidation_state_path(db_path)
        # Uma verificação no arranque e depois de hora em hora. A do arranque é o que
        # torna a decisão durável: quem abre o app uma vez por dia consolida naquela
        # abertura, e não "24 h depois de ter aberto".
        GLib.idle_add(self._check_consolidation)
        self._consolidation_source_id = GLib.timeout_add_seconds(
            self.CONSOLIDATION_TICK_SECONDS, self._check_consolidation
        )
        logger.info(
            "Consolidação automática da memória ativa (máximo uma vez a cada %.0f h).",
            getattr(self.config, "memory_consolidation_interval_hours", 24.0),
        )

    def _check_consolidation(self) -> bool:
        """Pergunta à decisão pura se é hora; se for, dispara em segundo plano.

        Devolve `True` para continuar no temporizador do GLib.
        """
        if self._consolidation_running:
            return True
        try:
            from ..core.consolidator import (
                load_consolidation_state,
                decide_consolidation,
            )

            state_path = getattr(self, "_consolidation_state_path", None)
            if state_path is None or getattr(self, "_consolidation_db_path", None) is None:
                return False
            state = load_consolidation_state(state_path)
            outcome = decide_consolidation(
                state,
                now=datetime.now(),
                enabled=bool(getattr(self.config, "memory_consolidation_enabled", False)),
                interval_hours=float(
                    getattr(self.config, "memory_consolidation_interval_hours", 24.0)
                ),
            )
        except Exception as exc:
            # Recurso, não requisito: nunca pode derrubar o app nem travar o timer.
            logger.debug("Falha ao decidir a consolidação: %s", exc)
            return True

        if not outcome.should_run:
            logger.debug("Consolidação não executada (%s).", outcome.decision.value)
            return True

        logger.info("Consolidação devida (%s); executando em segundo plano.", outcome.decision.value)
        self._run_consolidation_async()
        return True

    def _run_consolidation_async(self) -> None:
        """Roda a consolidação numa thread e grava o estado no fim.

        Thread própria porque é I/O de SQLite: `run_consolidation` varre e apaga
        linhas, e o banco tem `busy_timeout` de 5 s — bloquear o loop principal por
        isso congelaria a interface.
        """
        db_path = getattr(self, "_consolidation_db_path", None)
        state_path = getattr(self, "_consolidation_state_path", None)
        if db_path is None or state_path is None:
            return
        days_to_keep = int(getattr(self.config, "memory_consolidation_days_to_keep", 14))
        self._consolidation_running = True

        def trabalho() -> None:
            try:
                from ..core.consolidator import (
                    ConsolidationState,
                    MemoryConsolidator,
                    save_consolidation_state,
                )

                report = MemoryConsolidator(db_path=db_path).run_consolidation(
                    days_to_keep=days_to_keep
                )
                # O estado é gravado mesmo quando a poda não removeu nada: o que se
                # registra é "eu rodei", não "eu apaguei". Sem isso, um banco sem
                # linhas antigas faria a consolidação rodar a cada verificação.
                save_consolidation_state(
                    state_path,
                    ConsolidationState(
                        last_run_at=datetime.now(),
                        last_logs_pruned=report.logs_pruned,
                    ),
                )
            except Exception as exc:
                logger.warning("Falha na consolidação da memória: %s", exc)
            finally:
                self._consolidation_running = False

        threading.Thread(target=trabalho, daemon=True, name="memory-consolidation").start()

    def _stop_consolidation(self) -> None:
        """Desarma o temporizador. Idempotente."""
        source_id = self._consolidation_source_id
        self._consolidation_source_id = 0
        if source_id:
            try:
                GLib.source_remove(source_id)
            except Exception as exc:
                logger.debug("Falha ao remover o temporizador da consolidação: %s", exc)

    def _reconfigure_consolidation(self) -> None:
        """Rearma depois de salvar as preferências (ligar/desligar/alterar poda)."""
        self._stop_consolidation()
        self._init_consolidation()

    def _on_wake_word(self, phrase: str, command: str = "") -> None:
        """Chamado pela thread do motor; agenda a abertura da voz no loop principal."""
        GLib.idle_add(self._handle_wake_on_main_thread, phrase, command)

    def _handle_wake_on_main_thread(self, phrase: str, command: str = "") -> bool:
        # Se o usuário voltou a chamar durante o grace period de um
        # encerramento autônomo, o encerramento é cancelado.
        self._cancel_pending_end_session()
        # Se a conversa por voz já está ativa, o comando vira um turno de texto
        # na própria sessão (ex.: wake word acidental durante a chamada).
        if self.live_client and self.live_client.is_active():
            if command:
                self.live_client.send_text_input(command)
            return GLib.SOURCE_REMOVE

        # Respeita o modo de overlay escolhido: no modo "pill" a wake word abre
        # só a pílula flutuante, sem puxar a janela inteira do Copilot.
        use_pill = _overlay_is_pill(self.config)
        if command:
            self.show_toast(f'"{phrase}" — executando: "{command}"')
        else:
            self.show_toast(f'Entendi "{phrase}". Iniciando conversa...')

        if use_pill:
            self.start_live_voice(initial_command=command, as_pill=True)
        else:
            self.summon_hud()
            self.start_live_voice(initial_command=command, as_pill=False)
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------
    # Construção
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.toolbar_view = Adw.ToolbarView()

        self.vision = VisionAttachment(self)
        self.attachment_bar = AttachmentBar(self)
        self.drop_zone = DropZone(self)
        self.prompt_bar = PromptBar(self)

        self.header = HeaderBarWidget(self)
        self.toolbar_view.add_top_bar(self.header.header)

        # Barra de status inferior (estilo Warp): modelo, tokens, carga, RAM e RAG.
        # A cerca espacial é construída pelo header (popover e lógica lá) mas
        # hospedada aqui: é estado de ambiente, não comando do topo.
        self.status_bar = StatusBarWidget(self)
        self.status_bar.set_fence_widget(self.header.fence_menu_btn)
        self.toolbar_view.add_bottom_bar(self.status_bar.container)


        # Atributos de compatibilidade mantidos por código legado/testes
        self.answer_group = Adw.PreferencesGroup()
        self.answer_group.set_visible(False)
        self.actions_group = Adw.PreferencesGroup()
        self.actions_group.set_visible(False)
        self.actions_box = Gtk.Box()
        self.history_btn = Gtk.MenuButton()
        self.history_popover = Gtk.Popover()
        self.topic_revealer = Gtk.Revealer()
        self.topic_info_lbl = Gtk.Label()
        self.pin_btn = Gtk.Button()
        self.pin_btn_label = Gtk.Label()
        self.pin_btn_icon = Gtk.Image()
        self.exec_all_btn = Gtk.Button()
        self.copy_btn = Gtk.Button()

        self._install_key_controller()

        self.split_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        self.sidebar = SidebarPanel(self)
        self.split_box.append(self.sidebar.revealer)

        chat_main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        chat_main_box.set_hexpand(True)
        chat_main_box.add_css_class("chat-main-pane")

        self.live_voice_revealer = Gtk.Revealer()
        self.live_voice_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.live_voice_revealer.set_transition_duration(200)
        self.live_voice_revealer.set_reveal_child(False)
        chat_main_box.append(self.live_voice_revealer)

        self.chat_stream = ChatStreamView(self)
        chat_main_box.append(self.chat_stream.scrolled)

        clamp_bottom = Adw.Clamp(maximum_size=820)
        clamp_bottom.set_child(self.prompt_bar.container)
        chat_main_box.append(clamp_bottom)

        self.split_box.append(chat_main_box)

        # O painel de comandos fica num overlay acima do conteúdo, mas abaixo dos
        # toasts, para que avisos continuem visíveis enquanto ele está aberto.
        self.main_overlay = Gtk.Overlay()
        self.main_overlay.set_child(self.split_box)
        # Véu de "solte aqui" primeiro: o painel de comandos deve ficar por cima.
        self.main_overlay.add_overlay(self.drop_zone.overlay)
        self.command_palette = CommandPalette(self)
        self.main_overlay.add_overlay(self.command_palette)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.main_overlay)
        self.toolbar_view.set_content(self.toast_overlay)
        self.set_content(self.toolbar_view)

    def _install_key_controller(self) -> None:
        """Instala os atalhos declarativos da aplicação e o handler de Escape."""
        self._install_app_shortcuts()

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

    def _install_app_shortcuts(self) -> None:
        """Registra os atalhos de `APP_SHORTCUTS` via Gtk.ShortcutController.

        Antes as combinações eram comparadas manualmente contra keyvals; agora vêm
        de um registro único em `core.shortcuts`, reutilizável e documentável.
        """
        handlers = {
            "app.quit": self._quit_application,
            "app.toggle-live-voice": self.toggle_live_voice,
            "app.toggle-sidebar": self.toggle_sidebar,
            "app.new-topic": self._on_new_topic,
            "app.toggle-pin": self._on_toggle_pin,
            "app.command-palette": self._open_command_palette,
            "app.export-conversation": self.export_conversation,
            "app.undo-action": self._on_undo_shortcut,
            "app.model-selector": self._open_model_selector,
        }

        controller = Gtk.ShortcutController()
        # Escopo GLOBAL garante que os atalhos funcionem mesmo com o foco no campo de texto
        controller.set_scope(Gtk.ShortcutScope.GLOBAL)

        for shortcut in APP_SHORTCUTS:
            handler = handlers.get(shortcut.name)
            if handler is None:
                continue
            trigger = Gtk.ShortcutTrigger.parse_string(shortcut.accelerator)
            if trigger is None:
                logger.warning("Atalho inválido ignorado: %s", shortcut.accelerator)
                continue
            action = Gtk.CallbackAction.new(self._make_shortcut_callback(handler))
            controller.add_shortcut(Gtk.Shortcut.new(trigger, action))

        self.app_shortcut_controller = controller
        self.add_controller(controller)

    @staticmethod
    def _make_shortcut_callback(handler):
        """Envolve o handler para o CallbackAction.

        Se o handler devolver ``False``, o atalho é considerado não tratado e o
        evento segue para o widget focado — é o que permite ao Ctrl+K conviver com
        o atalho de edição de texto do GTK.
        """
        def invoke(_widget, _args):
            result = handler()
            return True if result is None else bool(result)
        return invoke

    @staticmethod
    def _quit_application() -> None:
        app = Adw.Application.get_default()
        if app:
            app.quit()

    def _on_key_pressed(self, _ctrl, keyval, _keycode, state) -> bool:
        """Mantém apenas o Escape no controlador de teclas, por sua lógica em cascata."""
        if keyval == Gdk.KEY_Escape:
            return self._handle_escape()
        return False

    def _handle_escape(self) -> bool:
        """Esc fecha primeiro o que estiver aberto; só esconde a janela por último.

        A versão anterior fechava a janela mesmo com um popover aberto, descartando
        menus sem intenção do usuário.
        """
        if self.command_palette.is_open:
            self.command_palette.close()
            return True

        if self.live_client and self.live_client.is_active():
            self.stop_live_voice()
            return True

        if self._active_agent_loop and getattr(self._active_agent_loop, "running", False):
            self.abort_active_agent()
            return True

        if self._close_open_popovers():
            return True

        if self.entry.get_text():
            self.entry.set_text("")
            return True

        if self.sidebar.search.get_text():
            self.sidebar.search.set_text("")
            return True

        self.set_visible(False)
        return True

    def _close_open_popovers(self) -> bool:
        """Fecha qualquer popover visível ancorado nos botões da interface."""
        closed = False
        for button in (
            self.prompt_bar.vision_btn,
            self.prompt_bar.clipboard_btn,
            self.header.fence_menu_btn,
            self.history_btn,
        ):
            popover = button.get_popover() if isinstance(button, Gtk.MenuButton) else None
            if popover and popover.get_visible():
                popover.popdown()
                closed = True
        return closed

    # ------------------------------------------------------------------
    # Painel de comandos (Ctrl+K)
    # ------------------------------------------------------------------
    def _open_command_palette(self) -> bool:
        """Abre (ou fecha) o painel de comandos.

        Devolve ``False`` quando o foco está num campo de texto: o GTK usa Ctrl+K
        para apagar até o fim da linha, e retornar False deixa o evento seguir
        para o widget focado em vez de roubar a combinação.
        """
        if self.command_palette.is_open:
            self.command_palette.close()
            return True

        focus = self.get_focus()
        if isinstance(focus, (Gtk.Editable, Gtk.TextView)):
            return False

        self.command_palette.open()
        return True

    def palette_commands(self) -> list[PaletteCommand]:
        """Comandos oferecidos no painel. Reconstruído a cada abertura."""
        accels = {s.name: s.accelerator for s in APP_SHORTCUTS}

        def acc(name: str) -> str:
            return accels.get(name, "")

        commands = [
            PaletteCommand(
                "app.new-topic", "Nova conversa", "Começar um tópico limpo",
                "list-add-symbolic", acc("app.new-topic"), ("limpar", "novo", "chat"),
            ),
            PaletteCommand(
                "app.toggle-sidebar", "Mostrar/ocultar conversas", "Alterna a barra lateral",
                "sidebar-show-symbolic", acc("app.toggle-sidebar"), ("histórico", "lateral"),
            ),
            PaletteCommand(
                "app.toggle-pin", "Fixar/desafixar conversa", "Mantém o tópico atual no topo",
                "view-pin-symbolic", acc("app.toggle-pin"), ("pin", "fixar"),
            ),
            PaletteCommand(
                "app.toggle-live-voice", "Conversa por voz ao vivo", "Inicia ou encerra o Gemini Live",
                "audio-input-microphone-symbolic", acc("app.toggle-live-voice"), ("voz", "microfone", "live"),
            ),
            PaletteCommand(
                "app.capture-area", "Recortar área da tela", "Analisa um recorte com a IA",
                "edit-cut-symbolic", "", ("screenshot", "print", "captura", "recorte"),
            ),
            PaletteCommand(
                "app.analyze-clipboard", "Analisar conteúdo copiado", "Usa o texto da área de transferência",
                "edit-paste-symbolic", "", ("colar", "clipboard", "cópia"),
            ),
            PaletteCommand(
                "app.toggle-dark-mode", "Alternar modo escuro", "Muda o tema do sistema",
                "weather-clear-night-symbolic", "", ("tema", "escuro", "claro", "dark"),
            ),
            PaletteCommand(
                "app.copy-answer", "Copiar última resposta", "Copia a resposta do assistente",
                "edit-copy-symbolic", "", ("copiar", "clipboard"),
            ),
            PaletteCommand(
                "app.export-conversation", "Exportar conversa (.md)", "Salva a conversa em Markdown",
                "document-save-symbolic", acc("app.export-conversation"),
                ("exportar", "salvar", "markdown", "md", "arquivo"),
            ),
            PaletteCommand(
                "app.open-settings", "Preferências", "Provedor de IA e chaves",
                "preferences-system-symbolic", "", ("configurações", "chave", "api", "modelo"),
            ),
            PaletteCommand(
                "app.shortcut-cheatsheet", "Atalhos de teclado", "Referência rápida de todos os atalhos",
                "input-keyboard-symbolic", "", ("atalho", "atalhos", "teclado", "keyboard", "cheatsheet", "help"),
            ),
            PaletteCommand(
                "app.clear-history", "Limpar todo o histórico", "Apaga as conversas salvas",
                "user-trash-symbolic", "", ("apagar", "excluir", "lixeira"),
            ),
            PaletteCommand(
                "app.toggle-agent", "Alternar Modo Agente", "Execução autônoma de tarefas no desktop",
                "system-run-symbolic", "", ("agente", "agent", "autônomo", "tarefas"),
            ),
            PaletteCommand(
                "app.context-refresh", "Atualizar sugestões da janela em foco",
                "Reexamina o aplicativo ativo e gera novas sugestões contextuais",
                "view-refresh-symbolic", "", ("contexto", "janela", "sugestoes", "foco"),
            ),
            PaletteCommand(
                "app.toggle-dictation", "Ditado por Voz Global", "Digitar fala no aplicativo em foco",
                "audio-input-microphone-symbolic", "<Super><Shift>d", ("ditado", "voz", "fala", "digitar", "stt"),
            ),
            PaletteCommand(
                "app.mcp-reload", "Recarregar Servidores MCP", "Reconectar e listar ferramentas dos servidores MCP",
                "network-server-symbolic", "", ("mcp", "extensões", "servidores", "plugins", "recarregar"),
            ),
            PaletteCommand(
                "app.model-selector", "Alternar Modelo ou Agente de IA",
                "Troca rápida entre Dolphin 3.1 Uncensored, modelos locais e nuvem",
                "preferences-system-symbolic", acc("app.model-selector"),
                ("modelo", "agente", "ia", "ollama", "dolphin", "qwen", "gemini", "trocar", "uncensored"),
            ),
            PaletteCommand(
                "app.network-inspect", "Diagnóstico de Infraestrutura de Rede",
                "Interfaces, gateway, latência de ping, dispositivos LAN e portas em escuta",
                "network-transmit-receive-symbolic", "",
                ("rede", "diagnostico", "ping", "gateway", "dns", "wifi", "lan", "portas", "ip"),
            ),
            PaletteCommand(
                "app.network-copy-ip", "Copiar Endereço IP Local",
                "Copia o IPv4 da interface de rede ativa para a área de transferência",
                "edit-copy-symbolic", "",
                ("ip", "copiar ip", "meu ip", "endereco", "rede", "lan"),
            ),
            PaletteCommand(
                "app.network-dns-bench", "Benchmark de Latência DNS",
                "Compara tempo de resposta de Cloudflare, Google, Quad9 e resolvedor local",
                "network-transmit-receive-symbolic", "",
                ("dns", "benchmark", "latencia", "rede", "cloudflare", "google", "quad9"),
            ),
            PaletteCommand(
                "app.network-audit", "Auditoria de Exposição de Portas",
                "Verifica serviços ouvindo em 0.0.0.0, dual-stack :: e loopback seguro",
                "security-high-symbolic", "",
                ("seguranca", "portas", "exposicao", "rede", "firewall", "auditoria", "superficie"),
            ),
            PaletteCommand(
                "app.network-throughput", "Consumo de Banda de Rede em Tempo Real",
                "Mede velocidade instantânea de download e upload (Rx/Tx)",
                "network-transmit-receive-symbolic", "",
                ("banda", "throughput", "velocidade", "download", "upload", "consumo", "taxa", "rede"),
            ),
            PaletteCommand(
                "app.network-diagnose", "Diagnóstico de Ponto de Falha de Conexão",
                "Teste isolado hop-by-hop (Interface física -> Gateway -> Internet/DNS)",
                "network-error-symbolic", "",
                ("diagnostico", "queda", "conexao", "gateway", "internet", "offline", "falha"),
            ),
            PaletteCommand(
                "app.docker-inspect", "Status de Containers Docker",
                "Inspeção de containers locais, portas e saúde operacional",
                "system-search-symbolic", "",
                ("docker", "container", "containers", "homeassistant", "status"),
            ),
            PaletteCommand(
                "app.hardware-health", "Saúde de Hardware & Sensores Térmicos",
                "Temperaturas CPU/GPU/NVMe, ventilação e pressão de memória PSI",
                "utilities-system-monitor-symbolic", "",
                ("hardware", "temperatura", "cpu", "gpu", "nvme", "sensores", "memoria", "psi"),
            ),
            PaletteCommand(
                "app.journal-errors", "Diagnóstico de Falhas do Sistema",
                "Extração e síntese heurística de erros recentes no journalctl",
                "dialog-warning-symbolic", "",
                ("journal", "logs", "erros", "falhas", "sistema", "systemd"),
            ),
            PaletteCommand(
                "app.quit", "Sair do Zorin Copilot", "",
                "application-exit-symbolic", acc("app.quit"), ("fechar", "encerrar"),
            ),
        ]

        if self._active_agent_loop and getattr(self._active_agent_loop, "running", False):
            commands.append(
                PaletteCommand(
                    "app.abort-agent", "Interromper Agente", "Para a execução do agente imediatamente",
                    "process-stop-symbolic", "Esc", ("parar", "abortar", "cancelar"),
                )
            )

        # Só aparece quando faz sentido: comando que não tem efeito polui a busca.
        undo_stack = getattr(self.executor, "undo_stack", None)
        undo_entry = undo_stack.peek() if undo_stack is not None else None
        if undo_entry is not None:
            commands.append(
                PaletteCommand(
                    "app.undo-action", f"Desfazer: {undo_entry.label}",
                    "Reverte a última ação reversível",
                    "edit-undo-symbolic", acc("app.undo-action"),
                    ("desfazer", "voltar", "undo", "ctrl+z"),
                )
            )
        if self.attachments:
            commands.append(
                PaletteCommand(
                    "app.clear-attachments", "Remover anexos do chat",
                    f"{len(self.attachments)} arquivo(s) em contexto",
                    "edit-clear-symbolic", "", ("anexo", "arquivo", "remover", "limpar"),
                )
            )

        commands.extend(self._mcp_prompt_commands())
        return commands

    def _mcp_prompt_commands(self) -> list[PaletteCommand]:
        """Comandos da paleta para os prompts MCP descobertos.

        Diferente das ferramentas, um prompt não executa nada: escolhê-lo escreve
        o texto na caixa de entrada. Por isso ele aparece aqui e não no executor.

        A lista é dinâmica — vem do que os servidores conectados anunciaram — e
        por isso não pode viver no dicionário estático de handlers; o roteamento
        é feito por prefixo em `run_palette_command`.
        """
        if not getattr(self.config, "mcp_enabled", True):
            return []

        manager = getattr(self, "mcp_manager", None)
        if manager is None:
            return []

        try:
            prompts = manager.get_all_prompts()
        except Exception as exc:
            # Um servidor mal-comportado não pode impedir a abertura do painel:
            # os comandos nativos continuam funcionando sem os prompts.
            logger.debug("Falha ao listar prompts MCP para a paleta: %s", exc)
            return []

        return [
            PaletteCommand(
                mcp_prompts.prompt_command_name(prompt),
                mcp_prompts.prettify_prompt_name(prompt.name),
                mcp_prompts.prompt_subtitle(prompt),
                "document-open-symbolic",
                "",
                mcp_prompts.prompt_keywords(prompt),
            )
            for prompt in prompts
        ]

    def _palette_handlers(self) -> dict[str, object]:
        """Mapa nome -> callable. Separado para poder ser verificado por testes."""
        return {
            "app.new-topic": self._on_new_topic,
            "app.toggle-sidebar": self.toggle_sidebar,
            "app.toggle-pin": self._on_toggle_pin,
            "app.toggle-live-voice": self.toggle_live_voice,
            "app.toggle-dictation": self.toggle_dictation,
            "app.toggle-agent": self.toggle_agent_mode,
            "app.abort-agent": self.abort_active_agent,
            "app.capture-area": lambda: self._start_screen_capture(interactive=True),
            "app.analyze-clipboard": lambda: self._trigger_prompt("analisar_copiado"),
            "app.toggle-dark-mode": lambda: self._trigger_prompt("ativar modo escuro"),
            "app.copy-answer": self._on_copy_answer,
            "app.export-conversation": self.export_conversation,
            "app.undo-action": self.undo_last_action,
            "app.clear-attachments": self.clear_attachments,
            "app.open-settings": self._open_settings,
            "app.shortcut-cheatsheet": self._show_shortcut_cheatsheet,
            "app.model-selector": self._open_model_selector,
            "app.clear-history": self.sidebar.clear_history,
            "app.context-refresh": self._refresh_context_command,
            "app.mcp-reload": self._reload_mcp_servers,
            "app.network-inspect": self._open_network_inspector,
            "app.network-copy-ip": self._copy_local_ip_command,
            "app.network-dns-bench": self._run_dns_benchmark_command,
            "app.network-audit": self._run_network_audit_command,
            "app.network-throughput": self._run_network_throughput_command,
            "app.network-diagnose": self._run_network_diagnose_command,
            "app.docker-inspect": lambda: self._open_infra_inspector("docker"),
            "app.hardware-health": lambda: self._open_infra_inspector("hardware"),
            "app.journal-errors": lambda: self._open_infra_inspector("journal"),
            "app.quit": self._quit_application,
        }

    def _reload_mcp_servers(self) -> None:
        """Recarrega os servidores MCP em segundo plano e exibe feedback via toast."""
        if not getattr(self.config, "mcp_enabled", True):
            self.show_toast("Suporte a MCP está desativado nas preferências.")
            return

        def reload_worker():
            try:
                self.mcp_manager.reload()
                self.mcp_manager.start_all_enabled()
                tool_count = len(self.mcp_manager.get_all_tools())
                prompt_count = len(self.mcp_manager.get_all_prompts())
                server_count = len(self.mcp_manager.get_running_servers())
                msg = (
                    f"MCP: {server_count} servidor(es) ativo(s), {tool_count} ferramentas "
                    f"e {prompt_count} prompts carregados."
                )
                GLib.idle_add(self.show_toast, msg)
            except Exception as exc:
                logger.error("Erro ao recarregar servidores MCP: %s", exc)
                GLib.idle_add(self.show_toast, f"Erro ao recarregar MCP: {exc}")

        import threading
        threading.Thread(target=reload_worker, daemon=True).start()
        self.show_toast("Recarregando servidores MCP...")

    def _refresh_context_command(self) -> None:
        """Atualiza manualmente o contexto e as sugestões a partir do desktop."""
        try:
            ctx = self.context_detector.detect_context()
            self.current_context = ctx
            self.chat_stream.update_context(ctx)
            if hasattr(self, "prompt_bar") and hasattr(self.prompt_bar, "set_active_context"):
                self.prompt_bar.set_active_context(ctx)
            if ctx.has_active_window:
                self.show_toast(f"Contexto: {ctx.app_name}")
            else:
                self.show_toast("Sugestões atualizadas")
        except Exception as exc:
            logger.debug("Falha ao atualizar contexto: %s", exc)
            self.show_toast("Não foi possível atualizar contexto")

    def _open_network_inspector(self) -> None:
        """Abre o diálogo modal com diagnóstico de infraestrutura e topologia de rede local."""
        try:
            from .widgets.network_view import NetworkInspectorDialog

            dialog = NetworkInspectorDialog(ctx=self)
            dialog.present(self)
        except Exception as exc:
            logger.exception("Falha ao abrir diálogo de rede: %s", exc)
            self.show_toast(f"Erro ao abrir diagnóstico de rede: {exc}")

    def _copy_local_ip_command(self) -> None:
        """Copia o endereço IP local ativo para a área de transferência."""
        try:
            from ..core.network_inspector import NetworkInspector

            report = NetworkInspector.get_health_report()
            ip = report.local_ip
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(ip)
            self.show_toast(f"IP local copiado: {ip}")
        except Exception as exc:
            logger.warning("Falha ao copiar IP local: %s", exc)
            self.show_toast("Não foi possível obter o IP local")

    def _run_dns_benchmark_command(self) -> None:
        """Executa o benchmark de servidores DNS e exibe o resultado via toast."""
        self.show_toast("Iniciando benchmark de latência DNS...")

        def bench_worker() -> None:
            try:
                from ..core.network_actions import benchmark_dns
                results = benchmark_dns()
                fastest = next((r for r in results if r.get("is_fastest")), None)
                if fastest:
                    msg = f"DNS mais rápido: {fastest['provider']} ({fastest['avg_latency_ms']} ms)"
                else:
                    msg = "Benchmark DNS concluído sem dados válidos."
                GLib.idle_add(self.show_toast, msg)
            except Exception as exc:
                logger.error("Falha no benchmark DNS via paleta: %s", exc)
                GLib.idle_add(self.show_toast, f"Erro no benchmark DNS: {exc}")

        import threading
        threading.Thread(target=bench_worker, daemon=True).start()

    def _run_network_audit_command(self) -> None:
        """Executa auditoria de portas expostas e exibe resumo via toast."""
        self.show_toast("Auditando portas e superfície de ataque...")

        def audit_worker() -> None:
            try:
                from ..core.network_actions import audit_attack_surface
                res = audit_attack_surface()
                total = res.get("total_listening", 0)
                exposed = res.get("exposed_count", 0)
                warnings = res.get("high_risk_warnings", [])
                if warnings:
                    msg = f"Auditoria: {len(warnings)} alerta(s) de portas dev/banco expostas na LAN!"
                elif exposed > 0:
                    msg = f"Auditoria: {exposed}/{total} portas escutando na rede ({total - exposed} em loopback)."
                else:
                    msg = f"Auditoria segura: Todas as {total} portas escutando apenas em loopback (127.0.0.1)."
                GLib.idle_add(self.show_toast, msg)
            except Exception as exc:
                logger.error("Falha na auditoria de rede via paleta: %s", exc)
                GLib.idle_add(self.show_toast, f"Erro na auditoria de portas: {exc}")

        import threading
        threading.Thread(target=audit_worker, daemon=True).start()

    def _run_network_throughput_command(self) -> None:
        """Mede throughput de rede instantâneo e exibe resultado via toast."""
        self.show_toast("Medindo tráfego de rede (amostra de 0.5s)...")

        def throughput_worker() -> None:
            try:
                from ..core.network_actions import measure_throughput
                res = measure_throughput(sample_seconds=0.5)
                if "error" in res:
                    msg = f"Throughput indisponível: {res['error']}"
                else:
                    iface = res.get("interface", "rede")
                    rx = res.get("rx_kb_s", 0.0)
                    tx = res.get("tx_kb_s", 0.0)
                    msg = f"Tráfego ({iface}): ↓ {rx:.1f} KB/s  ↑ {tx:.1f} KB/s"
                GLib.idle_add(self.show_toast, msg)
            except Exception as exc:
                logger.error("Falha ao medir throughput via paleta: %s", exc)
                GLib.idle_add(self.show_toast, f"Erro ao medir throughput: {exc}")

        import threading
        threading.Thread(target=throughput_worker, daemon=True).start()

    def _run_network_diagnose_command(self) -> None:
        """Executa diagnóstico hop-by-hop de conexão e exibe resultado via toast."""
        self.show_toast("Diagnosticando caminho de conexão...")

        def diagnose_worker() -> None:
            try:
                from ..core.network_actions import diagnose_connection_path
                res = diagnose_connection_path()
                ok = res.get("ok", False)
                failing = res.get("failing_stage", "Desconhecido")
                cause = res.get("root_cause", "")
                if ok:
                    msg = "Conexão íntegra: Cabo, Gateway e Internet funcionando."
                else:
                    msg = f"Falha de rede em [{failing}]: {cause[:60]}..."
                GLib.idle_add(self.show_toast, msg)
            except Exception as exc:
                logger.error("Falha no diagnóstico de rede via paleta: %s", exc)
                GLib.idle_add(self.show_toast, f"Erro no diagnóstico: {exc}")

        import threading
        threading.Thread(target=diagnose_worker, daemon=True).start()

    def _open_infra_inspector(self, initial_tab: str = "hardware") -> None:
        """Abre o diálogo modal com diagnóstico de infraestrutura, containers e hardware."""
        try:
            from .widgets.infra_view import InfraInspectorDialog

            dialog = InfraInspectorDialog(ctx=self)
            child = dialog.get_child()
            if hasattr(child, "view_stack") and initial_tab:
                child.view_stack.set_visible_child_name(initial_tab)
            dialog.present(self)
        except Exception as exc:
            logger.exception("Falha ao abrir diálogo de infraestrutura: %s", exc)
            self.show_toast(f"Erro ao abrir diagnóstico: {exc}")

    def _show_docker_status_command(self) -> None:
        """Inspeciona containers Docker em background e exibe resumo via toast."""
        self.show_toast("Consultando containers Docker...")

        def docker_worker() -> None:
            try:
                from ..core.docker_manager import DockerManager
                dm = DockerManager()
                if not dm.is_available():
                    GLib.idle_add(self.show_toast, "Docker não está disponível ou socket inacessível.")
                    return
                containers = dm.list_containers(all_containers=True)
                running = [c for c in containers if c.state == "running"]
                if not containers:
                    msg = "Nenhum container Docker encontrado no sistema."
                else:
                    names_running = ", ".join(c.name for c in running[:3])
                    msg = f"Docker: {len(running)}/{len(containers)} ativos ({names_running})"
                GLib.idle_add(self.show_toast, msg)
            except Exception as exc:
                logger.error("Falha ao consultar Docker: %s", exc)
                GLib.idle_add(self.show_toast, f"Erro ao consultar Docker: {exc}")

        import threading
        threading.Thread(target=docker_worker, daemon=True).start()

    def _show_hardware_health_command(self) -> None:
        """Coleta métricas térmicas e de pressão de memória em background."""
        self.show_toast("Coletando telemetria de hardware e sensores...")

        def hw_worker() -> None:
            try:
                from ..core.system_health import SystemHealthInspector
                report = SystemHealthInspector.get_complete_report()
                t = report.get("thermals", {})
                m = report.get("memory", {})
                cpu_t = t.get("cpu_temp_c", 0.0)
                gpu_t = t.get("gpu_temp_c", 0.0)
                used_p = m.get("used_percent", 0.0)
                press = m.get("pressure_level", "Baixa")
                msg = f"CPU: {cpu_t}°C · GPU: {gpu_t}°C · RAM: {used_p}% usada (Pressão {press})"
                GLib.idle_add(self.show_toast, msg)
            except Exception as exc:
                logger.error("Falha ao coletar saúde do hardware: %s", exc)
                GLib.idle_add(self.show_toast, f"Erro nos sensores: {exc}")

        import threading
        threading.Thread(target=hw_worker, daemon=True).start()

    def _show_journal_errors_command(self) -> None:
        """Extrai erros críticos recentes do journalctl em background."""
        self.show_toast("Inspecionando diário do sistema (journalctl)...")

        def journal_worker() -> None:
            try:
                from ..core.system_health import SystemHealthInspector
                incidents = SystemHealthInspector.get_journal_incidents(lines=25)
                if not incidents:
                    msg = "Nenhum erro crítico recente identificado no journal."
                else:
                    top = incidents[-1]
                    msg = f"Journal: {len(incidents)} erros recentes (Último em {top.unit}: {top.message[:50]}...)"
                GLib.idle_add(self.show_toast, msg)
            except Exception as exc:
                logger.error("Falha ao consultar journalctl: %s", exc)
                GLib.idle_add(self.show_toast, f"Erro no journalctl: {exc}")

        import threading
        threading.Thread(target=journal_worker, daemon=True).start()

    def run_palette_command(self, command: PaletteCommand) -> None:
        """Executa o comando escolhido no painel.

        Comandos de prompt MCP são roteados por prefixo, não pelo dicionário de
        handlers: os nomes deles vêm dos servidores conectados e mudam em tempo
        de execução, então não há como estarem num mapa estático.
        """
        if mcp_prompts.is_prompt_command(command.name):
            self._run_mcp_prompt(mcp_prompts.qualified_name_from_command(command.name))
            return

        handler = self._palette_handlers().get(command.name)
        if handler is None:
            logger.warning("Comando do painel sem handler: %s", command.name)
            return
        handler()

    # ------------------------------------------------------------------
    # Prompts MCP (paleta de comandos)
    # ------------------------------------------------------------------
    def _run_mcp_prompt(self, qualified_name: str) -> None:
        """Materializa um prompt MCP e o escreve na caixa de entrada.

        Com argumentos declarados, abre antes um diálogo de coleta; sem eles,
        preenche direto — pedir confirmação para um prompt sem parâmetros seria
        um passo a mais sem nada a decidir.
        """
        manager = getattr(self, "mcp_manager", None)
        if manager is None or not qualified_name:
            self.show_toast("Servidores MCP indisponíveis.")
            return

        try:
            prompt = manager.get_prompt(qualified_name)
        except Exception as exc:
            logger.error("Falha ao resolver o prompt MCP '%s': %s", qualified_name, exc)
            self.show_toast("Não foi possível ler o prompt MCP.")
            return

        if prompt is None:
            # O servidor pode ter caído entre a abertura do painel e a escolha.
            self.show_toast("Este prompt não está mais disponível.")
            return

        if not prompt.arguments:
            self._render_mcp_prompt(qualified_name, {})
            return

        self._open_prompt_arguments_dialog(prompt, qualified_name)

    def _open_prompt_arguments_dialog(self, prompt: MCPPrompt, qualified_name: str) -> None:
        """Diálogo Libadwaita que coleta os argumentos do prompt.

        Uma linha de entrada por argumento, na ordem declarada pelo servidor:
        `Adw.EntryRow` dentro de `Adw.PreferencesGroup` é o padrão do GNOME para
        formulário curto, e mantém o diálogo consistente com as Preferências.
        """
        dialog = Adw.Dialog()
        dialog.set_title(mcp_prompts.prettify_prompt_name(prompt.name))
        dialog.set_content_width(460)

        header = Adw.HeaderBar()
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        group.set_title("Argumentos")
        if prompt.description:
            group.set_description(prompt.description)

        entries: dict[str, Any] = {}
        for arg in prompt.arguments:
            row = Adw.EntryRow()
            row.set_title(arg.name)
            if arg.description:
                row.set_tooltip_text(arg.description)
            if arg.required:
                # Sem um sinal textual, um campo obrigatório vazio só se revela
                # no erro do servidor — depois de o usuário já ter confirmado.
                row.add_suffix(_required_badge())
            group.add(row)
            entries[arg.name] = row

        page.add(group)
        toolbar.set_content(page)
        dialog.set_child(toolbar)

        def _on_confirm(_btn) -> None:
            values = {name: row.get_text() for name, row in entries.items()}
            dialog.close()
            self._render_mcp_prompt(qualified_name, values)

        confirm = Gtk.Button(label="Inserir")
        confirm.add_css_class("suggested-action")
        confirm.connect("clicked", _on_confirm)
        header.pack_end(confirm)

        dialog.present(self)

        first = next(iter(entries.values()), None)
        if first is not None:
            GLib.idle_add(_focus_widget_once, first)

    def _render_mcp_prompt(self, qualified_name: str, values: dict[str, str]) -> None:
        """Chama ``prompts/get`` e deposita o texto na caixa de entrada."""
        manager = getattr(self, "mcp_manager", None)
        if manager is None:
            return

        try:
            prompt = manager.get_prompt(qualified_name)
        except Exception:
            prompt = None

        clean = mcp_prompts.clean_arguments(values, prompt)
        missing = mcp_prompts.missing_required_arguments(prompt, clean) if prompt else []
        if missing:
            names = ", ".join(a.name for a in missing)
            self.show_toast(f"Preencha: {names}")
            return

        def worker() -> None:
            try:
                text = manager.render_prompt(qualified_name, clean)
            except Exception as exc:
                logger.error("Falha ao materializar o prompt MCP '%s': %s", qualified_name, exc)
                text = ""

            if not text:
                GLib.idle_add(self.show_toast, "O servidor MCP não devolveu texto para este prompt.")
                return
            GLib.idle_add(self._insert_into_entry, text)

        # `prompts/get` fala com o servidor: fora da thread da UI para não
        # congelar a janela se o servidor estiver lento.
        threading.Thread(target=worker, daemon=True, name="mcp-prompt").start()

    def _insert_into_entry(self, text: str) -> bool:
        """Escreve o texto na caixa de entrada e devolve o foco a ela."""
        current = self.entry.get_text()
        self.entry.set_text(f"{current} {text}".strip() if current else text)
        self.entry.set_position(-1)
        self.entry.grab_focus()
        return GLib.SOURCE_REMOVE

    def palette_closed(self) -> None:
        """Devolve o foco ao campo de prompt depois de fechar o painel."""
        self.entry.grab_focus()

    # ------------------------------------------------------------------
    # Compatibilidade de API (usada por testes e código legado)
    # ------------------------------------------------------------------
    @property
    def window_title(self) -> Adw.WindowTitle:
        return self.header.window_title

    @property
    def sidebar_revealer(self) -> Gtk.Revealer:
        return self.sidebar.revealer

    @property
    def sidebar_search(self) -> Gtk.SearchEntry:
        return self.sidebar.search

    @property
    def history_listbox(self) -> Gtk.ListBox:
        return self.sidebar.history_listbox

    @property
    def clear_history_btn(self) -> Gtk.Button:
        return self.sidebar.clear_history_btn

    @property
    def sidebar_toggle_btn(self) -> Gtk.Button:
        return self.header.sidebar_toggle_btn

    @property
    def new_chat_btn(self) -> Gtk.Button:
        return self.header.new_chat_btn

    @property
    def voice_call_btn(self) -> Gtk.Button:
        return self.header.voice_call_btn

    @property
    def status_badge_btn(self) -> Gtk.Button:
        return self.header.status_badge_btn

    @property
    def status_badge(self) -> Gtk.Label:
        return self.header.status_badge

    @property
    def fence_menu_btn(self) -> Gtk.MenuButton:
        return self.header.fence_menu_btn

    @property
    def fence_lbl(self) -> Gtk.Label:
        return self.header.fence_lbl

    @property
    def chat_stream_box(self) -> Gtk.Box:
        return self.chat_stream.stream_box

    @property
    def chat_scrolled(self) -> Gtk.ScrolledWindow:
        return self.chat_stream.scrolled

    @property
    def welcome_box(self) -> Gtk.Box:
        return self.chat_stream.welcome_box

    @property
    def prompt_bar_box(self) -> Gtk.Box:
        return self.prompt_bar.prompt_bar_box

    @property
    def app_preview_revealer(self) -> Gtk.Revealer:
        return self.prompt_bar.app_preview_revealer

    @property
    def vision_preview_box(self) -> Gtk.Box:
        return self.vision.preview_box

    @property
    def vision_btn(self) -> Gtk.MenuButton:
        return self.prompt_bar.vision_btn

    @property
    def clipboard_btn(self) -> Gtk.MenuButton:
        return self.prompt_bar.clipboard_btn

    @property
    def bottom_voice_btn(self) -> Gtk.Button:
        return self.prompt_bar.bottom_voice_btn

    @property
    def submit_btn(self) -> Gtk.Button:
        return self.prompt_bar.submit_btn

    @property
    def spinner(self) -> Gtk.Spinner:
        return self.prompt_bar.spinner

    @property
    def agent_btn(self) -> Gtk.ToggleButton:
        return self.prompt_bar.agent_btn

    def toggle_agent_mode(self, _btn: Gtk.Widget | None = None) -> None:
        """Alterna o estado do botão de Modo Agente na barra de prompt."""
        active = not self.agent_btn.get_active()
        self.agent_btn.set_active(active)

    def abort_active_agent(self) -> None:
        """Interrompe a execução ativa do agente, se houver uma em andamento."""
        # O subagente primeiro: ele roda DENTRO de um passo do loop principal, e
        # o loop só enxerga o sinal de parada no fim do passo. Abortar só o pai
        # faria o botão parecer travado até o subagente terminar sozinho.
        subagent = getattr(self, "_active_subagent_pipeline", None)
        if subagent is not None:
            subagent.abort()
        if self._active_agent_loop:
            self._active_agent_loop.abort()
            self.show_toast("⏹ Sinal de parada enviado ao agente.")

    # -- watchdog e limpeza de transição ----------------------------------- #

    def _arm_agent_watchdog(self) -> None:
        """Agenda o encerramento forçado da UI se o Modo Agente exceder o limite interno.

        Defeito 1 / Frente A.3: se `_is_busy` continuar True além de
        `agent_max_seconds + 30`, o watchdog aborta e libera o spinner — defesa
        contra planejador travado em rede ruim. Singleton (reaproveita o source).
        """
        self._disarm_agent_watchdog()
        max_seconds = getattr(self.config, "agent_max_seconds", 300.0)
        self._agent_watchdog_source = GLib.timeout_add(
            int((max_seconds + 30) * 1000), self._fire_agent_watchdog
        )

    def _disarm_agent_watchdog(self) -> None:
        """Cancela o timer do watchdog, se houver um armado."""
        src = getattr(self, "_agent_watchdog_source", None)
        if src is not None:
            GLib.source_remove(src)
            self._agent_watchdog_source = None

    def _fire_agent_watchdog(self) -> bool:
        """Callback do watchdog: desliga a UI se o agente passou do limite interno."""
        self._agent_watchdog_source = None
        if not self._is_busy:
            return GLib.SOURCE_REMOVE
        logger.warning("Watchdog do Modo Agente: execução excedeu o limite interno.")
        if self._active_agent_loop is not None:
            self._active_agent_loop.abort()
        self._is_busy = False
        self._active_agent_loop = None
        self._active_agent_widget = None
        self.prompt_bar.set_busy(False)
        self.show_toast("Execução encerrada por tempo limite interno.")
        return GLib.SOURCE_REMOVE

    def _teardown_active_agent_ui(self) -> None:
        """Libera a UI de uma execução de agente descartada (troca/limpeza de conversa).

        Não grava o resultado obsoleto: o thread antigo chamará `_on_agent_finished`
        mais tarde e, ao ver o widget ativo já trocado, descarta o resultado (Defeito 2
        / Frente B.2, opção (a)).
        """
        self._disarm_agent_watchdog()
        widget = self._active_agent_widget
        if widget is not None:
            try:
                widget.set_visible(False)
            except Exception:
                pass
        self._active_agent_loop = None
        self._active_agent_widget = None
        if self._is_busy:
            self._is_busy = False
            self.prompt_bar.set_busy(False)

    # Delegações de comportamento
    def toggle_sidebar(self, _btn: Gtk.Button | None = None) -> None:
        """Alterna a visibilidade da barra lateral de conversas."""
        self.sidebar.toggle()

    def _populate_sidebar_history(self, filter_query: str = "") -> None:
        self.sidebar.populate(filter_query=filter_query)

    def _on_sidebar_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self.sidebar._on_search_changed(entry)

    def _on_history_row_activated(self, _listbox, row) -> None:
        self.sidebar._on_row_activated(_listbox, row)

    def _on_delete_topic(self, topic_id: str) -> None:
        self.sidebar.on_delete_topic(topic_id)

    def _on_clear_all_history(self, _btn: Gtk.Button) -> None:
        self.sidebar._on_clear_all_history(_btn)

    def _rebuild_chat_stream(self) -> None:
        self.chat_stream.rebuild()

    def _create_turn_widget(self, turn, plan=None, image_bytes=None, is_pending=False):
        return self.chat_stream.create_turn_widget(
            turn, plan=plan, image_bytes=image_bytes, is_pending=is_pending
        )

    def _create_action_row(self, action, turn=None, index=0):
        return self.chat_stream.create_action_row(action, turn=turn, index=index)

    def _scroll_to_bottom(self) -> None:
        self.chat_stream.scroll_to_bottom()

    def _update_provider_badge(self) -> None:
        self.header.update_provider_badge()
        # Mantém a barra de status sincronizada quando o provedor/modelo muda.
        if getattr(self, "status_bar", None) is not None:
            self.status_bar.refresh_model()
            self.status_bar.refresh_tokens()

    def _build_fence_popover(self) -> None:
        self.header.build_fence_popover()

    def _on_select_fence_monitor(self, monitor_idx: int, popover: Gtk.Popover) -> None:
        self.header.on_select_fence_monitor(monitor_idx, popover)

    def _on_select_all_monitors(self, popover: Gtk.Popover) -> None:
        self.header.on_select_all_monitors(popover)

    def _on_select_active_window_mode(self, popover: Gtk.Popover) -> None:
        self.header.on_select_active_window_mode(popover)

    def _on_select_window(self, win: Any, popover: Gtk.Popover) -> None:
        self.header.on_select_window(win, popover)

    def _on_toggle_kill_switch(self, popover: Gtk.Popover | None = None) -> None:
        self.header.on_toggle_kill_switch(popover)

    def toggle_kill_switch(self) -> None:
        """Alterna o kill switch sem popover — usado pelo menu da bandeja."""
        self.header.on_toggle_kill_switch(None)
        tray = getattr(self.get_application(), "_tray", None)
        if tray is not None and hasattr(tray, "set_kill_switch_active"):
            tray.set_kill_switch_active(self.fence.is_emergency_stopped)

    def _update_app_preview(self, text: str) -> None:
        self.prompt_bar._update_app_preview(text)

    def _on_quick_launch_app(self, _btn: Gtk.Button) -> None:
        self.prompt_bar._on_quick_launch_app(_btn)

    def _on_submit(self, _widget: Gtk.Widget) -> None:
        self.prompt_bar.submit(_widget)

    def _on_entry_changed(self, entry: Gtk.Entry) -> None:
        self.prompt_bar._on_entry_changed(entry)

    def _render_active_vision_thumbnail(
        self, image_bytes: bytes, is_area: bool = True, is_clipboard: bool = False
    ) -> None:
        self.vision.render_thumbnail(image_bytes, is_area=is_area, is_clipboard=is_clipboard)

    def _clear_active_vision(self, _btn: Gtk.Button | None = None) -> None:
        self.vision.clear(_btn)

    def _start_screen_capture(self, interactive: bool = True, direct_mode: bool = False) -> None:
        self.vision.start_capture(interactive=interactive, direct_mode=direct_mode)

    def _on_capture_finished(
        self, success, image_bytes, mode, prompt_typed, is_area, direct_mode=False
    ) -> bool:
        return self.vision._on_capture_finished(
            success, image_bytes, mode, prompt_typed, is_area, direct_mode
        )

    # ------------------------------------------------------------------
    # Ciclo de vida e HUD
    # ------------------------------------------------------------------
    def _on_realize_window(self, _win) -> None:
        """Aplica blur/rounding reais do compositor (Hyprland) à janela principal."""
        try:
            hyprland_effects.apply_for_app(__app_id__)
        except Exception as exc:  # nunca deve derrubar a janela
            logger.debug("não foi possível aplicar blur do compositor: %s", exc)

    def _on_close_request(self, _win) -> bool:
        """Em modo HUD, oculta a janela sem matar o processo em segundo plano."""
        if self.live_client and self.live_client.is_active():
            self.stop_live_voice()
        self._cancel_pending_end_session()
        self.set_visible(False)
        return True

    def teardown_background_services(self) -> None:
        """Devolve tudo o que a janela subiu em segundo plano.

        Motivo: uma `CopilotWindow` não é só widgets. Ela carrega subprocessos de
        servidor MCP (`uvx`/`npx`), uma varredura de disco do indexador RAG e o
        motor de wake word com o microfone aberto. Nada disso é liberado pela
        coleta de lixo do GTK, porque cada um mantém a própria thread — que, por
        sua vez, mantém a janela viva por referência. O resultado medido era de
        **+109,5 MB e +1,80 processo por janela**, nunca devolvidos; numa suíte
        que cria dezenas de janelas (há um teste com 30), é o que levava o
        sistema ao OOM killer.

        Idempotente: `destroy` e `do_shutdown` podem chamar em qualquer ordem.
        """
        if getattr(self, "_services_torn_down", False):
            return
        self._services_torn_down = True

        # 1. Voz ao vivo — solta o microfone e a conexão websocket.
        try:
            if self.live_client and self.live_client.is_active():
                self.stop_live_voice()
        except Exception as exc:
            logger.debug("Falha ao encerrar voz ao vivo no teardown: %s", exc)

        self._cancel_pending_end_session()

        # 2. Wake word — para a thread de captura de áudio.
        try:
            if self.wake_word_engine is not None:
                self.wake_word_engine.stop()
        except Exception as exc:
            logger.debug("Falha ao parar wake word no teardown: %s", exc)

        # 3. RAG — cancela a varredura de disco antes de ela terminar sozinha.
        try:
            if getattr(self, "rag", None) is not None:
                self.rag.cancel_indexing()
        except Exception as exc:
            logger.debug("Falha ao cancelar indexação RAG no teardown: %s", exc)

        # 4. Servidores MCP — mata os subprocessos externos.
        try:
            if getattr(self, "mcp_manager", None) is not None:
                self.mcp_manager.stop_all()
        except Exception as exc:
            logger.debug("Falha ao parar servidores MCP no teardown: %s", exc)

        # 5. Anúncios da casa — cancela a assinatura e cala a fala.
        # Sem isto o callback continuaria assinado no canal (que é singleton e
        # sobrevive à janela) e tentaria falar numa janela já destruída.
        try:
            self._stop_ha_announcements()
        except Exception as exc:
            logger.debug("Falha ao parar anúncios da casa no teardown: %s", exc)

        # 6. Consolidação da memória — desarma o temporizador.
        # A thread em andamento é `daemon` e termina sozinha; o que não pode ficar
        # é o `timeout_add_seconds` pendurado no loop principal chamando método de
        # janela destruída.
        try:
            self._stop_consolidation()
        except Exception as exc:
            logger.debug("Falha ao parar a consolidação no teardown: %s", exc)

        # 7. Monitor de arquivos — cancela os monitores do Gio e a thread de
        # debounce. A thread é `daemon` e sairia sozinha, mas os monitores não:
        # eles vivem no barramento do Gio e manteriam a referência da janela.
        try:
            self._stop_file_watcher()
        except Exception as exc:
            logger.debug("Falha ao parar o monitor de arquivos no teardown: %s", exc)

    def destroy(self) -> None:
        """Libera os serviços de segundo plano e destrói a janela.

        Por que sobrescrever o método em vez de conectar ao sinal `destroy`:
        medido nesta versão do GTK4, `Gtk.Window.destroy()` **não emite** o
        sinal `destroy` — nem em janela nunca apresentada, nem em janela
        apresentada (aí só sai `unrealize`). O sinal existe no GType, mas é
        vestigial. Um handler conectado a ele jamais rodaria, e o custo por
        janela nunca voltaria a zero nos testes.

        `unrealize` também não serve: ele dispara quando a janela é apenas
        escondida, e no modo HUD esconder a janela é o comportamento normal —
        derrubar MCP/RAG a cada `Esc` deixaria o app inútil ao reabrir.
        """
        self.teardown_background_services()
        super().destroy()

    def summon_hud(self) -> None:
        """Apresenta a janela com foco imediato no campo de busca com zero latência."""
        # Captura contexto externo antes de trazer a janela para o primeiro plano
        if getattr(self.config, "context_awareness_enabled", True):
            try:
                ctx = self.context_detector.detect_context()
                self.current_context = ctx
                self.chat_stream.update_context(ctx)
                if hasattr(self, "prompt_bar") and hasattr(self.prompt_bar, "set_active_context"):
                    self.prompt_bar.set_active_context(ctx)
            except Exception as exc:
                logger.debug("Falha ao detectar contexto do desktop no summon_hud: %s", exc)

        self.set_visible(True)
        self.present()
        self.entry.grab_focus()

    def toggle_hud(self) -> None:
        """Alterna a visibilidade da janela em modo HUD."""
        if self.get_visible() and self.is_active():
            self.set_visible(False)
        else:
            self.summon_hud()

    def show_toast(self, message: str) -> None:
        """Exibe uma notificação flutuante elegante na janela."""
        self.toast_overlay.add_toast(Adw.Toast.new(message))

    # ------------------------------------------------------------------
    # Configurações
    # ------------------------------------------------------------------
    def _open_settings(self, _btn: Gtk.Button | None = None) -> None:
        dialog = PreferencesDialog(self, on_saved=self._on_config_saved)
        dialog.present(self)

    def _open_model_selector(self, _btn: Gtk.Button | None = None) -> None:
        from .widgets.model_selector import ModelSelectorDialog
        dialog = ModelSelectorDialog(self)
        dialog.present(self)

    def _show_shortcut_cheatsheet(self, _btn: Gtk.Button | None = None) -> None:
        """Referência rápida dos atalhos, com as teclas renderizadas como kbd.

        Fecha o ciclo do pilar "atalhos fluidos": o painel de comandos já lista
        cada ação com o seu acelerador, e este diálogo dá a visão de conjunto —
        a mesma promessa anotada em `core.shortcuts.APP_SHORTCUTS`, que previa
        uma cheatsheet.
        """
        from .widgets.command_palette import _accelerator_badge

        dialog = Adw.Dialog()
        dialog.set_title("Atalhos de teclado")
        dialog.set_content_width(420)

        header = Adw.HeaderBar()
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)

        page = Adw.PreferencesPage()

        app_group = Adw.PreferencesGroup()
        app_group.set_title("Aplicativo")
        app_group.set_description("Funcionam com a janela do Copilot em foco.")

        for shortcut in APP_SHORTCUTS:
            row = Adw.ActionRow(title=shortcut.description)
            row.add_suffix(_accelerator_badge(shortcut.accelerator))
            row.set_activatable(False)
            app_group.add(row)

        global_group = Adw.PreferencesGroup()
        global_group.set_title("Globais do sistema")
        global_group.set_description(
            "Registrados no ambiente (GNOME, Hyprland, Sway ou KDE) e ativos "
            "mesmo com o Copilot em segundo plano."
        )
        for accelerator, description in (
            (self.config.global_shortcut_key, "Abrir/ocultar o Copilot"),
            (self.config.crop_shortcut_key, "Recortar área da tela"),
            (self.config.voice_shortcut_key, "Conversa por voz ao vivo"),
            (self.config.dictate_shortcut_key, "Ditado no app em foco"),
        ):
            row = Adw.ActionRow(title=description)
            row.add_suffix(_accelerator_badge(accelerator))
            row.set_activatable(False)
            global_group.add(row)

        page.add(app_group)
        page.add(global_group)
        toolbar.set_content(page)
        dialog.set_child(toolbar)
        dialog.present(self)

    def _on_config_saved(self, new_config: CopilotConfig) -> None:
        self.config = new_config
        self.engine.reload_config(new_config)
        self.header.update_provider_badge()
        # Propaga autonomia/zonas para a política compartilhada já viva — sem isto a
        # mudança só valeria no próximo arranque.
        if getattr(self, "risk_policy", None) is not None:
            from ..shell.risk import normalize_autonomy_level

            self.risk_policy.autonomy_level = normalize_autonomy_level(
                getattr(new_config, "autonomy_level", "standard")
            )
            self.risk_policy.set_trusted_dirs(
                getattr(new_config, "trusted_directories", None)
            )
        # A identidade pode ter mudado: o título tem de acompanhar na hora, e não só
        # no próximo arranque. É a mesma regra do monitor de arquivos — salvar a
        # preferência vale **já**, senão o usuário conclui que o campo não funcionou.
        self._apply_identity()
        self._reconfigure_wake_word()
        self._reconfigure_ha_announcements()
        self._reconfigure_file_watcher()
        self._reconfigure_consolidation()
        if hasattr(self, "executor") and hasattr(self, "mcp_manager"):
            self.executor.mcp_manager = self.mcp_manager if getattr(new_config, "mcp_enabled", True) else None

    # ------------------------------------------------------------------
    # Identidade (ver `core/identity.py`)
    # ------------------------------------------------------------------
    def _apply_identity(self) -> None:
        """Aplica a identidade ao que é **visível**. Idempotente.

        Hoje é uma coisa só — o título da janela —, mas mora num método porque é o
        ponto de extensão: notificações, rótulos e a saudação da pílula passam todos
        por aqui quando existirem. Deixar o `set_title` solto no `__init__` foi
        exatamente o que fez o nome ficar cravado em quatro lugares.
        """
        self.set_title(identity_from_config(getattr(self, "config", None)).display_name)

    # ------------------------------------------------------------------
    # Sugestões rápidas
    # ------------------------------------------------------------------
    def _open_compare(self, anchor_turn=None) -> None:
        """Abre o diálogo de comparação side-by-side de respostas (item #5)."""
        dlg = CompareResponsesDialog(self, anchor_turn)
        dlg.present(self)

    def _trigger_suggestion(self, suggestion: Any) -> None:
        """Dispara uma ação ou preenche o prompt a partir de uma ContextSuggestion."""
        action_type = getattr(suggestion, "action_type", "prompt")
        s_id = getattr(suggestion, "id", "")

        if action_type == "voz_ao_vivo":
            self.toggle_live_voice()
            return
        if action_type == "recortar_area":
            self._start_screen_capture(interactive=True)
            return
        if action_type == "capturar_tela":
            self._start_screen_capture(interactive=False)
            return
        if action_type == "analisar_copiado":
            self.entry.set_text("Analisar conteúdo da área de transferência")
            self.prompt_bar.submit(self.entry)
            return

        prompt_text = getattr(suggestion, "prompt_template", "") or str(suggestion)

        # 1. Auto-grounding visual para páginas web e documentos em foco
        if s_id in ("summarize_page", "summarize_doc"):
            ctx = getattr(self, "current_context", None)
            crop_rect = getattr(ctx, "bbox", None) if ctx else None
            self.set_visible(False)

            def capture_worker():
                time.sleep(0.15)
                success, img_bytes, _ = ScreenCaptureService.capture(
                    interactive=False, crop_rect=crop_rect
                )

                def on_done():
                    self.set_visible(True)
                    self.present()
                    if success and img_bytes:
                        self._active_image_bytes = img_bytes
                        self._active_image_is_area = bool(crop_rect)
                        lbl = "Janela em foco capturada" if crop_rect else "Tela inteira capturada"
                        self.vision.render_thumbnail(img_bytes, is_area=bool(crop_rect), label=lbl)
                    self.entry.set_text(prompt_text)
                    self.prompt_bar.submit(self.entry)

                GLib.idle_add(on_done)

            threading.Thread(target=capture_worker, daemon=True).start()
            return

        # 2. Auto-grounding de código para arquivos identificados no editor
        if s_id in ("explain_code_file", "generate_unit_tests", "refactor_code"):
            ctx = getattr(self, "current_context", None)
            if ctx and ctx.extracted_target:
                target_path = ctx.extracted_target.strip()
                resolved_file = None
                if os.path.isabs(target_path) and os.path.isfile(target_path):
                    resolved_file = target_path
                elif ctx.git_repo and os.path.isdir(ctx.git_repo):
                    candidate = os.path.join(ctx.git_repo, target_path)
                    if os.path.isfile(candidate):
                        resolved_file = candidate
                    else:
                        for root, _, files in os.walk(ctx.git_repo):
                            if target_path in files:
                                resolved_file = os.path.join(root, target_path)
                                break
                elif os.path.isfile(target_path):
                    resolved_file = os.path.abspath(target_path)

                if resolved_file:
                    try:
                        att = load_attachment(resolved_file)
                        if att.ok:
                            existing = [a.path for a in self.attachments]
                            if att.path not in existing:
                                self.attachments.append(att)
                                self.attachment_bar.refresh()
                    except Exception as exc:
                        logger.debug("Falha ao anexar arquivo de contexto: %s", exc)

        self.entry.set_text(prompt_text)
        self.prompt_bar.submit(self.entry)

    def _trigger_prompt(self, text: str) -> None:
        """Dispara um prompt ou ação a partir de um chip de sugestão rápida."""
        if text == "voz_ao_vivo":
            self.toggle_live_voice()
            return
        if text == "recortar_area":
            self._start_screen_capture(interactive=True)
            return
        if text == "capturar_tela":
            self._start_screen_capture(interactive=False)
            return
        if text == "analisar_copiado":
            self.entry.set_text("\U0001f4cb Analisar conteúdo da área de transferência")
            self.prompt_bar.submit(self.entry)
            return
        self.entry.set_text(text)
        self.prompt_bar.submit(self.entry)

    # ------------------------------------------------------------------
    # Ciclo de uma mensagem
    # ------------------------------------------------------------------
    def _on_plan_ready(
        self, plan: ActionPlan, prompt_text: str = "", attached_image: bytes | None = None
    ) -> bool:
        """Recebe o plano gerado pela IA e substitui o indicador de carregamento pelo resultado."""
        self._is_busy = False
        self.prompt_bar.set_busy(False)
        self.current_plan = plan
        self.chat_stream.welcome_box.set_visible(False)

        if self._pending_turn_box and self._pending_turn_box.get_parent() is self.chat_stream.stream_box:
            self.chat_stream.stream_box.remove(self._pending_turn_box)
            self._pending_turn_box = None

        explanation_text = plan.thought.strip()
        if not explanation_text and plan.actions:
            explanation_text = "Executei a ação solicitada no desktop."

        # O plano é gravado no turno para que os botões de ação continuem
        # disponíveis depois de um `rebuild()` (troca de tópico, undo) e após
        # reabrir o app — não apenas enquanto for a última resposta.
        turn = self.session.record_turn(prompt=prompt_text, answer=explanation_text, plan=plan)

        # Modo de estudos: se o prompt for uma equação/sistema resolvível por
        # álgebra pura, anexa o SolutionGraph ao turno (o botão abre na hora).
        # Se não casar mas PARECER matemática, guarda o enunciado para resolver
        # sob demanda (model-backed) quando o usuário clicar no botão — assim
        # não pagamos uma segunda chamada de LLM em toda resposta.
        try:
            from ..ai.math_solver import looks_like_math, try_solve

            graph = try_solve(prompt_text) if prompt_text else None
            if graph is not None:
                turn.solution = graph
            elif prompt_text and looks_like_math(prompt_text):
                turn.solution_problem = prompt_text
        except Exception:  # noqa: BLE001 - nunca deve derrubar a entrega da resposta
            logger.debug("try_solve falhou para o prompt; seguindo sem Quadro Negro", exc_info=True)

        self._save_current_session()
        self._update_pin_ui()

        self.chat_stream.stream_box.append(
            self.chat_stream.create_turn_widget(turn, plan=plan, image_bytes=attached_image)
        )

        if self.session.title:
            self.window_title.set_subtitle(self.session.title)

        self.sidebar.populate(filter_query=self.sidebar.search.get_text().strip())
        self.chat_stream.scroll_to_bottom()
        self.entry.grab_focus()

        # Consumo de tokens acumulado nesta resposta (item #2 e #9).
        self.header.refresh_token_usage()
        self.status_bar.refresh_tokens()

        # Execução automática proativa se habilitada nas preferências e ações forem seguras
        if not _is_test_environment():
            executable = [a for a in plan.actions if a.action_type != ActionType.ANSWER]
            if getattr(self.config, "auto_execute_safe_actions", False) and executable:
                safe_types = {ActionType.WRITE_FILE, ActionType.OPEN_DOCUMENT, ActionType.LAUNCH_APP, ActionType.OPEN_URL, ActionType.NOTIFY}
                if all(not a.requires_confirmation and a.action_type in safe_types for a in executable):
                    GLib.timeout_add(400, lambda: self._auto_execute_safe_plan(plan))

        return GLib.SOURCE_REMOVE

    def _auto_execute_safe_plan(self, plan: ActionPlan) -> bool:
        """Executa automaticamente ações seguras propostas pela IA (ex: salvar/abrir documentos)."""
        try:
            reports = self.execute_plan_with_undo(plan)
            ok_count = sum(1 for r in reports if r.success)
            if ok_count:
                self.show_toast(f"{ok_count} ação(ões) executada(s) automaticamente no desktop")
        except Exception as exc:
            logger.debug(f"Falha na auto-execução de ações seguras: {exc}")
        return GLib.SOURCE_REMOVE

    def _on_copy_answer(self, _btn: Gtk.Button) -> None:
        text = self._raw_answer_text or self.answer_label.get_text()
        if not text:
            return
        display = Gdk.Display.get_default()
        if display:
            display.get_clipboard().set(text)
            self.show_toast("Resposta copiada para a área de transferência")

    def _on_copy_ocr_text(self, _btn: Gtk.Button) -> None:
        ocr_text = getattr(self, "_current_ocr_text", None)
        if not ocr_text:
            return
        from ..core.clipboard import ClipboardService
        if ClipboardService.set_text(ocr_text):
            self.show_toast("Texto copiado para a área de transferência!")

    # ------------------------------------------------------------------
    # Execução do Modo Agente
    # ------------------------------------------------------------------
    def start_agent_execution(
        self,
        objective: str,
        attached_image: bytes | None = None,
        full_prompt: str | None = None,
        vision: bool = False,
    ) -> None:
        """Inicia a execução autônoma supervisionada de um objetivo no desktop."""
        import threading
        from ..ai.agent import AgentLoop
        from ..ai.agent_router import AgentRouter, RouteMode, get_step_budget
        from ..ai.agent_tools import ToolRegistry
        from .widgets.agent_card import AgentExecutionWidget

        try:
            self._is_busy = True
            self.prompt_bar.set_busy(True)
            self.chat_stream.welcome_box.set_visible(False)

            prompt_to_run = full_prompt or objective

            # Adiciona a bolha do usuário e cria o widget de execução do agente
            temp_turn = ChatTurn(prompt=objective, answer="")
            user_bubble = self.chat_stream._build_user_bubble(temp_turn, attached_image)

            agent_widget = AgentExecutionWidget(
                objective=objective,
                ctx=self,
                on_abort=self.abort_active_agent,
            )
            self._active_agent_widget = agent_widget

            turn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            turn_box.append(user_bubble)
            turn_box.append(agent_widget)

            self.chat_stream.stream_box.append(turn_box)
            self.chat_stream.scroll_to_bottom()
        except Exception as exc:
            logger.exception("Erro ao iniciar execução do modo agente: %s", exc)
            self._is_busy = False
            self.prompt_bar.set_busy(False)
            if not self.session.turns:
                self.chat_stream.welcome_box.set_visible(True)
            self.show_toast(f"Erro no modo agente: {exc}")
            return

        session_history = self.session.get_history_for_llm() if hasattr(self, "session") and self.session else []

        def agent_thread():
            result = None
            err = ""
            try:
                router = AgentRouter(self.config)
                route = router.route(prompt_to_run, RouteMode.AUTO)

                if route.planner is None:
                    err = f"Nenhum modelo disponível para o modo agente: {route.reason}."
                    GLib.idle_add(agent_widget.status_lbl.set_text, err)
                    return

                # Confina todas as ações estritamente na área/janela em que o assistente já está atuando
                try:
                    from ..core.window_manager import WindowManager
                    from ..core.fence import FenceMode
                    target_win = WindowManager.get_active_or_last_window()
                    if target_win and self.fence:
                        if self.fence.mode in (FenceMode.ACTIVE_WINDOW, FenceMode.PRIMARY_ONLY) or not self.fence.chosen_window:
                            self.fence.set_chosen_window(target_win)
                            logger.info("Cerca espacial confinada na janela ativa: %s", target_win.display_name())
                except Exception as exc:
                    logger.debug("Falha ao autoconfinar cerca na janela ativa: %s", exc)

                undo_stack = getattr(self.executor, "undo_stack", None)
                mcp_mgr = self.mcp_manager if getattr(self.config, "mcp_enabled", True) else None
                registry = ToolRegistry(
                    inspector=self.inspector,
                    input_driver=getattr(self.executor, "input_driver", None),
                    undo_stack=undo_stack,
                    fence=self.fence,
                    policy=self.risk_policy,
                    mcp_manager=mcp_mgr,
                    board_opener=self._open_blackboard_for_tool,
                )

                # Subagentes. `approval_ref` existe só por ordem de definição: o
                # aprovador é montado mais abaixo, e reordenar este método inteiro
                # para satisfazer a ordem seria pior do que um dicionário de uma chave.
                approval_ref: dict[str, Any] = {}
                registry.subagent_runner = self._build_subagent_runner(
                    registry, route.planner, agent_widget, approval_ref
                )

                base_steps = getattr(self.config, "agent_max_steps", 15)
                base_seconds = getattr(self.config, "agent_max_seconds", 300.0)
                adaptive = getattr(self.config, "agent_adaptive_budget", True)
                max_steps, max_seconds = get_step_budget(
                    prompt_to_run,
                    base_max_steps=base_steps,
                    base_max_seconds=base_seconds,
                    adaptive=adaptive,
                )

                loop = AgentLoop(
                    route.planner,
                    registry,
                    max_steps=max_steps,
                    max_seconds=max_seconds,
                    memory=self.engine.memory,
                    session_context=session_history,
                )

                # Modo guiado por visão: percepção fusionada + validação pré/pós-ação + anti-loop.
                if vision:
                    try:
                        from ..ai.browser_runtime import (
                            AntiLoopGuard,
                            VLMConditionChecker,
                            VisionBrowserPlanner,
                            VisionLLMPlanner,
                            ValidatingToolRegistry,
                        )
                        from ..core.perception import build_vision_perception

                        perception = build_vision_perception()
                        provider = getattr(route.planner, "provider", None)
                        if provider is None:
                            for getter in (router.cloud_planner, router.local_planner):
                                try:
                                    provider = getter().provider
                                    if provider:
                                        break
                                except Exception:
                                    pass
                        if provider is not None:
                            vplanner = VisionLLMPlanner(provider, perception=perception)
                        else:
                            vplanner = VisionBrowserPlanner(route.planner, perception=perception)
                        condition_checker = (
                            VLMConditionChecker(provider, perception)
                            if provider is not None
                            else None
                        )
                        vregistry = ValidatingToolRegistry(
                            registry,
                            perception=perception,
                            guard=AntiLoopGuard(),
                            condition_checker=condition_checker,
                        )
                        loop = AgentLoop(
                            vplanner,
                            vregistry,
                            max_steps=max_steps,
                            max_seconds=max_seconds,
                            memory=self.engine.memory,
                            session_context=session_history,
                        )
                        logger.info("Modo agente com visao ativo (percepcao + validacao + anti-loop).")
                    except Exception as exc:
                        logger.warning("Falha ao ativar modo visao na UI: %s", exc)

                self._active_agent_loop = loop

                def on_step(step):
                    GLib.idle_add(agent_widget.add_step, step)

                def on_approval(step) -> bool:
                    approval_event = threading.Event()
                    result_dict = {"approved": False}

                    def show_approval():
                        agent_widget.prompt_approval(step, approval_event, result_dict)
                        return GLib.SOURCE_REMOVE

                    GLib.idle_add(show_approval)
                    approval_event.wait(timeout=180.0)
                    return result_dict["approved"]

                # O subagente compartilha o aprovador do pai: uma ação de risco pedida
                # lá dentro continua chegando ao usuário, com o mesmo diálogo.
                approval_ref["fn"] = on_approval

                try:
                    result = loop.run(prompt_to_run, on_step=on_step, on_approval=on_approval)
                except Exception as exc:
                    logger.exception("Erro durante a execução do agente")
                    result = None
                    err = str(exc)
                else:
                    err = ""
            except Exception as exc:
                # Qualquer falha no setup (antes do loop.run) cai aqui: o
                # finally garante que a UI seja destravada (Defeito 3).
                logger.exception("Erro no setup do modo agente: %s", exc)
                result = None
                err = str(exc)
            finally:
                # Cleanup inevitável: chega em _on_agent_finished por qualquer
                # caminho de saída (exceção, retorno antecipado, KeyboardInterrupt).
                GLib.idle_add(self._on_agent_finished, result, objective, agent_widget, err)

        # Watchdog (Defeito 1 / Frente A.3): força o fim da UI se o agente
        # exceder `agent_max_seconds + 30`.
        self._arm_agent_watchdog()
        threading.Thread(target=agent_thread, daemon=True).start()

    def _build_subagent_runner(
        self,
        registry: Any,
        planner: Any,
        agent_widget: Any,
        approval_ref: dict,
    ) -> Any:
        """Monta o gancho `subagent_runner` do registro (ver `ai/subagents.py`).

        O gancho é chamado de dentro de `registry.call`, ou seja, no thread do
        agente — por isso todo toque em widget passa por `GLib.idle_add`, a mesma
        regra do `on_step`. O pipeline recebe o registro **completo** e deriva um
        restrito por especialista: é a restrição de schema que impede um
        subagente de abrir outro (nenhuma allowlist autoriza `run_subagent`).
        """
        def run_subagent_here(
            specialist: str,
            data: str,
            *,
            target_window: str = "",
            scratchpad_key: str = "",
        ) -> dict:
            from ..ai.subagents import build_pipeline

            pipeline = build_pipeline(
                [specialist],
                registry,
                planner,
                memory=self.engine.memory,
                on_stage=lambda state: GLib.idle_add(
                    agent_widget.add_subagent_stage, state
                ),
                on_step=lambda _spec, step: GLib.idle_add(agent_widget.add_step, step),
                on_approval=approval_ref.get("fn"),
            )
            # Guardado para o botão de parada alcançar o subagente em execução.
            self._active_subagent_pipeline = pipeline
            try:
                result = pipeline.run(
                    data,
                    target_window=target_window,
                    scratchpad_key=scratchpad_key,
                )
            finally:
                self._active_subagent_pipeline = None
            return {
                "ok": result.ok,
                "specialist": specialist,
                "message": result.message,
                "report": result.output,
                "error": result.error,
                "stages": [r.to_dict() for r in result.runs],
            }

        return run_subagent_here

    def continue_agent_execution(
        self,
        previous_result: Any,
        agent_widget: Any,
        additional_steps: int = 10,
    ) -> None:
        """Continua a execução de uma tarefa do Modo Agente que pausou por limite de passos."""
        import threading
        from ..ai.agent import AgentLoop
        from ..ai.agent_router import AgentRouter, RouteMode
        from ..ai.agent_tools import ToolRegistry

        if self._is_busy:
            self.show_toast("Aguarde a operação atual terminar.")
            return

        self._is_busy = True
        self.prompt_bar.set_busy(True)
        self._active_agent_widget = agent_widget

        objective = previous_result.objective
        existing_steps = list(previous_result.steps or [])
        new_max_steps = len(existing_steps) + max(1, int(additional_steps))
        timeout_seconds = getattr(self.config, "agent_max_seconds", 300.0)
        session_history = self.session.get_history_for_llm() if hasattr(self, "session") and self.session else []

        def continue_thread():
            result = None
            err = ""
            try:
                router = AgentRouter(self.config)
                route = router.route(objective, RouteMode.AUTO)

                if route.planner is None:
                    err = f"Nenhum modelo disponível para continuar: {route.reason}."
                    GLib.idle_add(agent_widget.status_lbl.set_text, err)
                    return

                # Confina todas as ações estritamente na área/janela em que o assistente já está atuando
                try:
                    from ..core.window_manager import WindowManager
                    from ..core.fence import FenceMode
                    target_win = WindowManager.get_active_or_last_window()
                    if target_win and self.fence:
                        if self.fence.mode in (FenceMode.ACTIVE_WINDOW, FenceMode.PRIMARY_ONLY) or not self.fence.chosen_window:
                            self.fence.set_chosen_window(target_win)
                except Exception as exc:
                    logger.debug("Falha ao autoconfinar cerca na janela ativa: %s", exc)

                undo_stack = getattr(self.executor, "undo_stack", None)
                mcp_mgr = self.mcp_manager if getattr(self.config, "mcp_enabled", True) else None
                registry = ToolRegistry(
                    inspector=self.inspector,
                    input_driver=getattr(self.executor, "input_driver", None),
                    undo_stack=undo_stack,
                    fence=self.fence,
                    policy=self.risk_policy,
                    mcp_manager=mcp_mgr,
                    board_opener=self._open_blackboard_for_tool,
                )

                # Subagentes também na continuação — ver `run_agent_task`.
                approval_ref: dict[str, Any] = {}
                registry.subagent_runner = self._build_subagent_runner(
                    registry, route.planner, agent_widget, approval_ref
                )

                loop = AgentLoop(
                    route.planner,
                    registry,
                    max_steps=new_max_steps,
                    max_seconds=timeout_seconds,
                    memory=self.engine.memory,
                    session_context=session_history,
                    run_id=previous_result.run_id or None,
                )
                self._active_agent_loop = loop

                def on_step(step):
                    GLib.idle_add(agent_widget.add_step, step)

                def on_approval(step) -> bool:
                    approval_event = threading.Event()
                    result_dict = {"approved": False}

                    def show_approval():
                        agent_widget.prompt_approval(step, approval_event, result_dict)
                        return GLib.SOURCE_REMOVE

                    GLib.idle_add(show_approval)
                    approval_event.wait(timeout=180.0)
                    return result_dict["approved"]

                approval_ref["fn"] = on_approval

                try:
                    result = loop.run(
                        objective,
                        on_step=on_step,
                        on_approval=on_approval,
                        initial_steps=existing_steps,
                    )
                except Exception as exc:
                    logger.exception("Erro durante a continuação do agente")
                    result = None
                    err = str(exc)
                else:
                    err = ""
            except Exception as exc:
                # Defeito 3 (caminho de continuação): qualquer falha no setup
                # cai aqui e o finally destrava a UI.
                logger.exception("Erro no setup da continuação do agente: %s", exc)
                result = None
                err = str(exc)
            finally:
                # Cleanup inevitável (igual a start_agent_execution).
                GLib.idle_add(self._on_agent_finished, result, objective, agent_widget, err, True)

        # Watchdog (Defeito 1 / Frente A.3).
        self._arm_agent_watchdog()
        threading.Thread(target=continue_thread, daemon=True).start()

    def _on_agent_finished(
        self,
        result: Any | None,
        objective: str,
        widget: Any,
        error_msg: str = "",
        is_continuation: bool = False,
    ) -> bool:
        """(main thread) Finaliza a execução do agente e atualiza o histórico e a UI."""
        # Watchdog (Defeito 1 / Frente A.3): a execução terminou, cancela o timer.
        self._disarm_agent_watchdog()

        # A execução foi descartada por troca/limpeza de conversa? O widget ativo
        # já não é este (a transição o anulou): libera o spinner, mas NÃO grava o
        # resultado obsoleto na sessão atual (Defeito 2 / Frente B.2, opção (a)).
        active_widget = self._active_agent_widget
        if widget is not active_widget:
            self._is_busy = False
            self._active_agent_loop = None
            self.prompt_bar.set_busy(False)
            return GLib.SOURCE_REMOVE

        self._is_busy = False
        self._active_agent_loop = None
        self._active_agent_widget = None
        self.prompt_bar.set_busy(False)

        if result is not None:
            widget.finish(result)
            answer_text = result.final_answer or result.stop_message
            if is_continuation and result is not None and self.session.turns and self.session.turns[-1].agent_result:
                self.session.turns[-1].answer = answer_text
                self.session.turns[-1].agent_result = result.to_dict()
                self.session.updated_at = datetime.now().isoformat()
            else:
                self.session.record_turn(
                    prompt=objective,
                    answer=answer_text,
                    agent_result=result.to_dict(),
                )
        else:
            if hasattr(widget, "finish_error"):
                widget.finish_error(error_msg or "Falha na execução do agente.")
            self.session.record_turn(
                prompt=objective,
                answer=error_msg or "Falha na execução do agente.",
            )

        self._save_current_session()
        self._update_pin_ui()
        if self.session.title:
            self.window_title.set_subtitle(self.session.title)
        self.sidebar.populate(filter_query=self.sidebar.search.get_text().strip())
        self.chat_stream.scroll_to_bottom()
        self.entry.grab_focus()

        # Atualiza tokens e status
        self.header.refresh_token_usage()
        self.status_bar.refresh_tokens()

        if result and result.success:
            self.show_toast(f"Modo Agente: objetivo cumprido em {len(result.steps)} passos.")
        elif result and result.stop_reason == "max_steps":
            self.show_toast("Modo Agente: limite de passos atingido. Clique em 'Continuar' para conceder mais passos.")
        elif result and result.stop_reason == "aborted":
            self.show_toast("Modo Agente: execução interrompida.")
        elif result and result.stop_reason == "rejected":
            self.show_toast("Modo Agente: ação recusada pelo usuário.")
        elif error_msg:
            self.show_toast(f"Modo Agente: {error_msg}")

        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------
    # Desfazer ações (Ctrl+Z)
    # ------------------------------------------------------------------
    def execute_plan_with_undo(
        self,
        plan: ActionPlan,
        *,
        ui_key: str | None = None,
        on_undone=None,
    ) -> list[ExecutionReport]:
        """Executa o plano e oferece desfazer se alguma ação for reversível.

        Compara o topo da pilha por identidade, não por tamanho: com a pilha
        cheia um `push` descarta a mais antiga e o tamanho não muda.
        """
        stack = getattr(self.executor, "undo_stack", None)
        if stack is None:
            # Executor sem suporte a desfazer (ou substituído por um duplo em
            # teste): executa normalmente, só não oferece o botão.
            return self.executor.execute_plan(plan, dry_run=False)

        top_before = stack.peek()
        reports = self.executor.execute_plan(plan, dry_run=False)
        top_after = stack.peek()

        if top_after is not None and top_after is not top_before:
            top_after.ui_key = ui_key
            top_after.on_undone = on_undone
            self._offer_undo(top_after)
        return reports

    def _offer_undo(self, entry: UndoEntry) -> None:
        """Toast com botão "Desfazer" — o jeito GTK de não interromper o fluxo."""
        toast = Adw.Toast.new(entry.label)
        toast.set_button_label("Desfazer")
        # Mais tempo que o padrão: decidir se quer desfazer leva alguns segundos.
        toast.set_timeout(10)
        toast.connect("button-clicked", lambda *_: self.undo_last_action())
        self.toast_overlay.add_toast(toast)

    def undo_last_action(self, *_args) -> None:
        """Reverte a ação reversível mais recente e avisa o resultado."""
        stack = getattr(self.executor, "undo_stack", None)
        if stack is None or not len(stack):
            self.show_toast("Nada para desfazer.")
            return

        ok, message, entry = stack.undo()
        if entry is not None and entry.ui_key and entry.on_undone is None:
            # Sem hook de repintura, o histórico ainda assim fica correto.
            self.chat_stream.outcomes.record(
                entry.ui_key, ActionOutcome(success=ok, message=message, undone=ok)
            )
        self.show_toast(message)

    def _on_undo_shortcut(self, *_args) -> bool:
        """`Ctrl+Z` desfaz a ação — mas não rouba o undo de texto do campo.

        Devolve False com o foco num campo editável, deixando o evento seguir
        para o widget (mesma regra do Ctrl+K).
        """
        focus = self.get_focus()
        if isinstance(focus, (Gtk.Editable, Gtk.TextView)):
            return False
        self.undo_last_action()
        return True

    # ------------------------------------------------------------------
    # Exportação da conversa (Markdown)
    # ------------------------------------------------------------------
    def _export_metadata(self) -> tuple[str, str]:
        """(provedor, modelo) ativos, para o frontmatter do arquivo exportado."""
        config = self.config
        provider = getattr(config, "provider", "") or ""
        model = {
            "gemini": getattr(config, "gemini_model", ""),
            "ollama": getattr(config, "ollama_model", ""),
            "openai": getattr(config, "openai_model", ""),
        }.get(provider, "")
        return provider, (model or "")

    def build_conversation_markdown(self) -> str:
        """Texto Markdown da conversa atual ("" se não houver nada a exportar)."""
        provider, model = self._export_metadata()
        return render_conversation_markdown(
            self.session, provider=provider, model=model
        )

    def export_conversation(self, *_args) -> None:
        """Abre o diálogo "salvar como" e grava a conversa em `.md`.

        O `Gtk.FileDialog` é assíncrono e não bloqueia a interface — diferente do
        antigo `Gtk.FileChooserDialog`, que travava o loop principal.
        """
        if not exportable_turns(self.session):
            self.show_toast("Nada para exportar: a conversa está vazia.")
            return

        dialog = Gtk.FileDialog()
        dialog.set_title("Exportar conversa como Markdown")
        dialog.set_initial_name(suggest_filename(self.session))

        md_filter = Gtk.FileFilter()
        md_filter.set_name("Markdown (*.md)")
        md_filter.add_pattern("*.md")
        dialog.set_default_filter(md_filter)

        dialog.save(self, None, self._on_export_target_chosen, None)

    def _on_export_target_chosen(
        self, dialog: Gtk.FileDialog, result: Gio.AsyncResult
    ) -> None:
        """Conclusão do diálogo de salvar: escreve o arquivo escolhido."""
        try:
            target = dialog.save_finish(result)
        except GLib.Error as err:
            if _is_dialog_dismissed(err):
                return
            logger.warning("Falha ao escolher destino da exportação: %s", err)
            self.show_toast("Não foi possível escolher onde salvar o arquivo.")
            return

        if target is None:
            return

        markdown = self.build_conversation_markdown()
        if not markdown:
            self.show_toast("Nada para exportar: a conversa está vazia.")
            return

        path = target.get_path()
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(markdown)
        except OSError as err:
            logger.warning("Falha ao gravar a exportação em %s: %s", path, err)
            self.show_toast("Não foi possível gravar o arquivo.")
            return

        self.show_toast(f"Conversa exportada para {os.path.basename(path)}")

    # ------------------------------------------------------------------
    # Anexos (arrastar e soltar)
    # ------------------------------------------------------------------
    def attach_files(self, paths: list[str]) -> None:
        """Anexa arquivos soltos na janela.

        Imagens vão para o canal multimodal (há um slot só, como na captura de
        tela); texto e PDF entram como contexto do prompt, com chip removível.
        """
        from dataclasses import replace as _replace

        resolved = load_attachments(paths)
        images = [a for a in resolved if a.ok and a.kind is AttachmentKind.IMAGE]
        docs = [a for a in resolved if a.ok and a.kind in (AttachmentKind.TEXT, AttachmentKind.PDF)]
        rejected = [a for a in resolved if not a.ok]

        # Soltar o mesmo arquivo duas vezes duplicaria o bloco de contexto — e o
        # usuário não teria como saber. Chips repetidos são o bug, não o recurso.
        known = {os.path.realpath(a.path) for a in self.attachments}
        fresh: list[Attachment] = []
        for att in docs:
            key = os.path.realpath(att.path)
            if key in known:
                rejected.append(_replace(att, error="já anexado"))
                continue
            known.add(key)
            fresh.append(att)

        if fresh:
            self.attachments.extend(fresh)

        image = images[0] if images else None
        if image is not None:
            self._attach_image_file(image)
            for extra in images[1:]:
                rejected.append(_replace(extra, error="só uma imagem por vez"))

        self.attachment_bar.refresh()
        self.show_toast(self._describe_attachments(fresh, image, rejected))

    def _attach_image_file(self, att: Attachment) -> None:
        """Coloca a imagem solta no mesmo slot multimodal da captura de tela."""
        if not att.data:
            return
        self._active_image_bytes = att.data
        self._active_image_is_area = False
        self._active_image_is_clipboard = False
        self.vision.render_thumbnail(
            att.data,
            is_area=False,
            is_clipboard=False,
            label=f"\U0001f5bc️ Imagem anexada: {att.name}",
        )
        self.entry.set_placeholder_text(
            "Faça uma pergunta sobre esta imagem ou pressione Enter..."
        )

    @staticmethod
    def _describe_attachments(
        docs: list[Attachment], image: Attachment | None, rejected: list[Attachment]
    ) -> str:
        """Frase do toast. Nomeia o primeiro problema: erro genérico não ajuda."""
        parts = []
        if image is not None:
            parts.append(f"imagem {image.name} anexada")
        if docs:
            n = len(docs)
            parts.append(f"{n} arquivo{'s' if n > 1 else ''} de texto anexado{'s' if n > 1 else ''}")
        if rejected:
            n = len(rejected)
            first = rejected[0]
            detail = f" ({first.error})" if n == 1 else ""
            parts.append(f"{n} ignorado{'s' if n > 1 else ''}: {first.name}{detail}")
        if not parts:
            return "Nada para anexar."
        return " · ".join(parts)

    def clear_attachments(self, *_args) -> None:
        """Remove todos os anexos de texto/PDF (não mexe na imagem ativa)."""
        if not self.attachments:
            self.show_toast("Nenhum anexo para remover.")
            return
        count = len(self.attachments)
        self.attachments.clear()
        self.attachment_bar.refresh()
        self.show_toast(f"{count} anexo{'s' if count > 1 else ''} removido{'s' if count > 1 else ''}.")

    # ------------------------------------------------------------------
    # Sessão e histórico
    # ------------------------------------------------------------------
    def _save_current_session(self) -> None:
        """Persiste a sessão atual no SQLite (auto-save estilo Gemini)."""
        if not self.session.turns:
            return
        self.engine.memory.save_chat_topic(
            topic_id=self.session.id,
            title=self.session.title
            or (self.session.turns[0].prompt[:50] if self.session.turns else "Nova Conversa"),
            turns=[t.to_dict() for t in self.session.turns],
            is_pinned=True,
            created_at=self.session.created_at,
        )

    def _resume_topic(self, topic_id: str) -> None:
        """Carrega e retoma uma conversa histórica com todo o seu fluxo de mensagens."""
        # Frente B / Defeito 2: retomar outra conversa cancela o agente em execução.
        self.abort_active_agent()  # transicao: retomar conversa cancela o agente
        self._teardown_active_agent_ui()  # transicao: retomar conversa libera a UI
        if self.session.turns:
            self._save_current_session()

        topic_data = self.engine.memory.get_chat_topic(topic_id)
        if not topic_data:
            return

        self.session.load_from_dict(topic_data)
        self.window_title.set_subtitle(self.session.title or "Conversa retomada")
        self.chat_stream.rebuild()
        self.sidebar.populate(filter_query=self.sidebar.search.get_text().strip())
        self.entry.grab_focus()
        self.show_toast(f'Conversa "{self.session.title}" aberta!')

    def _update_pin_ui(self) -> None:
        """Atualiza estado visual da fixação de tópicos."""

    def _on_toggle_pin(self, _btn: Gtk.Button | None = None) -> None:
        """Alterna a fixação da conversa."""
        self.session.toggle_pin()
        if self.session.turns:
            self._save_current_session()
        self.sidebar.populate(filter_query=self.sidebar.search.get_text().strip())
        self.show_toast("Conversa fixada no topo.")

    def _on_new_topic(self, _btn: Gtk.Button | None = None) -> None:
        """Inicia uma nova conversa limpa (Ctrl+N / Estilo Gemini)."""
        # Frente B / Defeito 2: trocar de conversa cancela o agente em execução.
        self.abort_active_agent()  # transicao: nova conversa cancela o agente
        self._teardown_active_agent_ui()  # transicao: nova conversa libera a UI
        if self.session.turns:
            self._save_current_session()
        self.session.reset_new()
        self.vision.clear()
        self.window_title.set_subtitle("Assistente Inteligente")
        self.chat_stream.rebuild()
        self.sidebar.populate(filter_query=self.sidebar.search.get_text().strip())
        self.entry.set_text("")
        self.entry.grab_focus()
        self.show_toast("Nova conversa iniciada")

    def _build_history_popover(self) -> None:
        """Método de compatibilidade para histórico."""

    def _populate_history_list(self) -> None:
        """Método de compatibilidade."""
        self.sidebar.populate()

    # ------------------------------------------------------------------
    # Voz ao vivo (Gemini Live)
    # ------------------------------------------------------------------
    def show_live_voice_pill(self) -> bool:
        """Reexibe a pílula de uma sessão de voz que segue viva, mas oculta.

        Par de "ocultar sem encerrar": a sessão continua conectada em segundo
        plano e o atalho global precisa **trazer a pílula de volta** em vez de
        encerrar a conversa. Sem isto, ocultar era uma porta de mão única — só
        restava encerrar e começar de novo.

        Devolve False quando não há pílula ligada ao cliente atual (ex.: a sessão
        nasceu como HUD no modo "full"); aí o chamador segue com o toggle normal.
        """
        pill = self.voice_pill_window
        if pill is None or pill.live_client is not self.live_client:
            return False
        pill.present()
        pill.place_smart()
        # Reaplica o último estado conhecido: enquanto esteve oculta a pílula não
        # recebeu frames, e o texto/flag de conexão podem estar defasados.
        try:
            pill._ui_on_state_change(pill._current_state, "")
        except Exception as exc:
            logger.debug("Falha ao reaplicar estado da pílula: %s", exc)
        return True

    def toggle_live_voice(self, as_pill: bool = False) -> None:
        """Alterna a ativação do modo de conversa de voz ao vivo (Gemini Live)."""
        if (self.live_client and self.live_client.is_active()) or (
            self.voice_pill_window and self.voice_pill_window.get_visible()
        ):
            self.stop_live_voice()
        else:
            self.start_live_voice(as_pill=as_pill)

    def _last_session_solution(self) -> Optional["SolutionGraph"]:
        """A última solução matemática da conversa (para o comando de voz sem problema)."""
        for turn in reversed(getattr(self.session, "turns", []) or []):
            sol = getattr(turn, "solution", None)
            if sol is not None:
                return sol
        return None

    def show_blackboard(self, graph: Optional["SolutionGraph"] = None, focus: str = "") -> None:
        """Abre (ou reusa) o Quadro Negro Digital com um SolutionGraph.

        Costura de integração para o chat e a voz ao vivo: quando o math_solver
        produz um grafo estruturado (resposta de matemática), o turno de chat ou o
        comando de voz chama este método para exibir o raciocínio no quadro.

        Sem grafo, reusa a última solução da conversa e, na falta dela, o exemplo
        canônico. Se já há um quadro aberto, ele é reaproveitado: recarrega o grafo
        e aplica o `focus` — evita abrir uma janela nova a cada comando de voz.
        `focus` destaca um nó (nome de método, 'enunciado' ou 'passo N').
        """
        from ..ai.math_solver import build_quadratic_sample
        from .widgets.blackboard import BlackboardWidget

        widget = getattr(self, "_blackboard_widget", None)
        win = getattr(widget, "_host_window", None) if widget is not None else None
        if widget is not None and win is not None:
            if graph is not None:
                widget.load_solution(graph)
            if focus:
                widget.highlight(focus)
            try:
                win.present()
            except Exception:  # janela pode ter sido destruída
                self._blackboard_widget = None
            return

        if graph is None:
            graph = self._last_session_solution() or build_quadratic_sample()
        self._blackboard_widget = BlackboardWidget.show_dialog(self, graph, focus=focus)

    def _board_route(
        self,
        problem: str,
        graph: Optional["SolutionGraph"],
    ) -> tuple[str, Optional["SolutionGraph"], object]:
        """Decide o caminho do quadro **sem tocar na UI**.

        Existe separado de `show_blackboard_for_problem` porque o gancho das
        ferramentas roda na thread do executor e precisa conhecer o desfecho
        **antes** de remeter o desenho para a main thread. Sem essa separação só
        restava afirmar "aberto" sem ter como verificar — que era o defeito.

        `try_solve` é puro e `is_configured()` apenas lê configuração, então a
        decisão é segura fora da main thread.
        """
        from ..ai.math_solver import try_solve

        if graph is None and problem:
            graph = try_solve(problem)

        provider = getattr(getattr(self, "engine", None), "llm_provider", None)
        configured = getattr(provider, "is_configured", None)
        provider_ready = provider is not None and callable(configured) and bool(configured())

        status = decide_path(
            has_graph=graph is not None,
            has_problem=bool(problem),
            provider_ready=provider_ready,
        )
        return status, graph, provider

    def _run_board_route(
        self,
        status: str,
        problem: str,
        graph: Optional["SolutionGraph"],
        provider: object,
        focus: str,
    ) -> None:
        """(main thread) Executa a rota já decidida. Nunca levanta."""
        from ..ai.math_solver import solve_with_provider

        if status == BOARD_OK:
            self.show_blackboard(graph, focus=focus)
            return

        self.show_toast(message_for(status))
        if status != BOARD_PENDING:
            return

        def _work() -> None:
            result = None
            try:
                result = solve_with_provider(problem, provider)
            except Exception:  # noqa: BLE001
                logger.debug("solve_with_provider falhou", exc_info=True)
            GLib.idle_add(lambda: self._after_model_solution(result, focus))

        threading.Thread(target=_work, daemon=True).start()

    def show_blackboard_for_problem(
        self, problem: str, focus: str = "", precomputed: Optional["SolutionGraph"] = None
    ) -> BoardOutcome:
        """Abre o Quadro Negro para `problem` e devolve o **desfecho**.

        Caminho: álgebra pura (offline) → provedor LLM (model-backed, em thread).
        Sem `problem`, mostra a última solução da conversa. A resolução
        model-backed roda fora da main thread para não travar a UI; o resultado
        volta pela main thread e abre o quadro — por isso o desfecho devolvido é
        `pending` nesse caso, e **não** `ok`: o quadro ainda não existe.
        """
        problem = (problem or "").strip()
        status, graph, provider = self._board_route(problem, precomputed)
        self._run_board_route(status, problem, graph, provider, focus)
        return BoardOutcome(status=status, message=message_for(status, focus))

    def _open_blackboard_for_tool(
        self,
        problem: str = "",
        graph: Optional["SolutionGraph"] = None,
        focus: str = "",
    ) -> dict:
        """Gancho injetado no ToolRegistry (voz/agente): abre o Quadro Negro.

        Roda na thread do executor de ferramentas; o desenho é remetido para a
        main thread via `GLib.idle_add` (thread-safe). Devolve o **desfecho**, e
        não uma mensagem otimista: sem isso a voz falava "Quadro Negro aberto"
        mesmo quando nada abria (sem provedor de IA, por exemplo) e o log da
        sessão gravava `success: True` para uma ação que não aconteceu.

        `graph` já vem resolvido (álgebra pura) quando possível; sem ele,
        `problem` é resolvido sob demanda (model-backed).
        """
        focus = (focus or "").strip()
        problem = (problem or "").strip()
        status, resolved, provider = self._board_route(problem, graph)

        def _apply() -> bool:
            self._run_board_route(status, problem, resolved, provider, focus)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_apply)
        return BoardOutcome(status=status, message=message_for(status, focus)).to_dict()

    def _after_model_solution(self, graph: Optional["SolutionGraph"], focus: str) -> bool:
        """(main thread) Abre o quadro com o grafo do modelo, ou avisa a falha."""
        if graph is not None:
            self.show_blackboard(graph, focus=focus)
        else:
            self.show_toast("Não consegui montar o quadro para este problema.")
        return GLib.SOURCE_REMOVE

    def start_live_voice(self, initial_command: str = "", as_pill: bool = False) -> None:
        """Inicia o chat de voz ao vivo com o Gemini Live ou Voz Local Soberana.

        ``initial_command`` (Fase 4A): comando capturado junto à wake word,
        enviado como primeiro turno de texto assim que a sessão conectar.
        ``as_pill``: exibe em janela pílula estilo Dynamic Island em vez do HUD completo.
        """
        prev_client = self.live_client
        self._live_voice_as_pill = as_pill
        use_local = (
            self.config.provider in ("local", "ollama", "hybrid")
            or not self.config.gemini_api_key.strip()
        )

        if use_local:
            try:
                self.live_client = LocalLiveVoiceClient(
                    config=self.config,
                    executor=self.executor,
                    memory=self.engine.memory,
                )
                toast_msg = "Voz Local iniciada. Pode falar..."
            except Exception as exc:
                if not self.config.gemini_api_key.strip():
                    self.show_toast(f"Falha ao iniciar voz local: {exc}")
                    return
                use_local = False

        if not use_local:
            if not self.config.gemini_api_key.strip():
                self.show_toast("Chave de API do Google Gemini necessária para voz ao vivo. Configure nas Preferências.")
                self._open_settings()
                return

            if not self.live_client or not isinstance(self.live_client, GeminiLiveClient):
                self.live_client = GeminiLiveClient(
                    config=self.config,
                    executor=self.executor,
                    memory=self.engine.memory,
                    mcp_manager=getattr(self, "mcp_manager", None),
                    risk_policy=self.risk_policy,
                )
            toast_msg = "Conversa ao vivo iniciada. Pode falar..."

        # Comando de voz "abre o quadro negro": injeta o abridor de UI nos DOIS
        # caminhos de voz. No Gemini Live ele vai para o registro de ferramentas
        # (que a sessão declara ao modelo); na voz local, para o gancho direto do
        # cliente — que não tem `ToolRegistry` (só `ActionPlan`/`ActionType`) e por
        # isso não teria como abrir o quadro de outro jeito.
        registry = getattr(self.live_client, "tool_registry", None)
        if registry is not None:
            registry.board_opener = self._open_blackboard_for_tool
        if hasattr(self.live_client, "board_opener"):
            self.live_client.board_opener = self._open_blackboard_for_tool

        # Trocamos de cliente (ex.: local → novo local, ou local → Gemini): encerra
        # o anterior para não deixar sessão, threads de captura ou callbacks
        # órfãos rodando em paralelo com a nova sessão.
        if prev_client is not None and prev_client is not self.live_client:
            try:
                stop = getattr(prev_client, "stop", None)
                if callable(stop):
                    stop()
            except Exception as exc:
                logger.debug("Falha ao encerrar cliente de voz anterior: %s", exc)

        if hasattr(self.live_client, "tool_registry") and self.live_client.tool_registry:
            mcp_mgr = getattr(self, "mcp_manager", None)
            if mcp_mgr:
                self.live_client.tool_registry.mcp_manager = mcp_mgr
                self.live_client.tool_registry.load_mcp_tools(mcp_mgr)
        if hasattr(self.live_client, "rag"):
            self.live_client.rag = self.rag
        if hasattr(self.live_client, "fence"):
            self.live_client.fence = self.fence
        if hasattr(self.live_client, "input_driver") and self.live_client.input_driver:
            self.live_client.input_driver.fence = self.fence
        if initial_command and hasattr(self.live_client, "queue_initial_text"):
            self.live_client.queue_initial_text(initial_command)

        def _on_live_error(err: str) -> None:
            GLib.idle_add(lambda: self.show_toast(f"Chamada de voz: {err}"))

        self.live_client.on_error = _on_live_error
        # Encerramento autônomo: o agente pode pedir para encerrar a sessão
        # (standby) ou o aplicativo (quit) quando o usuário se despede.
        if hasattr(self.live_client, "on_end_session"):
            self.live_client.on_end_session = self._on_end_session_requested
        self._cancel_pending_end_session()

        if as_pill:
            if not self.voice_pill_window:
                self.voice_pill_window = VoicePillWindow(
                    application=self.get_application(),
                    live_client=self.live_client,
                    on_expand=self._expand_from_pill,
                    on_close=self.stop_live_voice,
                    on_hide=self._on_pill_hidden,
                )
            else:
                # A pílula é reaproveitada entre sessões. Se o cliente mudou,
                # encerra o antigo antes de reconectar os eventos — senão a
                # sessão anterior seguia viva consumindo microfone.
                old_pill_client = self.voice_pill_window.live_client
                if old_pill_client is not None and old_pill_client is not self.live_client:
                    try:
                        stop = getattr(old_pill_client, "stop", None)
                        if callable(stop):
                            stop()
                    except Exception as exc:
                        logger.debug("Falha ao encerrar cliente antigo da pílula: %s", exc)
                self.voice_pill_window.live_client = self.live_client
                self.voice_pill_window._connect_client_events()

            # Mostra a pílula imediatamente, antes de o WebSocket conectar:
            # a espera silenciosa parecia "não me ouviu".
            self.voice_pill_window.present()
            self.voice_pill_window.place_smart()
            self.voice_pill_window.show_preparing()
            self.live_client.start()
            if self.wake_word_engine is not None:
                self.wake_word_engine.pause()
            self.show_toast(toast_msg)
            return

        if self.voice_pill_window:
            self.voice_pill_window.save_geometry()
            self.voice_pill_window.set_visible(False)

        self.live_voice_widget = LiveVoiceWidget(
            live_client=self.live_client,
            on_close=self.stop_live_voice,
        )
        self.live_voice_revealer.set_child(self.live_voice_widget)
        self.live_voice_revealer.set_reveal_child(True)
        self._collapse_sidebar_for_live()
        self.header.voice_call_btn.add_css_class("suggested-action")
        self.prompt_bar.bottom_voice_btn.add_css_class("suggested-action")
        self.chat_stream.welcome_box.set_visible(False)
        self.live_client.start()
        # O microfone passa a ser do Gemini Live (pw-record): pausa a wake word
        # para evitar conflito de captura e detecções espúrias da própria voz da IA.
        if self.wake_word_engine is not None:
            self.wake_word_engine.pause()
        self.show_toast(toast_msg)

    def _expand_from_pill(self) -> None:
        """Expande a conversa da pílula flutuante para a janela completa do Copilot."""
        self._live_voice_as_pill = False
        if self.voice_pill_window:
            self.voice_pill_window.save_geometry()
            self.voice_pill_window.set_visible(False)
        self.summon_hud()
        if not self.live_voice_widget:
            self.live_voice_widget = LiveVoiceWidget(
                live_client=self.live_client,
                on_close=self.stop_live_voice,
            )
            self.live_voice_revealer.set_child(self.live_voice_widget)
        else:
            self.live_voice_widget.live_client = self.live_client
            self.live_voice_widget._connect_client_events()

        self.live_voice_revealer.set_reveal_child(True)
        self._collapse_sidebar_for_live()
        self.header.voice_call_btn.add_css_class("suggested-action")
        self.prompt_bar.bottom_voice_btn.add_css_class("suggested-action")
        self.chat_stream.welcome_box.set_visible(False)

    def _on_pill_hidden(self) -> None:
        """A pílula foi ocultada **sem** encerrar: a sessão segue viva.

        Só avisa quando há onde mostrar o aviso — um toast na janela principal
        escondida seria feedback invisível. Quando não há, o próprio sumiço da
        pílula (com a sessão ainda ativa) já é o sinal.
        """
        if self.get_visible():
            self.show_toast("Sessão de voz continua ativa. Acione o atalho de voz para reabrir.")
        else:
            logger.info("Pílula ocultada; sessão de voz segue ativa em segundo plano.")

    def _consolidate_live_session(self) -> None:
        """Para o cliente e persiste o resumo da chamada no chat ativo.

        Extraído de `stop_live_voice` para que o encerramento autônomo
        (`end_session`) reaproveite exatamente este caminho. A ordem importa: o
        resumo TEM de ser lido antes do stop(), senão volta vazio.
        """
        summary = self.live_client.get_session_summary() if self.live_client else {}
        if self.live_client:
            self.live_client.stop()

        if summary.get("has_activity"):
            duration = summary.get("duration_sec", 0)
            actions = summary.get("actions_executed", [])
            lines = [f"**\U0001f399️ Sessão de Voz ao Vivo (Gemini Live)** • {duration}s de chamada\n"]
            if actions:
                lines.append("**Ações executadas no sistema:**")
                for act in actions:
                    t_name = act.get("tool", "")
                    t_args = act.get("args", {})
                    if t_name == "launch_app":
                        lines.append(f"- Abriu o aplicativo **{t_args.get('app_name', '')}**")
                    elif t_name == "system_control":
                        lines.append(
                            f"- Controle do sistema: **{t_args.get('action', '')}** ({t_args.get('value', '')})"
                        )
                    elif t_name == "open_url":
                        lines.append(f"- Abriu link: `{t_args.get('url', '')}`")
                    elif t_name == "capture_screen":
                        lines.append("- Capturou a tela para inspeção visual")
                    elif t_name == "web_search":
                        lines.append(f"- Pesquisa na web: *{t_args.get('query', '')}*")
                    elif t_name == "media_control":
                        lines.append(f"- Controle de mídia: **{t_args.get('action', '')}**")
                    elif t_name == "write_document":
                        _fn = (t_args.get("filename") or "").lower()
                        _label = (
                            "apresentação PowerPoint"
                            if _fn.endswith(".pptx")
                            else "documento Word"
                            if _fn.endswith(".docx")
                            else "documento"
                        )
                        lines.append(f"- Salvou {_label}: `{t_args.get('filename', '')}`")
                    elif t_name == "organize_directory":
                        lines.append(f"- Organizou pasta: `{t_args.get('directory', 'Downloads')}`")
                    else:
                        lines.append(f"- Executou ferramenta: `{t_name}`")
                lines.append("")
            else:
                lines.append("Conversa bidirecional em tempo real concluída.")

            if summary.get("video_streamed"):
                frames = summary.get("video_frames", 0)
                lines.append(
                    f"**Vídeo ao vivo:** Compartilhamento de tela contínuo ativo "
                    f"({frames} frames analisados).\n"
                )

            prompt_label = (
                "Chamada de Voz e Tela ao Vivo"
                if summary.get("video_streamed")
                else "Conversa de Voz ao Vivo"
            )
            turn = self.session.record_turn(prompt=prompt_label, answer="\n".join(lines).strip())
            self._save_current_session()
            self._update_pin_ui()

            self.chat_stream.welcome_box.set_visible(False)
            self.chat_stream.stream_box.append(
                self.chat_stream.create_turn_widget(turn)
            )
            self.sidebar.populate()
            self.chat_stream.scroll_to_bottom()
        else:
            if not self.session.turns:
                self.chat_stream.welcome_box.set_visible(True)

    def _resume_wake_word_after_speech(self) -> None:
        """Volta a ouvir a palavra de ativação depois do anti-eco.

        Sem essa surdez temporária, a própria despedida falada pela IA era
        transcrita de volta e reativava a wake word — um loop de auto-chamada.
        """
        if self.wake_word_engine is None:
            return
        delay = float(getattr(self.config, "wake_word_echo_delay_sec", 1.2) or 0.0)
        suspend = getattr(self.wake_word_engine, "suspend", None)
        if callable(suspend):
            suspend(delay)
        else:
            self.wake_word_engine.resume()

    def _collapse_sidebar_for_live(self) -> None:
        """Recolhe a barra lateral enquanto a voz ao vivo está em cena.

        Em voz, o foco é a onda e o histórico da chamada — não a lista de
        conversas, que ocupa cerca de um quarto da largura. O estado anterior é
        guardado para ser devolvido no fim: quem já tinha recolhido a barra por
        conta própria não pode vê-la **abrir sozinha** ao encerrar a chamada.
        """
        if self._sidebar_revealed_before_live is None:
            self._sidebar_revealed_before_live = self.sidebar.is_revealed
        self.sidebar.revealer.set_reveal_child(False)

    def _restore_sidebar_after_live(self) -> None:
        """Devolve a barra lateral ao estado que ela tinha antes da chamada."""
        previous = self._sidebar_revealed_before_live
        self._sidebar_revealed_before_live = None
        if previous:
            self.sidebar.revealer.set_reveal_child(True)

    def stop_live_voice(self) -> None:
        """Encerra a chamada de voz ao vivo e consolida a interação no chat ativo."""
        was_pill = self._live_voice_as_pill or (
            self.voice_pill_window is not None and self.voice_pill_window.get_visible()
        )
        self._live_voice_as_pill = False
        if self.voice_pill_window:
            self.voice_pill_window.save_geometry()
            self.voice_pill_window.set_visible(False)
        self._consolidate_live_session()
        self._resume_wake_word_after_speech()
        self.live_voice_revealer.set_reveal_child(False)
        self._restore_sidebar_after_live()
        self.header.voice_call_btn.remove_css_class("suggested-action")
        self.prompt_bar.bottom_voice_btn.remove_css_class("suggested-action")

        self.entry.grab_focus()
        self.show_toast("Conversa de voz encerrada.")
        if was_pill:
            return
        if self.get_visible() and self.is_active():
            self.set_visible(False)
        else:
            self.summon_hud()

    # ------------------------------------------------------------------
    # Encerramento autônomo solicitado pelo agente (ferramenta `end_session`)
    # ------------------------------------------------------------------
    def _on_end_session_requested(self, mode: str, reason: str = "") -> None:
        """Chamado na THREAD do executor de tools — só faz marshal p/ main thread."""
        GLib.idle_add(self._schedule_end_session, mode, reason)

    def _schedule_end_session(self, mode: str, reason: str = "") -> bool:
        """(main thread) Agenda o encerramento depois do tempo da despedida.

        O grace period existe para o modelo conseguir FALAR a despedida: o
        resultado da ferramenta volta no mesmo WebSocket, o Gemini gera o áudio
        e ele é reproduzido — encerrar na hora cortaria a fala no meio.
        """
        self._cancel_pending_end_session()
        try:
            grace = float(getattr(self.config, "end_session_grace_sec", 4.0) or 0.0)
        except (TypeError, ValueError):
            grace = 4.0
        grace = max(0.0, min(15.0, grace))
        logger.info(
            "Agente pediu encerramento mode=%s motivo=%r (despedida de %.1fs).", mode, reason, grace
        )
        self._pending_end_mode = mode
        self._end_session_source_id = GLib.timeout_add(
            int(grace * 1000), self._perform_end_session, mode
        )
        return GLib.SOURCE_REMOVE

    def _cancel_pending_end_session(self) -> None:
        """Cancela um encerramento agendado (ex.: usuário voltou a chamar)."""
        source = getattr(self, "_end_session_source_id", 0)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
        self._end_session_source_id = 0
        self._pending_end_mode = None

    def _perform_end_session(self, mode: str) -> bool:
        """(main thread) Executa o encerramento: standby ou saída do aplicativo.

        Não chama `stop_live_voice()` de propósito: ela termina com `summon_hud()`
        e reabriria a janela do Copilot justamente na hora de encerrar.
        """
        self._end_session_source_id = 0
        self._pending_end_mode = None

        self._consolidate_live_session()
        if self.voice_pill_window:
            self.voice_pill_window.save_geometry()
            self.voice_pill_window.set_visible(False)
        self.live_voice_revealer.set_reveal_child(False)
        self._restore_sidebar_after_live()
        self.header.voice_call_btn.remove_css_class("suggested-action")
        self.prompt_bar.bottom_voice_btn.remove_css_class("suggested-action")

        if mode == "quit":
            if self.wake_word_engine is not None:
                self.wake_word_engine.stop()
            # Sem toast aqui: o processo morre antes de renderizar.
            logger.info("Encerrando o aplicativo a pedido do agente.")
            self._quit_application()
            return GLib.SOURCE_REMOVE

        # standby: libera o microfone e fica em espera ouvindo a palavra.
        self._resume_wake_word_after_speech()
        self.show_toast("\U0001f44b Até logo! Continuo te ouvindo — é só me chamar.")
        if self.get_visible() and self.is_active():
            self.set_visible(False)
        return GLib.SOURCE_REMOVE

    def trigger_direct_crop(self) -> None:
        """Dispara o recorte de área da tela a partir do atalho global (Super+Shift+S)."""
        if self.get_visible():
            self.set_visible(False)
        self.vision.start_capture(interactive=True, direct_mode=True)

    # ------------------------------------------------------------------
    # Ditado Global / Voice Typing no aplicativo em foco
    # ------------------------------------------------------------------
    def toggle_dictation(self) -> None:
        """Alterna a ativação do Ditado Global no aplicativo em foco."""
        if self.dictation_service and self.dictation_service.is_active():
            self.stop_dictation()
        else:
            self.start_dictation()

    def start_dictation(self) -> None:
        """Inicia o processo de escuta e ditado para o aplicativo ativo."""
        from ..core.dictation import DictationService, DictationState
        from .dictation_osd import DictationOSDWindow

        if not self.dictation_service:
            self.dictation_service = DictationService(config=self.config)

        # Se a janela principal do Copilot estiver visível e com foco, digita direto no prompt
        if self.get_visible() and self.is_active():
            def _inject_to_entry(text: str) -> bool:
                GLib.idle_add(lambda: self._insert_text_to_entry(text))
                return True
            self.dictation_service.custom_text_injector = _inject_to_entry
        else:
            self.dictation_service.custom_text_injector = None

        if self.dictation_osd_window:
            try:
                self.dictation_osd_window.close_osd()
            except Exception:
                pass
            self.dictation_osd_window = None

        osd = DictationOSDWindow(
            application=self.get_application(),
            on_cancel=self.stop_dictation,
        )
        self.dictation_osd_window = osd

        def _on_state_change(state: DictationState, msg: str) -> None:
            GLib.idle_add(lambda: osd.update_state(state, msg) if osd else None)
            if state in (DictationState.DONE, DictationState.ERROR, DictationState.IDLE):
                if self.wake_word_engine is not None:
                    self.wake_word_engine.resume()

        def _on_audio_level(level: float) -> None:
            GLib.idle_add(lambda: osd.update_audio_level(level) if osd else None)

        self.dictation_service.on_state_change = _on_state_change
        self.dictation_service.on_audio_level = _on_audio_level

        if self.wake_word_engine is not None:
            self.wake_word_engine.pause()

        osd.present()
        started = self.dictation_service.start()
        if not started:
            osd.update_state(DictationState.ERROR, "Microfone indisponível")
            if self.wake_word_engine is not None:
                self.wake_word_engine.resume()

    def stop_dictation(self) -> None:
        """Interrompe o ditado atual."""
        if self.dictation_service:
            self.dictation_service.stop(cancel=True)
        if self.dictation_osd_window:
            try:
                self.dictation_osd_window.close_osd()
            except Exception:
                pass
            self.dictation_osd_window = None
        if self.wake_word_engine is not None:
            self.wake_word_engine.resume()

    def _insert_text_to_entry(self, text: str) -> None:
        """Insere o texto transcrito no campo de prompt do Copilot."""
        if hasattr(self, "entry") and self.entry:
            current = self.entry.get_text()
            if current:
                self.entry.set_text(f"{current} {text.strip()}")
            else:
                self.entry.set_text(text.strip())
            self.entry.grab_focus()



class ZorinCopilotApp(Adw.Application):
    """Aplicação Zorin Copilot com suporte a comando de linha, modo HUD e atalhos globais."""

    #: Ícone de bandeja, quando o modo --background consegue publicar um.
    _tray: object | None = None

    def __init__(self):
        super().__init__(
            application_id=__app_id__,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.add_main_option(
            "toggle", ord("t"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Alterna a visibilidade do HUD do Copilot", None,
        )
        self.add_main_option(
            "crop", ord("c"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Dispara a seleção interativa de área da tela e analisa com IA", None,
        )
        self.add_main_option(
            "voice", ord("v"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Inicia imediatamente a conversa de voz ao vivo (Gemini Live)", None,
        )
        self.add_main_option(
            "live-voice", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Alias para --voice (conversa de voz ao vivo)", None,
        )
        self.add_main_option(
            "voice-pill", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Abre a pílula de voz flutuante (ignora o modo de exibição configurado)", None,
        )
        self.add_main_option(
            "dictate", ord("d"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Inicia o ditado por voz contínuo no aplicativo atualmente em foco", None,
        )
        self.add_main_option(
            "background", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Inicia o Copilot silenciosamente em segundo plano (autostart)", None,
        )
        self.add_main_option(
            "announce", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
            "Fala um texto no Copilot já em execução (usado pela ponte HA→desktop). "
            "Sai com 3 se não houver instância rodando, sem abrir a janela.",
            "TEXTO",
        )

    def do_startup(self):
        Adw.Application.do_startup(self)
        # Atalhos de sistema: GNOME media-keys no Zorin/GNOME; portal GlobalShortcuts
        # nos demais compositores (Hyprland/KDE/dde) — agnóstico de distribuição.
        try:
            cfg = CopilotConfig.load()
            if is_gnome_desktop():
                if cfg.global_shortcut_enabled:
                    ShortcutManager.register(cfg.global_shortcut_key)
                if getattr(cfg, "crop_shortcut_enabled", True):
                    ShortcutManager.register_crop(getattr(cfg, "crop_shortcut_key", "<Super><Shift>s"))
                if getattr(cfg, "live_voice_hotkey_enabled", True):
                    ShortcutManager.register_voice(getattr(cfg, "live_voice_hotkey", "<Super>v"))
                if getattr(cfg, "voice_pill_shortcut_enabled", False):
                    ShortcutManager.register_voice_pill(
                        getattr(cfg, "voice_pill_shortcut_key", "<Super><Shift>p")
                    )
                if getattr(cfg, "dictate_shortcut_enabled", True):
                    ShortcutManager.register_dictate(getattr(cfg, "dictate_shortcut_key", "<Super><Shift>d"))
            else:
                try:
                    from ..core.shortcuts_portal import PortalShortcutManager, portal_shortcut_list

                    self._portal_shortcuts = PortalShortcutManager(self._on_portal_shortcut)
                    if self._portal_shortcuts.start(portal_shortcut_list(cfg)):
                        logger.info("Atalhos globais via GlobalShortcuts portal ativos.")
                except Exception as exc:
                    logger.warning(f"Falha ao iniciar atalhos via portal GlobalShortcuts: {exc}")
        except Exception:
            pass

        # Casa Inteligente: se o usuário habilitou eventos em tempo real, sobe o
        # canal WebSocket no arranque. Fica atrás de uma flag explícita porque
        # abre uma conexão persistente — nunca é ligado "de surpresa". Falhar
        # aqui não pode impedir o app de abrir: é recurso, não requisito.
        try:
            cfg = CopilotConfig.load()
            if getattr(cfg, "ha_enabled", False) and getattr(cfg, "ha_realtime_enabled", False):
                from ..core.home_assistant import HomeAssistantManager

                ha = HomeAssistantManager.get_default()
                if ha.is_configured():
                    if ha.start_realtime():
                        logger.info("Eventos do Home Assistant em tempo real ativos.")
                    else:
                        logger.info("Home Assistant em tempo real não iniciou (verifique URL/token).")
        except Exception as exc:
            logger.debug(f"Casa inteligente em tempo real não iniciada no arranque: {exc}")

    def _get_or_create_window(self) -> CopilotWindow:
        for win in self.get_windows():
            if isinstance(win, CopilotWindow):
                if win.get_content() is not None:
                    return win
                try:
                    win.destroy()
                except Exception:
                    pass
        return CopilotWindow(self)

    def _setup_background_tray(self, win: CopilotWindow) -> None:
        """Publica o ícone de bandeja no modo --background.

        Em compositores sem atalho global (Hyprland/Sway sem bind configurado) a
        bandeja é o único ponto de entrada, então falhar aqui significa deixar o
        usuário sem o app. Registramos o log e seguimos.
        """
        try:
            from .tray import SystemTrayIndicator

            # Os callbacks já são despachados na main loop pelo próprio
            # SystemTrayIndicator (_deferred), então passamos as referências puras.
            self._tray = SystemTrayIndicator(
                on_toggle_hud=win.toggle_hud,
                on_crop=win.trigger_direct_crop,
                on_preferences=win._open_settings,
                on_kill_switch=win.toggle_kill_switch,
                on_quit=self.quit,
            )
            if not self._tray.setup():
                logger.info("Bandeja indisponível neste ambiente; seguindo sem ícone.")
        except Exception as exc:
            logger.warning(f"Falha ao iniciar a bandeja em segundo plano: {exc}")

    @staticmethod
    def _handle_voice_shortcut(win: "CopilotWindow", force_pill: bool = False) -> None:
        """Atalho de voz ao vivo: abre **só a pílula** (modo "pill") ou o HUD completo
        (modo "full"), sem puxar a GUI inteira por engano.

        Regra antiga (bug): no modo "pill" a pílula só abria quando a janela
        principal estava escondida (`not win.get_visible()`). Se o HUD já estivesse
        visível, o atalho caía no ramo `summon_hud()` e abria a GUI inteira em vez
        da pílula. Agora o modo "pill" sempre exibe a pílula isolada; só o modo
        "full" carrega o HUD.

        ``force_pill`` (atalho dedicado `--voice-pill` / slot "voice-pill"): abre a
        pílula **independentemente** de `voice_overlay_mode`. Sessões em estado
        terminal (erro/desconexão) contam como inativas: o acionamento reinicia
        limpo em vez de apenas esconder um estado morto.
        """
        live = win.live_client
        state = getattr(live, "state", None) if live is not None else None
        dead = state in (LiveVoiceState.ERROR, LiveVoiceState.DISCONNECTED)
        live_active = bool(live is not None and live.is_active() and not dead)
        pill_visible = bool(win.voice_pill_window and win.voice_pill_window.get_visible())

        # Sessão viva porém oculta ("ocultar sem encerrar"): o atalho traz a pílula
        # de volta. Antes caía no ramo de encerramento, o que tornava a ocultação
        # uma porta de mão única.
        if live_active and not pill_visible and win.show_live_voice_pill():
            return

        if live_active or pill_visible:
            # Encerra a sessão atual. Se ela estava *morta* (erro/desconexão) mas a
            # pílula ainda visível, reabre limpa em vez de só esconder um estado
            # morto; se estava viva, é um toggle-off normal.
            win.stop_live_voice()
            if dead and (force_pill or _overlay_is_pill(win.config)):
                win.toggle_live_voice(as_pill=True)
            return

        if force_pill or _overlay_is_pill(win.config):
            win.toggle_live_voice(as_pill=True)
        else:
            win.summon_hud()
            win.toggle_live_voice(as_pill=False)

    def _on_portal_shortcut(self, shortcut_id: str) -> None:
        """Recebe o id de atalho do portal GlobalShortcuts e age na janela."""
        win = self._get_or_create_window()
        if shortcut_id == "toggle-hud":
            win.toggle_hud()
        elif shortcut_id == "crop":
            win.trigger_direct_crop()
        elif shortcut_id == "voice":
            self._handle_voice_shortcut(win)
        elif shortcut_id == "voice-pill":
            # Atalho dedicado: sempre a pílula, mesmo com voice_overlay_mode="full".
            self._handle_voice_shortcut(win, force_pill=True)
        elif shortcut_id == "dictate":
            win.toggle_dictation()
        else:
            logger.debug(f"Atalho de portal desconhecido: {shortcut_id}")

    def do_shutdown(self):
        # Encerra wake word, servidores MCP, RAG e voz ao vivo junto com o processo.
        for win in self.get_windows():
            if isinstance(win, CopilotWindow):
                win.teardown_background_services()
                try:
                    hyprland_effects.remove_window_blur(__app_id__)
                except Exception:
                    pass
        Adw.Application.do_shutdown(self)

    def do_activate(self):
        win = self._get_or_create_window()
        win.summon_hud()

    @staticmethod
    def _announce_option(options: GLib.VariantDict, args: list[str]) -> str | None:
        """Extrai o texto de `--announce`, ou `None` se a opção não veio.

        A varredura de `args` não é redundante: o resto de `do_command_line` já
        precisa dela porque o dicionário de opções nem sempre é preenchido, e
        aceitar `--announce=texto` custa uma linha.
        """
        try:
            value = options.lookup_value("announce", None)
        except Exception:
            value = None
        if value is not None:
            try:
                return value.get_string()
            except Exception:
                pass
        for index, arg in enumerate(args):
            if arg == "--announce" and index + 1 < len(args):
                return args[index + 1]
            if arg.startswith("--announce="):
                return arg.split("=", 1)[1]
        return None

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        options = command_line.get_options_dict()
        is_background = options.contains("background")
        is_toggle = options.contains("toggle")
        is_crop = options.contains("crop")
        is_voice = options.contains("voice")
        is_voice_pill = options.contains("voice-pill")
        is_dictate = options.contains("dictate")
        args = command_line.get_arguments()
        if "--background" in args:
            is_background = True
        if "--toggle" in args or "-t" in args:
            is_toggle = True
        if "--crop" in args or "-c" in args or "--snippet" in args:
            is_crop = True
        if "--voice" in args or "-v" in args or "--live-voice" in args:
            is_voice = True
        if "--voice-pill" in args:
            is_voice_pill = True
        if "--dictate" in args or "-d" in args:
            is_dictate = True

        # `--announce` (ponte HA→desktop) tem de ser tratado **antes** de criar
        # janela. Se este processo virou a instância primária, é porque o app não
        # estava rodando — e abrir a interface inteira só para dizer uma frase
        # seria o oposto do que a ponte quer. Recusar e sair deixa quem chamou
        # decidir o que fazer (a ponte cai no `spd-say`).
        announce_text = self._announce_option(options, args)
        if announce_text is not None:
            is_remote = command_line.get_is_remote()
            command = parse_announce_command(announce_text, is_remote=is_remote)
            if not command.should_speak:
                logger.info(
                    "Pedido de anúncio não atendido (%s).", command.action.value
                )
                if not is_remote:
                    # Ninguém para falar: encerra em vez de virar uma janela.
                    self.quit()
                # Dois canais, e os dois são necessários: `set_exit_status` é o
                # que vale quando este processo **é** a instância primária (foi
                # ele que iniciou o app), e o retorno é o que o processo
                # secundário recebe quando encaminhou a linha de comando. Medido:
                # só com o retorno, o código saía 0.
                command_line.set_exit_status(command.exit_code)
                return command.exit_code
            if command.truncated:
                logger.info(
                    "Anúncio encurtado para %d caracteres (mensagem maior que o teto).",
                    len(command.text),
                )
            self._get_or_create_window().announce_text(command.text, source="ponte")
            return command.exit_code

        win = self._get_or_create_window()
        if is_background:
            # Modo autostart: sem janela, mas com bandeja — senão não há como
            # reabrir o Copilot quando o ambiente não registra atalho global.
            self.hold()
            self._setup_background_tray(win)
            return 0
        elif is_dictate:
            win.toggle_dictation()
        elif is_voice_pill:
            self._handle_voice_shortcut(win, force_pill=True)
        elif is_voice:
            self._handle_voice_shortcut(win)
        elif is_crop:
            win.trigger_direct_crop()
        elif is_toggle:
            win.toggle_hud()
        else:
            win.summon_hud()

        return 0


def main() -> int:
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = ZorinCopilotApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    # `raise SystemExit` e não `main()` solto: sem ele o código de saída de
    # `do_command_line` é descartado e `python -m zorin_copilot.ui.app` sai
    # sempre 0. O entry point instalado (`zorin-copilot`) propaga por conta
    # própria, o que fazia o defeito aparecer só em quem chama pelo módulo —
    # e engolia justamente o código que `--announce` usa para dizer
    # "não havia instância para falar" (ver `core/ha_announce.py`).
    raise SystemExit(main())
