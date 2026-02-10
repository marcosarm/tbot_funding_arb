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
from btengine.types import DepthUpdate, MarkPrice, Trade
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
class Bar:
    start_ms: int
    open: float
    high: float
    low: float
    close: float


@dataclass(slots=True)
class BarBuilder:
    """Timeframe bar builder based on incoming prices.

    A bar is considered "closed" when we observe the first tick of the next bar.
    """

    tf_ms: int
    fill_missing: bool = False

    _bar_id: int | None = None
    _bar: Bar | None = None

    def on_price(self, t_ms: int, price: float) -> list[Bar]:
        t = int(t_ms)
        p = float(price)
        if self.tf_ms <= 0:
            raise ValueError("tf_ms must be > 0")

        bid = t // int(self.tf_ms)
        closed: list[Bar] = []

        if self._bar_id is None:
            self._bar_id = bid
            start = bid * int(self.tf_ms)
            self._bar = Bar(start_ms=int(start), open=p, high=p, low=p, close=p)
            return closed

        assert self._bar is not None
        if bid == self._bar_id:
            b = self._bar
            b.high = max(b.high, p)
            b.low = min(b.low, p)
            b.close = p
            return closed

        # New bar(s) started; close current.
        closed.append(self._bar)

        # Fill missing bars if requested (repeat last close).
        if self.fill_missing and bid > int(self._bar_id) + 1:
            last_close = float(self._bar.close)
            for mid in range(int(self._bar_id) + 1, int(bid)):
                start = mid * int(self.tf_ms)
                closed.append(
                    Bar(
                        start_ms=int(start),
                        open=last_close,
                        high=last_close,
                        low=last_close,
                        close=last_close,
                    )
                )

        # Start new bar with current tick.
        self._bar_id = bid
        start = bid * int(self.tf_ms)
        self._bar = Bar(start_ms=int(start), open=p, high=p, low=p, close=p)
        return closed


