from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from btengine.data.cryptohftdata import iter_liquidations


def test_iter_liquidations_sorts_and_casts(tmp_path: Path) -> None:
    p = tmp_path / "liquidations.parquet"

    # Two rows, deliberately out of order by event_time.
    rows = [
        (2_000_000_000_000_000_000, 2_000, "BTCUSDT", "BUY", "LIMIT", "IOC", "0.2", "100.0", "101.0", "FILLED", "0.2", "0.2", 2_000),
        (1_000_000_000_000_000_000, 1_000, "BTCUSDT", "SELL", "LIMIT", "IOC", "0.1", "99.0", "99.5", "FILLED", "0.1", "0.1", 1_000),
    ]

    table = pa.table(
        {
            "received_time": pa.array([r[0] for r in rows], type=pa.int64()),
            "event_time": pa.array([r[1] for r in rows], type=pa.int64()),
            "symbol": pa.array([r[2] for r in rows], type=pa.string()),
            "side": pa.array([r[3] for r in rows], type=pa.string()),
            "order_type": pa.array([r[4] for r in rows], type=pa.string()),
            "time_in_force": pa.array([r[5] for r in rows], type=pa.string()),
            "quantity": pa.array([r[6] for r in rows], type=pa.string()),
            "price": pa.array([r[7] for r in rows], type=pa.string()),
            "average_price": pa.array([r[8] for r in rows], type=pa.string()),
            "order_status": pa.array([r[9] for r in rows], type=pa.string()),
            "last_filled_quantity": pa.array([r[10] for r in rows], type=pa.string()),
            "filled_quantity": pa.array([r[11] for r in rows], type=pa.string()),
            "trade_time": pa.array([r[12] for r in rows], type=pa.int64()),
        }
    )
    pq.write_table(table, p)

    out = list(iter_liquidations(p))
    assert [e.event_time_ms for e in out] == [1_000, 2_000]
    assert out[0].quantity == 0.1
    assert out[1].quantity == 0.2

