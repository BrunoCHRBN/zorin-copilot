# Unificação do Gemini Live Voice com ToolRegistry e MCP

Este documento descreve a arquitetura técnica da **Unificação Total do Gemini Live Voice**, consolidando o `ToolRegistry` como a fonte única da verdade para execução de ferramentas, controle do sistema, Git nativo, terminal bash e interoperabilidade com servidores MCP por comandos de voz em tempo real.

---

## 1. Visão Geral da Arquitetura

Anteriormente, o `GeminiLiveClient` (`ai/live.py`) e o `ToolRegistry` (`ai/agent_tools.py`) mantinham listas separadas de ferramentas, resultando em duplicação de código e impossibilitando o uso de ferramentas do Modo Agente (como Git, execução de scripts ou ferramentas de servidores MCP dinâmicos) durante conversas ao vivo por áudio e vídeo.

A partir desta atualização:
1. **`ToolRegistry` como Repositório Unificado:** Contém 53 ferramentas nativas cobrindo todo o sistema operacional, janelas, terminal, Git, contatos, memória, busca acadêmica e casa inteligente.
2. **Setup WebSocket Dinâmico (`_live_tools_payload`):** Na inicialização da sessão `BidiGenerateContentSetup`, o Gemini Live recebe tanto as ferramentas base quanto quaisquer ferramentas expostas dinamicamente pelo `ToolRegistry` e servidores MCP conectados (`mcp__*`).
3. **Despacho Transparente (`_dispatch_tool`):** Chamadas de ferramentas não tratadas explicitamente pelo loop de áudio/vídeo são delegadas a `self.tool_registry.call(name, args)` com enriquecimento automático de mensagens amigáveis para síntese de voz (TTS).
4. **Portão de Risco Unificado (`RiskPolicy`):** Ações de risco (ex.: `run_command` com `rm`, `sudo`, `kill`, `git push` ou modificações destrutivas de arquivos) continuam protegidas pelo fluxo de confirmação verbal via `confirmation_id` e ferramenta `confirm_action`.

```
                    ┌────────────────────────────────────────────────────────┐
                    │               Usuário (Voz / Microfone)                │
                    └───────────────────────────┬────────────────────────────┘
                                                │
                                                ▼
                    ┌────────────────────────────────────────────────────────┐
                    │       Gemini Live WebSocket (BidiGenerateContent)      │
                    └───────────────────────────┬────────────────────────────┘
                                                │ tool_call("git_status", {})
                                                ▼
                    ┌────────────────────────────────────────────────────────┐
                    │               GeminiLiveClient._dispatch_tool          │
                    └─────────────┬───────────────────────────┬──────────────┘
                                  │                           │
                   [Ação Segura]  │            [Ação Sensível]│
                                  ▼                           ▼
                    ┌───────────────────────────┐  ┌─────────────────────────┐
                    │     ToolRegistry.call     │  │  _require_confirmation  │
                    │                           │  │  (Gera confirmation_id) │
                    └─────────────┬─────────────┘  └─────────────┬───────────┘
                                  │                              │
         ┌────────────────────────┼────────────────────────┐     │ Confirmação verbal ("Sim")
         │                        │                        │     ▼
         ▼                        ▼                        ▼  ┌─────────────────────────┐
┌──────────────────┐    ┌──────────────────┐    ┌──────────┐  │ confirm_action(approve) │
│ Subprocess / Git │    │  WindowManager   │    │ Servidor │  └──────────┬──────────────┘
│ & Terminal Bash  │    │  & Wayland/X11   │    │ MCP Tool │             │
└──────────────────┘    └──────────────────┘    └──────────┘             ▼
                                                        Executa ação aprovada
```

---

## 2. Novas Ferramentas Disponíveis por Voz

| Ferramenta | Descrição | Exemplo de Comando por Voz | Nível de Risco |
| :--- | :--- | :--- | :--- |
| `git_status` | Verifica o estado da árvore de trabalho e branch atual | *"Zorin, qual é o status do repositório Git atual?"* | Seguro (`SAFE`) |
| `git_log` | Consulta os últimos commits com autor e resumo | *"Quais foram os últimos 3 commits realizados no projeto?"* | Seguro (`SAFE`) |
| `git_diff` | Mostra alterações de código não commitadas | *"O que foi modificado nos arquivos desde o último commit?"* | Seguro (`SAFE`) |
| `run_command` | Executa comandos no terminal Linux (Bash) | *"Rode `uptime` no terminal e me diga o resultado"* | Adaptativo (Gera `CONFIRM` para `rm`, `sudo`, `kill`, etc.) |
| `window_management` | Minimiza, maximiza, restaura ou posiciona janelas | *"Coloca o VS Code ocupando a metade direita da tela"* | Mutante (`SAFE` / sem confirmação) |
| `mcp__*` | Ferramentas dinâmicas de servidores MCP ativos | *"Busque no banco SQLite os últimos 5 clientes cadastrados"* | Conforme classificação MCP |
| `system_control` | Ajuste de volume, temas e bloqueio de sessão | *"Aumenta o volume do computador em 10%"* | Seguro (`SAFE`) |
| `contact_save` / `contact_lookup` | Consulta e armazena contatos com apelidos | *"Qual é o e-mail do meu orientador na memória?"* | Mutante (`SAFE`) |
| `memory_remember` | Memorização imediata de preferências | *"Lembre-se que eu sempre prefiro respostas em tópicos"* | Mutante (`SAFE`) |

---

## 3. Formato de Resposta TTS Otimizado

Para que o sintetizador de voz do Gemini Live (Aoede, Puck, Zephyr, etc.) fale as respostas com naturalidade e concisão, todas as ferramentas implementadas no `ToolRegistry` fornecem:
- `success: bool`: Indicador booleano padronizado de sucesso.
- `ok: bool`: Compatibilidade total com o loop ReAct do Modo Agente.
- `message: str`: Frase direta, contextualizada e humanizada pronta para ser lida em voz alta.

Exemplo de saída de `_tool_git_status`:
```python
{
    "ok": True,
    "success": True,
    "path": "/home/bruno-vsantos/zorin-copilot",
    "status": "## main...origin/main\n M src/zorin_copilot/ai/live.py",
    "message": "Status Git em 'zorin-copilot':\n## main...origin/main\n M src/zorin_copilot/ai/live.py"
}
```

---

## 4. Garantias e Validação

- **Compatibilidade Reversa Total:** Nenhum comportamento existente do Modo Agente ou da UI do Gemini Live foi quebrado.
- **1.570 Testes Automatizados:** A suíte completa de testes unitários e de integração (`tests/test_live.py`, `tests/test_agent_tools.py`, `tests/test_mcp.py`, `tests/test_home_assistant.py`, etc.) foi executada e validada com 100% de aprovação.
