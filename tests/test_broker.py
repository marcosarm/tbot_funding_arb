import math

from btengine.broker import SimBroker
from btengine.execution.orders import Order
from btengine.marketdata import L2Book
from btengine.types import Trade, DepthUpdate


def test_broker_taker_market_fill_updates_portfolio():
    book = L2Book()
    book.apply_depth_update(bid_updates=[(99.0, 1.0)], ask_updates=[(100.0, 2.0)])

    broker = SimBroker(maker_fee_frac=0.0, taker_fee_frac=0.0)
    broker.submit(
        Order(id="o1", symbol="BTCUSDT", side="buy", order_type="market", quantity=1.5),
        book,
        now_ms=0,
    )

    pos = broker.portfolio.positions["BTCUSDT"]
    assert abs(pos.qty - 1.5) < 1e-12
    assert abs(pos.avg_price - 100.0) < 1e-12
    # Self-impact: taker fill consumes from the in-memory book.
    assert abs(book.asks[100.0] - 0.5) < 1e-12


def test_broker_maker_order_fills_on_trade():
    book = L2Book()
    book.apply_depth_update(bid_updates=[(100.0, 1.0)], ask_updates=[(101.0, 1.0)])

    broker = SimBroker(maker_fee_frac=0.0, taker_fee_frac=0.0)
    broker.submit(
        Order(
            id="m1",
            symbol="BTCUSDT",
            side="buy",
            order_type="limit",
            quantity=1.0,
            price=100.0,
            post_only=True,
        ),
        book,
        now_ms=0,
    )
    assert broker.has_open_orders()

    # Reduce visible qty at our price -> queue ahead decreases
    broker.on_depth_update(
        DepthUpdate(
            received_time_ns=0,
            event_time_ms=0,
            transaction_time_ms=0,
            symbol="BTCUSDT",
            first_update_id=1,
            final_update_id=1,
            prev_final_update_id=0,
            bid_updates=[(100.0, 0.2)],
            ask_updates=[],
        ),
        book,
    )

    # Sell aggressor trade at our price (buyer maker=True) should fill some.
    broker.on_trade(
        Trade(
            received_time_ns=0,
            event_time_ms=0,
            trade_time_ms=0,
            symbol="BTCUSDT",
            trade_id=1,
            price=100.0,
            quantity=1.0,
            is_buyer_maker=True,
        ),
        now_ms=0,
    )

    assert broker.has_open_orders()

    # Another trade finishes the remaining qty.
    broker.on_trade(
        Trade(
            received_time_ns=0,
            event_time_ms=0,
            trade_time_ms=0,
            symbol="BTCUSDT",
            trade_id=2,
            price=100.0,
            quantity=1.0,
            is_buyer_maker=True,
        ),
        now_ms=0,
    )

    assert not broker.has_open_orders()
    pos = broker.portfolio.positions["BTCUSDT"]
    assert abs(pos.qty - 1.0) < 1e-12
    assert abs(pos.avg_price - 100.0) < 1e-12


def test_broker_taker_ioc_respects_limit_price():
    book = L2Book()
    book.apply_depth_update(bid_updates=[(99.0, 1.0)], ask_updates=[(100.0, 1.0), (101.0, 10.0)])

    broker = SimBroker(maker_fee_frac=0.0, taker_fee_frac=0.0)
    broker.submit(
        Order(
            id="ioc1",
            symbol="BTCUSDT",
            side="buy",
            order_type="limit",
            quantity=5.0,
            price=100.0,
            time_in_force="IOC",
        ),
        book,
        now_ms=0,
    )
    pos = broker.portfolio.positions["BTCUSDT"]
    # Only 1.0 available at 100.0 within limit.
    assert abs(pos.qty - 1.0) < 1e-12
    assert math.isclose(pos.avg_price, 100.0)
    # Self-impact: the 100.0 ask level was fully consumed.
    assert 100.0 not in book.asks
    assert abs(book.asks[101.0] - 10.0) < 1e-12


