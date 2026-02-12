from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from btengine.analytics import max_drawdown, round_trips_from_fills, summarize_round_trips
from btengine.broker import SimBroker
from btengine.data.cryptohftdata import CryptoHftDayConfig, CryptoHftLayout, S3Config, build_day_stream, make_s3_filesystem
from btengine.engine import BacktestEngine, EngineConfig, EngineContext
from btengine.marketdata import L2Book
from btengine.types import DepthUpdate, Liquidation, MarkPrice, OpenInterest, Ticker, Trade
from btengine.util import load_dotenv

# Reuse strategies defined in other scripts (keeps this file small and consistent).
from run_backtest_entry_exit import EntryExitStrategy  # type: ignore
from run_backtest_ma_cross import MaCrossStrategy  # type: ignore


def _parse_day(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _parse_hours(s: str) -> range:
    if "-" in s:
        a, b = s.split("-", 1)
        h0, h1 = int(a), int(b)
        return range(h0, h1 + 1)
    h = int(s)
    return range(h, h + 1)


def _utc_iso(ms: int | None) -> str:
    if ms is None:
        return "n/a"
    return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc).isoformat()


@dataclass(slots=True)
class TemporalStats:
    count: int = 0
    first_ms: int | None = None
    last_ms: int | None = None
    out_of_order: int = 0
    duplicates: int = 0
    outside_window: int = 0

    def add(self, t_ms: int, *, window_start_ms: int, window_end_ms: int) -> None:
        t = int(t_ms)
        self.count += 1
        if self.first_ms is None:
            self.first_ms = t
        if self.last_ms is not None:
            if t < self.last_ms:
                self.out_of_order += 1
            if t == self.last_ms:
                self.duplicates += 1
        self.last_ms = t

        if t < int(window_start_ms) or t >= int(window_end_ms):
            self.outside_window += 1


@dataclass(slots=True)
class DepthContinuityStats:
    depth_updates: int = 0
    final_id_nonmonotonic: int = 0
    prev_id_mismatch: int = 0
    _last_final: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._last_final = {}

    def on_update(self, u: DepthUpdate) -> None:
        self.depth_updates += 1
        last = self._last_final.get(u.symbol)
        if last is not None:
            if int(u.final_update_id) < int(last):
                self.final_id_nonmonotonic += 1
            if int(u.prev_final_update_id) != int(last):
                self.prev_id_mismatch += 1
        self._last_final[u.symbol] = int(u.final_update_id)


@dataclass(slots=True)
class BookSanityStats:
    checks: int = 0
    crossed_book: int = 0
    missing_side: int = 0
    spread_n: int = 0
    spread_min: float = float("inf")
    spread_max: float = float("-inf")
    spread_sum: float = 0.0

    def on_check(self, book) -> None:
        self.checks += 1
        bid = book.best_bid()
        ask = book.best_ask()
        if bid is None or ask is None:
            self.missing_side += 1
            return
        if float(bid) > float(ask):
            self.crossed_book += 1
            return
        spr = float(ask) - float(bid)
        self.spread_n += 1
        self.spread_min = min(self.spread_min, spr)
        self.spread_max = max(self.spread_max, spr)
        self.spread_sum += spr

    def spread_mean(self) -> float | None:
        if self.spread_n <= 0:
            return None
        return self.spread_sum / float(self.spread_n)


@dataclass(slots=True)
class EventCounters:
    depth: int = 0
    trades: int = 0
    mark: int = 0
    ticker: int = 0
    open_interest: int = 0
    liquidations: int = 0


def _reset_book_in_place(book: L2Book) -> None:
    # Clear best bid/ask heaps too; otherwise best_* may pop many stale entries.
    book.bids.clear()
    book.asks.clear()
    book._bid_heap.clear()  # type: ignore[attr-defined]
    book._ask_heap.clear()  # type: ignore[attr-defined]


@dataclass(slots=True)
class BookGuardConfig:
    enabled: bool = False
    max_spread: float | None = None
    max_spread_bps: float | None = 5.0
    cooldown_ms: int = 1_000
    warmup_depth_updates: int = 1_000
    max_staleness_ms: int = 2_000
    reset_on_mismatch: bool = True
    reset_on_crossed: bool = True
    reset_on_missing_side: bool = False
    reset_on_spread: bool = False
    reset_on_stale: bool = False


