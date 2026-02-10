from __future__ import annotations

import heapq
import math

from ..marketdata.orderbook import L2Book
from ..types import Side


def simulate_taker_fill(
    book: L2Book,
    side: Side,
    quantity: float,
    *,
    limit_price: float | None = None,
) -> tuple[float, float]:
    """Simulate a taker fill using L2 book depth.

    Returns (avg_price, filled_qty).
    - For `side="buy"` consumes asks from low to high.
    - For `side="sell"` consumes bids from high to low.
    - If `limit_price` is provided, the fill will not cross it (IOC-like).
    """

    if quantity <= 0:
        raise ValueError("quantity must be > 0")

    if side == "buy":
        levels = sorted(book.asks.items(), key=lambda x: x[0])
        def crosses(p: float) -> bool:
            return limit_price is not None and p > limit_price
    elif side == "sell":
        levels = sorted(book.bids.items(), key=lambda x: x[0], reverse=True)
        def crosses(p: float) -> bool:
            return limit_price is not None and p < limit_price
    else:
        raise ValueError(f"invalid side: {side!r}")

    remaining = float(quantity)
    filled = 0.0
    cost = 0.0

    for price, lvl_qty in levels:
        if remaining <= 0:
            break
        if lvl_qty <= 0:
            continue
        if crosses(price):
            break

        take = lvl_qty if lvl_qty <= remaining else remaining
        filled += take
        cost += take * price
        remaining -= take

    if filled <= 0:
        return math.nan, 0.0

    return cost / filled, filled


def consume_taker_fill(
    book: L2Book,
    side: Side,
    quantity: float,
    *,
    limit_price: float | None = None,
    eps_qty: float = 1e-12,
) -> tuple[float, float]:
    """Simulate a taker fill and apply self-impact to the in-memory `book`.

    This is identical to `simulate_taker_fill(...)` but mutates `book` by
    decrementing quantities on the opposite side for the consumed levels.
    """

    if quantity <= 0:
        raise ValueError("quantity must be > 0")

    remaining = float(quantity)
    filled = 0.0
    cost = 0.0

    if side == "buy":
        # Consume asks (low -> high)
        levels = sorted(book.asks.items(), key=lambda x: x[0])

        def crosses(p: float) -> bool:
            return limit_price is not None and p > limit_price

        side_map = book.asks
        heap = book._ask_heap

        def heap_price(p: float) -> float:
            return p

    elif side == "sell":
        # Consume bids (high -> low)
        levels = sorted(book.bids.items(), key=lambda x: x[0], reverse=True)

        def crosses(p: float) -> bool:
            return limit_price is not None and p < limit_price

        side_map = book.bids
        heap = book._bid_heap

        def heap_price(p: float) -> float:
            return -p

    else:
        raise ValueError(f"invalid side: {side!r}")

    for price, lvl_qty in levels:
        if remaining <= 0:
            break
        if lvl_qty <= 0:
            continue
        if crosses(price):
            break

        take = lvl_qty if lvl_qty <= remaining else remaining
        filled += take
        cost += take * price
        remaining -= take

        new_qty = float(lvl_qty) - float(take)
        if new_qty <= eps_qty:
            side_map.pop(float(price), None)
        else:
            side_map[float(price)] = new_qty
            heapq.heappush(heap, heap_price(float(price)))

    if filled <= 0:
        return math.nan, 0.0

    return cost / filled, filled
