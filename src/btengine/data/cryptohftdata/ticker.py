from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterator, Literal

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.fs as fs
import pyarrow.parquet as pq

from ...types import Ticker
from ._arrow import resolve_filesystem_and_path, resolve_path
from .paths import CryptoHftLayout


def iter_ticker(parquet_path: str | Path, *, filesystem: fs.FileSystem | None = None) -> Iterator[Ticker]:
    """Iterate ticker events from a CryptoHFTData `ticker.parquet` file."""

    sort_mode: Literal["auto", "always", "never"] = "auto"
    yield from iter_ticker_advanced(parquet_path, filesystem=filesystem, sort_mode=sort_mode)


def iter_ticker_advanced(
    parquet_path: str | Path,
    *,
    filesystem: fs.FileSystem | None = None,
    sort_mode: Literal["auto", "always", "never"] = "auto",
) -> Iterator[Ticker]:
    if filesystem is None:
        filesystem, resolved_path = resolve_filesystem_and_path(parquet_path)
    else:
        resolved_path = resolve_path(parquet_path)

    pf = pq.ParquetFile(resolved_path, filesystem=filesystem)

    cols = [
        "received_time",
        "event_time",
        "symbol",
        "price_change",
        "price_change_percent",
        "weighted_average_price",
        "last_price",
        "last_quantity",
        "open_price",
        "high_price",
        "low_price",
        "base_asset_volume",
        "quote_asset_volume",
        "statistics_open_time",
        "statistics_close_time",
        "first_trade_id",
        "last_trade_id",
        "total_trades",
    ]

    needs_sort = sort_mode == "always"
    if sort_mode == "auto" and pf.num_row_groups > 0:
        sample = pf.read_row_group(0, columns=["event_time"])
        arr = sample["event_time"].to_numpy(zero_copy_only=False)
        if len(arr) > 1:
            monotonic = bool((arr[1:] >= arr[:-1]).all())
            if not monotonic:
                needs_sort = True

    if sort_mode == "never":
        needs_sort = False

    float_cols = [
        "price_change",
        "price_change_percent",
        "weighted_average_price",
        "last_price",
        "last_quantity",
        "open_price",
        "high_price",
        "low_price",
        "base_asset_volume",
        "quote_asset_volume",
    ]

    if needs_sort:
        table = pf.read(columns=cols)
        for c in float_cols:
            table = table.set_column(table.schema.get_field_index(c), c, pc.cast(table[c], pa.float64()))
        sort_idx = pc.sort_indices(table["event_time"])
        table = table.take(sort_idx)

        received = table["received_time"].to_numpy(zero_copy_only=False)
        event_time = table["event_time"].to_numpy(zero_copy_only=False)
        symbol = table["symbol"].to_numpy(zero_copy_only=False)

        price_change = table["price_change"].to_numpy(zero_copy_only=False)
        price_change_percent = table["price_change_percent"].to_numpy(zero_copy_only=False)
        weighted_average_price = table["weighted_average_price"].to_numpy(zero_copy_only=False)
        last_price = table["last_price"].to_numpy(zero_copy_only=False)
        last_quantity = table["last_quantity"].to_numpy(zero_copy_only=False)
        open_price = table["open_price"].to_numpy(zero_copy_only=False)
        high_price = table["high_price"].to_numpy(zero_copy_only=False)
        low_price = table["low_price"].to_numpy(zero_copy_only=False)
        base_asset_volume = table["base_asset_volume"].to_numpy(zero_copy_only=False)
        quote_asset_volume = table["quote_asset_volume"].to_numpy(zero_copy_only=False)

        statistics_open_time = table["statistics_open_time"].to_numpy(zero_copy_only=False)
        statistics_close_time = table["statistics_close_time"].to_numpy(zero_copy_only=False)
        first_trade_id = table["first_trade_id"].to_numpy(zero_copy_only=False)
        last_trade_id = table["last_trade_id"].to_numpy(zero_copy_only=False)
        total_trades = table["total_trades"].to_numpy(zero_copy_only=False)

        for i in range(len(received)):
            yield Ticker(
                received_time_ns=int(received[i]),
                event_time_ms=int(event_time[i]),
                symbol=str(symbol[i]),
                price_change=float(price_change[i]),
                price_change_percent=float(price_change_percent[i]),
                weighted_average_price=float(weighted_average_price[i]),
                last_price=float(last_price[i]),
                last_quantity=float(last_quantity[i]),
                open_price=float(open_price[i]),
                high_price=float(high_price[i]),
                low_price=float(low_price[i]),
                base_asset_volume=float(base_asset_volume[i]),
                quote_asset_volume=float(quote_asset_volume[i]),
                statistics_open_time_ms=int(statistics_open_time[i]),
                statistics_close_time_ms=int(statistics_close_time[i]),
                first_trade_id=int(first_trade_id[i]),
                last_trade_id=int(last_trade_id[i]),
                total_trades=int(total_trades[i]),
            )
        return

    for rg in range(pf.num_row_groups):
        table = pf.read_row_group(rg, columns=cols)
        for c in float_cols:
            table = table.set_column(table.schema.get_field_index(c), c, pc.cast(table[c], pa.float64()))

        received = table["received_time"].to_numpy(zero_copy_only=False)
        event_time = table["event_time"].to_numpy(zero_copy_only=False)
        symbol = table["symbol"].to_numpy(zero_copy_only=False)

        price_change = table["price_change"].to_numpy(zero_copy_only=False)
        price_change_percent = table["price_change_percent"].to_numpy(zero_copy_only=False)
        weighted_average_price = table["weighted_average_price"].to_numpy(zero_copy_only=False)
        last_price = table["last_price"].to_numpy(zero_copy_only=False)
        last_quantity = table["last_quantity"].to_numpy(zero_copy_only=False)
        open_price = table["open_price"].to_numpy(zero_copy_only=False)
        high_price = table["high_price"].to_numpy(zero_copy_only=False)
        low_price = table["low_price"].to_numpy(zero_copy_only=False)
        base_asset_volume = table["base_asset_volume"].to_numpy(zero_copy_only=False)
        quote_asset_volume = table["quote_asset_volume"].to_numpy(zero_copy_only=False)

        statistics_open_time = table["statistics_open_time"].to_numpy(zero_copy_only=False)
        statistics_close_time = table["statistics_close_time"].to_numpy(zero_copy_only=False)
        first_trade_id = table["first_trade_id"].to_numpy(zero_copy_only=False)
        last_trade_id = table["last_trade_id"].to_numpy(zero_copy_only=False)
        total_trades = table["total_trades"].to_numpy(zero_copy_only=False)

        for i in range(len(received)):
            yield Ticker(
                received_time_ns=int(received[i]),
                event_time_ms=int(event_time[i]),
                symbol=str(symbol[i]),
                price_change=float(price_change[i]),
                price_change_percent=float(price_change_percent[i]),
                weighted_average_price=float(weighted_average_price[i]),
                last_price=float(last_price[i]),
                last_quantity=float(last_quantity[i]),
                open_price=float(open_price[i]),
                high_price=float(high_price[i]),
                low_price=float(low_price[i]),
                base_asset_volume=float(base_asset_volume[i]),
                quote_asset_volume=float(quote_asset_volume[i]),
                statistics_open_time_ms=int(statistics_open_time[i]),
                statistics_close_time_ms=int(statistics_close_time[i]),
                first_trade_id=int(first_trade_id[i]),
                last_trade_id=int(last_trade_id[i]),
                total_trades=int(total_trades[i]),
            )


def iter_ticker_for_day(
    layout: CryptoHftLayout,
    *,
    exchange: str,
    symbol: str,
    day: date,
    filesystem: fs.FileSystem | None = None,
) -> Iterator[Ticker]:
    uri = layout.ticker(exchange=exchange, symbol=symbol, day=day)
    yield from iter_ticker(uri, filesystem=filesystem)

