from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Side = Literal["buy", "sell"]


@dataclass(frozen=True, slots=True)
class DepthUpdate:
    """L2 depth update.

    Represents a single exchange depth message, aggregated from a flattened
    parquet representation.
    """

    received_time_ns: int
    event_time_ms: int
    transaction_time_ms: int
    symbol: str

    first_update_id: int
    final_update_id: int
    prev_final_update_id: int

    # Updates are (price, qty) where qty == 0 indicates deletion.
    bid_updates: list[tuple[float, float]]
    ask_updates: list[tuple[float, float]]


@dataclass(frozen=True, slots=True)
class Trade:
    received_time_ns: int
    event_time_ms: int
    trade_time_ms: int
    symbol: str

    trade_id: int
    price: float
    quantity: float

    # Binance semantics: True => buyer was maker (sell aggressor).
    is_buyer_maker: bool


@dataclass(frozen=True, slots=True)
class MarkPrice:
    received_time_ns: int
    event_time_ms: int
    symbol: str

    mark_price: float
    index_price: float
    funding_rate: float
    next_funding_time_ms: int


@dataclass(frozen=True, slots=True)
class Ticker:
    """Aggregated ticker (Binance-style 24h rolling window metrics)."""

    received_time_ns: int
    event_time_ms: int
    symbol: str

    price_change: float
    price_change_percent: float
    weighted_average_price: float
    last_price: float
    last_quantity: float
    open_price: float
    high_price: float
    low_price: float
    base_asset_volume: float
    quote_asset_volume: float

    statistics_open_time_ms: int
    statistics_close_time_ms: int
    first_trade_id: int
    last_trade_id: int
    total_trades: int


@dataclass(frozen=True, slots=True)
class OpenInterest:
    """Open interest snapshot event (typically low frequency, e.g. 5m)."""

    received_time_ns: int
    # Availability time in the simulation clock. This is when the strategy is
    # allowed to "see" the snapshot. By default it matches `timestamp_ms`.
    event_time_ms: int
    # Measurement time from dataset `timestamp` (epoch ms, UTC).
    timestamp_ms: int
    symbol: str

    sum_open_interest: float
    sum_open_interest_value: float


@dataclass(frozen=True, slots=True)
class Liquidation:
    """Liquidation event (Binance-style force order / liquidation stream)."""

    received_time_ns: int
    event_time_ms: int
    symbol: str

    side: str
    order_type: str
    time_in_force: str
    quantity: float
    price: float
    average_price: float
    order_status: str
    last_filled_quantity: float
    filled_quantity: float
    trade_time_ms: int
