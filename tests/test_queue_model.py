from btengine.execution.queue_model import MakerQueueOrder
from btengine.types import Trade


def test_maker_queue_fill_buy_on_sell_aggressor_trades():
    order = MakerQueueOrder(
        symbol="BTCUSDT",
        side="buy",
        price=100.0,
        quantity=1.0,
        queue_ahead_qty=0.5,
    )

    # Sell aggressor trade at our price: is_buyer_maker=True
    t = Trade(
        received_time_ns=0,
        event_time_ms=0,
        trade_time_ms=0,
        symbol="BTCUSDT",
        trade_id=1,
        price=100.0,
        quantity=0.4,
        is_buyer_maker=True,
    )
    assert order.on_trade(t) == 0.0
    assert abs(order.queue_ahead_qty - 0.1) < 1e-12

    # Now enough volume to reach us and partially fill.
    t2 = Trade(
        received_time_ns=0,
        event_time_ms=0,
        trade_time_ms=0,
        symbol="BTCUSDT",
        trade_id=2,
        price=100.0,
        quantity=0.5,
        is_buyer_maker=True,
    )
    filled = order.on_trade(t2)
    assert abs(filled - 0.4) < 1e-12
    assert abs(order.filled_qty - 0.4) < 1e-12


def test_maker_queue_updates_only_decrease_queue_ahead():
    order = MakerQueueOrder(
        symbol="BTCUSDT",
        side="sell",
        price=101.0,
        quantity=1.0,
        queue_ahead_qty=2.0,
    )

    order.on_book_qty_update(3.0)
    assert order.queue_ahead_qty == 2.0  # increase ignored

    order.on_book_qty_update(1.5)
    assert order.queue_ahead_qty == 1.5

