# Decisão de design: diálogo nativo Libadwaita PreferencesDialog — integração visual perfeita com GNOME 46 / Zorin OS, com suporte a teste em tempo real de credenciais sem travar a interface.

"""Diálogo de configurações e preferências do Zorin Copilot."""

from __future__ import annotations

import threading
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..ai.providers import GeminiProvider, OllamaProvider, OpenAICompatProvider
from ..core.config import CopilotConfig
from ..core.memory import MemoryManager


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
            "Google Gemini (Recomendado / Nuvem)",
            "Ollama (Local / Offline)",
            "OpenAI / Compatível (Groq, OpenRouter)",
        ])
        self.provider_row = Adw.ComboRow(
            title="Motor de IA",
            subtitle="Escolha onde suas perguntas serão processadas",
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
            "gemini-2.5-pro",
            "gemini-2.5-flash",
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
            title="Configuração do Ollama (Local)",
            description="Processamento 100% privado e offline no seu computador.",
        )
        self.ollama_url_row = Adw.EntryRow(title="Endereço do Servidor")
        self.ollama_group.add(self.ollama_url_row)

        self.ollama_model_row = Adw.EntryRow(title="Nome do Modelo (ex: llama3.2, mistral)")
        self.ollama_group.add(self.ollama_model_row)
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
        prov_map = {"gemini": 0, "ollama": 1, "openai": 2}
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

        # OpenAI
        self.openai_url_row.set_text(self.config.openai_url)
        self.openai_key_row.set_text(self.config.openai_api_key)
        self.openai_model_row.set_text(self.config.openai_model)

        self._update_visibility()

    def _on_gemini_model_changed(self, *_args) -> None:
        is_custom = self.gemini_model_row.get_selected() == len(self.gemini_models_list) - 1
        self.gemini_custom_model_row.set_visible(is_custom)

    def _on_provider_changed(self, *_args) -> None:
        self._update_visibility()

    def _update_visibility(self) -> None:
        sel = self.provider_row.get_selected()
        self.gemini_group.set_visible(sel == 0)
        self.ollama_group.set_visible(sel == 1)
        self.openai_group.set_visible(sel == 2)

    def _collect_current_config(self) -> CopilotConfig:
        cfg = CopilotConfig()
        sel = self.provider_row.get_selected()
        cfg.provider = ["gemini", "ollama", "openai"][sel]

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
        cfg.ollama_url = self.ollama_url_row.get_text().strip() or "http://localhost:11434"
        cfg.ollama_model = self.ollama_model_row.get_text().strip() or "llama3.2:latest"

        # OpenAI
        cfg.openai_url = self.openai_url_row.get_text().strip() or "https://api.openai.com/v1"
        cfg.openai_api_key = self.openai_key_row.get_text().strip()
        cfg.openai_model = self.openai_model_row.get_text().strip() or "gpt-4o-mini"

        return cfg

    def _on_test_connection(self, _btn: Gtk.Button) -> None:
        cfg = self._collect_current_config()
        self.test_spinner.start()

        def run_test():
            if cfg.provider == "gemini":
                prov = GeminiProvider(cfg.gemini_api_key, cfg.gemini_model)
            elif cfg.provider == "ollama":
                prov = OllamaProvider(cfg.ollama_url, cfg.ollama_model)
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
        if self.on_saved:
            self.on_saved(cfg)
        toast = Adw.Toast.new("Configurações salvas com sucesso!")
        self.add_toast(toast)
        GLib.timeout_add(700, lambda: (self.close(), GLib.SOURCE_REMOVE)[1])
