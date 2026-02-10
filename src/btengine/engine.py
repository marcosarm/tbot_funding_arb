from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Protocol

from .broker import SimBroker
from .marketdata.orderbook import L2Book
from .types import DepthUpdate, Liquidation, MarkPrice, OpenInterest, Ticker, Trade

Event = DepthUpdate | Trade | MarkPrice | Ticker | OpenInterest | Liquidation


class Strategy(Protocol):
    """Strategy callback interface for the backtest engine.

    Strategies should be pure decision logic; market state lives in `EngineContext`.
    """

    def on_start(self, ctx: "EngineContext") -> None: ...

    def on_tick(self, now_ms: int, ctx: "EngineContext") -> None: ...

    def on_event(self, event: Event, ctx: "EngineContext") -> None: ...

    def on_end(self, ctx: "EngineContext") -> None: ...


@dataclass(slots=True)
class EngineConfig:
    tick_interval_ms: int = 1_000
    trading_start_ms: int | None = None
    trading_end_ms: int | None = None


@dataclass(slots=True)
class EngineContext:
    config: EngineConfig
    broker: SimBroker
    books: dict[str, L2Book] = field(default_factory=dict)

    now_ms: int = 0

    # Latest MarkPrice per symbol.
    mark: dict[str, MarkPrice] = field(default_factory=dict)
    ticker: dict[str, Ticker] = field(default_factory=dict)
    open_interest: dict[str, OpenInterest] = field(default_factory=dict)
    liquidation: dict[str, Liquidation] = field(default_factory=dict)

    # Funding bookkeeping.
    _last_funding_applied_ms: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def is_trading_time(self) -> bool:
        if self.config.trading_start_ms is not None and self.now_ms < self.config.trading_start_ms:
            return False
        if self.config.trading_end_ms is not None and self.now_ms > self.config.trading_end_ms:
            return False
        return True

    def book(self, symbol: str) -> L2Book:
        b = self.books.get(symbol)
        if b is None:
            b = L2Book()
            self.books[symbol] = b
        return b

    def apply_funding_if_due(self, mp: MarkPrice) -> float:
        """Apply funding at funding timestamps when due.

        Returns funding pnl applied (USDT), or 0.0 if nothing happened.
        """

        if mp.next_funding_time_ms <= 0:
            return 0.0

        # Apply at the first mark-price event at/after the funding time. This
        # matches the dataset shape where the funding timestamp appears in
        # `next_funding_time_ms`.
        if mp.event_time_ms < mp.next_funding_time_ms:
            return 0.0

        last_applied = self._last_funding_applied_ms.get(mp.symbol, -1)
        if mp.next_funding_time_ms <= last_applied:
            return 0.0

        self._last_funding_applied_ms[mp.symbol] = mp.next_funding_time_ms
        return self.broker.portfolio.apply_funding(mp.symbol, mp.mark_price, mp.funding_rate)


@dataclass(slots=True)
class BacktestResult:
    ctx: EngineContext


class BacktestEngine:
    def __init__(self, *, config: EngineConfig, broker: SimBroker | None = None) -> None:
        self.config = config
        self.broker = broker or SimBroker()

    def run(self, events: Iterable[Event], *, strategy: Strategy) -> BacktestResult:
        ctx = EngineContext(config=self.config, broker=self.broker)

        # Optional methods: let a strategy implement only the hooks it needs.
        on_start = getattr(strategy, "on_start", None)
        on_tick = getattr(strategy, "on_tick", None)
        on_event = getattr(strategy, "on_event", None)
        on_end = getattr(strategy, "on_end", None)

        if callable(on_start):
            on_start(ctx)

        next_tick_ms: int | None = None
        tick_interval = int(self.config.tick_interval_ms or 0)

        for ev in events:
            now = int(ev.event_time_ms)

            # Drive ticks up to current event time.
            if tick_interval > 0 and callable(on_tick):
                if next_tick_ms is None:
                    # Anchor ticks to the first observed timestamp.
                    next_tick_ms = (now // tick_interval) * tick_interval

                while next_tick_ms <= now:
                    ctx.now_ms = next_tick_ms
                    ctx.broker.on_time(next_tick_ms)
                    on_tick(next_tick_ms, ctx)
                    next_tick_ms += tick_interval

            ctx.now_ms = now
            ctx.broker.on_time(now)

            if isinstance(ev, DepthUpdate):
                book = ctx.book(ev.symbol)
                ctx.broker.on_depth_update(ev, book)
            elif isinstance(ev, Trade):
                ctx.broker.on_trade(ev, now_ms=now)
            elif isinstance(ev, MarkPrice):
                ctx.mark[ev.symbol] = ev
                ctx.apply_funding_if_due(ev)
            elif isinstance(ev, Ticker):
                ctx.ticker[ev.symbol] = ev
            elif isinstance(ev, OpenInterest):
                ctx.open_interest[ev.symbol] = ev
            elif isinstance(ev, Liquidation):
                ctx.liquidation[ev.symbol] = ev
            else:
                raise TypeError(f"unsupported event type: {type(ev)}")

            if callable(on_event):
                on_event(ev, ctx)

        # One last tick at the end so strategies can cleanup on grid boundaries.
        if next_tick_ms is not None and callable(on_tick):
            ctx.now_ms = next_tick_ms
            ctx.broker.on_time(next_tick_ms)
            on_tick(next_tick_ms, ctx)

        if callable(on_end):
            on_end(ctx)

        return BacktestResult(ctx=ctx)
