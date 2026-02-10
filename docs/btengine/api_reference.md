# API Reference (imports principais)

Este arquivo lista os objetos mais importantes e onde importa-los. O codigo tem type hints e docstrings; este e um mapa rapido.

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
  - `simulate_taker_fill(book, side, quantity, limit_price=None) -> (avg_price, filled_qty)`
- `src/btengine/execution/queue_model.py`
  - `MakerQueueOrder` (modelo aproximado de maker fills)
- `src/btengine/broker.py`
  - `SimBroker` (fees + latencia + modelo maker conservador; ver `on_time()` + `submit()`/`cancel()`)
  - `Fill`

## Portfolio

Arquivo: `src/btengine/portfolio.py`

- `Portfolio`
  - `positions: dict[str, Position]`
  - `realized_pnl_usdt`, `fees_paid_usdt`
  - `apply_fill(...)`
  - `apply_funding(symbol, mark_price, funding_rate) -> funding_pnl_usdt`

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
