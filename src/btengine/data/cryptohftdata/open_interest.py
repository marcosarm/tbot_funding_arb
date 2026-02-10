from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.fs as fs
import pyarrow.parquet as pq

from ...types import OpenInterest
from ._arrow import resolve_filesystem_and_path, resolve_path
from .paths import CryptoHftLayout


def iter_open_interest(
    parquet_path: str | Path, *, filesystem: fs.FileSystem | None = None
) -> Iterator[OpenInterest]:
    """Iterate open-interest snapshot events from `open_interest.parquet`."""

    sort_mode: Literal["auto", "always", "never"] = "auto"
    yield from iter_open_interest_advanced(parquet_path, filesystem=filesystem, sort_mode=sort_mode)


def iter_open_interest_advanced(
    parquet_path: str | Path,
    *,
    filesystem: fs.FileSystem | None = None,
    sort_mode: Literal["auto", "always", "never"] = "auto",
) -> Iterator[OpenInterest]:
    if filesystem is None:
        filesystem, resolved_path = resolve_filesystem_and_path(parquet_path)
    else:
        resolved_path = resolve_path(parquet_path)

    pf = pq.ParquetFile(resolved_path, filesystem=filesystem)

    cols = [
        "received_time",
        "timestamp",
        "symbol",
        "sum_open_interest",
        "sum_open_interest_value",
    ]

    needs_sort = sort_mode == "always"
    if sort_mode == "auto" and pf.num_row_groups > 0:
        sample = pf.read_row_group(0, columns=["timestamp"])
        arr = sample["timestamp"].to_numpy(zero_copy_only=False)
        if len(arr) > 1:
            monotonic = bool((arr[1:] >= arr[:-1]).all())
            if not monotonic:
                needs_sort = True

    if sort_mode == "never":
        needs_sort = False

    if needs_sort:
        table = pf.read(columns=cols)
        table = table.set_column(
            table.schema.get_field_index("sum_open_interest"),
            "sum_open_interest",
            pc.cast(table["sum_open_interest"], pa.float64()),
        )
        table = table.set_column(
            table.schema.get_field_index("sum_open_interest_value"),
            "sum_open_interest_value",
            pc.cast(table["sum_open_interest_value"], pa.float64()),
        )

        sort_idx = pc.sort_indices(table["timestamp"])
        table = table.take(sort_idx)

        received = table["received_time"].to_numpy(zero_copy_only=False)
        timestamp = table["timestamp"].to_numpy(zero_copy_only=False)
        symbol = table["symbol"].to_numpy(zero_copy_only=False)
        sum_oi = table["sum_open_interest"].to_numpy(zero_copy_only=False)
        sum_oi_val = table["sum_open_interest_value"].to_numpy(zero_copy_only=False)

        for i in range(len(received)):
            ts = int(timestamp[i])
            yield OpenInterest(
                received_time_ns=int(received[i]),
                event_time_ms=ts,
                timestamp_ms=ts,
                symbol=str(symbol[i]),
                sum_open_interest=float(sum_oi[i]),
                sum_open_interest_value=float(sum_oi_val[i]),
            )
        return

    for rg in range(pf.num_row_groups):
        table = pf.read_row_group(rg, columns=cols)

        sum_oi = pc.cast(table["sum_open_interest"], pa.float64()).to_numpy(zero_copy_only=False)
        sum_oi_val = pc.cast(table["sum_open_interest_value"], pa.float64()).to_numpy(zero_copy_only=False)

        received = table["received_time"].to_numpy(zero_copy_only=False)
        timestamp = table["timestamp"].to_numpy(zero_copy_only=False)
        symbol = table["symbol"].to_numpy(zero_copy_only=False)

        for i in range(len(received)):
            ts = int(timestamp[i])
            yield OpenInterest(
                received_time_ns=int(received[i]),
                event_time_ms=ts,
                timestamp_ms=ts,
                symbol=str(symbol[i]),
                sum_open_interest=float(sum_oi[i]),
                sum_open_interest_value=float(sum_oi_val[i]),
            )


def _iter_open_interest_for_day_from_uri(
    parquet_uri: str | Path,
    *,
    day: date,
    filesystem: fs.FileSystem | None,
) -> Iterator[OpenInterest]:
    """Read an open-interest file and filter to the requested UTC day.

    Some datasets may store multi-day responses in an `open_interest_parcial.parquet`.
    This helper guarantees day filtering and sorting.
    """

    day_start_ms = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)
    day_end_ms = day_start_ms + 86_400_000

    if filesystem is None:
        filesystem, resolved_path = resolve_filesystem_and_path(parquet_uri)
    else:
        resolved_path = resolve_path(parquet_uri)

    pf = pq.ParquetFile(resolved_path, filesystem=filesystem)
    cols = [
        "received_time",
        "timestamp",
        "symbol",
        "sum_open_interest",
        "sum_open_interest_value",
    ]

    table = pf.read(columns=cols)
    table = table.set_column(
        table.schema.get_field_index("sum_open_interest"),
        "sum_open_interest",
        pc.cast(table["sum_open_interest"], pa.float64()),
    )
    table = table.set_column(
        table.schema.get_field_index("sum_open_interest_value"),
        "sum_open_interest_value",
        pc.cast(table["sum_open_interest_value"], pa.float64()),
    )

    m0 = pc.greater_equal(table["timestamp"], pa.scalar(day_start_ms, pa.int64()))
    m1 = pc.less(table["timestamp"], pa.scalar(day_end_ms, pa.int64()))
    mask = pc.and_(m0, m1)
    table = table.filter(mask)

    # Sort by timestamp after filtering.
    if table.num_rows:
        sort_idx = pc.sort_indices(table["timestamp"])
        table = table.take(sort_idx)

    received = table["received_time"].to_numpy(zero_copy_only=False)
    timestamp = table["timestamp"].to_numpy(zero_copy_only=False)
    symbol = table["symbol"].to_numpy(zero_copy_only=False)
    sum_oi = table["sum_open_interest"].to_numpy(zero_copy_only=False)
    sum_oi_val = table["sum_open_interest_value"].to_numpy(zero_copy_only=False)

    for i in range(len(received)):
        ts = int(timestamp[i])
        yield OpenInterest(
            received_time_ns=int(received[i]),
            event_time_ms=ts,
            timestamp_ms=ts,
            symbol=str(symbol[i]),
            sum_open_interest=float(sum_oi[i]),
            sum_open_interest_value=float(sum_oi_val[i]),
        )


def iter_open_interest_for_day(
    layout: CryptoHftLayout,
    *,
    exchange: str,
    symbol: str,
    day: date,
    filesystem: fs.FileSystem | None = None,
) -> Iterator[OpenInterest]:
    uri = layout.open_interest(exchange=exchange, symbol=symbol, day=day)
    try:
        yield from _iter_open_interest_for_day_from_uri(uri, day=day, filesystem=filesystem)
    except FileNotFoundError:
        # Fallback: some datasets store multi-day results under this name.
        if str(uri).endswith("/open_interest.parquet"):
            alt = str(uri)[: -len("open_interest.parquet")] + "open_interest_parcial.parquet"
        else:
            alt = str(uri).replace("open_interest.parquet", "open_interest_parcial.parquet")
        yield from _iter_open_interest_for_day_from_uri(alt, day=day, filesystem=filesystem)
