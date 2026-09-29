# Decisão de design: diálogo nativo Libadwaita PreferencesDialog — integração visual perfeita com GNOME 46 / Zorin OS, com suporte a teste em tempo real de credenciais sem travar a interface.

"""Diálogo de configurações e preferências do Zorin Copilot."""

from __future__ import annotations

import logging
import shlex
import threading
from typing import Callable

from .gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from ..ai.providers import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMINI_LIVE_MODEL,
    DEFAULT_GEMINI_LIVE_VOICE,
    GEMINI_MODEL_CHOICES,
    GEMINI_LIVE_MODEL_CHOICES,
    GEMINI_LIVE_VOICE_CHOICES,
    GeminiProvider,
    HybridProvider,
    OllamaProvider,
    OpenAICompatProvider,
    WorkBuddyProvider,
)
from ..core.config import CopilotConfig
from ..core.google_auth import SERVICE_LABELS, known_services as google_services
from ..core.identity import DEFAULT_NAME, PT_BR_VOICES, AssistantIdentity, voice_from_selection
from ..core.memory import MemoryManager
from ..core.shortcuts import AutostartManager, ShortcutManager
from ..mcp.categories import (
    CATEGORY_DESCRIPTIONS,
    CATEGORY_LABELS,
    group_by_category,
)
from ..mcp.manager import DEFAULT_MCP_CONFIG_PATH, MCPManager
from ..shell.wake_word import normalize_text
from ..shell.risk import normalize_autonomy_level

logger = logging.getLogger(__name__)

# Ícones das abas de provedor. O fallback existe porque o Zorin OS pode usar um
# tema de ícones diferente do Adwaita padrão.
PROVIDER_ICONS: dict[str, str] = {
    "gemini": "network-server-symbolic",
    "ollama": "computer-symbolic",
    "openai": "preferences-system-network-symbolic",
}

# --------------------------------------------------------------------------- #
# Prévia de entidades do Home Assistant                                        #
#                                                                              #
# O "Testar" dizia só "conectado" — e conectar não prova que a integração       #
# enxerga a casa. Uma amostra do que foi descoberto é a diferença entre "o      #
# servidor respondeu" e "o Copilot consegue acender a luz da cozinha".          #
# --------------------------------------------------------------------------- #

#: Quantas entidades a amostra mostra. É uma prova, não um painel: cinco cabe
#: numa olhada, vinte viram uma lista que ninguém lê.
HA_PREVIEW_LIMIT = 5

#: Domínios que a integração sabe **acionar**, na ordem em que o usuário pensa
#: neles. Vêm primeiro porque são os que respondem "o Copilot controla o quê?".
HA_PREVIEW_DOMAINS: tuple[str, ...] = (
    "light",
    "switch",
    "climate",
    "cover",
    "fan",
    "media_player",
)

# O Home Assistant responde `on`/`off`; a interface é em português. Traduzir só
# os estados que aparecem na amostra — o resto passa como veio, em vez de fingir
# uma tradução completa que não existe.
HA_STATE_LABELS: dict[str, str] = {
    "on": "ligado",
    "off": "desligado",
    "unavailable": "indisponível",
    "unknown": "desconhecido",
}


def select_preview_entities(
    states: list[dict], limit: int = HA_PREVIEW_LIMIT
) -> list[dict]:
    """Escolhe até ``limit`` entidades para a amostra, priorizando as acionáveis.

    Nunca levanta: a lista vem de JSON de rede, então pode ser qualquer coisa.
    """
    if not isinstance(states, list):
        return []

    actionable: list[dict] = []
    others: list[dict] = []
    for state in states:
        if not isinstance(state, dict):
            continue
        domain = str(state.get("entity_id") or "").split(".", 1)[0]
        (actionable if domain in HA_PREVIEW_DOMAINS else others).append(state)

    try:
        take = max(0, int(limit))
    except (TypeError, ValueError):
        take = HA_PREVIEW_LIMIT
    return (actionable + others)[:take]


def describe_entity_chip(state: dict) -> str:
    """Texto de um chip de leitura: ``Cozinha · ligado``."""
    if not isinstance(state, dict):
        return "—"
    attributes = state.get("attributes")
    name = ""
    if isinstance(attributes, dict):
        name = str(attributes.get("friendly_name") or "").strip()
    name = name or str(state.get("entity_id") or "").strip() or "Entidade sem nome"

    raw = str(state.get("state") or "").strip()
    value = HA_STATE_LABELS.get(raw, raw) or "—"
    return f"{name} · {value}"


FALLBACK_ICON = "application-x-executable-symbolic"
VALID_PROVIDERS = ("gemini", "ollama", "openai")


class WakePhrasesRow(Adw.PreferencesRow):
    """Editor das frases de ativação como *chips* removíveis.

    Por que não um campo com vírgulas (o que havia antes): a vírgula é sintaxe
    invisível. Quem escreve ``ok copilot, ei copilot`` vê uma frase ambígua e
    não tem como saber se o resultado são duas frases ou três; e tirar a
    terceira de cinco exige editar a string inteira com precisão. A lista é um
    conjunto de objetos discretos — o controle deve refletir isso.

    Decisões de desenho:

    * **Sem ícone no chip.** O ícone seria idêntico em todos os chips e não
      distingue nada; repetir N vezes o mesmo glifo é decoração, não informação.
    * **Remover só no hover.** Mesma regra da sidebar e das zonas de confiança
      (§0.2): ação destrutiva não fica à vista convidando ao clique errado.
    * **Deduplicação pela normalização do motor.** ``OK Copilot`` e
      ``ok copilot`` são a mesma frase para o Vosk (comparação por substring
      sobre texto normalizado); aceitar as duas criaria duas entradas que se
      comportam igual. A normalização vem de :func:`normalize_text`, o mesmo
      código que decide o match — uma fonte só de verdade.
    """

    def __init__(self, title: str = "Frases de Ativação") -> None:
        super().__init__()
        # A linha contém um campo de texto; ativá-la abriria/selecionaria algo
        # que não existe. O clique deve ir direto para o campo.
        self.set_activatable(False)

        self._phrases: list[str] = []

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        self.set_child(box)

        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class("heading")
        box.append(heading)

        hint = Gtk.Label(
            label="Diga uma destas frases para acordar o Copilot por voz. "
            "Enter adiciona uma nova; o botão de cada chip remove.",
            xalign=0,
        )
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        hint.set_wrap(True)
        box.append(hint)

        self._flow = Gtk.FlowBox()
        self._flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._flow.set_max_children_per_line(3)
        self._flow.set_min_children_per_line(1)
        self._flow.set_row_spacing(6)
        self._flow.set_column_spacing(6)
        self._flow.set_homogeneous(False)
        box.append(self._flow)

        self._empty_label = Gtk.Label(
            label="Nenhuma frase configurada — o Copilot não acordará por voz.",
            xalign=0,
        )
        self._empty_label.add_css_class("caption")
        self._empty_label.add_css_class("dim-label")
        box.append(self._empty_label)

        add_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text("Adicionar frase…")
        self._entry.set_hexpand(True)
        self._entry.connect("activate", self._on_entry_activate)
        add_box.append(self._entry)

        self._add_button = Gtk.Button.new_from_icon_name("list-add-symbolic")
        self._add_button.set_tooltip_text("Adicionar frase")
        self._add_button.add_css_class("flat")
        self._add_button.connect("clicked", self._on_entry_activate)
        add_box.append(self._add_button)
        box.append(add_box)

        self._refresh()

    # -- API ---------------------------------------------------------------
    def get_phrases(self) -> list[str]:
        """As frases na ordem exibida (cópia — o chamador não mexe no estado)."""
        return list(self._phrases)

    def set_phrases(self, phrases) -> None:
        """Substitui a lista inteira, limpando e deduplicando."""
        self._phrases = []
        for phrase in phrases or []:
            self._append(str(phrase))
        self._refresh()

    def add_phrase(self, phrase: str) -> bool:
        """Acrescenta uma frase. ``False`` se vazia ou já presente."""
        if not self._append(str(phrase)):
            return False
        self._refresh()
        return True

    def remove_phrase(self, phrase: str) -> bool:
        """Remove uma frase. ``False`` se não estava na lista."""
        if phrase not in self._phrases:
            return False
        self._phrases.remove(phrase)
        self._refresh()
        return True

    # -- Interno -----------------------------------------------------------
    def _append(self, phrase: str) -> bool:
        """Valida e acrescenta em ``_phrases``, sem reconstruir a UI."""
        clean = " ".join(phrase.split())  # colapsa espaços internos, preserva caixa
        if not clean:
            return False
        key = normalize_text(clean)
        if not key:
            return False
        if any(normalize_text(p) == key for p in self._phrases):
            return False
        self._phrases.append(clean)
        return True

    def _on_entry_activate(self, _widget: Gtk.Widget) -> None:
        text = self._entry.get_text()
        if self.add_phrase(text):
            self._entry.set_text("")
        # Frase vazia/duplicada: mantém o texto no campo. Apagar o que a pessoa
        # digitou sem explicação é pior que deixar corrigir.

    def _refresh(self) -> None:
        """Reconstrói os chips a partir de ``_phrases``.

        Reconstruir é mais simples e mais difícil de errar que casar chip com
        entrada da lista; a lista tem unidades de itens e o custo é irrelevante.
        """
        while (child := self._flow.get_first_child()) is not None:
            self._flow.remove(child)
        for phrase in self._phrases:
            self._flow.append(self._build_chip(phrase))
        self._empty_label.set_visible(not self._phrases)

    def _build_chip(self, phrase: str) -> Gtk.Widget:
        chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        chip.add_css_class("card")
        chip.add_css_class("wake-phrase-chip")

        label = Gtk.Label(label=phrase, xalign=0, ellipsize=3)  # PANGO_ELLIPSIZE_END
        label.set_margin_start(10)
        label.set_max_width_chars(24)
        chip.append(label)

        remove = Gtk.Button.new_from_icon_name("window-close-symbolic")
        remove.add_css_class("flat")
        remove.add_css_class("circular")
        remove.set_tooltip_text(f'Remover a frase "{phrase}"')
        remove.set_visible(False)
        remove.set_margin_end(2)
        remove.connect("clicked", lambda *_: self.remove_phrase(phrase))
        chip.append(remove)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: remove.set_visible(True))
        motion.connect("leave", lambda *_: remove.set_visible(False))
        chip.add_controller(motion)
        return chip


