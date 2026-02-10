from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from btengine.data.cryptohftdata import iter_open_interest


def test_iter_open_interest_sorts_and_casts(tmp_path: Path) -> None:
    p = tmp_path / "open_interest.parquet"

    # Two rows, deliberately out of order by timestamp.
    rows = [
        (2_000_000_000_000_000_000, "BTCUSDT", "10.0", "1000.0", 2_000),
        (1_000_000_000_000_000_000, "BTCUSDT", "11.0", "1100.0", 1_000),
    ]

    table = pa.table(
        {
            "received_time": pa.array([r[0] for r in rows], type=pa.int64()),
            "symbol": pa.array([r[1] for r in rows], type=pa.string()),
            "sum_open_interest": pa.array([r[2] for r in rows], type=pa.string()),
            "sum_open_interest_value": pa.array([r[3] for r in rows], type=pa.string()),
            "timestamp": pa.array([r[4] for r in rows], type=pa.int64()),
        }
    )
    pq.write_table(table, p)

    out = list(iter_open_interest(p))
    assert [e.event_time_ms for e in out] == [1_000, 2_000]
    assert [e.timestamp_ms for e in out] == [1_000, 2_000]
    assert out[0].sum_open_interest == 11.0
    assert out[1].sum_open_interest == 10.0
