# Plano de Implementação — Aprimoramento de Skills e Aplicabilidade do Agente em Infraestrutura

> Roteiro orientador e sequencial para elevar a **usabilidade** das skills e a **aplicabilidade** do agente em domínios de infraestrutura (redes, nuvem, banco de dados, servidores, contêineres, observabilidade/segurança).

**Legenda de prioridade**
- 🔴 **ALTA** — maior impacto; desbloqueia ou habilita outras etapas; faça primeiro.
- 🟠 **MÉDIA** — impacto relevante; ROI visível; execute após as fundações.
- 🟡 **BAIXA/MENOR** — consolidação e polish; execute por último.

**Princípio norteador:** nunca aplicar mudança destrutiva sem *dry-run* + confirmação; nunca assumir estado — sempre consultar o sistema vivo antes de propor alteração.

---

## Fase 0 — Fundação de Segurança e Usabilidade (habilita tudo)

### Etapa 1 🔴 — Padrão obrigatório de modo leitura/escrita + dry-run + confirmação
- **Objetivo:** Garantir que nenhuma skill execute ação destrutiva (aplicar config, dropar tabela, deletar recurso) sem revisão humana e sem simulação prévia.
- **Tarefas principais:**
  1. Definir contrato comum de skills: todo comando de escrita expõe `--dry-run`/equivalente e gera um *diff* legível da mudança.
  2. Implementar confirmação em duas etapas (mostrar diff → pedir confirmação explícita) para ações de impacto.
  3. Criar utilitário de *rollback script* automático embutido nas skills de configuração.
- **Dependências:** Nenhuma (ponto de partida).
- **Ordem de execução:** 1.ª — base de tudo; precede Fases 1–4.

### Etapa 2 🔴 — Explicabilidade pós-ação e recuperação de erro guiada
- **Objetivo:** Toda skill deve relatar *o que* fez, *por que*, o *risco residual* e, em falha, diagnosticar causa e propor próximo passo.
- **Tarefas principais:**
  1. Padronizar saída pós-ação (resumo + risco + logs relevantes).
  2. Criar catálogo de erros comuns por domínio (permissão, syntax, conectividade) e resposta guiada.
  3. Adicionar "próximo passo sugerido" automático em caso de falha.
- **Dependências:** Etapa 1 (reutiliza o contrato de ações).
- **Ordem de execução:** 2.ª.

### Etapa 3 🟠 — Documentação embutida + exemplos prontos por skill
- **Objetivo:** Reduzir tempo de descoberta; cada skill traz 2–3 exemplos copiáveis do domínio real.
- **Tarefas principais:**
  1. Template de SKILL.md com seção "Exemplos prontos".
  2. Revisar skills existentes para incluir exemplos concretos.
  3. Gerar índice navegável de skills por domínio de infra.
- **Dependências:** Etapa 2.
- **Ordem de execução:** 3.ª.

---

## Fase 1 — Conectividade e Aplicabilidade (o "cano" com sistemas reais)

### Etapa 4 🔴 — Conectores MCP para Nuvem (AWS / Azure / GCP)
- **Objetivo:** Permitir que o agente leia estado e aplique mudanças em provedores de nuvem de forma auditada.
- **Tarefas principais:**
  1. Configurar conector com credenciais *least-privilege* (roles limitadas).
  2. Mapear operações suportadas (leitura de recursos, `terraform plan`, custo).
  3. Validar em sandbox antes de produção.
- **Dependências:** Etapa 1 (dry-run/confirmação obrigatória para escrita).
- **Ordem de execução:** 4.ª — maior alavanca de aplicabilidade.

### Etapa 5 🔴 — Conectores MCP para Redes (SSH / Netconf / API de fabricantes)
- **Objetivo:** Conectar o agente a roteadores, switches e firewalls para diagnóstico e configuração.
- **Tarefas principais:**
  1. Estabelecer acesso SSH/Netconf com chaves e bastion.
  2. Definir whitelist de comandos de leitura seguros.
  3. Integrar com a Etapa 1 para config de escrita com rollback.
- **Dependências:** Etapa 1.
- **Ordem de execução:** 5.ª — habilita a skill de redes (Etapa 8).

### Etapa 6 🟠 — Conectores para Banco de Dados
- **Objetivo:** Ler schema/estado e propor migrações/tuning com segurança.
- **Tarefas principais:**
  1. Conexão via drivers seguros com usuário de leitura + usuário de migração separado.
  2. Bloquear `DROP`/`TRUNCATE` em produção sem janela aprovada.
  3. Habilitar `EXPLAIN`/`SHOW` antes de qualquer `ALTER`.
