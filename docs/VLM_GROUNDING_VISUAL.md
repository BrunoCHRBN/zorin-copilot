# Grounding Visual por VLM Local — Zorin Copilot

O **Grounding Visual por VLM** (Vision-Language Models) permite que o Zorin Copilot localize, compreenda e interaja com botões, controles de interface, ícones, formulários em canvas e reprodutores de mídia em **qualquer aplicativo Linux/Wayland**, mesmo em interfaces opacas onde a árvore de acessibilidade AT-SPI2 é cega e onde o OCR tradicional por texto é insuficiente.

---

## 1. O Problema das Telas Opacas no Desktop Linux

Historicamente, a automação de desktop e os agentes de IA enfrentam três grandes cenários de falha na automação:

1. **Portais EAD / Ambientes Virtuais de Aprendizagem (AVA/SCORM):** Portais com players customizados em `<canvas>`, iframes opacos ou SPAs complexas não expõem nós semânticos no AT-SPI.
2. **Ícones Sem Texto:** Botões como *Play/Pause*, *Engrenagem de Configurações*, *Fechar (X)*, *Lupa de Busca*, *Menu Hambúrguer*, controles deslizantes de volume e botões de avanço de aula não possuem rótulos textuais legíveis por OCR simples.
3. **Jogos, Emuladores e Apps Electron Customizados:** Interfaces desenhadas diretamente via OpenGL/Vulkan/DirectX ou engines gráficas onde AT-SPI reporta apenas um nó raiz vazio.

---

## 2. Arquitetura da Cascata Tripla de Localização

O Zorin Copilot implementa uma **cascata inteligente de 3 níveis**, garantindo o menor tempo de resposta e economia de recursos computacionais:

```
                  CASCATA INTELIGENTE DE LOCALIZAÇÃO ESPACIAL
                  
       [ Solicitação do Usuário ou Agente: "Clica no botão de play" ]
                                      │
                                      ▼
                      ┌───────────────────────────────┐
                      │  Nível 1: AT-SPI Semântico    │
                      │  (0ms, CPU mínima, sem GPU)   │
                      └───────────────┬───────────────┘
                                      │
                         [ Encontrado? ] ──► SIM ──► Emite Clique Virtual
                                      │ NÃO / Opaco
                                      ▼
                      ┌───────────────────────────────┐
                      │  Nível 2: Tesseract OCR       │
                      │  (120-250ms, busca textual)   │
                      └───────────────┬───────────────┘
                                      │
                         [ Encontrado? ] ──► SIM ──► Emite Clique com Ghost Cursor
                                      │ NÃO / Ícone sem texto
                                      ▼
                      ┌───────────────────────────────┐
                      │  Nível 3: VLM Local (Ollama)  │
                      │  (qwen2.5vl, minicpm-v, llava)│
                      └───────────────┬───────────────┘
                                      │
                         [ Encontrado? ] ──► SIM ──► Converte [0..1000] -> Pixels
                                      │              e clica com Ghost Cursor
                                      │ NÃO / Ollama offline
                                      ▼
                      ┌───────────────────────────────┐
                      │  Fallback: Gemini Vision 2.5  │
                      │  (Nuvem, via API do Google)   │
                      └───────────────────────────────┘
```

---

## 3. Componentes Implementados

