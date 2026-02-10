from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pyarrow.compute as pc
import pyarrow as pa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from btengine.data.cryptohftdata import CryptoHftLayout, S3Config, make_s3_filesystem
from btengine.util import load_dotenv


def _parse_day(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _s3_path(uri: str) -> str:
    return uri[len("s3://") :] if uri.startswith("s3://") else uri


@dataclass(frozen=True, slots=True)
class ParquetQuickInfo:
    uri: str
    rows: int
    row_groups: int
    schema: str
    first_event_time_ms: int | None
    last_event_time_ms: int | None
    first_final_update_id: int | None
    last_final_update_id: int | None
    first_prev_final_update_id: int | None


def inspect_parquet(
    uri: str,
    *,
    fs,
    time_col: str | None = None,
    orderbook_ids: bool = False,
) -> ParquetQuickInfo:
    path = _s3_path(uri)
    pf = pq.ParquetFile(path, filesystem=fs)

    rows = pf.metadata.num_rows
    row_groups = pf.metadata.num_row_groups
    schema = str(pf.schema_arrow)

    first_time = last_time = None
    first_final = last_final = first_prev_final = None

    if time_col:
        cols = [time_col]
        if orderbook_ids:
            cols += ["final_update_id", "prev_final_update_id"]

        # Do not assume parquet is stored in time/update order. Compute min/max
        # from columns to support interleaved storage.
        t = pf.read(columns=cols)
        first_time = int(pc.min(t[time_col]).as_py())
        last_time = int(pc.max(t[time_col]).as_py())

        if orderbook_ids:
            min_final = int(pc.min(t["final_update_id"]).as_py())
            max_final = int(pc.max(t["final_update_id"]).as_py())
            first_final = min_final
            last_final = max_final

            # prev_final_update_id is constant within a final_update_id group.
            m = pc.equal(t["final_update_id"], pa.scalar(min_final, pa.int64()))
            prev_vals = pc.filter(t["prev_final_update_id"], m)
            if len(prev_vals) > 0:
                first_prev_final = int(prev_vals[0].as_py())

    return ParquetQuickInfo(
        uri=uri,
        rows=rows,
        row_groups=row_groups,
        schema=schema,
        first_event_time_ms=first_time,
        last_event_time_ms=last_time,
        first_final_update_id=first_final,
        last_final_update_id=last_final,
        first_prev_final_update_id=first_prev_final,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate CryptoHFTData parquet dataset on S3.")
    ap.add_argument("--dotenv", default=str(ROOT / ".env"), help="Path to .env with S3/AWS settings.")
    ap.add_argument("--exchange", default="binance_futures")
    ap.add_argument("--day", required=True, help="YYYY-MM-DD (UTC)")
    ap.add_argument(
        "--symbols",
        default="BTCUSDT",
        help="Comma-separated symbols (e.g. BTCUSDT,BTCUSDT_260626).",
    )
    ap.add_argument("--hours", default="0-23", help="Orderbook hours range (e.g. 0-23 or 12-12).")
    ap.add_argument("--skip-missing", action="store_true", help="Skip missing files instead of failing.")
    args = ap.parse_args()

    env = load_dotenv(args.dotenv, override=False).values

    bucket = env.get("S3_BUCKET")
    prefix = env.get("S3_PREFIX")
    region = env.get("AWS_REGION") or None

    if not bucket or not prefix:
        print("ERROR: missing S3_BUCKET or S3_PREFIX in .env", file=sys.stderr)
        return 2

    access_key = env.get("AWS_ACCESS_KEY_ID") or None
    secret_key = env.get("AWS_SECRET_ACCESS_KEY") or None

    fs = make_s3_filesystem(S3Config(region=region, access_key=access_key, secret_key=secret_key))
    layout = CryptoHftLayout(bucket=bucket, prefix=prefix)

    day = _parse_day(args.day)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    # Parse hours.
    if "-" in args.hours:
        a, b = args.hours.split("-", 1)
        h0, h1 = int(a), int(b)
        hours = range(h0, h1 + 1)
    else:
        hh = int(args.hours)
        hours = range(hh, hh + 1)

    failures = 0

    for sym in symbols:
        print(f"\n== {sym} ({args.exchange} {day.isoformat()}) ==")

        # Trades
        trades_uri = layout.trades(exchange=args.exchange, symbol=sym, day=day)
        try:
            info = inspect_parquet(trades_uri, fs=fs, time_col="event_time", orderbook_ids=False)
            print(f"trades: rows={info.rows} rg={info.row_groups} time=[{info.first_event_time_ms},{info.last_event_time_ms}]")
        except FileNotFoundError as e:
            print(f"trades: MISSING {e!r}")
            if not args.skip_missing:
                failures += 1
        except Exception as e:
            print(f"trades: ERROR {e!r}")
            failures += 1
            if not args.skip_missing:
                continue

        # Mark price (may not exist for futures symbols depending on your dataset)
        mp_uri = layout.mark_price(exchange=args.exchange, symbol=sym, day=day)
        try:
            info = inspect_parquet(mp_uri, fs=fs, time_col="event_time", orderbook_ids=False)
            print(f"mark_price: rows={info.rows} rg={info.row_groups} time=[{info.first_event_time_ms},{info.last_event_time_ms}]")
        except FileNotFoundError as e:
            print(f"mark_price: MISSING {e!r}")
        except Exception as e:
            print(f"mark_price: WARN {e!r}")

        # Ticker (may not exist for some symbols/layouts)
        ticker_uri = layout.ticker(exchange=args.exchange, symbol=sym, day=day)
        try:
            info = inspect_parquet(ticker_uri, fs=fs, time_col="event_time", orderbook_ids=False)
            print(f"ticker: rows={info.rows} rg={info.row_groups} time=[{info.first_event_time_ms},{info.last_event_time_ms}]")
        except FileNotFoundError as e:
            print(f"ticker: MISSING {e!r}")
        except Exception as e:
            print(f"ticker: WARN {e!r}")

        # Open interest (may not exist for some symbols/layouts)
        oi_uri = layout.open_interest(exchange=args.exchange, symbol=sym, day=day)
        try:
            info = inspect_parquet(oi_uri, fs=fs, time_col="timestamp", orderbook_ids=False)
            print(f"open_interest: rows={info.rows} rg={info.row_groups} time=[{info.first_event_time_ms},{info.last_event_time_ms}]")
        except FileNotFoundError as e:
            print(f"open_interest: MISSING {e!r}")
        except Exception as e:
            print(f"open_interest: WARN {e!r}")

        # Liquidations (may not exist for some symbols/layouts)
        liq_uri = layout.liquidations(exchange=args.exchange, symbol=sym, day=day)
        try:
            info = inspect_parquet(liq_uri, fs=fs, time_col="event_time", orderbook_ids=False)
            print(f"liquidations: rows={info.rows} rg={info.row_groups} time=[{info.first_event_time_ms},{info.last_event_time_ms}]")
        except FileNotFoundError as e:
            print(f"liquidations: MISSING {e!r}")
        except Exception as e:
            print(f"liquidations: WARN {e!r}")

        # Orderbook hours
        prev_last_final: int | None = None
        for h in hours:
            ob_uri = layout.orderbook(exchange=args.exchange, symbol=sym, day=day, hour=h)
            try:
                info = inspect_parquet(ob_uri, fs=fs, time_col="event_time", orderbook_ids=True)
                print(
                    f"orderbook_{h:02d}: rows={info.rows} rg={info.row_groups} "
                    f"time=[{info.first_event_time_ms},{info.last_event_time_ms}] "
                    f"first_final={info.first_final_update_id} first_prev={info.first_prev_final_update_id} last_final={info.last_final_update_id}"
                )
                if prev_last_final is not None and info.first_prev_final_update_id is not None:
                    if info.first_prev_final_update_id != prev_last_final:
                        print(
                            f"  WARN continuity: first prev_final_update_id={info.first_prev_final_update_id} "
                            f"!= previous last_final_update_id={prev_last_final}"
                        )
                prev_last_final = info.last_final_update_id
            except FileNotFoundError as e:
                print(f"orderbook_{h:02d}: MISSING {e!r}")
                if not args.skip_missing:
                    failures += 1
                    break
            except Exception as e:
                print(f"orderbook_{h:02d}: ERROR {e!r}")
                failures += 1
                if not args.skip_missing:
                    break

    if failures:
        print(f"\nFAILURES: {failures}")
        return 1

    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
