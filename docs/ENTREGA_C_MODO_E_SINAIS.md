# Entrega C — Indicador de modo (local × nuvem) e reconciliação de sinais

> Plano de ação para o **P1-5** de `docs/MELHORIAS_VOZ_TELA_AO_VIVO.md` §1
> (*"Indicador de modo (local × nuvem) e reconciliação de sinais"*, impacto Médio, risco Baixo).
> Cada ponto ancora no estado real do código, **lido nesta rodada (2026-09-29)**.
>
> **STATUS: IMPLEMENTADO (2026-09-29).** Entregas anteriores: `docs/ENTREGA_A_VOZ_TELA.md`
> (P0-1/2/3), `docs/ENTREGA_B_RECONEXAO.md` (P0-4).

---

## 0. Contexto

O Zorin Copilot tem **dois clientes de voz ao vivo** que implementam o mesmo protocolo de
callbacks: `GeminiLiveClient` (nuvem, `ai/live.py`) e `LocalLiveVoiceClient` (100% offline,
`ai/local_voice.py:522`). A mesma `VoicePillWindow` (`ui/voice_pill.py`) serve os dois.

O problema não é ter dois modos — é a UI **não distinguir** os dois. O usuário vê o mesmo
popover, com os mesmos controles, e não tem como saber se está falando com a nuvem ou com o
modelo local; pior, alguns controles **parecem funcionar e não fazem nada**.

---

## 1. Evidência do problema (código atual, verificado)

### 1.1 Não existe indicador de modo

O cabeçalho do popover diz apenas "Chamada de Voz Ativa" (`voice_pill.py:1178`) e a pílula
mostra só o `status_lbl` (`:389`, truncado em 28 chars). Nenhum dos dois clientes declara o
próprio modo: `mode` não existe em `ai/live.py` nem em `ai/local_voice.py`.

### 1.2 Controles de tela que parecem funcionar no modo local — e não funcionam

`LocalLiveVoiceClient` declara **stubs de compatibilidade** para os métodos de vídeo
(`local_voice.py:1090-1101`): `toggle_video_stream() -> False`, `set_video_mode() -> None`,
`panic_stop_video() -> None`, `send_screen_frame() -> False`.

Consequência direta: os `hasattr` da pílula **passam** no modo local.

- `voice_pill.py:1237` — `if hasattr(self.live_client, "toggle_video_stream")` → verdadeiro →
  o botão **"Transmitir Tela" é renderizado** e fica clicável.
- `voice_pill.py:1153-1160` — ao clicar, `toggle_video_stream()` devolve `False` e a pílula
  mostra o chip **"Transmissão de tela pausada"**. A mensagem *afirma que a transmissão
  existe e está pausada* — quando na verdade ela **não existe** no modo local.
- `voice_pill.py:1250-1262` — o botão de modo ("Janela" / "Tela Cheia") também é renderizado;
  `video_mode` não existe no cliente local, então o rótulo é sempre "Janela" e clicar chama
  `set_video_mode()` (no-op) e re-renderiza o mesmo rótulo: **um botão que não muda nada**.

### 1.3 O popover nomeia um modelo que não está rodando

`voice_pill.py:1284`:

```python
model_name = getattr(self.live_client, "model", "") or getattr(self.config, "gemini_live_model", "Gemini 2.0 Flash Live")
```

`LocalLiveVoiceClient` não tem `model` → cai no fallback → em modo local a linha mostra
**"Modelo: Gemini 2.0 Flash Live"** enquanto quem responde é o Qwen local
(`config.py:36`, `ollama_model = "qwen2.5:7b"`). É a UI afirmando um fato falso.

### 1.4 A linha da Cerca Espacial afirma um recurso inexistente no modo local

`voice_pill.py:1306-1313` monta a linha **"Cerca Espacial & Cursor Fantasma ativos"** com texto
fixo. No cliente local, `self.fence = None` (`local_voice.py:576`) e não há captura de tela
(`is_video_streaming() -> False`, `:646`) → a cerca não pode estar ativa. Texto fixo que
**mente** no modo local.

### 1.5 Sinais que nunca acendem no modo local (silêncio indistinguível de "desligado")

