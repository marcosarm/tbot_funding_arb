from btengine.replay import slice_event_stream
from btengine.types import Trade


def _trade(t: int) -> Trade:
    return Trade(
        received_time_ns=0,
        event_time_ms=t,
        trade_time_ms=t,
        symbol="BTCUSDT",
        trade_id=t,
        price=100.0,
        quantity=1.0,
        is_buyer_maker=True,
    )


def test_slice_event_stream_no_window_yields_all():
    events = [_trade(1), _trade(2), _trade(3)]
    out = list(slice_event_stream(events))
    assert [e.event_time_ms for e in out] == [1, 2, 3]


def test_slice_event_stream_start_only_skips_prefix():
    events = [_trade(1), _trade(2), _trade(3)]
    out = list(slice_event_stream(events, start_ms=2))
    assert [e.event_time_ms for e in out] == [2, 3]


def test_slice_event_stream_end_only_stops_early():
    events = [_trade(1), _trade(2), _trade(3)]
    out = list(slice_event_stream(events, end_ms=3))
    assert [e.event_time_ms for e in out] == [1, 2]


def test_slice_event_stream_start_end_slices_half_open_interval():
    events = [_trade(1), _trade(2), _trade(3), _trade(4)]
    out = list(slice_event_stream(events, start_ms=2, end_ms=4))
    assert [e.event_time_ms for e in out] == [2, 3]