### 3.1. `src/zorin_copilot/core/vlm_grounding.py`
- **`VLMBoundingBox`:** Classe geométrica que processa coordenadas brutas (0..1000 ou 0.0..1.0), realiza desambiguação de eixos entre convenções de diferentes modelos (Qwen-VL `[xmin, ymin, xmax, ymax]` vs. Gemini `[ymin, xmin, ymax, xmax]`) e projeta as caixas em pixels absolutos da tela considerando monitores e janelas ativas com precisão float IEEE-754.
- **`VLMGroundingResult`:** Estrutura unificada contendo centro absoluto `(x, y)`, caixa delimitadora `(x, y, w, h)`, grau de confiança, descrição semântica gerada pelo modelo e conversão direta para `VisualElement`.
- **`LocalVLMGroundingClient`:**
  - **Auto-Discovery de Modelos:** Inspeciona automaticamente `GET /api/tags` no Ollama local procurando modelos com capacidades de `"vision"` ou famílias compatíveis (`qwen25vl`, `minicpm-v`, `llava`, `moondream`).
  - **Seleção Inteligente:** Prioriza modelos instalados como `qwen2.5vl:7b` ou `qwen2.5vl:3b`.
  - **Prompt Rígido de Grounding:** Gera respostas em JSON puro estruturado com bounding box e ponto central normalizado.
  - **Fallback Automático:** Caso o Ollama esteja inativo ou sem modelo de visão, recorre ao `gemini-2.5-flash` se configurado.

### 3.2. `src/zorin_copilot/core/ui_grounding.py`
- **`UIGroundingService.locate_element(query, mode='auto', threshold=0.65)`:** Executa a cascata completa OCR $\rightarrow$ VLM.
- **`UIGroundingService.ground_visual_element(query, fence=...)`:** Captura a tela e obtém diretamente o elemento visual via VLM.
- **`UIGroundingService.click_visual_element(query, prefer_vlm=...)`:** Localiza o alvo, valida os limites contra a cerca digital (`ScreenFenceManager`), respeita o botão de parada de emergência e anima o **Ghost Cursor** até o alvo executando o clique físico.

### 3.3. Ferramentas Expostas no `ToolRegistry` e `GeminiLiveClient`
- **`locate_element_visual`:** Localiza qualquer elemento gráfico ou textual, devolvendo coordenadas e bounding box.
- **`click_visual_element`:** Localiza e executa o clique com prioridade visual VLM para ícones e controles opacos.
- **`find_on_screen` e `click_on_screen`:** Atualizadas com parâmetros adicionais `mode` (`auto`, `vlm`, `ocr`) e `prefer_vlm`.
- **Integração por Voz (Gemini Live):** Ao falar com o assistente *"clica no botão de play do vídeo"* ou *"fecha a janela no ícone de fechar"*, o assistente utiliza a ferramenta de visual grounding correspondente.

---

## 4. Configuração em `config.json`

As novas diretivas de Grounding Visual estão disponíveis em `~/.config/zorin-copilot/config.json`:

```json
{
  "ollama_url": "http://127.0.0.1:11434",
  "ollama_vision_model": "minicpm-v",
  "vlm_grounding_enabled": true,
  "vlm_grounding_model": "",
  "vlm_grounding_fallback_to_gemini": true,
  "vlm_grounding_timeout_sec": 18.0,
  "vlm_confidence_threshold": 0.40
}
```

- Se `vlm_grounding_model` for deixado vazio `""`, o Zorin Copilot detectará automaticamente qual modelo de visão está instalado no seu Ollama (por exemplo `qwen2.5vl:7b` ou `qwen2.5vl:3b`).

---

## 5. Exemplo de Uso Prático

### Via Código / Python API:
```python
from zorin_copilot.core.ui_grounding import UIGroundingService

# Localiza elemento em tela com fallback automático
el, score, source = UIGroundingService.locate_element("ícone de engrenagem")
if el:
    print(f"Localizado em ({el.x}, {el.y}) via {source} com confiança {score:.2f}")

# Clica diretamente com o Ghost Cursor
ok, msg, coords = UIGroundingService.click_visual_element(
    "botão avançar módulo",
    prefer_vlm=True
)
print(msg)
```

### Via Prompt em Linguagem Natural (Chat ou Voz):
> **Usuário:** "Avança para o próximo módulo do curso no navegador."  
> **Zorin Copilot:** Localiza o botão gráfico de avanço na SPA/canvas via `qwen2.5vl`, anima o cursor até o centro do botão e realiza o clique com segurança.
