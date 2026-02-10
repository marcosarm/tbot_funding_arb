from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from btengine.analytics import max_drawdown, round_trips_from_fills, summarize_round_trips
from btengine.broker import SimBroker
from btengine.data.cryptohftdata import CryptoHftDayConfig, CryptoHftLayout, S3Config, build_day_stream, make_s3_filesystem
from btengine.engine import BacktestEngine, EngineConfig, EngineContext
from btengine.execution.orders import Order
from btengine.replay import merge_event_streams
from btengine.types import DepthUpdate, MarkPrice
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


def _utc_iso(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc).isoformat()


@dataclass(slots=True)
class EntryExitStrategy:
    symbol: str
    direction: Literal["long", "short"]
    target_qty: float
    schedule_ms: list[tuple[int, int]]  # [(enter_ms, exit_ms), ...]
    force_close_on_end: bool = True

    # Internal state.
    _cycle: int = 0
    _in_position: bool = False
    equity_curve: list[tuple[int, float]] = field(default_factory=list)

    def _pos_qty(self, ctx: EngineContext) -> float:
        p = ctx.broker.portfolio.positions.get(self.symbol)
        return float(p.qty) if p is not None else 0.0

    def _close_qty(self, ctx: EngineContext) -> float:
        return abs(self._pos_qty(ctx))

    def _submit_entry(self, ctx: EngineContext) -> None:
        side = "buy" if self.direction == "long" else "sell"
        book = ctx.book(self.symbol)
        ctx.broker.submit(
            Order(
                id=f"entry_{self._cycle}",
                symbol=self.symbol,
                side=side,
                order_type="market",
                quantity=float(self.target_qty),
            ),
            book,
            now_ms=int(ctx.now_ms),
        )

        # Market fills are immediate when there is depth.
        self._in_position = (self._pos_qty(ctx) != 0.0)

    def _submit_exit(self, ctx: EngineContext) -> None:
        q = self._close_qty(ctx)
        if q <= 0.0:
            self._in_position = False
            return

        side = "sell" if self._pos_qty(ctx) > 0.0 else "buy"
        book = ctx.book(self.symbol)
        ctx.broker.submit(
            Order(
                id=f"exit_{self._cycle}",
                symbol=self.symbol,
                side=side,
                order_type="market",
                quantity=float(q),
            ),
            book,
            now_ms=int(ctx.now_ms),
        )
        self._in_position = (self._pos_qty(ctx) != 0.0)

    def on_event(self, event: object, ctx: EngineContext) -> None:
        # Equity curve (PnL) sampled on mark price.
        if isinstance(event, MarkPrice) and event.symbol == self.symbol:
            p = ctx.broker.portfolio.positions.get(self.symbol)
            unreal = 0.0
            if p is not None and p.qty != 0.0:
                unreal = float(p.qty) * (float(event.mark_price) - float(p.avg_price))
            eq = float(ctx.broker.portfolio.realized_pnl_usdt) + unreal
            self.equity_curve.append((int(event.event_time_ms), float(eq)))
            return

        if not isinstance(event, DepthUpdate) or event.symbol != self.symbol:
            return

        if self._cycle >= len(self.schedule_ms):
            return

        enter_ms, exit_ms = self.schedule_ms[self._cycle]
        now = int(ctx.now_ms)

        # Wait until book is formed.
        book = ctx.book(self.symbol)
        if book.best_bid() is None or book.best_ask() is None:
            return

        if not self._in_position and now >= int(enter_ms):
            self._submit_entry(ctx)
            return

        if self._in_position and now >= int(exit_ms):
            self._submit_exit(ctx)
            if not self._in_position:
                self._cycle += 1

    def on_end(self, ctx: EngineContext) -> None:
        if not self.force_close_on_end:
            return
        if self._close_qty(ctx) <= 0.0:
            return
        self._submit_exit(ctx)


