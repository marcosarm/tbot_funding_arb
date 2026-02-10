from btengine.engine import BacktestEngine, EngineConfig
from btengine.broker import SimBroker
from btengine.types import Liquidation, MarkPrice, OpenInterest, Ticker


class NoopStrategy:
    pass


def test_engine_applies_funding_once_per_timestamp():
    broker = SimBroker(maker_fee_frac=0.0, taker_fee_frac=0.0)

    # Open a short position (qty negative). Positive funding => shorts receive.
    broker.portfolio.apply_fill("BTCUSDT", "sell", qty=1.0, price=100.0, fee_usdt=0.0)

    engine = BacktestEngine(config=EngineConfig(tick_interval_ms=0), broker=broker)

    events = [
        MarkPrice(
            received_time_ns=0,
            event_time_ms=1_000,
            symbol="BTCUSDT",
            mark_price=100.0,
            index_price=100.0,
            funding_rate=0.01,
            next_funding_time_ms=1_000,
        ),
        # Same funding timestamp should not apply twice.
        MarkPrice(
            received_time_ns=0,
            event_time_ms=1_001,
            symbol="BTCUSDT",
            mark_price=101.0,
            index_price=101.0,
            funding_rate=0.02,
            next_funding_time_ms=1_000,
        ),
    ]

    res = engine.run(events, strategy=NoopStrategy())

    assert abs(res.ctx.broker.portfolio.realized_pnl_usdt - 1.0) < 1e-12


def test_engine_stores_latest_aux_events_in_context():
    engine = BacktestEngine(config=EngineConfig(tick_interval_ms=0), broker=SimBroker(maker_fee_frac=0.0, taker_fee_frac=0.0))

    events = [
        Ticker(
            received_time_ns=0,
            event_time_ms=1_000,
            symbol="BTCUSDT",
            price_change=1.0,
            price_change_percent=0.1,
            weighted_average_price=100.0,
            last_price=101.0,
            last_quantity=0.5,
            open_price=99.0,
            high_price=102.0,
            low_price=98.0,
            base_asset_volume=10.0,
            quote_asset_volume=1000.0,
            statistics_open_time_ms=0,
            statistics_close_time_ms=1_000,
            first_trade_id=1,
            last_trade_id=2,
            total_trades=10,
        ),
        OpenInterest(
            received_time_ns=0,
            event_time_ms=2_000,
            timestamp_ms=2_000,
            symbol="BTCUSDT",
            sum_open_interest=11.0,
            sum_open_interest_value=1100.0,
        ),
        Liquidation(
            received_time_ns=0,
            event_time_ms=3_000,
            symbol="BTCUSDT",
            side="BUY",
            order_type="LIMIT",
            time_in_force="IOC",
            quantity=0.1,
            price=100.0,
            average_price=100.0,
            order_status="FILLED",
            last_filled_quantity=0.1,
            filled_quantity=0.1,
            trade_time_ms=3_000,
        ),
    ]

    res = engine.run(events, strategy=NoopStrategy())
    ctx = res.ctx
    assert ctx.ticker["BTCUSDT"].event_time_ms == 1_000
    assert ctx.open_interest["BTCUSDT"].event_time_ms == 2_000
    assert ctx.liquidation["BTCUSDT"].event_time_ms == 3_000
