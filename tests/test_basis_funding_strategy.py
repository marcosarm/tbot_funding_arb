from __future__ import annotations

import math

from btengine.broker import SimBroker
from btengine.engine import BacktestEngine, EngineConfig
from btengine.marketdata import L2Book
from funding import (
    BasisFundingStrategy,
    basis_signal_mid,
    dynamic_z_threshold,
    execution_cost_std_rev,
    has_min_liquidity,
    should_exit_hard_stop,
    should_exit_mean_reversion,
)
from btengine.types import DepthUpdate, MarkPrice


def _depth(symbol: str, t_ms: int, update_id: int, bid: float, ask: float, qty: float = 100.0) -> DepthUpdate:
    return DepthUpdate(
        received_time_ns=0,
        event_time_ms=int(t_ms),
        transaction_time_ms=int(t_ms),
        symbol=symbol,
        first_update_id=int(update_id),
        final_update_id=int(update_id),
        prev_final_update_id=int(update_id) - 1,
        bid_updates=[(float(bid), float(qty))],
        ask_updates=[(float(ask), float(qty))],
    )


def _mark(symbol: str, t_ms: int, mark: float, funding_rate: float) -> MarkPrice:
    return MarkPrice(
        received_time_ns=0,
        event_time_ms=int(t_ms),
        symbol=symbol,
        mark_price=float(mark),
        index_price=float(mark),
        funding_rate=float(funding_rate),
        next_funding_time_ms=9_999_999_999,
    )


def test_dynamic_z_threshold_regimes():
    assert dynamic_z_threshold(0.7) == 1.5
    assert dynamic_z_threshold(1.0) == 2.0
    assert dynamic_z_threshold(1.6) == 3.0


def test_exit_threshold_rules():
    assert should_exit_mean_reversion(0.19, 0.2)
    assert not should_exit_mean_reversion(0.21, 0.2)

    assert should_exit_hard_stop(4.1, 4.0)
    assert not should_exit_hard_stop(3.9, 4.0)


def test_basis_and_execution_cost_math():
    perp = L2Book()
    fut = L2Book()

    perp.apply_depth_update(bid_updates=[(100.0, 10.0)], ask_updates=[(100.2, 10.0)])
    fut.apply_depth_update(bid_updates=[(101.0, 10.0)], ask_updates=[(101.2, 10.0)])

    b = basis_signal_mid(100.1, 101.1)
    assert abs(b - (1.0 / 100.1)) < 1e-12

    c_std, c_rev = execution_cost_std_rev(perp, fut, impact_notional_usdt=100.0)
    # Standard: short perp@bid 100, long future@ask 101.2
    assert abs(c_std - ((101.2 - 100.0) / 100.0)) < 1e-12
    # Reverse: long perp@ask 100.2, short future@bid 101.0
    assert abs(c_rev - ((101.0 - 100.2) / 100.2)) < 1e-12


def test_liquidity_reject():
    book = L2Book()
    # Ask notional near mid is only 5 * 0.1 = 0.5
    book.apply_depth_update(bid_updates=[(99.9, 0.1)], ask_updates=[(100.0, 0.005)])

    ok = has_min_liquidity(
        book,
        "ask",
        mid_price=100.0,
        depth_pct=0.01,
        order_notional=100.0,
        min_ratio=5.0,
    )
    assert not ok


