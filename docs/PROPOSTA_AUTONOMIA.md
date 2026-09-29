# PROPOSTA — Mais autonomia para o assistente (sem virar falha silenciosa)

_Data: 2026-09-22. Base: leitura de `shell/risk.py`, `ai/live.py`, `ai/agent.py`, `ai/agent_tools.py`, `core/config.py`, `core/trust.py`, `core/memory.py`._

> **Pré-requisitos e riscos de implementação:** ver `docs/ANEXO_AUTONOMIA_PREREQUISITOS.md`
> (bloqueadores de arquitetura, armadilhas de config, segurança que a autonomia amplifica,
> blast radius de testes e ordem revisada).

## Tese

A capacidade já existe: são 83 ferramentas e a superfície **SAFE** já é ampla (controle de casa/HA,
`system_control` — inclusive travar tela —, fechar janela, input de desktop, escrita de memória/
contato). O que falta não é peça, é **confiança graduada**.

Hoje o modelo de risco é **binário**: `shell/risk.py` só tem `SAFE` e `CONFIRM`, com dois pontos
de enforcement independentes. Para dar mais autonomia de forma segura, troca-se o binário por um
risco de **3 níveis** + uma camada de **confiança persistente e aprendida**, com **um único ponto
de decisão** e **trilha de auditoria** — mantendo o princípio _fail-closed_.

Não é "tirar confirmação". Tirar confirmação troca a proteção mais cara do projeto (a falha
silenciosa) por conveniência. A autonomia deve ser **reversível, auditável e aprendida**, não cega.

## Estado atual (evidência)

- `src/zorin_copilot/shell/risk.py`: `RiskLevel` só tem `SAFE`/`CONFIRM`. `classify()` decide por
  nome de ferramenta + args para um punhado (`calendar_event`, `google_drive`, `google_gmail`,
  `run_command`, `write_file`/`edit_file`, hotkeys destrutivos). **Não considera zona de
  confiança nem reversibilidade.**
- Dois enforcement separados:
  - Live (`ai/live.py:_dispatch_tool` ~2510) → `confirm_action` + `_pending_actions` (TTL 120s).
  - Agent Loop (`ai/agent.py:379`) → callback `on_approval` no GTK, **fail-closed** se ausente.
- Autonomia hoje: `auto_execute_safe_actions` (conjunto SAFE estreito:
  `WRITE_FILE/OPEN_DOCUMENT/LAUNCH_APP/OPEN_URL/NOTIFY`) + "aprovar tudo" por execução no cartão
  do agente (`auto_approve_rest`).
- Zonas de confiança **já existem** em `core/config.py` (`trusted_directories`,
  `quarantine_directories`, `ignored_patterns`) e `core/trust.py` (`TrustLevel`,
  `DocumentTrustManager`) — **mas não estão conectadas ao risk gate**.
- Auditoria parcial: `core/memory.py` tem `action_logs`.

## Alavancas (cada uma aponta o código e o custo)

### 1. Risco de 3 níveis + reversibilidade
Adicionar `RiskLevel.AUTO` (executa, anuncia depois, registra) entre SAFE e CONFIRM. Classificar
por **reversibilidade × blast radius × zona de confiança**, não só por nome.
- `write_file` em `trusted_directories` e arquivo versionado por git → **AUTO** (reversível:
  `git checkout`). `write_file` em `quarantine_directories`/`~/Downloads` → **CONFIRM**.
- `system_control lock`, toggles de HA → já SAFE; manter.
- `run_command` com redirecionamento/`sudo` → **CONFIRM** sempre (irreversível / blast grande).

### 2. TrustManager persistente e aprendido (reaproveita `core/trust.py`)
- Aprovar `calendar_event create` N vezes seguidas dentro de um horizonte → AUTO para aquela
  ferramenta (com kill-switch e _decay_ se parar de usar).
- Respeita `None ≠ 0.0`: sem histórico → **CONFIRM** (lado visível/seguro), nunca AUTO por default.
- Mora em `core/trust.py`; consumido por `RiskPolicy`.

### 3. Knob de nível de autonomia no config (`autonomy_level: low|standard|high`)
- Hoje não existe (confirmado em `core/config.py`). `standard` = atual. `low` = tudo CONFIRM.
  `high` = AUTO onde possível, mantendo irreversíveis em CONFIRM.
- Default `standard`. Mapa de categoria→nível num único lugar.

### 4. Unificar os dois enforcement points
- Mover a decisão de autonomia para `RiskPolicy` + `TrustManager` consumidos por Live e Agent
  Loop. Evita implementar a mesma feature duas vezes — e é exatamente onde nasce a falha
  silenciosa (uma das duas vias esquecida).

### 5. Auditoria via digesto / `action_logs`
- Ações AUTO devem gravar em `action_logs` com detalhe para desfazer. O digesto da Fase 2
  (`_attention_badges` / `core/digest.py`) é a superfície natural de "o que o assistente fez" —
  conecta autonomia com revisão pós-fato.

## Princípios de segurança (não negociáveis)
- **Fail-closed**: `autonomy_level` desconhecido ou trust não estabelecido → CONFIRM.
- **`None` ≠ `0.0`**: sinal de confiança ausente → CONFIRM, não AUTO.
- **Erra para o lado que se VÊ**: melhor o assistente perguntar e parecer "pouco autônomo" do
  que agir e ninguém descobrir.
- **Reversibilidade primeiro**: só vira AUTO o que dá para desfazer.

## Ordem sugerida
1. Unificar decisão em `RiskPolicy` (refactor, sem mudar comportamento) — abre caminho.
2. `autonomy_level` + mapeamento (baixo risco, alto valor).
3. Zonas de confiança no gate + reversibilidade (`write_file`/git).
4. TrustManager aprendido (Fase 2 do digesto já dá a UI de auditoria).
5. Estender o padrão `end_session_grace_sec` (anuncia-depois) para categorias AUTO.

## Fora de escopo nesta proposta
- Remover confirmação de ações irreversíveis (e-mail enviado, `rm`, `sudo`, `systemctl`).
- Autonomia cega sem auditoria.
- Mudar o envelope Jarvis (Fase 2 do digesto já cobre a parte de "falar por conta própria").
