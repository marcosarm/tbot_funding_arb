# Reuso em outros projetos

Este guia mostra como usar o `btengine` como biblioteca em outro repositorio, sem depender dos scripts deste projeto.

## 1) Formas de instalar

Escolha uma estrategia de distribuicao.

Opcao A: dependencia local (desenvolvimento paralelo):

```bash
pip install -e C:\\caminho\\btengine
```

Opcao B: dependencia via Git (recomendado para CI):

```bash
pip install "git+https://github.com/marcosarm/btengine.git@<commit-ou-tag>"
```

Opcao C: wheel interno (recomendado para ambiente controlado):

```bash
python -m build
pip install dist\\btengine-0.1.0-py3-none-any.whl
```

Notas:

- prefira pin por `tag` ou `commit` para reproducibilidade.
- `scripts/` nao e API publica; trate como exemplos.

## 2) Fronteira da API para consumo externo

Para reduzir risco de quebra entre versoes, importe apenas esta superficie:

- `btengine.BacktestEngine`, `btengine.EngineConfig`, `btengine.Strategy`
- `btengine.types` (eventos: `DepthUpdate`, `Trade`, `MarkPrice`, `Ticker`, `OpenInterest`, `Liquidation`)
- `btengine.broker.SimBroker`
- `btengine.execution.orders.Order`
- `btengine.analytics` (round trips, max drawdown)

Evite acoplar em atributos internos (`_privados`) de broker/book.

## 3) Contrato minimo de integracao

Para qualquer dataset/exchange, o motor precisa de:

1. stream de eventos ordenado por `event_time_ms` (UTC, ms)
2. eventos mapeados para os tipos do pacote `btengine.types`
3. estrategia implementando callbacks opcionais:
   - `on_start(ctx)`
   - `on_tick(now_ms, ctx)`
   - `on_event(event, ctx)`
   - `on_end(ctx)`

Se um callback nao for necessario, ele pode ser omitido.

## 4) Adapter proprio (dataset/exchange diferente)

Padrao recomendado:

1. ler a fonte original (S3, parquet local, kafka, etc.)
2. normalizar schema para os dataclasses de `btengine.types`
3. garantir ordenacao por `event_time_ms` por stream
4. fazer merge temporal com `btengine.replay.merge_event_streams`

Exemplo minimo:

```python
from __future__ import annotations

from typing import Iterable, Iterator

from btengine.replay import merge_event_streams
from btengine.types import DepthUpdate, MarkPrice, Trade


def iter_my_trades() -> Iterator[Trade]:
    # mapear fonte original -> Trade(event_time_ms=..., symbol=..., ...)
    yield from ()


def iter_my_depth() -> Iterator[DepthUpdate]:
    # mapear fonte original -> DepthUpdate(...)
    yield from ()


def iter_my_mark() -> Iterator[MarkPrice]:
    # mapear fonte original -> MarkPrice(...)
    yield from ()


def build_stream() -> Iterable[DepthUpdate | Trade | MarkPrice]:
    return merge_event_streams(iter_my_depth(), iter_my_trades(), iter_my_mark())
```

## 5) Bootstrap do engine no projeto consumidor

```python
from btengine.broker import SimBroker
from btengine.engine import BacktestEngine, EngineConfig

events = build_stream()  # do seu adapter

broker = SimBroker(
    maker_fee_frac=0.0004,
    taker_fee_frac=0.0005,
    submit_latency_ms=20,
    cancel_latency_ms=20,
)

engine = BacktestEngine(
    config=EngineConfig(
        tick_interval_ms=1000,
        trading_start_ms=None,
        trading_end_ms=None,
    ),
    broker=broker,
)

result = engine.run(events, strategy=my_strategy)
```

## 6) Ordem e execucao (realismo)

O `SimBroker` atual suporta:

- market/taker com consumo de book (`consume_taker_fill`, com self-impact)
- limit/maker com modelo aproximado de fila + tape de trades
- delays deterministicas de submit/cancel
- fees maker/taker
- funding aplicado em eventos `MarkPrice`

Ele nao e matching engine completo. Para estrategias ultra sensiveis a microestrutura, trate resultados como aproximacao conservadora.

## 7) Checklist antes de plugar em outro projeto

Checklist de dados:

1. validar range temporal e monotonicidade por stream
2. medir taxa de `prev_final_update_id` mismatch no orderbook
3. medir ocorrencia de book crossed e lado faltante
4. confirmar timezone UTC e unidade em ms

Checklist de simulacao:

1. executar smoke test curto (1h) com estrategia dummy
2. validar round-trips, fees e funding
3. validar reproducibilidade com seed/config fixa
4. comparar dias limpos vs dias com falhas de book

Checklist de operacao:

1. pin de versao (tag/commit)
2. lock de dependencias no projeto consumidor
3. baseline de regressao (csv de resultados por dia)

## 8) Estrategia recomendada para dias com book inconsistente

Quando houver mismatch/crossed:

- usar `scripts/run_backtest_batch.py --strict-book`
- manter reset em mismatch/crossed
- aplicar warmup/cooldown apos reset
- opcionalmente limitar spread com:
  - `--strict-book-max-spread`
  - `--strict-book-max-spread-bps`
  - `--strict-book-max-staleness-ms`

Isso reduz outliers de fill em dias degradados.

## 9) Compatibilidade e evolucao

Para manter reuso entre projetos:

- trate `btengine` como semver interno (ex: `0.1.x`)
- ao mudar assinatura de tipos/callbacks, versionar com bump explicito
- manter testes de contrato no projeto consumidor (imports + smoke replay)
