# Estimativa de Implementação — Integração do Jev (TypeSafe AI) no Zorin Copilot

> Análise de esforço, tempo, complexidade e adequação de adicionar o "System One Model"
> Jev como nó de decisão do Zorin Copilot.
> Data: 2026-09-20. Baseado na leitura do código atual (`src/zorin_copilot/...`).

## Resumo executivo

A integração é **tecnicamente fácil** (há pontos de encaixe limpos) mas o **valor
agregado é baixo para o núcleo do produto**, porque o projeto **já resolve
localmente e de graça** os três casos de uso propostos para o Jev (roteamento,
gate de segurança e limiar de confiança → autonomia). O Jev só faz sentido como
**camada semântica opcional e estritamente off-by-default**, consultada apenas
quando os classificadores determinísticos locais ficam em dúvida.

Veredito: **faça só como enhancer opcional; não como substituto.** Esforço real de
~3–5 dias-dev para produção, ou 1–2 dias para um spike.

## O que o projeto JÁ tem (e que sobrepõe o Jev)

| Papel proposto para o Jev | Módulo nativo atual | Como funciona | Custo |
|---|---|---|---|
| Roteamento de intenção (`Choice`) | `ai/fast_rnn_router.py` → `FastRNNRouter.classify()` | Roteador recorrente (GRU/RNN) com regex ponderados; < 0,5 ms; devolve `intent` + `confidence` + `is_fast_path` | Zero, local, offline |
| Roteamento local ↔ nuvem | `ai/agent_router.py` → `classify_objective()` | Léxico determinístico (verbos/conectores/markers) | Zero, local |
| Gate de segurança (`Noul`: "ação insegura?") | `shell/risk.py` → `RiskPolicy.classify()` | Heurística nome+args → `SAFE`/`CONFIRM`; consultado antes de executar | Zero, local |
| Rede de segurança de privacidade | `core/privacy_gate.py` → `PrivacyGatekeeper` | Bloqueia apps/janelas sensíveis e mascara segredos (O(1), regex pré-compiladas) | Zero, local |

Conclusão: os três usos sugeridos no brainstorm (guardrail, roteamento, confiança→autonomia)
**já estão cobertos** por lógica determinística que o projeto escolheu explicitamente
para ser rápida, gratuita, offline e privada — exatamente os valores do Zorin Copilot
("Privacidade Híbrida", local-first).

## Estimativa de esforço de desenvolvimento

Como os pontos de encaixe existem, o Jev entra como **fonte de decisão opcional** atrás
dos classificadores atuais (fallback em casos ambíguos), não no caminho quente.

| Componente | LOC aprox. | Dias-dev | Detalhe |
|---|---|---|---|
| `ai/jev_client.py` (wrapper do SDK/typesafe) | 150–250 | 1,0 | Mapeia `state`+`questions` → `Noul`/`Choice`/`Score`; retry/timeout; degrada p/ caminho determinístico |
| Encaixe em `RiskPolicy` + `FastRNNRouter` (modo dúvida) | 80–120 | 1,0 | Só chama Jev quando confiança local < limiar; nunca bloqueia o loop |
| Config (`jev_api_key`, `jev_enabled`, `jev_mode`) | 40 + docs | 0,5 | Off-by-default; modo `hosted` ou `nanolocal` |
| UI: toggle + indicador "Jev decidiu" | 60–100 | 0,5–1,0 | Em `model_selector` / settings; opcional |
| Testes (unit + integração atrás de flag) | ~150 | 1,0 | Garante fallback quando API cai/está sem key |
| Hardening (latência, rate-limit, drift) | — | 0,5–1,0 | Chamada não-bloqueante ou só em ambíguo |
| **Total (produção)** | **~600–800** | **3–5** | Revisão + testes inclusos |
| **Spike / PoC** | **~300** | **1–2** | Sem UI nem hardening completo |

## Tempo estimado (calendário)

- **PoC funcional:** 2–3 dias.
- **Produção (testado, off-by-default, com fallback):** 1–2 semanas (inclui revisão e iteração).

## Complexidade técnica: **BAIXA–MÉDIA**

As partes difíceis (roteamento, gate de risco, limiares de confiança) **já existem**.
A complexidade real está em:

1. **Degradação graciosa** — se a API do Jev cair ou estiver sem chave, o agente deve
   continuar pelo caminho determinístico sem nunca travar o loop.
2. **Orçamento de latência** — chamada síncrona no hot path adicionaria ~0,1 s (hosted)
   a *toda* decisão; logo, Jev só em casos de baixa confiança local ou em paralelo.
3. **Manter estritamente opt-in** — usuários offline/privados não podem ser afetados.
4. **Evitar drfit** — manter dois classificadores (regex + Jev) consistentes.

## Adequação e utilidade para o uso diário

### Benefícios (reais, mas estreitos)
- **Nuance semântica** que escapa a regex/palavras-chave: ex. "manda ver e some com isso"
  (apagar) sem gatilho léxico — o `RiskPolicy` atual poderia deixar passar.
- **Segunda opinião** de segurança em linguagem natural ambígua.

### Trade-offs
- **Custo por chamada** (modo hosted ~US$ 0,00008; NanoJev local exige GPU que a maioria
  dos usuários Zorin não tem).
- **Latência** se síncrono no hot path.
- **Nova dependência externa / lock-in** numa API em *early access* (lançada 15/set/2026).
- **Contradição com o posicionamento local-first/privacidade** se usado em modo hosted.
- **Redundância** — manter classificador regex + Jev multiplica superfície de manutenção.

### Limitações
- Jev é **early access**; API e preços podem mudar; benchmarks são da própria TypeSafe.
- Hosted precisa de API key; local (`NanoJev`) precisa de GPU.
- É um **"juiz", não um planejador** — não substitui o LLM no planejamento de ações.
- Devolve probabilidades → você ainda precisa de limiares (que o projeto já tem via
  `is_fast_path` / `confidence`).

## Veredito e recomendação

**Não integre o Jev no núcleo do loop** — ele seria redundante com `FastRNNRouter`,
`AgentRouter` e `RiskPolicy`, e reintroduziria custo/rede/latência/dependência para um
problema já resolvido localmente.

Se mesmo assim houver interesse, faça como **enhancer estritamente opcional**:
- `jev_enabled = false` por padrão.
- Consulte Jev **apenas** quando `FastRNNRouter`/`RiskPolicy` ficam abaixo do limiar de
  confiança; nunca no caminho quente; fallback instantâneo ao caminho determinístico.
- Modo `nanolocal` (GPU) preferível a `hosted` para preservar privacidade.

### Alternativa recomendada (melhor ROI para este projeto)
Em vez de Jev, **estenda os classificadores locais com um pequeno modelo de embedding
local** (ex.: `sentence-transformers` / modelo GRU já existente no `FastRNNRouter`):
- Dá "roteamento semântico" sem custo, latência de rede ou saída de dados.
- Alinha-se 100% aos valores local-first/privacidade do Zorin Copilot.
- Aproveita a infraestrutura já presente (`FastRNNRouter`, `PrivacyGatekeeper`).
