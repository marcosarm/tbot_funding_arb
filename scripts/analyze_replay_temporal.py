from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from btengine.data.cryptohftdata import CryptoHftDayConfig, CryptoHftLayout, S3Config, build_day_stream, make_s3_filesystem
from btengine.marketdata import L2Book
from btengine.replay import merge_event_streams
from btengine.types import DepthUpdate, Liquidation, MarkPrice, OpenInterest, Ticker, Trade
from btengine.util import load_dotenv


def _parse_day(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _parse_hours(s: str) -> range:
    if "-" in s:
        a, b = s.split("-", 1)
        h0, h1 = int(a), int(b)
        return range(h0, h1 + 1)
    h = int(s)
    return range(h, h + 1)


def _parse_utc_ts(s: str) -> int:
    t = s.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _utc_iso(ms: int | None) -> str:
    if ms is None:
        return "n/a"
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


@dataclass(slots=True)
class RunningFloatStats:
    n: int = 0
    min_v: float = float("inf")
    max_v: float = float("-inf")
    sum_v: float = 0.0

    def add(self, x: float) -> None:
        self.n += 1
        if x < self.min_v:
            self.min_v = x
        if x > self.max_v:
            self.max_v = x
        self.sum_v += x

    def mean(self) -> float | None:
        if self.n <= 0:
            return None
        return self.sum_v / self.n


@dataclass(slots=True)
class TemporalStats:
    count: int = 0
    first_ms: int | None = None
    last_ms: int | None = None
    out_of_order: int = 0
    duplicates: int = 0

    dt_min_ms: int | None = None
    dt_max_ms: int | None = None
    dt_sum_ms: int = 0
    dt_n: int = 0

    latency_ms: RunningFloatStats = field(default_factory=RunningFloatStats)
    outside_window: int = 0

    def add(
        self,
        event_time_ms: int,
        *,
        received_time_ns: int | None = None,
        window_start_ms: int | None = None,
        window_end_ms: int | None = None,
    ) -> None:
        t = int(event_time_ms)

        self.count += 1
        if self.first_ms is None:
            self.first_ms = t
        if self.last_ms is not None:
            if t < self.last_ms:
                self.out_of_order += 1
            if t == self.last_ms:
                self.duplicates += 1
            dt = t - self.last_ms
            self.dt_n += 1
            self.dt_sum_ms += dt
            if self.dt_min_ms is None or dt < self.dt_min_ms:
                self.dt_min_ms = dt
            if self.dt_max_ms is None or dt > self.dt_max_ms:
                self.dt_max_ms = dt
        self.last_ms = t

        if window_start_ms is not None and t < int(window_start_ms):
            self.outside_window += 1
        if window_end_ms is not None and t >= int(window_end_ms):
            self.outside_window += 1

        if received_time_ns is not None:
            recv_ms = float(received_time_ns) / 1_000_000.0
            self.latency_ms.add(recv_ms - float(t))

    def dt_mean_ms(self) -> float | None:
        if self.dt_n <= 0:
            return None
        return float(self.dt_sum_ms) / float(self.dt_n)


@dataclass(slots=True)
class DepthChecks:
    depth_updates: int = 0
    final_id_nonmonotonic: int = 0
    prev_id_mismatch: int = 0
    crossed_book: int = 0
    missing_side: int = 0
    spread: RunningFloatStats = field(default_factory=RunningFloatStats)

    _last_final_id: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _book_checks: int = 0

    def on_update(self, u: DepthUpdate) -> None:
        self.depth_updates += 1
        last_final = self._last_final_id.get(u.symbol)
        if last_final is not None:
            if int(u.final_update_id) < int(last_final):
                self.final_id_nonmonotonic += 1
            if int(u.prev_final_update_id) != int(last_final):
                self.prev_id_mismatch += 1
        self._last_final_id[u.symbol] = int(u.final_update_id)

    def on_book_check(self, book: L2Book) -> None:
        self._book_checks += 1
        bid = book.best_bid()
        ask = book.best_ask()
        if bid is None or ask is None:
            self.missing_side += 1
            return
        if bid > ask:
            self.crossed_book += 1
            return
        self.spread.add(float(ask) - float(bid))


def main() -> int:
    ap = argparse.ArgumentParser(description="Temporal sanity-check of CryptoHFTData replay streams.")
    ap.add_argument("--dotenv", default=str(ROOT / ".env"))
    ap.add_argument("--exchange", default="binance_futures")
    ap.add_argument("--day", required=True, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--symbols", default="BTCUSDT", help="Comma-separated symbols")
    ap.add_argument("--mark-price-symbols", default="BTCUSDT", help="Symbols that should include mark_price stream")
    ap.add_argument("--hours", default="0-23", help="Orderbook hours range (e.g. 0-23 or 12-12)")
    ap.add_argument("--include-ticker", action="store_true")
    ap.add_argument("--include-open-interest", action="store_true")
    ap.add_argument("--include-liquidations", action="store_true")
    ap.add_argument("--open-interest-delay-ms", type=int, default=0, help="Delay (ms) to apply to open_interest availability time.")
    ap.add_argument("--skip-missing", action="store_true")
    ap.add_argument("--start-utc", default=None)
    ap.add_argument("--end-utc", default=None)
    ap.add_argument("--start-ms", type=int, default=None)
    ap.add_argument("--end-ms", type=int, default=None)
    ap.add_argument("--max-events", type=int, default=0, help="0 = no limit")
    ap.add_argument("--book-check-every", type=int, default=500, help="Check bid/ask/spread every N depth updates")
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
    session_token = env.get("AWS_SESSION_TOKEN") or None

    fs = make_s3_filesystem(
        S3Config(region=region, access_key=access_key, secret_key=secret_key, session_token=session_token)
    )
    layout = CryptoHftLayout(bucket=bucket, prefix=prefix)

    d = _parse_day(args.day)
    hours = _parse_hours(args.hours)

    day_start_ms = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
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

    per_symbol: list[Iterable[DepthUpdate | Trade | MarkPrice | Ticker | OpenInterest | Liquidation]] = []
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
        per_symbol.append(build_day_stream(layout, cfg=cfg, symbol=sym, day=d, filesystem=fs))

    events = merge_event_streams(*per_symbol)

    all_stats = TemporalStats()
    by_type: dict[str, TemporalStats] = {k: TemporalStats() for k in ["depth", "trade", "mark", "ticker", "oi", "liq"]}
    depth_checks = DepthChecks()
    trade_time_mismatch = 0

    oi_publish_delay_ms = RunningFloatStats()
    oi_ingest_latency_ms = RunningFloatStats()

    books: dict[str, L2Book] = {}

    def _stype(ev) -> str:
        if isinstance(ev, DepthUpdate):
            return "depth"
        if isinstance(ev, Trade):
            return "trade"
        if isinstance(ev, MarkPrice):
            return "mark"
        if isinstance(ev, Ticker):
            return "ticker"
        if isinstance(ev, OpenInterest):
            return "oi"
        if isinstance(ev, Liquidation):
            return "liq"
        return "unknown"

    max_events = int(args.max_events or 0)
    book_every = int(args.book_check_every or 0)
    if book_every < 0:
        print("ERROR: --book-check-every must be >= 0", file=sys.stderr)
        return 2

    for i, ev in enumerate(events):
        if max_events > 0 and i >= max_events:
            break

        t = int(ev.event_time_ms)
        recv_ns = int(getattr(ev, "received_time_ns", 0) or 0)

        all_stats.add(t, received_time_ns=recv_ns, window_start_ms=window_start_ms, window_end_ms=window_end_ms)

        st = _stype(ev)
        if st in by_type:
            by_type[st].add(t, received_time_ns=recv_ns, window_start_ms=window_start_ms, window_end_ms=window_end_ms)

        if isinstance(ev, DepthUpdate):
            depth_checks.on_update(ev)
            book = books.get(ev.symbol)
            if book is None:
                book = L2Book()
                books[ev.symbol] = book
            book.apply_depth_update(ev.bid_updates, ev.ask_updates)
            if book_every and (depth_checks.depth_updates % book_every == 0):
                depth_checks.on_book_check(book)

        if isinstance(ev, Trade):
            if int(ev.trade_time_ms) != int(ev.event_time_ms):
                trade_time_mismatch += 1
        if isinstance(ev, OpenInterest):
            oi_publish_delay_ms.add(float(int(ev.event_time_ms) - int(ev.timestamp_ms)))
            oi_ingest_latency_ms.add(float(int(ev.received_time_ns) / 1_000_000.0 - int(ev.timestamp_ms)))

    print("\n== Window ==")
    print(f"start_ms: {window_start_ms} ({_utc_iso(window_start_ms)})")
    print(f"end_ms:   {window_end_ms} ({_utc_iso(window_end_ms)})")

    def _print_stats(name: str, s: TemporalStats) -> None:
        if s.count <= 0:
            print(f"{name}: count=0")
            return
        rng_ms = (int(s.last_ms) - int(s.first_ms)) if (s.first_ms is not None and s.last_ms is not None) else 0
        rate = (float(s.count) / (rng_ms / 1000.0)) if rng_ms > 0 else float("nan")
        print(
            f"{name}: count={s.count} "
            f"time=[{_utc_iso(s.first_ms)},{_utc_iso(s.last_ms)}] "
            f"ooo={s.out_of_order} dup={s.duplicates} "
            f"dt_ms(min/mean/max)={s.dt_min_ms}/{s.dt_mean_ms()}/{s.dt_max_ms} "
            f"lat_ms(min/mean/max)={s.latency_ms.min_v if s.latency_ms.n else None}/{s.latency_ms.mean()}/{s.latency_ms.max_v if s.latency_ms.n else None} "
            f"outside_window={s.outside_window} "
            f"rate_ev_s={rate}"
        )

    print("\n== Temporal Summary ==")
    _print_stats("all", all_stats)
    _print_stats("depth", by_type["depth"])
    _print_stats("trade", by_type["trade"])
    _print_stats("mark", by_type["mark"])
    _print_stats("ticker", by_type["ticker"])
    _print_stats("open_interest", by_type["oi"])
    _print_stats("liquidations", by_type["liq"])

    print("\n== Depth Continuity / Book Sanity ==")
    if depth_checks.depth_updates <= 0:
        print("depth_updates: 0")
    else:
        mismatch_rate = depth_checks.prev_id_mismatch / depth_checks.depth_updates
        print(f"depth_updates: {depth_checks.depth_updates}")
        print(f"final_update_id non-monotonic: {depth_checks.final_id_nonmonotonic}")
        print(f"prev_final_update_id mismatches: {depth_checks.prev_id_mismatch} (rate={mismatch_rate:.6f})")
        if depth_checks.spread.n > 0:
            print(
                f"book_checks: {depth_checks._book_checks} crossed={depth_checks.crossed_book} missing_side={depth_checks.missing_side} "
                f"spread(min/mean/max)={depth_checks.spread.min_v}/{depth_checks.spread.mean()}/{depth_checks.spread.max_v}"
            )
        else:
            print(
                f"book_checks: {depth_checks._book_checks} crossed={depth_checks.crossed_book} missing_side={depth_checks.missing_side}"
            )

    if trade_time_mismatch:
        print(f"\ntrade_time mismatches: {trade_time_mismatch}")

    if oi_publish_delay_ms.n:
        print("\n== Open Interest Timing ==")
        print(
            f"publish_delay_ms(min/mean/max)={oi_publish_delay_ms.min_v}/{oi_publish_delay_ms.mean()}/{oi_publish_delay_ms.max_v}"
        )
        print(
            f"ingest_latency_ms(min/mean/max)={oi_ingest_latency_ms.min_v}/{oi_ingest_latency_ms.mean()}/{oi_ingest_latency_ms.max_v}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
