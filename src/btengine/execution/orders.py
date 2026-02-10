from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..types import Side

OrderType = Literal["market", "limit"]
TimeInForce = Literal["GTC", "IOC"]


@dataclass(frozen=True, slots=True)
class Order:
    id: str
    symbol: str
    side: Side  # "buy" | "sell"
    order_type: OrderType
    quantity: float

    # For limit orders:
    price: float | None = None
    time_in_force: TimeInForce = "GTC"
    post_only: bool = False

    created_time_ms: int = 0