@dataclass(slots=True)
class BookGuardStats:
    resets: int = 0
    mismatch_trips: int = 0
    cross_trips: int = 0
    missing_side_trips: int = 0
    spread_trips: int = 0
    stale_trips: int = 0

    blocked_submits: int = 0
    blocked_submit_reason: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.blocked_submit_reason = {}


class BookGuardedBroker:
    """SimBroker wrapper that blocks order submits when the book is invalid.

    It also detects orderbook continuity breaks (prev_final_update_id mismatch)
    and can reset the in-memory L2Book + enforce a warmup period.
    """

    def __init__(self, inner: SimBroker, *, symbol: str, cfg: BookGuardConfig) -> None:
        self.inner = inner
        self.symbol = str(symbol)
        self.cfg = cfg
        self.stats = BookGuardStats()

        self._blocked_until_ms: int = 0
        self._warmup_remaining: int = 0
        self._last_final_update_id: int | None = None
        self._last_depth_event_ms: int | None = None

    @property
    def portfolio(self):
        return self.inner.portfolio

    @property
    def fills(self):
        return self.inner.fills

    def has_open_orders(self) -> bool:
        return self.inner.has_open_orders()

    def on_time(self, now_ms: int) -> None:
        return self.inner.on_time(now_ms)

    def cancel(self, order_id: str, *, now_ms: int | None = None) -> None:
        return self.inner.cancel(order_id, now_ms=now_ms)

    def on_trade(self, trade: Trade, now_ms: int) -> None:
        return self.inner.on_trade(trade, now_ms=now_ms)

    def _trip(self, book: L2Book, *, now_ms: int, reason: str) -> None:
        if int(self.cfg.cooldown_ms or 0) > 0:
            self._blocked_until_ms = max(self._blocked_until_ms, int(now_ms) + int(self.cfg.cooldown_ms))
        if int(self.cfg.warmup_depth_updates or 0) > 0:
            self._warmup_remaining = max(self._warmup_remaining, int(self.cfg.warmup_depth_updates))

        reset = False
        if reason == "mismatch":
            reset = bool(self.cfg.reset_on_mismatch)
        elif reason == "crossed":
            reset = bool(self.cfg.reset_on_crossed)
        elif reason == "missing_side":
            reset = bool(self.cfg.reset_on_missing_side)
        elif reason == "spread":
            reset = bool(self.cfg.reset_on_spread)
        elif reason == "stale":
            reset = bool(self.cfg.reset_on_stale)

        if reset:
            _reset_book_in_place(book)
            # Also clear maker orders, which depend on queue state at a specific price level.
            # This is conservative and avoids carrying state across a book reset.
            try:
                self.inner._maker_orders.clear()  # type: ignore[attr-defined]
            except Exception:
                pass
            self.stats.resets += 1

    def on_depth_update(self, update: DepthUpdate, book: L2Book) -> None:
        if self.cfg.enabled and update.symbol == self.symbol:
            self._last_depth_event_ms = int(update.event_time_ms)
            if self._warmup_remaining > 0:
                self._warmup_remaining -= 1

            # Detect continuity mismatch before applying the delta.
            if self._last_final_update_id is not None and int(update.prev_final_update_id) != int(self._last_final_update_id):
                self.stats.mismatch_trips += 1
                self._trip(book, now_ms=int(update.event_time_ms), reason="mismatch")

            self._last_final_update_id = int(update.final_update_id)

        return self.inner.on_depth_update(update, book)

    def submit(self, order, book: L2Book, now_ms: int) -> None:
        if not self.cfg.enabled or order.symbol != self.symbol:
            return self.inner.submit(order, book, now_ms)

        now = int(now_ms)
        if now < int(self._blocked_until_ms):
            self.stats.blocked_submits += 1
            self.stats.blocked_submit_reason["cooldown"] = self.stats.blocked_submit_reason.get("cooldown", 0) + 1
            return
        if int(self._warmup_remaining) > 0:
            self.stats.blocked_submits += 1
            self.stats.blocked_submit_reason["warmup"] = self.stats.blocked_submit_reason.get("warmup", 0) + 1
            return

        if int(self.cfg.max_staleness_ms or 0) > 0:
            last_depth_ms = self._last_depth_event_ms
            if last_depth_ms is None or (now - int(last_depth_ms)) > int(self.cfg.max_staleness_ms):
                self.stats.blocked_submits += 1
                self.stats.blocked_submit_reason["stale"] = self.stats.blocked_submit_reason.get("stale", 0) + 1
                self.stats.stale_trips += 1
                self._trip(book, now_ms=now, reason="stale")
                return

        bid = book.best_bid()
        ask = book.best_ask()
        if bid is None or ask is None:
            self.stats.blocked_submits += 1
            self.stats.blocked_submit_reason["missing_side"] = self.stats.blocked_submit_reason.get("missing_side", 0) + 1
            self.stats.missing_side_trips += 1
            self._trip(book, now_ms=now, reason="missing_side")
            return

        if float(bid) >= float(ask):
            self.stats.blocked_submits += 1
            self.stats.blocked_submit_reason["crossed"] = self.stats.blocked_submit_reason.get("crossed", 0) + 1
            self.stats.cross_trips += 1
            self._trip(book, now_ms=now, reason="crossed")
            return

        spread = float(ask) - float(bid)
        if self.cfg.max_spread is not None and spread > float(self.cfg.max_spread):
            self.stats.blocked_submits += 1
            self.stats.blocked_submit_reason["spread"] = self.stats.blocked_submit_reason.get("spread", 0) + 1
            self.stats.spread_trips += 1
            self._trip(book, now_ms=now, reason="spread")
            return

        if self.cfg.max_spread_bps is not None:
            mid = (float(ask) + float(bid)) / 2.0
            if mid > 0.0:
                spread_bps = (spread / mid) * 10_000.0
                if spread_bps > float(self.cfg.max_spread_bps):
                    self.stats.blocked_submits += 1
                    self.stats.blocked_submit_reason["spread"] = self.stats.blocked_submit_reason.get("spread", 0) + 1
                    self.stats.spread_trips += 1
                    self._trip(book, now_ms=now, reason="spread")
                    return

        return self.inner.submit(order, book, now_ms)

    def __getattr__(self, name: str):
        # Delegate any other methods/attrs to the underlying SimBroker.
        return getattr(self.inner, name)


