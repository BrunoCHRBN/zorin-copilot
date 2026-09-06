# Decisão de design: diálogo nativo Libadwaita PreferencesDialog — integração visual perfeita com GNOME 46 / Zorin OS, com suporte a teste em tempo real de credenciais sem travar a interface.

"""Diálogo de configurações e preferências do Zorin Copilot."""

from __future__ import annotations

import threading
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..ai.providers import GeminiProvider, HybridProvider, OllamaProvider, OpenAICompatProvider
from ..core.config import CopilotConfig
from ..core.memory import MemoryManager
from ..core.shortcuts import AutostartManager, ShortcutManager


class PreferencesDialog(Adw.PreferencesDialog):
    """Diálogo de configurações do Zorin Copilot usando Libadwaita."""

    def __init__(self, parent: Gtk.Window, on_saved: Callable[[CopilotConfig], None] | None = None):
        super().__init__()
        self.set_title("Configurações do Copilot")
        self.on_saved = on_saved
        self.config = CopilotConfig.load()
        self.memory = MemoryManager()

        self._build_ui()
        self._load_values()

    def _build_ui(self) -> None:
        page = Adw.PreferencesPage(title="Inteligência Artificial", icon_name="preferences-system-symbolic")
        self.add(page)

        # ---------------------------------------------------------------------
        # Grupo: Seleção de Provedor
        # ---------------------------------------------------------------------
        provider_group = Adw.PreferencesGroup(title="Provedor Ativo")
        
        self.provider_model = Gtk.StringList.new([
            "Híbrido Inteligente (Gemini + Auto-Failover Local GPU)",
            "Google Gemini (Nuvem / AI Studio)",
            "Ollama (100% Local Offline / RX 7600 GPU)",
            "OpenAI / Compatível (Groq, OpenRouter)",
        ])
        self.provider_row = Adw.ComboRow(
            title="Motor de IA",
            subtitle="Híbrido prioriza Gemini e comuta para Ollama local se a cota esgotar",
            model=self.provider_model,
        )
        self.provider_row.connect("notify::selected", self._on_provider_changed)
        provider_group.add(self.provider_row)
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

        self.gemini_models_list = [
            "gemini-3.8-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
            "gemini-flash-latest",
            "gemini-2.5-pro",
            "Outro (Personalizado)",
        ]
        self.gemini_model_row = Adw.ComboRow(
            title="Modelo Gemini",
            subtitle="gemini-3.8-flash (geração 3.8 / ultrarrápido) ou selecione outro",
            model=Gtk.StringList.new(self.gemini_models_list),
        )
        self.gemini_model_row.connect("notify::selected", self._on_gemini_model_changed)
        self.gemini_group.add(self.gemini_model_row)

        self.gemini_custom_model_row = Adw.EntryRow(title="Nome do Modelo Personalizado")
        self.gemini_custom_model_row.set_visible(False)
        self.gemini_group.add(self.gemini_custom_model_row)

        link_row = Adw.ActionRow(title="Obter chave gratuita")
        link_btn = Gtk.LinkButton(
            label="Abrir Google AI Studio",
            uri="https://aistudio.google.com/app/apikey",
            valign=Gtk.Align.CENTER,
        )
        link_row.add_suffix(link_btn)
        self.gemini_group.add(link_row)
        page.add(self.gemini_group)

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
        page.add(self.ollama_group)

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
        page = Adw.PreferencesPage(title="Atalho & HUD", icon_name="input-keyboard-symbolic")
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

        voice_info_row = Adw.ActionRow(
            title="Voz Local Contínua & Soberana",
            subtitle="Usa Piper TTS (voz masculina brasileira pt_BR-faber-medium) e faster-whisper na CPU. 0 MB de VRAM gastos.",
        )
        voice_group.add(voice_info_row)
        page.add(voice_group)

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

    def _build_documents_privacy_page(self) -> None:
        page = Adw.PreferencesPage(title="Documentos & Privacidade", icon_name="security-high-symbolic")
        self.add(page)

        # 1. Zonas de Confiança
        zones_group = Adw.PreferencesGroup(
            title="Zonas de Confiança de Documentos",
            description="Diretórios locais monitorados para busca e respostas RAG.",
        )

        self.trusted_dirs_row = Adw.ActionRow(
            title="Pastas Confiáveis (Leitura e Síntese)",
            subtitle=", ".join(self.config.trusted_directories) if self.config.trusted_directories else "~/Documentos",
        )
        zones_group.add(self.trusted_dirs_row)

        self.quarantine_dirs_row = Adw.ActionRow(
            title="Zona de Cautela (Downloads)",
            subtitle=", ".join(self.config.quarantine_directories) if self.config.quarantine_directories else "~/Downloads",
        )
        zones_group.add(self.quarantine_dirs_row)
        page.add(zones_group)

        # 2. Blindagem e PII
        privacy_group = Adw.PreferencesGroup(
            title="Blindagem e Proteção de Dados",
            description="Políticas de segurança contra vazamento de dados confidenciais e injeções indiretas.",
        )

        self.mask_pii_switch_row = Adw.SwitchRow(
            title="Mascarar Dados Sensíveis (PII)",
            subtitle="Substitui automaticamente CPFs, cartões e chaves de API por marcadores anônimos antes do envio à nuvem",
        )
        privacy_group.add(self.mask_pii_switch_row)

        self.rag_local_only_switch_row = Adw.SwitchRow(
            title="RAG Local Estrito (Ollama Offline)",
            subtitle="Responde sobre documentos locais exclusivamente via modelo local offline (nenhum dado sai do computador)",
        )
        privacy_group.add(self.rag_local_only_switch_row)

        self.ignored_patterns_entry_row = Adw.EntryRow(
            title="Padrões Ignorados (.copilotignore)",
        )
        privacy_group.add(self.ignored_patterns_entry_row)

        page.add(privacy_group)

    def _build_memory_page(self) -> None:
        page = Adw.PreferencesPage(title="Base de Conhecimento", icon_name="document-properties-symbolic")
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

    def _populate_facts_group(self) -> None:
        for r in self.fact_rows:
            self.facts_group.remove(r)
        self.fact_rows.clear()

        facts = self.memory.get_all_facts()
        if not facts:
            empty_row = Adw.ActionRow(
                title="Nenhum fato memorizado ainda",
                subtitle="Diga 'lembre-se que...' no Copilot para ensinar preferências à IA.",
            )
            self.facts_group.add(empty_row)
            self.fact_rows.append(empty_row)
            return

        for f in facts:
            row = Adw.ActionRow(
                title=f["content"],
                subtitle=f"Origem: {f['source']} • Atualizado em {f['updated_at'][:10]}",
            )
            del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_tooltip_text("Excluir este conhecimento")
            del_btn.set_valign(Gtk.Align.CENTER)
            fact_id = f["id"]
            del_btn.connect("clicked", lambda _b, fid=fact_id: self._on_delete_fact(fid))
            row.add_suffix(del_btn)
            self.facts_group.add(row)
            self.fact_rows.append(row)

    def _on_delete_fact(self, fact_id: int) -> None:
        self.memory.delete_fact(fact_id)
        self._populate_facts_group()
        toast = Adw.Toast.new("Conhecimento removido da base.")
        self.add_toast(toast)

    def _on_clear_memory(self, _btn: Gtk.Button) -> None:
        self.memory.clear_all()
        self._populate_facts_group()
        toast = Adw.Toast.new("Base de conhecimento e histórico limpos!")
        self.add_toast(toast)

    def _load_values(self) -> None:
        # Define provedor ativo no ComboRow
        prov_map = {"hybrid": 0, "gemini": 1, "ollama": 2, "openai": 3}
        self.provider_row.set_selected(prov_map.get(self.config.provider, 0))

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

        # Autostart com o Sistema
        is_auto = AutostartManager.is_enabled() or getattr(self.config, "autostart_enabled", False)
        self.autostart_switch_row.set_active(is_auto)

        # Documentos e Privacidade
        self.mask_pii_switch_row.set_active(getattr(self.config, "mask_pii", True))
        self.rag_local_only_switch_row.set_active(getattr(self.config, "rag_local_only", False))
        pats = getattr(self.config, "ignored_patterns", [])
        self.ignored_patterns_entry_row.set_text(", ".join(pats))

        self._update_visibility()

    def _on_gemini_model_changed(self, *_args) -> None:
        is_custom = self.gemini_model_row.get_selected() == len(self.gemini_models_list) - 1
        self.gemini_custom_model_row.set_visible(is_custom)

    def _on_provider_changed(self, *_args) -> None:
        self._update_visibility()

    def _update_visibility(self) -> None:
        sel = self.provider_row.get_selected()
        # 0: Híbrido (exibe Gemini + Ollama para que o usuário veja ambas configurações)
        # 1: Gemini (apenas Gemini)
        # 2: Ollama (apenas Ollama)
        # 3: OpenAI (apenas OpenAI)
        self.gemini_group.set_visible(sel in (0, 1))
        self.ollama_group.set_visible(sel in (0, 2))
        self.openai_group.set_visible(sel == 3)

    def _collect_current_config(self) -> CopilotConfig:
        cfg = CopilotConfig()
        sel = self.provider_row.get_selected()
        prov_rev = {0: "hybrid", 1: "gemini", 2: "ollama", 3: "openai"}
        cfg.provider = prov_rev.get(sel, "hybrid")

        # Gemini
        cfg.gemini_api_key = self.gemini_key_row.get_text().strip()
        g_idx = self.gemini_model_row.get_selected()
        if g_idx == len(self.gemini_models_list) - 1:
            cfg.gemini_model = self.gemini_custom_model_row.get_text().strip() or "gemini-3.8-flash"
        elif g_idx < len(self.gemini_models_list):
            cfg.gemini_model = self.gemini_models_list[g_idx]
        else:
            cfg.gemini_model = "gemini-3.8-flash"

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
            elif cfg.provider == "ollama":
                prov = OllamaProvider(cfg.ollama_url, cfg.ollama_model, cfg.ollama_vision_model)
            else:
                prov = OpenAICompatProvider(cfg.openai_url, cfg.openai_api_key, cfg.openai_model)

            ok, msg = prov.test_connection()

            def update_ui():
                self.test_spinner.stop()
                icon = "✓" if ok else "✗"
                toast = Adw.Toast.new(f"{icon} {msg}")
                self.add_toast(toast)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(update_ui)

        threading.Thread(target=run_test, daemon=True).start()

    def _on_save(self, _btn: Gtk.Button) -> None:
        cfg = self._collect_current_config()
        cfg.save()

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

        # Sincroniza Inicialização com o Sistema (Autostart)
        if cfg.autostart_enabled:
            AutostartManager.enable()
        else:
            AutostartManager.disable()

        if self.on_saved:
            self.on_saved(cfg)
        toast = Adw.Toast.new("Configurações salvas com sucesso!")
        self.add_toast(toast)
        GLib.timeout_add(700, lambda: (self.close(), GLib.SOURCE_REMOVE)[1])