class PreferencesDialog(Adw.PreferencesDialog):
    """Diálogo de configurações do Zorin Copilot usando Libadwaita."""

    def __init__(self, parent: Gtk.Window | None, on_saved: Callable[[CopilotConfig], None] | None = None):
        super().__init__()
        self.set_title("Configurações do Copilot")
        self.copilot_parent = parent
        self.on_saved = on_saved
        self.config = CopilotConfig.load()
        self.memory = MemoryManager()
        self._is_closed = False
        # Guarda de carregamento dos toggles Google: sem ele, `set_active` durante
        # a leitura dispararia os handlers e MUTARIA a config — abrir a tela
        # materializaria a allowlist sozinho.
        self._loading_google = False
        self._mcp_rows: list[Gtk.Widget] = []
        self._mcp_manager = getattr(self.copilot_parent, "mcp_manager", None) or MCPManager()
        self._mcp_change_listener = self._on_mcp_changed
        self._mcp_manager.add_change_listener(self._mcp_change_listener)
        self.connect("closed", self._on_dialog_closed)

        self._build_ui()
        self._load_values()

    def _build_ui(self) -> None:
        page = Adw.PreferencesPage(title="IA & Modelos", icon_name="preferences-system-symbolic")
        self.add(page)

        # ---------------------------------------------------------------------
        # Seleção de provedor: abas (ViewSwitcher) em vez de um dropdown, para que
        # as três opções fiquem visíveis de imediato — antes era preciso abrir o
        # combo para descobrir que Ollama/OpenAI eram suportados.
        # ---------------------------------------------------------------------
        self.provider_buttons: dict[str, Gtk.ToggleButton] = {}
        provider_group = Adw.PreferencesGroup(
            title="Provedor Ativo",
            description="Escolha onde suas perguntas serão processadas.",
        )
        segmented = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        segmented.add_css_class("linked")
        segmented.set_halign(Gtk.Align.CENTER)
        segmented.set_margin_top(6)
        segmented.set_margin_bottom(6)

        for key, label in (
            ("gemini", "Gemini"),
            ("ollama", "Ollama"),
            ("openai", "OpenAI"),
        ):
            btn = self._make_provider_button(key, label)
            self.provider_buttons[key] = btn
            segmented.append(btn)

        provider_group.add(segmented)
        page.add(provider_group)

        # ---------------------------------------------------------------------
        # Grupo: Google Gemini
        # ---------------------------------------------------------------------
        self.gemini_group = Adw.PreferencesGroup(
            title="Configuração do Google Gemini",
            description="Requer uma chave de API do Google AI Studio (gratuita).",
        )
        
        self.gemini_key_row = Adw.PasswordEntryRow(title="Chave de API (Gemini)")
        self.gemini_group.add(self.gemini_key_row)

        # A lista vem de `ai.providers` para não divergir do que o provedor
        # realmente aceita. "Outro (Personalizado)" fica sempre no fim — o
        # código de leitura/escrita abaixo depende dessa posição.
        self.gemini_models_list: list[str] = [*GEMINI_MODEL_CHOICES, "Outro (Personalizado)"]
        self.gemini_model_row = Adw.ComboRow(
            title="Modelo Gemini",
            subtitle=f"{GEMINI_MODEL_CHOICES[0]} acompanha a versão estável atual; fixe uma versão se preferir",
            model=Gtk.StringList.new(self.gemini_models_list),
        )
        self.gemini_model_row.connect("notify::selected", self._on_gemini_model_changed)
        self.gemini_group.add(self.gemini_model_row)

        self.gemini_custom_model_row = Adw.EntryRow(title="Nome do Modelo Personalizado")
        self.gemini_custom_model_row.set_visible(False)
        self.gemini_group.add(self.gemini_custom_model_row)

        # Modelo de Voz em Tempo Real (Gemini Multimodal Live API)
        self.gemini_live_models_list: list[str] = [m[1] for m in GEMINI_LIVE_MODEL_CHOICES] + ["Outro (Personalizado)"]
        self.gemini_live_model_row = Adw.ComboRow(
            title="Modelo de Voz (Live API)",
            subtitle="Modelo de áudio bidirecional em tempo real (Ctrl+M ou Super+Shift+V)",
            model=Gtk.StringList.new(self.gemini_live_models_list),
        )
        self.gemini_live_model_row.connect("notify::selected", self._on_gemini_live_model_changed)
        self.gemini_group.add(self.gemini_live_model_row)

        self.gemini_custom_live_model_row = Adw.EntryRow(title="Nome do Modelo Live Personalizado")
        self.gemini_custom_live_model_row.set_visible(False)
        self.gemini_group.add(self.gemini_custom_live_model_row)

        # Voz do Assistente (Gemini Live Voice)
        self.gemini_live_voices_list: list[str] = [v[1] for v in GEMINI_LIVE_VOICE_CHOICES]
        self.gemini_live_voice_row = Adw.ComboRow(
            title="Voz do Assistente",
            subtitle="Timbre e personalidade da fala gerada pela IA",
            model=Gtk.StringList.new(self.gemini_live_voices_list),
        )
        self.gemini_live_voice_prev_btn = Gtk.Button.new_from_icon_name("media-playback-start-symbolic")
        self.gemini_live_voice_prev_btn.add_css_class("flat")
        self.gemini_live_voice_prev_btn.add_css_class("circular")
        self.gemini_live_voice_prev_btn.set_valign(Gtk.Align.CENTER)
        self.gemini_live_voice_prev_btn.set_tooltip_text("Ouvir demonstração da voz selecionada")
        self.gemini_live_voice_prev_btn.connect("clicked", self._on_preview_live_voice_clicked)
        self.gemini_live_voice_row.add_suffix(self.gemini_live_voice_prev_btn)
        self.gemini_live_voice_row.connect("notify::selected", self._on_preferences_voice_selected)
        self.gemini_group.add(self.gemini_live_voice_row)

        link_row = Adw.ActionRow(title="Obter chave gratuita")
        link_btn = Gtk.LinkButton(
            label="Abrir Google AI Studio",
            uri="https://aistudio.google.com/app/apikey",
            valign=Gtk.Align.CENTER,
        )
        link_row.add_suffix(link_btn)
        self.gemini_group.add(link_row)

        # ---------------------------------------------------------------------
        # Grupo: WorkBuddy AI (Tencent HY4)
        # ---------------------------------------------------------------------
        self.workbuddy_group = Adw.PreferencesGroup(
            title="Configuração do WorkBuddy AI",
            description="Agente Tencent HY4 (Hunyuan MoE 770B) com raciocínio e visão multimodal.",
        )
        self.workbuddy_key_row = Adw.PasswordEntryRow(title="Chave de API (WorkBuddy ck_...)")
        self.workbuddy_group.add(self.workbuddy_key_row)

        self.workbuddy_models_list = [
            "hy4-preview",
            "default-model",
            "primary-model",
            "deep-model",
            "fast-model",
            "Outro (Personalizado)",
        ]
        self.workbuddy_model_row = Adw.ComboRow(
            title="Modelo WorkBuddy",
            subtitle="hy4-preview (Tencent HY4 MoE) ou selecione outro",
            model=Gtk.StringList.new(self.workbuddy_models_list),
        )
        self.workbuddy_model_row.connect("notify::selected", self._on_workbuddy_model_changed)
        self.workbuddy_group.add(self.workbuddy_model_row)

        self.workbuddy_custom_model_row = Adw.EntryRow(title="Nome do Modelo Personalizado")
        self.workbuddy_custom_model_row.set_visible(False)
        self.workbuddy_group.add(self.workbuddy_custom_model_row)

        self.workbuddy_url_row = Adw.EntryRow(title="URL Base da API (Endpoint v2)")
        self.workbuddy_group.add(self.workbuddy_url_row)
        page.add(self.workbuddy_group)

        # ---------------------------------------------------------------------
        # Grupo: Ollama (Local)
        # ---------------------------------------------------------------------
        self.ollama_group = Adw.PreferencesGroup(
            title="Configuração do Ollama (Local na GPU)",
            description="Processamento privado acelerado na GPU AMD Radeon RX 7600 (Vulkan/ROCm).",
        )
        self.ollama_url_row = Adw.EntryRow(title="Endereço do Servidor")
        self.ollama_group.add(self.ollama_url_row)

        self.ollama_model_row = Adw.EntryRow(title="Modelo de Texto (ex: qwen2.5:7b, mistral)")
        self.ollama_group.add(self.ollama_model_row)
        self.ollama_vision_model_row = Adw.EntryRow(title="Modelo de Visão / Recortes (ex: minicpm-v, llava)")
        self.ollama_group.add(self.ollama_vision_model_row)

        # ---------------------------------------------------------------------
        # Grupo: OpenAI / Compatível
        # ---------------------------------------------------------------------
        self.openai_group = Adw.PreferencesGroup(
            title="OpenAI / APIs Compatíveis",
            description="Compatível com Groq, DeepSeek, OpenRouter e OpenAI.",
        )
        self.openai_url_row = Adw.EntryRow(title="URL Base da API")
        self.openai_group.add(self.openai_url_row)

        self.openai_key_row = Adw.PasswordEntryRow(title="Chave de API")
        self.openai_group.add(self.openai_key_row)

        self.openai_model_row = Adw.EntryRow(title="Nome do Modelo")
        self.openai_group.add(self.openai_model_row)

        # Cada provedor é um grupo independente; somente o ativo fica visível
        # ---------------------------------------------------------------------
        # Grupo: Identidade do Assistente (ver `core/identity.py`)
        #
        # O nome e a voz local moram aqui porque **não tinham casa**: o nome estava
        # cravado em quatro lugares (título da janela, duas vezes no prompt, e o texto
        # que `load()` gerava) e `piper_voice_model` não tinha linha nenhuma — só era
        # ajustável editando o `config.json` à mão.
        # ---------------------------------------------------------------------
        identity_group = Adw.PreferencesGroup(
            title="Identidade do Assistente",
            description=(
                "O nome vale para o prompt, para o título da janela e para os rótulos. "
                "Um só lugar decide quem o assistente é."
            ),
        )
        self.assistant_name_row = Adw.EntryRow(title="Nome do Assistente")
        self.assistant_name_row.set_tooltip_text(
            f"Vazio usa o padrão ({DEFAULT_NAME})."
        )
        identity_group.add(self.assistant_name_row)

        self.local_voice_row = Adw.ComboRow(
            title="Voz Local (Piper)",
            subtitle="Voz offline, usada quando não há rede. O modelo é baixado na primeira fala.",
            model=Gtk.StringList.new([rotulo for _, rotulo in PT_BR_VOICES]),
        )
        identity_group.add(self.local_voice_row)

        # Sem controle para a persona, e é decisão: é um texto de vários parágrafos, e
        # um `Adw.EntryRow` de uma linha convidaria a uma persona truncada. A linha
        # existe para o ajuste **não ficar escondido** — um ajuste que ninguém sabe que
        # existe é o mesmo que não existir.
        persona_hint = Adw.ActionRow(
            title="Persona Personalizada",
            subtitle="Ajustável no config.json (chave system_prompt). Substitui o texto de personalidade e preserva as diretrizes técnicas.",
        )
        identity_group.add(persona_hint)
        page.add(identity_group)

        page.add(self.gemini_group)
        page.add(self.ollama_group)
        page.add(self.openai_group)

        # ---------------------------------------------------------------------
        # Grupo: Recursos Adicionais (Pesquisa na Web)
        # ---------------------------------------------------------------------
        features_group = Adw.PreferencesGroup(title="Recursos Adicionais")
        self.web_search_row = Adw.SwitchRow(
            title="Pesquisa na Web em Tempo Real",
            subtitle="Permite à IA buscar fatos recentes na internet para responder sobre atualidades.",
        )
        features_group.add(self.web_search_row)
        page.add(features_group)

        # ---------------------------------------------------------------------
        # Grupo: Modo Agente e Automação
        # ---------------------------------------------------------------------
        agent_group = Adw.PreferencesGroup(
            title="Modo Agente e Automação",
            description="Controle do orçamento de raciocínio e execução de tarefas autônomas no desktop.",
        )
        self.agent_adaptive_row = Adw.SwitchRow(
            title="Orçamento Adaptativo de Passos",
            subtitle="Amplia automaticamente o número de passos e tempo para tarefas complexas de análise e pesquisa.",
        )
        agent_group.add(self.agent_adaptive_row)

        self.agent_steps_adj = Gtk.Adjustment(value=15, lower=5, upper=50, step_increment=1, page_increment=5)
        self.agent_steps_row = Adw.SpinRow(
            title="Limite Padrão de Passos",
            subtitle="Número máximo de ações consecutivas por execução antes de pausar ou pedir confirmação.",
            adjustment=self.agent_steps_adj,
        )
        agent_group.add(self.agent_steps_row)

        self.autonomy_options = [
            ("low", "Baixa — pergunta mais"),
            ("standard", "Padrão"),
            ("high", "Alta — executa e avisa depois"),
        ]
        self.autonomy_level_row = Adw.ComboRow(
            title="Nível de Autonomia",
            subtitle=(
                "Quanto o assistente executa sem pedir confirmação. No nível alto, "
                "escritas reversíveis nas pastas de confiança rodam sozinhas e são avisadas depois."
            ),
            model=Gtk.StringList.new([label for _, label in self.autonomy_options]),
        )
        agent_group.add(self.autonomy_level_row)
        page.add(agent_group)

        # ---------------------------------------------------------------------
        # Grupo: Ações e Teste
        # ---------------------------------------------------------------------
        action_group = Adw.PreferencesGroup(title="Validação e Salvamento")
        
        test_row = Adw.ActionRow(title="Conexão com a IA")
        self.status_label = Gtk.Label(label="", xalign=0)
        self.status_label.add_css_class("dim-label")
        self.status_label.set_wrap(True)
        test_row.set_subtitle_lines(2)

        self.test_spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        test_row.add_suffix(self.test_spinner)

        test_btn = Gtk.Button(label="Testar", valign=Gtk.Align.CENTER)
        test_btn.connect("clicked", self._on_test_connection)
        test_row.add_suffix(test_btn)

        save_btn = Gtk.Button(label="Salvar", valign=Gtk.Align.CENTER)
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save)
        test_row.add_suffix(save_btn)

        action_group.add(test_row)
        page.add(action_group)
        self._build_memory_page()
        self._build_shortcuts_page()
        self._build_documents_privacy_page()

    def _build_shortcuts_page(self) -> None:
        page = Adw.PreferencesPage(title="Atalhos", icon_name="input-keyboard-symbolic")
        self.add(page)

        hud_group = Adw.PreferencesGroup(
            title="Invocação Rápida (Modo HUD)",
            description="Permite alternar a visibilidade do Zorin Copilot instantaneamente a partir de qualquer aplicativo do desktop.",
        )

        self.shortcut_switch_row = Adw.SwitchRow(
            title="Ativar Atalho Global de Sistema",
            subtitle="Registra uma tecla de atalho de sistema no GNOME/Zorin OS",
        )
        hud_group.add(self.shortcut_switch_row)

        self.shortcut_options = [
            ("<Super>c", "Super + C (Recomendado - Tecla Zorin/Windows + C)"),
            ("<Primary><Alt>space", "Ctrl + Alt + Espaço (Estilo Raycast/Alfred)"),
            ("<Super>grave", "Super + ` (Abaixo da tecla Esc)"),
            ("<Super>z", "Super + Z"),
        ]
        self.shortcut_combo_row = Adw.ComboRow(
            title="Combinação de Teclas",
            subtitle="Selecione a tecla para invocar o assistente",
            model=Gtk.StringList.new([label for _, label in self.shortcut_options]),
        )
        hud_group.add(self.shortcut_combo_row)

        info_row = Adw.ActionRow(
            title="Como Funciona o Modo HUD",
            subtitle="Ao pressionar o atalho, o Copilot surge centralizado com foco imediato no campo de busca. Pressione Esc ou o atalho novamente para ocultar.",
        )
        hud_group.add(info_row)
        page.add(hud_group)

        # ---------------------------------------------------------------------
        # Grupo: Recorte Inteligente Instantâneo (Visão IA)
        # ---------------------------------------------------------------------
        crop_group = Adw.PreferencesGroup(
            title="Recorte Inteligente Instantâneo (Visão IA)",
            description="Permite disparar a mira de seleção de área diretamente da tela de qualquer aplicativo e analisar imediatamente com a IA.",
        )

        self.crop_shortcut_switch_row = Adw.SwitchRow(
            title="Ativar Atalho de Recorte Direto",
            subtitle="Registra a combinação global no GNOME/Zorin OS para seleção direta",
        )
        crop_group.add(self.crop_shortcut_switch_row)

        self.crop_shortcut_options = [
            ("<Super><Shift>s", "Super + Shift + S (Padrão - Estilo Ferramenta de Captura)"),
            ("<Primary><Alt>v", "Ctrl + Alt + V (Visão Direta)"),
            ("<Primary><Alt>s", "Ctrl + Alt + S (Snippet)"),
            ("<Super>s", "Super + S"),
        ]
        self.crop_shortcut_combo_row = Adw.ComboRow(
            title="Combinação de Teclas de Recorte",
            subtitle="Selecione a tecla para acionar o recorte",
            model=Gtk.StringList.new([label for _, label in self.crop_shortcut_options]),
        )
        crop_group.add(self.crop_shortcut_combo_row)

        crop_info_row = Adw.ActionRow(
            title="Como Funciona o Recorte Direto",
            subtitle="Ao pressionar as teclas, a mira de seleção surge imediatamente na tela. Ao soltar o mouse, o Copilot abre já processando a leitura visual.",
        )
        crop_group.add(crop_info_row)
        page.add(crop_group)

        self._build_wake_word_group(page)

        # ---------------------------------------------------------------------
        # Grupo: Atalho Global de Conversa por Voz (Live Voice)
        # ---------------------------------------------------------------------
        voice_group = Adw.PreferencesGroup(
            title="Atalho Global de Conversa por Voz",
            description="Permite iniciar instantaneamente a chamada de voz contínua de qualquer aplicativo ou tela.",
        )

        self.voice_shortcut_switch_row = Adw.SwitchRow(
            title="Ativar Atalho de Conversa por Voz",
            subtitle="Registra a combinação global no GNOME para iniciar conversa por voz",
        )
        voice_group.add(self.voice_shortcut_switch_row)

        self.voice_shortcut_options = [
            ("<Super><Shift>v", "Super + Shift + V (Padrão - Conversa por Voz)"),
            ("<Primary><Alt>m", "Ctrl + Alt + M (Microfone)"),
            ("<Primary><Alt>v", "Ctrl + Alt + V (Voz)"),
            ("<Super>v", "Super + V"),
        ]
        self.voice_shortcut_combo_row = Adw.ComboRow(
            title="Combinação de Teclas de Voz",
            subtitle="Selecione a tecla para acionar a chamada de voz",
            model=Gtk.StringList.new([label for _, label in self.voice_shortcut_options]),
        )
        voice_group.add(self.voice_shortcut_combo_row)

        self.visualizer_style_options = [
            ("waves", "Ondas Fluidas Multicamadas (Estilo Fitas / Siri)"),
            ("bars", "Barras de Equalizador (Espectro com Cantos Arredondados)"),
            ("matrix", "Matriz de Pontos (LED Grid Futurista)"),
            ("orb", "Orbe Pulsante (Esferas Concêntricas Clássicas)"),
        ]
        self.visualizer_style_combo_row = Adw.ComboRow(
            title="Estilo do Visualizador de Áudio",
            subtitle="Animação interativa exibida durante a conversa por voz",
            model=Gtk.StringList.new([label for _, label in self.visualizer_style_options]),
        )
        voice_group.add(self.visualizer_style_combo_row)

        self.voice_overlay_options = [
            ("pill", "Pílula Flutuante Compacta (Dynamic Island - Minimalista)"),
            ("full", "Janela Completa (HUD Expandido)"),
        ]
        self.voice_overlay_combo_row = Adw.ComboRow(
            title="Formato de Exibição da Voz",
            subtitle="Interface exibida ao acionar o atalho global de conversa por voz",
            model=Gtk.StringList.new([label for _, label in self.voice_overlay_options]),
        )
        voice_group.add(self.voice_overlay_combo_row)

        # Descoberta dos atalhos de teclado da pílula. Sem isto eles só existiam
        # para quem abrisse o menu de clique direito.
        self.pill_hint_switch_row = Adw.SwitchRow(
            title="Mostrar Dicas de Atalho na Pílula",
            subtitle="Apresenta os atalhos de teclado (Espaço, Ctrl+M, i, p) na primeira vez que a pílula aparece",
        )
        voice_group.add(self.pill_hint_switch_row)

        # Atalho dedicado da pílula: sempre abre a pílula, mesmo no modo "full".
        self.voice_pill_shortcut_switch_row = Adw.SwitchRow(
            title="Atalho Dedicado da Pílula Flutuante",
            subtitle="Abre a pílula de voz mesmo quando o formato de exibição é a Janela Completa",
        )
        voice_group.add(self.voice_pill_shortcut_switch_row)

        self.voice_pill_shortcut_options = [
            ("<Super><Shift>p", "Super + Shift + P (Padrão - Pílula)"),
            ("<Primary><Alt>p", "Ctrl + Alt + P (Pílula)"),
            ("<Super>p", "Super + P (Pílula)"),
        ]
        self.voice_pill_shortcut_combo_row = Adw.ComboRow(
            title="Combinação de Teclas da Pílula",
            subtitle="Selecione a tecla para abrir diretamente a pílula flutuante",
            model=Gtk.StringList.new([label for _, label in self.voice_pill_shortcut_options]),
        )
        voice_group.add(self.voice_pill_shortcut_combo_row)

        # O subtítulo é **derivado** da voz configurada, e não cravado: antes dizia
        # "pt_BR-faber-medium" fixo, então quem trocasse de voz via esta linha
        # continuar a afirmar a voz antiga — um texto que mentia, e no mesmo app que
        # tinha o defeito de baixar a voz errada.
        self.local_voice_info_row = Adw.ActionRow(
            title="Voz Local Contínua &amp; Soberana",
            subtitle="",
        )
        voice_group.add(self.local_voice_info_row)
        page.add(voice_group)

        # ---------------------------------------------------------------------
        # Grupo: Ditado Global por Voz no Aplicativo em Foco (Voice Typing)
        # ---------------------------------------------------------------------
        dictate_group = Adw.PreferencesGroup(
            title="Ditado Global por Voz (Voice Typing)",
            description="Permite ditar texto diretamente em qualquer aplicativo ativo (navegador, LibreOffice, editor, terminal) sem roubar o foco.",
        )

        self.dictate_shortcut_switch_row = Adw.SwitchRow(
            title="Ativar Atalho de Ditado Global",
            subtitle="Registra a combinação global no sistema para ditar fala no app em foco",
        )
        dictate_group.add(self.dictate_shortcut_switch_row)

        self.dictate_shortcut_options = [
            ("<Super><Shift>d", "Super + Shift + D (Padrão - Ditado Global)"),
            ("<Primary><Alt>d", "Ctrl + Alt + D (Ditado)"),
            ("<Super>d", "Super + D (Ditado)"),
        ]
        self.dictate_shortcut_combo_row = Adw.ComboRow(
            title="Combinação de Teclas de Ditado",
            subtitle="Selecione a tecla para acionar o ditado por voz",
            model=Gtk.StringList.new([label for _, label in self.dictate_shortcut_options]),
        )
        dictate_group.add(self.dictate_shortcut_combo_row)

        self.dictate_silence_spin = Adw.SpinRow.new_with_range(0.4, 3.0, 0.1)
        self.dictate_silence_spin.set_title("Tempo de Silêncio para Concluir (VAD)")
        self.dictate_silence_spin.set_subtitle("Segundos de pausa na fala antes de processar e digitar o texto")
        dictate_group.add(self.dictate_silence_spin)

        self.dictate_sound_switch_row = Adw.SwitchRow(
            title="Efeitos Sonoros de Início e Conclusão (Earcons)",
            subtitle="Toca um som sutil do sistema ao abrir o microfone e ao concluir a digitação",
        )
        dictate_group.add(self.dictate_sound_switch_row)

        self.dictate_polish_switch_row = Adw.SwitchRow(
            title="Polimento Inteligente &amp; Pontuação Verbal",
            subtitle="Remove hesitações ('tipo assim', repetições) e converte comandos falados ('vírgula', 'ponto', 'nova linha')",
        )
        dictate_group.add(self.dictate_polish_switch_row)

        dictate_info_row = Adw.ActionRow(
            title="Como Funciona o Ditado Global",
            subtitle="Pressione o atalho enquanto foca em qualquer app. Um indicador surge, escuta a fala, transcreve localmente via Whisper e injeta o texto no cursor.",
        )
        dictate_group.add(dictate_info_row)
        page.add(dictate_group)

        # ---------------------------------------------------------------------
        # Grupo: Consciência de Contexto (Fase 3: Context Awareness)
        # ---------------------------------------------------------------------
        context_group = Adw.PreferencesGroup(
            title="Consciência de Contexto &amp; Sugestões Proativas",
            description="Adapta as ações sugeridas na tela inicial de acordo com o aplicativo em foco e o conteúdo copiado.",
        )
        self.context_awareness_switch_row = Adw.SwitchRow(
            title="Ativar Sugestões Conscientes de Contexto",
            subtitle="Sugere prompts adaptados para código, páginas web, erros de terminal e documentos",
        )
        context_group.add(self.context_awareness_switch_row)
        page.add(context_group)

        # ---------------------------------------------------------------------
        # Grupo: Inicialização com o Sistema (Autostart)
        # ---------------------------------------------------------------------
        autostart_group = Adw.PreferencesGroup(
            title="Inicialização com o Zorin OS",
            description="Permite que o assistente esteja ativo na memória no login sem abrir janelas intrusivas.",
        )
        self.autostart_switch_row = Adw.SwitchRow(
            title="Iniciar com o Sistema",
            subtitle="Inicia o Zorin Copilot em segundo plano para resposta instantânea ao atalho global",
        )
        autostart_group.add(self.autostart_switch_row)
        page.add(autostart_group)

    def _build_wake_word_group(self, page: Adw.PreferencesPage) -> None:
        """Grupo de wake word ("palavra de ativação") — invocação hands-free.

        As frases são definidas/editáveis aqui (separadas por vírgula). Como o
        STT offline (Vosk) transcreve e comparamos por substring, qualquer frase
        funciona — o usuário pode trocar/adicionar a qualquer momento.
        """
        wake_group = Adw.PreferencesGroup(
            title="Palavra de Ativação (Wake Word)",
            description="Invoque o Copilot por voz, sem tocar no teclado. A detecção é 100% local e offline (Vosk) — nenhum áudio sai do computador.",
        )
        # Guardado pelo mesmo motivo de `_trusted_group`/`_quarantine_group`: a
        # linha de frases é reconstruída internamente e o grupo é o ponto de
        # entrada estável para inspecionar a página.
        self._wake_group = wake_group

        self.wake_word_switch_row = Adw.SwitchRow(
            title="Ativar Palavra de Ativação",
            subtitle="Mantém o microfone ouvindo as frases abaixo quando o app está aberto",
        )
        wake_group.add(self.wake_word_switch_row)

        self.wake_phrases_row = WakePhrasesRow(title="Frases de Ativação")
        wake_group.add(self.wake_phrases_row)

        # O modelo do Vosk é uma PASTA (contém am/, conf/, graph/…), não um
        # arquivo — por isso `select_folder` e não `open`. Escolher um arquivo
        # aqui produziria um caminho que o `vosk.Model()` recusa em runtime, sem
        # o usuário entender por quê.
        self.wake_model_row = Adw.EntryRow(title="Pasta do Modelo Vosk")
        self.wake_model_row.set_tooltip_text(
            "Pasta do modelo de linguagem (ex.: ~/modelos/vosk-pt). "
            "Baixe em alphacephei.com/vosk/models."
        )
        self.wake_model_button = Gtk.Button.new_from_icon_name("folder-open-symbolic")
        self.wake_model_button.set_valign(Gtk.Align.CENTER)
        self.wake_model_button.add_css_class("flat")
        self.wake_model_button.set_tooltip_text("Selecionar a pasta do modelo…")
        self.wake_model_button.connect("clicked", self._on_choose_wake_model)
        self.wake_model_row.add_suffix(self.wake_model_button)
        wake_group.add(self.wake_model_row)

        # Botão de download: baixa o modelo de linguagem (~32 MB) do Vosk para o
        # cache padrão e preenche o caminho sozinho. É a solução definitiva para
        # o "não tenho a pasta do Vosk" — sem o usuário ter que caçar o zip.
        from ..shell.vosk_model import DEFAULT_LANG, KNOWN_MODELS

        label = KNOWN_MODELS.get(DEFAULT_LANG, {}).get("label", "Modelo Vosk")
        self.wake_download_row = Adw.ActionRow(
            title=f"Baixar Modelo ({DEFAULT_LANG})",
            subtitle=f"{label}. Baixa para o cache e configura o caminho automaticamente.",
        )
        self.wake_download_button = Gtk.Button(label="Baixar")
        self.wake_download_button.set_valign(Gtk.Align.CENTER)
        self.wake_download_button.connect("clicked", self._on_download_wake_model)
        self.wake_download_row.add_suffix(self.wake_download_button)
        wake_group.add(self.wake_download_row)

        # --- Ajustes finos -------------------------------------------------
        # O Vosk reemite o mesmo texto várias vezes por segundo enquanto a fala
        # acontece; sem estes controles uma única frase disparava a ativação
        # várias vezes seguidas.
        self.wake_sensitivity_row = Adw.ActionRow(
            title="Sensibilidade do Microfone",
            subtitle="Mais alta: detecta até voz baixa (pode gerar falsos positivos). "
            "Mais baixa: exige fala mais alta.",
        )
        self.wake_sensitivity_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.05
        )
        self.wake_sensitivity_scale.set_draw_value(True)
        self.wake_sensitivity_scale.set_digits(2)
        self.wake_sensitivity_scale.set_size_request(150, -1)
        self.wake_sensitivity_row.add_suffix(self.wake_sensitivity_scale)
        wake_group.add(self.wake_sensitivity_row)

        self.wake_cooldown_row = Adw.ActionRow(
            title="Intervalo entre Ativações (s)",
            subtitle="Tempo mínimo entre duas ativações — evita disparos repetidos da mesma fala.",
        )
        self.wake_cooldown_spin = Gtk.SpinButton.new_with_range(0.0, 30.0, 0.5)
        self.wake_cooldown_spin.set_digits(1)
        self.wake_cooldown_spin.set_valign(Gtk.Align.CENTER)
        self.wake_cooldown_row.add_suffix(self.wake_cooldown_spin)
        wake_group.add(self.wake_cooldown_row)

        self.wake_echo_row = Adw.ActionRow(
            title="Espera Após a Resposta (s)",
            subtitle="Fica surdo por esse tempo depois que a IA fala — evita que a própria "
            "resposta reative o Copilot.",
        )
        self.wake_echo_spin = Gtk.SpinButton.new_with_range(0.0, 10.0, 0.1)
        self.wake_echo_spin.set_digits(1)
        self.wake_echo_spin.set_valign(Gtk.Align.CENTER)
        self.wake_echo_row.add_suffix(self.wake_echo_spin)
        wake_group.add(self.wake_echo_row)

        self.wake_vad_row = Adw.SwitchRow(
            title="Ignorar Silêncio (VAD)",
            subtitle="Só processa o áudio quando há fala — menos CPU e menos falsos positivos.",
        )
        wake_group.add(self.wake_vad_row)

        self.wake_device_row = Adw.EntryRow(
            title="Dispositivo de Microfone (opcional)",
        )
        self.wake_device_row.set_tooltip_text(
            "Nome ou alvo do PipeWire/ALSA. Vazio = padrão do sistema."
        )
        wake_group.add(self.wake_device_row)

        # Teste de nível: o usuário confere o microfone antes de sair falando.
        self.wake_test_row = Adw.ActionRow(
            title="Testar Microfone",
            subtitle="Clique em Testar e fale: a barra mostra o nível capturado.",
        )
        self.wake_level_bar = Gtk.LevelBar.new_for_interval(0.0, 1.0)
        self.wake_level_bar.set_size_request(120, -1)
        self.wake_level_bar.set_valign(Gtk.Align.CENTER)
        self.wake_test_button = Gtk.Button(label="Testar")
        self.wake_test_button.set_valign(Gtk.Align.CENTER)
        self.wake_test_button.connect("clicked", self._on_test_microphone)
        self.wake_test_row.add_suffix(self.wake_level_bar)
        self.wake_test_row.add_suffix(self.wake_test_button)
        wake_group.add(self.wake_test_row)

        wake_info_row = Adw.ActionRow(
            title="Como Funciona a Palavra de Ativação",
            subtitle="Diga uma das frases (ex.: \"ok copilot\") para iniciar a conversa por voz. "
            "Você pode definir e alterar as frases livremente. Requer o pacote 'vosk' e um modelo "
            "de linguagem (ex.: pt-BR) — sem eles, o recurso permanece desativado.",
        )
        wake_info_row.set_subtitle_lines(4)
        wake_group.add(wake_info_row)
        page.add(wake_group)

    def _on_test_microphone(self, _btn: Gtk.Button) -> None:
        """Amostra o microfone por ~2,5s em thread e mostra o nível na barra.

        Roda em thread porque `sample_level()` bloqueia lendo o PCM; o resultado
        volta para a main thread via GLib.idle_add.
        """
        if getattr(self, "_mic_test_running", False):
            return
        self._mic_test_running = True
        self.wake_test_button.set_label("Ouvindo...")
        self.wake_test_button.set_sensitive(False)
        self.wake_test_row.set_subtitle("Fale agora...")
        device = self.wake_device_row.get_text().strip()

        def _sample() -> None:
            peak = 0.0
            err = ""
            try:
                from ..shell.wake_word import VoskWakeWordBackend

                # model_path vazio de propósito: medir o nível não depende do
                # modelo, e assim não pagamos o custo de carregá-lo.
                backend = VoskWakeWordBackend(model_path="", vad_enabled=False, device=device)
                peak = backend.sample_level(2.5)
            except Exception as exc:
                err = str(exc)
            GLib.idle_add(self._finish_mic_test, peak, err)

        threading.Thread(target=_sample, daemon=True, name="mic-level-test").start()

    def _finish_mic_test(self, peak: float, err: str) -> bool:
        """(main thread) Mostra o resultado da amostragem do microfone."""
        self._mic_test_running = False
        self.wake_test_button.set_label("Testar")
        self.wake_test_button.set_sensitive(True)
        self.wake_level_bar.set_value(peak)
        if err:
            self.wake_test_row.set_subtitle(f"Não foi possível abrir o microfone: {err}")
        elif peak < 0.05:
            self.wake_test_row.set_subtitle("Nenhum som detectado. Verifique o microfone.")
        else:
            self.wake_test_row.set_subtitle(f"Nível máximo capturado: {int(peak * 100)}%")
        return GLib.SOURCE_REMOVE

    def _on_choose_wake_model(self, _btn: Gtk.Button) -> None:
        """Abre o seletor nativo de pastas para o modelo Vosk."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Escolher a pasta do modelo Vosk")
        dialog.select_folder(self, None, self._on_wake_model_chosen)

    def _on_wake_model_chosen(
        self, dialog: Gtk.FileDialog, result: Gio.AsyncResult
    ) -> None:
        """Conclusão do seletor: escreve a pasta no campo do modelo."""
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error as err:
            # Cancelar não é erro; qualquer outra falha é irrelevante para o
            # usuário, que só precisa saber que o campo não mudou.
            logger.debug("Seleção da pasta do modelo Vosk cancelada ou falhou: %s", err)
            return
        if folder is None:
            return
        path = folder.get_path()
        if path:
            self.wake_model_row.set_text(path)

    def _on_download_wake_model(self, _btn: Gtk.Button) -> None:
        """Baixa o modelo Vosk em thread e preenche o caminho ao terminar.

        Roda em thread porque ``download_model`` bloqueia na rede; o resultado
        volta para a main thread via GLib.idle_add. Reentrada é bloqueada pelo
        flag ``_wake_dl_running``.
        """
        if getattr(self, "_wake_dl_running", False):
            return
        self._wake_dl_running = True
        self.wake_download_button.set_sensitive(False)
        self.wake_download_button.set_label("Baixando…")
        self.wake_download_row.set_subtitle("Baixando modelo Vosk (pt-BR)…")

        from ..shell.vosk_model import DEFAULT_LANG, download_model

        def _work() -> None:
            try:
                path = download_model(
                    DEFAULT_LANG, progress=self._on_wake_dl_progress
                )
                GLib.idle_add(self._finish_wake_dl, path, "")
            except Exception as exc:  # rede/extração — avisa em vez de falhar calado
                GLib.idle_add(self._finish_wake_dl, "", str(exc))

        threading.Thread(target=_work, daemon=True, name="wake-model-dl").start()

    def _on_wake_dl_progress(self, fraction: float, message: str) -> None:
        """Chamado da thread de download; agenda a atualização na main thread."""
        GLib.idle_add(self._set_wake_dl_subtitle, f"Baixando… {message}")

    def _set_wake_dl_subtitle(self, text: str) -> bool:
        self.wake_download_row.set_subtitle(text)
        return GLib.SOURCE_REMOVE

    def _finish_wake_dl(self, path: str, err: str) -> bool:
        """(main thread) Conclusão do download: preenche o caminho ou reporta."""
        self._wake_dl_running = False
        self.wake_download_button.set_sensitive(True)
        self.wake_download_button.set_label("Baixar")
        if err:
            self.wake_download_row.set_subtitle(f"Falha: {err}")
            self.show_toast(f"Falha ao baixar modelo Vosk: {err}")
            return GLib.SOURCE_REMOVE
        self.wake_model_row.set_text(path)
        self.wake_download_row.set_subtitle(f"Modelo pronto: {path}")
        return GLib.SOURCE_REMOVE

    def _make_provider_button(self, key: str, label: str) -> Gtk.ToggleButton:
        """Cria um botão do controle segmentado de provedores."""
        btn = Gtk.ToggleButton()
        btn.set_hexpand(True)
        btn.set_child(Adw.ButtonContent(icon_name=self._resolve_icon(key), label=label))
        btn.connect("toggled", self._on_provider_toggled, key)
        return btn

    def _on_provider_toggled(self, button: Gtk.ToggleButton, key: str) -> None:
        """Mantém exatamente um provedor ativo e sincroniza os grupos visíveis."""
        if button.get_active():
            for other_key, other in self.provider_buttons.items():
                if other_key != key and other.get_active():
                    other.set_active(False)
        self._update_visibility()

    def active_provider(self) -> str:
        """Retorna o provedor atualmente selecionado (padrão: gemini)."""
        for key, btn in self.provider_buttons.items():
            if btn.get_active():
                return key
        return "gemini"

    def _update_visibility(self) -> None:
        """Exibe apenas o grupo de configuração do provedor ativo."""
        active = self.active_provider()
        self.gemini_group.set_visible(active == "gemini")
        self.ollama_group.set_visible(active == "ollama")
        self.openai_group.set_visible(active == "openai")

    def set_active_provider(self, provider: str) -> None:
        """Seleciona o provedor informado, caindo para gemini se for inválido."""
        if provider not in VALID_PROVIDERS:
            provider = "gemini"
        btn = self.provider_buttons.get(provider)
        if btn is not None:
            btn.set_active(True)

    @staticmethod
    def _resolve_icon(provider: str) -> str:
        """Devolve o ícone do provedor, caindo para um genérico se o tema não o tiver."""
        icon = PROVIDER_ICONS.get(provider, FALLBACK_ICON)
        display = Gdk.Display.get_default()
        if display is None:
            return icon
        theme = Gtk.IconTheme.get_for_display(display)
        return icon if theme.has_icon(icon) else FALLBACK_ICON

    def _build_documents_privacy_page(self) -> None:
        page = Adw.PreferencesPage(title="Privacidade", icon_name="security-high-symbolic")
        self.add(page)

        # 1. Zonas de Confiança.
        #
        # Antes eram duas linhas de texto puro: o usuário via as pastas e não
        # podia tocá-las — para mudar qualquer coisa era preciso editar o JSON
        # na mão. Agora o que é listado é editável no mesmo lugar, que é a regra
        # desta tela: nada de mostrar dado sem dar o controle dele.
        self._trusted_group = Adw.PreferencesGroup(
            title="Pastas Confiáveis (Leitura e Síntese)",
            description="Documentos aqui são indexados e podem ser citados nas respostas.",
        )
        page.add(self._trusted_group)

        self._quarantine_group = Adw.PreferencesGroup(
            title="Zona de Cautela (Downloads)",
            description=(
                "Indexados, mas tratados como conteúdo não confiável: o texto "
                "nunca vira instrução para o assistente."
            ),
        )
        page.add(self._quarantine_group)

        self._populate_zone_rows(self._trusted_group, "trusted")
        self._populate_zone_rows(self._quarantine_group, "quarantine")

        # 2. Blindagem e PII.
        #
        # Os títulos continuam técnicos de propósito — precisão importa quando o
        # assunto é o que sai do PC. O que faltava era a tradução: cada toggle
        # ganha UM exemplo humano, concreto, no lugar de uma paráfrase do nome.
        privacy_group = Adw.PreferencesGroup(
            title="Blindagem e Proteção de Dados",
            description="Políticas de segurança contra vazamento de dados confidenciais e injeções indiretas.",
        )

        self.mask_pii_switch_row = Adw.SwitchRow(
            title="Mascarar Dados Sensíveis (PII)",
            subtitle="Mascara CPF, e-mail e token antes de sair do PC.",
        )
        privacy_group.add(self.mask_pii_switch_row)

        self.rag_local_only_switch_row = Adw.SwitchRow(
            title="Forçar RAG Exclusivamente Local",
            subtitle="Seu índice de documentos nunca toca a nuvem.",
        )
        privacy_group.add(self.rag_local_only_switch_row)

        # Fica aqui, e não em "Manutenção Automática": o que ele decide é se as
        # pastas monitoradas são **vigiadas** durante a sessão. É a mesma família de
        # decisão das duas linhas acima — sobre o que sai do PC e o que é lido dele.
        # Ligado por padrão: completa a indexação que já está ligada, que hoje só
        # roda no arranque.
        self.file_watcher_row = Adw.SwitchRow(
            title="Indexar Documentos Assim que Forem Salvos",
            subtitle=(
                "Vigia as pastas confiáveis e de cautela e indexa na hora o que muda. "
                "Desligado, um documento salvo com o app aberto só entra na busca "
                "quando o Copilot reiniciar."
            ),
        )
        privacy_group.add(self.file_watcher_row)

        self.ignored_patterns_entry_row = Adw.EntryRow(
            title="Padrões Ignorados (.env, *.key, senhas)",
        )
        privacy_group.add(self.ignored_patterns_entry_row)

        page.add(privacy_group)

        # 3. Automação e Execução
        exec_group = Adw.PreferencesGroup(
            title="Automação e Execução de Tarefas",
            description="Controla a proatividade na execução de ações seguras propostas pelo assistente.",
        )
        self.auto_execute_switch_row = Adw.SwitchRow(
            title="Executar Ações Seguras Automaticamente",
            subtitle="Abre arquivos e links na hora, sem pedir confirmação a cada vez.",
        )
        exec_group.add(self.auto_execute_switch_row)
        page.add(exec_group)

    # ------------------------------------------------------------------
    # Zonas de Confiança — lista editável
    # ------------------------------------------------------------------
    _ZONE_ATTRS = {
        "trusted": "trusted_directories",
        "quarantine": "quarantine_directories",
    }

    def _zone_paths(self, zone: str) -> list[str]:
        """Pastas configuradas numa zona, sempre como lista nova."""
        return list(getattr(self.config, self._ZONE_ATTRS[zone], []) or [])

    def _populate_zone_rows(self, group: Adw.PreferencesGroup, zone: str) -> None:
        """(Re)constrói as linhas de uma zona de confiança.

        Reconstruir é mais simples — e mais difícil de errar — que tentar casar
        linha com entrada da config: a lista tem unidades de itens e o custo é
        irrelevante. As linhas anteriores são removidas antes, para a ordem se
        manter estável.
        """
        key = f"_zone_rows_{zone}"
        for row in getattr(self, key, []):
            group.remove(row)

        rows: list[Adw.ActionRow] = []
        for path in self._zone_paths(zone):
            rows.append(self._build_zone_dir_row(group, zone, path))
        rows.append(self._build_add_folder_row(group, zone))

        for row in rows:
            group.add(row)
        setattr(self, key, rows)

    def _build_zone_dir_row(
        self, group: Adw.PreferencesGroup, zone: str, path: str
    ) -> Adw.ActionRow:
        """Uma pasta da zona, com a lixeira revelada só no hover.

        Mesma decisão da sidebar (§0.2 do plano): ação destrutiva não fica
        permanentemente à vista convidando ao clique errado.
        """
        row = Adw.ActionRow(title=path)
        row.set_activatable(False)

        icon = Gtk.Image.new_from_icon_name("folder-symbolic")
        row.add_prefix(icon)

        remove_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        remove_btn.set_valign(Gtk.Align.CENTER)
        remove_btn.add_css_class("flat")
        remove_btn.set_tooltip_text("Remover esta pasta da zona")
        remove_btn.set_visible(False)
        remove_btn.connect("clicked", lambda *_: self._remove_zone_path(group, zone, path))
        row.add_suffix(remove_btn)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: remove_btn.set_visible(True))
        motion.connect("leave", lambda *_: remove_btn.set_visible(False))
        row.add_controller(motion)
        return row

    def _build_add_folder_row(
        self, group: Adw.PreferencesGroup, zone: str
    ) -> Adw.ActionRow:
        """Linha que abre o seletor de diretório nativo."""
        label = "Adicionar pasta confiável…" if zone == "trusted" else "Adicionar pasta de cautela…"
        row = Adw.ActionRow(title=label)
        row.set_activatable(True)
        row.add_prefix(Gtk.Image.new_from_icon_name("folder-new-symbolic"))
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        row.connect("activated", lambda *_: self._choose_zone_folder(group, zone))
        return row

    def _choose_zone_folder(self, group: Adw.PreferencesGroup, zone: str) -> None:
        """Abre o `Gtk.FileDialog` em modo pasta — assíncrono, não trava a UI."""
        dialog = Gtk.FileDialog()
        dialog.set_title(
            "Escolher pasta confiável" if zone == "trusted" else "Escolher pasta de cautela"
        )
        dialog.select_folder(self, None, self._on_zone_folder_chosen, (group, zone))

    def _on_zone_folder_chosen(
        self, dialog: Gtk.FileDialog, result: Gio.AsyncResult, data: tuple
    ) -> None:
        """Conclusão do seletor: acrescenta a pasta à zona escolhida."""
        group, zone = data
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error as err:
            # Cancelar o diálogo não é erro; qualquer outra falha é silenciosa
            # aqui porque o usuário só precisa saber que nada mudou.
            logger.debug("Seleção de pasta cancelada ou falhou: %s", err)
            return
        if folder is None:
            return

        path = folder.get_path()
        if not path:
            return

        attr = self._ZONE_ATTRS[zone]
        current = self._zone_paths(zone)
        if path in current:
            return
        current.append(path)
        setattr(self.config, attr, current)
        self._populate_zone_rows(group, zone)

    def _remove_zone_path(self, group: Adw.PreferencesGroup, zone: str, path: str) -> None:
        """Tira uma pasta da zona e reconstrói as linhas."""
        current = self._zone_paths(zone)
        if path not in current:
            return
        current.remove(path)
        setattr(self.config, self._ZONE_ATTRS[zone], current)
        self._populate_zone_rows(group, zone)

    def _build_memory_page(self) -> None:
        page = Adw.PreferencesPage(title="Memória", icon_name="document-properties-symbolic")
        self.add(page)

        # 1. Estatísticas de Ações e Perfil do Sistema
        stats = self.memory.get_action_stats()
        profile = self.memory.get_system_profile()

        info_group = Adw.PreferencesGroup(title="Métricas e Perfil Local")
        stats_row = Adw.ActionRow(
            title="Ações Executadas no Desktop",
            subtitle=f"Total: {stats['total']} ações | {stats['successful']} com sucesso ({stats['success_rate']}%)",
        )
        info_group.add(stats_row)

        os_info = profile.get("os_name", "Zorin OS 18")
        session = profile.get("session_type", "wayland")
        browser = profile.get("default_browser", "Detectando...")
        profile_row = Adw.ActionRow(
            title="Perfil do Computador",
            subtitle=f"{os_info} ({session}) • Navegador: {browser}",
        )
        info_group.add(profile_row)
        page.add(info_group)

        # 2. Fatos e Preferências
        self.facts_group = Adw.PreferencesGroup(
            title="Fatos e Preferências Aprendidas",
            description="Informações que o Copilot memorizou para personalizar suas respostas e ações.",
        )
        self.fact_rows: list[Adw.ActionRow] = []
        # Linha de ação só aparece quando há duplicatas legadas a consolidar.
        self.facts_merge_row: Adw.ActionRow | None = None
        self._populate_facts_group()
        page.add(self.facts_group)

        # 3. Gerenciamento
        mgmt_group = Adw.PreferencesGroup(title="Privacidade e Controle")
        clear_row = Adw.ActionRow(
            title="Limpar Toda a Memória",
            subtitle="Remove o histórico de ações e conhecimentos aprendidos localmente",
        )
        clear_btn = Gtk.Button(label="Limpar Memória", valign=Gtk.Align.CENTER)
        clear_btn.add_css_class("destructive-action")
        clear_btn.connect("clicked", self._on_clear_memory)
        clear_row.add_suffix(clear_btn)
        mgmt_group.add(clear_row)
        page.add(mgmt_group)

        # 4. Manutenção automática
        #
        # Fica na página Memória, e não em "Casa Inteligente": isto é sobre o banco
        # local, não sobre o Home Assistant. O interruptor é opt-in explícito porque
        # a consolidação **apaga** histórico de ações antigo.
        maintenance_group = Adw.PreferencesGroup(
            title="Manutenção Automática",
            description=(
                "Poda o histórico de ações antigo e destila padrões para o conhecimento "
                "local. Roda no máximo uma vez por dia, em segundo plano."
            ),
        )
        self.consolidation_row = Adw.SwitchRow(
            title="Consolidar Memória Automaticamente",
            subtitle="Mantém o banco local enxuto removendo o histórico mais antigo",
        )
        self.consolidation_row.connect(
            "notify::active",
            lambda row, _p: self.consolidation_days_row.set_sensitive(row.get_active()),
        )
        maintenance_group.add(self.consolidation_row)

        self.consolidation_days_row = Adw.SpinRow.new_with_range(1, 365, 1)
        self.consolidation_days_row.set_title("Manter Histórico de Ações")
        self.consolidation_days_row.set_subtitle("Dias de histórico preservados antes da poda")
        maintenance_group.add(self.consolidation_days_row)
        page.add(maintenance_group)

        # ---------------------------------------------------------------------
        # Página 5: Conexões (Extensões MCP + Casa Inteligente)
        #
        # Duas integrações que o usuário pensa como uma coisa só: "o que o
        # Copilot se conecta". Eram duas páginas separadas, o que empurrava a
        # barra de navegação para seis rótulos — e seis rótulos não cabem sem
        # truncar. Cinco páginas cabem, e o agrupamento é mais honesto.
        # ---------------------------------------------------------------------
        connections_page = Adw.PreferencesPage(
            title="Conexões", icon_name="application-x-addon-symbolic"
        )
        self.add(connections_page)
        self._build_mcp_page(connections_page)
        self._build_smart_home_page(connections_page)
        self._build_google_page(connections_page)

    def _build_google_page(self, page: Adw.PreferencesPage) -> None:
        """Contas Google (Agenda, Drive, Gmail). Opt-in: nada sai da máquina sozinho.

        O grupo é guardado como atributo porque `Adw.PreferencesDialog` **não** dá
        acesso à árvore das páginas (`labels(dlg)` devolve `[]`) — os testes de
        forma precisam do grupo em mãos.
        """
        self.google_group = Adw.PreferencesGroup(
            title="Contas Google",
            description=(
                "Agenda, Drive e Gmail da sua conta. Desligado por padrão: ao ativar, "
                "os dados desses serviços passam a ir para os servidores da Google."
            ),
        )

        self.google_switch_row = Adw.SwitchRow(
            title="Habilitar integrações Google",
            subtitle="Enquanto desligado, o Copilot não consulta nem envia nada para o Google.",
        )
        self.google_switch_row.set_active(bool(getattr(self.config, "google_enabled", False)))
        self.google_switch_row.connect("notify::active", self._on_google_master_toggled)
        self.google_group.add(self.google_switch_row)

        # Aviso proativo de expiração do refresh token. Nasce escondido e só
        # aparece quando há o que dizer — em modo "Testing" o acesso dura ~7 dias
        # e o usuário não tem como adivinhar isso.
        self.google_warning_row = Adw.ActionRow()
        self.google_warning_icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        self.google_warning_row.add_prefix(self.google_warning_icon)
        self.google_warning_row.set_visible(False)
        self.google_group.add(self.google_warning_row)

        self.google_service_rows: dict[str, Adw.ActionRow] = {}
        self.google_service_switches: dict[str, Gtk.Switch] = {}
        self.google_service_buttons: dict[str, Gtk.Button] = {}

        for service in google_services():
            row = Adw.ActionRow(title=SERVICE_LABELS.get(service, service))

            switch = Gtk.Switch(valign=Gtk.Align.CENTER)
            switch.set_active(self._google_service_enabled(service))
            switch.connect("notify::active", self._on_google_service_toggled, service)
            row.add_suffix(switch)

            button = Gtk.Button(valign=Gtk.Align.CENTER)
            button.add_css_class("pill")
            button.connect("clicked", self._on_google_connect_clicked, service)
            row.add_suffix(button)

            self.google_group.add(row)
            self.google_service_rows[service] = row
            self.google_service_switches[service] = switch
            self.google_service_buttons[service] = button

        self._refresh_google_rows()
        page.add(self.google_group)

    def _google_service_enabled(self, service: str) -> bool:
        """Espelha a regra do domínio — importada, não reescrita.

        Usa `google_auth.service_enabled`, o **mesmo** predicado que o agente
        consulta. Uma cópia local divergiria em silêncio: a tela mostraria
        "ligado" com o agente recusando.
        """
        from ..core import google_auth

        return google_auth.service_enabled(
            service,
            bool(getattr(self.config, "google_enabled", False)),
            getattr(self.config, "google_enabled_services", None),
        )

    def _google_expiry_notice(self) -> tuple[str, str, str] | None:
        """(título, subtítulo, ícone) do aviso de expiração, ou `None`.

        A idade vem do que o app registrou no `connect`; a Google não informa
        quando o refresh expira. Quando a idade é desconhecida, o aviso DIZ isso
        em vez de sumir — silêncio ali seria a falha que não se vê.
        """
        from ..core import google_auth

        warn_days = int(getattr(self.config, "google_refresh_warning_days", 7) or 0)
        avisos = google_auth.refresh_expiry_warnings(warn_days=warn_days)
        if not avisos:
            return None

        primeiro = avisos[0]
        severidade = primeiro["severity"]
        if severidade == "warning":
            titulo = "Autorização expirada"
            icone = "dialog-warning-symbolic"
        elif severidade == "unknown":
            titulo = "Expiração da autorização (data desconhecida)"
            icone = "dialog-question-symbolic"
        else:
            titulo = "Expiração da autorização"
            icone = "dialog-information-symbolic"

        restantes = len(avisos) - 1
        subtitulo = primeiro["message"]
        if restantes > 0:
            subtitulo += f" (vale para mais {restantes} serviço(s))"
        return titulo, subtitulo, icone

    def _refresh_google_rows(self) -> None:
        """Recalcula subtítulo e botão de cada serviço a partir do estado real."""
        from ..core import google_auth

        master = self.google_switch_row.get_active()
        status = {row["service"]: row for row in google_auth.connection_status()}

        for service, row in self.google_service_rows.items():
            info = status.get(service, {})
            connected = bool(info.get("connected"))

            switch = self.google_service_switches[service]
            switch.set_sensitive(master)

            button = self.google_service_buttons[service]
            if connected:
                row.set_subtitle("Conectado")
                button.set_label("Desconectar")
                button.remove_css_class("suggested-action")
                button.set_sensitive(True)
            else:
                # Habilitado mas sem credencial utilizável = precisa reconectar.
                # Distinguir isto de "nunca conectado" é o que evita o usuário
                # descobrir a expiração só quando um comando falha.
                wants_it = master and self._google_service_enabled(service)
                row.set_subtitle("Reconexão necessária" if wants_it else "Não conectado")
                button.set_label("Conectar")
                button.add_css_class("suggested-action")
                button.set_sensitive(master)

        aviso = self._google_expiry_notice()
        if aviso is None:
            self.google_warning_row.set_visible(False)
        else:
            titulo, subtitulo, icone = aviso
            self.google_warning_row.set_title(titulo)
            self.google_warning_row.set_subtitle(subtitulo)
            self.google_warning_icon.set_from_icon_name(icone)
            self.google_warning_row.set_visible(True)

    def _on_google_master_toggled(self, row: Adw.SwitchRow, _pspec) -> None:
        if self._loading_google:
            return
        self.config.google_enabled = bool(row.get_active())
        self._refresh_google_rows()

    def _on_google_service_toggled(self, switch: Gtk.Switch, _pspec, service: str) -> None:
        """Liga/desliga um serviço, materializando a allowlist quando ela está vazia.

        Lista vazia significa "sem restrição por serviço". Ao mexer num serviço
        ela vira explícita com os **demais ligados** — senão desligar um apagaria
        a permissão dos outros sem o usuário ter pedido.
        """
        if self._loading_google:
            return
        allowed = [str(s) for s in (getattr(self.config, "google_enabled_services", []) or [])]
        if not allowed:
            allowed = list(google_services())
        if switch.get_active():
            if service not in allowed:
                allowed.append(service)
        else:
            allowed = [s for s in allowed if s != service]
        self.config.google_enabled_services = allowed
        self._refresh_google_rows()

    def _on_google_connect_clicked(self, button: Gtk.Button, service: str) -> None:
        """Conecta (fluxo OAuth) ou desconecta o serviço."""
        from ..core import google_auth

        label = SERVICE_LABELS.get(service, service)

        if google_auth.is_connected(service):
            google_auth.disconnect(service)
            self._refresh_google_rows()
            self.add_toast(Adw.Toast.new(f"{label} desconectado."))
            return

        # O fluxo OAuth abre o navegador e BLOQUEIA até o consentimento. Rodar isso
        # no laço do GTK congelaria a janela, então vai para uma thread e o
        # resultado volta por `GLib.idle_add` (único lugar onde tocar em widget é
        # seguro). Mesmo padrão do teste de conexão de provedor.
        button.set_sensitive(False)
        button.set_label("Aguardando…")

        def work() -> None:
            try:
                result = google_auth.run_connect_flow(service)
                ok, message = True, f"{result['label']} conectado."
            except Exception as exc:
                ok, message = False, str(exc).splitlines()[0]
            GLib.idle_add(self._after_google_connect, ok, message)

        threading.Thread(target=work, daemon=True, name=f"google-connect-{service}").start()

    def _after_google_connect(self, ok: bool, message: str) -> bool:
        self._refresh_google_rows()
        self.add_toast(Adw.Toast.new(message))
        return GLib.SOURCE_REMOVE

    def _build_mcp_page(self, page: Adw.PreferencesPage) -> None:
        # A página é guardada porque os grupos de servidores são recriados a cada
        # recarga: a categoria de cada servidor é **derivada** dos dados (ver
        # `mcp/categories.py`), então um servidor recém-adicionado pode cair num
        # grupo que ainda não existe.
        self._mcp_page = page

        # 1. Configuração Principal
        mcp_group = Adw.PreferencesGroup(
            title="Protocolo de Contexto do Modelo (MCP)",
            description="Extensibilidade padronizada para conectar o Zorin Copilot a ferramentas externas (Git, SQLite, Docker, APIs).",
        )
        self.mcp_switch_row = Adw.SwitchRow(
            title="Habilitar Extensões MCP",
            subtitle="Disponibiliza ferramentas de servidores externos para o Modo Agente e o Chat.",
        )
        mcp_group.add(self.mcp_switch_row)

        # "Adicionar servidor…" vem ANTES de "Editar JSON" de propósito. As duas
        # portas escrevem no mesmo arquivo, mas a ordem diz qual é a porta: editar
        # JSON cru não é um caminho para quem não é dev, é um beco. O arquivo
        # continua sendo a fonte da verdade — só deixa de ser a única entrada.
        add_row = Adw.ActionRow(
            title="Adicionar servidor…",
            subtitle="Nome e comando bastam; a gravação vai para o arquivo abaixo.",
        )
        add_btn = Gtk.Button(label="Adicionar", valign=Gtk.Align.CENTER)
        add_btn.add_css_class("pill")
        add_btn.connect("clicked", self._on_add_mcp_server)
        add_row.add_suffix(add_btn)
        mcp_group.add(add_row)

        config_row = Adw.ActionRow(
            title="Arquivo de Configuração (JSON)",
            subtitle=DEFAULT_MCP_CONFIG_PATH,
        )
        open_cfg_btn = Gtk.Button(label="Editar JSON", valign=Gtk.Align.CENTER)
        open_cfg_btn.add_css_class("pill")
        open_cfg_btn.connect("clicked", self._on_open_mcp_config)
        config_row.add_suffix(open_cfg_btn)

        reload_btn = Gtk.Button(label="Recarregar", valign=Gtk.Align.CENTER)
        reload_btn.add_css_class("suggested-action")
        reload_btn.add_css_class("pill")
        reload_btn.connect("clicked", self._on_reload_mcp_servers)
        config_row.add_suffix(reload_btn)

        mcp_group.add(config_row)
        page.add(mcp_group)

        # 2. Servidores Configurados — os grupos nascem em `_populate_mcp_servers`,
        #    um por categoria, porque a categoria vem dos dados.
        self._mcp_groups: list[Adw.PreferencesGroup] = []
        self._populate_mcp_servers()

    def _build_smart_home_page(self, page: Adw.PreferencesPage) -> None:
        ha_group = Adw.PreferencesGroup(
            title="Home Assistant (IoT)",
            description="Integração local para controle de lâmpadas (Avant Neo / Tuya), ar-condicionado e tomadas.",
        )

        self.ha_switch_row = Adw.SwitchRow(
            title="Habilitar Casa Inteligente",
            subtitle="Permite ao Gemini Live e ao agente ReAct controlar aparelhos via Home Assistant.",
        )
        ha_group.add(self.ha_switch_row)

        self.ha_url_row = Adw.EntryRow(title="URL do Home Assistant")
        ha_group.add(self.ha_url_row)

        self.ha_token_row = Adw.PasswordEntryRow(title="Token de Acesso (Long-Lived Access Token)")
        ha_group.add(self.ha_token_row)

        test_row = Adw.ActionRow(
            title="Testar Conexão",
            subtitle="Verifica se o container ou servidor local está acessível e autenticado.",
        )
        self.ha_test_btn = Gtk.Button(label="Testar", valign=Gtk.Align.CENTER)
        self.ha_test_btn.add_css_class("pill")
        self.ha_test_btn.connect("clicked", self._on_test_ha_connection)
        test_row.add_suffix(self.ha_test_btn)
        ha_group.add(test_row)

        self.ha_default_light_row = Adw.EntryRow(
            title="Entidade de Luz Padrão (Opcional)",
        )
        ha_group.add(self.ha_default_light_row)

        self.ha_realtime_row = Adw.SwitchRow(
            title="Eventos em Tempo Real (WebSocket)",
            subtitle=(
                "Mantém uma conexão aberta para o Copilot saber das mudanças de estado na hora "
                "(sem ficar perguntando). Habilita reações e o comando de voz 'o que mudou na casa?'."
            ),
        )
        ha_group.add(self.ha_realtime_row)

        self.ha_announce_row = Adw.SwitchRow(
            title="Anunciar Eventos em Voz Alta",
            subtitle=(
                "O Copilot fala sozinho quando algo muda na casa (porta que abre, luz que "
                "acende). Exige os eventos em tempo real ligados. Só entidades com frase "
                "conhecida são anunciadas, e o anúncio fica em silêncio durante uma conversa por voz."
            ),
        )
        ha_group.add(self.ha_announce_row)

        self.ha_announce_entities_row = Adw.EntryRow(
            title="Anunciar Somente Estas Entidades (Opcional)",
        )
        ha_group.add(self.ha_announce_entities_row)

        page.add(ha_group)

        # ---------------------------------------------------------------------
        # Política de atenção (`core/attention.py`)
        #
        # Grupo próprio, e não mais quatro linhas no grupo acima: isto responde a
        # uma pergunta diferente. O grupo de cima diz *o que o Copilot vê*; este
        # diz *quando ele pode interromper* — e é a peça que decide se a presença
        # será útil ou insuportável.
        #
        # Governa só os anúncios daqui. Mensagem pedida de fora (`--announce`,
        # vinda da ponte HA) não passa por esta política: alguém a escreveu à mão
        # numa automação, então ela é para ser dita.
        # ---------------------------------------------------------------------
        attention_group = Adw.PreferencesGroup(
            title="Política de Atenção",
            description=(
                "Decide quando vale interromper. Sem isto, cada mudança da casa vira "
                "uma fala — e o assistente vira um alarme."
            ),
        )
        self.attention_row = Adw.SwitchRow(
            title="Interromper Só Quando Fizer Sentido",
            subtitle=(
                "Fora da janela de silêncio ou acima do limite de falas, o evento vira "
                "aviso na tela ou marca para depois, em vez de ser falado. Desligado, "
                "todo evento com frase conhecida é falado na hora."
            ),
        )
        self.attention_row.connect(
            "notify::active",
            lambda row, _p: self._set_attention_rows_sensitive(row.get_active()),
        )
        attention_group.add(self.attention_row)

        self.attention_silence_row = Adw.EntryRow(title="Horas de Silêncio (ex.: 23-7, 13-14)")
        self.attention_silence_row.set_tooltip_text(
            "Intervalos de hora em que o Copilot não fala nem avisa, separados por vírgula. "
            "Um intervalo que atravessa a meia-noite é escrito com o fim menor que o início "
            "(23-7 cobre das 23h às 6h59). A hora do fim não conta. Vazio desliga o silêncio. "
            "Evento de segurança — fumaça, gás, água — fala mesmo assim."
        )
        attention_group.add(self.attention_silence_row)

        self.attention_budget_row = Adw.SpinRow.new_with_range(0, 60, 1)
        self.attention_budget_row.set_title("Máximo de Falas por Hora")
        self.attention_budget_row.set_subtitle(
            "Teto de falas na última hora. O que passar disso fica marcado para depois. "
            "Zero não fala nada."
        )
        attention_group.add(self.attention_budget_row)

        # `attention_away_minutes` **não** tem linha aqui, de propósito — mesma
        # razão de `memory_consolidation_interval_hours` não ter: hoje não existe
        # fonte de ociosidade nesta máquina (`GetSessionIdleTime` não responde;
        # mutter, gnome-screensaver e logind não estão no barramento), então
        # `_attention_context` manda a ociosidade como desconhecida e a condição
        # de ausência é **pulada**. Um controle que não muda nada é pior do que
        # um controle ausente. A chave continua na config para que os testes
        # possam exercitá-la, e ela volta para esta tela quando houver fonte.
        page.add(attention_group)

        # Prévia de estado: o grupo é criado uma vez e preenchido quando o
        # "Testar" dá certo. Começa invisível — um grupo vazio com título
        # prometeria uma informação que ainda não existe.
        self.ha_preview_group = Adw.PreferencesGroup(
            title="Entidades descobertas",
            description="Amostra do que o Copilot enxerga nesta casa. É leitura: nada aqui é acionável.",
        )
        self.ha_preview_group.set_visible(False)
        page.add(self.ha_preview_group)
        self._ha_preview_rows: list[Gtk.Widget] = []

        # Grupo: Instruções Docker
        docker_group = Adw.PreferencesGroup(
            title="Instalação Local via Docker",
            description="Comando pronto para iniciar o Home Assistant Container localmente na sua máquina.",
        )
        docker_cmd = (
            "docker run -d --name homeassistant --privileged --restart=unless-stopped "
            "-e TZ=America/Sao_Paulo -v ~/.homeassistant:/config --network=host "
            "ghcr.io/home-assistant/home-assistant:stable"
        )
        docker_row = Adw.ActionRow(
            title="Iniciar Container Docker",
            subtitle="Disponível em http://localhost:8123",
        )
        copy_docker_btn = Gtk.Button(label="Copiar Comando", valign=Gtk.Align.CENTER)
        copy_docker_btn.add_css_class("pill")

        def _on_copy_docker(_btn: Gtk.Button) -> None:
            try:
                display = Gdk.Display.get_default()
                if display:
                    display.get_clipboard().set(docker_cmd)
                    self.add_toast(Adw.Toast.new("Comando Docker copiado para a área de transferência!"))
            except Exception as exc:
                self.add_toast(Adw.Toast.new(f"Falha ao copiar: {exc}"))

        copy_docker_btn.connect("clicked", _on_copy_docker)
        docker_row.add_suffix(copy_docker_btn)
        docker_group.add(docker_row)
        page.add(docker_group)

    def _set_attention_rows_sensitive(self, active: bool) -> None:
        """Apaga os ajustes finos quando a política está desligada.

        Mesma regra de `consolidation_days_row`: um campo ativo que não muda nada
        promete um efeito que não existe. Desligada a política, nenhuma das duas
        condições é consultada.
        """
        self.attention_silence_row.set_sensitive(active)
        self.attention_budget_row.set_sensitive(active)

    def _on_test_ha_connection(self, _btn: Gtk.Button) -> None:
        url = self.ha_url_row.get_text().strip() or "http://localhost:8123"
        token = self.ha_token_row.get_text().strip()
        self.ha_test_btn.set_sensitive(False)

        def run_test():
            from ..core.home_assistant import HomeAssistantManager

            ha = HomeAssistantManager(url=url, token=token)
            res = ha.test_connection()
            msg = res.get("message", "Sem resposta")
            # Só busca as entidades se a conexão subiu: com 401 ou host fora do
            # ar, a chamada seguinte gastaria outro tempo limite para devolver
            # exatamente a mesma falha.
            states = ha.get_states() if res.get("success") else []

            def update_ui():
                self.ha_test_btn.set_sensitive(True)
                self._render_ha_entities(states)
                self.add_toast(Adw.Toast.new(msg))
                return GLib.SOURCE_REMOVE

            GLib.idle_add(update_ui)

        threading.Thread(target=run_test, daemon=True).start()

    def _render_ha_entities(self, states: list[dict]) -> None:
        """Preenche a amostra de entidades; sem entidades, o grupo se esconde.

        Esconder em vez de mostrar "nenhuma entidade" é deliberado: quem testou
        uma conexão que falhou já recebeu o motivo no aviso, e um grupo vazio
        logo abaixo repetiria a mesma notícia em outro lugar.
        """
        for row in list(getattr(self, "_ha_preview_rows", [])):
            self.ha_preview_group.remove(row)
        self._ha_preview_rows = []

        entities = select_preview_entities(states)
        if not entities:
            self.ha_preview_group.set_visible(False)
            return

        row = Adw.PreferencesRow()
        row.set_activatable(False)

        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(3)
        flow.set_row_spacing(6)
        flow.set_column_spacing(6)
        flow.set_margin_top(6)
        flow.set_margin_bottom(6)
        flow.set_margin_start(6)
        flow.set_margin_end(6)

        for state in entities:
            chip = Gtk.Label(label=describe_entity_chip(state))
            chip.add_css_class("entity-chip")
            chip.set_valign(Gtk.Align.CENTER)
            # O `entity_id` vai no tooltip: o nome amigável é o que se lê, o id é
            # o que se digita no campo "Entidade de Luz Padrão" logo acima.
            chip.set_tooltip_text(str(state.get("entity_id") or ""))
            flow.append(chip)

        row.set_child(flow)
        self.ha_preview_group.add(row)
        self._ha_preview_rows.append(row)
        self.ha_preview_group.set_visible(True)

    def _submit_new_mcp_server(
        self, name: str, command: str, args_text: str = ""
    ) -> tuple[bool, str]:
        """Valida e grava um servidor novo. Devolve ``(ok, mensagem)``.

        Separado do diálogo de propósito: é aqui que mora a regra, e assim ela é
        testável sem construir nem apresentar uma janela.
        """
        try:
            args = shlex.split(args_text) if args_text.strip() else []
        except ValueError as exc:
            # Aspas desbalanceadas fazem `shlex` levantar. É erro de digitação, e
            # a mensagem do `shlex` já diz onde — repetir em português pioraria.
            return False, f"Argumentos inválidos: {exc}"

        mgr = (
            getattr(self, "_mcp_manager", None)
            or getattr(self.copilot_parent, "mcp_manager", None)
            or MCPManager()
        )
        return mgr.add_server(name, command, args=args)

    def _on_add_mcp_server(self, _btn: Gtk.Button) -> None:
        """Formulário curto para acrescentar um servidor sem editar o JSON."""
        dialog = Adw.Dialog()
        dialog.set_title("Adicionar servidor MCP")
        dialog.set_content_width(460)

        header = Adw.HeaderBar()
        confirm_btn = Gtk.Button(label="Adicionar")
        confirm_btn.add_css_class("suggested-action")
        header.pack_end(confirm_btn)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            description=(
                "O servidor é gravado no arquivo de configuração e iniciado na hora. "
                "Para ver as ferramentas, expanda a linha dele na lista."
            ),
        )
        name_row = Adw.EntryRow(title="Nome")
        command_row = Adw.EntryRow(title="Comando")
        args_row = Adw.EntryRow(title="Argumentos (opcional)")
        for row in (name_row, command_row, args_row):
            group.add(row)
        page.add(group)
        toolbar.set_content(page)
        dialog.set_child(toolbar)

        def on_confirm(*_args) -> None:
            ok, message = self._submit_new_mcp_server(
                name_row.get_text(), command_row.get_text(), args_row.get_text()
            )
            self.add_toast(Adw.Toast.new(message))
            if ok:
                dialog.close()
                self._populate_mcp_servers()

        confirm_btn.connect("clicked", on_confirm)
        # Enter em qualquer campo confirma: são três campos curtos, e obrigar a
        # mirar o botão depois do último é atrito sem motivo.
        for row in (name_row, command_row, args_row):
            row.connect("entry-activated", on_confirm)

        dialog.present(self)

    def _on_open_mcp_config(self, _btn: Gtk.Button) -> None:
        import subprocess
        try:
            subprocess.Popen(["xdg-open", DEFAULT_MCP_CONFIG_PATH])
        except Exception as exc:
            self.add_toast(Adw.Toast.new(f"Erro ao abrir arquivo: {exc}"))

    def _on_dialog_closed(self, *args) -> None:
        self._is_closed = True
        try:
            from ..ai.voice_preview import VoicePreviewService
            VoicePreviewService.get_default().stop()
        except Exception:
            pass
        mgr = getattr(self, "_mcp_manager", None)
        if mgr and getattr(self, "_mcp_change_listener", None):
            mgr.remove_change_listener(self._mcp_change_listener)
            self._mcp_change_listener = None

    def _on_mcp_changed(self) -> None:
        if not getattr(self, "_is_closed", False):
            GLib.idle_add(self._populate_mcp_servers)

    def _on_reload_mcp_servers(self, _btn: Gtk.Button) -> None:
        try:
            mgr = getattr(self, "_mcp_manager", None) or getattr(self.copilot_parent, "mcp_manager", None) or MCPManager()
            mgr.reload()
            self._populate_mcp_servers()
            self.add_toast(Adw.Toast.new("Servidores MCP recarregados!"))
        except Exception as exc:
            self.add_toast(Adw.Toast.new(f"Erro ao recarregar MCP: {exc}"))

    def _populate_mcp_servers(self) -> bool:
        if getattr(self, "_is_closed", False):
            return GLib.SOURCE_REMOVE

        mgr = getattr(self, "_mcp_manager", None) or getattr(self.copilot_parent, "mcp_manager", None) or MCPManager()
        summary = mgr.get_status_summary()

        # Os grupos são recriados a cada recarga. A categoria é derivada dos dados,
        # então um servidor recém-adicionado pode cair num grupo que ainda não
        # existe; recriar é mais simples — e mais difícil de errar — do que mover
        # linhas entre grupos que podem deixar de existir.
        page = getattr(self, "_mcp_page", None)
        for group in getattr(self, "_mcp_groups", []):
            if page is not None:
                page.remove(group)
        self._mcp_groups = []
        self._mcp_rows.clear()

        if not summary:
            group = Adw.PreferencesGroup(title="Servidores Configurados")
            empty_row = Adw.ActionRow(
                title="Nenhum servidor MCP configurado",
                subtitle="Use 'Adicionar servidor…' acima, ou edite o JSON para configurar vários de uma vez.",
            )
            group.add(empty_row)
            self._mcp_rows.append(empty_row)
            self._append_mcp_group(group)
            return GLib.SOURCE_REMOVE

        # Um grupo por categoria. Antes era uma lista plana: seis linhas com a
        # mesma silhueta, sem responder à primeira pergunta de quem vai ligar um
        # servidor — o que isto alcança?
        for category, items in group_by_category(summary):
            group = Adw.PreferencesGroup(
                title=CATEGORY_LABELS.get(category, category),
                description=CATEGORY_DESCRIPTIONS.get(category, ""),
            )
            for item in items:
                row = self._build_mcp_server_row(item, mgr)
                group.add(row)
                self._mcp_rows.append(row)
            self._append_mcp_group(group)

        return GLib.SOURCE_REMOVE

    def _append_mcp_group(self, group: Adw.PreferencesGroup) -> None:
        page = getattr(self, "_mcp_page", None)
        if page is not None:
            page.add(group)
        self._mcp_groups.append(group)

    def _build_mcp_server_row(self, item: dict, mgr: MCPManager) -> Adw.ExpanderRow:
        """Uma linha de servidor: estado na linha fechada, ferramentas no expansor."""
        name = item["name"]
        tool_cnt = item["tool_count"]
        state = item["state"]
        desc = item["description"] or item["command"]
        error = item.get("error", "")
        tool_details = item.get("tool_details", [])

        if state == "connected":
            icon_name = "emblem-ok-symbolic"
            css_class = "success"
            state_label = "Conectado"
        elif state in ("connecting", "restarting"):
            icon_name = "process-working-symbolic"
            css_class = "warning"
            state_label = "Conectando..." if state == "connecting" else "Reiniciando..."
        elif state == "error":
            icon_name = "dialog-error-symbolic"
            css_class = "error"
            state_label = "Erro"
        elif state == "disabled":
            icon_name = "media-playback-stop-symbolic"
            css_class = "dim-label"
            state_label = "Desativado"
        else:
            icon_name = "network-offline-symbolic"
            css_class = "dim-label"
            state_label = state.capitalize()

        trans_tag = "SSE Remoto" if item.get("transport") == "sse" else "Local"
        if error:
            # O erro assume a linha fechada no lugar da descrição. O subtítulo do
            # expansor tem UMA linha, e antes o aviso vinha por último — depois da
            # descrição inteira. Era o primeiro trecho a ser cortado, justamente o
            # mais urgente. A descrição reaparece dentro do expansor.
            sub_text = f"{state_label}: {error}"
        elif state == "connected":
            sub_text = f"{desc} • {trans_tag} • {tool_cnt} ferramenta(s)"
        else:
            sub_text = f"{desc} • {trans_tag} • {state_label}"

        expander = Adw.ExpanderRow(
            title=name,
            subtitle=sub_text,
        )

        # `.error` na linha inteira, não só no ícone: assim o aviso pertence à
        # linha que ele descreve, em vez de se dissolver no texto. A classe pinta
        # os rótulos internos porque a cor é herdada (medido em Adw 1.9.4).
        if state == "error":
            expander.add_css_class("error")

        status_icon = Gtk.Image.new_from_icon_name(icon_name)
        status_icon.set_valign(Gtk.Align.CENTER)
        status_icon.add_css_class(css_class)
        expander.add_prefix(status_icon)

        sw = Gtk.Switch(active=item["enabled"], valign=Gtk.Align.CENTER)

        def on_sw_toggled(switch, _gparam, s_name=name):
            mgr.set_server_enabled(s_name, switch.get_active())
            self._populate_mcp_servers()

        sw.connect("notify::active", on_sw_toggled)
        expander.add_suffix(sw)

        if tool_details:
            for tool in tool_details:
                t_row = Adw.ActionRow(
                    title=tool["name"],
                    subtitle=tool.get("description") or "Sem descrição fornecida.",
                )
                t_row.set_subtitle_lines(2)
                t_icon = Gtk.Image.new_from_icon_name("system-run-symbolic")
                t_icon.add_css_class("dim-label")
                t_row.add_prefix(t_icon)
                expander.add_row(t_row)
        elif state == "connected":
            empty_tool_row = Adw.ActionRow(
                title="Nenhuma ferramenta exportada",
                subtitle="Este servidor não registrou ferramentas via tools/list.",
            )
            expander.add_row(empty_tool_row)
        elif state == "error":
            # A descrição saiu da linha fechada para o erro caber nela, então ela
            # reaparece aqui: o expansor não pode ser o lugar onde a informação
            # some. `set_subtitle_lines(2)` porque o aviso real costuma ser uma
            # frase inteira ("requer token em GITHUB_PERSONAL_ACCESS_TOKEN").
            if desc:
                desc_row = Adw.ActionRow(
                    title="O que este servidor faria",
                    subtitle=desc,
                )
                desc_row.set_subtitle_lines(2)
                expander.add_row(desc_row)
            err_tool_row = Adw.ActionRow(
                title="Falha na inicialização",
                subtitle=error or "Verifique o comando configurado ou execute os testes.",
            )
            err_tool_row.set_subtitle_lines(2)
            expander.add_row(err_tool_row)

        return expander

    def _populate_facts_group(self) -> None:
        for r in self.fact_rows:
            self.facts_group.remove(r)
        self.fact_rows.clear()
        self.facts_merge_row = None

        facts = self.memory.get_all_facts()
        if not facts:
            empty_row = Adw.ActionRow(
                title="Nenhum fato memorizado ainda",
                subtitle="Diga 'lembre-se que...' no Copilot para ensinar preferências à IA.",
            )
            self.facts_group.add(empty_row)
            self.fact_rows.append(empty_row)
            return

        # Agrupa por conteúdo normalizado. Duplicatas legadas (gravadas antes de
        # a chave passar a derivar do conteúdo) têm chaves distintas mas o mesmo
        # texto — mostrar uma linha por registro enchia a tela de repetições e
        # multiplicava os alvos de exclusão.
        grouped: dict[str, list[dict]] = {}
        order: list[str] = []
        for f in facts:
            norm = " ".join((f["content"] or "").strip().lower().split())
            if norm not in grouped:
                grouped[norm] = []
                order.append(norm)
            grouped[norm].append(f)

        duplicate_count = sum(len(v) - 1 for v in grouped.values())
        if duplicate_count:
            self.facts_merge_row = self._build_merge_row(duplicate_count)

        for norm in order:
            entries = grouped[norm]
            latest = entries[0]
            title = latest["content"]
            if len(entries) > 1:
                title = f"{title}  (×{len(entries)})"
            row = Adw.ActionRow(
                title=title,
                subtitle=f"Origem: {latest['source']} • Atualizado em {latest['updated_at'][:10]}",
            )
            del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_tooltip_text("Excluir este conhecimento")
            del_btn.set_valign(Gtk.Align.CENTER)
            # Exclui por conteúdo, não por id: senão apagar "uma" duplicata
            # deixaria as outras na lista.
            content = latest["content"]
            del_btn.connect("clicked", lambda _b, c=content: self._on_delete_fact(c))
            row.add_suffix(del_btn)
            self.facts_group.add(row)
            self.fact_rows.append(row)

    def _build_merge_row(self, duplicate_count: int) -> Adw.ActionRow:
        """Linha de ação para consolidar duplicatas legadas em um clique."""
        row = Adw.ActionRow(
            title="Duplicatas encontradas",
            subtitle=f"{duplicate_count} registro(s) repetem um fato já existente.",
        )
        merge_btn = Gtk.Button(label="Mesclar", valign=Gtk.Align.CENTER)
        merge_btn.add_css_class("suggested-action")
        merge_btn.set_tooltip_text("Manter a versão mais recente de cada fato e remover as repetidas")
        merge_btn.connect("clicked", self._on_merge_duplicate_facts)
        row.add_suffix(merge_btn)
        self.facts_group.add(row)
        self.fact_rows.append(row)
        return row

    def _on_merge_duplicate_facts(self, _btn: Gtk.Button) -> None:
        removed = self.memory.dedupe_facts()
        self._populate_facts_group()
        self.add_toast(Adw.Toast.new(f"{removed} registro(s) duplicado(s) removido(s)."))

    def _on_delete_fact(self, content: str) -> None:
        self.memory.delete_facts_by_content(content)
        self._populate_facts_group()
        toast = Adw.Toast.new("Conhecimento removido da base.")
        self.add_toast(toast)

    def _on_clear_memory(self, _btn: Gtk.Button) -> None:
        self.memory.clear_all()
        self._populate_facts_group()
        toast = Adw.Toast.new("Base de conhecimento e histórico limpos!")
        self.add_toast(toast)

    def _load_values(self) -> None:
        # Define provedor ativo no controle segmentado
        self.set_active_provider(self.config.provider)

        # Gemini
        self.gemini_key_row.set_text(self.config.gemini_api_key)
        if self.config.gemini_model in self.gemini_models_list[:-1]:
            m_idx = self.gemini_models_list.index(self.config.gemini_model)
            self.gemini_model_row.set_selected(m_idx)
            self.gemini_custom_model_row.set_visible(False)
        else:
            custom_idx = len(self.gemini_models_list) - 1
            self.gemini_model_row.set_selected(custom_idx)
            self.gemini_custom_model_row.set_text(self.config.gemini_model)
            self.gemini_custom_model_row.set_visible(True)

        # Gemini Live Model
        live_model = getattr(self.config, "gemini_live_model", DEFAULT_GEMINI_LIVE_MODEL)
        live_ids = [m[0] for m in GEMINI_LIVE_MODEL_CHOICES]
        if live_model in live_ids:
            lm_idx = live_ids.index(live_model)
            self.gemini_live_model_row.set_selected(lm_idx)
            self.gemini_custom_live_model_row.set_visible(False)
        else:
            custom_lm_idx = len(self.gemini_live_models_list) - 1
            self.gemini_live_model_row.set_selected(custom_lm_idx)
            self.gemini_custom_live_model_row.set_text(live_model)
            self.gemini_custom_live_model_row.set_visible(True)

        # Gemini Live Voice
        live_voice = getattr(self.config, "gemini_live_voice", DEFAULT_GEMINI_LIVE_VOICE)
        voice_ids = [v[0] for v in GEMINI_LIVE_VOICE_CHOICES]
        if live_voice in voice_ids:
            v_idx = voice_ids.index(live_voice)
            self.gemini_live_voice_row.set_selected(v_idx)
        else:
            self.gemini_live_voice_row.set_selected(0)

        # Identidade do assistente (ver `core/identity.py`)
        identidade = AssistantIdentity(
            name=getattr(self.config, "assistant_name", "") or "",
            local_voice=getattr(self.config, "piper_voice_model", "") or "",
            live_voice=live_voice or "",
        )
        self.assistant_name_row.set_text(identidade.name)
        chaves = [c for c, _ in PT_BR_VOICES]
        if identidade.local_voice in chaves:
            self.local_voice_row.set_selected(chaves.index(identidade.local_voice))
        else:
            # Voz fora da lista ofertada (config editado à mão). Mostrar a **primeira**
            # faria a tela afirmar uma voz que não é a que está em uso — então a linha
            # fica sem seleção e diz o que está configurado.
            self.local_voice_row.set_selected(Gtk.INVALID_LIST_POSITION)
            self.local_voice_row.set_subtitle(
                f"Configurado: {identidade.local_voice} (fora da lista de vozes pt_BR)"
            )
        self._refresh_local_voice_info(identidade.local_voice)

        # WorkBuddy
        self.workbuddy_key_row.set_text(getattr(self.config, "workbuddy_api_key", ""))
        wb_model = getattr(self.config, "workbuddy_model", "hy4-preview")
        if wb_model in self.workbuddy_models_list[:-1]:
            wb_idx = self.workbuddy_models_list.index(wb_model)
            self.workbuddy_model_row.set_selected(wb_idx)
            self.workbuddy_custom_model_row.set_visible(False)
        else:
            custom_wb_idx = len(self.workbuddy_models_list) - 1
            self.workbuddy_model_row.set_selected(custom_wb_idx)
            self.workbuddy_custom_model_row.set_text(wb_model)
            self.workbuddy_custom_model_row.set_visible(True)
        self.workbuddy_url_row.set_text(getattr(self.config, "workbuddy_url", "https://www.workbuddy.ai/v2"))

        # Ollama
        self.ollama_url_row.set_text(self.config.ollama_url)
        self.ollama_model_row.set_text(self.config.ollama_model)
        self.ollama_vision_model_row.set_text(getattr(self.config, "ollama_vision_model", "minicpm-v"))

        # OpenAI
        self.openai_url_row.set_text(self.config.openai_url)
        self.openai_key_row.set_text(self.config.openai_api_key)
        self.openai_model_row.set_text(self.config.openai_model)

        # Pesquisa Web
        self.web_search_row.set_active(self.config.web_search_enabled)

        # Modo Agente e Automação
        self.agent_adaptive_row.set_active(getattr(self.config, "agent_adaptive_budget", True))
        self.agent_steps_row.set_value(float(getattr(self.config, "agent_max_steps", 15)))
        current_autonomy = normalize_autonomy_level(
            getattr(self.config, "autonomy_level", "standard")
        )
        self.autonomy_level_row.set_selected(
            next(
                (
                    i
                    for i, (code, _) in enumerate(self.autonomy_options)
                    if code == current_autonomy
                ),
                1,  # fallback: "Padrão" (índice 1)
            )
        )

        # Atalho Global do Sistema (HUD)
        self.shortcut_switch_row.set_active(self.config.global_shortcut_enabled)
        matching_idx = 0
        for idx, (b_code, _) in enumerate(self.shortcut_options):
            if b_code == self.config.global_shortcut_key:
                matching_idx = idx
                break
        self.shortcut_combo_row.set_selected(matching_idx)

        # Atalho Global Direto de Recorte (Pilar 3)
        self.crop_shortcut_switch_row.set_active(self.config.crop_shortcut_enabled)
        matching_crop_idx = 0
        for idx, (b_code, _) in enumerate(self.crop_shortcut_options):
            if b_code == self.config.crop_shortcut_key:
                matching_crop_idx = idx
                break
        self.crop_shortcut_combo_row.set_selected(matching_crop_idx)

        # Wake word ("palavra de ativação")
        self.wake_word_switch_row.set_active(getattr(self.config, "wake_word_enabled", False))
        phrases = getattr(self.config, "wake_phrases", None) or ["ok copilot", "olá copilot"]
        self.wake_phrases_row.set_phrases(phrases)
        self.wake_model_row.set_text(getattr(self.config, "wake_word_model_path", ""))
        # Ajustes finos — sempre com getattr + default para não quebrar
        # configurações antigas (salvas antes de estes campos existirem).
        self.wake_sensitivity_scale.set_value(
            float(getattr(self.config, "wake_word_sensitivity", 0.5) or 0.0)
        )
        self.wake_cooldown_spin.set_value(
            float(getattr(self.config, "wake_word_cooldown_sec", 4.0) or 0.0)
        )
        self.wake_echo_spin.set_value(
            float(getattr(self.config, "wake_word_echo_delay_sec", 1.2) or 0.0)
        )
        self.wake_vad_row.set_active(bool(getattr(self.config, "wake_word_vad_enabled", True)))
        self.wake_device_row.set_text(str(getattr(self.config, "wake_word_device", "") or ""))

        # Atalho Global de Conversa por Voz
        self.voice_shortcut_switch_row.set_active(getattr(self.config, "voice_shortcut_enabled", True))
        matching_voice_idx = 0
        current_voice_key = getattr(self.config, "voice_shortcut_key", "<Super><Shift>v")
        for idx, (b_code, _) in enumerate(self.voice_shortcut_options):
            if b_code == current_voice_key:
                matching_voice_idx = idx
                break
        self.voice_shortcut_combo_row.set_selected(matching_voice_idx)

        # Estilo do visualizador de áudio
        current_style = getattr(self.config, "voice_visualizer_style", "waves")
        matching_style_idx = 0
        for idx, (s_code, _) in enumerate(self.visualizer_style_options):
            if s_code == current_style:
                matching_style_idx = idx
                break
        self.visualizer_style_combo_row.set_selected(matching_style_idx)

        # Formato de Exibição da Voz (Pílula ou Completo)
        current_overlay = getattr(self.config, "voice_overlay_mode", "pill")
        matching_overlay_idx = 0
        for idx, (o_code, _) in enumerate(self.voice_overlay_options):
            if o_code == current_overlay:
                matching_overlay_idx = idx
                break
        self.voice_overlay_combo_row.set_selected(matching_overlay_idx)

        self.pill_hint_switch_row.set_active(getattr(self.config, "pill_hint_tips", True))

        # Atalho dedicado da pílula flutuante
        self.voice_pill_shortcut_switch_row.set_active(
            getattr(self.config, "voice_pill_shortcut_enabled", False)
        )
        matching_pill_idx = 0
        current_pill_key = getattr(self.config, "voice_pill_shortcut_key", "<Super><Shift>p")
        for idx, (b_code, _) in enumerate(self.voice_pill_shortcut_options):
            if b_code == current_pill_key:
                matching_pill_idx = idx
                break
        self.voice_pill_shortcut_combo_row.set_selected(matching_pill_idx)

        # Ditado Global por Voz no Aplicativo em Foco
        self.dictate_shortcut_switch_row.set_active(getattr(self.config, "dictate_shortcut_enabled", True))
        matching_dictate_idx = 0
        current_dictate_key = getattr(self.config, "dictate_shortcut_key", "<Super><Shift>d")
        for idx, (b_code, _) in enumerate(self.dictate_shortcut_options):
            if b_code == current_dictate_key:
                matching_dictate_idx = idx
                break
        self.dictate_shortcut_combo_row.set_selected(matching_dictate_idx)
        self.dictate_silence_spin.set_value(float(getattr(self.config, "dictate_silence_timeout_sec", 0.9)))
        self.dictate_sound_switch_row.set_active(getattr(self.config, "dictate_sound_effects", True))
        self.dictate_polish_switch_row.set_active(getattr(self.config, "dictate_smart_polish", True))
        self.context_awareness_switch_row.set_active(getattr(self.config, "context_awareness_enabled", True))

        # Autostart com o Sistema
        is_auto = AutostartManager.is_enabled() or getattr(self.config, "autostart_enabled", False)
        self.autostart_switch_row.set_active(is_auto)

        # Documentos e Privacidade
        self.mask_pii_switch_row.set_active(getattr(self.config, "mask_pii", True))
        self.rag_local_only_switch_row.set_active(getattr(self.config, "rag_local_only", False))
        pats = getattr(self.config, "ignored_patterns", [])
        self.ignored_patterns_entry_row.set_text(", ".join(pats))
        self.auto_execute_switch_row.set_active(getattr(self.config, "auto_execute_safe_actions", False))
        self.file_watcher_row.set_active(getattr(self.config, "file_watcher_enabled", True))

        # MCP (Model Context Protocol)
        self.mcp_switch_row.set_active(getattr(self.config, "mcp_enabled", True))

        # Contas Google. O guarda em volta dos `set_active` impede que os handlers
        # reajam e escrevam na config durante a LEITURA (ver `__init__`).
        self._loading_google = True
        try:
            self.google_switch_row.set_active(bool(getattr(self.config, "google_enabled", False)))
            for service, switch in self.google_service_switches.items():
                switch.set_active(self._google_service_enabled(service))
        finally:
            self._loading_google = False
        self._refresh_google_rows()

        # Casa Inteligente (Home Assistant)
        self.ha_switch_row.set_active(getattr(self.config, "ha_enabled", True))
        self.ha_url_row.set_text(getattr(self.config, "ha_url", "http://localhost:8123"))
        self.ha_token_row.set_text(getattr(self.config, "ha_token", ""))
        self.ha_default_light_row.set_text(getattr(self.config, "ha_default_light", ""))
        self.ha_realtime_row.set_active(getattr(self.config, "ha_realtime_enabled", False))
        self.ha_announce_row.set_active(getattr(self.config, "ha_announce_enabled", False))
        self.ha_announce_entities_row.set_text(getattr(self.config, "ha_announce_entities", ""))

        # Política de atenção. `attention_away_minutes` não tem linha (ver
        # `_build_smart_home_page`), então não é lido aqui.
        self.attention_row.set_active(getattr(self.config, "attention_enabled", True))
        self.attention_silence_row.set_text(
            getattr(self.config, "attention_silence_hours", "23-7")
        )
        self.attention_budget_row.set_value(
            getattr(self.config, "attention_max_per_hour", 6)
        )
        self._set_attention_rows_sensitive(self.attention_row.get_active())

        self.consolidation_row.set_active(
            getattr(self.config, "memory_consolidation_enabled", False)
        )
        self.consolidation_days_row.set_value(
            getattr(self.config, "memory_consolidation_days_to_keep", 14)
        )
        # A poda só existe se a consolidação estiver ligada; um campo ativo sem efeito
        # é pior do que um campo escondido.
        self.consolidation_days_row.set_sensitive(self.consolidation_row.get_active())

        self._update_visibility()

    def _on_gemini_model_changed(self, *_args) -> None:
        is_custom = self.gemini_model_row.get_selected() == len(self.gemini_models_list) - 1
        self.gemini_custom_model_row.set_visible(is_custom)

    def _on_gemini_live_model_changed(self, *_args) -> None:
        is_custom = self.gemini_live_model_row.get_selected() == len(self.gemini_live_models_list) - 1
        self.gemini_custom_live_model_row.set_visible(is_custom)

    def _on_workbuddy_model_changed(self, *_args) -> None:
        is_custom = self.workbuddy_model_row.get_selected() == len(self.workbuddy_models_list) - 1
        self.workbuddy_custom_model_row.set_visible(is_custom)

    def _on_preview_live_voice_clicked(self, _btn: Gtk.Button) -> None:
        """Inicia ou interrompe a demonstração em áudio da voz selecionada no combo."""
        from ..ai.voice_preview import VoicePreviewService
        from ..ai.providers import GEMINI_LIVE_VOICE_CHOICES

        service = VoicePreviewService.get_default()
        idx = self.gemini_live_voice_row.get_selected()
        v_name = GEMINI_LIVE_VOICE_CHOICES[idx][0] if 0 <= idx < len(GEMINI_LIVE_VOICE_CHOICES) else "Puck"

        if service.is_playing(v_name):
            service.stop()
            self.gemini_live_voice_prev_btn.set_icon_name("media-playback-start-symbolic")
            return

        service.stop()
        self.gemini_live_voice_prev_btn.set_icon_name("media-playback-stop-symbolic")

        def _done():
            self.gemini_live_voice_prev_btn.set_icon_name("media-playback-start-symbolic")

        service.play_voice(v_name, on_finished=_done)

    def _refresh_local_voice_info(self, voz: str) -> None:
        """Alinha o texto informativo da aba Atalhos com a voz **em uso**.

        Existe porque aquele subtítulo era literal ("pt_BR-faber-medium"): quem
        trocasse de voz continuaria lendo a voz antiga. Texto informativo que não
        acompanha o estado é pior do que texto nenhum — ele é consultado justamente
        por quem quer saber o estado.
        """
        rotulo = next((r for c, r in PT_BR_VOICES if c == voz), voz)
        self.local_voice_info_row.set_subtitle(
            f"Piper TTS ({rotulo}) e faster-whisper na CPU. 0 MB de VRAM gastos."
        )

    def _on_preferences_voice_selected(self, _row, _pspec) -> None:
        """Interrompe áudio anterior se o usuário alterar a voz no combo."""
        try:
            from ..ai.voice_preview import VoicePreviewService
            VoicePreviewService.get_default().stop()
        except Exception:
            pass
        if hasattr(self, "gemini_live_voice_prev_btn"):
            self.gemini_live_voice_prev_btn.set_icon_name("media-playback-start-symbolic")

    def _collect_current_config(self) -> CopilotConfig:
        cfg = CopilotConfig()
        # Identidade (ver `core/identity.py`). O nome é **reatribuído** porque tem
        # linha; a persona é **preservada** porque não tem.
        #
        # A distinção importa: este método parte de um `CopilotConfig()` **novo**, então
        # todo campo que não for reatribuído volta ao default do dataclass. Sem a linha
        # abaixo, salvar qualquer preferência **apagaria** a persona que o usuário
        # escreveu no `config.json` — silenciosamente, e no primeiro clique em
        # "Salvar". Mesmo cuidado que os outros campos sem linha já têm.
        cfg.assistant_name = self.assistant_name_row.get_text().strip()
        cfg.system_prompt = getattr(self.config, "system_prompt", "") or ""
        # `voice_from_selection` trata o índice inválido (voz fora da lista) preservando
        # a configurada, em vez de cair no default — ver `core/identity.py`.
        cfg.piper_voice_model = voice_from_selection(
            self.local_voice_row.get_selected(),
            getattr(self.config, "piper_voice_model", "") or "",
        )
        cfg.provider = self.active_provider()

        # Gemini
        cfg.gemini_api_key = self.gemini_key_row.get_text().strip()
        g_idx = self.gemini_model_row.get_selected()
        if g_idx == len(self.gemini_models_list) - 1:
            cfg.gemini_model = self.gemini_custom_model_row.get_text().strip() or DEFAULT_GEMINI_MODEL
        elif g_idx < len(self.gemini_models_list):
            cfg.gemini_model = self.gemini_models_list[g_idx]
        else:
            cfg.gemini_model = DEFAULT_GEMINI_MODEL

        # Gemini Live Model & Voice
        lm_idx = self.gemini_live_model_row.get_selected()
        if lm_idx == len(self.gemini_live_models_list) - 1:
            cfg.gemini_live_model = self.gemini_custom_live_model_row.get_text().strip() or DEFAULT_GEMINI_LIVE_MODEL
        elif lm_idx < len(GEMINI_LIVE_MODEL_CHOICES):
            cfg.gemini_live_model = GEMINI_LIVE_MODEL_CHOICES[lm_idx][0]
        else:
            cfg.gemini_live_model = DEFAULT_GEMINI_LIVE_MODEL

        v_idx = self.gemini_live_voice_row.get_selected()
        if 0 <= v_idx < len(GEMINI_LIVE_VOICE_CHOICES):
            cfg.gemini_live_voice = GEMINI_LIVE_VOICE_CHOICES[v_idx][0]
        else:
            cfg.gemini_live_voice = DEFAULT_GEMINI_LIVE_VOICE

        # WorkBuddy
        cfg.workbuddy_api_key = self.workbuddy_key_row.get_text().strip()
        wb_idx = self.workbuddy_model_row.get_selected()
        if wb_idx == len(self.workbuddy_models_list) - 1:
            cfg.workbuddy_model = self.workbuddy_custom_model_row.get_text().strip() or "hy4-preview"
        elif wb_idx < len(self.workbuddy_models_list):
            cfg.workbuddy_model = self.workbuddy_models_list[wb_idx]
        else:
            cfg.workbuddy_model = "hy4-preview"
        cfg.workbuddy_url = self.workbuddy_url_row.get_text().strip() or "https://www.workbuddy.ai/v2"

        # Ollama
        cfg.ollama_url = self.ollama_url_row.get_text().strip() or "http://127.0.0.1:11434"
        cfg.ollama_model = self.ollama_model_row.get_text().strip() or "qwen2.5:7b"
        cfg.ollama_vision_model = self.ollama_vision_model_row.get_text().strip() or "minicpm-v"

        # OpenAI
        cfg.openai_url = self.openai_url_row.get_text().strip() or "https://api.openai.com/v1"
        cfg.openai_api_key = self.openai_key_row.get_text().strip()
        cfg.openai_model = self.openai_model_row.get_text().strip() or "gpt-4o-mini"

        # Pesquisa Web
        cfg.web_search_enabled = self.web_search_row.get_active()

        # Modo Agente e Automação
        cfg.agent_adaptive_budget = self.agent_adaptive_row.get_active()
        cfg.agent_max_steps = int(self.agent_steps_row.get_value())
        cfg.agent_max_seconds = float(getattr(self.config, "agent_max_seconds", 300.0))
        # Nível de autonomia. Reatribuição OBRIGATÓRIA: o coletor parte de um
        # `CopilotConfig()` novo, então sem esta linha salvar QUALQUER preferência
        # zeraria a autonomia de volta ao default — o mesmo defeito que já apagou o
        # `system_prompt` de instalações reais. Ver `docs/ANEXO_AUTONOMIA_PREREQUISITOS.md` (B2).
        sel_autonomy = self.autonomy_level_row.get_selected()
        cfg.autonomy_level = (
            self.autonomy_options[sel_autonomy][0]
            if 0 <= sel_autonomy < len(self.autonomy_options)
            else "standard"
        )

        # Atalho Global do Sistema (HUD)
        cfg.global_shortcut_enabled = self.shortcut_switch_row.get_active()
        sel_shortcut = self.shortcut_combo_row.get_selected()
        if 0 <= sel_shortcut < len(self.shortcut_options):
            cfg.global_shortcut_key = self.shortcut_options[sel_shortcut][0]
        else:
            cfg.global_shortcut_key = "<Super>c"

        # Atalho Global Direto de Recorte (Pilar 3)
        cfg.crop_shortcut_enabled = self.crop_shortcut_switch_row.get_active()
        sel_crop = self.crop_shortcut_combo_row.get_selected()
        if 0 <= sel_crop < len(self.crop_shortcut_options):
            cfg.crop_shortcut_key = self.crop_shortcut_options[sel_crop][0]
        else:
            cfg.crop_shortcut_key = "<Super><Shift>s"

        # Wake word ("palavra de ativação")
        cfg.wake_word_enabled = self.wake_word_switch_row.get_active()
        phrases = self.wake_phrases_row.get_phrases()
        cfg.wake_phrases = phrases or ["ok copilot", "olá copilot"]
        cfg.wake_word_model_path = self.wake_model_row.get_text().strip()
        # Ajustes finos. `_collect_current_config` parte de um CopilotConfig()
        # novo, então TODO campo precisa ser reatribuído aqui — do contrário o
        # valor volta ao default (e zera) sempre que o usuário salvar.
        cfg.wake_word_sensitivity = float(self.wake_sensitivity_scale.get_value())
        cfg.wake_word_cooldown_sec = float(self.wake_cooldown_spin.get_value())
        cfg.wake_word_echo_delay_sec = float(self.wake_echo_spin.get_value())
        cfg.wake_word_vad_enabled = bool(self.wake_vad_row.get_active())
        cfg.wake_word_device = self.wake_device_row.get_text().strip()
        cfg.wake_word_match_partials = bool(
            getattr(self.config, "wake_word_match_partials", False)
        )
        cfg.end_session_enabled = bool(getattr(self.config, "end_session_enabled", True))
        cfg.end_session_grace_sec = float(
            getattr(self.config, "end_session_grace_sec", 4.0) or 0.0
        )

        # Atalho Global de Conversa por Voz
        cfg.voice_shortcut_enabled = self.voice_shortcut_switch_row.get_active()
        sel_voice = self.voice_shortcut_combo_row.get_selected()
        if 0 <= sel_voice < len(self.voice_shortcut_options):
            cfg.voice_shortcut_key = self.voice_shortcut_options[sel_voice][0]
        else:
            cfg.voice_shortcut_key = "<Super><Shift>v"

        # Estilo do Visualizador de Áudio
        sel_style = self.visualizer_style_combo_row.get_selected()
        if 0 <= sel_style < len(self.visualizer_style_options):
            cfg.voice_visualizer_style = self.visualizer_style_options[sel_style][0]
        else:
            cfg.voice_visualizer_style = "waves"

        # Formato de Exibição da Voz
        sel_overlay = self.voice_overlay_combo_row.get_selected()
        if 0 <= sel_overlay < len(self.voice_overlay_options):
            cfg.voice_overlay_mode = self.voice_overlay_options[sel_overlay][0]
        else:
            cfg.voice_overlay_mode = "pill"

        # Atalho dedicado da pílula flutuante
        cfg.voice_pill_shortcut_enabled = self.voice_pill_shortcut_switch_row.get_active()
        sel_pill = self.voice_pill_shortcut_combo_row.get_selected()
        if 0 <= sel_pill < len(self.voice_pill_shortcut_options):
            cfg.voice_pill_shortcut_key = self.voice_pill_shortcut_options[sel_pill][0]
        else:
            cfg.voice_pill_shortcut_key = "<Super><Shift>p"

        # Pílula de voz. Estes campos não têm controle dedicado nesta tela, então
        # precisam ser copiados explicitamente — o coletor parte de um
        # CopilotConfig() novo e, sem isto, cada save zeraria posição/estado da
        # pílula para os defaults.
        cfg.pill_monitor_idx = int(getattr(self.config, "pill_monitor_idx", -1))
        cfg.pill_corner = str(getattr(self.config, "pill_corner", "top-center"))
        cfg.pill_pinned = bool(getattr(self.config, "pill_pinned", True))
        cfg.pill_margin = int(getattr(self.config, "pill_margin", 12))
        cfg.pill_idle_timeout_sec = int(getattr(self.config, "pill_idle_timeout_sec", 8))
        cfg.pill_error_autohide_sec = int(getattr(self.config, "pill_error_autohide_sec", 12))
        cfg.pill_hint_tips = self.pill_hint_switch_row.get_active()
        cfg.pill_hint_sec = int(getattr(self.config, "pill_hint_sec", 12))

        # Ditado Global por Voz
        cfg.dictate_shortcut_enabled = self.dictate_shortcut_switch_row.get_active()
        sel_dictate = self.dictate_shortcut_combo_row.get_selected()
        if 0 <= sel_dictate < len(self.dictate_shortcut_options):
            cfg.dictate_shortcut_key = self.dictate_shortcut_options[sel_dictate][0]
        else:
            cfg.dictate_shortcut_key = "<Super><Shift>d"
        cfg.dictate_silence_timeout_sec = float(self.dictate_silence_spin.get_value())
        cfg.dictate_sound_effects = self.dictate_sound_switch_row.get_active()
        cfg.dictate_smart_polish = self.dictate_polish_switch_row.get_active()
        cfg.context_awareness_enabled = self.context_awareness_switch_row.get_active()

        # Autostart
        cfg.autostart_enabled = self.autostart_switch_row.get_active()

        # Documentos e Privacidade
        cfg.mask_pii = self.mask_pii_switch_row.get_active()
        cfg.rag_local_only = self.rag_local_only_switch_row.get_active()
        raw_pats = self.ignored_patterns_entry_row.get_text()
        cfg.ignored_patterns = [p.strip() for p in raw_pats.split(",") if p.strip()]
        cfg.trusted_directories = list(self.config.trusted_directories)
        cfg.quarantine_directories = list(self.config.quarantine_directories)
        cfg.max_file_size_mb = self.config.max_file_size_mb
        cfg.auto_execute_safe_actions = self.auto_execute_switch_row.get_active()
        cfg.file_watcher_enabled = self.file_watcher_row.get_active()

        # MCP (Model Context Protocol)
        cfg.mcp_enabled = self.mcp_switch_row.get_active()
        cfg.mcp_config_path = getattr(self.config, "mcp_config_path", "")
        cfg.mcp_auto_connect = getattr(self.config, "mcp_auto_connect", True)

        # Integrações Google. Agora COM controles: o portão mestre e os toggles por
        # serviço vêm das linhas. Os campos sem linha (caminho do segredo e backend
        # do cofre) seguem preservados do config atual — o coletor parte de um
        # CopilotConfig() novo e, sem isto, salvar QUALQUER preferência os apagaria
        # em silêncio: o mesmo defeito que `system_prompt` já sofreu.
        cfg.google_enabled = self.google_switch_row.get_active()
        cfg.google_enabled_services = [
            service
            for service, switch in self.google_service_switches.items()
            if switch.get_active()
        ]
        cfg.google_client_secret_path = str(getattr(self.config, "google_client_secret_path", "") or "")
        cfg.google_token_backend = str(getattr(self.config, "google_token_backend", "") or "")

        # Casa Inteligente (Home Assistant)
        cfg.ha_enabled = self.ha_switch_row.get_active()
        cfg.ha_url = self.ha_url_row.get_text().strip() or "http://localhost:8123"
        cfg.ha_token = self.ha_token_row.get_text().strip()
        cfg.ha_default_light = self.ha_default_light_row.get_text().strip()
        cfg.ha_realtime_enabled = self.ha_realtime_row.get_active()
        cfg.ha_announce_enabled = self.ha_announce_row.get_active()
        cfg.ha_announce_entities = self.ha_announce_entities_row.get_text().strip()

        # Política de atenção. `attention_away_minutes` é preservado sem passar
        # pela interface (não há linha para ele): reatribuir do config atual é o
        # que impede o valor de voltar ao default a cada salvamento — o mesmo
        # cuidado de `memory_consolidation_interval_hours` abaixo.
        cfg.attention_enabled = self.attention_row.get_active()
        cfg.attention_silence_hours = self.attention_silence_row.get_text().strip()
        cfg.attention_max_per_hour = int(self.attention_budget_row.get_value())
        cfg.attention_away_minutes = getattr(self.config, "attention_away_minutes", 30)

        cfg.memory_consolidation_enabled = self.consolidation_row.get_active()
        cfg.memory_consolidation_days_to_keep = int(self.consolidation_days_row.get_value())
        # O intervalo não tem linha na interface de propósito: "diário" é o desenho, e
        # o valor existe na config para que os testes possam encurtá-lo.
        cfg.memory_consolidation_interval_hours = getattr(
            self.config, "memory_consolidation_interval_hours", 24.0
        )

        return cfg

    def _on_test_connection(self, _btn: Gtk.Button) -> None:
        cfg = self._collect_current_config()
        self.test_spinner.start()

        def run_test():
            if cfg.provider == "hybrid":
                prov = HybridProvider(
                    gemini_provider=GeminiProvider(cfg.gemini_api_key, cfg.gemini_model),
                    ollama_provider=OllamaProvider(cfg.ollama_url, cfg.ollama_model, cfg.ollama_vision_model),
                    mode="hybrid",
                )
            elif cfg.provider == "gemini":
                prov = GeminiProvider(cfg.gemini_api_key, cfg.gemini_model)
            elif cfg.provider == "workbuddy":
                prov = WorkBuddyProvider(cfg.workbuddy_api_key, cfg.workbuddy_model, cfg.workbuddy_url)
            elif cfg.provider == "ollama":
                prov = OllamaProvider(cfg.ollama_url, cfg.ollama_model, cfg.ollama_vision_model)
            else:
                prov = OpenAICompatProvider(cfg.openai_url, cfg.openai_api_key, cfg.openai_model)

            ok, msg = prov.test_connection()

            def update_ui():
                self.test_spinner.stop()
                toast = Adw.Toast.new(msg)
                self.add_toast(toast)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(update_ui)

        threading.Thread(target=run_test, daemon=True).start()

    def _on_save(self, _btn: Gtk.Button) -> None:
        cfg = self._collect_current_config()
        cfg.save()

        # Casa Inteligente: aplica já, sem reiniciar o aplicativo. O gerenciador
        # relê a config por mtime, mas o reset explícito cobre a granularidade de
        # 1s do sistema de arquivos — sem ele, salvar o token poderia só valer no
        # próximo arranque, e o Live continuaria dizendo "não configurado".
        try:
            from ..core.home_assistant import HomeAssistantManager

            HomeAssistantManager.reset()
            if cfg.ha_enabled and cfg.ha_realtime_enabled:
                HomeAssistantManager.get_default().start_realtime()
            else:
                HomeAssistantManager.get_default().stop_realtime()
        except Exception as exc:
            logger.debug("Falha ao reconfigurar Casa Inteligente após salvar: %s", exc)

        # Sincroniza Atalho do HUD
        if cfg.global_shortcut_enabled:
            ShortcutManager.register(cfg.global_shortcut_key)
        else:
            ShortcutManager.unregister()

        # Sincroniza Atalho de Recorte Direto
        if cfg.crop_shortcut_enabled:
            ShortcutManager.register_crop(cfg.crop_shortcut_key)
        else:
            ShortcutManager.unregister_crop()

        # Sincroniza Atalho de Conversa por Voz
        if getattr(cfg, "voice_shortcut_enabled", True):
            ShortcutManager.register_voice(getattr(cfg, "voice_shortcut_key", "<Super><Shift>v"))
        else:
            ShortcutManager.unregister_voice()

        # Sincroniza Atalho Dedicado da Pílula Flutuante
        if getattr(cfg, "voice_pill_shortcut_enabled", False):
            ShortcutManager.register_voice_pill(
                getattr(cfg, "voice_pill_shortcut_key", "<Super><Shift>p")
            )
        else:
            ShortcutManager.unregister_voice_pill()

        # Sincroniza Atalho de Ditado Global
        if getattr(cfg, "dictate_shortcut_enabled", True):
            ShortcutManager.register_dictate(getattr(cfg, "dictate_shortcut_key", "<Super><Shift>d"))
        else:
            ShortcutManager.unregister_dictate()

        # Sincroniza Inicialização com o Sistema (Autostart)
        if cfg.autostart_enabled:
            AutostartManager.enable()
        else:
            AutostartManager.disable()

        if self.on_saved:
            self.on_saved(cfg)
        toast = Adw.Toast.new("Configurações salvas com sucesso!")
        self.add_toast(toast)

        # Fecha depois de o toast aparecer. Antes era
        # `lambda: (self.close(), GLib.SOURCE_REMOVE)[1]` — a tupla servia só
        # para enfiar duas coisas numa lambda, e lia-se como se o retorno fosse
        # o resultado de close().
        def close_after_toast() -> bool:
            self.close()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(700, close_after_toast)
