# Contexto de Integração Btengine

## Objetivo da separação

- O repositório `btengine` fica responsável apenas por biblioteca de backtest genérica (engine + execução + adapters).
- O repositório `tbot_funding_arb` fica responsável por estratégia de funding/basis e pipelines do projeto.
- A estratégia de negócio (`funding/`) não deve depender de código de domínio do `btengine`, apenas da sua API pública.

## Repositórios

- Engine: `C:\4mti\Projetos\btengine` (`https://github.com/marcosarm/btengine`)
- Estratégia/app: `C:\4mti\Projetos\tbot_funding_arb`

## Contrato de uso (resumo)

- Instalar `btengine` antes de executar scripts do projeto de estratégia.
- Importações principais:
  - `btengine.engine.BacktestEngine`, `btengine.engine.EngineConfig`, `btengine.engine.EngineContext`
  - `btengine.broker.SimBroker`
  - `btengine.types.DepthUpdate`, `btengine.types.MarkPrice`, `btengine.types.Trade`
- A estratégia (`funding/basis_funding.py`) deve tratar:
  - sinalização
  - risco operacional
  - gestão de exposição e stops
- Execução realista é responsabilidade do engine (taker, maker, funding, fees, filas, janela temporal).

## Bootstrap local (Windows)

```bash
cd C:\4mti\Projetos\btengine
pip install -e .

cd C:\4mti\Projetos\tbot_funding_arb
pip install -e .
```

## Regras para não acoplar

- Evitar qualquer alteração de estratégia dentro de `src/btengine`.
- Evitar dependências do projeto `tbot_funding_arb` dentro de módulos do `btengine`.
- Manter qualquer lógica de seleção de setup, parâmetros táticos e regras de saída em `funding/*` e `scripts/*`.

## Fontes de verdade

- Especificação funcional: `SPECIFICATION.md`
- Documentação de integração: `docs/btengine/reuse_in_other_projects.md`
- Documentação de execução: `docs/btengine/quickstart.md`
