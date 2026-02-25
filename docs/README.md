# Documentacao

## Projeto atual (`tbot_funding_arb`)

- Documento funcional principal: `SPECIFICATION.md`
- Contexto de integracao com engine: `BTENGINE_CONTEXT.md`
- Guia de uso rapido: `readme.md`

## Motor externo (`btengine`)

- Repositorio (privado): `git@github.com:marcosarm/btengine.git`
- Documentacao local do engine:
  - `C:\4mti\Projetos\btengine\docs\btengine\README.md`
  - `C:\4mti\Projetos\btengine\docs\btengine\quickstart.md`
  - `C:\4mti\Projetos\btengine\docs\btengine\api_reference.md`

## Operacao recomendada

1. Validar setup e credenciais (`.env`) no `tbot_funding_arb`.
2. Rodar `pytest -q` neste repositorio.
3. Rodar backtest batch com `scripts\run_backtest_basis_funding.py`.
4. Analisar CSV de saida (`status`, fills, round trips, pnl/fees/drawdown).
