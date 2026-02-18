# API Reference (imports principais)

Este arquivo lista os objetos mais importantes e onde importa-los. O codigo tem type hints e docstrings; este e um mapa rapido.

## Superficie recomendada para consumo externo

Para reuso em outro projeto, prefira importar desta superficie (mais estavel):

- `btengine` (top-level): engine + tipos principais
- `btengine.engine`: `EngineContext` e `BacktestResult`
- `btengine.broker`: `SimBroker` e `Fill`
- `btengine.execution.orders`: `Order`
- `btengine.analytics`: round trips e drawdown
- `btengine.data.cryptohftdata`: apenas se for usar esse adapter especifico

Evite depender de atributos internos (`_privados`) ou estrutura interna de scripts.

## `btengine` (top-level)

Arquivo: `src/btengine/__init__.py`

```python
from btengine import BacktestEngine, EngineConfig, Strategy
from btengine import DepthUpdate, Trade, MarkPrice, Ticker, OpenInterest, Liquidation, Side
```

## Engine

Arquivo: `src/btengine/engine.py`

- `BacktestEngine(config: EngineConfig, broker: SimBroker | None = None)`
  - `.run(events: Iterable[Event], strategy: Strategy) -> BacktestResult`
- `EngineConfig`
  - `tick_interval_ms: int`
  - `trading_start_ms: int | None`
  - `trading_end_ms: int | None`
- `EngineContext`
  - `now_ms: int`
  - `books: dict[str, L2Book]`
  - `broker: SimBroker`
  - `mark: dict[str, MarkPrice]`
  - `ticker: dict[str, Ticker]`
  - `open_interest: dict[str, OpenInterest]`
  - `liquidation: dict[str, Liquidation]`
  - `is_trading_time() -> bool`
  - `book(symbol) -> L2Book`

## Tipos de eventos

Arquivo: `src/btengine/types.py`

- `DepthUpdate`
- `Trade`
- `MarkPrice`
- `Ticker`
- `OpenInterest`
  - `event_time_ms`: disponibilidade no clock do motor
  - `timestamp_ms`: timestamp medido do snapshot (dataset)
- `Liquidation`
- `Side = Literal["buy","sell"]`

## Replay / streams

Arquivo: `src/btengine/replay.py`

- `merge_event_streams(*streams) -> Iterator[EventLike]`
- `slice_event_stream(events, start_ms=None, end_ms=None) -> Iterator[EventLike]`

## Marketdata

Arquivo: `src/btengine/marketdata/orderbook.py`

- `L2Book`
  - `apply_depth_update(bid_updates, ask_updates)`
  - `best_bid()`, `best_ask()`, `mid_price()`
  - `impact_vwap(side, target_notional, max_levels=..., eps_notional=...)`

## Execucao e broker

Arquivos:

- `src/btengine/execution/orders.py`
  - `Order`
- `src/btengine/execution/taker.py`
  - `simulate_taker_fill(book, side, quantity, limit_price=None) -> (avg_price, filled_qty)` (puro, nao muta o book)
  - `consume_taker_fill(book, side, quantity, limit_price=None) -> (avg_price, filled_qty)` (mutante, com self-impact)
- `src/btengine/execution/queue_model.py`
  - `MakerQueueOrder` (modelo aproximado de maker fills)
- `src/btengine/broker.py`
  - `SimBroker` (fees + latencia + modelo maker conservador; ver `on_time()` + `submit()`/`cancel()`)
    - params: `maker_fee_frac`, `taker_fee_frac`
    - params: `submit_latency_ms`, `cancel_latency_ms`
    - params: `maker_queue_ahead_factor`, `maker_queue_ahead_extra_qty`, `maker_trade_participation`
  - `Fill`

## Portfolio

Arquivo: `src/btengine/portfolio.py`

- `Portfolio`
  - `positions: dict[str, Position]`
  - `realized_pnl_usdt`, `fees_paid_usdt`
  - `apply_fill(...)`
  - `apply_funding(symbol, mark_price, funding_rate) -> funding_pnl_usdt`

## Analytics (PnL / stats)

Pacote: `src/btengine/analytics/`

- `round_trips_from_fills(fills: list[Fill]) -> list[RoundTrip]`
- `summarize_round_trips(trades: list[RoundTrip]) -> RoundTripSummary`
- `max_drawdown(equity_curve: list[(time_ms, equity)]) -> float | None`

## Estrategia de exemplo (no repo)

Este repositorio inclui uma estrategia de exemplo que usa o `btengine`, mas nao faz parte do pacote `btengine` (para manter a biblioteca generica):

- `funding/basis_funding.py`
  - `BasisFundingStrategy` (perp x future)
  - basis por mid-price (`basis_signal_mid`)
  - custo de execucao por impact price (`execution_cost_std_rev`)
  - limiar dinamico (`dynamic_z_threshold`)
  - filtros de liquidez/funding + maquina de estados (`flat`, `standard`, `reverse`)

## Adapter CryptoHFTData

Pacote: `src/btengine/data/cryptohftdata/`

Imports:

```python
from btengine.data.cryptohftdata import (
    CryptoHftLayout,
    CryptoHftDayConfig,
    S3Config,
    make_s3_filesystem,
    iter_trades,
    iter_trades_for_day,
    iter_mark_price,
    iter_mark_price_for_day,
    iter_depth_updates,
    iter_depth_updates_for_day,
    iter_ticker,
    iter_ticker_for_day,
    iter_open_interest,
    iter_open_interest_for_day,
    iter_liquidations,
    iter_liquidations_for_day,
    build_day_stream,
)
```

Config:

- `CryptoHftDayConfig`
  - `exchange`
  - `include_trades`, `include_orderbook`, `include_mark_price`
  - `include_ticker`, `include_open_interest`, `include_liquidations`
  - `open_interest_delay_ms`
  - `orderbook_hours`, `orderbook_skip_missing`
  - `skip_missing_daily_files`
  - `stream_start_ms`, `stream_end_ms`

## Utilitarios

Arquivo: `src/btengine/util/dotenv.py`

- `load_dotenv(path, override=False) -> DotenvResult`
