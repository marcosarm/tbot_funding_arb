from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect CryptoHFTData orderbook parquet (flattened L2 deltas).")
    ap.add_argument("path", type=Path, help="Path to orderbook_XX.parquet")
    args = ap.parse_args()

    pf = pq.ParquetFile(args.path)
    print(f"path: {args.path}")
    print(f"row_groups: {pf.metadata.num_row_groups}")
    print(f"rows: {pf.metadata.num_rows}")
    print(f"created_by: {pf.metadata.created_by}")
    print("\nschema:")
    print(pf.schema_arrow)

    cols = ["event_time", "final_update_id", "prev_final_update_id", "event_type", "last_update_id", "order_count"]
    df = pq.read_table(args.path, columns=cols).to_pandas()

    print("\nevent_time range (ms):")
    print(f"  min: {int(df['event_time'].min())} ({_utc(int(df['event_time'].min()))})")
    print(f"  max: {int(df['event_time'].max())} ({_utc(int(df['event_time'].max()))})")

    print("\nmonotonicity checks:")
    ev = df["event_time"].to_numpy()
    fid = df["final_update_id"].to_numpy()
    print(f"  event_time non-decreasing: {bool(np.all(ev[1:] >= ev[:-1]))}")
    print(f"  final_update_id non-decreasing: {bool(np.all(fid[1:] >= fid[:-1]))}")

    event_types = sorted(df["event_type"].dropna().unique().tolist())
    print("\nevent_type unique:", event_types)
    print(f"last_update_id non-null rows: {int(df['last_update_id'].notna().sum())}")
    print(f"order_count non-null rows: {int(df['order_count'].notna().sum())}")

    # Per-message stats (grouped by final_update_id)
    counts = df["final_update_id"].value_counts()
    print("\nmessages:")
    print(f"  distinct final_update_id: {int(counts.size)}")
    print("\nrows/message:")
    desc = counts.describe(percentiles=[0.5, 0.9, 0.95, 0.99])
    print(desc.to_string())

    # Continuity check using unique messages
    unique = df[["final_update_id", "prev_final_update_id", "event_time"]].drop_duplicates("final_update_id")
    unique = unique.sort_values(["event_time", "final_update_id"]).reset_index(drop=True)
    prev = unique["final_update_id"].shift(1)
    ok = (unique["prev_final_update_id"] == prev)
    ok.iloc[0] = True
    print("\nupdate_id continuity:")
    print(f"  prev_final_update_id matches previous final_update_id: {float(ok.mean()):.6f}")
    print(f"  mismatches: {int((~ok).sum())}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

