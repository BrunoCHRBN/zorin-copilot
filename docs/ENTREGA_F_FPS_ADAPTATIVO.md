# Entrega F — FPS adaptativo no stream de vídeo + captura full-res sob demanda

> Plano de ação para o **P1-6** de `docs/MELHORIAS_VOZ_TELA_AO_VIVO.md` §2.3
> (*"FPS adaptativo (P1-6)"* e *"Captura sob demanda em alta resolução (P1-6)"*).
> Cada ponto ancora no estado real do código, **lido nesta rodada (2026-09-29)**.
>
> **STATUS: IMPLEMENTADO (2026-09-29).** Entregas anteriores: `ENTREGA_A_VOZ_TELA.md` (P0-1/2/3),
> `ENTREGA_B_RECONEXAO.md` (P0-4), `ENTREGA_C_MODO_E_SINAIS.md` (P1-5),
> `ENTREGA_D_WATCHDOG_PENSANDO.md` (P1-8), `ENTREGA_E_PRIVACY_SHIELD.md` (P1-9).

---

## 1. Evidência do problema (código atual, verificado)

### 1.1 O intervalo é calculado **uma vez** e nunca mais

`ai/live.py:4255`, no topo de `_video_stream_worker` (`:4253`):

```python
delay = max(0.4, 1.0 / max(0.2, fps))
```

E o laço dorme sempre esse valor (`:4378`):

```python
            time.sleep(delay)
```

Com o padrão `fps=1.0` (que é o que `start_video_stream` recebe), `delay = 1.0 s`: **um quadro
por segundo, sempre** — tela parada ou vídeo em movimento, é o mesmo ritmo.

### 1.2 O `diff` é calculado e **jogado fora** como sinal de ritmo

O laço já paga o custo do frame diffing (`:4348-4356`):

```python
diff = ScreenCaptureService.compute_frame_diff(img_bytes, self._last_sent_frame_bytes)
is_static = (diff < 0.02) and (self._last_sent_frame_bytes is not None)
if is_static and self._unchanged_frames_count < 8:
    self._unchanged_frames_count += 1
else:
    ...  # envia
```

O `diff` decide **se envia** (keep-alive de 8 quadros) — mas **não** decide **de quanto em
quanto tempo capturar**. Ou seja: a informação que permitiria dosar o ritmo já está na mão e
não é usada. O custo por quadro é alto (`capture()` real + resize + encode JPEG + resize 32×32
para o diff), e ele é pago igualmente numa tela parada.

### 1.3 Consequência dupla

| cenário | hoje | deveria |
|---|---|---|
| tela parada (lendo um documento) | 1 captura/s **sem necessidade** — CPU e uplink à toa | desacelerar |
| tela em movimento (rolagem, vídeo, arrasto) | 1 captura/s → visão **picotada** para o modelo | acelerar |

Os dois erros vêm do mesmo lugar: **um ritmo fixo não serve a dois regimes opostos.**

### 1.4 O stream é a **única** resolução — não há captura sob demanda

`ai/live.py:4333-4335` fixa o quadro do stream em `max_size=800`/`quality=70`. E a ferramenta
`capture_screen` (`:204` declaração, `:2970` handler) chama:

```python
ok, img_bytes, mode = ScreenCaptureService.capture(interactive=interactive)
```

Sem `max_size`/`quality` → cai nos defaults de `vision.capture` (`:31-32`, `1280`/`85`). É
melhor que o stream, mas **ainda é downscale** — não dá para o modelo pedir "leia este trecho
em resolução nativa". O plano pede exatamente isso: manter o stream leve e oferecer um quadro
full-res **sob pedido**.

---

## 2. Objetivo

1. O intervalo entre quadros **segue o movimento**: rápido quando a tela muda, lento quando
   está parada, com piso e teto explícitos.
2. O ritmo continua **derivado do `fps` pedido** — quem pede 1 fps recebe 1 fps de base, não
   um valor mágico novo.
3. `capture_screen` ganha um modo **full-res** (resolução nativa), sem mexer no caminho atual.

**Fora de escopo:** mudar o keep-alive de 8 quadros (`:4355`), o limiar de 2 % (`:4354`) ou o
crop da cerca (P1-9 já tratado em `ENTREGA_E`); UI para escolher o fps.

---

## 3. Passos

