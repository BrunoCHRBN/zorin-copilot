# Busca Nativa de Código com Ripgrep e Inspeção de Processos — Zorin Copilot

Este documento detalha a implementação das ferramentas nativas de alta densidade `search_code` e `system_processes`, projetadas para maximizar o orçamento adaptativo de passos do agente ReAct e do assistente de voz Gemini Live no desktop Linux.

---

## 1. O Problema Resolvido

Nas tarefas cotidianas de desenvolvimento e suporte no desktop Linux, o agente enfrentava dois grandes gargalos operacionais:

1. **Exaustão de Passos ao Navegar Código:** Para localizar onde uma função, classe ou variável estava definida, o agente precisava listar diretórios recursivamente (`list_directory`) e ler arquivos um por um (`read_file`). Isso consumia de 5 a 15 passos do orçamento de passos apenas para achar a linha correta.
2. **Opacidade dos Processos do Sistema:** O assistente não possuía uma forma segura, leve e somente leitura (`RiskLevel.SAFE`) de checar se servidores de desenvolvimento (como FastAPI, Node/Express, Vite, Ollama, Docker) estavam ativos e consumindo recursos de CPU/RAM, dependendo de comandos Bash arbitrários sujeitos à política de confirmação de risco.

---

## 2. Arquitetura da Solução

### 2.1 Módulo Central: `src/zorin_copilot/core/system_inspect.py`

* **`CodeSearchEngine`:**
  * **Detecção Automática do Ripgrep (`rg`):** Se `rg` estiver presente no sistema, utiliza execução nativa com saída estruturada `--json`, filtrando flags como `--case-sensitive`, `--fixed-strings`, `--glob` e linhas de contexto `-C`.
  * **Fallback Determinístico em Python:** Caso o binário do ripgrep não esteja instalado ou ocorra erro de execução, o motor aciona automaticamente um rastreador em Python com expressão regular compilada, ignorando pastas pesadas (`.git`, `node_modules`, `.venv`, `__pycache__`, `target`).
  * **Proteção contra Timeouts:** Subprocessos isolados com limite estrito de tempo (padrão 8.0s/10.0s), evitando travamentos de I/O em discos lentos ou pastas de rede.
  * **Formatação de Resumo Otimizada para LLMs:** Saída condensada agrupada por arquivo, com números de linha e trechos de código limpos para economia de tokens.

* **`ProcessInspector`:**
  * **Inspeção de Baixo Overhead:** Consulta tabelas de processos via `ps -eo pid,ppid,%cpu,%mem,user,comm,args --sort=...`.
  * **Fallback Direto via `/proc`:** Se o comando `ps` não estiver disponível no PATH, o inspetor lê os metadados diretamente do pseudofilesystem `/proc/[pid]/comm` e `cmdline`.
  * **Filtragem Inteligente:** Busca case-insensitive em nome do comando, argumentos completos, usuário ou PID numérico.
  * **Formatação Tabular Alinhada:** Tabela textual compacta com colunas `PID`, `%CPU`, `%MEM`, `USER`, `COMMAND` e `ARGS`.

---

## 3. Ferramentas Registradas no `ToolRegistry`

Ambas as ferramentas são classificadas como `RiskLevel.SAFE` (leitura segura sem mutação do sistema):

| Ferramenta | Parâmetros | Descrição |
| :--- | :--- | :--- |
| `search_code` | `pattern` (str, obrigatório)<br>`path` (str, opcional, padrão `.` )<br>`file_glob` (str, opcional, ex: `*.py`)<br>`case_sensitive` (bool, padrão `false`)<br>`is_regex` (bool, padrão `true`)<br>`max_results` (int, padrão `30`)<br>`context_lines` (int, padrão `1`) | Pesquisa de alta performance em arquivos de código com números de linha e contexto. |
| `system_processes` | `filter` (str, opcional)<br>`sort_by` (str, `cpu`, `memory` ou `pid`, padrão `cpu`)<br>`limit` (int, padrão `20`) | Lista processos em execução, consumo de CPU e RAM no sistema. |

---

## 4. Integração Visual & Gemini Live por Voz

1. **GUI Nativa Libadwaita (`AgentExecutionWidget`):**
   * `search_code`: Rótulo amigável *"Buscar no Código"* com ícone simbólico `system-search-symbolic`.
   * `system_processes`: Rótulo amigável *"Processos do Sistema"* com ícone `utilities-system-monitor-symbolic`.
   * Exibição limpa de parâmetros no cabeçalho do passo (ex: `"calculate" em src (*.py)` ou `filtro "python" (top 10)`).

2. **Gemini Live Voice Client (`ai/live.py`):**
   * Integradas diretamente à especificação `LIVE_TOOLS_DECLARATION`.
   * Despacho dinâmico via voz: comandos como *"Copilot, procura onde a classe ToolRegistry está definida no código"* ou *"Copilot, tem algum processo do python consumindo muita memória?"* executam as ferramentas instantaneamente e alimentam a síntese de áudio.

---

## 5. Exemplos de Uso no Modo Agente

```json
// Busca de código:
{
  "tool": "search_code",
  "args": {
    "pattern": "class ToolRegistry",
    "path": "src",
    "file_glob": "*.py",
    "max_results": 5
  },
  "rationale": "Localizando a definição do registro de ferramentas para inspecionar os métodos disponíveis."
}

// Inspeção de processos:
{
  "tool": "system_processes",
  "args": {
    "filter": "ollama",
    "sort_by": "memory",
    "limit": 5
  },
  "rationale": "Verificando se o servidor Ollama está em execução e quanta memória RAM está alocando."
}
```