class ValidatingStrategy:
    def __init__(
        self,
        base: Any,
        *,
        symbol: str,
        window_start_ms: int,
        window_end_ms: int,
        book_check_every: int = 5000,
    ) -> None:
        self.base = base
        self.symbol = str(symbol)
        self.window_start_ms = int(window_start_ms)
        self.window_end_ms = int(window_end_ms)
        self.book_check_every = int(book_check_every)

        self.temporal = TemporalStats()
        self.depth = DepthContinuityStats()
        self.book = BookSanityStats()
        self.counts = EventCounters()

    def on_start(self, ctx: EngineContext) -> None:
        fn = getattr(self.base, "on_start", None)
        if callable(fn):
            fn(ctx)

    def on_tick(self, now_ms: int, ctx: EngineContext) -> None:
        fn = getattr(self.base, "on_tick", None)
        if callable(fn):
            fn(now_ms, ctx)

    def on_event(self, event: object, ctx: EngineContext) -> None:
        # Temporal checks (after engine updated broker/books, but timestamp is same).
        t_ms = int(getattr(event, "event_time_ms", 0))
        self.temporal.add(t_ms, window_start_ms=self.window_start_ms, window_end_ms=self.window_end_ms)

        if isinstance(event, DepthUpdate):
            self.counts.depth += 1
            self.depth.on_update(event)
            if self.book_check_every > 0 and (self.counts.depth % self.book_check_every == 0):
                b = ctx.books.get(self.symbol)
                if b is not None:
                    self.book.on_check(b)
        elif isinstance(event, Trade):
            self.counts.trades += 1
        elif isinstance(event, MarkPrice):
            self.counts.mark += 1
        elif isinstance(event, Ticker):
            self.counts.ticker += 1
        elif isinstance(event, OpenInterest):
            self.counts.open_interest += 1
        elif isinstance(event, Liquidation):
            self.counts.liquidations += 1

        fn = getattr(self.base, "on_event", None)
        if callable(fn):
            fn(event, ctx)

    def on_end(self, ctx: EngineContext) -> None:
        fn = getattr(self.base, "on_end", None)
        if callable(fn):
            fn(ctx)


def _limit_events(events: Iterable[Any], *, max_events: int) -> Iterable[Any]:
    if max_events <= 0:
        return events

    def _gen():
        for i, ev in enumerate(events):
            if i >= max_events:
                break
            yield ev

    return _gen()


