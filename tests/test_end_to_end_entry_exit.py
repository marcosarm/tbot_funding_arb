from __future__ import annotations

from dataclasses import dataclass

from btengine.analytics import round_trips_from_fills
from btengine.broker import SimBroker
from btengine.engine import BacktestEngine, EngineConfig, EngineContext
from btengine.execution.orders import Order
from btengine.types import DepthUpdate


@dataclass
class _EntryExitStrategy:
    symbol: str = "BTCUSDT"
    entered: bool = False
    exited: bool = False

    def on_event(self, event: object, ctx: EngineContext) -> None:
        if not isinstance(event, DepthUpdate):
            return
        if event.symbol != self.symbol:
            return

        book = ctx.book(self.symbol)

        if not self.entered and ctx.now_ms == 0:
            ctx.broker.submit(
                Order(id="entry", symbol=self.symbol, side="buy", order_type="market", quantity=1.0),
                book,
                now_ms=ctx.now_ms,
            )
            self.entered = True
            return

        if self.entered and not self.exited and ctx.now_ms == 1_000:
            pos = ctx.broker.portfolio.positions.get(self.symbol)
            q = abs(pos.qty) if pos is not None else 0.0
            ctx.broker.submit(
                Order(id="exit", symbol=self.symbol, side="sell", order_type="market", quantity=q),
                book,
                now_ms=ctx.now_ms,
            )
            self.exited = True


def test_end_to_end_entry_exit_generates_pnl_and_round_trip():
    broker = SimBroker(maker_fee_frac=0.0, taker_fee_frac=0.0)
    engine = BacktestEngine(config=EngineConfig(tick_interval_ms=0), broker=broker)

    events = [
        DepthUpdate(
            received_time_ns=0,
            event_time_ms=0,
            transaction_time_ms=0,
            symbol="BTCUSDT",
            first_update_id=1,
            final_update_id=1,
            prev_final_update_id=0,
            bid_updates=[(99.0, 10.0)],
            ask_updates=[(100.0, 10.0)],
        ),
        DepthUpdate(
            received_time_ns=0,
            event_time_ms=1_000,
            transaction_time_ms=1_000,
            symbol="BTCUSDT",
            first_update_id=2,
            final_update_id=2,
            prev_final_update_id=1,
            bid_updates=[(109.0, 10.0)],
            ask_updates=[(110.0, 10.0)],
        ),
    ]

    res = engine.run(events, strategy=_EntryExitStrategy())

    assert len(res.ctx.broker.fills) == 2
    assert abs(res.ctx.broker.portfolio.realized_pnl_usdt - 9.0) < 1e-12
    pos = res.ctx.broker.portfolio.positions["BTCUSDT"]
    assert abs(pos.qty - 0.0) < 1e-12
    assert abs(pos.avg_price - 0.0) < 1e-12

    trades = round_trips_from_fills(res.ctx.broker.fills)
    assert len(trades) == 1
    assert abs(trades[0].net_pnl_usdt - 9.0) < 1e-12
