from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

import pyarrow.fs as fs

from ...replay import merge_event_streams, slice_event_stream
from ...types import DepthUpdate, Liquidation, MarkPrice, OpenInterest, Ticker, Trade
from .liquidations import iter_liquidations_for_day
from .mark_price import iter_mark_price_for_day
from .open_interest import iter_open_interest_for_day
from .orderbook import iter_depth_updates_for_day
from .paths import CryptoHftLayout
from .ticker import iter_ticker_for_day
from .trades import iter_trades_for_day


@dataclass(frozen=True, slots=True)
class CryptoHftDayConfig:
    exchange: str = "binance_futures"
    include_trades: bool = True
    include_orderbook: bool = True
    include_mark_price: bool = False
    include_ticker: bool = False
    include_open_interest: bool = False
    include_liquidations: bool = False
    open_interest_delay_ms: int = 0
    orderbook_hours: range = range(24)
    orderbook_skip_missing: bool = True
    skip_missing_daily_files: bool = False
    stream_start_ms: int | None = None
    stream_end_ms: int | None = None


def build_day_stream(
    layout: CryptoHftLayout,
    *,
    cfg: CryptoHftDayConfig,
    symbol: str,
    day: date,
    filesystem: fs.FileSystem | None = None,
) -> Iterable[DepthUpdate | Trade | MarkPrice | Ticker | OpenInterest | Liquidation]:
    """Build a merged event stream for one symbol on one day.

    If `cfg.stream_start_ms`/`cfg.stream_end_ms` are provided, each underlying
    stream is time-sliced before merging (useful to focus on specific hours).
    """

    streams: list[Iterable[DepthUpdate | Trade | MarkPrice | Ticker | OpenInterest | Liquidation]] = []
    start_ms = cfg.stream_start_ms
    end_ms = cfg.stream_end_ms

    def _safe(stream: Iterable[DepthUpdate | Trade | MarkPrice | Ticker | OpenInterest | Liquidation]):
        try:
            yield from stream
        except FileNotFoundError:
            if cfg.skip_missing_daily_files:
                return
            raise

    if cfg.include_orderbook:
        ob = (
            iter_depth_updates_for_day(
                layout,
                exchange=cfg.exchange,
                symbol=symbol,
                day=day,
                filesystem=filesystem,
                hours=cfg.orderbook_hours,
                skip_missing=cfg.orderbook_skip_missing,
            )
        )
        if start_ms is not None or end_ms is not None:
            ob = slice_event_stream(ob, start_ms=start_ms, end_ms=end_ms)
        streams.append(ob)

    if cfg.include_trades:
        tr = iter_trades_for_day(layout, exchange=cfg.exchange, symbol=symbol, day=day, filesystem=filesystem)
        if start_ms is not None or end_ms is not None:
            tr = slice_event_stream(tr, start_ms=start_ms, end_ms=end_ms)
        streams.append(_safe(tr))

    if cfg.include_mark_price:
        mp = iter_mark_price_for_day(layout, exchange=cfg.exchange, symbol=symbol, day=day, filesystem=filesystem)
        if start_ms is not None or end_ms is not None:
            mp = slice_event_stream(mp, start_ms=start_ms, end_ms=end_ms)
        streams.append(_safe(mp))

    if cfg.include_ticker:
        tk = iter_ticker_for_day(layout, exchange=cfg.exchange, symbol=symbol, day=day, filesystem=filesystem)
        if start_ms is not None or end_ms is not None:
            tk = slice_event_stream(tk, start_ms=start_ms, end_ms=end_ms)
        streams.append(_safe(tk))

    if cfg.include_open_interest:
        oi = iter_open_interest_for_day(layout, exchange=cfg.exchange, symbol=symbol, day=day, filesystem=filesystem)
        if cfg.open_interest_delay_ms:
            if cfg.open_interest_delay_ms < 0:
                raise ValueError("open_interest_delay_ms must be >= 0")
            delay = int(cfg.open_interest_delay_ms)

            def _shifted(stream: Iterable[OpenInterest]) -> Iterable[OpenInterest]:
                for ev in stream:
                    yield OpenInterest(
                        received_time_ns=int(ev.received_time_ns),
                        event_time_ms=int(ev.timestamp_ms) + delay,
                        timestamp_ms=int(ev.timestamp_ms),
                        symbol=str(ev.symbol),
                        sum_open_interest=float(ev.sum_open_interest),
                        sum_open_interest_value=float(ev.sum_open_interest_value),
                    )

            oi = _shifted(oi)
        if start_ms is not None or end_ms is not None:
            oi = slice_event_stream(oi, start_ms=start_ms, end_ms=end_ms)
        streams.append(_safe(oi))

    if cfg.include_liquidations:
        liq = iter_liquidations_for_day(layout, exchange=cfg.exchange, symbol=symbol, day=day, filesystem=filesystem)
        if start_ms is not None or end_ms is not None:
            liq = slice_event_stream(liq, start_ms=start_ms, end_ms=end_ms)
        streams.append(_safe(liq))

    return merge_event_streams(*streams)
