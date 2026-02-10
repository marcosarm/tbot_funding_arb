"""Adapters for the CryptoHFTData parquet layout described in `cryptohftdata_amostras.md`."""

from .liquidations import iter_liquidations, iter_liquidations_for_day
from .mark_price import iter_mark_price, iter_mark_price_for_day
from .open_interest import iter_open_interest, iter_open_interest_for_day
from .orderbook import iter_depth_updates, iter_depth_updates_for_day
from .paths import CryptoHftLayout
from .replay import CryptoHftDayConfig, build_day_stream
from .s3 import S3Config, make_s3_filesystem
from .ticker import iter_ticker, iter_ticker_for_day
from .trades import iter_trades, iter_trades_for_day

__all__ = [
    "CryptoHftLayout",
    "CryptoHftDayConfig",
    "S3Config",
    "iter_depth_updates",
    "iter_depth_updates_for_day",
    "iter_ticker",
    "iter_ticker_for_day",
    "iter_mark_price",
    "iter_mark_price_for_day",
    "iter_open_interest",
    "iter_open_interest_for_day",
    "iter_liquidations",
    "iter_liquidations_for_day",
    "iter_trades",
    "iter_trades_for_day",
    "build_day_stream",
    "make_s3_filesystem",
]