- **Dependências:** Etapa 1.
- **Ordem de execução:** 6.ª — habilita Etapa 12.

### Etapa 7 🟠 — Execução remota segura + auditoria
- **Objetivo:** Toda execução em host real é registrada, com privilégio mínimo e reversível.
- **Tarefas principais:**
  1. Camada de auditoria (log imutável de comandos enviados).
  2. Sandbox/branch de execução para comandos não validados.
  3. Policy de *least-privilege* por conector.
- **Dependências:** Etapas 4, 5, 6.
- **Ordem de execução:** 7.ª.

---

## Fase 2 — Skills de Diagnóstico de Alto Impacto (MTTR imediato)

### Etapa 8 🔴 — Skill de diagnóstico de redes
- **Objetivo:** Receber sintoma ("VPN caiu", "latência alta") e retornar hipóteses ranqueadas + passos.
- **Tarefas principais:**
  1. Sequência automatizada: `ping`/`mtr`/`traceroute`, análise BGP/ARP, checagem de firewall.
  2. Ranqueamento de causas prováveis com evidência.
  3. Sugestão de correção com dry-run (via Etapa 5).
- **Dependências:** Etapas 1, 5.
- **Ordem de execução:** 8.ª.

### Etapa 9 🔴 — Skill de revisão/validação de IaC (Terraform / Pulumi)
- **Objetivo:** `plan` + detecção de drift, recursos órfãos e violações de política.
- **Tarefas principais:**
  1. Executar `plan` e parsear mudanças críticas.
  2. Detectar drift entre estado e realidade.
  3. Checar politicas (S3 público, portas expostas) contra benchmarks.
- **Dependências:** Etapas 1, 4.
- **Ordem de execução:** 9.ª.

### Etapa 10 🟠 — Skill de diagnóstico Kubernetes
- **Objetivo:** Diagnosticar `CrashLoopBackOff`, OOMKill, problemas de rede de service com causa raiz.
- **Tarefas principais:**
  1. Coletar logs/events/descrição de pods.
  2. Correlacionar com limites de recursos e probes.
  3. Propor correção de manifesto.
- **Dependências:** Etapa 1 (kubeconfig com permissão mínima).
- **Ordem de execução:** 10.ª.

---

## Fase 3 — Skills de Otimização (ROI visível)

### Etapa 11 🔴 — Skill de otimização de custo de nuvem
- **Objetivo:** Identificar instâncias subutilizadas, oportunidades Reserved/Spot e gerar relatório de savings.
- **Tarefas principais:**
  1. Coletar métricas de utilização por recurso.
  2. Recomendar rightsizing e reservas.
  3. Estimar economia mensal atribuível.
- **Dependências:** Etapas 4, 7.
- **Ordem de execução:** 11.ª.

### Etapa 12 🔴 — Skill de tuning de queries / migração segura de banco
- **Objetivo:** Receber query lenta → plan de execução → índices/rewrite propostos; migrações com `EXPLAIN` e trava em tabelas grandes.
- **Tarefas principais:**
  1. `EXPLAIN` e análise de plano.
  2. Gerar/validar migrations com estimativa de impacto.
  3. Sugerir janela de manutenção para locks.
- **Dependências:** Etapas 1, 6.
- **Ordem de execução:** 12.ª.

### Etapa 13 🟠 — Skill de hardening de servidores
- **Objetivo:** Aplicar CIS/STIG via Ansible com checklist e exceções documentadas.
- **Tarefas principais:**
  1. Playbook de hardening parametrizável.
  2. Checklist de conformidade com exceções justificadas.
  3. Dry-run (`--check`) + relatório de Compliance Score.
- **Dependências:** Etapas 1, 7.
- **Ordem de execução:** 13.ª.

---

## Fase 4 — Consolidação e Capacidades Transversais

### Etapa 14 🟠 — Skill de observabilidade / correlação
- **Objetivo:** Correlacionar logs/metrics/traces para triagem rápida.
- **Tarefas principais:**
  1. Integrar fontes (Prometheus, logs, APM).
  2. Gerar hipótese de causa raiz a partir de correlação.
- **Dependências:** Etapa 7.
- **Ordem de execução:** 14.ª.

### Etapa 15 🟠 — Skill de resposta a incidentes (runbook assistido)
- **Objetivo:** Guiar execução de runbooks com passos validados e rollback.
- **Tarefas principais:**
  1. Importar/cadastrar runbooks por tipo de incidente.
  2. Executar passos com confirmação e registro.