def test_basis_funding_standard_entry_and_mean_reversion_exit():
    broker = SimBroker(maker_fee_frac=0.0, taker_fee_frac=0.0)
    engine = BacktestEngine(config=EngineConfig(tick_interval_ms=0), broker=broker)

    strat = BasisFundingStrategy(
        perp_symbol="BTCUSDT",
        future_symbol="BTCUSDT_260626",
        impact_notional_usdt=100.0,
        funding_threshold=0.0001,
        max_slippage=0.0,
        entry_safety_margin=0.0,
        taker_fee_frac=0.0,
        liquidity_min_ratio=1.0,
        liquidity_depth_pct=0.10,
        z_window=6,
        vol_ratio_window=3,
        z_exit_eps=1.00,
        z_hard_stop=10.0,
        entry_cooldown_sec=0,
        hedge_eps_base=10.0,
        allow_reverse=True,
        force_close_on_end=False,
    )

    events = [
        _mark("BTCUSDT", 500, 100.0, 0.001),
        _depth("BTCUSDT", 1000, 1, 100.0, 100.1),
        _depth("BTCUSDT_260626", 1000, 1, 101.0, 101.1),
        _depth("BTCUSDT", 2000, 2, 100.0, 100.1),
        _depth("BTCUSDT_260626", 2000, 2, 101.0, 101.1),
        _depth("BTCUSDT", 3000, 3, 100.0, 100.1),
        _depth("BTCUSDT_260626", 3000, 3, 101.0, 101.1),
        # Outlier basis -> should trigger standard entry (short perp / long future).
        _depth("BTCUSDT", 4000, 4, 100.0, 100.1),
        _depth("BTCUSDT_260626", 4000, 4, 97.0, 97.1),
        # Mean reversion -> should exit.
        _depth("BTCUSDT", 5000, 5, 100.0, 100.1),
        _depth("BTCUSDT_260626", 5000, 5, 101.0, 101.1),
    ]

    res = engine.run(events, strategy=strat)

    assert strat.entries_standard >= 1
    assert strat.exits_mean_reversion >= 1
    assert strat.state == "flat"

    p_perp = res.ctx.broker.portfolio.positions.get("BTCUSDT")
    p_fut = res.ctx.broker.portfolio.positions.get("BTCUSDT_260626")
    assert p_perp is not None and abs(p_perp.qty) <= 1e-9
    assert p_fut is not None and abs(p_fut.qty) <= 1e-9
    assert len(res.ctx.broker.fills) >= 4


def test_basis_funding_exits_on_funding_flip():
    broker = SimBroker(maker_fee_frac=0.0, taker_fee_frac=0.0)
    engine = BacktestEngine(config=EngineConfig(tick_interval_ms=0), broker=broker)

    strat = BasisFundingStrategy(
        perp_symbol="BTCUSDT",
        future_symbol="BTCUSDT_260626",
        impact_notional_usdt=100.0,
        funding_threshold=0.0001,
        max_slippage=0.0,
        entry_safety_margin=0.0,
        taker_fee_frac=0.0,
        liquidity_min_ratio=1.0,
        liquidity_depth_pct=0.10,
        z_window=6,
        vol_ratio_window=3,
        z_exit_eps=0.01,
        z_hard_stop=10.0,
        entry_cooldown_sec=0,
        hedge_eps_base=10.0,
        allow_reverse=True,
        force_close_on_end=False,
    )

    events = [
        _mark("BTCUSDT", 500, 100.0, 0.001),
        _depth("BTCUSDT", 1000, 1, 100.0, 100.1),
        _depth("BTCUSDT_260626", 1000, 1, 101.0, 101.1),
        _depth("BTCUSDT", 2000, 2, 100.0, 100.1),
        _depth("BTCUSDT_260626", 2000, 2, 101.0, 101.1),
        _depth("BTCUSDT", 3000, 3, 100.0, 100.1),
        _depth("BTCUSDT_260626", 3000, 3, 101.0, 101.1),
        _depth("BTCUSDT", 4000, 4, 100.0, 100.1),
        _depth("BTCUSDT_260626", 4000, 4, 97.0, 97.1),
        # Funding flips against standard mode.
        _mark("BTCUSDT", 4500, 100.0, -0.001),
    ]

    res = engine.run(events, strategy=strat)

    assert strat.entries_standard >= 1
    assert strat.exits_funding_flip >= 1
    assert strat.state == "flat"

    p_perp = res.ctx.broker.portfolio.positions.get("BTCUSDT")
    p_fut = res.ctx.broker.portfolio.positions.get("BTCUSDT_260626")
    assert p_perp is not None and math.isclose(p_perp.qty, 0.0, abs_tol=1e-9)
    assert p_fut is not None and math.isclose(p_fut.qty, 0.0, abs_tol=1e-9)
