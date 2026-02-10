import math

from btengine.marketdata import L2Book


def test_best_bid_ask_and_mid():
    book = L2Book()
    book.apply_depth_update(bid_updates=[(100.0, 1.0)], ask_updates=[(101.0, 2.0)])

    assert book.best_bid() == 100.0
    assert book.best_ask() == 101.0
    assert book.mid_price() == 100.5


def test_impact_vwap_partial_fill():
    book = L2Book()
    # Asks: 1 @ 100, 1 @ 101
    book.apply_depth_update(bid_updates=[], ask_updates=[(100.0, 1.0), (101.0, 1.0)])

    # Buy notional 150 => take 1 @ 100 (=100) + 0.4950495 @ 101 (=50)
    vwap = book.impact_vwap("buy", 150.0, max_levels=10)
    assert not math.isnan(vwap)
    assert abs(vwap - (150.0 / (1.0 + 50.0 / 101.0))) < 1e-9


def test_impact_vwap_insufficient_depth_returns_nan():
    book = L2Book()
    book.apply_depth_update(bid_updates=[], ask_updates=[(100.0, 0.5)])
    vwap = book.impact_vwap("buy", 100.0, max_levels=10)
    assert math.isnan(vwap)

