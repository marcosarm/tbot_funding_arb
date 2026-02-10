from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Iterable, Literal

BookSide = Literal["bid", "ask"]


@dataclass(slots=True)
class L2Book:
    """In-memory L2 book keyed by price, suitable for backtesting.

    Notes:
    - Uses heaps to provide O(log n) best bid/ask retrieval without requiring
      an always-sorted structure.
    - Intended for correctness and simplicity first; optimize later if needed.
    """

    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)

    _bid_heap: list[float] = field(default_factory=list, init=False, repr=False)  # store -price
    _ask_heap: list[float] = field(default_factory=list, init=False, repr=False)  # store +price

    def _maybe_rebuild_heaps(self) -> None:
        # Heaps accumulate stale entries. Rebuild opportunistically to cap memory/latency.
        if len(self._bid_heap) > (len(self.bids) * 4 + 1000):
            self._bid_heap = [-p for p in self.bids.keys()]
            heapq.heapify(self._bid_heap)
        if len(self._ask_heap) > (len(self.asks) * 4 + 1000):
            self._ask_heap = [p for p in self.asks.keys()]
            heapq.heapify(self._ask_heap)

    def apply_level(self, side: BookSide, price: float, quantity: float) -> None:
        """Apply a single level update."""

        if side == "bid":
            book = self.bids
            heap = self._bid_heap
            heap_price = -price
        elif side == "ask":
            book = self.asks
            heap = self._ask_heap
            heap_price = price
        else:
            raise ValueError(f"invalid side: {side!r}")

        if quantity <= 0.0:
            book.pop(price, None)
            return

        book[price] = quantity
        heapq.heappush(heap, heap_price)

    def apply_depth_update(
        self,
        bid_updates: Iterable[tuple[float, float]],
        ask_updates: Iterable[tuple[float, float]],
    ) -> None:
        """Apply a depth message update atomically."""

        for price, qty in bid_updates:
            self.apply_level("bid", float(price), float(qty))
        for price, qty in ask_updates:
            self.apply_level("ask", float(price), float(qty))

    def best_bid(self) -> float | None:
        self._maybe_rebuild_heaps()
        while self._bid_heap:
            price = -self._bid_heap[0]
            qty = self.bids.get(price)
            if qty is None or qty <= 0.0:
                heapq.heappop(self._bid_heap)
                continue
            return price
        return None

    def best_ask(self) -> float | None:
        self._maybe_rebuild_heaps()
        while self._ask_heap:
            price = self._ask_heap[0]
            qty = self.asks.get(price)
            if qty is None or qty <= 0.0:
                heapq.heappop(self._ask_heap)
                continue
            return price
        return None

    def mid_price(self) -> float | None:
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    def impact_vwap(
        self,
        side: Literal["buy", "sell"],
        target_notional: float,
        *,
        max_levels: int = 200,
        eps_notional: float = 1e-6,
    ) -> float:
        """VWAP for consuming liquidity until `target_notional` is reached.

        Returns NaN when there is insufficient depth available.
        """

        if target_notional <= 0:
            raise ValueError("target_notional must be > 0")

        if side == "buy":
            items = self.asks.items()
            # (price, qty) sorted asc by price
            levels = heapq.nsmallest(max_levels, items) if max_levels else sorted(items)
        elif side == "sell":
            items = self.bids.items()
            # (price, qty) sorted desc by price
            levels = heapq.nlargest(max_levels, items) if max_levels else sorted(items, reverse=True)
        else:
            raise ValueError(f"invalid side: {side!r}")

        remaining = float(target_notional)
        total_qty = 0.0
        total_cost = 0.0

        for price, qty in levels:
            if remaining <= eps_notional:
                break
            if qty <= 0.0:
                continue
            level_notional = price * qty
            if level_notional <= 0.0:
                continue

            take_notional = level_notional if level_notional <= remaining else remaining
            take_qty = take_notional / price

            total_cost += take_qty * price
            total_qty += take_qty
            remaining -= take_notional

        if remaining > eps_notional or total_qty <= 0.0:
            # If we limited levels, retry once with full depth to avoid false NaN.
            if max_levels:
                if side == "buy" and len(self.asks) > max_levels:
                    return self.impact_vwap(side, target_notional, max_levels=0, eps_notional=eps_notional)
                if side == "sell" and len(self.bids) > max_levels:
                    return self.impact_vwap(side, target_notional, max_levels=0, eps_notional=eps_notional)

            return math.nan

        return total_cost / total_qty
