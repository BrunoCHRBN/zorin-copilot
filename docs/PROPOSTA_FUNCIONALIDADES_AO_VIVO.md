# PROPOSTA — Funcionalidades "ao vivo" (speech/meeting/slides)

> Avaliação de viabilidade baseada **no código atual** do Zorin Copilot (leitura de
> `src/zorin_copilot/ai/live.py`, `ai/local_voice.py`, `core/dictation.py`,
> `core/study_capture.py`, `core/study_summary.py`, `core/calendar.py`,
> `core/document_generators.py`). Cada afirmação aponta o arquivo/linha que a
> sustenta. Sem suposição.

## 0. Premissa: dois caminhos de voz coexistindo

O projeto tem **dois** clientes de voz ao vivo, com capacidades muito diferentes:

| Caminho | Arquivo | STT | LLM | TTS | Vídeo/tela | Idiomas |
|---|---|---|---|---|---|---|
| **Local 100% offline** | `ai/local_voice.py` (`LocalLiveVoiceClient`) | faster-whisper (`language="pt"` fixo) | `IntentEngine` (parser de comando, **não gerativo**) | Piper `pt_BR` | **não** (`is_video_streaming()` → `False`, linha 654) | só PT |
| **Gemini Live (nuvem)** | `ai/live.py` (`GeminiLiveClient`) | Google (auto, multilíngue) | Gemini (gerativo + tool calling) | Google (devolve áudio) | **sim** (`send_screen_frame`, `toggle_video_stream`) | multilíngue |

Consequência direta: **tudo que exige gerar texto livre em outro idioma ou "entender"
conversa contínua só é viável pelo caminho Gemini Live.** O caminho local é cimentado
em português e em comandos de desktop (`STT(pt) → IntentEngine → ação → TTS(pt)`).

## 1. Tradução de fala em tempo real

**Veredito: viável pelo Gemini Live; inviável localmente como está.**

### Viável (Gemini Live)
O `GeminiLiveClient` já faz WebSocket bidirecional com `realtimeInput` de áudio 16 kHz
(`live.py:98`, `:2101`) e recebe áudio de volta para reprodução (`live.py:2244`). Basta
semear o sistema com um prompt do tipo *"ouça em <idioma X>, responda traduzido para
<idioma Y>"* e o modelo faz STT+tradução+TTS num só turno. O `on_transcript` (`live.py`)
já expõe o texto para legendar na tela.

### Por que não localmente (hoje)
- `LocalVoiceTranscriber.transcribe_pcm` **fixa `language="pt"`** (`local_voice.py:470-484`).
  Falar em inglês/espanhol é transcrito como português (Wer degrada).
- O gargalo após a STT é o `IntentEngine`, que devolve `ActionPlan` (comando de desktop),
  **não** um tradutor (`local_voice.py:788-882`). Não há onde encaixar "traduza isto".
- O TTS local (`LocalVoiceSynthesizer`) é Piper com voz `pt_BR` por padrão
  (`local_voice.py:56`, `core/identity.py`). Piper *suporta* outros locales, mas o código
  usa uma única voz configurável (`piper_voice_model`) — traduzir para inglês exigiria
  baixar uma voz `en_US-*` e rotear por idioma de destino.

### Componentes para fechar (caminho local, se desejado)
1. Tornear `language` dinâmico/auto-detect no `transcribe_pcm`.
2. Substituir (ou contornar) o `IntentEngine` por um LLM gerativo local (Ollama/Qwen) com
   prompt de tradução — reusar `ai/agent_router.py`.
3. Mapa idioma→voz Piper e download sob demanda (já existe `ensure_model_available`).

## 2. Geração de resumo durante reuniões

**Veredito: parcialmente viável; o bloqueio principal é a CAPTURA de áudio, não o resumo.**

### O que já existe para o "resumo"
- `core/study_summary.generate_summary` gera tópicos + glossário a partir de texto
  (`study_summary.py:171`). É *batch* (pós-captura) e o prompt é PT-BR (`:45-64`), mas a
  lógica de resumo é reutilizável para qualquer transcrição.
- `GeminiLiveClient` mantém `transcripts_log` por turno (`live.py:1486`) — mas é
  comando/resposta, não um *stream* contínuo de reunião com falantes.

### O bloqueio real: não há captura do áudio da reunião
- **Nenhuma captura de áudio do sistema (loopback/monitor source) existe no repo.**
  Grep por `loopback|module-loopback|pactl monitor|pw-dump|\.monitor` só acha rede e
  monitores de tela. Os gravadores são **microfone exclusivo**: `pw-record`/`arecord`
  (`dictation.py:266`, `local_voice.py:678`). `pactl` aparece só para volume
  (`core/desktop/controls.py:164`), nunca para criar source de monitor.
- Sem loopback, só a **sua** voz (e só se perto do mic) entra. A fala dos outros
  participantes (Meet/Zoom/Teams) não é capturada → não há reunião para resumir.
- `CalendarManager` é SQLite local + `.ics` (`calendar.py`); **sem** sync Google/Exchange,
  sem detectar/join de chamada. Há ferramenta `calendar_event` (create/list/delete) no Live
  (`live.py:636`), mas não "entre na reunião das 15h e grave".
- Sem diarização de falante (quem disse o quê) — o Whisper aqui não separa speakers.

### Caminho viável (Gemini Live + loopback)
1. **Adicionar captura de monitor source** do PipeWire (ex.: `pw-record` apontando para
   `@DEFAULT_MONITOR@` ou source criado via `pactl load-module module-loopback`). É a única
   peça faltante de peso.