- **Dependências:** Etapas 1, 14.
- **Ordem de execução:** 15.ª.

### Etapa 16 🟡 — Composabilidade entre skills + gatilho contextual
- **Objetivo:** Encadear skills (ex.: descobrir topologia → gerar Terraform → validar plan) e detectar intenção proativamente.
- **Tarefas principais:**
  1. Protocolo de orquestração de skills.
  2. Detector de intenção que sugere a skill certa automaticamente.
- **Dependências:** Etapas 8–15 (skills individuais prontas).
- **Ordem de execução:** 16.ª.

---

## Fase 5 — Validação e Critérios de Sucesso

### Etapa 17 🔴 — Instrumentação de métricas de sucesso
- **Objetivo:** Medir se o roteiro entregou valor.
- **Tarefas principais:**
  1. Instrumentar: MTTR por domínio, % de ações com dry-run, taxa de reversão, % de infra sob IaC validada, economia de custo, compliance score.
  2. Dashboard de acompanhamento.
- **Dependências:** Etapas 1–16.
- **Ordem de execução:** 17.ª — executar em paralelo à adoção.

### Etapa 18 🟠 — Programa piloto + feedback loop
- **Objetivo:** Validar em ambiente real, coletar dor da equipe e ajustar.
- **Tarefas principais:**
  1. Piloto com 1–2 domínios (redes + nuvem).
  2. Coletar feedback de usabilidade e aplicabilidade.
  3. Ajustar prioridades das Fases 3–4 com base em dados.
- **Dependências:** Etapas 8, 9, 11.
- **Ordem de execução:** 18.ª.

---

## Resumo da sequência lógica (ordem de execução)

| # | Etapa | Fase | Prioridade | Dependências |
|---|-------|------|-----------|--------------|
| 1 | Modo leitura/escrita + dry-run + confirmação | 0 | 🔴 | — |
| 2 | Explicabilidade + recuperação de erro | 0 | 🔴 | 1 |
| 3 | Docs embutidas + exemplos | 0 | 🟠 | 2 |
| 4 | Conectores Nuvem (MCP) | 1 | 🔴 | 1 |
| 5 | Conectores Redes (MCP) | 1 | 🔴 | 1 |
| 6 | Conectores Banco | 1 | 🟠 | 1 |
| 7 | Execução remota segura + auditoria | 1 | 🟠 | 4,5,6 |
| 8 | Skill diagnóstico de redes | 2 | 🔴 | 1,5 |
| 9 | Skill revisão IaC (Terraform) | 2 | 🔴 | 1,4 |
| 10 | Skill diagnóstico K8s | 2 | 🟠 | 1 |
| 11 | Skill otimização de custo nuvem | 3 | 🔴 | 4,7 |
| 12 | Skill tuning/migração banco | 3 | 🔴 | 1,6 |
| 13 | Skill hardening servidores | 3 | 🟠 | 1,7 |
| 14 | Skill observabilidade | 4 | 🟠 | 7 |
| 15 | Skill resposta a incidentes | 4 | 🟠 | 1,14 |
| 16 | Composabilidade + gatilho contextual | 4 | 🟡 | 8–15 |
| 17 | Instrumentação de métricas | 5 | 🔴 | 1–16 |
| 18 | Programa piloto + feedback | 5 | 🟠 | 8,9,11 |

**Ações de maior impacto (foco inicial):** Etapas 1, 4, 5, 8, 9, 11, 12, 17 — são as que desbloqueiam as demais e entregam MTTR, custo e segurança visíveis primeiro.

---

## Critérios de sucesso esperados (meta)

- **Usabilidade:** tempo para encontrar a skill certa < 1 interação; 100% das ações destrutivas com dry-run + confirmação.
- **Aplicabilidade:** cobertura dos conectores dos 4 domínios prioritários; execução remota 100% auditada.
- **Confiança:** taxa de reversão de mudanças < 2%; zero incidentes causados por ação do agente não confirmada.
- **Domínio Redes:** redução de MTTR; % de incidentes triados sem sênior.
- **Domínio Nuvem:** % de infra sob IaC validada; economia mensal atribuível; redução de achados críticos de segurança.
- **Domínio Banco:** redução de tempo de query; 100% de migrations com `EXPLAIN`; zero locks em produção sem janela.
- **Domínio Servidores:** compliance score; redução de incidentes por config; tempo de provisioning.