def _schedule_entry_exit(
    *,
    window_start_ms: int,
    window_end_ms: int,
    enter_offset_s: int,
    hold_s: int,
    gap_s: int,
    cycles: int,
) -> list[tuple[int, int]]:
    schedule: list[tuple[int, int]] = []
    step_ms = (int(hold_s) + int(gap_s)) * 1000
    for i in range(int(cycles)):
        enter_ms = int(window_start_ms) + int(enter_offset_s) * 1000 + i * step_ms
        exit_ms = int(enter_ms) + int(hold_s) * 1000
        if exit_ms >= int(window_end_ms):
            break
        schedule.append((int(enter_ms), int(exit_ms)))
    return schedule


def main() -> int:
    ap = argparse.ArgumentParser(description="Run a backtest setup in batch over multiple days (and validate data).")
    ap.add_argument("--dotenv", default=str(ROOT / ".env"))
    ap.add_argument("--exchange", default="binance_futures")
    ap.add_argument("--symbol", default="BTCUSDT")

    ap.add_argument("--start-day", required=True, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--days", type=int, default=15)
    ap.add_argument("--hours", default="12-13")

    ap.add_argument("--setup", choices=["entry_exit", "ma_cross"], default="ma_cross")
    ap.add_argument("--out-csv", default="batch_results.csv")

    # Common knobs.
    ap.add_argument("--tick-ms", type=int, default=0)
    ap.add_argument("--max-events", type=int, default=0)
    ap.add_argument("--book-check-every", type=int, default=5000)

    ap.add_argument("--strict-book", action="store_true", help="Guard submits when the book is invalid; reset/warmup on mismatches/crossed book.")
    ap.add_argument("--strict-book-max-spread", type=float, default=None, help="Optional max spread (abs) to allow trading.")
    ap.add_argument("--strict-book-max-spread-bps", type=float, default=5.0, help="Optional max spread in bps to allow trading (default: 5 bps).")
    ap.add_argument("--strict-book-max-staleness-ms", type=int, default=2_000, help="Block submits when latest depth update is older than N ms.")
    ap.add_argument("--strict-book-cooldown-ms", type=int, default=1_000, help="Block submits for N ms after a guard trip.")
    ap.add_argument("--strict-book-warmup-depth-updates", type=int, default=1_000, help="Block submits for N depth updates after a guard trip.")
    ap.add_argument("--strict-book-reset-on-mismatch", action="store_true", default=True, help="Reset L2Book on prev_final_update_id mismatches.")
    ap.add_argument("--strict-book-no-reset-on-mismatch", dest="strict_book_reset_on_mismatch", action="store_false")
    ap.add_argument("--strict-book-reset-on-crossed", action="store_true", default=True, help="Reset L2Book when the book is crossed (bid>=ask).")
    ap.add_argument("--strict-book-no-reset-on-crossed", dest="strict_book_reset_on_crossed", action="store_false")
    ap.add_argument("--strict-book-reset-on-missing-side", action="store_true", default=False, help="Reset L2Book when one side is missing at submit time.")
    ap.add_argument("--strict-book-reset-on-spread", action="store_true", default=False, help="Reset L2Book when spread guard trips.")
    ap.add_argument("--strict-book-reset-on-stale", action="store_true", default=False, help="Reset L2Book when staleness guard trips.")

    ap.add_argument("--maker-fee-frac", type=float, default=0.0004)
    ap.add_argument("--taker-fee-frac", type=float, default=0.0005)
    ap.add_argument("--submit-latency-ms", type=int, default=0)
    ap.add_argument("--cancel-latency-ms", type=int, default=0)

    # Extra streams (validation).
    ap.add_argument("--include-ticker", action="store_true")
    ap.add_argument("--include-open-interest", action="store_true")
    ap.add_argument("--include-liquidations", action="store_true")
    ap.add_argument("--open-interest-delay-ms", type=int, default=0)
    ap.add_argument("--skip-missing", action="store_true")

    # entry_exit params.
    ap.add_argument("--direction", choices=["long", "short"], default="long")
    ap.add_argument("--enter-offset-s", type=int, default=30)
    ap.add_argument("--hold-s", type=int, default=60)
    ap.add_argument("--gap-s", type=int, default=60)
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--qty", type=float, default=0.001)

    # ma_cross params.
    ap.add_argument("--price-source", choices=["mark", "trade"], default="mark")
    ap.add_argument("--tf-min", type=int, default=5)
    ap.add_argument("--ma-len", type=int, default=9)
    ap.add_argument("--rule", choices=["cross", "state"], default="cross")
    ap.add_argument("--mode", choices=["long_short", "long_only"], default="long_short")
    ap.add_argument("--fill-missing-bars", action="store_true")
    args = ap.parse_args()

    if args.days <= 0:
        print("ERROR: --days must be >= 1", file=sys.stderr)
        return 2
    if args.qty <= 0:
        print("ERROR: --qty must be > 0", file=sys.stderr)
        return 2

    start_day = _parse_day(args.start_day)
    hours = _parse_hours(args.hours)

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

    # CSV output.
    out_path = Path(str(args.out_csv))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fields = [
        "day",
        "status",
        "runtime_s",
        "events",
        "depth",
        "trades",
        "mark",
        "ticker",
        "open_interest",
        "liquidations",
        "out_of_order",
        "duplicates",
        "outside_window",
        "final_id_nonmonotonic",
        "prev_id_mismatch",
        "book_checks",
        "book_crossed",
        "book_missing_side",
        "spread_min",
        "spread_mean",
        "spread_max",
        "strict_book",
        "strict_guard_resets",
        "strict_guard_mismatch_trips",
        "strict_guard_cross_trips",
        "strict_guard_missing_side_trips",
        "strict_guard_spread_trips",
        "strict_guard_stale_trips",
        "strict_blocked_submits",
        "strict_blocked_submit_reason",
        "fills",
        "round_trips",
        "win_rate",
        "net_pnl_usdt",
        "gross_pnl_usdt",
        "fees_usdt",
        "realized_pnl_usdt",
        "fees_paid_usdt",
        "max_drawdown_usdt",
        "eq_min",
        "eq_max",
        "error",
    ]

    rows: list[dict[str, Any]] = []

    ok = missing = errors = 0

    for i in range(int(args.days)):
        d = start_day + timedelta(days=i)
        day_str = d.isoformat()

        day_start_ms = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
        window_start_ms = day_start_ms + int(hours.start) * 3_600_000
        window_end_ms = day_start_ms + int(hours.stop) * 3_600_000

        t0 = time.perf_counter()
        status = "OK"
        err = ""

        try:
            include_trades = True
            if args.setup == "ma_cross" and args.price_source == "mark":
                # Trades aren't required for signals, but keeping it True validates
                # the stream too (more coverage).
                include_trades = True

            cfg = CryptoHftDayConfig(
                exchange=str(args.exchange),
                include_trades=bool(include_trades),
                include_orderbook=True,
                include_mark_price=True,
                include_ticker=bool(args.include_ticker),
                include_open_interest=bool(args.include_open_interest),
                include_liquidations=bool(args.include_liquidations),
                open_interest_delay_ms=int(args.open_interest_delay_ms or 0),
                orderbook_hours=hours,
                orderbook_skip_missing=False,  # fail-fast for missing hours (validation)
                skip_missing_daily_files=bool(args.skip_missing),
                stream_start_ms=window_start_ms,
                stream_end_ms=window_end_ms,
            )

            events = build_day_stream(layout, cfg=cfg, symbol=str(args.symbol), day=d, filesystem=fs)
            events = _limit_events(events, max_events=int(args.max_events or 0))

            broker = SimBroker(
                maker_fee_frac=float(args.maker_fee_frac),
                taker_fee_frac=float(args.taker_fee_frac),
                submit_latency_ms=int(args.submit_latency_ms),
                cancel_latency_ms=int(args.cancel_latency_ms),
            )
            if args.strict_book:
                broker = BookGuardedBroker(
                    broker,
                    symbol=str(args.symbol),
                    cfg=BookGuardConfig(
                        enabled=True,
                        max_spread=args.strict_book_max_spread,
                        max_spread_bps=args.strict_book_max_spread_bps,
                        max_staleness_ms=int(args.strict_book_max_staleness_ms or 0),
                        cooldown_ms=int(args.strict_book_cooldown_ms or 0),
                        warmup_depth_updates=int(args.strict_book_warmup_depth_updates or 0),
                        reset_on_mismatch=bool(args.strict_book_reset_on_mismatch),
                        reset_on_crossed=bool(args.strict_book_reset_on_crossed),
                        reset_on_missing_side=bool(args.strict_book_reset_on_missing_side),
                        reset_on_spread=bool(args.strict_book_reset_on_spread),
                        reset_on_stale=bool(args.strict_book_reset_on_stale),
                    ),
                )
            engine = BacktestEngine(config=EngineConfig(tick_interval_ms=int(args.tick_ms)), broker=broker)

            if args.setup == "entry_exit":
                schedule = _schedule_entry_exit(
                    window_start_ms=window_start_ms,
                    window_end_ms=window_end_ms,
                    enter_offset_s=int(args.enter_offset_s),
                    hold_s=int(args.hold_s),
                    gap_s=int(args.gap_s),
                    cycles=int(args.cycles),
                )
                if not schedule:
                    raise ValueError("empty entry/exit schedule (window too small or invalid parameters)")
                base = EntryExitStrategy(
                    symbol=str(args.symbol),
                    direction=str(args.direction),  # type: ignore[arg-type]
                    target_qty=float(args.qty),
                    schedule_ms=schedule,
                    force_close_on_end=True,
                )
            else:
                base = MaCrossStrategy(
                    symbol=str(args.symbol),
                    qty=float(args.qty),
                    tf_ms=int(args.tf_min) * 60_000,
                    ma_len=int(args.ma_len),
                    rule=str(args.rule),  # type: ignore[arg-type]
                    mode=str(args.mode),  # type: ignore[arg-type]
                    price_source=str(args.price_source),  # type: ignore[arg-type]
                    fill_missing_bars=bool(args.fill_missing_bars),
                )

            strat = ValidatingStrategy(
                base,
                symbol=str(args.symbol),
                window_start_ms=window_start_ms,
                window_end_ms=window_end_ms,
                book_check_every=int(args.book_check_every),
            )

            res = engine.run(events, strategy=strat)

            fills = res.ctx.broker.fills
            rts = round_trips_from_fills(fills)
            rt_summary = summarize_round_trips(rts)

            equity_curve = getattr(base, "equity_curve", [])
            mdd = max_drawdown(equity_curve)
            eq_min = min((x for _, x in equity_curve), default=None)
            eq_max = max((x for _, x in equity_curve), default=None)

            guard_stats = getattr(res.ctx.broker, "stats", None)
            if guard_stats is None:
                guard_stats = BookGuardStats()

            row = {
                "day": day_str,
                "status": status,
                "runtime_s": round(time.perf_counter() - t0, 3),
                "events": int(strat.temporal.count),
                "depth": int(strat.counts.depth),
                "trades": int(strat.counts.trades),
                "mark": int(strat.counts.mark),
                "ticker": int(strat.counts.ticker),
                "open_interest": int(strat.counts.open_interest),
                "liquidations": int(strat.counts.liquidations),
                "out_of_order": int(strat.temporal.out_of_order),
                "duplicates": int(strat.temporal.duplicates),
                "outside_window": int(strat.temporal.outside_window),
                "final_id_nonmonotonic": int(strat.depth.final_id_nonmonotonic),
                "prev_id_mismatch": int(strat.depth.prev_id_mismatch),
                "book_checks": int(strat.book.checks),
                "book_crossed": int(strat.book.crossed_book),
                "book_missing_side": int(strat.book.missing_side),
                "spread_min": (None if strat.book.spread_n <= 0 else float(strat.book.spread_min)),
                "spread_mean": strat.book.spread_mean(),
                "spread_max": (None if strat.book.spread_n <= 0 else float(strat.book.spread_max)),
                "strict_book": bool(args.strict_book),
                "strict_guard_resets": int(getattr(guard_stats, "resets", 0)),
                "strict_guard_mismatch_trips": int(getattr(guard_stats, "mismatch_trips", 0)),
                "strict_guard_cross_trips": int(getattr(guard_stats, "cross_trips", 0)),
                "strict_guard_missing_side_trips": int(getattr(guard_stats, "missing_side_trips", 0)),
                "strict_guard_spread_trips": int(getattr(guard_stats, "spread_trips", 0)),
                "strict_guard_stale_trips": int(getattr(guard_stats, "stale_trips", 0)),
                "strict_blocked_submits": int(getattr(guard_stats, "blocked_submits", 0)),
                "strict_blocked_submit_reason": str(dict(sorted(getattr(guard_stats, "blocked_submit_reason", {}).items()))),
                "fills": len(fills),
                "round_trips": int(rt_summary.trades),
                "win_rate": rt_summary.win_rate,
                "net_pnl_usdt": float(rt_summary.net_pnl_usdt),
                "gross_pnl_usdt": float(rt_summary.gross_pnl_usdt),
                "fees_usdt": float(rt_summary.fees_usdt),
                "realized_pnl_usdt": float(res.ctx.broker.portfolio.realized_pnl_usdt),
                "fees_paid_usdt": float(res.ctx.broker.portfolio.fees_paid_usdt),
                "max_drawdown_usdt": mdd,
                "eq_min": eq_min,
                "eq_max": eq_max,
                "error": "",
            }
            rows.append(row)
            ok += 1
        except FileNotFoundError as e:
            status = "MISSING"
            err = repr(e)
            missing += 1
            rows.append(
                {
                    "day": day_str,
                    "status": status,
                    "runtime_s": round(time.perf_counter() - t0, 3),
                    "events": 0,
                    "depth": 0,
                    "trades": 0,
                    "mark": 0,
                    "ticker": 0,
                    "open_interest": 0,
                    "liquidations": 0,
                    "out_of_order": 0,
                    "duplicates": 0,
                    "outside_window": 0,
                    "final_id_nonmonotonic": 0,
                    "prev_id_mismatch": 0,
                    "book_checks": 0,
                    "book_crossed": 0,
                    "book_missing_side": 0,
                    "spread_min": None,
                    "spread_mean": None,
                    "spread_max": None,
                    "strict_book": bool(args.strict_book),
                    "strict_guard_resets": 0,
                    "strict_guard_mismatch_trips": 0,
                    "strict_guard_cross_trips": 0,
                    "strict_guard_missing_side_trips": 0,
                    "strict_guard_spread_trips": 0,
                    "strict_guard_stale_trips": 0,
                    "strict_blocked_submits": 0,
                    "strict_blocked_submit_reason": "",
                    "fills": 0,
                    "round_trips": 0,
                    "win_rate": None,
                    "net_pnl_usdt": 0.0,
                    "gross_pnl_usdt": 0.0,
                    "fees_usdt": 0.0,
                    "realized_pnl_usdt": 0.0,
                    "fees_paid_usdt": 0.0,
                    "max_drawdown_usdt": None,
                    "eq_min": None,
                    "eq_max": None,
                    "error": err,
                }
            )
        except Exception as e:
            status = "ERROR"
            err = repr(e)
            errors += 1
            rows.append(
                {
                    "day": day_str,
                    "status": status,
                    "runtime_s": round(time.perf_counter() - t0, 3),
                    "events": 0,
                    "depth": 0,
                    "trades": 0,
                    "mark": 0,
                    "ticker": 0,
                    "open_interest": 0,
                    "liquidations": 0,
                    "out_of_order": 0,
                    "duplicates": 0,
                    "outside_window": 0,
                    "final_id_nonmonotonic": 0,
                    "prev_id_mismatch": 0,
                    "book_checks": 0,
                    "book_crossed": 0,
                    "book_missing_side": 0,
                    "spread_min": None,
                    "spread_mean": None,
                    "spread_max": None,
                    "strict_book": bool(args.strict_book),
                    "strict_guard_resets": 0,
                    "strict_guard_mismatch_trips": 0,
                    "strict_guard_cross_trips": 0,
                    "strict_guard_missing_side_trips": 0,
                    "strict_guard_spread_trips": 0,
                    "strict_guard_stale_trips": 0,
                    "strict_blocked_submits": 0,
                    "strict_blocked_submit_reason": "",
                    "fills": 0,
                    "round_trips": 0,
                    "win_rate": None,
                    "net_pnl_usdt": 0.0,
                    "gross_pnl_usdt": 0.0,
                    "fees_usdt": 0.0,
                    "realized_pnl_usdt": 0.0,
                    "fees_paid_usdt": 0.0,
                    "max_drawdown_usdt": None,
                    "eq_min": None,
                    "eq_max": None,
                    "error": err,
                }
            )

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print("\n== Batch Summary ==")
    print(f"setup: {args.setup} symbol: {args.symbol} exchange: {args.exchange}")
    print(f"days: {args.days} start_day: {start_day.isoformat()} hours: {args.hours}")
    print(f"ok={ok} missing={missing} errors={errors}")
    print(f"wrote: {out_path}")

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
