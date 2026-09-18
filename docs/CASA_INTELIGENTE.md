# Integração de Casa Inteligente & IoT — Zorin Copilot

O **Zorin Copilot** possui um subsistema nativo de integração com Casa Inteligente e Internet das Coisas (IoT), permitindo controlar lâmpadas inteligentes (dimerização, temperatura Kelvin, cores RGB), ar-condicionado, tomadas, interruptores e cenas diretamente por **comandos de voz ao vivo (Gemini Live)** ou pelo **Modo Agente autônomo**.

---

## 1. Arquitetura da Integração

```
┌─────────────────────────────────────────────────────────────┐
│             Usuário (Voz, Vídeo ou Texto)                  │
└──────────────┬───────────────────────────────┬──────────────┘
               │                               │
               ▼                               ▼
    [Gemini Live WebSocket]           [Agente ReAct / Chat]
    • live.py                         • agent_tools.py
    • smart_home_control              • smart_home_control
    • smart_home_status               • smart_home_status
               │                               │
               └───────────────┬───────────────┘
                               │
                               ▼
               [HomeAssistantManager (core/home_assistant.py)]
               • Cliente REST assíncrono ultra-leve
               • Cache inteligente de estados (8s)
               • Parser de palavras-chave em português
               • Mapeamento de Kelvin (2000K-6500K) e cores RGB
                               │
                               ▼
               [Home Assistant (Docker Local ou Rede)]
                     http://localhost:8123/api
                               │
         ┌─────────────────────┼─────────────────────┐
         ▼                     ▼                     ▼
  [Lâmpadas Wi-Fi]       [Ar-Condicionado]       [Tomadas / Switches]
  Avant Neo, Tuya,       Wi-Fi nativo ou         Shelly, Sonoff,
  Philips Hue, Shelly    Broadlink IR Mini       Smart Plugs Tuya
```

---

## 2. Aparelhos Suportados

### 2.1 Iluminação Inteligente (Avant Neo, Tuya, Smart Life, Shelly, etc.)
- **Ligar / Desligar / Alternar (Toggle)**.
- **Brilho e Dimerização:** Ajuste percentual de 1% a 100% ou escala 0-255.
- **Temperatura de Cor (Kelvin):**
  - Mapeamento por palavras-chave:
    - *"quente"*, *"âmbar"*, *"relax"* $\rightarrow$ **2700K** (Branco quente aconchegante)
    - *"suave"* $\rightarrow$ **3200K**
    - *"neutro"*, *"natural"* $\rightarrow$ **4000K** (Branco neutro balanceado)
    - *"dia"* $\rightarrow$ **4500K**
    - *"frio"*, *"foco"*, *"estudo"*, *"trabalho"* $\rightarrow$ **6500K** (Branco frio de alta produtividade)
- **Cores RGB:**
  - Nomes em português (*"vermelho"*, *"azul"*, *"verde"*, *"amarelo"*, *"laranja"*, *"roxo"*, *"rosa"*, *"ciano"*, *"branco"*) ou código hexadecimal (`#FF5500`).

### 2.2 Climatização & Ar-Condicionado
- **Ligar / Desligar**.
- **Temperatura alvo:** Ajuste em graus Celsius (ex: 22°C).
- **Modos de Operação (HVAC):** `cool` (frio / refrigerar), `heat` (quente / aquecer), `fan_only` (ventilação), `auto` (automático).

### 2.3 Tomadas & Interruptores (Switches)
- **Ligar / Desligar / Alternar** tomadas de computador, monitores, cafeteiras ou iluminação de bancada.

---

## 3. Configuração Passo a Passo

### Passo 1: Iniciar o Home Assistant no Docker

Na sua máquina Linux:
```bash
docker run -d \
  --name homeassistant \
  --privileged \
  --restart=unless-stopped \
  -e TZ=America/Sao_Paulo \
  -v "$HOME/.homeassistant:/config" \
  --network=host \
  ghcr.io/home-assistant/home-assistant:stable
```

### Passo 2: Parear a Lâmpada Avant Neo (Tuya)
1. Conecte a lâmpada no aplicativo **Smart Life** (ou Tuya Smart) pelo celular.
2. Acesse no navegador: `http://localhost:8123` e finalize a criação do usuário.
3. Vá em **Configurações > Dispositivos e Serviços > Adicionar Integração > Tuya**.
4. Escaneie o QR Code na tela usando o app Smart Life no celular (**Perfil > Escanear QR Code**).

### Passo 3: Gerar Token de Acesso de Longa Duração
1. No Home Assistant, clique no seu perfil de usuário (canto inferior esquerdo).
2. Na aba **Segurança**, role até o final em **Tokens de Acesso de Longa Duração**.
3. Clique em **Criar Token**, atribua um nome (ex: `zorin-copilot`) e copie a chave gerada.

### Passo 4: Conectar no Zorin Copilot
1. Abra o Zorin Copilot e acerte as preferências (<kbd>Ctrl</kbd> + <kbd>,</kbd> ou menu lateral).
2. Selecione a aba **Casa Inteligente**.
3. Insira a URL (`http://localhost:8123`) e cole o **Token de Acesso**.
4. Clique em **Testar Conexão** para validar.

---

## 4. Exemplos de Comandos por Voz (Gemini Live)

Com o Gemini Live ativo (<kbd>Ctrl</kbd> + <kbd>M</kbd> ou <kbd>Super</kbd> + <kbd>Shift</kbd> + <kbd>V</kbd>):

* *"Zorin, liga a lâmpada em branco quente e coloca o brilho em 30%."*
* *"Aumenta a luz para foco total a 100%."*
* *"Muda a luz para azul suave."*
* *"Como está a iluminação do quarto agora?"*
* *"Coloca o ar-condicionado em 22 graus no modo frio."*
* *"Apaga todas as luzes e desliga a tomada do monitor."*