`LocalLiveVoiceClient` **declara** `on_video_state_change` (`:587`) e `on_privacy_state_change`
(`:589`) — para que a pílula possa conectá-los sem `AttributeError` (`voice_pill.py:564-571`) —
mas **nunca os invoca**. Logo o REC dot (`:1027`) e a borda de privacidade (`:1034`) ficam
permanentemente apagados. "Apagado porque não está transmitindo" e "apagado porque o modo não
tem o recurso" ficam **visualmente idênticos** — que é exatamente o
"mesmo visual, dois comportamentos" descrito na §2.1 do plano de melhorias.

---

## 2. Objetivo

Que a UI **declare o modo** e que **nenhum controle afirme uma capacidade que o cliente ativo
não tem**. Concretamente:

1. O cliente declara o próprio modo (fonte da verdade), em vez de a UI adivinhar por `hasattr`.
2. A pílula mostra um chip **"Local" / "Nuvem"**.
3. No modo local, os recursos de tela aparecem **rotulados como indisponíveis**, não como
   controles mortos nem como "pausados".
4. As linhas informativas (modelo, cerca) passam a dizer a **verdade do modo ativo**.

**Fora de escopo:** dar vídeo ao modo local (é outra decisão, P1-6); diarização (P1-7);
alterar o comportamento dos callbacks de privacidade da nuvem.

---

## 3. Passos

| # | Arquivo:linha | O que fazer | Riscos / armadilhas |
|---|---|---|---|
| M1 | `ai/live.py` (`__init__`) | Declarar `self.mode = "cloud"` e `self.supports_video = True`. | Não quebrar quem lê `video_mode` hoje. |
| M2 | `ai/local_voice.py` (`__init__`, ~`:576`) | Declarar `self.mode = "local"` e `self.supports_video = False`. | Manter os stubs de `:1090-1101` (contrato de compatibilidade). |
| M3 | `ui/voice_pill.py` | Helpers `_client_mode(client)` / `_supports_video(client)`: leem os atributos e **caem no comportamento antigo** quando ausentes (dublês/legado). | `MagicMock` devolve mock, não `bool`/`str` → validar o tipo antes de aceitar. |
| M4 | `ui/voice_pill.py` (`_build_ui`) | Chip de modo "Local"/"Nuvem" junto ao status. | Anti-slop: sóbrio, sem emoji, sem gradiente; classes CSS em `data/zorin-copilot.css`. |
| M5 | `ui/voice_pill.py:1237-1262` | Gatear os botões de tela em `_supports_video`; no modo local, mostrar "Recurso indisponível no modo local" (`dim-label`). | Não remover a seção — rotular, para não virar "sumiu". |
| M6 | `ui/voice_pill.py:1153-1160` | `_on_toggle_video_stream` honesto quando não há suporte ("indisponível no modo local"), em vez de "pausada". | — |
| M7 | `ui/voice_pill.py:1284-1294` | Linha do modelo: modo local → `config.ollama_model`; nuvem → `gemini_live_model`. | `live_model_label()` só entende nomes Gemini → usar rótulo próprio no local. |
| M8 | `ui/voice_pill.py:1306-1313` | Linha da cerca: só afirmar "ativos" se `getattr(client, "fence", None)`; senão, indisponível. | — |
| M9 | `tests/test_voice_pill.py` | Testes com cliente fake declarando `mode`/`supports_video`: chip correto nos dois modos; botões de tela ausentes no local; modelo/cerca verdadeiros. | Dublê `MagicMock` mantém o caminho antigo (não regride a suíte existente). |

---

## 4. Critérios de aceitação

- Com o cliente local: a pílula mostra "Local"; a seção de tela **não** oferece botões e
  **diz** que o recurso é indisponível; a linha do modelo cita o modelo local; a cerca não é
  afirmada como ativa.
- Com o cliente nuvem: comportamento **idêntico ao de hoje** (chip "Nuvem" é a única adição).
- Nenhum controle clicável que não faça nada.

---

## 5. Implementado (2026-09-29) — anchors reais