| # | Arquivo:linha | O que fazer | Riscos / armadilhas |
|---|---|---|---|
| W1 | `ai/live.py` (módulo) | `adaptive_video_delay(diff, base_delay, *, moving_factor, static_factor, min_delay, max_delay)` — função **pura**. Constantes `MOVING_FRAME_THRESHOLD=0.02` (o mesmo limiar do keep-alive), `VIDEO_MOVING_DELAY_FACTOR=0.5`, `VIDEO_STATIC_DELAY_FACTOR=2.0`, `VIDEO_MIN_DELAY=0.25`, `VIDEO_MAX_DELAY=4.0`. | `diff is None` (captura falhou) → **manter** o ritmo base, não acelerar. Nunca devolver fora de `[min, max]`. |
| W2 | `ai/live.py:4255` + `:4378` | Renomear `delay`→`base_delay`; dentro do laço, `frame_delay = base_delay` por padrão e `frame_delay = adaptive_video_delay(diff, base_delay)` quando há captura nova; dormir `frame_delay`. | `frame_delay` precisa existir **antes** do `time.sleep` em todos os caminhos (inclusive quando `_ws` é None). O caminho do Privacy Shield tem o próprio sleep — não mexer. |
| W3 | `ai/live.py:204` + `:2970` | `capture_screen` ganha o parâmetro `full_resolution` (BOOLEAN) na declaração; no handler, `full_resolution=True` → `capture(max_size=0, quality=92)` (`max_size=0` = **sem downscale**, ver `vision.py:115`). O caminho sem o parâmetro fica **byte-idêntico** ao de hoje. | Resolução nativa pode gerar payload grande; é sob pedido explícito do modelo. Documentar o trade-off. |

---

## 4. Critérios de aceitação

- Tela parada → intervalo **maior** que o base; tela mudando → intervalo **menor**.
- O intervalo nunca sai de `[0.25 s, 4.0 s]` (4 fps … 0,25 fps).
- `diff is None` → o intervalo **não** muda (não acelera por ausência de dado).
- Com `fps=1.0`: parado ≈ 2,0 s (0,5 fps) e em movimento ≈ 0,5 s (2 fps).
- O intervalo continua **escalando** com o `fps` pedido.
- `capture_screen` sem `full_resolution` comporta-se exatamente como antes (1280/85).
- `capture_screen` com `full_resolution=True` pede resolução nativa (`max_size=0`).

---

## 5. Implementado (2026-09-29) — âncoras reais

### 5.1 Constantes e função pura — `ai/live.py`

| Âncora | Linha | Valor | O que é |
|---|---|---|---|
| `MOVING_FRAME_THRESHOLD` | `:171` | `0.02` | Mesmo limiar do keep-alive (`is_static`) — um só sentido de "parado" no módulo. |
| `VIDEO_MOVING_DELAY_FACTOR` | `:174` | `0.5` | Em movimento, o intervalo base é **dividido** por isto. |
| `VIDEO_STATIC_DELAY_FACTOR` | `:176` | `2.0` | Parado, o intervalo base é **multiplicado** por isto. |
| `VIDEO_MIN_DELAY` / `VIDEO_MAX_DELAY` | `:178-179` | `0.25` / `4.0` | Piso (4 fps) e teto (0,25 fps) do intervalo. |
| `STREAM_CAPTURE_MAX_SIZE` / `_QUALITY` | `:182-183` | `800` / `70` | O stream contínuo, leve. Agora nomeado, não literal. |
| `FULL_CAPTURE_MAX_SIZE` / `_QUALITY` | `:185-186` | `0` / `92` | Captura sob demanda. `0` = **sem downscale** (ver `vision.py:115`). |
| `adaptive_video_delay()` | `:189-231` | — | A função pura. |

Semântica: `factor = moving_factor if diff >= MOVING_FRAME_THRESHOLD else static_factor`, e
`min(max_delay, max(min_delay, base * factor))`. Com o padrão `fps=1.0` (base 1,0 s):

| `diff` | intervalo | taxa |
|---|---|---|
| `0.00` (parado) | **2,0 s** | 0,5 fps |
| `0.02` (limiar, inclusivo) | **0,5 s** | 2 fps |
| `0.30` (movimento) | **0,5 s** | 2 fps |
| `None` (sem captura) | **1,0 s** | mantém a taxa pedida |

### 5.2 A fiação — `ai/live.py`

