from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterator

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.fs as fs
import pyarrow.parquet as pq

from ...types import MarkPrice
from ._arrow import resolve_filesystem_and_path, resolve_path
from .paths import CryptoHftLayout


def iter_mark_price(parquet_path: str | Path, *, filesystem: fs.FileSystem | None = None) -> Iterator[MarkPrice]:
    """Iterate MarkPrice events from a CryptoHFTData `mark_price.parquet` file."""

    if filesystem is None:
        filesystem, resolved_path = resolve_filesystem_and_path(parquet_path)
    else:
        resolved_path = resolve_path(parquet_path)
    pf = pq.ParquetFile(resolved_path, filesystem=filesystem)

    cols = [
        "received_time",
        "event_time",
        "symbol",
        "mark_price",
        "index_price",
        "funding_rate",
        "next_funding_time",
    ]

    for rg in range(pf.num_row_groups):
        table = pf.read_row_group(rg, columns=cols)

        received = table["received_time"].to_numpy(zero_copy_only=False)
        event_time = table["event_time"].to_numpy(zero_copy_only=False)
        symbol = table["symbol"].to_numpy(zero_copy_only=False)

        mark = pc.cast(table["mark_price"], pa.float64()).to_numpy(zero_copy_only=False)
        index = pc.cast(table["index_price"], pa.float64()).to_numpy(zero_copy_only=False)
        funding = pc.cast(table["funding_rate"], pa.float64()).to_numpy(zero_copy_only=False)
        next_ft = table["next_funding_time"].to_numpy(zero_copy_only=False)

        for i in range(len(received)):
            yield MarkPrice(
                received_time_ns=int(received[i]),
                event_time_ms=int(event_time[i]),
                symbol=str(symbol[i]),
                mark_price=float(mark[i]),
                index_price=float(index[i]),
                funding_rate=float(funding[i]),
                next_funding_time_ms=int(next_ft[i]),
            )


def iter_mark_price_for_day(
    layout: CryptoHftLayout,
    *,
    exchange: str,
    symbol: str,
    day: date,
    filesystem: fs.FileSystem | None = None,
) -> Iterator[MarkPrice]:
    uri = layout.mark_price(exchange=exchange, symbol=symbol, day=day)
    yield from iter_mark_price(uri, filesystem=filesystem)