- `ai/live.py:1368` `self.mode = "cloud"`, `:1369` `self.supports_video = True`.
- `ai/local_voice.py:583` `self.mode = "local"`, `:584` `self.supports_video = False`.
- `ui/voice_pill.py:108` `_client_mode(client)`, `:119` `_supports_video(client)` — leem o que
  o cliente declara e **caem no comportamento histórico (nuvem)** quando o atributo não existe
  (dublês/legado). `_supports_video` exige um `bool` de verdade: `MagicMock` devolve um mock
  (truthy) e aceitá-lo faria o modo local parecer capaz de vídeo.
- `ui/voice_pill.py:400` chip de modo na linha do título; CSS em
  `data/zorin-copilot.css:1135-1147` (`.pill-mode`, `.pill-mode-cloud`, `.pill-mode-local`).
- `ui/voice_pill.py:1246` gate dos controles de tela; `:1254` rótulo "Indisponível no modo
  local"; `:1187-1196` `_on_toggle_video_stream` honesto.
- `ui/voice_pill.py:1346` linha do modelo por modo; `:1375-1387` linha da cerca em 3 estados
  (ativos / indisponível no modo local / inativa na nuvem sem cerca).

### Evidência visual (não só teste)

`scripts/snapshot_mode_indicator.py` rasteriza a pílula e o popover nos dois modos
(GTK4 → Gsk → textura → PNG). Saída em `docs/`:

| Arquivo | O que mostra |
|---|---|
| `indicador-modo-pilula-local.png` | chip **"Local"** em verde ao lado de "Copilot Live" |
| `indicador-modo-pilula-nuvem.png` | chip **"Nuvem"** em cinza discreto |
| `indicador-modo-popover-local.png` | "○ Indisponível no modo local", **sem** botões; "Modelo: dolphin3:latest" (o modelo local real do config); cerca indisponível |
| `indicador-modo-popover-nuvem.png` | "Compartilhamento pausado" + "Janela: Workbuddy ai" + botões; "Modelo: Gemini 3.8 Live"; cerca ativa |

**Defeito que só apareceu ao olhar o PNG** (o teste estava verde): na primeira rodada o popover
saía **branco sobre branco** — 2 cores distintas, 0 pixels de texto. O tema da sessão é escuro
(`gtk-application-prefer-dark-theme`), então o texto resolvia branco sobre uma superfície clara.
Um teste que só afirma `assertIn("Indisponível no modo local", labels)` fica verde e não vê
nada disso. Corrigido no script com `Adw.StyleScheme.FORCE_LIGHT`, para os PNGs ficarem legíveis
e coerentes com os demais docs do projeto.

**Correção de rótulo encontrada na mesma inspeção:** a primeira versão dizia "Cerca Espacial
indisponível **neste modo**" também na nuvem — onde a cerca **existe** e está apenas inativa.
O rótulo passou a distinguir as duas causas (`:1380-1386`), porque "o modo não tem" e "não está
ligada" são fatos diferentes.

### Validação

- `tests/test_voice_pill.py` → **94 passed** (80 pré-existentes + 14 novos: `TestModeHelpers`,
  `TestModeIndicator`). Nenhum teste antigo mudou de comportamento — o dublê `MagicMock`, que
  não declara modo, continua no caminho da nuvem.
- **Harness de mutação durável:** `tests/mutacao/mutate_mode_indicator.py` — **6 matadas, 0
  sobreviventes, 0 inválidas** (exit 0). Cobre: chip mentindo, `supports_video` aceitando
  truthy não-bool, gate voltando a `hasattr`, linha do modelo voltando ao fallback da nuvem,
  cerca afirmada sempre ativa, e o toggle respondendo "pausada". Âncoras conferidas por
  `tests/test_mutacao_anchors.py` (3 passed).
- **Suíte completa** (`run_tests_safe.py`, um arquivo por processo) → **161 módulos, 0 falhas,
  210 s**, pico de RSS 370 MB (máquina sem swap, teto de 900 MB).

**Não verificado:** o comportamento com os clientes **reais** (`GeminiLiveClient` /
`LocalLiveVoiceClient` instanciados de verdade, com PipeWire e Ollama vivos) — a evidência é
com dublês que declaram o mesmo contrato. O contrato em si (atributos `mode`/`supports_video`)
é verificado por leitura direta dos dois `__init__`.
