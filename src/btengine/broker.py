from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from .execution.orders import Order
from .execution.queue_model import MakerQueueOrder
from .execution.taker import consume_taker_fill
from .marketdata.orderbook import L2Book
from .portfolio import Portfolio
from .types import DepthUpdate, Trade


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: str
    symbol: str
    side: str  # "buy" | "sell"
    quantity: float
    price: float
    fee_usdt: float
    event_time_ms: int
    liquidity: str  # "maker" | "taker"


@dataclass(slots=True)
class SimBroker:
    """Minimal broker simulator with:
    - taker fills from book depth
    - maker queue model fills from trade tape
    """

    maker_fee_frac: float = 0.0004
    taker_fee_frac: float = 0.0005

    # Realism knobs.
    submit_latency_ms: int = 0
    cancel_latency_ms: int = 0

    # Conservative maker queue modeling.
    maker_queue_ahead_factor: float = 1.0
    maker_queue_ahead_extra_qty: float = 0.0
    maker_trade_participation: float = 1.0

    portfolio: Portfolio = field(default_factory=Portfolio)
    fills: list[Fill] = field(default_factory=list)

    _maker_orders: dict[str, MakerQueueOrder] = field(default_factory=dict, init=False, repr=False)
    _pending_submits: list[tuple[int, int, Order, L2Book]] = field(default_factory=list, init=False, repr=False)
    _pending_cancels: list[tuple[int, int, str]] = field(default_factory=list, init=False, repr=False)
    _seq: int = field(default=0, init=False, repr=False)
    _canceled: set[str] = field(default_factory=set, init=False, repr=False)

    def on_time(self, now_ms: int) -> None:
        """Advance broker time and process any pending submissions/cancels."""

        now = int(now_ms)

        # Cancels first: if a cancel and submit become due at the same time,
        # treat it as cancel arriving first (conservative).
        while self._pending_cancels and self._pending_cancels[0][0] <= now:
            _, _, order_id = heapq.heappop(self._pending_cancels)
            self._cancel_now(order_id)

        while self._pending_submits and self._pending_submits[0][0] <= now:
            _, _, order, book = heapq.heappop(self._pending_submits)
            if order.id in self._canceled:
                self._canceled.discard(order.id)
                continue
            self._submit_now(order, book, now)

    def submit(self, order: Order, book: L2Book, now_ms: int) -> None:
        """Submit an order into the simulator.

        When `submit_latency_ms > 0`, orders are queued and activated later via `on_time()`.
        """

        if self.submit_latency_ms and self.submit_latency_ms > 0:
            self._seq += 1
            due = int(now_ms) + int(self.submit_latency_ms)
            heapq.heappush(self._pending_submits, (due, self._seq, order, book))
            return

        self._submit_now(order, book, int(now_ms))

    def _submit_now(self, order: Order, book: L2Book, now_ms: int) -> None:
        if order.order_type == "market":
            self._fill_taker(order, book, now_ms, limit_price=None)
            return

        if order.order_type != "limit":
            raise ValueError(f"unsupported order_type: {order.order_type!r}")
        if order.price is None:
            raise ValueError("limit order requires price")

        limit_px = float(order.price)
        best_bid = book.best_bid()
        best_ask = book.best_ask()

        def crosses() -> bool:
            # Buy crosses if it reaches the ask; sell crosses if it reaches the bid.
            if order.side == "buy":
                return best_ask is not None and limit_px >= float(best_ask)
            return best_bid is not None and limit_px <= float(best_bid)

        if order.post_only:
            # Post-only orders that would execute immediately should be rejected.
            if crosses():
                return
            self._open_maker(order, book)
            return

        # Non-post-only limit: IOC acts as taker up to the limit.
        if order.time_in_force == "IOC":
            self._fill_taker(order, book, now_ms, limit_price=limit_px)
            return

        # GTC limit without post-only:
        # - if it crosses the spread, it should execute immediately (taker)
        # - otherwise, it rests (maker)
        if crosses():
            _, filled_qty = self._fill_taker(order, book, now_ms, limit_price=limit_px)
            remaining = float(order.quantity) - float(filled_qty)
            if remaining > 0.0:
                # In real exchanges, the unfilled portion of a limit order remains on the book.
                self._open_maker(
                    Order(
                        id=order.id,
                        symbol=order.symbol,
                        side=order.side,
                        order_type="limit",
                        quantity=remaining,
                        price=limit_px,
                        time_in_force="GTC",
                        post_only=False,
                        created_time_ms=int(order.created_time_ms),
                    ),
                    book,
                )
            return

        self._open_maker(order, book)

    def _open_maker(self, order: Order, book: L2Book) -> None:
        # Visible qty at the level on our side.
        if order.side == "buy":
            q_ahead = float(book.bids.get(float(order.price), 0.0))
        else:
            q_ahead = float(book.asks.get(float(order.price), 0.0))

        if self.maker_queue_ahead_factor < 0.0:
            raise ValueError("maker_queue_ahead_factor must be >= 0")
        if self.maker_queue_ahead_extra_qty < 0.0:
            raise ValueError("maker_queue_ahead_extra_qty must be >= 0")

        q_ahead = q_ahead * float(self.maker_queue_ahead_factor) + float(self.maker_queue_ahead_extra_qty)

        self._maker_orders[order.id] = MakerQueueOrder(
            symbol=order.symbol,
            side=order.side,
            price=float(order.price),
            quantity=float(order.quantity),
            queue_ahead_qty=q_ahead,
            trade_participation=float(self.maker_trade_participation),
        )

    def _fill_taker(self, order: Order, book: L2Book, now_ms: int, *, limit_price: float | None) -> tuple[float, float]:
        avg_px, filled_qty = consume_taker_fill(
            book,
            side=order.side,
            quantity=float(order.quantity),
            limit_price=limit_price,
        )
        if filled_qty <= 0.0 or math.isnan(avg_px):
            return avg_px, 0.0

        fee = filled_qty * avg_px * self.taker_fee_frac
        self.portfolio.apply_fill(order.symbol, order.side, filled_qty, avg_px, fee_usdt=fee)
        self.fills.append(
            Fill(
                order_id=order.id,
                symbol=order.symbol,
                side=order.side,
                quantity=filled_qty,
                price=avg_px,
                fee_usdt=fee,
                event_time_ms=now_ms,
                liquidity="taker",
            )
        )
        return avg_px, filled_qty

    def on_depth_update(self, update: DepthUpdate, book: L2Book) -> None:
        # Update book first.
        book.apply_depth_update(update.bid_updates, update.ask_updates)

        # Progress maker queues for touched levels.
        for order_id, mo in list(self._maker_orders.items()):
            if mo.symbol != update.symbol:
                continue
            # Only updates on our side at our price affect queue_ahead.
            touched = update.bid_updates if mo.side == "buy" else update.ask_updates
            for p, q in touched:
                if math.isclose(p, mo.price, rel_tol=0.0, abs_tol=1e-9):
                    mo.on_book_qty_update(float(q))
                    break

            if mo.is_filled():
                # Filled via previous trades; finalize and remove.
                self._maker_orders.pop(order_id, None)

    def on_trade(self, trade: Trade, now_ms: int) -> None:
        for order_id, mo in list(self._maker_orders.items()):
            fill_qty = mo.on_trade(trade)
            if fill_qty <= 0.0:
                continue

            fee = fill_qty * trade.price * self.maker_fee_frac
            self.portfolio.apply_fill(mo.symbol, mo.side, fill_qty, trade.price, fee_usdt=fee)
            self.fills.append(
                Fill(
                    order_id=order_id,
                    symbol=mo.symbol,
                    side=mo.side,
                    quantity=fill_qty,
                    price=trade.price,
                    fee_usdt=fee,
                    event_time_ms=now_ms,
                    liquidity="maker",
                )
            )

            if mo.is_filled():
                self._maker_orders.pop(order_id, None)

    def cancel(self, order_id: str, *, now_ms: int | None = None) -> None:
        """Cancel an open maker order.

        If `cancel_latency_ms > 0` and `now_ms` is provided, cancellation will be delayed
        and applied via `on_time()`.
        """

        if self.cancel_latency_ms and self.cancel_latency_ms > 0 and now_ms is not None:
            self._seq += 1
            due = int(now_ms) + int(self.cancel_latency_ms)
            heapq.heappush(self._pending_cancels, (due, self._seq, order_id))
            return

        self._cancel_now(order_id)

    def _cancel_now(self, order_id: str) -> None:
        self._maker_orders.pop(order_id, None)
        # Also cancel an order that has been submitted but not yet activated.
        self._canceled.add(order_id)

    def has_open_orders(self) -> bool:
        return bool(self._maker_orders)
