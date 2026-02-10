from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterator, Literal

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.fs as fs
import pyarrow.parquet as pq

from ...types import Liquidation
from ._arrow import resolve_filesystem_and_path, resolve_path
from .paths import CryptoHftLayout


def iter_liquidations(
    parquet_path: str | Path, *, filesystem: fs.FileSystem | None = None
) -> Iterator[Liquidation]:
    """Iterate liquidation events from a CryptoHFTData `liquidations.parquet` file."""

    sort_mode: Literal["auto", "always", "never"] = "auto"
    yield from iter_liquidations_advanced(parquet_path, filesystem=filesystem, sort_mode=sort_mode)


def iter_liquidations_advanced(
    parquet_path: str | Path,
    *,
    filesystem: fs.FileSystem | None = None,
    sort_mode: Literal["auto", "always", "never"] = "auto",
) -> Iterator[Liquidation]:
    if filesystem is None:
        filesystem, resolved_path = resolve_filesystem_and_path(parquet_path)
    else:
        resolved_path = resolve_path(parquet_path)

    pf = pq.ParquetFile(resolved_path, filesystem=filesystem)

    cols = [
        "received_time",
        "event_time",
        "trade_time",
        "symbol",
        "side",
        "order_type",
        "time_in_force",
        "quantity",
        "price",
        "average_price",
        "order_status",
        "last_filled_quantity",
        "filled_quantity",
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
        "quantity",
        "price",
        "average_price",
        "last_filled_quantity",
        "filled_quantity",
    ]

    if needs_sort:
        table = pf.read(columns=cols)
        for c in float_cols:
            table = table.set_column(table.schema.get_field_index(c), c, pc.cast(table[c], pa.float64()))

        sort_idx = pc.sort_indices(table["event_time"])
        table = table.take(sort_idx)

        received = table["received_time"].to_numpy(zero_copy_only=False)
        event_time = table["event_time"].to_numpy(zero_copy_only=False)
        trade_time = table["trade_time"].to_numpy(zero_copy_only=False)
        symbol = table["symbol"].to_numpy(zero_copy_only=False)
        side = table["side"].to_numpy(zero_copy_only=False)
        order_type = table["order_type"].to_numpy(zero_copy_only=False)
        tif = table["time_in_force"].to_numpy(zero_copy_only=False)
        order_status = table["order_status"].to_numpy(zero_copy_only=False)

        quantity = table["quantity"].to_numpy(zero_copy_only=False)
        price = table["price"].to_numpy(zero_copy_only=False)
        avg_price = table["average_price"].to_numpy(zero_copy_only=False)
        last_filled_qty = table["last_filled_quantity"].to_numpy(zero_copy_only=False)
        filled_qty = table["filled_quantity"].to_numpy(zero_copy_only=False)

        for i in range(len(received)):
            yield Liquidation(
                received_time_ns=int(received[i]),
                event_time_ms=int(event_time[i]),
                symbol=str(symbol[i]),
                side=str(side[i]),
                order_type=str(order_type[i]),
                time_in_force=str(tif[i]),
                quantity=float(quantity[i]),
                price=float(price[i]),
                average_price=float(avg_price[i]),
                order_status=str(order_status[i]),
                last_filled_quantity=float(last_filled_qty[i]),
                filled_quantity=float(filled_qty[i]),
                trade_time_ms=int(trade_time[i]),
            )
        return

    for rg in range(pf.num_row_groups):
        table = pf.read_row_group(rg, columns=cols)

        received = table["received_time"].to_numpy(zero_copy_only=False)
        event_time = table["event_time"].to_numpy(zero_copy_only=False)
        trade_time = table["trade_time"].to_numpy(zero_copy_only=False)
        symbol = table["symbol"].to_numpy(zero_copy_only=False)
        side = table["side"].to_numpy(zero_copy_only=False)
        order_type = table["order_type"].to_numpy(zero_copy_only=False)
        tif = table["time_in_force"].to_numpy(zero_copy_only=False)
        order_status = table["order_status"].to_numpy(zero_copy_only=False)

        quantity = pc.cast(table["quantity"], pa.float64()).to_numpy(zero_copy_only=False)
        price = pc.cast(table["price"], pa.float64()).to_numpy(zero_copy_only=False)
        avg_price = pc.cast(table["average_price"], pa.float64()).to_numpy(zero_copy_only=False)
        last_filled_qty = pc.cast(table["last_filled_quantity"], pa.float64()).to_numpy(zero_copy_only=False)
        filled_qty = pc.cast(table["filled_quantity"], pa.float64()).to_numpy(zero_copy_only=False)

        for i in range(len(received)):
            yield Liquidation(
                received_time_ns=int(received[i]),
                event_time_ms=int(event_time[i]),
                symbol=str(symbol[i]),
                side=str(side[i]),
                order_type=str(order_type[i]),
                time_in_force=str(tif[i]),
                quantity=float(quantity[i]),
                price=float(price[i]),
                average_price=float(avg_price[i]),
                order_status=str(order_status[i]),
                last_filled_quantity=float(last_filled_qty[i]),
                filled_quantity=float(filled_qty[i]),
                trade_time_ms=int(trade_time[i]),
            )


def iter_liquidations_for_day(
    layout: CryptoHftLayout,
    *,
    exchange: str,
    symbol: str,
    day: date,
    filesystem: fs.FileSystem | None = None,
) -> Iterator[Liquidation]:
    uri = layout.liquidations(exchange=exchange, symbol=symbol, day=day)
    yield from iter_liquidations(uri, filesystem=filesystem)

