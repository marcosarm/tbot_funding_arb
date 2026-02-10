from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from btengine.data.cryptohftdata import CryptoHftDayConfig, CryptoHftLayout, S3Config, build_day_stream, make_s3_filesystem
from btengine.broker import SimBroker
from btengine.engine import BacktestEngine, EngineConfig
from btengine.replay import merge_event_streams
from btengine.types import DepthUpdate, Liquidation, MarkPrice, OpenInterest, Ticker, Trade
from btengine.util import load_dotenv


def _parse_day(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _parse_utc_ts(s: str) -> int:
    """Parse an ISO timestamp string as UTC and return epoch ms.

    Accepts:
    - 2025-07-01T12:00:00Z
    - 2025-07-01T12:00:00+00:00
    - 2025-07-01T12:00:00  (treated as UTC)
    """

    t = s.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _parse_hours(s: str) -> range:
    if "-" in s:
        a, b = s.split("-", 1)
        h0, h1 = int(a), int(b)
        return range(h0, h1 + 1)
    h = int(s)
    return range(h, h + 1)


def _limit_events(
    events: Iterable[DepthUpdate | Trade | MarkPrice | Ticker | OpenInterest | Liquidation],
    *,
    max_events: int,
) -> Iterator[DepthUpdate | Trade | MarkPrice | Ticker | OpenInterest | Liquidation]:
    if max_events <= 0:
        yield from events
        return
    for i, ev in enumerate(events):
        if i >= max_events:
            break
        yield ev


def _count_events(
    events: Iterable[DepthUpdate | Trade | MarkPrice | Ticker | OpenInterest | Liquidation],
    *,
    counters: "Counters",
) -> Iterator[DepthUpdate | Trade | MarkPrice | Ticker | OpenInterest | Liquidation]:
    for ev in events:
        if isinstance(ev, DepthUpdate):
            counters.depth += 1
        elif isinstance(ev, Trade):
            counters.trades += 1
        elif isinstance(ev, MarkPrice):
            counters.mark += 1
        elif isinstance(ev, Ticker):
            counters.ticker += 1
        elif isinstance(ev, OpenInterest):
            counters.open_interest += 1
        elif isinstance(ev, Liquidation):
            counters.liquidations += 1
        yield ev


@dataclass(slots=True)
class Counters:
    depth: int = 0
    trades: int = 0
    mark: int = 0
    ticker: int = 0
    open_interest: int = 0
    liquidations: int = 0


class NoopStrategy:
    pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay CryptoHFTData (S3) through btengine with a No-op strategy.")
    ap.add_argument("--dotenv", default=str(ROOT / ".env"))
    ap.add_argument("--exchange", default="binance_futures")
    ap.add_argument("--day", required=True, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--symbols", default="BTCUSDT", help="Comma-separated symbols")
    ap.add_argument("--mark-price-symbols", default="BTCUSDT", help="Symbols that should include mark_price stream")
    ap.add_argument("--hours", default="0-23", help="Orderbook hours range (e.g. 0-23 or 12-12)")
    ap.add_argument("--include-ticker", action="store_true", help="Include ticker.parquet stream (if available).")
    ap.add_argument("--include-open-interest", action="store_true", help="Include open_interest.parquet stream (if available).")
    ap.add_argument("--include-liquidations", action="store_true", help="Include liquidations.parquet stream (if available).")
    ap.add_argument("--open-interest-delay-ms", type=int, default=0, help="Delay (ms) to make open_interest snapshots available after their timestamp (anti-lookahead).")
    ap.add_argument("--skip-missing", action="store_true", help="Skip missing daily files (trades/mark/ticker/oi/liquidations).")
    ap.add_argument("--start-utc", default=None, help="Optional ISO timestamp (UTC) to slice streams (e.g. 2025-07-01T12:00:00Z)")
    ap.add_argument("--end-utc", default=None, help="Optional ISO timestamp (UTC) to slice streams (exclusive end)")
    ap.add_argument("--start-ms", type=int, default=None, help="Optional epoch ms start (overrides --start-utc)")
    ap.add_argument("--end-ms", type=int, default=None, help="Optional epoch ms end (exclusive; overrides --end-utc)")
    ap.add_argument("--max-events", type=int, default=200_000, help="Stop after N merged events (0 = no limit)")
    ap.add_argument("--tick-ms", type=int, default=1000, help="Strategy tick interval in ms (0 disables ticks)")
    ap.add_argument("--maker-fee-frac", type=float, default=0.0004, help="Maker fee fraction (e.g. 0.0004 = 4 bps).")
    ap.add_argument("--taker-fee-frac", type=float, default=0.0005, help="Taker fee fraction (e.g. 0.0005 = 5 bps).")
    ap.add_argument("--submit-latency-ms", type=int, default=0, help="Order submit latency in ms (activation delay).")
    ap.add_argument("--cancel-latency-ms", type=int, default=0, help="Cancel latency in ms.")
    ap.add_argument("--maker-queue-ahead-factor", type=float, default=1.0, help="Multiply visible queue ahead by this factor.")
    ap.add_argument("--maker-queue-ahead-extra-qty", type=float, default=0.0, help="Add extra base qty ahead (conservative).")
    ap.add_argument("--maker-trade-participation", type=float, default=1.0, help="Trade participation factor in (0,1].")
    args = ap.parse_args()

    env = load_dotenv(args.dotenv, override=False).values
    bucket = env.get("S3_BUCKET")
    prefix = env.get("S3_PREFIX")
    if not bucket or not prefix:
        print("ERROR: missing S3_BUCKET or S3_PREFIX in .env", file=sys.stderr)
        return 2

    region = env.get("AWS_REGION") or None
    access_key = env.get("AWS_ACCESS_KEY_ID") or None
    secret_key = env.get("AWS_SECRET_ACCESS_KEY") or None

    fs = make_s3_filesystem(S3Config(region=region, access_key=access_key, secret_key=secret_key))
    layout = CryptoHftLayout(bucket=bucket, prefix=prefix)

    day = _parse_day(args.day)
    hours = _parse_hours(args.hours)

    # Default window derived from requested orderbook hours.
    day_start_ms = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)
    window_start_ms = day_start_ms + int(hours.start) * 3_600_000
    window_end_ms = day_start_ms + int(hours.stop) * 3_600_000

    if args.start_utc:
        window_start_ms = _parse_utc_ts(args.start_utc)
    if args.end_utc:
        window_end_ms = _parse_utc_ts(args.end_utc)

    if args.start_ms is not None:
        window_start_ms = int(args.start_ms)
    if args.end_ms is not None:
        window_end_ms = int(args.end_ms)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    mp_symbols = {s.strip() for s in args.mark_price_symbols.split(",") if s.strip()}

    per_symbol_streams: list[Iterable[DepthUpdate | Trade | MarkPrice | Ticker | OpenInterest | Liquidation]] = []
    for sym in symbols:
        cfg = CryptoHftDayConfig(
            exchange=args.exchange,
            include_trades=True,
            include_orderbook=True,
            include_mark_price=(sym in mp_symbols),
            include_ticker=args.include_ticker,
            include_open_interest=args.include_open_interest,
            include_liquidations=args.include_liquidations,
            open_interest_delay_ms=int(args.open_interest_delay_ms or 0),
            orderbook_hours=hours,
            orderbook_skip_missing=True,
            skip_missing_daily_files=args.skip_missing,
            stream_start_ms=window_start_ms,
            stream_end_ms=window_end_ms,
        )
        per_symbol_streams.append(build_day_stream(layout, cfg=cfg, symbol=sym, day=day, filesystem=fs))

    events = merge_event_streams(*per_symbol_streams)
    counters = Counters()
    events = _count_events(events, counters=counters)
    events = _limit_events(events, max_events=args.max_events)

    broker = SimBroker(
        maker_fee_frac=float(args.maker_fee_frac),
        taker_fee_frac=float(args.taker_fee_frac),
        submit_latency_ms=int(args.submit_latency_ms),
        cancel_latency_ms=int(args.cancel_latency_ms),
        maker_queue_ahead_factor=float(args.maker_queue_ahead_factor),
        maker_queue_ahead_extra_qty=float(args.maker_queue_ahead_extra_qty),
        maker_trade_participation=float(args.maker_trade_participation),
    )
    engine = BacktestEngine(config=EngineConfig(tick_interval_ms=args.tick_ms), broker=broker)
    res = engine.run(events, strategy=NoopStrategy())

    # Summaries
    ctx = res.ctx
    print("\n== Summary ==")
    print(f"window_ms: [{window_start_ms},{window_end_ms})")
    print(f"symbols: {', '.join(sorted(ctx.books.keys()))}")
    print(
        "events: "
        f"depth={counters.depth} trades={counters.trades} mark={counters.mark} "
        f"ticker={counters.ticker} oi={counters.open_interest} liq={counters.liquidations}"
    )
    print(f"fills: {len(ctx.broker.fills)}")
    print(f"realized_pnl_usdt: {ctx.broker.portfolio.realized_pnl_usdt:.6f}")
    print(f"fees_paid_usdt: {ctx.broker.portfolio.fees_paid_usdt:.6f}")
    for sym, book in ctx.books.items():
        print(f"{sym}: best_bid={book.best_bid()} best_ask={book.best_ask()} mid={book.mid_price()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
