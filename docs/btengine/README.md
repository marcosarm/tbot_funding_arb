# btengine (Motor de Backtest / Simulacao)

`btengine` e uma biblioteca Python pequena e direta para backtest orientado a eventos, com:

- stream de eventos unificado (orderbook L2, trades e mark price)
- streams opcionais adicionais (ticker, open interest, liquidations)
- book L2 em memoria (para impacto/VWAP e simulacao de taker)
- simulacao simples de execucao:
  - taker: consome profundidade do L2
  - maker: modelo aproximado de fila (queue ahead) preenchido via trade tape
- portfolio e PnL realizado + funding (perpetuos)
- adapter para o layout CryptoHFTData (Parquet em S3)

Objetivo: ser generico o suficiente para ser reutilizado como biblioteca em outros projetos/estrategias, sem acoplar a Binance/CCXT no core do motor.

## Reuso em outros projetos

O `btengine` foi organizado para ser importado como biblioteca, com baixo acoplamento:

- Core agnostico de exchange/dataset:
  - `btengine.engine`
  - `btengine.types`
  - `btengine.broker`
  - `btengine.marketdata`
  - `btengine.execution`
  - `btengine.analytics`
- Adapter de dataset separado em `btengine.data.*`:
  - hoje: `btengine.data.cryptohftdata`
  - novos datasets/exchanges podem entrar como novos adapters, sem mudar o core
- Scripts em `scripts/` sao exemplos/entrypoints, nao dependencia do core

Guia dedicado para integrar em outro repositorio:

- `docs/btengine/reuse_in_other_projects.md`

## Estado atual

O foco atual e:

- corretude basica e clareza de API
- replay realista o bastante para aproximar fills e custos (fees/funding)
- tooling de validacao do dataset no S3

Nao e (ainda) um simulador "exchange-grade" com latencia/ack/cancel delays, matching engine ou modelagem completa de microestrutura.

Ele ja inclui:

- simulacao taker por consumo de L2 (VWAP)
- self-impact para taker (reduz profundidade do book in-memory)
- modelo maker aproximado (fila + trade tape)
- delays deterministicas opcionais de submit/cancel (para evitar otimismo)

Mas ainda nao inclui matching engine completo ou overlay das nossas ordens no book.

## Navegacao da documentacao

- Quickstart: `docs/btengine/quickstart.md`
- Reuso em outros projetos: `docs/btengine/reuse_in_other_projects.md`
- Conceitos (eventos, tempo, streams): `docs/btengine/core_concepts.md`
- Adapter CryptoHFTData (S3/Parquet): `docs/btengine/crypto_hftdata.md`
- Modelo de execucao/fills e portfolio: `docs/btengine/execution_model.md`
- API reference (imports e objetos): `docs/btengine/api_reference.md`
- Scripts (validacao e replay): `docs/btengine/scripts.md`
- Plano de implementacao por fases: `docs/btengine/implementation_plan.md`

## Layout do codigo

- Core engine: `src/btengine/engine.py`
- Eventos (tipos): `src/btengine/types.py`
- Replay/merge de streams: `src/btengine/replay.py`
- Marketdata (L2 book): `src/btengine/marketdata/orderbook.py`
- Execucao (orders/fill): `src/btengine/broker.py`, `src/btengine/execution/*`
- Adapter CryptoHFTData: `src/btengine/data/cryptohftdata/*`

## Desenvolvimento local

Dependencias:

- Python 3.10+
- `pyarrow`, `numpy`, `pandas`

Rodar testes:

```bash
pytest -q
```
