## Descrição / Description

<!-- PT: Resumo claro das alterações introduzidas e motivação técnica. -->
<!-- EN: Clear summary of changes introduced and technical rationale. -->

## Tipo de Mudança / Type of Change

- [ ] Correção de bug / Bug fix
- [ ] Nova funcionalidade / New feature
- [ ] Refatoração de código / Code refactoring
- [ ] Melhoria de documentação / Documentation improvement
- [ ] Ajuste de testes ou CI / Test or CI adjustment

## Como foi testado? / How was it tested?

- [ ] Suíte unitária e de integração via pytest:
  ```bash
  .venv/bin/python -m pytest -q tests/test_<nome>.py
  ```
- [ ] Verificação de isolamento de teste (`ZORIN_TEST_MODE=1` ativado via sandbox)
- [ ] Teste em ambiente gráfico virtual ou display real (Wayland / X11)
- [ ] Verificação de consumo de memória (< 900 MB, sem vazamento de handles D-Bus)

## Checklist

- [ ] Código adere aos padrões do projeto (sem imports não-tipados no núcleo, preservando abstrações).
- [ ] Termos técnicos mantidos no idioma original.
- [ ] Documentação atualizada (README, ARCHITECTURE ou guias em `docs/`).
- [ ] Sem quebras de retrocompatibilidade em configurações (`config.json`) ou esquemas de memória (`memory.db`).
- [ ] Todos os novos testes estão passando.

