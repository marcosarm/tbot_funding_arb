from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterator, Literal

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.fs as fs
import pyarrow.parquet as pq

from ...types import Trade
from ._arrow import resolve_filesystem_and_path, resolve_path
from .paths import CryptoHftLayout


def iter_trades(parquet_path: str | Path, *, filesystem: fs.FileSystem | None = None) -> Iterator[Trade]:
    """Iterate trades from a CryptoHFTData `trades.parquet` file.

    Trades are iterated in ascending `trade_time` order (ms). Some datasets may
    not be stored sorted; this function sorts when needed.
    """

    sort_mode: Literal["auto", "always", "never"] = "auto"
    yield from iter_trades_advanced(parquet_path, filesystem=filesystem, sort_mode=sort_mode)


def iter_trades_advanced(
    parquet_path: str | Path,
    *,
    filesystem: fs.FileSystem | None = None,
    sort_mode: Literal["auto", "always", "never"] = "auto",
) -> Iterator[Trade]:
    """Advanced variant with explicit sort control."""

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
        "trade_id",
        "price",
        "quantity",
        "is_buyer_maker",
    ]

    needs_sort = sort_mode == "always"
    if sort_mode == "auto" and pf.num_row_groups > 0:
        sample = pf.read_row_group(0, columns=["trade_time"])
        arr = sample["trade_time"].to_numpy(zero_copy_only=False)
        if len(arr) > 1:
            monotonic = bool((arr[1:] >= arr[:-1]).all())
            if not monotonic:
                needs_sort = True

    if sort_mode == "never":
        needs_sort = False

    if needs_sort:
        table = pf.read(columns=cols)

        table = table.set_column(table.schema.get_field_index("price"), "price", pc.cast(table["price"], pa.float64()))
        table = table.set_column(
            table.schema.get_field_index("quantity"), "quantity", pc.cast(table["quantity"], pa.float64())
        )

        sort_idx = pc.sort_indices(table["trade_time"])
        table = table.take(sort_idx)

        received = table["received_time"].to_numpy(zero_copy_only=False)
        trade_time = table["trade_time"].to_numpy(zero_copy_only=False)
        symbol = table["symbol"].to_numpy(zero_copy_only=False)
        trade_id = table["trade_id"].to_numpy(zero_copy_only=False)
        is_buyer_maker = table["is_buyer_maker"].to_numpy(zero_copy_only=False)
        price = table["price"].to_numpy(zero_copy_only=False)
        qty = table["quantity"].to_numpy(zero_copy_only=False)

        for i in range(len(received)):
            tt = int(trade_time[i])
            yield Trade(
                received_time_ns=int(received[i]),
                event_time_ms=tt,  # use trade_time as canonical timestamp
                trade_time_ms=tt,
                symbol=str(symbol[i]),
                trade_id=int(trade_id[i]),
                price=float(price[i]),
                quantity=float(qty[i]),
                is_buyer_maker=bool(is_buyer_maker[i]),
            )
        return

    for rg in range(pf.num_row_groups):
        table = pf.read_row_group(rg, columns=cols)

        received = table["received_time"].to_numpy(zero_copy_only=False)
        trade_time = table["trade_time"].to_numpy(zero_copy_only=False)
        symbol = table["symbol"].to_numpy(zero_copy_only=False)
        trade_id = table["trade_id"].to_numpy(zero_copy_only=False)
        is_buyer_maker = table["is_buyer_maker"].to_numpy(zero_copy_only=False)

        price = pc.cast(table["price"], pa.float64()).to_numpy(zero_copy_only=False)
        qty = pc.cast(table["quantity"], pa.float64()).to_numpy(zero_copy_only=False)

        for i in range(len(received)):
            tt = int(trade_time[i])
            yield Trade(
                received_time_ns=int(received[i]),
                event_time_ms=tt,  # use trade_time as canonical timestamp
                trade_time_ms=tt,
                symbol=str(symbol[i]),
                trade_id=int(trade_id[i]),
                price=float(price[i]),
                quantity=float(qty[i]),
                is_buyer_maker=bool(is_buyer_maker[i]),
            )


def iter_trades_for_day(
    layout: CryptoHftLayout,
    *,
    exchange: str,
    symbol: str,
    day: date,
    filesystem: fs.FileSystem | None = None,
) -> Iterator[Trade]:
    uri = layout.trades(exchange=exchange, symbol=symbol, day=day)
    yield from iter_trades(uri, filesystem=filesystem)
