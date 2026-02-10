from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from btengine.data.cryptohftdata import iter_depth_updates
from btengine.marketdata import L2Book


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay an orderbook parquet into an in-memory L2 book.")
    ap.add_argument("path", type=Path, help="Path to orderbook_XX.parquet")
    ap.add_argument("--notional", type=float, default=25_000.0, help="Notional for impact VWAP (USDT).")
    ap.add_argument("--every-ms", type=int, default=1_000, help="Print snapshot every N ms of event_time.")
    ap.add_argument("--max-messages", type=int, default=50_000, help="Stop after N depth messages.")
    args = ap.parse_args()

    book = L2Book()
    last_print_ms: int | None = None
    count = 0

    for update in iter_depth_updates(args.path):
        count += 1
        book.apply_depth_update(update.bid_updates, update.ask_updates)

        now = update.event_time_ms
        if last_print_ms is None or now - last_print_ms >= args.every_ms:
            bid = book.best_bid()
            ask = book.best_ask()
            mid = book.mid_price()
            buy_vwap = book.impact_vwap("buy", args.notional)
            sell_vwap = book.impact_vwap("sell", args.notional)
            print(
                f"{_utc(now)}  bid={bid} ask={ask} mid={mid} "
                f"impact_buy={buy_vwap} impact_sell={sell_vwap} msgs={count}"
            )
            last_print_ms = now

        if count >= args.max_messages:
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