def test_broker_submit_latency_defers_market_fill():
    book = L2Book()
    book.apply_depth_update(bid_updates=[(99.0, 1.0)], ask_updates=[(100.0, 2.0)])

    broker = SimBroker(maker_fee_frac=0.0, taker_fee_frac=0.0, submit_latency_ms=100)
    broker.submit(Order(id="o1", symbol="BTCUSDT", side="buy", order_type="market", quantity=1.0), book, now_ms=0)

    assert broker.portfolio.positions.get("BTCUSDT") is None

    broker.on_time(99)
    assert broker.portfolio.positions.get("BTCUSDT") is None

    broker.on_time(100)
    pos = broker.portfolio.positions["BTCUSDT"]
    assert abs(pos.qty - 1.0) < 1e-12
    assert abs(pos.avg_price - 100.0) < 1e-12


def test_broker_post_only_crossing_is_rejected():
    book = L2Book()
    book.apply_depth_update(bid_updates=[(99.0, 1.0)], ask_updates=[(100.0, 2.0)])

    broker = SimBroker(maker_fee_frac=0.0, taker_fee_frac=0.0)
    broker.submit(
        Order(
            id="po1",
            symbol="BTCUSDT",
            side="buy",
            order_type="limit",
            quantity=1.0,
            price=100.0,  # touches ask => would execute
            post_only=True,
        ),
        book,
        now_ms=0,
    )
    assert not broker.has_open_orders()
    assert broker.portfolio.positions.get("BTCUSDT") is None


def test_broker_maker_trade_participation_is_conservative():
    book = L2Book()
    book.apply_depth_update(bid_updates=[(100.0, 1.0)], ask_updates=[(102.0, 1.0)])

    broker = SimBroker(maker_fee_frac=0.0, taker_fee_frac=0.0, maker_trade_participation=0.5)
    broker.submit(
        Order(
            id="m1",
            symbol="BTCUSDT",
            side="sell",
            order_type="limit",
            quantity=1.0,
            price=101.0,
            post_only=True,
        ),
        book,
        now_ms=0,
    )
    assert broker.has_open_orders()

    # Buy aggressor at 101.0 (is_buyer_maker=False) would hit asks, filling our sell order.
    broker.on_trade(
        Trade(
            received_time_ns=0,
            event_time_ms=0,
            trade_time_ms=0,
            symbol="BTCUSDT",
            trade_id=1,
            price=101.0,
            quantity=1.0,
            is_buyer_maker=False,
        ),
        now_ms=0,
    )

    # With trade_participation=0.5, only half the trade volume is credited to us.
    assert broker.has_open_orders()
    pos = broker.portfolio.positions["BTCUSDT"]
    assert abs(pos.qty + 0.5) < 1e-12
    assert abs(pos.avg_price - 101.0) < 1e-12


def test_broker_gtc_crossing_limit_partial_fill_leaves_remainder_resting():
    book = L2Book()
    # Best ask is 100.0. A buy limit at 100.5 crosses, but cannot consume 101.0 (beyond limit),
    # so it should fill 1.0 @ 100.0 and leave the remainder resting at 100.5.
    book.apply_depth_update(bid_updates=[(99.0, 1.0)], ask_updates=[(100.0, 1.0), (101.0, 10.0)])

    broker = SimBroker(maker_fee_frac=0.0, taker_fee_frac=0.0)
    broker.submit(
        Order(
            id="gtc1",
            symbol="BTCUSDT",
            side="buy",
            order_type="limit",
            quantity=5.0,
            price=100.5,
            time_in_force="GTC",
            post_only=False,
        ),
        book,
        now_ms=0,
    )

    # Immediate taker fill for the crossed portion.
    pos = broker.portfolio.positions["BTCUSDT"]
    assert abs(pos.qty - 1.0) < 1e-12
    assert abs(pos.avg_price - 100.0) < 1e-12

    # Remainder should be resting as a maker order.
    assert broker.has_open_orders()

    # Self-impact: the 100.0 ask level was fully consumed.
    assert 100.0 not in book.asks

    # Trade at our resting bid price with sell aggressor (buyer maker=True) fills the remainder.
    broker.on_trade(
        Trade(
            received_time_ns=0,
            event_time_ms=0,
            trade_time_ms=0,
            symbol="BTCUSDT",
            trade_id=1,
            price=100.5,
            quantity=10.0,
            is_buyer_maker=True,
        ),
        now_ms=0,
    )

    assert not broker.has_open_orders()
    pos = broker.portfolio.positions["BTCUSDT"]
    assert abs(pos.qty - 5.0) < 1e-12
    assert abs(pos.avg_price - 100.4) < 1e-12