@dataclass(slots=True)
class MaCrossStrategy:
    symbol: str
    qty: float
    tf_ms: int = 300_000  # 5m
    ma_len: int = 9
    rule: Literal["cross", "state"] = "cross"  # cross = only trade on cross, state = always target side
    mode: Literal["long_short", "long_only"] = "long_short"
    price_source: Literal["mark", "trade"] = "mark"
    fill_missing_bars: bool = False
    eps_qty: float = 1e-12

    bars: list[Bar] = field(default_factory=list)
    closes: list[float] = field(default_factory=list)
    prev_diff: float | None = None

    equity_curve: list[tuple[int, float]] = field(default_factory=list)

    _bar_builder: BarBuilder | None = None

    def on_start(self, ctx: EngineContext) -> None:
        if self.qty <= 0:
            raise ValueError("qty must be > 0")
        if self.ma_len <= 0:
            raise ValueError("ma_len must be > 0")
        self._bar_builder = BarBuilder(tf_ms=int(self.tf_ms), fill_missing=bool(self.fill_missing_bars))

    def _pos_qty(self, ctx: EngineContext) -> float:
        p = ctx.broker.portfolio.positions.get(self.symbol)
        return float(p.qty) if p is not None else 0.0

    def _ensure_book_ready(self, ctx: EngineContext) -> bool:
        book = ctx.books.get(self.symbol)
        if book is None:
            return False
        return book.best_bid() is not None and book.best_ask() is not None

    def _set_target(self, ctx: EngineContext, *, target_qty: float, reason: str) -> None:
        if not self._ensure_book_ready(ctx):
            return
        cur = self._pos_qty(ctx)
        delta = float(target_qty) - float(cur)
        if abs(delta) <= float(self.eps_qty):
            return

        side = "buy" if delta > 0.0 else "sell"
        q = abs(delta)
        book = ctx.books[self.symbol]
        ctx.broker.submit(
            Order(
                id=f"ma_{reason}_{int(ctx.now_ms)}",
                symbol=self.symbol,
                side=side,
                order_type="market",
                quantity=q,
            ),
            book,
            now_ms=int(ctx.now_ms),
        )

    def _on_closed_bar(self, b: Bar, ctx: EngineContext) -> None:
        self.bars.append(b)
        self.closes.append(float(b.close))

        if len(self.closes) < int(self.ma_len):
            return

        w = self.closes[-int(self.ma_len) :]
        ma = sum(w) / float(len(w))
        diff = float(b.close) - float(ma)

        # Decide desired direction.
        desired: Literal["long", "short", "flat"] | None = None
        if self.rule == "state":
            desired = "long" if diff >= 0.0 else "short"
        else:  # cross
            if self.prev_diff is not None:
                if self.prev_diff <= 0.0 and diff > 0.0:
                    desired = "long"
                elif self.prev_diff >= 0.0 and diff < 0.0:
                    desired = "short"
            else:
                # First eligible bar: pick a side, but it's still conservative
                # because it only uses completed history.
                desired = "long" if diff > 0.0 else ("short" if diff < 0.0 else None)

        self.prev_diff = diff

        if desired is None:
            return
        if self.mode == "long_only" and desired == "short":
            desired = "flat"

        if desired == "long":
            self._set_target(ctx, target_qty=float(self.qty), reason="long")
        elif desired == "short":
            self._set_target(ctx, target_qty=-float(self.qty), reason="short")
        else:  # flat
            self._set_target(ctx, target_qty=0.0, reason="flat")

    def on_event(self, event: object, ctx: EngineContext) -> None:
        # Equity curve sampled on mark price.
        if isinstance(event, MarkPrice) and event.symbol == self.symbol:
            p = ctx.broker.portfolio.positions.get(self.symbol)
            unreal = 0.0
            if p is not None and p.qty != 0.0:
                unreal = float(p.qty) * (float(event.mark_price) - float(p.avg_price))
            eq = float(ctx.broker.portfolio.realized_pnl_usdt) + unreal
            self.equity_curve.append((int(event.event_time_ms), float(eq)))

        # Bar aggregation.
        if self._bar_builder is None:
            return

        if self.price_source == "mark":
            if not isinstance(event, MarkPrice) or event.symbol != self.symbol:
                return
            t_ms = int(event.event_time_ms)
            price = float(event.mark_price)
        else:  # trade
            if not isinstance(event, Trade) or event.symbol != self.symbol:
                return
            t_ms = int(event.event_time_ms)
            price = float(event.price)

        closed = self._bar_builder.on_price(t_ms, price)
        for b in closed:
            self._on_closed_bar(b, ctx)

    def on_end(self, ctx: EngineContext) -> None:
        # Force-flat at end (optional semantics: if mode is long_short, still go flat at end).
        self._set_target(ctx, target_qty=0.0, reason="end")


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
    ap = argparse.ArgumentParser(description="MA(9) vs price strategy using 5m bars (test setup).")
    ap.add_argument("--dotenv", default=str(ROOT / ".env"))
    ap.add_argument("--exchange", default="binance_futures")
    ap.add_argument("--day", required=True, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--hours", default="12-13")
    ap.add_argument("--skip-missing", action="store_true")

    ap.add_argument("--price-source", choices=["mark", "trade"], default="mark")
    ap.add_argument("--tf-min", type=int, default=5, help="Timeframe in minutes (e.g. 5 for 5m).")
    ap.add_argument("--ma-len", type=int, default=9, help="Moving average length in bars.")
    ap.add_argument("--rule", choices=["cross", "state"], default="cross")
    ap.add_argument("--mode", choices=["long_short", "long_only"], default="long_short")
    ap.add_argument("--qty", type=float, default=0.001)
    ap.add_argument("--fill-missing-bars", action="store_true")

    ap.add_argument("--tick-ms", type=int, default=0, help="Engine tick interval (0 disables ticks).")
    ap.add_argument("--max-events", type=int, default=0, help="0 = no limit")

    ap.add_argument("--maker-fee-frac", type=float, default=0.0004)
    ap.add_argument("--taker-fee-frac", type=float, default=0.0005)
    ap.add_argument("--submit-latency-ms", type=int, default=0)
    ap.add_argument("--cancel-latency-ms", type=int, default=0)

    ap.add_argument("--out-fills-csv", default=None)
    ap.add_argument("--out-equity-csv", default=None)
    args = ap.parse_args()

    if args.tf_min <= 0:
        print("ERROR: --tf-min must be > 0", file=sys.stderr)
        return 2
    if args.ma_len <= 0:
        print("ERROR: --ma-len must be > 0", file=sys.stderr)
        return 2

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

    include_trades = bool(args.price_source == "trade")
    cfg = CryptoHftDayConfig(
        exchange=args.exchange,
        include_trades=include_trades,
        include_orderbook=True,
        include_mark_price=True,  # equity curve (and price when --price-source mark)
        include_ticker=False,
        include_open_interest=False,
        include_liquidations=False,
        orderbook_hours=hours,
        orderbook_skip_missing=True,
        skip_missing_daily_files=bool(args.skip_missing),
        stream_start_ms=window_start_ms,
        stream_end_ms=window_end_ms,
    )

    events = build_day_stream(layout, cfg=cfg, symbol=str(args.symbol), day=d, filesystem=fs)

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

    strat = MaCrossStrategy(
        symbol=str(args.symbol),
        qty=float(args.qty),
        tf_ms=int(args.tf_min) * 60_000,
        ma_len=int(args.ma_len),
        rule=str(args.rule),  # type: ignore[arg-type]
        mode=str(args.mode),  # type: ignore[arg-type]
        price_source=str(args.price_source),  # type: ignore[arg-type]
        fill_missing_bars=bool(args.fill_missing_bars),
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

    print("\n== Strategy ==")
    print(f"price_source: {args.price_source}")
    print(f"timeframe: {args.tf_min}m  ma_len: {args.ma_len}  rule: {args.rule}  mode: {args.mode}")
    print(f"qty: {args.qty}")

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

