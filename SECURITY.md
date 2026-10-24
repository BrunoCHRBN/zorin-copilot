# Política de Segurança — Zorin Copilot

[🇺🇸 English Version](SECURITY.en.md) | [🇧🇷 Versão em Português](SECURITY.md)

Como o **Zorin Copilot** interage diretamente com barramentos do sistema operacional, simulação de entrada de periféricos, leitura de telas e gerenciamento de arquivos locais, a segurança é tratada como requisito arquitetural de primeira classe.

---

## 1. Relato de Vulnerabilidades

Se você identificar uma falha de segurança que possa comprometer a integridade de dados, evasão de permissões ou bypass de políticas de segurança:

1. **Não abra uma issue pública.**
2. Envie um relatório detalhado por e-mail para o mantenedor principal ou abra um Security Advisory privado no GitHub.
3. Inclua no relatório:
   - Descrição técnica do vetor de ataque ou falha de validação.
   - Prova de conceito (PoC) ou passos determinísticos para reprodução.
   - Ambiente operacional (distribuição Linux, compositor Wayland/X11, versão do Zorin Copilot).
   - Avaliação do impacto potencial.

---

## 2. Camadas de Defesa e Mecanismos de Proteção

O Zorin Copilot implementa quatro camadas independentes de contenção de segurança em tempo de execução:

### 2.1 Cerca Digital de Tela (`ScreenFenceManager`)
- **Módulo:** `src/zorin_copilot/core/fence.py`
- **Mecanismo:** Define restrições espaciais rígidas por coordenadas absolutas de tela e delimitadores de monitores. Ações de clique, movimentação de cursor ou inspeção de widgets direcionadas a áreas fora da cerca permitida (ou em zonas expressamente marcadas como restritas) são rejeitadas antes de qualquer despacho de input.
- **Parada de Emergência:** O atalho de sistema ou evento de aborto desativa imediatamente os drivers de injeção virtual (`libei` / `ydotool`).

### 2.2 Política de Classificação de Risco (`RiskPolicy`)
- **Módulo:** `src/zorin_copilot/shell/risk.py`
- **Mecanismo:** Toda chamada de ferramenta proposta pela IA é inspecionada determinísticamente antes da execução:
  - `SAFE`: Operações de leitura, inspeção de estado do desktop e navegação básica.
  - `CONFIRM`: Operações com potencial destrutivo ou de modificação de estado de terceiros (escrita em arquivos, execução de comandos shell, encerramento de processos, envio de mensagens, atalhos como `Alt+F4` ou `Ctrl+Q`).
- Em modo não-interativo ou CLI headless sem callback de aprovação explícito (`--dry-run` ou ausência de `--execute`), qualquer ação marcada como `CONFIRM` é bloqueada por padrão.

### 2.3 Pilha de Reversibilidade (`UndoStack`)
- **Módulo:** `src/zorin_copilot/shell/undo.py`
- **Mecanismo:** Ações que modificam arquivos locais ou reorganizam diretórios criam snapshots em memória ou arquivos de restauração prévia. Arquivos binários ou superiores a 1 MB não sofrem snapshot destrutivo e exigem confirmação explícita adicional.

### 2.4 Armazenamento Seguro de Credenciais
- **Módulo:** `src/zorin_copilot/core/google_auth.py`
- **Mecanismo:** Chaves de API e tokens de autenticação (OAuth2 Desktop app) utilizam a interface do Secret Service do Linux (via biblioteca `keyring`). Quando o serviço de cofre do sistema estiver indisponível, o sistema armazena o token exclusivamente com permissões POSIX restritas `0600` no diretório de configuração do usuário (`~/.config/zorin-copilot/`).