| Âncora | Linha | O que é |
|---|---|---|
| `base_delay = max(0.4, 1.0 / max(0.2, fps))` | `:4339` | Renomeado de `delay` — agora é a **referência**, não o valor final. |
| `frame_delay = base_delay` | `:4348` | Padrão por volta: sem captura nova, mantém a taxa pedida. |
| `frame_delay = adaptive_video_delay(diff, base_delay)` | `:4446` | O `diff` — que já era calculado — passa a **dosar o ritmo**. |
| `time.sleep(frame_delay)` | `:4473` | Antes: `time.sleep(delay)` fixo. |
| `time.sleep(base_delay * 1.5)` | `:4391` | Caminho do Privacy Shield: inalterado em comportamento (só o rename). |
| `max_size=STREAM_CAPTURE_MAX_SIZE, quality=STREAM_CAPTURE_QUALITY` | `:4422-4423` | O stream nomeia os próprios ajustes. |

`frame_delay` é inicializado **antes** do `time.sleep` em todos os caminhos — inclusive quando
`_ws` é `None` (nenhuma captura acontece) ou quando o pipeline levanta.

### 5.3 Captura sob demanda — `ai/live.py`

| Âncora | Linha | O que é |
|---|---|---|
| `"full_resolution"` na declaração | `:280` | O modelo só pode pedir o que a declaração anuncia. |
| `if bool(args.get("full_resolution", False)):` | `:3049` | Ramo novo; o `else` chama `capture(interactive=interactive)` **como sempre**. |

O caminho default fica **byte-idêntico** ao anterior (1280/85, os defaults de `vision.capture`).
O ramo novo pede `max_size=0` (nativo) e `quality=92`.

**Trade-off documentado:** resolução nativa pode gerar payload grande. É aceitável porque é
**sob pedido explícito** do modelo, para ler texto pequeno — o contínuo segue leve justamente
para não competir com o áudio.

---

## 6. Validação (medida nesta rodada)

### 6.1 A função pura, medida

Executada contra o módulo real (não por inspeção) — os números da tabela §5.1 e ainda:

```
escala com o fps pedido:  fps=0.5 base=2.000 parado=4.000 mov=1.000
                          fps=1.0 base=1.000 parado=2.000 mov=0.500
                          fps=2.0 base=0.500 parado=1.000 mov=0.250
                          fps=4.0 base=0.400 parado=0.800 mov=0.250
limites:  base 0.01 + movimento -> 0.250s   |  base 100 + parado -> 4.000s
entradas lixo ('x','y') -> 0.250s (degrada, não explode)
```

### 6.2 Testes — `tests/test_video_pacing.py` (novo, 16 testes + 20 subtestes)

Três classes: `AdaptiveDelayTest` (a função pura: parado/movimento, limiar inclusivo,
`diff=None`, piso/teto, escala com o `fps`, entrada inválida), `VideoLoopPacingTest` (a
**fiação** — roda voltas reais de `_video_stream_worker` com inspetor, captura e `sleep`
dublados e afirma o intervalo dormido) e `CaptureScreenFullResTest` (o caminho default
inalterado, o ramo nativo, e a declaração).

`VideoLoopPacingTest` existe porque a função pura sozinha não basta: voltar o laço a
`time.sleep(base_delay)` deixaria a função **verde e inerte** — exatamente a lacuna que o
harness abaixo mede.

### 6.3 Harness de mutação — `tests/mutacao/mutate_video_pacing.py` (novo)

**12 KILLED / 0 sobreviventes / 0 equivalentes / 0 inválidas**, `exit=0`, na primeira rodada.

Cobre W1 (fator fixo, limiar invertido, fator de movimento anulado, `diff=None` acelerando,
teto e piso removidos) e W3 (a CHAMADA adaptativa trocada pelo base, o `sleep` usando o base,
o stream subindo para resolução nativa, `capture_screen` perdendo o nativo, o caminho default
passando a usar full-res, e a declaração deixando de anunciar o parâmetro).

Integridade após a rodada: `grep` por resíduo nas fontes → **vazio**; âncoras presentes;
`tests/test_mutacao_anchors.py` → **3 passed**.

### 6.4 Suíte completa (runner seguro)

Executada pelo `run_tests_safe.py` (sem swap → nunca `pytest` direto). Resultado no fim desta
entrega, registrado em `ENTREGA_A_VOZ_TELA.md` §suíte:

```
módulos: 164   falhas: 0   tempo: 219s
pico de RSS em qualquer arquivo: 497 MB
```

O total subiu de 163 → **164** módulos porque esta entrega adiciona
`tests/test_video_pacing.py`.
