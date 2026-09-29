# Decisão de design: armazenamento seguro em ~/.config/zorin-copilot/config.json com suporte a fallback em variáveis de ambiente e permissões restritas.

"""Gerenciamento de configurações do Zorin Copilot."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .identity import is_legacy_auto_prompt


@dataclass
class CopilotConfig:
    provider: str = "gemini"  # "gemini", "hybrid", "ollama", "openai"
    fallback_to_ollama: bool = True
    
    # Configurações do Google Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    # Importante: a BidiGenerateContent (Live API) rejeita aliases como
    # "-latest" para modelos de áudio — precisa ser um model code válido.
    # Modelos de voz oficiais Gemini 3.8 (lançados em setembro/2026):
    # - models/gemini-3.8-live (Baixa latência, conversação contínua fluida)
    # - models/gemini-3.8-live-extended-thinking (Raciocínio profundo e narração de progresso)
    # (https://ai.google.dev/gemini-api/docs/live-api)
    gemini_live_model: str = "models/gemini-3.8-live"
    gemini_live_voice: str = "Puck"  # "Puck", "Aoede", "Charon", "Kore", "Fenrir", "Zephyr", "Callirrhoe", "Sulafat"
    
    # Configurações do Ollama (Local)
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_vision_model: str = "minicpm-v"

    # Grounding Visual Multimodal (VLM Local / Telas sem AT-SPI)
    vlm_grounding_enabled: bool = True
    vlm_grounding_model: str = ""  # vazio = auto-detecta (qwen2.5vl:7b, qwen2.5vl:3b, minicpm-v, etc.)
    vlm_grounding_fallback_to_gemini: bool = True
    vlm_grounding_timeout_sec: float = 18.0
    vlm_confidence_threshold: float = 0.40
    
    # Configurações de API compatível com OpenAI (OpenAI, Groq, DeepSeek, OpenRouter)
    openai_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Configurações do WorkBuddy AI (Tencent Hunyuan / HY4)
    workbuddy_url: str = "https://www.workbuddy.ai/v2"
    workbuddy_api_key: str = ""
    workbuddy_model: str = "hy4-preview"

    # Pesquisa na Web em tempo real
    web_search_enabled: bool = True

    # Modo Tutor Socrático no chat/HUD: quando ativo, o assistente NÃO entrega
    # respostas prontas para conteúdo acadêmico — guia com perguntas e pistas.
    study_tutor: bool = False

    # Atalho Global do Sistema (HUD Summon)
    global_shortcut_enabled: bool = True
    global_shortcut_key: str = "<Super>c"
    
    # Atalho Global Direto de Recorte Inteligente (Pilar 3: Visão Instantânea)
    crop_shortcut_enabled: bool = True
    crop_shortcut_key: str = "<Super><Shift>s"

    # Atalho Global Direto de Conversa por Voz (Fase 4: Live Voice Local)
    voice_shortcut_enabled: bool = True
    voice_shortcut_key: str = "<Super><Shift>v"
    # Atalho dedicado que SEMPRE abre a pílula flutuante, mesmo com
    # voice_overlay_mode="full". Desligado por padrão: não registra atalho global
    # novo sem opt-in explícito do usuário.
    voice_pill_shortcut_enabled: bool = False
    voice_pill_shortcut_key: str = "<Super><Shift>p"

    # Atalho Global Direto de Ditado / Voice Typing no app em foco (Etapa 2)
    dictate_shortcut_enabled: bool = True
    dictate_shortcut_key: str = "<Super><Shift>d"
    dictate_silence_timeout_sec: float = 0.9
    dictate_sound_effects: bool = True
    dictate_smart_polish: bool = True

    # Consciência de Contexto Ativo e Sugestões Dinâmicas (Fase 3)
    context_awareness_enabled: bool = True
    context_suggestions_limit: int = 4

    # Atalho Global de Voz ao Vivo (Fase 3, parte B)
    live_voice_hotkey_enabled: bool = True
    live_voice_hotkey: str = "<Super>v"
    live_voice_jitter_buffer_ms: int = 240  # buffer de pré-carregamento (ms) para absorver jitter
    live_voice_ducking_enabled: bool = True  # atenua envio do mic enquanto a IA fala para evitar corte por eco
    live_voice_interrupt_threshold_rms: float = 2800.0  # volume mínimo (RMS) para interrupção voluntária (barge-in)

    # Isolamento de eco / loopback do microfone (WS2 / Entrega A): enquanto o
    # assistente fala ou processa, o áudio do mic NÃO é enviado ao servidor (eco
    # acústico da sala), salvo interrupção voluntária (voz alta do usuário).
    # Mantém o barge-in. Respeita o ducking acima quando habilitado.
    live_mic_speaker_isolation: bool = True
    # Processamento de captura no source (WS2 / Entrega A): redução de ruído (NS,
    # ex.: RNNoise) e ganho automático (AGC) via PipeWire filter-chain. Best-effort:
    # se o PipeWire ou os módulos não estiverem presentes, cai no microfone padrão.
    live_mic_ns_enabled: bool = True
    live_mic_agc_enabled: bool = True

    # Resumo contínuo da sessão (WS3 / Entrega A): a cada N minutos injeta no
    # contexto do modelo (realtimeInput) um resumo do que rolou na chamada,
    # mantendo-o aterrado em sessões longas. 0 desliga. Só GeminiLiveClient (tem ws).
    live_summary_interval_min: float = 4.0

    # Reconexão automática do gravador de microfone no modo local (Entrega B / P0-4):
    # se o `pw-record`/`arecord` morre (mic desconectado, PipeWire reiniciado) ou a
    # leitura falha, a escuta reabre o gravador com backoff exponencial (1.5s→30s)
    # em vez de encerrar a thread em silêncio. Espelha o wrapper `_live_session` do
    # `GeminiLiveClient`. Só vai a ERROR ao esgotar as tentativas.
    live_local_reconnect_enabled: bool = True
    live_local_reconnect_max_attempts: int = 5

    # Buffer de áudio na queda (Entrega B / P0-4, WS2): segundos de microfone retidos
    # durante a reconexão do Gemini Live e reenviados ao servidor assim que a sessão
    # volta — fecha a lacuna de transcrição do intervalo de backoff. Teto por segundos:
    # sem swap, não pode crescer sem limite. 0 desliga (o áudio do intervalo é descartado).
    live_reconnect_buffer_sec: float = 3.0

    # Watchdog de THINKING (Entrega D / P1-8): quanto esperar pela resposta do modelo
    # depois de devolver as observações das ferramentas, antes de agir. O piso substitui a
    # constante fixa de 12 s; o volume da observação soma tempo, com teto. `max_nudges`
    # é quantas vezes pedimos continuação antes de, como último recurso, liberar o
    # microfone (0 desliga a continuação e libera no primeiro estouro).
    live_thinking_watchdog_base_sec: float = 12.0
    live_thinking_watchdog_max_sec: float = 60.0
    live_thinking_max_nudges: int = 2

    # Wake word ("palavra de ativação") — detecção offline e hands-free
    wake_word_enabled: bool = False
    wake_phrases: list = field(default_factory=lambda: ["ok copilot", "olá copilot"])
    wake_word_model_path: str = ""  # caminho do modelo Vosk (ex.: pt-BR)

    # Ajustes finos da palavra de ativação.
    # O cooldown existe porque o Vosk reemite o mesmo texto várias vezes por
    # segundo enquanto a fala acontece: sem ele, uma única frase "ok copilot"
    # disparava a ativação várias vezes seguidas.
    wake_word_cooldown_sec: float = 4.0  # intervalo mínimo entre duas ativações
    wake_word_echo_delay_sec: float = 1.2  # fica "surdo" após a IA falar (anti-eco)
    wake_word_sensitivity: float = 0.5  # 0..1 (1 = detecta até voz baixa)
    wake_word_vad_enabled: bool = True  # ignora silêncio: menos CPU e menos falsos positivos
    wake_word_device: str = ""  # alvo opcional do pw-record/arecord; vazio = padrão
    wake_word_match_partials: bool = False  # casar em resultados parciais (mais rápido, menos preciso)

    # Encerramento autônomo da sessão pelo agente (ferramenta `end_session`).
    # Fechar o aplicativo SEMPRE pede confirmação verbal, independente daqui.
    end_session_enabled: bool = True  # False = a ferramenta não é oferecida ao modelo
    end_session_grace_sec: float = 4.0  # espera a despedida falada antes de encerrar (0..15)

    # Configurações de Voz Local (Piper TTS + faster-whisper)
    piper_voice_model: str = "pt_BR-faber-medium"
    whisper_model: str = "small"
    voice_visualizer_style: str = "waves"  # "waves", "bars", "matrix", "orb"
    voice_overlay_mode: str = "pill"  # "pill" (Pílula Compacta Flutuante) ou "full" (Janela Completa)

    # Posicionamento e comportamento da pílula de voz (voice_overlay_mode="pill")
    pill_monitor_idx: int = -1  # -1 = auto (monitor sob o cursor), senão índice Gdk.Display.get_monitors()
    pill_corner: str = "top-center"  # "top-center", "top-right", "top-left", "bottom-center"
    pill_pinned: bool = True  # manter acima de outras janelas (best-effort; X11-only)
    pill_margin: int = 12  # folga das bordas quando ancorada via layer-shell (Wayland)
    pill_idle_timeout_sec: int = 8  # 0 = nunca esmaecer; fade ocioso para 30% de opacidade
    # Auto-oculta a pílula quando a sessão cai (erro/desconexão), encerrando-a em
    # vez de deixá-la presa mostrando "Erro"/"Desconectado". 0 = nunca auto-ocultar.
    pill_error_autohide_sec: int = 12
    # Dicas de atalho na pílula: apresentadas uma por vez na primeira invocação
    # de cada execução do app (não a cada sessão de voz). `pill_hint_sec` é a
    # duração total do ciclo e precisa cobrir `len(_HINT_TIPS) × intervalo`
    # (6 × 2s = 12s) para o ciclo inteiro aparecer; 0 desliga.
    pill_hint_tips: bool = True
    pill_hint_sec: int = 12

    # Cursor Fantasma do Agente (Ghost Cursor Overlay - Computer-Use / Operator)
    ghost_cursor_enabled: bool = True
    ghost_cursor_color: str = "#00d2ff"  # cor primária neon do cursor/ripple
    ghost_cursor_anim_speed_ms: int = 250  # velocidade de deslocamento suave (ms)

    # Inicialização automática com o sistema (Autostart no boot/login)
    autostart_enabled: bool = False

    # Microinterações da interface (estilo Superhuman): transições de hover,
    # foco e estado. `True` zera todas as durações — interface estática para
    # quem prefere (ou precisa de) ausência total de movimento.
    reduced_motion: bool = False

    # Execução proativa de ações seguras (criar/abrir documentos em pastas autorizadas)
    auto_execute_safe_actions: bool = False

    # Nível de autonomia do assistente (ver `shell/risk.normalize_autonomy_level`):
    # "low" | "standard" | "high". Governa o teto do que executa sem pergunta —
    # "high" promove ações reversíveis em `trusted_directories` a AUTO. Valor
    # inválido é normalizado no `load()` para "low" (fail-closed, lado que se VÊ).
    autonomy_level: str = "standard"

    # Red zones (faixas bloqueadas para automação) em pixels.
    # -1 = automático: pergunta ao ambiente (GNOME/KDE têm painel; wlroots só
    # tem barra se o usuário rodar uma). 0 desliga a faixa.
    red_zone_bottom_px: int = -1
    red_zone_top_px: int = -1

    # Servidores MCP (Model Context Protocol - Extensibilidade e Ferramentas Externas)
    mcp_enabled: bool = True
    mcp_config_path: str = ""  # vazio = ~/.config/zorin-copilot/mcp_servers.json
    mcp_auto_connect: bool = True  # conectar automaticamente aos servidores habilitados

    # Integrações Google (OAuth2 "Desktop app"). Desligado por padrão, e o motivo
    # é de privacidade, não de prudência genérica: aqui os dados **saem** da
    # máquina para os servidores do Google (eventos, arquivos, e-mails). Ao
    # contrário do RAG local — que só cresce por dentro —, isto precisa de um
    # opt-in explícito. `google_enabled` é o portão mestre;
    # `google_enabled_services` lista os serviços autorizados individualmente
    # (ver `core/google_auth.known_services()`). Os escopos NÃO moram aqui: são
    # constantes em `core/google_auth.py`, fonte única, para não divergirem.
    google_enabled: bool = False
    google_client_secret_path: str = ""  # vazio = ~/.config/zorin-copilot/google_client_secret.json
    google_enabled_services: list = field(default_factory=list)
    google_token_backend: str = ""  # "" = auto (keyring > arquivo); "keyring"; "file"
    # Aviso proativo de expiração do refresh token. Em modo "Testing" a Google
    # expira em ~7 dias; 0 desliga o aviso. Ver `google_auth.refresh_expiry_warning`
    # para a premissa (é propriedade da PUBLICAÇÃO do app, não do token).
    google_refresh_warning_days: int = 7

    # Modo Agente: limites de passos, tempo e orçamento adaptativo de raciocínio
    agent_max_steps: int = 15
    agent_max_seconds: float = 300.0
    agent_adaptive_budget: bool = True

    # Casa Inteligente e IoT (Home Assistant)
    ha_enabled: bool = True
    ha_url: str = "http://localhost:8123"
    ha_token: str = ""
    ha_default_light: str = ""  # ID opcional da lâmpada principal (ex: light.lampada_quarto)
    # Eventos em tempo real (WebSocket): o Copilot passa a "saber" de mudanças
    # de estado sem precisar consultar (polling). Desligado por padrão porque
    # abre uma conexão persistente — habilite para reações/proatividade.
    ha_realtime_enabled: bool = False
    ha_realtime_entities: str = ""  # filtro opcional: IDs separados por vírgula (vazio = todas)
    # Segredo compartilhado da ponte HA→Copilot (scripts/ha_bridge.py). Vazio = ponte sem token.
    ha_bridge_token: str = ""
    # Anúncio falado de eventos da casa. Opt-in explícito: um assistente que fala
    # por conta própria precisa ser pedido, nunca presumido. Exige o canal de
    # tempo real ligado (`ha_realtime_enabled`) — sem ele não há evento para ouvir.
    ha_announce_enabled: bool = False
    # Filtro opcional de entidades para o anúncio (IDs separados por vírgula).
    # Vazio = todas as que tiverem frase conhecida (ver `core/ha_announce.py`).
    ha_announce_entities: str = ""
    # Política de atenção (ver `core/attention.py`): decide se vale **interromper**
    # e com que intensidade — falar, aviso visual silencioso, selo, ou nada.
    #
    # Ligada por padrão porque só **reduz** interrupção: todos os ajustes abaixo
    # tornam o assistente mais discreto do que a versão sem política. Desligá-la
    # devolve o comportamento antigo ("fala sempre que não estiver falando"), que
    # é justamente o que se quer evitar num assistente ambiente.
    attention_enabled: bool = True
    # Janela de silêncio no formato `inicio-fim` (hora cheia), separada por vírgula.
    # `23-7` atravessa a meia-noite. Vazio = sem janela de silêncio.
    attention_silence_hours: str = "23-7"
    # Teto de falas por hora. `0` ou negativo significa **nada fala** — não "sem
    # limite". A leitura conservadora é deliberada: ver a nota em `core/attention.py`
    # sobre por que a assimetria com `memory_consolidation_interval_hours` é correta.
    attention_max_per_hour: int = 6
    # Minutos de ociosidade a partir dos quais o usuário é considerado ausente e a
    # fala vira selo. Só tem efeito quando o app consegue medir a ociosidade — hoje
    # não consegue nesta máquina (ver `AttentionContext.idle_seconds`).
    attention_away_minutes: int = 30

    # Monitor de arquivos (inotify/Gio) para indexação contínua do RAG. **Ligado por
    # padrão**, ao contrário da consolidação e dos anúncios: ele não cria um recurso
    # novo, ele completa um que já está ligado. `index_directories()` roda uma única
    # vez no arranque, então sem isto um documento salvo com o app aberto só entra no
    # índice no próximo arranque — existe no disco e não existe para a busca.
    # O interruptor existe para poder desligar, não para precisar ligar.
    file_watcher_enabled: bool = True

    # Consolidação da memória ("sono REM"): poda os `action_logs` antigos e destila
    # padrões para o grafo de conhecimento. Opt-in explícito pelo mesmo motivo dos
    # anúncios — **e mais um**: isto APAGA linhas do histórico de ações. Medido:
    # 194 linhas em 5 dias (~97 KB) viram ~6,9 MB por ano sem poda.
    memory_consolidation_enabled: bool = False
    # Quantos dias de histórico de ações manter. O corte é o que impede o banco de
    # crescer para sempre; ver `core/consolidator.py` para as medições.
    memory_consolidation_days_to_keep: int = 14
    # Intervalo mínimo entre execuções. "Diário" significa **no máximo** uma vez por
    # dia, e não "24 h depois do arranque": o estado fica em disco justamente para
    # que reiniciar o app todo dia não adie a consolidação para sempre.
    memory_consolidation_interval_hours: float = 24.0

    # Configurações de Confiança, Privacidade e RAG

    @staticmethod
    def detect_platform() -> str:
        """Descreve o sistema real onde o Copilot roda, para o prompt de sistema.

        Lê ``/etc/os-release`` e as variáveis de ambiente do compositor. Evita
        mentir para o modelo (ex.: dizer "Zorin OS 18" quando o usuário está no
        EndeavourOS + Hyprland) — a detecção correta dita as sugestões de pacotes
        e comandos que o agente faz.
        """
        pretty = "Linux"
        try:
            with open("/etc/os-release", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("PRETTY_NAME="):
                        pretty = line.split("=", 1)[1].strip().strip('"')
                        break
        except OSError:
            pass
        desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").strip() or "desconhecido"
        session = (os.environ.get("XDG_SESSION_TYPE") or "").strip()
        if session:
            return f"{pretty} ({desktop} no {session})"
        return f"{pretty} ({desktop})"
    trusted_directories: list[str] = field(default_factory=lambda: ["~/Documentos"])
    quarantine_directories: list[str] = field(default_factory=lambda: ["~/Downloads"])
    ignored_patterns: list[str] = field(
        default_factory=lambda: [
            "*.kdbx",
            "*.key",
            "*.pem",
            "*.env",
            "*IRPF*",
            "*senha*",
            "*extrato*",
            "*credentials*",
        ]
    )
    rag_local_only: bool = False
    mask_pii: bool = True
    # Privacy Shield (Entrega E / P1-9). Identidades de aplicativo (nome/classe, ex.:
    # "libreoffice-writer", "firefox") comparadas **exatamente**, sem caixa. A allowlist
    # é o consentimento do usuário: a janela deixa de ser ocultada do stream de vídeo
    # mesmo que o título case um padrão sensível. A denylist acrescenta janelas a ocultar.
    # Cofres de senha e prompts de autenticação **ignoram** a allowlist — ver
    # `PrivacyGatekeeper.evaluate_window`.
    privacy_shield_allowlist: list[str] = field(default_factory=list)
    privacy_shield_denylist: list[str] = field(default_factory=list)
    max_file_size_mb: int = 40
    # Nome do assistente. Governa o **prompt**, o **título da janela** e o rótulo das
    # notificações — ver `core/identity.py`. Vazio = o nome histórico.
    assistant_name: str = ""
    # Persona customizada. Vazio = **derive** de `assistant_name` + o texto base de
    # `ai/providers.py`; não-vazio = substitui a persona (o bloco antes de
    # `identity.PERSONA_MARKER`), mantendo as diretrizes técnicas.
    #
    # O que este campo era, e não devia: `load()` o preenchia quando vazio e `save()`
    # o persistia, e **ninguém o lia**. Quem o editasse não mudava nada e ainda via o
    # campo preenchido — um campo que mentia. Ver a guarda de migração em `load()`.
    system_prompt: str = ""

    @classmethod
    def config_dir(cls) -> Path:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        path = Path(base) / "zorin-copilot"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def config_file(cls) -> Path:
        return cls.config_dir() / "config.json"

    @classmethod
    def load(cls) -> CopilotConfig:
        config = cls()
        file_path = cls.config_file()
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data: dict[str, Any] = json.load(f)
                    for k, v in data.items():
                        if hasattr(config, k) and v is not None:
                            setattr(config, k, v)
            except Exception:
                pass

        # Migração de limiares acústicos legados (evita auto-interrupção / engasgos por eco)
        if config.live_voice_interrupt_threshold_rms < 2000.0:
            config.live_voice_interrupt_threshold_rms = 2800.0
        if config.live_voice_jitter_buffer_ms < 240:
            config.live_voice_jitter_buffer_ms = 240

        # Nível de autonomia: normaliza valor inválido (fail-closed). O `load()` não
        # valida tipos — sem isto, um typo no `config.json` passaria direto.
        from ..shell.risk import normalize_autonomy_level

        config.autonomy_level = normalize_autonomy_level(config.autonomy_level)

        # Fallback para variáveis de ambiente se campos estiverem vazios
        if not config.gemini_api_key:
            config.gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
        if not config.openai_api_key:
            config.openai_api_key = os.environ.get("OPENAI_API_KEY", "")
        if not config.workbuddy_api_key:
            config.workbuddy_api_key = os.environ.get("WORKBUDDY_API_KEY", "")
        if not config.ha_url:
            config.ha_url = os.environ.get("HOME_ASSISTANT_URL", "http://localhost:8123")
        if not config.ha_token:
            config.ha_token = os.environ.get("HOME_ASSISTANT_TOKEN", "")
        if not config.ha_bridge_token:
            config.ha_bridge_token = os.environ.get("ZORIN_HA_BRIDGE_TOKEN", "")
        if not config.google_client_secret_path:
            config.google_client_secret_path = os.environ.get("ZORIN_GOOGLE_CLIENT_SECRET", "")
        if not config.google_token_backend:
            config.google_token_backend = os.environ.get("ZORIN_GOOGLE_TOKEN_BACKEND", "")

        # Migração do prompt de sistema (2026-09-21).
        #
        # `load()` **gerava** um prompt curto aqui quando o campo estava vazio, e
        # `save()` o persistia (`asdict`). Resultado: toda instalação já usada tem esse
        # texto gravado no `config.json`. No dia em que o campo passou a ser **lido**
        # — que era o conserto do campo que mentia —, aquele texto viraria um override
        # de verdade e o prompt cairia de 9,5 KB para 260 caracteres em toda
        # instalação existente. Regressão silenciosa, e por isso a guarda é obrigatória.
        #
        # Reconhecido o texto gerado, ele é descartado: vazio volta a significar
        # "derive da identidade", que é o que o campo sempre prometeu.
        if is_legacy_auto_prompt(config.system_prompt):
            config.system_prompt = ""

        return config

    def save(self) -> None:
        file_path = self.config_file()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)
        # Permissões 0600 para proteger chaves de API
        try:
            os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def is_configured(self) -> bool:
        if self.provider == "hybrid":
            return bool(self.gemini_api_key.strip() or self.ollama_url.strip())
        if self.provider == "gemini":
            return bool(self.gemini_api_key.strip())
        if self.provider == "ollama":
            return bool(self.ollama_url.strip())
        if self.provider == "openai":
            return bool(self.openai_api_key.strip())
        if self.provider == "workbuddy":
            return bool(self.workbuddy_api_key.strip())
        return False
