# Btengine Integration Context

## Purpose
- `btengine` is the standalone backtest engine (private repo).
- `tbot_funding_arb` is the strategy/application repo.
- Strategy code must not live inside `btengine`.

## Repos
- Engine: `C:\\4mti\\Projetos\\btengine` (`git@github.com:marcosarm/btengine.git`, private)
- Strategy/app: `C:\\4mti\\Projetos\\tbot_funding_arb`

## Usage contract
- Install `btengine` before running strategy scripts.
- Core imports:
  - `btengine.engine.BacktestEngine`, `btengine.engine.EngineConfig`, `btengine.engine.EngineContext`
  - `btengine.broker.SimBroker`
  - `btengine.types.DepthUpdate`, `btengine.types.MarkPrice`, `btengine.types.Trade`
- Strategy (`funding/`) handles:
  - signals
  - risk rules
  - position management
- Execution realism is handled by the engine (taker/maker/funding/fees/book model).

## Local bootstrap (Windows)

```bash
cd C:\\4mti\\Projetos\\btengine
pip install -e .

cd C:\\4mti\\Projetos\\tbot_funding_arb
pip install -e .
```

## Do not couple
- Do not add strategy code inside `src\\btengine`.
- Do not add dependencies from `tbot_funding_arb` into `btengine`.

## References
- Strategy spec: `SPECIFICATION.md`
- Engine integration guide: `C:\\4mti\\Projetos\\btengine\\docs\\btengine\\reuse_in_other_projects.md`
- Engine quickstart: `C:\\4mti\\Projetos\\btengine\\docs\\btengine\\quickstart.md`