2. Mandar esse áudio para o `GeminiLiveClient` (ou STT contínuo) e pedir transcrição +
   sumário contínuo pelo `on_transcript`/tool calling.
3. Opção local: mic → Whisper (PT) → `generate_summary` sobre o buffer acumulado — mas
   só captura o usuário e só PT.

### Limitações atuais que impactam
- Sem loopback de áudio (item acima) — **bloqueante**.
- `study_summary` é PT-BR e assume material de estudo (filtros de "rótulo de seção",
  `BUREAUCRATIC_PATTERNS`). Reunião precisa de prompt próprio (decisões, pendências, donos).
- Sem contexto de calendário ligado à gravação (não se sabe "qual" reunião está rolando).

## 3. Apresentação de slides assistida

**Veredito: a parte de GERAR e CONTROLAR slides já existe; a parte de "apresentar"
(narrar/responder) é viável pelo Gemini Live.**

### Já existe
- **Gerar deck real**: `core/document_generators.generate_pptx` escreve `.pptx` de verdade
  a partir de Markdown (slides separados por `---`) (`document_generators.py:413`). O Live
  já tem ferramenta `write_document` que gera `.pptx` (`live.py:259`). `app_integrations`
  abre Google Slides (`core/app_integrations.py:137`).
- **Avançar slides por voz/controle**: `study_capture._advance_slide` avança páginas via
  VLM / AT-SPI / `Page_Down` (`study_capture.py:350`). É reaproveitável para o Impress:
  basta apontar o alvo para a janela do apresentador. `shell/input_driver.py` e
  `core/window_manager.py` já enviam tecla/clique.
- **Narrar**: TTS local Piper ou áudio do Gemini Live pode ler notas — mas **não há
  extração de speaker notes do .pptx** hoje (geramos a partir de Markdown; ler notas de
  volta exigiria `python-pptx` parse, que já é dependência do extra `office`).

### Viável com pouco
- "Próximo slide" / "volte" / "anote isso" por voz → reusar `_advance_slide` + `IntentEngine`
  (comando) ou Gemini Live (linguagem livre).
- Narração das notas: adicionar leitura de `slide.notes_slide` via `python-pptx` e passar
  ao `LocalVoiceSynthesizer.speak`.
- Q&A sobre o slide durante a apresentação: Gemini Live com vídeo (`send_screen_frame`)
  já "vê" a tela e responde por tool calling (`live.py` vídeo + `:2456`).

### Limitações atuais
- `LocalLiveVoiceClient` **não** transmite vídeo (`is_video_streaming()` → `False`,
  `local_voice.py:654`) → Q&A "sobre o que está na tela" é só Gemini Live.
- Sem design automático de layout (IA de slide) — geração é Markdown→slides genéricos.
- Sem sincronia com apresentador remoto (aplicar no seu Impress local, não controlar o de
  terceiros).

## 4. Recursos semelhantes (matriz rápida)

| Recurso | Viável? | Caminho | Peça faltante / limitação |
|---|---|---|---|
| **Legendas ao vivo (mono, PT)** | Sim (Gemini Live); PT-local limitado | Live `on_transcript` | Local exige Whisper PT só |
| **Legendas traduzidas ao vivo** | Sim (Gemini Live) | Live + prompt | Loopback não necessário (só seu microfone) |
| **Automação por voz na apresentação** (luz HA, mute, trocar janela) | Sim | Já pronto: Live → `smart_home_*`, `window_manager`, `controls` | — |
| **Q&A sobre a tela durante apresentação** | Sim (Gemini Live) | `send_screen_frame` + tool calling | Só nuvem (local sem vídeo) |
| **Gravar e transcrever reunião completa** | Parcial | requer loopback | Sem captura de áudio do sistema (bloqueante) |
| **Resumo + pendências de reunião** | Parcial | `generate_summary` + loopback | Sem diarização; prompt PT-BR |
| **Entrar/join em Meet/Zoom automático** | Não | — | Sem integração com apps de call |
| **Tradução offline de idioma arbitrário** | Não (como está) | — | `language="pt"` fixo + IntentEngine não gerativo |

## 5. Ordem de implementação recomendada (esforço × impacto)

1. **Loopback de áudio do PipeWire** (uma função nova em `dictation.py`/`local_voice.py` +
   `pactl module-loopback`). Destrava reunião/resumo/legenda traduzida. *É o maior gargalo.*
2. **Modo "tradução"/"legenda" no Gemini Live** — só prompt + UI (OSD de legenda já tem
   primitiva em `dictation_osd.py`/`voice_pill.py`).
3. **Controle de slides por voz** — reaproveitar `_advance_slide` + `window_manager` (Impress).
4. **Narração de notas** — `python-pptx` lê notas → Piper/Gemini TTS.
5. (Opcional, pesado) **Tradução offline** — Whisper multilíngue + Ollama gerativo + vozes
   Piper por idioma.

## 6. Riscos de código a considerar

- O caminho local é **PT-cêntrico por construção** (`language="pt"`, TTS `pt_BR`,
  `IntentEngine` não gerativo). Qualquer feature de idioma estrangeiro nasce no Gemini Live.
- `LocalLiveVoiceClient` e `GeminiLiveClient` são interfaces paralelas (mesmos callbacks
  `on_transcript`, `on_audio_level`), mas o local **não** implementa vídeo nem geração
  livre — não confundir "funciona na nuvem" com "funciona offline".
- Sem swap na máquina (ver MEMORY): carregar Whisper + Piper + Ollama juntos durante uma
  reunião pode estourar RSS. O runner de teste já documenta esse teto.
