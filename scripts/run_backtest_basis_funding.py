from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from btengine.analytics import max_drawdown, round_trips_from_fills, summarize_round_trips
from btengine.broker import SimBroker
from btengine.data.cryptohftdata import CryptoHftDayConfig, CryptoHftLayout, S3Config, build_day_stream, make_s3_filesystem
from btengine.engine import BacktestEngine, EngineConfig
from btengine.replay import merge_event_streams
from funding import BasisFundingStrategy
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


@dataclass(slots=True)
class EventCounters:
    depth: int = 0
    trades: int = 0
    mark: int = 0
    ticker: int = 0
    open_interest: int = 0
    liquidations: int = 0


EventLike = DepthUpdate | Trade | MarkPrice | Ticker | OpenInterest | Liquidation


def _count_events(events: Iterable[EventLike], *, counters: EventCounters) -> Iterator[EventLike]:
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


def _limit_events(events: Iterable[EventLike], *, max_events: int) -> Iterable[EventLike]:
    if max_events <= 0:
        return events

    def _gen() -> Iterator[EventLike]:
        for i, ev in enumerate(events):
            if i >= max_events:
                break
            yield ev

    return _gen()


def main() -> int:
    ap = argparse.ArgumentParser(description="Run basis+funding backtests (Perp x Quarterly) in batch.")
    ap.add_argument("--dotenv", default=str(ROOT / ".env"))
    ap.add_argument("--exchange", default="binance_futures")

    ap.add_argument("--perp-symbol", default="BTCUSDT")
    ap.add_argument("--future-symbol", required=True)

    ap.add_argument("--start-day", required=True, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--hours", default="0-23")

    ap.add_argument("--include-ticker", action="store_true")
    ap.add_argument("--include-open-interest", action="store_true")
    ap.add_argument("--include-liquidations", action="store_true")
    ap.add_argument("--include-future-mark-price", action="store_true")
    ap.add_argument("--open-interest-delay-ms", type=int, default=0)
    ap.add_argument("--skip-missing", action="store_true")

    ap.add_argument("--tick-ms", type=int, default=0)
    ap.add_argument("--max-events", type=int, default=0)

    ap.add_argument("--maker-fee-frac", type=float, default=0.0004)
    ap.add_argument("--taker-fee-frac", type=float, default=0.0005)
    ap.add_argument("--submit-latency-ms", type=int, default=0)
    ap.add_argument("--cancel-latency-ms", type=int, default=0)

    # Strategy knobs (SPEC defaults).
    ap.add_argument("--impact-notional-usdt", type=float, default=25_000.0)
    ap.add_argument("--funding-threshold", type=float, default=0.0001)
    ap.add_argument("--max-slippage", type=float, default=0.0005)
    ap.add_argument("--entry-safety-margin", type=float, default=0.0002)
    ap.add_argument("--liquidity-min-ratio", type=float, default=5.0)
    ap.add_argument("--liquidity-depth-pct", type=float, default=0.001)
    ap.add_argument("--z-window", type=int, default=1440)
    ap.add_argument("--vol-ratio-window", type=int, default=60)
    ap.add_argument("--z-exit-eps", type=float, default=0.2)
    ap.add_argument("--z-hard-stop", type=float, default=4.0)
    ap.add_argument("--entry-cooldown-sec", type=int, default=30)
    ap.add_argument("--maker-wait-sec", type=float, default=5.0)
    ap.add_argument("--legging-check-delay-ms", type=int, default=200)
    ap.add_argument("--asof-tolerance-ms", type=int, default=100)
    ap.add_argument("--basis-sample-ms", type=int, default=1000)
    ap.add_argument("--hedge-eps-base", type=float, default=0.001)
    ap.add_argument("--no-reverse", dest="allow_reverse", action="store_false")
    ap.add_argument("--no-force-close-on-end", dest="force_close_on_end", action="store_false")
    ap.set_defaults(allow_reverse=True, force_close_on_end=True)

    ap.add_argument("--out-csv", default="batch_basis_funding.csv")
    args = ap.parse_args()

    if int(args.days) <= 0:
        print("ERROR: --days must be >= 1", file=sys.stderr)
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
        "entries_standard",
        "entries_reverse",
        "exits_mean_reversion",
        "exits_hard_stop",
        "exits_funding_flip",
        "liquidity_rejects",
        "hedge_actions",
        "state_end",
        "fills",
        "round_trips",
        "net_pnl_usdt",
        "gross_pnl_usdt",
        "fees_usdt",
        "realized_pnl_usdt",
        "fees_paid_usdt",
        "max_drawdown_usdt",
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

        try:
            symbols = [str(args.perp_symbol), str(args.future_symbol)]
            streams: list[Iterable[EventLike]] = []
            for sym in symbols:
                include_mark = sym == str(args.perp_symbol) or bool(args.include_future_mark_price)
                cfg = CryptoHftDayConfig(
                    exchange=str(args.exchange),
                    include_trades=True,
                    include_orderbook=True,
                    include_mark_price=bool(include_mark),
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
                streams.append(build_day_stream(layout, cfg=cfg, symbol=sym, day=d, filesystem=fs))

            events: Iterable[EventLike] = merge_event_streams(*streams)
            counters = EventCounters()
            events = _count_events(events, counters=counters)
            events = _limit_events(events, max_events=int(args.max_events or 0))

            broker = SimBroker(
                maker_fee_frac=float(args.maker_fee_frac),
                taker_fee_frac=float(args.taker_fee_frac),
                submit_latency_ms=int(args.submit_latency_ms),
                cancel_latency_ms=int(args.cancel_latency_ms),
            )
            engine = BacktestEngine(config=EngineConfig(tick_interval_ms=int(args.tick_ms)), broker=broker)

            strat = BasisFundingStrategy(
                perp_symbol=str(args.perp_symbol),
                future_symbol=str(args.future_symbol),
                impact_notional_usdt=float(args.impact_notional_usdt),
                funding_threshold=float(args.funding_threshold),
                max_slippage=float(args.max_slippage),
                entry_safety_margin=float(args.entry_safety_margin),
                taker_fee_frac=float(args.taker_fee_frac),
                liquidity_min_ratio=float(args.liquidity_min_ratio),
                liquidity_depth_pct=float(args.liquidity_depth_pct),
                z_window=int(args.z_window),
                vol_ratio_window=int(args.vol_ratio_window),
                z_exit_eps=float(args.z_exit_eps),
                z_hard_stop=float(args.z_hard_stop),
                entry_cooldown_sec=int(args.entry_cooldown_sec),
                maker_wait_sec=float(args.maker_wait_sec),
                legging_check_delay_ms=int(args.legging_check_delay_ms),
                asof_tolerance_ms=int(args.asof_tolerance_ms),
                basis_sample_ms=int(args.basis_sample_ms),
                hedge_eps_base=float(args.hedge_eps_base),
                allow_reverse=bool(args.allow_reverse),
                force_close_on_end=bool(args.force_close_on_end),
            )

            res = engine.run(events, strategy=strat)

            fills = res.ctx.broker.fills
            round_trips = round_trips_from_fills(fills)
            rt_summary = summarize_round_trips(round_trips)
            mdd = max_drawdown(strat.equity_curve)

            rows.append(
                {
                    "day": day_str,
                    "status": "OK",
                    "runtime_s": round(time.perf_counter() - t0, 3),
                    "events": int(counters.depth + counters.trades + counters.mark + counters.ticker + counters.open_interest + counters.liquidations),
                    "depth": int(counters.depth),
                    "trades": int(counters.trades),
                    "mark": int(counters.mark),
                    "ticker": int(counters.ticker),
                    "open_interest": int(counters.open_interest),
                    "liquidations": int(counters.liquidations),
                    "entries_standard": int(strat.entries_standard),
                    "entries_reverse": int(strat.entries_reverse),
                    "exits_mean_reversion": int(strat.exits_mean_reversion),
                    "exits_hard_stop": int(strat.exits_hard_stop),
                    "exits_funding_flip": int(strat.exits_funding_flip),
                    "liquidity_rejects": int(strat.liquidity_rejects),
                    "hedge_actions": int(strat.hedge_actions),
                    "state_end": str(strat.state),
                    "fills": int(len(fills)),
                    "round_trips": int(rt_summary.trades),
                    "net_pnl_usdt": float(rt_summary.net_pnl_usdt),
                    "gross_pnl_usdt": float(rt_summary.gross_pnl_usdt),
                    "fees_usdt": float(rt_summary.fees_usdt),
                    "realized_pnl_usdt": float(res.ctx.broker.portfolio.realized_pnl_usdt),
                    "fees_paid_usdt": float(res.ctx.broker.portfolio.fees_paid_usdt),
                    "max_drawdown_usdt": mdd,
                    "error": "",
                }
            )
            ok += 1
        except FileNotFoundError as e:
            rows.append(
                {
                    "day": day_str,
                    "status": "MISSING",
                    "runtime_s": round(time.perf_counter() - t0, 3),
                    "events": 0,
                    "depth": 0,
                    "trades": 0,
                    "mark": 0,
                    "ticker": 0,
                    "open_interest": 0,
                    "liquidations": 0,
                    "entries_standard": 0,
                    "entries_reverse": 0,
                    "exits_mean_reversion": 0,
                    "exits_hard_stop": 0,
                    "exits_funding_flip": 0,
                    "liquidity_rejects": 0,
                    "hedge_actions": 0,
                    "state_end": "flat",
                    "fills": 0,
                    "round_trips": 0,
                    "net_pnl_usdt": 0.0,
                    "gross_pnl_usdt": 0.0,
                    "fees_usdt": 0.0,
                    "realized_pnl_usdt": 0.0,
                    "fees_paid_usdt": 0.0,
                    "max_drawdown_usdt": None,
                    "error": repr(e),
                }
            )
            missing += 1
        except Exception as e:
            rows.append(
                {
                    "day": day_str,
                    "status": "ERROR",
                    "runtime_s": round(time.perf_counter() - t0, 3),
                    "events": 0,
                    "depth": 0,
                    "trades": 0,
                    "mark": 0,
                    "ticker": 0,
                    "open_interest": 0,
                    "liquidations": 0,
                    "entries_standard": 0,
                    "entries_reverse": 0,
                    "exits_mean_reversion": 0,
                    "exits_hard_stop": 0,
                    "exits_funding_flip": 0,
                    "liquidity_rejects": 0,
                    "hedge_actions": 0,
                    "state_end": "flat",
                    "fills": 0,
                    "round_trips": 0,
                    "net_pnl_usdt": 0.0,
                    "gross_pnl_usdt": 0.0,
                    "fees_usdt": 0.0,
                    "realized_pnl_usdt": 0.0,
                    "fees_paid_usdt": 0.0,
                    "max_drawdown_usdt": None,
                    "error": repr(e),
                }
            )
            errors += 1

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print("\n== Batch Summary ==")
    print(f"pair: perp={args.perp_symbol} future={args.future_symbol} exchange={args.exchange}")
    print(f"days: {args.days} start_day: {start_day.isoformat()} hours: {args.hours}")
    print(f"ok={ok} missing={missing} errors={errors}")
    print(f"wrote: {out_path}")

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