def _write_fills_csv(path: str, fills) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["event_time_ms", "symbol", "order_id", "side", "qty", "price", "fee_usdt", "liquidity"])
        for x in fills:
            w.writerow([x.event_time_ms, x.symbol, x.order_id, x.side, x.quantity, x.price, x.fee_usdt, x.liquidity])


def _write_equity_csv(path: str, equity_curve: list[tuple[int, float]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["event_time_ms", "equity_pnl_usdt"])
        for t, eq in equity_curve:
            w.writerow([t, eq])


def main() -> int:
    ap = argparse.ArgumentParser(description="Simple entry/exit setup to sanity-check PnL and basic stats.")
    ap.add_argument("--dotenv", default=str(ROOT / ".env"))
    ap.add_argument("--exchange", default="binance_futures")
    ap.add_argument("--day", required=True, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--hours", default="12-12")
    ap.add_argument("--include-ticker", action="store_true")
    ap.add_argument("--include-open-interest", action="store_true")
    ap.add_argument("--include-liquidations", action="store_true")
    ap.add_argument("--open-interest-delay-ms", type=int, default=0)
    ap.add_argument("--skip-missing", action="store_true")

    ap.add_argument("--direction", choices=["long", "short"], default="long")
    ap.add_argument("--qty", type=float, default=0.001)
    ap.add_argument("--enter-offset-s", type=int, default=30, help="Enter offset from window start (seconds).")
    ap.add_argument("--hold-s", type=int, default=60, help="Hold duration before exit (seconds).")
    ap.add_argument("--gap-s", type=int, default=60, help="Gap between cycles (seconds).")
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument(
        "--no-force-close-on-end",
        dest="force_close_on_end",
        action="store_false",
        help="Do not force-close an open position at the end of the stream.",
    )
    ap.set_defaults(force_close_on_end=True)

    ap.add_argument("--tick-ms", type=int, default=0, help="Engine tick interval (0 disables ticks).")
    ap.add_argument("--max-events", type=int, default=0, help="0 = no limit")

    ap.add_argument("--maker-fee-frac", type=float, default=0.0004)
    ap.add_argument("--taker-fee-frac", type=float, default=0.0005)
    ap.add_argument("--submit-latency-ms", type=int, default=0)
    ap.add_argument("--cancel-latency-ms", type=int, default=0)

    ap.add_argument("--out-fills-csv", default=None)
    ap.add_argument("--out-equity-csv", default=None)
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

    if args.cycles <= 0:
        print("ERROR: --cycles must be >= 1", file=sys.stderr)
        return 2
    if args.qty <= 0:
        print("ERROR: --qty must be > 0", file=sys.stderr)
        return 2
    if args.enter_offset_s < 0 or args.hold_s <= 0 or args.gap_s < 0:
        print("ERROR: invalid enter/hold/gap parameters", file=sys.stderr)
        return 2

    schedule: list[tuple[int, int]] = []
    step_ms = (int(args.hold_s) + int(args.gap_s)) * 1000
    for i in range(int(args.cycles)):
        enter_ms = window_start_ms + int(args.enter_offset_s) * 1000 + i * step_ms
        exit_ms = enter_ms + int(args.hold_s) * 1000
        if exit_ms >= window_end_ms:
            break
        schedule.append((int(enter_ms), int(exit_ms)))

    if not schedule:
        print("ERROR: schedule is empty (window too small for enter/exit).", file=sys.stderr)
        return 2

    cfg = CryptoHftDayConfig(
        exchange=args.exchange,
        include_trades=True,
        include_orderbook=True,
        include_mark_price=True,
        include_ticker=bool(args.include_ticker),
        include_open_interest=bool(args.include_open_interest),
        include_liquidations=bool(args.include_liquidations),
        open_interest_delay_ms=int(args.open_interest_delay_ms or 0),
        orderbook_hours=hours,
        orderbook_skip_missing=True,
        skip_missing_daily_files=bool(args.skip_missing),
        stream_start_ms=window_start_ms,
        stream_end_ms=window_end_ms,
    )

    stream = build_day_stream(layout, cfg=cfg, symbol=str(args.symbol), day=d, filesystem=fs)
    events = merge_event_streams(stream)

    max_events = int(args.max_events or 0)
    if max_events > 0:
        def _limit(xs):
            for i, ev in enumerate(xs):
                if i >= max_events:
                    break
                yield ev
        events = _limit(events)

    broker = SimBroker(
        maker_fee_frac=float(args.maker_fee_frac),
        taker_fee_frac=float(args.taker_fee_frac),
        submit_latency_ms=int(args.submit_latency_ms),
        cancel_latency_ms=int(args.cancel_latency_ms),
    )
    engine = BacktestEngine(config=EngineConfig(tick_interval_ms=int(args.tick_ms)), broker=broker)

    strat = EntryExitStrategy(
        symbol=str(args.symbol),
        direction=str(args.direction),  # type: ignore[arg-type]
        target_qty=float(args.qty),
        schedule_ms=schedule,
        force_close_on_end=bool(args.force_close_on_end),
    )
    res = engine.run(events, strategy=strat)

    fills = res.ctx.broker.fills
    trades = round_trips_from_fills(fills)
    summary = summarize_round_trips(trades)

    eq = strat.equity_curve
    mdd = max_drawdown(eq)
    eq_min = min((x for _, x in eq), default=None)
    eq_max = max((x for _, x in eq), default=None)

    print("\n== Window ==")
    print(f"symbol: {args.symbol}")
    print(f"start_ms: {window_start_ms} ({_utc_iso(window_start_ms)})")
    print(f"end_ms:   {window_end_ms} ({_utc_iso(window_end_ms)})")

    print("\n== Schedule (UTC) ==")
    for i, (a, b) in enumerate(schedule):
        print(f"{i}: enter={_utc_iso(a)} exit={_utc_iso(b)}")

    print("\n== Fills ==")
    if not fills:
        print("fills: 0")
    else:
        for f in fills:
            print(
                f"t={_utc_iso(f.event_time_ms)} order={f.order_id} {f.side} "
                f"qty={f.quantity} px={f.price} fee={f.fee_usdt} liq={f.liquidity}"
            )

    print("\n== Portfolio ==")
    print(f"realized_pnl_usdt: {res.ctx.broker.portfolio.realized_pnl_usdt:.6f}")
    print(f"fees_paid_usdt:    {res.ctx.broker.portfolio.fees_paid_usdt:.6f}")
    p = res.ctx.broker.portfolio.positions.get(str(args.symbol))
    if p is None:
        print("final_position: none")
    else:
        print(f"final_position: qty={p.qty} avg_price={p.avg_price}")

    print("\n== Round Trips (from fills) ==")
    print(f"trades: {summary.trades} wins={summary.wins} losses={summary.losses} win_rate={summary.win_rate}")
    print(f"net_pnl_usdt:   {summary.net_pnl_usdt:.6f}")
    print(f"gross_pnl_usdt: {summary.gross_pnl_usdt:.6f}")
    print(f"fees_usdt:      {summary.fees_usdt:.6f}")
    print(f"avg_duration_ms:{summary.avg_duration_ms} max_duration_ms:{summary.max_duration_ms}")

    print("\n== Equity Curve (mark_price) ==")
    print(f"points: {len(eq)} eq_min={eq_min} eq_max={eq_max} max_drawdown={mdd}")

    if args.out_fills_csv:
        _write_fills_csv(str(args.out_fills_csv), fills)
        print(f"\nwrote fills csv: {args.out_fills_csv}")

    if args.out_equity_csv:
        _write_equity_csv(str(args.out_equity_csv), eq)
        print(f"wrote equity csv: {args.out_equity_csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
