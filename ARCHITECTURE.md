# Arquitetura Técnica — Zorin Copilot

O **Zorin Copilot** foi projetado para operar com baixo overhead, determinismo e alta fidelidade sobre o **Zorin OS 18.1** (Ubuntu 24.04 LTS, GNOME 46 no compositor Mutter / Wayland).

---

## 1. As Camadas do Sistema

O projeto é dividido em quatro módulos bem desacoplados:

### Camada 1: Percepção do Desktop (`zorin_copilot.core`)
* **`a11y.py` (AT-SPI2):** Conecta-se ao barramento de acessibilidade do GNOME (`org.a11y.Bus`). Extrai a hierarquia de widgets de qualquer aplicativo compatível com GTK, Qt, Electron e navegadores (Firefox/Chromium). Obtém:
  * Papel do elemento (`button`, `entry`, `window`, `menu_item`, etc.).
  * Rótulo de texto / descrição acessível.
  * Coordenadas absolutas na tela.
  * Ações permitidas (`press`, `activate`, `select`).
* **`dbus_bridge.py`:** Monitora janelas ativas através de interfaces do GNOME Shell (`org.gnome.Shell`) e reprodutores de mídia (`org.mpris.MediaPlayer2`).

### Camada 2: Motor de Ações e Execução (`zorin_copilot.shell`)
* **Injeção de Input no Wayland:**
  1. *Ações semânticas diretas:* Chamadas `doAction` no objeto acessível AT-SPI2 (não exige movimentação física do mouse nem focar a janela).
  2. *Extensão GNOME Shell:* Pequena extensão auxiliar que provê um serviço D-Bus interno no Mutter para mover/redimensionar janelas e desenhar retângulos de destaque (overlays) ao redor do elemento em foco.
  3. *Injeção de tecla/mouse:* Via protocolo `libei` / `ydotool` para aplicativos que exigem clique simulado.

### Camada 3: Camada de Inteligência e Ações (`zorin_copilot.ai`)
* **Esquema Estrito de Ações (Tool Calling / JSON Schema):** A IA não gera código solto nem alucina; ela responde estritamente uma lista de intenções acionáveis:
  * `click_element(id_or_label)`
  * `type_text(id_or_label, text)`
  * `organize_windows(layout="split_left_right", app_left="firefox", app_right="code")`
  * `change_setting(schema, key, value)`
  * `open_application(app_name)`
  * `read_screen_context()`
* **Provedores Suportados:**
  * *Local:* Ollama / vLLM / llama.cpp (offline, com privacidade total).
  * *Nuvem:* Gemini / Claude API para raciocínio complexo.

### Camada 4: Interface do Usuário (`zorin_copilot.ui`)
* Construída exclusivamente com **GTK4 e Libadwaita**.
* Janela flutuante no centro da tela (estilo Spotlight / Raycast), sem bordas pesadas e integrada à paleta do sistema Zorin.
* Feedback visual limpo: indicador de pensamento da IA e prévia das ações antes de executá-las.

---

## 2. Fluxo de Execução de um Comando

```
[Usuário aciona Super+Espaço]
         │
         ▼
[Digita: "Preencha o campo de email com bruno@exemplo.com e clique em Salvar"]
         │
         ▼
[Core lê a árvore AT-SPI da janela em foco]
  -> Elementos: Entry(id="txt_email"), Button(name="Salvar")
         │
         ▼
[AI Model recebe o contexto reduzido + pedido do usuário]
         │
         ▼
[AI devolve plano estruturado:]
  1. type_text(target="txt_email", value="bruno@exemplo.com")
  2. click_element(target="Salvar")
         │
         ▼
[Shell Injector executa com confirmação e feedback visual na tela]
```
